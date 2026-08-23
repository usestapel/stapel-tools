"""adoption-lint tests — every ADO rule via fixture mini-projects.

A fixture project is a sibling of one or more fake ``stapel-<mod>`` module
repos (neighbour-repo layout), so the linter's default schema/urls search root
(the project's parent) resolves them without anything being pip-installed.
"""
import json
import subprocess

from stapel_tools.adoption_lint import (
    lint_project,
    main,
    normalize_route,
)

# ---------------------------------------------------------------------------
# fixture builders
# ---------------------------------------------------------------------------


def make_module(workspace, short, *, paths=None, with_urls=True):
    """A fake neighbour module repo ``stapel-<short>`` with an optional
    ``urls.py`` and a ``docs/schema.json`` (OpenAPI ``paths``)."""
    repo = workspace / f"stapel-{short}"
    repo.mkdir(parents=True, exist_ok=True)
    if with_urls:
        (repo / "urls.py").write_text("urlpatterns = []\n")
    docs = repo / "docs"
    docs.mkdir(exist_ok=True)
    schema = {"openapi": "3.0.3", "paths": {}}
    for p in paths or []:
        schema["paths"][p] = {"get": {"operationId": f"{short}_op"}}
    (docs / "schema.json").write_text(json.dumps(schema))
    return repo


def make_project(
    workspace,
    *,
    name="proj",
    requirements=(),
    installed_apps=(),
    urlpatterns=(),
    headless=(),
    root_urlconf=True,
    extra_files=None,
):
    proj = workspace / name
    (proj / "config").mkdir(parents=True, exist_ok=True)

    (proj / "requirements.txt").write_text("\n".join(requirements) + "\n")

    settings = ["ROOT_URLCONF = \"config.urls\"" if root_urlconf else ""]
    settings.append("INSTALLED_APPS = [")
    for app in installed_apps:
        settings.append(f"    {app!r},")
    settings.append("]")
    (proj / "config" / "settings.py").write_text("\n".join(settings) + "\n")

    lines = ["from django.urls import include, path", ""]
    for mark in headless:
        lines.append(f"# stapel: headless {mark}")
    lines.append("urlpatterns = [")
    for pat in urlpatterns:
        lines.append(f"    {pat},")
    lines.append("]")
    (proj / "config" / "urls.py").write_text("\n".join(lines) + "\n")

    for rel, body in (extra_files or {}).items():
        target = proj / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    return proj


def rules(findings):
    return sorted(f.rule for f in findings)


# ---------------------------------------------------------------------------
# ADO001 — installed but not mounted
# ---------------------------------------------------------------------------


