"""The per-project lint profile — the switch that makes an imported LEGACY
project gateable at all.

The arsenal ``stapel-verify`` composes encodes this fleet's contracts. Against
a project stapel did not generate every one of them is noise, in the hundreds,
on the first commit — and a permanently red gate is a gate that is off, minus
the record. ``stapel-lint.toml`` is the record: per surface, ``stapel`` (the
default), ``native`` (the project's own linter IS the gate) or ``off`` (with a
written reason).

What these tests hold down, in order of how badly each would hurt if it broke:

1. no file ⇒ nothing changes (a generated project must be unaffected);
2. ``off``/``native`` without a reason/command is an ERROR in the profile, not
   a quiet pass — the whole mechanism is worthless if it can go silent;
3. a skipped surface is still REPORTED, with its reason, in both renderings;
4. a native gate's exit code is the verdict, and it does not run unless the
   caller asked for it;
5. every composed linter carries a surface — one that did not would escape
   every profile without anybody noticing.
"""
from pathlib import Path

import pytest

from stapel_tools import lint_profile, verify
from stapel_tools.lint_profile import (
    MODE_NATIVE,
    MODE_OFF,
    MODE_STAPEL,
    LintProfile,
    LintProfileError,
    SurfaceProfile,
    load_profile,
    parse_profile,
    render_toml,
)


# ── 1. absent file: the generated-project case is untouched ────────────────

def test_no_profile_file_means_the_full_arsenal(tmp_path):
    profile = load_profile(tmp_path)
    assert profile.present is False
    assert all(profile.for_surface(s).mode == MODE_STAPEL
               for s in lint_profile.SURFACES)
    assert profile.summary() == []


def test_absent_profile_adds_no_notes_to_the_report(tmp_path):
    assert verify.profile_notes(load_profile(tmp_path), []) == []


# ── 2. a profile cannot go silent ───────────────────────────────────────────

def test_off_without_a_reason_is_an_error(tmp_path):
    (tmp_path / lint_profile.PROFILE_FILENAME).write_text(
        '[surface.python]\nmode = "off"\n', encoding="utf-8")
    with pytest.raises(LintProfileError) as exc:
        load_profile(tmp_path)
    assert "reason" in str(exc.value)


def test_native_without_a_command_is_an_error(tmp_path):
    (tmp_path / lint_profile.PROFILE_FILENAME).write_text(
        '[surface.frontend]\nmode = "native"\n', encoding="utf-8")
    with pytest.raises(LintProfileError) as exc:
        load_profile(tmp_path)
    assert "command" in str(exc.value)


def test_a_waiver_without_a_reason_is_an_error(tmp_path):
    (tmp_path / lint_profile.PROFILE_FILENAME).write_text(
        '[waivers]\nSWAP002 = ""\n', encoding="utf-8")
    with pytest.raises(LintProfileError):
        load_profile(tmp_path)


def test_unknown_surface_is_an_error_not_a_shrug(tmp_path):
    (tmp_path / lint_profile.PROFILE_FILENAME).write_text(
        '[surface.backend]\nmode = "off"\nreason = "typo for python"\n',
        encoding="utf-8")
    with pytest.raises(LintProfileError) as exc:
        load_profile(tmp_path)
    assert "unknown surface" in str(exc.value)


def test_unknown_mode_is_an_error(tmp_path):
    (tmp_path / lint_profile.PROFILE_FILENAME).write_text(
        '[surface.python]\nmode = "lenient"\n', encoding="utf-8")
    with pytest.raises(LintProfileError):
        load_profile(tmp_path)


def test_malformed_toml_stops_the_gate_rather_than_degrading(tmp_path):
    (tmp_path / lint_profile.PROFILE_FILENAME).write_text(
        "[surface.python\nmode =", encoding="utf-8")
    with pytest.raises(LintProfileError):
        load_profile(tmp_path)


# ── 3. what is not checked stays visible ────────────────────────────────────

