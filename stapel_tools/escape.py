"""
stapel_tools.escape — the one ``# noqa`` grammar every linter in this package
parses, and the registry of what a marker naming a rule actually does.

Why this exists
----------------
On 2026-09-07 a client fleet's host (svc-agent) carried ``# noqa: SUR002`` on a
``permission_classes`` line for two years. ``stapel-surface-lint`` has never
read a noqa comment at all — the marker suppressed nothing, and nothing
noticed, because every OTHER linter in this package that DOES read noqa
comments carried its own hand-rolled reimplementation of the same dozen
lines of string-splitting, each with small drift (case folding, first-token-
of-chunk vs whole-chunk matching, blanket handling). A marker naming a rule
outside whatever set a given file happened to check was never a parse error
— it was silently inert, and looked identical to one that worked.

This module is the mechanism-level fix, not just the one incident:

* :func:`parse_noqa` / :func:`is_suppressed` / :func:`line_suppressed` /
  :func:`any_line_suppressed` are the ONE escape grammar. Every linter in
  this package that reads ``# noqa`` calls into these — no linter keeps its
  own copy of the split/strip/uppercase logic any more.
* :data:`RULE_REGISTRY` is the fleet's own map of "this rule id belongs to
  this linter, and does a ``# noqa: <ID>`` marker on its reported construct
  do anything at all" — the table :mod:`stapel_tools.escape_lint` (ESC001 /
  ESC002) checks every marker in a tree against.

Grammar
-------
``# noqa`` alone (no colon) is a BLANKET suppression: it silences whatever a
caller checks against that line. ``# noqa: RULE[, RULE ...]`` names one or
more rule ids, comma- or semicolon-separated; only the FIRST whitespace
token of each chunk is taken as the rule id, so a written reason on the same
line reads fine and is not part of the grammar it breaks::

    # noqa: AUTHZ001 - storefront login, not an admin view
    # noqa: SWAP003, SWAP004 - both are the vendored fallback path

Rule ids are matched case-insensitively and normalized to upper case: every
rule id in this fleet is canonically upper case, and a linter should not
care that someone typed ``sur002``.

A handful of linters carry a SECOND, family-specific escape on top of this
one — ``# stapel: strict-authenticator`` (AUTHZ007, class-scoped), ``#
stapel: env-address-ok`` (env-address-lint), the CFG007-only-a-NAMED-noqa-
counts rule in config-lint. Those stay exactly as each linter already
implements them; this module is only the common ``# noqa: RULE`` grammar
underneath, and none of those markers are read by :mod:`escape_lint` — a
marker outside the ``# noqa: RULE`` grammar is not this module's claim to
verify.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

__all__ = [
    "parse_noqa",
    "is_suppressed",
    "line_suppressed",
    "any_line_suppressed",
    "RuleInfo",
    "RULE_REGISTRY",
]


def parse_noqa(line: str) -> Optional[set[str]]:
    """Parse one source line for a ``# noqa`` escape.

    Returns:
        ``None`` — the line carries no ``# noqa`` marker at all.
        ``set()`` — a BLANKET ``# noqa`` (no ``:``): suppresses anything a
            caller checks against this line.
        ``{RULE, ...}`` — the explicit, upper-cased rule ids named after
            ``# noqa:``.
    """
    if "# noqa" not in line:
        return None
    if "# noqa:" not in line:
        return set()
    tail = line.split("# noqa:", 1)[1]
    rules: set[str] = set()
    for chunk in tail.replace(";", ",").split(","):
        token = chunk.strip().split()[:1]
        if token:
            rules.add(token[0].upper())
    return rules


def is_suppressed(rules: Optional[set[str]], rule_id: str) -> bool:
    """Does an already-parsed :func:`parse_noqa` result silence *rule_id*?"""
    return rules is not None and (not rules or rule_id.upper() in rules)


def line_suppressed(line: str, rule_id: str) -> bool:
    """Parse *line* and check in one call — the common single-line shape.

    Called through the module (``escape.line_suppressed(...)``) rather than
    imported by name in every linter, on purpose: :mod:`stapel_tools.
    escape_lint`'s ESC002 (stale-escape) check needs to run the whole arsenal
    a second time with the grammar disabled, and it does that by swapping
    ``escape.parse_noqa`` for a no-op for the duration of that pass — which
    only reaches every call site if they resolve ``parse_noqa`` through this
    module's namespace at call time instead of binding the function locally
    at import time.
    """
    return is_suppressed(parse_noqa(line), rule_id)


def any_line_suppressed(lines: list[str], rule_id: str, *line_numbers: int) -> bool:
    """True if any of *line_numbers* (1-based, into *lines*) carries a noqa
    that silences *rule_id* — the "anchor plus the finding's own line" shape
    nginx-cache-lint / env-address-lint / frontend-delivery-lint check."""
    for number in line_numbers:
        if not (0 < number <= len(lines)):
            continue
        if line_suppressed(lines[number - 1], rule_id):
            return True
    return False


@dataclass(frozen=True)
class RuleInfo:
    """One rule id this version of stapel-tools knows about."""

    #: display name — matches ``stapel_tools.verify.LinterReport.name``
    linter: str
    #: does that linter's code actually read a noqa marker naming this rule
    #: anywhere on its construct?
    honors_noqa: bool
    #: what the marker would sit on — used in the ESC001 message
    construct: str


#: The fleet's own rule catalogue for THIS stapel-tools version. A rule id
#: absent from this dict is, by definition, unknown to every linter this
#: version ships — exactly the ESC001 "this marker suppresses nothing" case
#: for an id that is misspelled, retired, or was never a stapel rule at all
#: (a ruff/flake8 code like ``F401`` is deliberately never registered here —
#: :mod:`stapel_tools.escape_lint` only ever looks at markers shaped like a
#: stapel rule id, see its own module docstring).
RULE_REGISTRY: dict[str, RuleInfo] = {}


def _register(linter: str, honors_noqa: bool, construct: str, *ids: str) -> None:
    for rule_id in ids:
        RULE_REGISTRY[rule_id] = RuleInfo(linter, honors_noqa, construct)


_register(
    "stapel-lint", True, "the reported line",
    "R001", "R002", "R003", "R004", "R005", "R006", "R007", "R008",
    "R009", "R010", "R011",
)
# R100 is a repo-level README check (i18n-shipping.md §4) with nothing to
# annotate a single line of — deliberately unregistered, so a noqa marker
# naming it reads as unknown, which it is.

_register(
    "stapel-adoption-lint", False,
    "a urlconf/requirements/migration-status finding (no per-line escape wired)",
    "ADO001", "ADO002", "ADO003", "ADO004", "ADO005",
)

_register("stapel-url-lint", True, "the field's line", "URL001")

_register(
    "stapel-authz-lint", True, "the reported line",
    "AUTHZ001", "AUTHZ002", "AUTHZ003", "AUTHZ004", "AUTHZ005", "AUTHZ006",
    "AUTHZ007",
)

_register(
    "stapel-sibling-lint", True, "the reported line",
    "SIB001", "SIB002", "SIB003", "SIB004", "SIB005",
)
_register(
    "stapel-sibling-lint", False,
    "a docs/errors.json entry (JSON carries no comment token to write a noqa in)",
    "SIB006",
)

_register(
    "stapel-api-lint", False, "a schema-diff finding (no source line to annotate)",
    "API001", "API002", "API003", "SCHEMA001",
)

_register("stapel-config-lint", True, "the reported line", "CFG001", "CFG006", "CFG007")
_register(
    "stapel-config-lint", False,
    "a CONFIG.MD row (no source line to annotate)",
    "CFG000", "CFG002", "CFG003", "CFG004", "CFG005",
)

_register(
    "stapel-migration-lint", False,
    "the destructive-op line (no per-line escape wired for this rule)",
    "MIG001", "MIG002", "MIG003", "MIG005",
)
_register("stapel-migration-lint", True, "the AddField line", "MIG004")
_register(
    "stapel-migration-lint", False,
    "a stale '# stapel: irreversible' marker finding (no per-line escape wired)",
    "MIG102",
)
_register(
    "stapel-migration-lint", False,
    "an unanalyzable-migration note (no per-line escape wired)",
    "MIG101",
)

_register(
    "stapel-swap-lint", True, "the reported line",
    "SWAP001", "SWAP002", "SWAP003", "SWAP004",
)

_register("stapel-doc-lint", True, "the field's line", "DOC001")

_register(
    "stapel-index-lint", False,
    "a docs/index.json / model-field finding (no per-line escape wired)",
    "IDX001", "IDX002", "IDX003", "IDX004", "IDX005",
)

_register(
    "stapel-surface-lint", True, "the reported line",
    "SUR001", "SUR002", "SUR003",
)
_register(
    "stapel-surface-lint", False,
    "the consuming package's package.json (JSON carries no comment token)",
    "SUR004",
)

_register(
    "stapel-nginx-cache-lint", True, "the location's line",
    "NGX001", "NGX002", "NGX003", "NGX004", "NGX005",
)

_register(
    "stapel-po-lint", False, "a .po catalogue entry (no per-line escape wired)",
    "PO000", "PO001", "PO002", "PO003", "PO004",
)

_register(
    "stapel-exposure-lint", False,
    "a name-exposure finding (no per-line escape wired)",
    "EXP001", "EXP002",
)

_register(
    "stapel-env-address-lint", True, "the reported line",
    "EADDR001", "EADDR002", "EADDR003",
)

_register(
    "stapel-frontend-delivery-lint", True, "the mount/service line",
    "FED001", "FED002", "FED003", "FED005", "FED006",
)
_register(
    "stapel-frontend-delivery-lint", False,
    "a build-info/contract-snapshot finding (no per-line escape wired)",
    "FED004",
)

_register("stapel-shell-python-lint", True, "the payload line", "SH001", "SH002")

# Not composed into stapel-verify (neither reads a project tree; they check
# a release's tag/publish state and a contract artifact's version), and
# neither reads a noqa marker at all — a finding here has no source line.
_register(
    "stapel-registry-check", False,
    "a package/tag publish-state finding (not a source line)",
    "REG001", "REG002", "REG003",
)
_register(
    "stapel-release-manifest", False,
    "a contract-artifact version finding (not a source line)",
    "REL001", "REL002",
)
_register(
    "stapel-fixture-lint", False,
    "a JSON vocabulary/catalog fixture finding (no line number at all)",
    "CAT001", "VOC001", "VOC002", "VOC003", "VOC004", "VOC005", "VOC006",
    "VOC007", "VOC008",
)

_register(
    "stapel-escape-lint", True, "the marker's own line",
    "ESC001", "ESC002",
)
