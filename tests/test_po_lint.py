"""Tests for the gettext catalogue gate (stapel_tools.po_lint) and the
extraction wrapper that runs it (stapel_tools.makemessages).

The defect these close: a bare ``makemessages`` over a product tree demotes
every entry whose source it cannot find, gettext skips both demotions, and
nothing goes red — the strings just start shipping in their source language.
"""
from pathlib import Path

import pytest

from stapel_tools import makemessages, po_lint

HEADER = '''msgid ""
msgstr ""
"Language: ru\\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
'''


def write_catalog(root: Path, body: str, lang: str = "ru") -> Path:
    path = root / "locale" / lang / "LC_MESSAGES" / "django.po"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "views.py").write_text(
        'from django.utils.translation import gettext as _\n'
        'msg = _("Hello")\n', encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# the parser
# ---------------------------------------------------------------------------


def test_parser_reads_flags_refs_and_continuation_lines():
    entries = po_lint.parse_po(
        HEADER + '\n'
        '#: app/views.py:2\n'
        '#, fuzzy, python-format\n'
        'msgid "Hello "\n'
        '"world"\n'
        'msgstr "Privet mir"\n'
    )
    assert len(entries) == 2
    entry = entries[1]
    assert entry.msgid == "Hello world"
    assert entry.msgstrs == ["Privet mir"]
    assert entry.flags == {"fuzzy", "python-format"}
    assert entry.refs == ["app/views.py:2"]


def test_parser_separates_an_obsolete_block_from_its_previous_msgid():
    entries = po_lint.parse_po(
        HEADER + '\n'
        '#~| msgid "Old"\n'
        '#~ msgid "Gone"\n'
        '#~ msgstr "Ushlo"\n'
    )
    obsolete = [e for e in entries if e.obsolete]
    assert [e.msgid for e in obsolete] == ["Gone"]


# ---------------------------------------------------------------------------
# PO001 fuzzy — the dangerous one
# ---------------------------------------------------------------------------


def test_a_fuzzy_entry_is_an_error(project: Path):
    write_catalog(project, (
        '#: app/views.py:2\n'
        '#, fuzzy\n'
        'msgid "Hello"\n'
        'msgstr "Privet"\n'
    ))
    violations = po_lint.lint_paths([str(project)])
    assert [v.rule for v in violations] == ["PO001"]
    assert violations[0].level == "error"


def test_a_fuzzy_entry_looks_translated_but_gettext_skips_it(project: Path):
    """The property that makes fuzzy worse than obsolete: it carries a
    translation and sits among the live entries."""
    path = write_catalog(project, (
        '#: app/views.py:2\n'
        '#, fuzzy\n'
        'msgid "Hello"\n'
        'msgstr "Privet"\n'
    ))
    text = path.read_text(encoding="utf-8")
    assert "Privet" in text and "#~" not in text  # nothing looks wrong
    assert po_lint.lint_paths([str(project)])[0].rule == "PO001"


def test_max_fuzzy_baseline_tolerates_a_known_count_but_fails_on_a_rise(project: Path):
    (project / "app" / "more.py").write_text('_("Bye")\n', encoding="utf-8")
    write_catalog(project, (
        '#: app/views.py:2\n#, fuzzy\nmsgid "Hello"\nmsgstr "Privet"\n'
        '\n'
        '#: app/more.py:1\n#, fuzzy\nmsgid "Bye"\nmsgstr "Poka"\n'
    ))
    assert len(po_lint.lint_paths([str(project)], max_fuzzy=2)) == 0
    assert len(po_lint.lint_paths([str(project)], max_fuzzy=1)) == 1


# ---------------------------------------------------------------------------
# PO002 obsolete
# ---------------------------------------------------------------------------


def test_an_obsolete_entry_is_an_error(project: Path):
    write_catalog(project, (
        '#: app/views.py:2\nmsgid "Hello"\nmsgstr "Privet"\n'
        '\n'
        '#~ msgid "Gone"\n#~ msgstr "Ushlo"\n'
    ))
    violations = po_lint.lint_paths([str(project)])
    assert [v.rule for v in violations] == ["PO002"]


def test_a_clean_catalog_reports_nothing(project: Path):
    write_catalog(project, '#: app/views.py:2\nmsgid "Hello"\nmsgstr "Privet"\n')
    assert po_lint.lint_paths([str(project)]) == []


# ---------------------------------------------------------------------------
# PO003 untranslated
# ---------------------------------------------------------------------------


def test_an_empty_msgstr_is_a_warning_not_an_error(project: Path):
    write_catalog(project, '#: app/views.py:2\nmsgid "Hello"\nmsgstr ""\n')
    violations = po_lint.lint_paths([str(project)])
    assert [(v.rule, v.level) for v in violations] == [("PO003", "warning")]


def test_the_header_entry_is_never_reported(project: Path):
    write_catalog(project, '#: app/views.py:2\nmsgid "Hello"\nmsgstr "Privet"\n')
    assert po_lint.lint_paths([str(project)]) == []


# ---------------------------------------------------------------------------
# PO004 ownership — a catalogue is a projection of its own sources
# ---------------------------------------------------------------------------


def test_an_entry_whose_reference_names_another_package_is_unowned(project: Path):
    write_catalog(project, (
        '#: app/views.py:2\nmsgid "Hello"\nmsgstr "Privet"\n'
        '\n'
        '#: stapel_notifications translation_keys.py (subject override)\n'
        'msgid "Your verification code"\n'
        'msgstr "Vash kod"\n'
    ))
    violations = po_lint.lint_paths([str(project)])
    assert [(v.rule, v.level) for v in violations] == [("PO004", "warning")]
    assert "projection of its own sources" in violations[0].message


def test_a_reference_without_a_line_number_still_counts_as_owned(project: Path):
    """Catalogues whose line numbers were stripped are still projections."""
    write_catalog(project, '#: app/views.py\nmsgid "Hello"\nmsgstr "Privet"\n')
    assert po_lint.lint_paths([str(project)]) == []


def test_an_authored_library_catalog_is_not_judged_on_ownership(tmp_path: Path):
    """A catalogue whose ``#:`` slot holds translation keys rather than paths
    is not a projection of anything, so PO004 must stay silent on it — this is
    how the fleet's own library catalogues are written."""
    write_catalog(tmp_path, (
        '#: notification.otp_code.subject\n'
        'msgid "Your {company_name} verification code: {code}"\n'
        'msgstr "Vash kod"\n'
        '\n'
        '#: notification.otp_code.heading\n'
        'msgid "Your verification code"\n'
        'msgstr "Vash kod podtverzhdeniya"\n'
    ))
    assert po_lint.lint_paths([str(tmp_path)]) == []


def test_ownership_never_fires_on_an_obsolete_entry(project: Path):
    """PO002 already covers it; two findings for one entry is noise."""
    write_catalog(project, (
        '#: app/views.py:2\nmsgid "Hello"\nmsgstr "Privet"\n'
        '\n'
        '#~ msgid "Gone"\n#~ msgstr "Ushlo"\n'
    ))
    rules = {v.rule for v in po_lint.lint_paths([str(project)])}
    assert rules == {"PO002"}


# ---------------------------------------------------------------------------
# discovery + CLI
# ---------------------------------------------------------------------------


def test_discovery_skips_vendored_trees(project: Path):
    write_catalog(project, '#: app/views.py:2\nmsgid "Hello"\nmsgstr "Privet"\n')
    vendored = project / ".venv" / "lib" / "site-packages" / "django"
    write_catalog(vendored, '#, fuzzy\nmsgid "x"\nmsgstr "y"\n')
    assert po_lint.lint_paths([str(project)]) == []


def test_cli_returns_1_on_an_error_and_0_when_clean(project: Path, capsys):
    write_catalog(project, '#: app/views.py:2\nmsgid "Hello"\nmsgstr "Privet"\n')
    assert po_lint.main([str(project)]) == 0

    write_catalog(project, '#: app/views.py:2\n#, fuzzy\nmsgid "Hello"\nmsgstr "Privet"\n')
    assert po_lint.main([str(project)]) == 1
    assert "PO001" in capsys.readouterr().out


def test_cli_strict_promotes_warnings(project: Path):
    write_catalog(project, '#: nowhere/at/all.py:1\nmsgid "Hello"\nmsgstr "Privet"\n')
    # not a projection at all (nothing resolves) -> PO004 stays silent
    write_catalog(project, (
        '#: app/views.py:2\nmsgid "Hello"\nmsgstr "Privet"\n'
        '\n'
        '#: someone_elses_package (parked)\nmsgid "Theirs"\nmsgstr "Ikh"\n'
    ))
    assert po_lint.main([str(project)]) == 0
    assert po_lint.main([str(project), "--strict"]) == 1


def test_cli_rejects_a_missing_path():
    assert po_lint.main(["/no/such/place"]) == 2


# ---------------------------------------------------------------------------
# the wrapper: an extraction that would un-translate leaves nothing behind
# ---------------------------------------------------------------------------


class _FakeRun:
    """Stands in for the makemessages subprocess: rewrites the catalogue the
    way a real demoting run would, so the wrapper's rollback is what is under
    test rather than Django's extractor."""

    def __init__(self, path: Path, new_text: str, returncode: int = 0):
        self.path = path
        self.new_text = new_text
        self.returncode = returncode
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if self.returncode == 0:
            self.path.write_text(self.new_text, encoding="utf-8")

        class _Result:
            returncode = self.returncode

        return _Result()


def test_the_wrapper_rolls_back_an_extraction_that_would_demote(project, monkeypatch):
    path = write_catalog(project, '#: app/views.py:2\nmsgid "Hello"\nmsgstr "Privet"\n')
    good = path.read_text(encoding="utf-8")
    demoted = HEADER + '\n#~ msgid "Hello"\n#~ msgstr "Privet"\n'

    fake = _FakeRun(path, demoted)
    monkeypatch.setattr(makemessages.subprocess, "run", fake)

    assert makemessages.run(project) == 1
    assert path.read_text(encoding="utf-8") == good, "catalogue must be restored"


def test_the_wrapper_keeps_the_result_with_accept_losses(project, monkeypatch):
    path = write_catalog(project, '#: app/views.py:2\nmsgid "Hello"\nmsgstr "Privet"\n')
    demoted = HEADER + '\n#~ msgid "Hello"\n#~ msgstr "Privet"\n'
    monkeypatch.setattr(makemessages.subprocess, "run", _FakeRun(path, demoted))

    assert makemessages.run(project, accept_losses=True) == 0
    assert "#~" in path.read_text(encoding="utf-8")


def test_the_wrapper_keeps_a_clean_extraction(project, monkeypatch):
    path = write_catalog(project, '#: app/views.py:2\nmsgid "Hello"\nmsgstr "Privet"\n')
    clean = HEADER + '\n#: app/views.py:2\nmsgid "Hello"\nmsgstr "Privet"\n\n' \
                     '#: app/views.py:3\nmsgid "Bye"\nmsgstr "Poka"\n'
    monkeypatch.setattr(makemessages.subprocess, "run", _FakeRun(path, clean))

    assert makemessages.run(project) == 0
    assert "Poka" in path.read_text(encoding="utf-8")


def test_the_wrapper_restores_after_a_failed_extraction(project, monkeypatch):
    path = write_catalog(project, '#: app/views.py:2\nmsgid "Hello"\nmsgstr "Privet"\n')
    good = path.read_text(encoding="utf-8")
    monkeypatch.setattr(makemessages.subprocess, "run", _FakeRun(path, "", returncode=3))

    assert makemessages.run(project) == 3
    assert path.read_text(encoding="utf-8") == good


def test_the_wrapper_names_the_ignores_django_does_not_apply_itself(project, monkeypatch):
    path = write_catalog(project, '#: app/views.py:2\nmsgid "Hello"\nmsgstr "Privet"\n')
    fake = _FakeRun(path, path.read_text(encoding="utf-8"))
    monkeypatch.setattr(makemessages.subprocess, "run", fake)
    makemessages.run(project)
    cmd = fake.calls[0]
    assert "--all" in cmd
    for pattern in ("node_modules", "staticfiles", "media"):
        assert pattern in cmd