LEGACY_TOML = """
[surface.python]
mode = "native"
command = "ruff check ."

[surface.frontend]
mode = "native"
command = "npm run lint"

[surface.docs]
mode = "off"
reason = "reference docs live in Confluence"

[waivers]
SWAP002 = "presenters are the app's own; ADR-7"
"""


def _legacy_project(tmp_path: Path) -> Path:
    (tmp_path / lint_profile.PROFILE_FILENAME).write_text(LEGACY_TOML, encoding="utf-8")
    return tmp_path


def test_profile_parses_the_three_modes(tmp_path):
    profile = load_profile(_legacy_project(tmp_path))
    assert profile.present
    assert profile.for_surface("python").mode == MODE_NATIVE
    assert profile.for_surface("python").command == "ruff check ."
    assert profile.for_surface("docs").mode == MODE_OFF
    assert profile.for_surface("docs").reason == "reference docs live in Confluence"
    # untouched surfaces keep the arsenal
    assert profile.for_surface("deploy").mode == MODE_STAPEL
    assert profile.waivers == {"SWAP002": "presenters are the app's own; ADR-7"}


def test_every_python_linter_is_skipped_and_every_skip_says_why(tmp_path):
    project = _legacy_project(tmp_path)
    reports = verify.verify_project(project)
    by_name = {r.name: r for r in reports}

    for name, surface in lint_profile.LINTER_SURFACES.items():
        report = by_name[name]
        if surface in ("python", "frontend"):
            assert report.skipped, name
            assert report.mode == MODE_NATIVE
            assert "own linter" in report.notes[0]
        elif surface == "docs":
            assert report.skipped, name
            assert "Confluence" in report.notes[0]
        else:
            assert not report.skipped, name

    # a skipped report is still a report — never a hole in the list
    assert len(reports) == len(lint_profile.LINTER_SURFACES) + 2  # + 2 native gates
    assert all(r.errors == 0 for r in reports if r.skipped)


def test_profile_notes_name_every_ungated_surface(tmp_path):
    project = _legacy_project(tmp_path)
    profile = load_profile(project)
    notes = verify.profile_notes(profile, verify.verify_project(project))
    text = "\n".join(notes)
    assert lint_profile.PROFILE_FILENAME in text
    assert "surface python: native gate: ruff check ." in text
    assert "surface docs: off — reference docs live in Confluence" in text
    assert "waiver SWAP002" in text


def test_a_waiver_that_matched_nothing_is_reported(tmp_path):
    (tmp_path / lint_profile.PROFILE_FILENAME).write_text(
        '[waivers]\nR006 = "legacy responses, tracked in JIRA-4"\n', encoding="utf-8")
    profile = load_profile(tmp_path)
    notes = verify.profile_notes(profile, verify.verify_project(tmp_path))
    assert any("matched nothing" in n for n in notes)


def test_waived_findings_are_dropped_and_counted(tmp_path):
    report = verify.LinterReport(
        "stapel-swap-lint", errors=2, warnings=1,
        findings=[
            {"path": "a.py", "line": 1, "rule": "SWAP002", "message": "m", "level": "error"},
            {"path": "b.py", "line": 2, "rule": "SWAP002", "message": "m", "level": "error"},
            {"path": "c.py", "line": 3, "rule": "SWAP001", "message": "m", "level": "warning"},
        ],
    )
    out = verify._apply_waivers(report, {"SWAP002": "ADR-7"})
    assert out.errors == 0
    assert out.warnings == 1
    assert [f["rule"] for f in out.findings] == ["SWAP001"]
    assert any("waived SWAP002 x2: ADR-7" == n for n in out.notes)


# ── 4. the native gate ──────────────────────────────────────────────────────

def test_native_gate_does_not_run_unless_asked(tmp_path):
    project = _legacy_project(tmp_path)
    calls = []

    def runner(cmd, cwd):
        calls.append(cmd)
        return 0, ""

    reports = verify.verify_project(project, native_runner=runner)
    assert calls == []
    native = [r for r in reports if r.name.startswith("native:")]
    assert len(native) == 2
    assert all(r.skipped for r in native)
    assert all("NOT RUN" in r.notes[0] for r in native)


