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
