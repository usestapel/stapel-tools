"""R010/R011 — the source-is-English rules.

Every test asserts BOTH directions: the rule fires on a real violation AND
stays silent on the look-alikes. That is not symmetry for its own sake — while
this rule was being drafted a bad regex silenced it entirely, and its zero
violations read exactly like a clean run.
"""
from stapel_tools.lint import scan_file


def lint(tmp_path, source, name="views.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return [(v.rule, v.line) for v in scan_file(str(path))]


def codes(result):
    return {rule for rule, _ in result}


# --- R010: comments, docstrings, identifiers -----------------------------


def test_russian_comment_is_flagged(tmp_path):
    assert ("R010", 1) in lint(tmp_path, "# \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u0434\u043e\u0441\u0442\u0443\u043f\u0430\nx = 1\n")


def test_russian_docstring_is_flagged(tmp_path):
    result = lint(tmp_path, '"""\u041a\u0430\u043a \u044d\u0442\u043e \u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442."""\nx = 1\n')
    assert ("R010", 1) in result


def test_russian_identifier_is_flagged(tmp_path):
    result = lint(tmp_path, "def \u043f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c():\n    pass\n")
    assert "R010" in codes(result)


def test_string_literals_are_not_flagged(tmp_path):
    """The whole design rests on this exemption.

    Russian inside a string is the legitimate case — i18n catalogues, e-mail
    bodies, fixtures whose Cyrillic is the thing under test. Because data is
    exempt, the rule needs no per-path allowlist, and a rule with no allowlist
    is one nobody learns to silence wholesale.
    """
    source = 'MESSAGE = "\u0412\u0430\u0448 \u043a\u043e\u0434 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u044f"\nTRUTHY = {"\u0434\u0430", "\u043d\u0435\u0442"}\n'
    assert codes(lint(tmp_path, source)) == set()


def test_module_docstring_violation_is_reported_where_it_can_be_suppressed(tmp_path):
    """A module docstring opens on line 1, and line 1 is inside the string —
    a ``# noqa`` there would be text, not a directive. So the violation must
    be reported on the line the Cyrillic actually sits on."""
    source = '"""Title.\n\n\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043c\u043e\u0434\u0443\u043b\u044f.\n"""\nx = 1\n'
    assert ("R010", 3) in lint(tmp_path, source)


def test_noqa_on_the_closing_line_silences_a_docstring(tmp_path):
    source = '"""Title.\n\n\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435.\n"""  # noqa: R010\nx = 1\n'
    assert "R010" not in codes(lint(tmp_path, source))


def test_noqa_silences_a_comment(tmp_path):
    assert codes(lint(tmp_path, "x = 1  # \u043f\u043e\u044f\u0441\u043d\u0435\u043d\u0438\u0435  # noqa: R010\n")) == set()


# --- R011: homoglyphs ----------------------------------------------------


def test_mixed_script_word_is_flagged(tmp_path):
    """A Latin word carrying one Cyrillic letter: reads as Latin, greps as
    neither, survives review because the eye cannot tell the two apart."""
    result = lint(tmp_path, 'NAME = "mi\u0442\u0442udei"\n')
    assert "R011" in codes(result)


def test_escape_sequences_do_not_create_fake_words(tmp_path):
    r"""``\nУточняющий`` used to read as the mixed word ``nУточняющий``.

    Three of the first four hits across the fleet were exactly this, and none
    of them were defects.
    """
    source = 'TEXT = "\\n\u0423\u0442\u043e\u0447\u043d\u044f\u044e\u0449\u0438\u0439 \u0432\u043e\u043f\u0440\u043e\u0441"\nPAT = r"\\b\u0433\u043e\u0442\u043e\u0432\u043e\\b"\n'
    assert "R011" not in codes(lint(tmp_path, source))


def test_regex_character_classes_are_not_homoglyphs(tmp_path):
    """``[a-zА-Я]`` puts ``z`` directly against ``А``."""
    assert "R011" not in codes(lint(tmp_path, 'PAT = r"[a-z\u0410-\u042f]+"\n'))


def test_noqa_silences_a_homoglyph(tmp_path):
    source = 'NAME = "mi\u0442\u0442udei"  # noqa: R011\n'
    assert "R011" not in codes(lint(tmp_path, source))


# --- coverage of test files ---------------------------------------------


def test_the_language_rules_reach_test_files(tmp_path):
    """Layer rules skip tests; these must not.

    Russian names were thickest exactly there — whole classes and test methods
    spelled in Cyrillic, and pytest prints those names. A language rule blind
    to test files would miss its main target.
    """
    from stapel_tools.lint import scan_paths

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "test_thing.py").write_text(
        "class \u0422\u0435\u0441\u0442\u041a\u043b\u0430\u0441\u0441:\n    pass\n", encoding="utf-8"
    )
    assert "R010" in {v.rule for v in scan_paths([str(pkg)])}