class TestNotMounted:
    def test_installed_not_mounted_errors(self, tmp_path):
        make_module(tmp_path, "widget")
        proj = make_project(
            tmp_path,
            requirements=["stapel-widget"],
            installed_apps=["stapel_widget"],
            urlpatterns=[],  # not mounted
        )
        findings = lint_project(proj)
        assert rules(findings) == ["ADO001"]
        assert findings[0].level == "error"
        assert "stapel_widget" in findings[0].message

    def test_mounted_passes(self, tmp_path):
        make_module(tmp_path, "widget")
        proj = make_project(
            tmp_path,
            requirements=["stapel-widget"],
            installed_apps=["stapel_widget"],
            urlpatterns=['path("widget/api/", include("stapel_widget.urls"))'],
        )
        findings = lint_project(proj)
        assert rules(findings) == []

    def test_mounted_via_fstring_route_passes(self, tmp_path):
        """Regression: stapel-tools' OWN generated config/urls.py mounts every
        stapel module at a COMPUTED prefix — ``path(f"{url_prefix}api/",
        include("stapel_widget.urls"))`` (see _templates.URLS_PY /
        new_service.make_context) — not a plain string literal route. A
        freshly generated monolith with e.g. auth+notifications false-
        positived ADO001 on itself under the e2e-generated-project CI gate
        because the f-string route parsed as neither ast.Constant nor
        anything _route_literal recognized, so the include() one argument
        over was never reached and the mount was silently dropped."""
        make_module(tmp_path, "widget")
        proj = make_project(
            tmp_path,
            requirements=["stapel-widget"],
            installed_apps=["stapel_widget"],
            urlpatterns=[
                'path(f"{url_prefix}api/", include("stapel_widget.urls"))'
            ],
        )
        assert rules(lint_project(proj)) == []

    def test_headless_marker_suppresses(self, tmp_path):
        make_module(tmp_path, "widget")
        proj = make_project(
            tmp_path,
            requirements=["stapel-widget"],
            installed_apps=["stapel_widget"],
            urlpatterns=[],
            headless=["widget"],  # short name form
        )
        assert rules(lint_project(proj)) == []

    def test_headless_marker_full_name_form(self, tmp_path):
        make_module(tmp_path, "widget")
        proj = make_project(
            tmp_path,
            installed_apps=["stapel_widget"],
            urlpatterns=[],
            headless=["stapel_widget"],
        )
        assert rules(lint_project(proj)) == []

    def test_library_module_without_urls_not_flagged(self, tmp_path):
        # A stapel module that ships no urlconf (library-only) must never
        # trip ADO001 for being unmounted.
        make_module(tmp_path, "corelib", with_urls=False)
        proj = make_project(
            tmp_path,
            requirements=["stapel-corelib"],
            installed_apps=["stapel_corelib"],
            urlpatterns=[],
        )
        assert rules(lint_project(proj)) == []

    def test_installed_via_requirements_only(self, tmp_path):
        # present in requirements but not INSTALLED_APPS — still expected mounted
        make_module(tmp_path, "widget")
        proj = make_project(
            tmp_path,
            requirements=["stapel-widget @ git+https://example/stapel-widget.git"],
            installed_apps=[],
            urlpatterns=[],
        )
        assert rules(lint_project(proj)) == ["ADO001"]

    def test_inline_list_include_counts_as_mounted(self, tmp_path):
        make_module(tmp_path, "widget")
        proj = make_project(
            tmp_path,
            installed_apps=["stapel_widget"],
            urlpatterns=[
                'path("w/", include(['
                'path("api/", include("stapel_widget.urls"))]))'
            ],
        )
        assert rules(lint_project(proj)) == []


# ---------------------------------------------------------------------------
# ADO002 — custom route duplicates a module operation
# ---------------------------------------------------------------------------


class TestDuplicateRoute:
    def test_duplicate_route_errors(self, tmp_path):
        make_module(tmp_path, "widget", paths=["/widget/api/items/{id}/"])
        proj = make_project(
            tmp_path,
            installed_apps=["stapel_widget"],
            urlpatterns=[
                'path("widget/api/", include("stapel_widget.urls"))',
                'path("widget/api/items/<int:pk>/", views.custom)',
            ],
        )
        findings = lint_project(proj)
        assert "ADO002" in rules(findings)
        dup = [f for f in findings if f.rule == "ADO002"][0]
        assert dup.level == "error"
        assert "stapel_widget" in dup.message
        assert "widget_op" in dup.message

    def test_param_normalization_equates_id_and_pk(self, tmp_path):
        assert normalize_route("/widget/api/items/{id}/") == "widget/api/items/{}"
        assert normalize_route("widget/api/items/<int:pk>/") == "widget/api/items/{}"

    def test_distinct_route_passes(self, tmp_path):
        make_module(tmp_path, "widget", paths=["/widget/api/items/{id}/"])
        proj = make_project(
            tmp_path,
            installed_apps=["stapel_widget"],
            urlpatterns=[
                'path("widget/api/", include("stapel_widget.urls"))',
                'path("widget/api/reports/<int:pk>/", views.custom)',
            ],
        )
        assert "ADO002" not in rules(lint_project(proj))

    def test_no_schema_skips_check_with_note(self, tmp_path):
        make_module(tmp_path, "widget", paths=[])  # empty schema still present
        # remove the schema to simulate a wheel install without docs/
        (tmp_path / "stapel-widget" / "docs" / "schema.json").unlink()
        proj = make_project(
            tmp_path,
            installed_apps=["stapel_widget"],
            urlpatterns=[
                'path("widget/api/", include("stapel_widget.urls"))',
                'path("widget/api/items/<int:pk>/", views.custom)',
            ],
        )
        notes = []
        findings = lint_project(proj, notes=notes)
        assert "ADO002" not in rules(findings)
        assert any("schema.json" in n for n in notes)


