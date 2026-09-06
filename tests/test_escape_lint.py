"""stapel-escape-lint tests — ESC001 (inert escape) / ESC002 (stale escape).

Incident (a client fleet, 2026-09-07): a ``# noqa: SUR002`` sat on a
``permission_classes`` line for two years — ``stapel-surface-lint`` read no
noqa comment at all, so the marker suppressed nothing and nothing said so.
These tests exercise the audit that now catches exactly that shape, plus its
sibling failure (a marker naming a rule id nothing has ever heard of) and the
"used to work, doesn't any more" shape (a stale escape).
"""
from pathlib import Path

from stapel_tools import escape, escape_lint
from stapel_tools.escape_lint import (
    Marker,
    check_inert,
    check_stale,
    collect_raw_findings,
    lint_project,
    scan_markers,
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def codes(findings):
    return sorted(f.rule for f in findings)


# ---------------------------------------------------------------------------
# scan_markers
# ---------------------------------------------------------------------------


def test_scan_markers_finds_a_named_rule(tmp_path):
    _write(tmp_path, "app/views.py", "x = 1  # noqa: SUR002\n")
    markers = scan_markers(tmp_path)
    assert len(markers) == 1
    assert markers[0].line == 1
    assert markers[0].rules == frozenset({"SUR002"})


def test_scan_markers_ignores_bare_blanket_marker(tmp_path):
    _write(tmp_path, "app/views.py", "x = 1  # noqa\n")
    assert scan_markers(tmp_path) == []


def test_scan_markers_ignores_a_ruff_style_code(tmp_path):
    """`# noqa: F401` is ruff's own grammar for a rule id that was never a
    stapel rule id — never in scope for this scan, and never claimed to be:
    it fails the "looks like a stapel rule id" shape (letters + 3 digits)."""
    _write(tmp_path, "app/views.py", "import os  # noqa: F401\n")
    assert scan_markers(tmp_path) == []


def test_scan_markers_only_scans_known_suffixes(tmp_path):
    _write(tmp_path, "README.md", "See `# noqa: SUR002` in the docs.\n")
    assert scan_markers(tmp_path) == []


# ---------------------------------------------------------------------------
# ESC001 — inert escape
# ---------------------------------------------------------------------------


def test_esc001_flags_an_unknown_rule_id():
    marker = Marker("app/views.py", 3, frozenset({"SUR099"}))
    findings = check_inert([marker])
    assert codes(findings) == ["ESC001"]
    assert "SUR099" in findings[0].message
    assert "suppresses nothing" in findings[0].message


def test_esc001_flags_a_known_rule_whose_linter_never_reads_noqa():
    """ADO001 is a real rule id (stapel-adoption-lint) — it just never reads
    a noqa marker on any construct at all."""
    marker = Marker("app/views.py", 3, frozenset({"ADO001"}))
    findings = check_inert([marker])
    assert codes(findings) == ["ESC001"]
    assert "stapel-adoption-lint" in findings[0].message
    assert "ADO001" in findings[0].message


def test_esc001_quiet_for_a_noqa_aware_rule():
    marker = Marker("app/views.py", 3, frozenset({"SUR002"}))
    assert check_inert([marker]) == []


def test_esc001_one_finding_per_offending_rule_on_a_multi_rule_marker():
    marker = Marker("app/views.py", 3, frozenset({"SUR002", "SUR099", "ADO001"}))
    findings = check_inert([marker])
    assert codes(findings) == ["ESC001", "ESC001"]
    rules_named = {f.message.split("'# noqa: ")[1].split("'")[0] for f in findings}
    assert rules_named == {"SUR099", "ADO001"}


def test_esc001_distinguishes_noqa_aware_rules_within_one_family():
    """CFG001 is noqa-aware; CFG002 (same linter) is not — the construct
    differs (a source line vs. a CONFIG.MD row), and ESC001 must key on the
    exact rule id, not the family prefix."""
    findings = check_inert([
        Marker("settings.py", 1, frozenset({"CFG001"})),
        Marker("settings.py", 2, frozenset({"CFG002"})),
    ])
    assert codes(findings) == ["ESC001"]
    assert findings[0].line == 2


# ---------------------------------------------------------------------------
# ESC002 — stale escape
# ---------------------------------------------------------------------------


def test_esc002_flags_a_marker_that_never_fired():
    marker = Marker("app/views.py", 5, frozenset({"SUR002"}))
    findings = check_stale([marker], raw_findings={})
    assert codes(findings) == ["ESC002"]
    assert findings[0].level == "warning"
    assert "did not fire" in findings[0].message


def test_esc002_quiet_when_the_raw_pass_found_the_same_rule_on_the_same_line():
    marker = Marker("app/views.py", 5, frozenset({"SUR002"}))
    raw = {("app/views.py", 5): {"SUR002"}}
    assert check_stale([marker], raw) == []


def test_esc002_quiet_for_a_rule_that_is_not_noqa_aware():
    # Not this rule's territory — ESC001 already said this marker is inert;
    # ESC002 must not pile a second, misleading finding on top of it.
    marker = Marker("app/views.py", 5, frozenset({"ADO001"}))
    assert check_stale([marker], raw_findings={}) == []


def test_esc002_quiet_for_an_unknown_rule():
    marker = Marker("app/views.py", 5, frozenset({"SUR099"}))
    assert check_stale([marker], raw_findings={}) == []


def test_esc002_a_fired_rule_on_a_different_line_still_counts_as_stale():
    marker = Marker("app/views.py", 5, frozenset({"SUR002"}))
    raw = {("app/views.py", 6): {"SUR002"}}  # one line off
    assert codes(check_stale([marker], raw)) == ["ESC002"]


# ---------------------------------------------------------------------------
# collect_raw_findings — the noqa-disabled re-run
# ---------------------------------------------------------------------------


class _Violation:
    def __init__(self, path, line, rule):
        self.path, self.line, self.rule = path, line, rule


def test_collect_raw_findings_reads_dict_and_object_shapes():
    def as_dicts():
        return [{"path": "a.py", "line": 1, "rule": "SUR002"}]

    def as_objects():
        return [_Violation("b.py", 2, "SUR003")]

    raw = collect_raw_findings([as_dicts, as_objects])
    assert raw == {("a.py", 1): {"SUR002"}, ("b.py", 2): {"SUR003"}}


def test_collect_raw_findings_skips_a_raising_callable():
    def boom():
        raise RuntimeError("linter crashed")

    def fine():
        return [{"path": "a.py", "line": 1, "rule": "SUR002"}]

    assert collect_raw_findings([boom, fine]) == {("a.py", 1): {"SUR002"}}


def test_collect_raw_findings_disables_and_restores_parse_noqa():
    seen_during = {}

    def probe():
        seen_during["parse_noqa_is_default"] = escape.parse_noqa("x  # noqa: SUR002") is None
        return []

    original = escape.parse_noqa
    collect_raw_findings([probe])
    assert seen_during["parse_noqa_is_default"] is True  # disabled -> None during the run
    assert escape.parse_noqa is original  # restored after


def test_collect_raw_findings_actually_disables_suppression_end_to_end(tmp_path):
    """The real point of the whole mechanism: a linter that suppresses a
    finding via ``escape.line_suppressed`` must still surface it here."""
    from stapel_tools import surface_lint

    core = tmp_path / "stapel-core"
    (core / "docs").mkdir(parents=True)
    (core / "docs" / "capabilities.json").write_text(
        '{"module": "stapel-core", "version": "1.0.0", "provides": "x", '
        '"axes": [], "extension_points": [], "requires": [], "surface": [{'
        '"name": "IsNotAnonymousUser", "kind": "permission_class", '
        '"path": "stapel_core.django.api.permissions.IsNotAnonymousUser", '
        '"intent": "gate"}]}'
    )
    proj = tmp_path / "proj"
    _write(proj, "permissions.py", (
        "from rest_framework import permissions\n\n\n"
        "class IsNotAnonymousUser(permissions.BasePermission):  # noqa: SUR001\n"
        "    pass\n"
    ))

    def run_surface():
        return surface_lint.lint_project(
            proj, search_roots=[tmp_path], use_installed=False,
        )

    # Suppressed normally:
    assert run_surface() == []
    # ...but the underlying condition is real: the raw pass sees it.
    raw = collect_raw_findings([run_surface])
    assert raw.get((str(proj / "permissions.py"), 4)) == {"SUR001"}
    # And suppression is back to normal afterwards.
    assert run_surface() == []


# ---------------------------------------------------------------------------
# lint_project — the driver
# ---------------------------------------------------------------------------


def test_lint_project_esc001_only_without_composed(tmp_path):
    _write(tmp_path, "app/views.py", (
        "x = 1  # noqa: SUR099\n"
        "y = 2  # noqa: ADO001\n"
    ))
    notes: list = []
    findings = lint_project(tmp_path, notes=notes)
    assert codes(findings) == ["ESC001", "ESC001"]


def test_lint_project_notes_esc002_skipped_when_a_noqa_aware_marker_exists_but_no_composed(
    tmp_path,
):
    _write(tmp_path, "app/views.py", "x = 1  # noqa: SUR002\n")
    notes: list = []
    findings = lint_project(tmp_path, notes=notes)
    assert findings == []  # SUR002 is noqa-aware — not ESC001's business
    assert any("ESC002" in n and "skipped" in n for n in notes)


def test_lint_project_no_note_when_nothing_noqa_aware_is_present(tmp_path):
    _write(tmp_path, "app/views.py", "x = 1  # noqa: SUR099\n")
    notes: list = []
    lint_project(tmp_path, notes=notes)
    assert notes == []


def test_lint_project_runs_esc002_when_composed_is_given(tmp_path):
    _write(tmp_path, "app/views.py", "x = 1  # noqa: SUR002\n")

    def nothing_fires():
        return []

    findings = lint_project(tmp_path, composed=[nothing_fires])
    assert codes(findings) == ["ESC002"]


def test_cli_json_shape(tmp_path, capsys):
    _write(tmp_path, "app/views.py", "x = 1  # noqa: SUR099\n")
    code = escape_lint.main([str(tmp_path), "--json"])
    assert code == 1
    out = capsys.readouterr().out
    import json
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["errors"] == 1
    assert payload["findings"][0]["rule"] == "ESC001"


def test_cli_clean_exit_code(tmp_path, capsys):
    code = escape_lint.main([str(tmp_path)])
    assert code == 0
    assert "clean" in capsys.readouterr().out
