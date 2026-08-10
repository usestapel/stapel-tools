"""Tests for stapel_tools.po_prune — the product-side catalogue fixer.

It answers one question per entry ("does this tree actually contain this
string?") and it must answer it the way the extractor does, not the way a grep
does, because a ``{% blocktranslate %}`` msgid is not a literal anybody typed.
"""
from pathlib import Path

from stapel_tools import po_prune

HEADER = '''msgid ""
msgstr ""
"Language: ru\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
'''


def write_catalog(root: Path, body: str, lang: str = "ru") -> Path:
    path = root / "locale" / lang / "LC_MESSAGES" / "django.po"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")
    return path


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "backend"
    (project / "app").mkdir(parents=True)
    (project / "app" / "views.py").write_text('_("Alive")\n', encoding="utf-8")
    return project


def make_library(tmp_path: Path, package: str = "stapel_notifications") -> Path:
    """A wheel-shaped package: its own catalogue keyed on translation keys,
    plus the key registry where several keys can share one English default."""
    site = tmp_path / "site-packages"
    pkg = site / package
    (pkg / "locale" / "ru" / "LC_MESSAGES").mkdir(parents=True)
    (pkg / "locale" / "ru" / "LC_MESSAGES" / "django.po").write_text(
        HEADER + "\n"
        '#: notification.otp_code.subject\n'
        'msgid "Your verification code"\n'
        'msgstr "Vash kod"\n'
        '\n'
        '#: notification.invitation.subject\n'
        'msgid "You are invited"\n'
        'msgstr "Vas priglasili"\n',
        encoding="utf-8",
    )
    (pkg / "translation_keys.py").write_text(
        'NOTIFICATION_KEYS = {\n'
        '    "notification.otp_code.subject": "Your verification code",\n'
        '    "notification.invitation.subject": "You are invited",\n'
        '    "notification.invitation.new_user.subject": "You are invited",\n'
        '}\n',
        encoding="utf-8",
    )
    return site


# ---------------------------------------------------------------------------
# classification (heuristic mode — no Django in the fixture)
# ---------------------------------------------------------------------------


def test_a_string_the_extraction_finds_is_kept(tmp_path):
    project = make_project(tmp_path)
    write_catalog(project, '#: app/views.py:1\nmsgid "Alive"\nmsgstr "Zhivo"\n')
    findings, mode = po_prune.classify(
        project, search_roots=[], live_msgids={"Alive"})
    assert mode == "extract"
    assert [f.bucket for f in findings] == ["sourced"]


def test_a_string_the_extraction_no_longer_finds_is_dead(tmp_path):
    project = make_project(tmp_path)
    write_catalog(project, (
        '#: app/views.py:1\nmsgid "Alive"\nmsgstr "Zhivo"\n'
        '\n'
        '#: app/views.py:9\nmsgid "Rewritten away"\nmsgstr "Ushlo"\n'
    ))
    findings, _ = po_prune.classify(project, search_roots=[], live_msgids={"Alive"})
    dead = [f.msgid for f in findings if f.bucket == "dead"]
    assert dead == ["Rewritten away"]


def test_a_blocktranslate_msgid_is_alive_although_no_source_holds_the_literal(tmp_path):
    """The reason classification asks the extractor rather than grepping:
    ``{% blocktranslate %}Hi {{ name }}{% endblocktranslate %}`` is extracted as
    ``Hi %(name)s``, a string no file contains. The source scan calls it dead;
    the extractor does not, and the extractor is right."""
    project = make_project(tmp_path)
    (project / "app" / "hi.html").write_text(
        "{% blocktranslate %}Hi {{ name }}{% endblocktranslate %}\n", encoding="utf-8")
    write_catalog(project, (
        '#: app/views.py:1\nmsgid "Alive"\nmsgstr "Zhivo"\n'
        '\n'
        '#: app/hi.html:1\nmsgid "Hi %(name)s"\nmsgstr "Privet, %(name)s"\n'
    ))
    by_extraction, _ = po_prune.classify(
        project, search_roots=[], live_msgids={"Alive", "Hi %(name)s"})
    assert {f.bucket for f in by_extraction} == {"sourced"}

    by_scan, _ = po_prune.classify(project, search_roots=[], mode="heuristic")
    scanned = {f.msgid: f.bucket for f in by_scan}
    assert scanned["Hi %(name)s"] == "stale-ref"  # kept, not deleted