# ---------------------------------------------------------------------------
# ADO004 — dead requirement pin
# ---------------------------------------------------------------------------


class TestDeadPin:
    def test_unused_pin_warns(self, tmp_path):
        proj = make_project(tmp_path, requirements=["PyJWT>=2.0"])
        findings = lint_project(proj)
        assert rules(findings) == ["ADO004"]
        assert findings[0].level == "warning"
        assert "jwt" in findings[0].message  # resolved import name, not "pyjwt"

    def test_imported_pin_passes(self, tmp_path):
        proj = make_project(
            tmp_path,
            requirements=["requests"],
            extra_files={"apps/thing/client.py": "import requests\n"},
        )
        assert "ADO004" not in rules(lint_project(proj))

    def test_stapel_module_pin_exempt(self, tmp_path):
        # a stapel module is referenced by dotted string, never imported —
        # it must not be reported as a dead pin (ADO001 owns its mount check)
        make_module(tmp_path, "widget")
        proj = make_project(
            tmp_path,
            requirements=["stapel-widget"],
            installed_apps=["stapel_widget"],
            urlpatterns=['path("w/", include("stapel_widget.urls"))'],
        )
        assert "ADO004" not in rules(lint_project(proj))

    def test_configured_by_settings_string_exempt(self, tmp_path):
        # pinned + registered in INSTALLED_APPS by string but never imported —
        # used (configured), not dead
        proj = make_project(
            tmp_path,
            requirements=["requests"],
            installed_apps=["requests"],
        )
        assert "ADO004" not in rules(lint_project(proj))

    def test_runtime_only_pin_exempt(self, tmp_path):
        proj = make_project(tmp_path, requirements=["gunicorn>=21", "pytest"])
        assert "ADO004" not in rules(lint_project(proj))

    def test_unresolvable_pin_left_alone(self, tmp_path):
        proj = make_project(
            tmp_path, requirements=["some-package-that-is-not-installed-xyz"]
        )
        assert "ADO004" not in rules(lint_project(proj))


# ---------------------------------------------------------------------------
# ADO003 — migration done on an unmerged branch (git)
# ---------------------------------------------------------------------------


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
             "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo)},
    )


class TestBranchGate:
    def _init_on_main(self, proj):
        _git(proj, "init", "-q")
        _git(proj, "checkout", "-q", "-b", "main")
        (proj / "seed.txt").write_text("x")
        _git(proj, "add", ".")
        _git(proj, "commit", "-q", "-m", "seed")

    def test_done_on_unmerged_branch_warns(self, tmp_path):
        proj = make_project(tmp_path)
        self._init_on_main(proj)
        _git(proj, "checkout", "-q", "-b", "migrate/stapel")
        (proj / "STAPEL-MIGRATION.md").write_text("# Migration\n- [x] Phase 3 done\n")
        _git(proj, "add", ".")
        _git(proj, "commit", "-q", "-m", "migration done")
        findings = lint_project(proj)
        assert "ADO003" in rules(findings)
        f = [f for f in findings if f.rule == "ADO003"][0]
        assert f.level == "warning"
        assert "migrate/stapel" in f.message

    def test_done_on_main_passes(self, tmp_path):
        proj = make_project(tmp_path)
        self._init_on_main(proj)
        (proj / "STAPEL-MIGRATION.md").write_text("done\n")
        _git(proj, "add", ".")
        _git(proj, "commit", "-q", "-m", "on main")
        assert "ADO003" not in rules(lint_project(proj))

    def test_merged_branch_passes(self, tmp_path):
        proj = make_project(tmp_path)
        self._init_on_main(proj)
        (proj / "STAPEL-MIGRATION.md").write_text("- [x] done\n")
        _git(proj, "add", ".")
        _git(proj, "commit", "-q", "-m", "add migration doc on main")
        # branch that is behind/at main → HEAD is ancestor of main
        _git(proj, "checkout", "-q", "-b", "topic")
        assert "ADO003" not in rules(lint_project(proj))

    def test_no_migration_doc_no_warn(self, tmp_path):
        proj = make_project(tmp_path)
        self._init_on_main(proj)
        _git(proj, "checkout", "-q", "-b", "feature")
        (proj / "x.txt").write_text("y")
        _git(proj, "add", ".")
        _git(proj, "commit", "-q", "-m", "unrelated")
        assert "ADO003" not in rules(lint_project(proj))

    def test_migration_without_done_marks_no_warn(self, tmp_path):
        proj = make_project(tmp_path)
        self._init_on_main(proj)
        _git(proj, "checkout", "-q", "-b", "feature")
        (proj / "STAPEL-MIGRATION.md").write_text("# Plan\n- [ ] Phase 3 pending\n")
        _git(proj, "add", ".")
        _git(proj, "commit", "-q", "-m", "plan only")
        assert "ADO003" not in rules(lint_project(proj))


