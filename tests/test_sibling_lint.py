"""stapel-sibling-lint tests — SIB001-005.

The fixtures under ``tests/fixtures/siblings/`` are not invented: each one is
an excerpt of a real repository at a real ref, and the verdict asserted here is
the verdict the linter gives that repository. Recorded 2026-08-26, running the
rule against the working checkouts:

    stapel-chat @ v0.5.0     10 errors — SIB001 x10
                             (stapel_moderation x6, stapel_cdn x2,
                              stapel_tools x2), no `test` extra existed
    stapel-chat @ HEAD        0 errors, 0 warnings — declared + strict CI
    stapel-core @ HEAD       10 errors — SIB001 x9 (stapel_tools, tests/
                             test_contract.py) + SIB002 x1 (stapel_realtime in
                             override_settings(INSTALLED_APPS=...), the 0.44.0
                             incident, still in the tree at HEAD as a settings
                             read that no longer imports)
    stapel-notifications @ HEAD  3 errors — SIB001 x3 (stapel_translate, the
                             loop test) + 1 warning SIB004 (stapel_realtime
                             behind importorskip with no workflow setting
                             STAPEL_TEST_STRICT_SIBLINGS=1)

The fixture trees carry the same defects at smaller scale, so the numbers below
are the fixtures' own; what is recorded from the real runs is which RULE fires
on which FILE, and that is what these tests hold.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from stapel_tools import lint_profile, verify
from stapel_tools.sibling_lint import (
    Violation,
    dist_of,
    lint_paths,
    lint_project,
    main,
    read_declaration,
    strict_siblings_declared,
    suite_files,
)

FIXTURES = Path(__file__).parent / "fixtures" / "siblings"


def _codes(violations) -> list:
    return sorted(v.rule for v in violations)


def _by_file(violations) -> dict:
    out: dict = {}
    for violation in violations:
        out.setdefault(Path(violation.path).name, []).append(violation.rule)
    return {k: sorted(v) for k, v in out.items()}


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _project(root: Path, *, deps=(), test_extra=None, extras=None, name="stapel-thing"):
    """A minimal repo: pyproject + whatever tests the caller writes."""
    lines = ["[project]", f'name = "{name}"', 'version = "0.1.0"', "dependencies = ["]
    lines += [f'    "{d}",' for d in deps]
    lines.append("]")
    if test_extra is not None or extras:
        lines.append("")
        lines.append("[project.optional-dependencies]")
        for extra_name, specs in (extras or {}).items():
            lines.append(f"{extra_name} = [" + ", ".join(f'"{s}"' for s in specs) + "]")
        if test_extra is not None:
            lines.append("test = [" + ", ".join(f'"{s}"' for s in test_extra) + "]")
    _write(root, "pyproject.toml", "\n".join(lines) + "\n")
    return root


# ---------------------------------------------------------------------------
# the recorded runs — one per real incident
# ---------------------------------------------------------------------------


def test_chat_at_v0_5_0_is_flagged():
    """The release that shipped red: three siblings, zero declarations."""
    violations = lint_project(FIXTURES / "chat-0-5-0")
    assert _by_file(violations) == {
        "test_attachments.py": ["SIB001"],
        "test_contract.py": ["SIB001"],
        "test_moderation_seam.py": ["SIB001", "SIB001", "SIB001"],
    }
    assert all(v.level == "error" for v in violations)
    messages = " ".join(v.message for v in violations)
    assert "stapel-moderation" in messages
    assert "stapel-cdn" in messages
    assert "stapel-tools" in messages


def test_chat_at_head_is_clean():
    """The same suite, declared — and the skip guard in test_attachments.py
    stays silent only because the workflow sets the strict flag."""
    assert lint_project(FIXTURES / "chat-head") == []


def test_chat_at_head_without_the_strict_flag_reports_the_guarded_import(tmp_path):
    """Delete one env line from the workflow and SIB004 appears: the proof
    that the contract, not the wording, is what the rule reads."""
    head = FIXTURES / "chat-head"
    copy = tmp_path / "chat"
    _write(copy, "pyproject.toml", (head / "pyproject.toml").read_text())
    for path in (head / "tests").glob("*.py"):
        _write(copy, f"tests/{path.name}", path.read_text())
    workflow = (head / ".github/workflows/ci.yml").read_text()
    _write(copy, ".github/workflows/ci.yml",
           "\n".join(line for line in workflow.splitlines()
                     if "STAPEL_TEST_STRICT_SIBLINGS" not in line))

    violations = lint_project(copy)
    assert _codes(violations) == ["SIB004"]
    assert "stapel-cdn" in violations[0].message
    assert violations[0].level == "warning"


def test_core_at_0_44_0_is_flagged_on_the_settings_string():
    """No import token on the failing line, and it imports all the same."""
    violations = lint_project(FIXTURES / "core-0-44-0")
    assert _by_file(violations) == {
        "test_contract.py": ["SIB001", "SIB001"],
        "test_jwt_ws_origin.py": ["SIB002"],
    }
    settings_finding = next(v for v in violations if v.rule == "SIB002")
    assert "stapel_realtime" in settings_finding.message
    assert "INSTALLED_APPS" in settings_finding.message


def test_notifications_at_head_separates_the_declared_from_the_undeclared():
    """stapel-realtime arrives through a SELF-referential extra and is
    declared; stapel-translate is imported and declared nowhere."""
    violations = lint_project(FIXTURES / "notifications-head")
    assert _by_file(violations) == {
        "test_feed_stream.py": ["SIB004"],
        "test_i18n_loop.py": ["SIB001", "SIB001"],
    }
    assert all("stapel-translate" in v.message for v in violations if v.rule == "SIB001")
    assert "stapel-realtime" in next(v for v in violations if v.rule == "SIB004").message


def test_every_recorded_fixture_names_its_source():
    """A fixture whose provenance is not written down is a fixture nobody can
    check against the thing it claims to be."""
    for case in sorted(FIXTURES.iterdir()):
        pyproject = (case / "pyproject.toml").read_text()
        assert "stapel-" in pyproject
        for path in (case / "tests").glob("*.py"):
            head = path.read_text()[:400]
            assert "stapel-chat" in head or "stapel-core" in head \
                or "stapel-notifications" in head, path


# ---------------------------------------------------------------------------
# SIB001 — imports, at any depth
# ---------------------------------------------------------------------------


def test_sib001_catches_an_import_inside_a_function(tmp_path):
    _project(tmp_path, deps=["stapel-core>=0.1"])
    _write(tmp_path, "tests/test_seam.py",
           "def test_x():\n    from stapel_moderation.registry import reset\n    assert reset\n")
    assert _codes(lint_project(tmp_path)) == ["SIB001"]


def test_sib001_catches_a_plain_import_statement(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "tests/test_seam.py", "import stapel_cdn.kinds\n")
    assert _codes(lint_project(tmp_path)) == ["SIB001"]


def test_sib001_is_silent_for_a_runtime_dependency(tmp_path):
    _project(tmp_path, deps=["stapel-core>=0.45.0,<1.0"])
    _write(tmp_path, "tests/test_seam.py", "from stapel_core.comm import signal\n")
    assert lint_project(tmp_path) == []


def test_sib001_is_silent_for_a_test_extra_dependency(tmp_path):
    _project(tmp_path, test_extra=["pytest", "stapel-moderation>=0.3.0,<1.0"])
    _write(tmp_path, "tests/test_seam.py",
           "def test_x():\n    from stapel_moderation import services\n    assert services\n")
    assert lint_project(tmp_path) == []


def test_the_package_never_reports_itself(tmp_path):
    _project(tmp_path, name="stapel-chat")
    _write(tmp_path, "tests/test_x.py", "from stapel_chat.models import Message\n")
    assert lint_project(tmp_path) == []


def test_a_relative_import_is_not_a_sibling(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "tests/test_x.py", "from .siblings import requires\n")
    assert lint_project(tmp_path) == []


def test_a_non_stapel_import_is_not_this_linters_business(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "tests/test_x.py", "import channels\nfrom daphne import server\n")
    assert lint_project(tmp_path) == []


def test_a_helper_module_inside_tests_is_part_of_the_suite(tmp_path):
    """chat's own `tests/siblings.py` is imported by every test that needs a
    sibling; a hole there is a hole in the suite."""
    _project(tmp_path)
    _write(tmp_path, "tests/helpers.py", "from stapel_geo import boundaries\n")
    assert _codes(lint_project(tmp_path)) == ["SIB001"]


def test_a_fixture_tree_is_data_not_code(tmp_path):
    """A generated project under tests/fixtures/ names apps it never imports
    here — reading it would make every scaffold test a violation."""
    _project(tmp_path)
    _write(tmp_path, "tests/fixtures/proj/settings.py",
           'INSTALLED_APPS = ["stapel_auth"]\n')
    _write(tmp_path, "tests/fixtures/proj/test_generated.py",
           "from stapel_auth.models import Thing\n")
    assert lint_project(tmp_path) == []


def test_source_code_outside_the_suite_is_not_scanned(tmp_path):
    """A sibling imported by the LIBRARY is a runtime dependency question,
    which stapel-adoption-lint and pip already answer."""
    _project(tmp_path)
    _write(tmp_path, "stapel_thing/services.py", "from stapel_geo import boundaries\n")
    assert lint_project(tmp_path) == []


# ---------------------------------------------------------------------------
# SIB002 — a settings string is an import
# ---------------------------------------------------------------------------


def test_sib002_override_settings_keyword(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "tests/test_x.py",
           "from django.test import override_settings\n\n\n"
           '@override_settings(INSTALLED_APPS=["stapel_realtime"])\n'
           "def test_x():\n    assert True\n")
    assert _codes(lint_project(tmp_path)) == ["SIB002"]


def test_sib002_module_constant_and_attribute_assignment(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "tests/test_x.py",
           'INSTALLED_APPS = ["stapel_geo"]\n'
           'settings.INSTALLED_APPS = ["stapel_billing"]\n')
    assert _codes(lint_project(tmp_path)) == ["SIB002", "SIB002"]


def test_sib002_reads_a_modify_settings_dict(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "tests/test_x.py",
           "from django.test import modify_settings\n\n\n"
           '@modify_settings(INSTALLED_APPS={"append": ["stapel_realtime"]})\n'
           "def test_x():\n    assert True\n")
    assert _codes(lint_project(tmp_path)) == ["SIB002"]


def test_sib002_is_silent_when_the_app_is_declared(tmp_path):
    _project(tmp_path, test_extra=["stapel-realtime>=0.1.2,<1.0"])
    _write(tmp_path, "tests/test_x.py",
           'INSTALLED_APPS = ["stapel_realtime", "channels"]\n')
    assert lint_project(tmp_path) == []


def test_other_settings_lists_are_not_installed_apps(tmp_path):
    """MIDDLEWARE and DRF class paths are dotted STRINGS Django imports too —
    but they are core's own paths in every case that has occurred, and a rule
    that guessed at every settings list would flag its own package."""
    _project(tmp_path)
    _write(tmp_path, "tests/test_x.py",
           'MIDDLEWARE = ["stapel_realtime.middleware.Thing"]\n')
    assert lint_project(tmp_path) == []


# ---------------------------------------------------------------------------
# SIB003 / SIB004 — the skip that never ran
# ---------------------------------------------------------------------------


def test_sib003_importorskip_of_an_undeclared_sibling(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "tests/test_x.py",
           'import pytest\n\npytest.importorskip("stapel_cdn")\n')
    violations = lint_project(tmp_path)
    assert _codes(violations) == ["SIB003"]
    assert "asserted nowhere" in violations[0].message


def test_sib004_warns_when_a_declared_sibling_is_skip_guarded(tmp_path):
    _project(tmp_path, test_extra=["stapel-cdn>=0.16.0,<1.0"])
    _write(tmp_path, "tests/test_x.py",
           'import pytest\n\npytest.importorskip("stapel_cdn")\n')
    violations = lint_project(tmp_path)
    assert _codes(violations) == ["SIB004"]
    assert violations[0].level == "warning"


def test_sib004_covers_the_try_except_import_error_form(tmp_path):
    _project(tmp_path, test_extra=["stapel-cdn>=0.16.0,<1.0"])
    _write(tmp_path, "tests/test_x.py",
           "import pytest\n\n\n"
           "def test_x():\n"
           "    try:\n"
           "        from stapel_cdn.kinds import KINDS\n"
           "    except ImportError:\n"
           '        pytest.skip("stapel-cdn is not installed")\n'
           "    assert KINDS\n")
    assert _codes(lint_project(tmp_path)) == ["SIB004"]


def test_a_try_except_that_does_not_skip_is_not_a_guard(tmp_path):
    """Re-raising, or falling back to something real, is a decision that the
    author took and that a runner can still fail on."""
    _project(tmp_path, test_extra=["stapel-cdn>=0.16.0,<1.0"])
    _write(tmp_path, "tests/test_x.py",
           "def test_x():\n"
           "    try:\n"
           "        from stapel_cdn.kinds import KINDS\n"
           "    except ImportError:\n"
           "        raise\n"
           "    assert KINDS\n")
    assert lint_project(tmp_path) == []


def test_the_requires_seam_counts_as_reaching_for_a_sibling(tmp_path):
    """`requires("stapel_cdn")` names the module as a STRING. A rule that
    only read imports would call the declaration an unused extra (SIB005) and
    push a suite off the very seam this contract asks it to use."""
    _project(tmp_path, test_extra=["stapel-cdn>=0.16.0,<1.0"])
    _write(tmp_path, "tests/test_x.py",
           "from siblings import requires\n\n\n"
           '@requires("stapel_cdn")\n'
           "def test_x():\n    assert True\n")
    assert lint_project(tmp_path) == []


def test_the_requires_seam_never_earns_sib004(tmp_path):
    """It is already strict-aware: under STAPEL_TEST_STRICT_SIBLINGS it FAILS
    rather than skips, which is exactly what SIB004 asks for."""
    _project(tmp_path, test_extra=["stapel-cdn>=0.16.0,<1.0"])
    _write(tmp_path, "tests/test_x.py",
           'from siblings import requires\n\n\n@requires("stapel_cdn")\n'
           "def test_x():\n    assert True\n")
    # No workflow, so strict is NOT declared — an importorskip would warn here.
    assert lint_project(tmp_path) == []


def test_requires_on_an_undeclared_sibling_is_still_an_error(tmp_path):
    """The seam is how you reach a declared sibling, not a way around
    declaring one."""
    _project(tmp_path)
    _write(tmp_path, "tests/test_x.py",
           'from siblings import requires\n\n\n@requires("stapel_cdn")\n'
           "def test_x():\n    assert True\n")
    violations = lint_project(tmp_path)
    assert _codes(violations) == ["SIB003"]
    assert 'requires("stapel_cdn")' in violations[0].message


def test_sib004_is_silent_once_a_workflow_sets_the_strict_flag(tmp_path):
    _project(tmp_path, test_extra=["stapel-cdn>=0.16.0,<1.0"])
    _write(tmp_path, "tests/test_x.py",
           'import pytest\n\npytest.importorskip("stapel_cdn")\n')
    _write(tmp_path, ".github/workflows/ci.yml",
           "jobs:\n  test:\n    steps:\n      - env:\n"
           '          STAPEL_TEST_STRICT_SIBLINGS: "1"\n        run: pytest tests/\n')
    assert lint_project(tmp_path) == []
    assert strict_siblings_declared(tmp_path) is True


def test_the_strict_flag_must_actually_be_on(tmp_path):
    _write(tmp_path, ".github/workflows/ci.yml",
           '        env:\n          STAPEL_TEST_STRICT_SIBLINGS: "0"\n')
    assert strict_siblings_declared(tmp_path) is False


# ---------------------------------------------------------------------------
# SIB005 — the extra, read backwards
# ---------------------------------------------------------------------------


def test_sib005_flags_an_extra_nobody_uses(tmp_path):
    _project(tmp_path, test_extra=["pytest", "stapel-geo>=0.4.0,<1.0"])
    _write(tmp_path, "tests/test_x.py", "def test_x():\n    assert True\n")
    violations = lint_project(tmp_path)
    assert _codes(violations) == ["SIB005"]
    assert violations[0].path.endswith("pyproject.toml")
    assert violations[0].level == "warning"


def test_sib005_ignores_the_harness(tmp_path):
    """pytest, channels and daphne are the harness, not modules under
    contract with this one."""
    _project(tmp_path, test_extra=["pytest", "pytest-django", "channels[daphne]>=4.0"])
    _write(tmp_path, "tests/test_x.py", "def test_x():\n    assert True\n")
    assert lint_project(tmp_path) == []


def test_sib005_points_at_the_line_that_declares_it(tmp_path):
    _project(tmp_path, test_extra=["pytest", "stapel-geo>=0.4.0,<1.0"])
    _write(tmp_path, "tests/test_x.py", "def test_x():\n    assert True\n")
    violation = lint_project(tmp_path)[0]
    line = Path(violation.path).read_text().splitlines()[violation.line - 1]
    assert "stapel-geo" in line


# ---------------------------------------------------------------------------
# SIB006 — the committed catalogue describes the module, not the venv
# ---------------------------------------------------------------------------


def _errors_json(root: Path, owners) -> None:
    _write(root, "docs/errors.json", json.dumps(
        [{"code": f"error.400.{owner.split('_')[-1]}_thing", "status": 400,
          "owner": owner} for owner in owners],
        indent=2,
    ) + "\n")


def test_sib006_flags_a_foreign_owned_error_key(tmp_path):
    """The workspace-virtualenv leak: a codegen run with every sibling
    installed publishes keys this package does not own."""
    _project(tmp_path, name="stapel-notifications")
    _errors_json(tmp_path, ["stapel_core", "stapel_notifications", "stapel_chat"])
    violations = lint_project(tmp_path)
    assert _codes(violations) == ["SIB006"]
    assert "stapel_chat" in violations[0].message
    assert violations[0].path.endswith("errors.json")


def test_sib006_allows_core_and_the_module_itself(tmp_path):
    _project(tmp_path, name="stapel-notifications")
    _errors_json(tmp_path, ["stapel_core", "stapel_notifications"])
    assert lint_project(tmp_path) == []


def test_sib006_points_at_the_offending_entry(tmp_path):
    _project(tmp_path, name="stapel-notifications")
    _errors_json(tmp_path, ["stapel_core", "stapel_chat"])
    violation = lint_project(tmp_path)[0]
    line = Path(violation.path).read_text().splitlines()[violation.line - 1]
    assert "error.400.chat_thing" in line


def test_sib006_is_silent_without_the_artifact(tmp_path):
    _project(tmp_path)
    assert lint_project(tmp_path) == []


def test_sib006_survives_a_shape_it_does_not_know(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "docs/errors.json", '{"not": "a list of entries"}\n')
    assert lint_project(tmp_path) == []


def test_sib006_ignores_entries_with_no_owner(tmp_path):
    """Older catalogues predate the `owner` field; absence is not a claim."""
    _project(tmp_path)
    _write(tmp_path, "docs/errors.json",
           '[{"code": "error.400.x", "status": 400}]\n')
    assert lint_project(tmp_path) == []


# ---------------------------------------------------------------------------
# declarations: what counts as one
# ---------------------------------------------------------------------------


def test_a_self_referential_extra_is_expanded(tmp_path):
    """`stapel-notifications[realtime]` inside notifications' own `test`
    extra declares what `realtime` holds — this file, about this file."""
    _project(
        tmp_path,
        name="stapel-notifications",
        test_extra=["stapel-notifications[realtime]", "pytest"],
        extras={"realtime": ["stapel-realtime[channels]>=0.1.2,<1.0"]},
    )
    _write(tmp_path, "tests/test_x.py", "from stapel_realtime import envelope\n")
    assert lint_project(tmp_path) == []


def test_a_non_test_extra_is_not_a_test_declaration(tmp_path):
    """`.[realtime]` is what a DEPLOYMENT may install. The suite's needs are
    the `test` extra, or nothing at all — CI installs `.[test]`."""
    _project(tmp_path, extras={"realtime": ["stapel-realtime>=0.1.2,<1.0"]},
             test_extra=["pytest"])
    _write(tmp_path, "tests/test_x.py", "from stapel_realtime import envelope\n")
    assert _codes(lint_project(tmp_path)) == ["SIB001"]


def test_transitivity_is_not_a_declaration(tmp_path):
    """stapel-chat depends on stapel-realtime, which depends on stapel-core;
    a suite that imports core because realtime dragged it in is exactly the
    accident this class is made of."""
    _project(tmp_path, deps=["stapel-realtime>=0.1.2,<1.0"])
    _write(tmp_path, "tests/test_x.py", "from stapel_core.comm import signal\n")
    assert _codes(lint_project(tmp_path)) == ["SIB001"]


def test_declaration_reading_normalizes_names(tmp_path):
    _project(tmp_path, test_extra=["Stapel_Moderation >= 0.3.0"])
    declaration = read_declaration(tmp_path)
    assert "stapel-moderation" in declaration.test_extra
    assert dist_of("stapel_moderation") == "stapel-moderation"


def test_no_pyproject_means_no_verdict(tmp_path):
    _write(tmp_path, "tests/test_x.py", "from stapel_geo import boundaries\n")
    notes: list = []
    assert lint_project(tmp_path, notes=notes) == []
    assert any("no pyproject.toml" in note for note in notes)


def test_a_pyproject_without_a_project_table_is_not_a_package(tmp_path):
    _write(tmp_path, "pyproject.toml", '[tool.ruff]\nline-length = 100\n')
    _write(tmp_path, "tests/test_x.py", "from stapel_geo import boundaries\n")
    assert lint_project(tmp_path) == []


def test_a_broken_pyproject_does_not_crash_the_gate(tmp_path):
    _write(tmp_path, "pyproject.toml", "[project\nname =\n")
    _write(tmp_path, "tests/test_x.py", "from stapel_geo import boundaries\n")
    assert lint_project(tmp_path) == []


def test_unparseable_test_files_are_skipped_not_fatal(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "tests/test_broken.py", "def (:\n")
    assert lint_project(tmp_path) == []


# ---------------------------------------------------------------------------
# suppression, notes, CLI
# ---------------------------------------------------------------------------


def test_noqa_suppresses_the_named_rule_with_a_reason(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "tests/test_x.py",
           "from stapel_geo import boundaries  # noqa: SIB001 - vendored in CI image\n")
    assert lint_project(tmp_path) == []


def test_a_bare_noqa_suppresses_all_of_them(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "tests/test_x.py", "from stapel_geo import boundaries  # noqa\n")
    assert lint_project(tmp_path) == []


def test_noqa_for_another_rule_does_not_suppress_this_one(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "tests/test_x.py", "from stapel_geo import boundaries  # noqa: E402\n")
    assert _codes(lint_project(tmp_path)) == ["SIB001"]


def test_notes_say_what_was_scanned_and_whether_strict_is_declared(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "tests/test_x.py", "def test_x():\n    assert True\n")
    notes: list = []
    lint_project(tmp_path, notes=notes)
    assert any("1 suite file(s) scanned" in note for note in notes)
    assert any("set by no workflow here" in note for note in notes)


def test_suite_files_finds_conftest_at_the_repo_root(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "conftest.py", "def pytest_configure(config):\n    pass\n")
    assert [p.name for p in suite_files(tmp_path)] == ["conftest.py"]


def test_lint_paths_rejects_a_missing_path(tmp_path):
    with pytest.raises(SystemExit):
        lint_paths([str(tmp_path / "nope")])


def test_cli_json_output_and_exit_code(tmp_path, capsys):
    _project(tmp_path)
    _write(tmp_path, "tests/test_x.py", "from stapel_geo import boundaries\n")
    assert main([str(tmp_path), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["errors"] == 1
    assert payload["violations"][0]["rule"] == "SIB001"
    assert payload["notes"]


def test_cli_is_clean_on_a_declared_tree(tmp_path, capsys):
    _project(tmp_path, test_extra=["stapel-geo>=0.4.0,<1.0"])
    _write(tmp_path, "tests/test_x.py", "from stapel_geo import boundaries\n")
    assert main([str(tmp_path)]) == 0
    assert "No violations found." in capsys.readouterr().out


def test_cli_strict_promotes_the_warnings(tmp_path, capsys):
    _project(tmp_path, test_extra=["pytest", "stapel-geo>=0.4.0,<1.0"])
    _write(tmp_path, "tests/test_x.py", "def test_x():\n    assert True\n")
    assert main([str(tmp_path)]) == 0
    assert main([str(tmp_path), "--strict"]) == 1


def test_cli_reports_a_missing_path_as_a_usage_error(tmp_path, capsys):
    assert main([str(tmp_path / "nope")]) == 2


def test_violation_renders_its_level(tmp_path):
    assert "[SIB001]" in str(Violation("a.py", 3, "SIB001", "m"))
    assert "[SIB004 warning]" in str(Violation("a.py", 3, "SIB004", "m", level="warning"))


# ---------------------------------------------------------------------------
# the wiring — a linter nobody composes is a linter nobody runs
# ---------------------------------------------------------------------------


def test_it_is_actually_wired_into_the_gate():
    assert "stapel-sibling-lint" in verify.COMPOSED_LINTERS
    assert lint_profile.LINTER_SURFACES["stapel-sibling-lint"] == "python"


def test_verify_reports_the_findings(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "tests/test_x.py", "from stapel_geo import boundaries\n")
    reports = {r.name: r for r in verify.verify_project(tmp_path)}
    assert reports["stapel-sibling-lint"].errors == 1
    assert {f["rule"] for f in reports["stapel-sibling-lint"].findings} == {"SIB001"}


def test_the_console_script_is_registered():
    """A CLI documented and not installed is a CLI nobody can run."""
    import tomllib

    root = Path(__file__).resolve().parent.parent
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert scripts["stapel-sibling-lint"] == "stapel_tools.sibling_lint:main"


def test_make_check_runs_it():
    """`make check` is the laptop half of the gate; a rule only in CI is a
    rule found after the tag."""
    root = Path(__file__).resolve().parent.parent
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    assert "sibling-lint" in makefile
    assert "check: lint sibling-lint" in makefile


def test_this_repo_declares_its_own_siblings():
    """Dogfood: stapel-tools' own suite imports stapel_core and friends, and
    used to declare none of them — the same defect, in the repo that ships
    the rule."""
    root = Path(__file__).resolve().parent.parent
    violations = [v for v in lint_project(root) if v.level == "error"]
    assert violations == [], "\n".join(str(v) for v in violations)