def test_a_library_owned_string_is_foreign_not_dead(tmp_path):
    """The distinction that matters: deleting it hands the string back to the
    library default, which is a silent behaviour change, not a cleanup."""
    project = make_project(tmp_path)
    site = make_library(tmp_path)
    write_catalog(project, (
        '#: app/views.py:1\nmsgid "Alive"\nmsgstr "Zhivo"\n'
        '\n'
        '#: stapel_notifications (subject override)\n'
        'msgid "Your verification code"\nmsgstr "Nash brendovyy kod"\n'
    ))
    findings, _ = po_prune.classify(
        project, search_roots=[site], live_msgids={"Alive"})
    foreign = [f for f in findings if f.bucket == "foreign"]
    assert [f.msgid for f in foreign] == ["Your verification code"]
    assert foreign[0].owner == "stapel_notifications"
    assert "notification.otp_code.subject" in foreign[0].keys


def test_a_shadowed_library_msgid_is_reported_even_though_extraction_finds_it(tmp_path):
    """It survives only because something in this tree quotes the literal —
    a test asserting the override. That is an accident, not ownership."""
    project = make_project(tmp_path)
    site = make_library(tmp_path)
    (project / "tests").mkdir()
    (project / "tests" / "test_subjects.py").write_text(
        'gettext("Your verification code")\n', encoding="utf-8")
    write_catalog(project, (
        '#: tests/test_subjects.py:1\n'
        'msgid "Your verification code"\nmsgstr "Nash brendovyy kod"\n'
    ))
    findings, _ = po_prune.classify(
        project, search_roots=[site], live_msgids={"Your verification code"})
    assert [f.bucket for f in findings] == ["shadow"]


def test_one_msgid_standing_for_several_library_keys_yields_all_of_them(tmp_path):
    """gettext holds one entry per msgid; the library's registry may map
    several keys to the same default. Relocating only the first would drop the
    override for the rest — exactly the trap the product's own note described.
    """
    project = make_project(tmp_path)
    site = make_library(tmp_path)
    write_catalog(project, (
        '#: app/views.py:1\nmsgid "Alive"\nmsgstr "Zhivo"\n'
        '\n'
        '#: parked\nmsgid "You are invited"\nmsgstr "Vas zhdut"\n'
    ))
    findings, _ = po_prune.classify(
        project, search_roots=[site], live_msgids={"Alive"})
    keys = [f for f in findings if f.bucket == "foreign"][0].keys
    assert "notification.invitation.subject" in keys
    assert "notification.invitation.new_user.subject" in keys


def test_an_authored_library_catalog_is_not_pruned(tmp_path):
    """po_prune only ever touches projections of sources."""
    site = make_library(tmp_path)
    findings, _ = po_prune.classify(
        site / "stapel_notifications", search_roots=[], live_msgids=set())
    assert findings == []


# ---------------------------------------------------------------------------
# rewriting
# ---------------------------------------------------------------------------


def test_apply_removes_only_dead_entries_and_leaves_the_rest_byte_identical(tmp_path):
    project = make_project(tmp_path)
    path = write_catalog(project, (
        '#. a translator note worth keeping\n'
        '#: app/views.py:1\nmsgid "Alive"\nmsgstr "Zhivo"\n'
        '\n'
        '#: app/views.py:9\nmsgid "Rewritten away"\nmsgstr "Ushlo"\n'
    ))
    findings, _ = po_prune.classify(project, search_roots=[], live_msgids={"Alive"})
    po_prune.apply_prune(project, findings)
    text = path.read_text(encoding="utf-8")
    assert "Rewritten away" not in text
    assert "#. a translator note worth keeping" in text
    assert '#: app/views.py:1\nmsgid "Alive"\nmsgstr "Zhivo"' in text


def test_apply_leaves_foreign_entries_alone_until_the_seam_is_in_place(tmp_path):
    project = make_project(tmp_path)
    site = make_library(tmp_path)
    path = write_catalog(project, (
        '#: app/views.py:1\nmsgid "Alive"\nmsgstr "Zhivo"\n'
        '\n'
        '#: parked\nmsgid "Your verification code"\nmsgstr "Nash kod"\n'
    ))
    findings, _ = po_prune.classify(
        project, search_roots=[site], live_msgids={"Alive"})
    po_prune.apply_prune(project, findings)
    assert "Your verification code" in path.read_text(encoding="utf-8")

    po_prune.apply_prune(project, findings, buckets=("dead", "foreign", "shadow"))
    assert "Your verification code" not in path.read_text(encoding="utf-8")


def test_the_header_is_never_removed(tmp_path):
    project = make_project(tmp_path)
    path = write_catalog(project, (
        '#: app/views.py:1\nmsgid "Alive"\nmsgstr "Zhivo"\n'
        '\n'
        '#: app/views.py:9\nmsgid "Gone"\nmsgstr "Ushlo"\n'
    ))
    findings, _ = po_prune.classify(project, search_roots=[], live_msgids={"Alive"})
    po_prune.apply_prune(project, findings)
    assert path.read_text(encoding="utf-8").startswith('msgid ""')