# ---------------------------------------------------------------------------
# ADO005 — a gdpr owner library needs a consumer process and a part
# ---------------------------------------------------------------------------


ERASURE_SCHEMA = json.dumps({"type": "object", "properties": {}})


def make_gdpr_owner(
    workspace,
    short,
    *,
    owner,
    subject_types,
    prefix="",
    decl_file="erasure.py",
    indirect=False,
):
    """A neighbour owner library: it ships the erasure consume-contract (which
    IS the declaration of gdpr participation) and names itself in erasure.py.

    ``prefix="GDPR_"`` produces the workspaces/profiles/notifications spelling;
    ``indirect=True`` produces the recordings/agent shapes (a tuple of module
    constants, an owner read off a class attribute)."""
    repo = make_module(workspace, short, with_urls=False)
    consumes = repo / "schemas" / "consumes"
    consumes.mkdir(parents=True, exist_ok=True)
    (consumes / "gdpr.erasure.requested.json").write_text(ERASURE_SCHEMA)

    lines = []
    if indirect:
        lines += ["class Provider:", f'    section = "{owner}"']
        for index, subject in enumerate(subject_types):
            lines.append(f'SUBJECT_{index} = "{subject}"')
        lines.append(f"{prefix}OWNER = Provider.section")
        names = ", ".join(f"SUBJECT_{i}" for i in range(len(subject_types)))
        lines.append(f"{prefix}SUBJECT_TYPES = ({names},)")
    else:
        lines.append(f'{prefix}OWNER = "{owner}"')
        literals = ", ".join(repr(s) for s in subject_types)
        lines.append(f"{prefix}SUBJECT_TYPES = ({literals},)")
    (repo / decl_file).write_text("\n".join(lines) + "\n")
    return repo


def make_fleet(
    workspace,
    *,
    apps,
    consumer=True,
    data_owners=None,
    gdpr_host=True,
    commented_consumer=False,
    action_transport="bus",
):
    """The fleet shape stapel-tools emits: ``services.conf`` at the root,
    ``svc-<name>/`` service directories, ``svc-<name>.yml`` compose fragments.
    Returns (fleet root, the service project to lint)."""
    fleet = workspace / "fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    services = ["app"] + (["gdpr"] if gdpr_host else [])
    (fleet / "services.conf").write_text("\n".join(services) + "\n")

    app = make_project(fleet, name="svc-app", installed_apps=apps)
    if action_transport is not None:
        app_settings = app / "config" / "settings.py"
        app_settings.write_text(
            app_settings.read_text()
            + f'\nSTAPEL_COMM = {{"ACTION_TRANSPORT": "{action_transport}"}}\n'
        )
    fragment = [
        "services:",
        "  svc-app:",
        '    command: sh -c "sh bootstrap.sh && ${RUN_CMD}"',
    ]
    consumer_lines = [
        "  svc-app-actions:",
        '    command: sh -c "python manage.py consume_actions"',
    ]
    if consumer:
        fragment += consumer_lines
    elif commented_consumer:
        fragment += [f"  # {line.strip()}" for line in consumer_lines]
    (fleet / "svc-app.yml").write_text("\n".join(fragment) + "\n")

    if gdpr_host:
        host = make_project(fleet, name="svc-gdpr", installed_apps=["stapel_gdpr"])
        settings = host / "config" / "settings.py"
        settings.write_text(
            settings.read_text()
            + f"\nSTAPEL_GDPR = {{\n    \"DATA_OWNERS\": {data_owners!r},\n}}\n"
        )
        (fleet / "svc-gdpr.yml").write_text(
            "services:\n  svc-gdpr:\n    command: gunicorn\n"
        )
    return fleet, app