def test_native_gate_exit_code_is_the_verdict(tmp_path):
    project = _legacy_project(tmp_path)
    seen = []

    def runner(cmd, cwd):
        seen.append((cmd, Path(cwd).name))
        return (1, "src/a.ts:3  no-unused-vars") if "npm" in cmd else (0, "All checks passed!")

    reports = verify.verify_project(project, run_native=True, native_runner=runner)
    native = {r.name: r for r in reports if r.name.startswith("native:")}

    assert sorted(cmd for cmd, _ in seen) == ["npm run lint", "ruff check ."]
    assert native["native:python"].errors == 0
    assert native["native:frontend"].errors == 1
    # the tool's own output IS the evidence — stapel does not parse it
    assert "no-unused-vars" in native["native:frontend"].findings[0]["message"]
    assert native["native:frontend"].findings[0]["rule"] == "NATIVE"


def test_a_red_native_gate_fails_the_whole_run(tmp_path):
    project = _legacy_project(tmp_path)
    reports = verify.verify_project(
        project, run_native=True, native_runner=lambda c, d: (2, "boom"))
    assert sum(r.errors for r in reports) > 0


def test_off_everywhere_is_allowed_and_says_so(tmp_path):
    body = "".join(
        f'[surface.{s}]\nmode = "off"\nreason = "legacy import, ungated by decision"\n\n'
        for s in lint_profile.SURFACES
    )
    (tmp_path / lint_profile.PROFILE_FILENAME).write_text(body, encoding="utf-8")
    profile = load_profile(tmp_path)
    reports = verify.verify_project(tmp_path, profile=profile)
    assert all(r.skipped for r in reports)
    assert sum(r.errors for r in reports) == 0
    # ...but the report cannot hide it
    assert len(verify.profile_notes(profile, reports)) == 1 + len(lint_profile.SURFACES)


# ── 5. no linter may escape the mechanism ───────────────────────────────────

def test_every_composed_linter_carries_a_surface(tmp_path):
    """A linter composed by stapel-verify but absent from LINTER_SURFACES
    would run under every profile, including one that says the surface is
    off — the single failure mode this switch must not have."""
    reports = verify.verify_project(tmp_path)
    composed = {r.name for r in reports if not r.name.startswith("native:")}
    assert composed == set(lint_profile.LINTER_SURFACES)


def test_surfaces_referenced_by_linters_all_exist():
    assert set(lint_profile.LINTER_SURFACES.values()) <= set(lint_profile.SURFACES)


# ── rendering: the writer cannot emit a file the reader rejects ─────────────

def test_render_round_trips_through_the_parser(tmp_path):
    profile = LintProfile(
        surfaces={
            **{s: SurfaceProfile(s) for s in lint_profile.SURFACES},
            "python": SurfaceProfile("python", MODE_NATIVE, command='ruff check "src"'),
            "i18n": SurfaceProfile("i18n", MODE_OFF, reason="no catalogues"),
        },
        waivers={"R006": "legacy"},
    )
    text = render_toml(profile)
    (tmp_path / lint_profile.PROFILE_FILENAME).write_text(text, encoding="utf-8")
    back = load_profile(tmp_path)
    assert back.for_surface("python").command == 'ruff check "src"'
    assert back.for_surface("i18n").reason == "no catalogues"
    assert back.for_surface("docs").mode == MODE_STAPEL
    assert back.waivers == {"R006": "legacy"}


def test_parse_profile_validates_an_in_memory_declaration():
    """Studio seeds a profile into an imported repo; it must be validated by
    the same code the gate reads it with, not a second implementation."""
    with pytest.raises(LintProfileError):
        parse_profile({"surface": {"python": {"mode": "off"}}})
    ok = parse_profile({"surface": {"python": {"mode": "off", "reason": "legacy"}}})
    assert ok.for_surface("python").mode == MODE_OFF