def test_a_second_apply_changes_nothing(tmp_path):
    project = make_project(tmp_path)
    path = write_catalog(project, (
        '#: app/views.py:1\nmsgid "Alive"\nmsgstr "Zhivo"\n'
        '\n'
        '#: app/views.py:9\nmsgid "Rewritten away"\nmsgstr "Ushlo"\n'
    ))
    for _ in range(2):
        findings, _mode = po_prune.classify(
            project, search_roots=[], live_msgids={"Alive"})
        po_prune.apply_prune(project, findings)
    once = path.read_text(encoding="utf-8")

    findings, _mode = po_prune.classify(project, search_roots=[], live_msgids={"Alive"})
    assert [f for f in findings if f.bucket == "dead"] == []
    po_prune.apply_prune(project, findings)
    assert path.read_text(encoding="utf-8") == once


def test_idempotence_report_proves_the_second_pass_removes_nothing(tmp_path):
    project = make_project(tmp_path)
    write_catalog(project, (
        '#: app/views.py:1\nmsgid "Alive"\nmsgstr "Zhivo"\n'
        '\n'
        '#: app/views.py:9\nmsgid "Rewritten away"\nmsgstr "Ushlo"\n'
    ))
    report = po_prune.idempotence_report(
        project, search_roots=[], mode="heuristic", live_msgids={"Alive"})
    assert report["entries_before"] == 2
    assert report["entries_after_first_apply"] == 1
    assert report["would_remove_on_second_apply"] == 0
    assert report["idempotent"] is True


# ---------------------------------------------------------------------------
# the relocation snippet
# ---------------------------------------------------------------------------


def test_the_relocation_snippet_carries_every_language_under_the_owner_key(tmp_path):
    project = make_project(tmp_path)
    site = make_library(tmp_path)
    body = ('#: app/views.py:1\nmsgid "Alive"\nmsgstr "Zhivo"\n\n'
            '#: parked\nmsgid "Your verification code"\nmsgstr "%s"\n')
    write_catalog(project, body % "Nash kod")
    write_catalog(project, body % "Our code", lang="en")
    findings, _ = po_prune.classify(
        project, search_roots=[site], live_msgids={"Alive"})
    snippet = po_prune.relocation_snippet(findings)
    assert "STAPEL_NOTIFICATIONS" in snippet
    assert '"notification.otp_code.subject"' in snippet
    assert '"ru": "Nash kod"' in snippet
    assert '"en": "Our code"' in snippet


def test_no_foreign_entries_means_no_snippet(tmp_path):
    project = make_project(tmp_path)
    write_catalog(project, '#: app/views.py:1\nmsgid "Alive"\nmsgstr "Zhivo"\n')
    findings, _ = po_prune.classify(project, search_roots=[], live_msgids={"Alive"})
    assert po_prune.relocation_snippet(findings) == ""


# ---------------------------------------------------------------------------
# modes
# ---------------------------------------------------------------------------


def test_extract_mode_refuses_to_guess_when_it_cannot_run(tmp_path):
    """A tool that silently degrades to a weaker check is the class of defect
    being removed, so --mode extract fails instead."""
    project = make_project(tmp_path)
    write_catalog(project, '#: app/views.py:1\nmsgid "Alive"\nmsgstr "Zhivo"\n')
    try:
        po_prune.classify(project, search_roots=[], mode="extract")
    except RuntimeError as exc:
        assert "makemessages could not be run" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected a RuntimeError")


def test_auto_mode_falls_back_and_names_the_mode_it_used(tmp_path):
    project = make_project(tmp_path)
    write_catalog(project, '#: app/views.py:1\nmsgid "Alive"\nmsgstr "Zhivo"\n')
    _findings, mode = po_prune.classify(project, search_roots=[], mode="auto")
    assert mode == "heuristic"


def test_cli_dry_run_writes_nothing(tmp_path, capsys):
    project = make_project(tmp_path)
    path = write_catalog(project, (
        '#: app/views.py:1\nmsgid "Alive"\nmsgstr "Zhivo"\n'
        '\n'
        '#: app/views.py:9\nmsgid "Rewritten away"\nmsgstr "Ushlo"\n'
    ))
    before = path.read_text(encoding="utf-8")
    assert po_prune.main([str(project), "--search-root", str(tmp_path / "nothing")]) == 0
    assert path.read_text(encoding="utf-8") == before
    assert "dry run" in capsys.readouterr().out
