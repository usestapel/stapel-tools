"""stapel_tools.escape — the one ``# noqa`` grammar every linter shares.

The incident (a client fleet, 2026-09-07): a ``# noqa: SUR002`` sat on a
``permission_classes`` line for two years while ``stapel-surface-lint`` read
no noqa comment at all. These tests pin the grammar itself; the fix to
surface-lint and the escape-lint audit have their own test files.
"""
from stapel_tools import escape


def test_no_marker_is_none():
    assert escape.parse_noqa("permission_classes = [IsAuthenticated]") is None


def test_bare_noqa_is_blanket():
    assert escape.parse_noqa("x = 1  # noqa") == set()


def test_named_rule_is_parsed_and_uppercased():
    assert escape.parse_noqa("x = 1  # noqa: sur002") == {"SUR002"}


def test_multiple_rules_comma_separated():
    assert escape.parse_noqa("x = 1  # noqa: SWAP003, SWAP004") == {"SWAP003", "SWAP004"}


def test_multiple_rules_semicolon_separated():
    assert escape.parse_noqa("x = 1  # noqa: SWAP003; SWAP004") == {"SWAP003", "SWAP004"}


def test_written_reason_after_rule_id_is_read_fine():
    # Only the FIRST token of each comma-separated chunk is a candidate rule
    # id. "storefront login" is one chunk (first token AUTHZ001's own reason
    # text, ignored beyond its first word) and "not an admin view" is a
    # second chunk whose first token becomes a second (harmless, matches
    # nothing real) candidate — the grammar does not try to parse English,
    # it only guarantees the NAMED rule id is never lost to a written reason.
    line = "x = 1  # noqa: AUTHZ001 - storefront login, not an admin view"
    rules = escape.parse_noqa(line)
    assert "AUTHZ001" in rules
    assert escape.is_suppressed(rules, "AUTHZ001") is True


def test_written_reason_does_not_smuggle_in_an_unrelated_real_rule_id():
    # The prose after the dash never accidentally spells a REAL rule id here,
    # so a marker naming AUTHZ001 only ever suppresses AUTHZ001.
    line = "x = 1  # noqa: AUTHZ001 - storefront login, not an admin view"
    assert escape.is_suppressed(escape.parse_noqa(line), "SUR002") is False


def test_is_suppressed_none_means_not_suppressed():
    assert escape.is_suppressed(None, "SUR002") is False


def test_is_suppressed_blanket_matches_anything():
    assert escape.is_suppressed(set(), "SUR002") is True


def test_is_suppressed_named_matches_only_named():
    assert escape.is_suppressed({"SUR002"}, "SUR002") is True
    assert escape.is_suppressed({"SUR002"}, "SUR003") is False


def test_is_suppressed_case_insensitive_on_the_query():
    assert escape.is_suppressed({"SUR002"}, "sur002") is True


def test_line_suppressed_combines_parse_and_check():
    assert escape.line_suppressed("x = 1  # noqa: SUR002", "SUR002") is True
    assert escape.line_suppressed("x = 1  # noqa: SUR002", "SUR003") is False
    assert escape.line_suppressed("x = 1", "SUR002") is False


def test_any_line_suppressed_checks_every_anchor():
    lines = ["a", "b  # noqa: NGX002", "c"]
    assert escape.any_line_suppressed(lines, "NGX002", 1, 2) is True
    assert escape.any_line_suppressed(lines, "NGX002", 1, 3) is False


def test_any_line_suppressed_ignores_out_of_range_anchors():
    lines = ["a  # noqa: NGX002"]
    assert escape.any_line_suppressed(lines, "NGX002", 0, 1, 99) is True


# ---------------------------------------------------------------------------
# RULE_REGISTRY — spot checks, not exhaustive (escape_lint's own tests cover
# the registry's actual USE; this just pins a few load-bearing entries so a
# careless edit here cannot silently widen or narrow what ESC001 accepts).
# ---------------------------------------------------------------------------


def test_registry_has_sur001_through_sur003_as_noqa_aware():
    for rule in ("SUR001", "SUR002", "SUR003"):
        info = escape.RULE_REGISTRY[rule]
        assert info.linter == "stapel-surface-lint"
        assert info.honors_noqa is True


def test_registry_marks_sur004_as_not_noqa_aware():
    # SUR004 reports against package.json, which carries no comment token.
    info = escape.RULE_REGISTRY["SUR004"]
    assert info.linter == "stapel-surface-lint"
    assert info.honors_noqa is False


def test_registry_marks_adoption_lint_rules_as_not_noqa_aware():
    for rule in ("ADO001", "ADO002", "ADO003", "ADO004", "ADO005"):
        assert escape.RULE_REGISTRY[rule].honors_noqa is False


def test_registry_has_no_entry_for_an_unknown_rule():
    assert "SUR099" not in escape.RULE_REGISTRY
    assert "NOTAREALRULE" not in escape.RULE_REGISTRY


def test_registry_distinguishes_noqa_aware_rules_within_one_family():
    # CFG001/006/007 are noqa-aware; CFG002-005 and CFG000 are not — same
    # linter, different constructs (a source line vs. a CONFIG.MD row).
    assert escape.RULE_REGISTRY["CFG001"].honors_noqa is True
    assert escape.RULE_REGISTRY["CFG002"].honors_noqa is False
    assert escape.RULE_REGISTRY["MIG004"].honors_noqa is True
    assert escape.RULE_REGISTRY["MIG001"].honors_noqa is False