def ado005(findings):
    return [f for f in findings if f.rule == "ADO005"]


class TestGdprOwnerReachable:
    def test_missing_consumer_errors(self, tmp_path):
        make_gdpr_owner(tmp_path, "widget", owner="widget",
                        subject_types=("account",))
        _, app = make_fleet(
            tmp_path,
            apps=["stapel_widget"],
            consumer=False,
            data_owners={"widget": ["account"]},
        )
        findings = ado005(lint_project(app, search_roots=[tmp_path]))
        assert len(findings) == 1
        assert findings[0].level == "error"
        assert "consume_actions" in findings[0].message
        assert "svc-app.yml" in findings[0].message

    def test_commented_consumer_does_not_count(self, tmp_path):
        make_gdpr_owner(tmp_path, "widget", owner="widget",
                        subject_types=("account",))
        _, app = make_fleet(
            tmp_path,
            apps=["stapel_widget"],
            consumer=False,
            commented_consumer=True,
            data_owners={"widget": ["account"]},
        )
        findings = ado005(lint_project(app, search_roots=[tmp_path]))
        assert len(findings) == 1
        assert "consume_actions" in findings[0].message

    def test_owner_missing_from_data_owners_errors(self, tmp_path):
        make_gdpr_owner(tmp_path, "widget", owner="widget",
                        subject_types=("account", "workspace"))
        _, app = make_fleet(
            tmp_path,
            apps=["stapel_widget"],
            data_owners={"other": ["account"]},
        )
        findings = ado005(lint_project(app, search_roots=[tmp_path]))
        assert len(findings) == 1
        assert findings[0].level == "error"
        assert "DATA_OWNERS" in findings[0].message
        assert "'widget'" in findings[0].message

    def test_all_good_is_silent(self, tmp_path):
        make_gdpr_owner(tmp_path, "widget", owner="widget",
                        subject_types=("account", "workspace"))
        _, app = make_fleet(
            tmp_path,
            apps=["stapel_widget"],
            data_owners={"widget": ["account", "workspace"]},
        )
        assert rules(lint_project(app, search_roots=[tmp_path])) == []

    def test_subject_type_not_claimed_errors(self, tmp_path):
        # The owner answers, but only for `account`: an erasure of a workspace
        # never creates a part for it, and completes without its rows.
        make_gdpr_owner(tmp_path, "widget", owner="widget",
                        subject_types=("account", "workspace"))
        _, app = make_fleet(
            tmp_path,
            apps=["stapel_widget"],
            data_owners={"widget": ["account"]},
        )
        findings = ado005(lint_project(app, search_roots=[tmp_path]))
        assert len(findings) == 1
        assert "workspace" in findings[0].message

    def test_legacy_list_form_means_account_only(self, tmp_path):
        # `DATA_OWNERS = ["widget"]` (the pre-0.5.0 shape) means ["account"].
        make_gdpr_owner(tmp_path, "widget", owner="widget",
                        subject_types=("account", "file"))
        _, app = make_fleet(
            tmp_path,
            apps=["stapel_widget"],
            data_owners=["widget"],
        )
        findings = ado005(lint_project(app, search_roots=[tmp_path]))
        assert len(findings) == 1
        assert "file" in findings[0].message

    def test_owner_name_differs_from_module_name(self, tmp_path):
        # stapel-cdn answers to "media", stapel-profiles to "profile": the name
        # is read from the library, never guessed from the package.
        make_gdpr_owner(tmp_path, "blobs", owner="media",
                        subject_types=("account", "file"))
        _, app = make_fleet(
            tmp_path,
            apps=["stapel_blobs"],
            data_owners={"media": ["account", "file"]},
        )
        assert ado005(lint_project(app, search_roots=[tmp_path])) == []

    def test_gdpr_prefixed_constants_are_read(self, tmp_path):
        make_gdpr_owner(tmp_path, "spaces", owner="workspaces",
                        subject_types=("account", "workspace"),
                        prefix="GDPR_")
        _, app = make_fleet(
            tmp_path,
            apps=["stapel_spaces"],
            data_owners={"workspaces": ["account"]},
        )
        findings = ado005(lint_project(app, search_roots=[tmp_path]))
        assert len(findings) == 1
        assert "workspace" in findings[0].message

    def test_indirect_constants_are_resolved(self, tmp_path):
        # `OWNER = Provider.section` + `SUBJECT_TYPES = (SUBJECT_0, ...)` —
        # the agent/recordings shapes, in gdpr.py rather than erasure.py.
        make_gdpr_owner(tmp_path, "notes", owner="notes",
                        subject_types=("account", "workspace"),
                        decl_file="gdpr.py", indirect=True)
        _, app = make_fleet(
            tmp_path,
            apps=["stapel_notes"],
            data_owners={"notes": ["account", "workspace"]},
        )
        assert ado005(lint_project(app, search_roots=[tmp_path])) == []

    def test_module_without_erasure_contract_is_not_an_owner(self, tmp_path):
        make_module(tmp_path, "widget", with_urls=False)
        _, app = make_fleet(
            tmp_path, apps=["stapel_widget"], consumer=False, data_owners={}
        )
        assert ado005(lint_project(app, search_roots=[tmp_path])) == []

    def test_no_deploy_file_skips_with_a_note(self, tmp_path):
        make_gdpr_owner(tmp_path, "widget", owner="widget",
                        subject_types=("account",))
        proj = make_project(tmp_path, installed_apps=["stapel_widget"])
        settings = proj / "config" / "settings.py"
        settings.write_text(
            settings.read_text() + '\nSTAPEL_COMM = {"ACTION_TRANSPORT": "bus"}\n'
        )
        notes = []
        findings = lint_project(proj, search_roots=[tmp_path], notes=notes)
        assert ado005(findings) == []
        assert any("consumer check is skipped" in n for n in notes)

    def test_no_gdpr_host_skips_the_data_owners_half(self, tmp_path):
        make_gdpr_owner(tmp_path, "widget", owner="widget",
                        subject_types=("account",))
        _, app = make_fleet(tmp_path, apps=["stapel_widget"], gdpr_host=False)
        notes = []
        findings = lint_project(app, search_roots=[tmp_path], notes=notes)
        assert ado005(findings) == []
        assert any("DATA_OWNERS check is skipped" in n for n in notes)

    def test_computed_stapel_gdpr_is_not_a_finding(self, tmp_path):
        make_gdpr_owner(tmp_path, "widget", owner="widget",
                        subject_types=("account",))
        fleet, app = make_fleet(
            tmp_path, apps=["stapel_widget"], data_owners={"widget": ["account"]}
        )
        settings = fleet / "svc-gdpr" / "config" / "settings.py"
        settings.write_text(
            settings.read_text().replace("STAPEL_GDPR = {", "STAPEL_GDPR = build({")
            .replace("}\n", "})\n")
        )
        notes = []
        findings = lint_project(app, search_roots=[tmp_path], notes=notes)
        assert ado005(findings) == []
        assert any("DATA_OWNERS check is skipped" in n for n in notes)

    def test_monolith_shape(self, tmp_path):
        # One service, its own docker-compose.yml, gdpr installed in the same
        # settings: the same two obligations, read from one place.
        make_gdpr_owner(tmp_path, "widget", owner="widget",
                        subject_types=("account",))
        proj = make_project(
            tmp_path,
            installed_apps=["stapel_widget", "stapel_gdpr"],
            extra_files={
                "docker-compose.yml": (
                    "services:\n  web:\n    command: gunicorn config.wsgi\n"
                ),
            },
        )
        settings = proj / "config" / "settings.py"
        settings.write_text(
            settings.read_text()
            + '\nSTAPEL_COMM = {"ACTION_TRANSPORT": "bus"}\n'
            + '\nSTAPEL_GDPR = {"DATA_OWNERS": {}}\n'
        )
        findings = ado005(lint_project(proj, search_roots=[tmp_path]))
        assert {"consume_actions" in f.message for f in findings} == {True, False}
        assert len(findings) == 2

    def test_inprocess_delivery_needs_no_consumer(self, tmp_path):
        # stapel-core's default and every monolith: the handler runs in the
        # emitting process. Demanding a consumer process there would be a gate
        # reporting a defect that does not exist — which is how the e2e
        # generated project first met this rule.
        make_gdpr_owner(tmp_path, "widget", owner="widget",
                        subject_types=("account",))
        _, app = make_fleet(
            tmp_path,
            apps=["stapel_widget"],
            consumer=False,
            action_transport="inprocess",
            data_owners={"widget": ["account"]},
        )
        assert ado005(lint_project(app, search_roots=[tmp_path])) == []

    def test_undeclared_transport_is_inprocess(self, tmp_path):
        make_gdpr_owner(tmp_path, "widget", owner="widget",
                        subject_types=("account",))
        _, app = make_fleet(
            tmp_path,
            apps=["stapel_widget"],
            consumer=False,
            action_transport=None,     # no STAPEL_COMM at all
            data_owners={"widget": ["account"]},
        )
        assert ado005(lint_project(app, search_roots=[tmp_path])) == []

    def test_transport_from_getenv_default_is_read(self, tmp_path):
        # The generated settings write every transport as
        # os.getenv("STAPEL_ACTION_TRANSPORT", "<broker default>").
        make_gdpr_owner(tmp_path, "widget", owner="widget",
                        subject_types=("account",))
        _, app = make_fleet(
            tmp_path,
            apps=["stapel_widget"],
            consumer=False,
            action_transport=None,
            data_owners={"widget": ["account"]},
        )
        settings = app / "config" / "settings.py"
        settings.write_text(
            settings.read_text()
            + "\nimport os\n"
            + 'STAPEL_COMM = {"ACTION_TRANSPORT": '
            + 'os.getenv("STAPEL_ACTION_TRANSPORT", "bus")}\n'
        )
        findings = ado005(lint_project(app, search_roots=[tmp_path]))
        assert len(findings) == 1
        assert "consume_actions" in findings[0].message

    def test_monolith_shape_clean(self, tmp_path):
        make_gdpr_owner(tmp_path, "widget", owner="widget",
                        subject_types=("account",))
        proj = make_project(
            tmp_path,
            installed_apps=["stapel_widget", "stapel_gdpr"],
            extra_files={
                "docker-compose.yml": (
                    "services:\n"
                    "  web:\n    command: gunicorn config.wsgi\n"
                    "  actions:\n    command: python manage.py consume_actions\n"
                ),
            },
        )
        settings = proj / "config" / "settings.py"
        settings.write_text(
            settings.read_text()
            + '\nSTAPEL_GDPR = {"DATA_OWNERS": {"widget": ["account"]}}\n'
        )
        assert ado005(lint_project(proj, search_roots=[tmp_path])) == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_json_and_exit_code(self, tmp_path, capsys):
        make_module(tmp_path, "widget")
        proj = make_project(
            tmp_path,
            installed_apps=["stapel_widget"],
            urlpatterns=[],
        )
        code = main([str(proj), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 1
        assert payload["ok"] is False
        assert payload["errors"] == 1
        assert payload["findings"][0]["rule"] == "ADO001"

    def test_clean_exit_zero(self, tmp_path, capsys):
        make_module(tmp_path, "widget")
        proj = make_project(
            tmp_path,
            installed_apps=["stapel_widget"],
            urlpatterns=['path("w/", include("stapel_widget.urls"))'],
        )
        assert main([str(proj)]) == 0
        assert "No adoption issues" in capsys.readouterr().out

    def test_strict_promotes_warnings(self, tmp_path):
        proj = make_project(tmp_path, requirements=["PyJWT"])
        assert main([str(proj)]) == 0            # ADO004 is a warning
        assert main([str(proj), "--strict"]) == 1

    def test_missing_dir_is_usage_error(self, tmp_path):
        assert main([str(tmp_path / "nope")]) == 2
