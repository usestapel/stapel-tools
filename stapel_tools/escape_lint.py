"""
stapel-escape-lint — ESC001/ESC002: an escape that does not do anything.

Why this exists
----------------
2026-09-07, a client fleet: a host (``svc-agent``) carried ``# noqa: SUR002`` on
a ``permission_classes`` line for two years. ``stapel-surface-lint`` has
never read a noqa comment — the marker suppressed nothing, and nothing in
this package noticed, because every check that reads ``# noqa`` only ever
looks for ITS OWN rule ids on ITS OWN reported lines. A marker naming a rule
that linter does not know, or a rule whose linter never reads noqa at all
(``stapel-adoption-lint``'s ADO-codes, to pick one of several), is not a
parse error anywhere — it is silently inert, and reads exactly like a marker
that works. ``stapel_tools.escape``'s shared grammar fixed the mechanism for
every linter that reads noqa at all (SUR001-003 now genuinely honour it);
this module is the check that a marker actually landed on something that
reads it.

Two rules, both against the ``# noqa: RULE[, RULE ...]`` grammar only — the
family-specific second escapes each linter documents (AUTHZ007's
``# stapel: strict-authenticator``, env-address-lint's ``# stapel:
env-address-ok``, ...) are a different, deliberate grammar and are not this
module's business.

2026-09-07, again, a client fleet: ``svc-agent/apps/agent/feature_descent.py``
carried ``# noqa: BLE001`` — ruff's blind-except code — and ESC001 flagged it
as an unknown stapel rule id. ``# noqa`` is a grammar every one of ruff,
flake8 and pyflakes shares with us; their ids (``BLE001``, ``E501``,
``F401``, ``S101``, ``PLR0913``, ...) sit right alongside ours on the same
comment, and several of them (``BLE001``, ``ANN401``, ``SIM108``, ...)
happen to match ``_ID_RE``'s letters-then-three-digits shape exactly as well
as a stapel id does. Matching the shape was never enough to prove a marker
is even claiming to be OURS. ESC001 now also checks rule FAMILY (the
alphabetic prefix) against the set of families ``RULE_REGISTRY`` actually
registers — an unknown id outside every stapel family is a foreign linter's
business and is silently ignored; an unknown id INSIDE one of our families
(``SUR999``) is still exactly the inert-escape case this rule exists to
catch. A marker mixing ids from both worlds (``# noqa: BLE001, SUR002``) is
judged only on the ones that are ours.

ESC001 (error) — INERT ESCAPE. A ``# noqa: <RULE>`` names:

* a rule id whose alphabetic family belongs to this fleet but that no linter
  in this stapel-tools version actually knows (a typo, a retired rule, or an
  id that never existed — see ``_rule_family``/``_STAPEL_FAMILIES`` below;
  an id whose family is foreign to us entirely, a genuine ruff/flake8/
  pyflakes code, is never this rule's business no matter how closely its
  shape resembles ours), or
* a rule id a linter in this version DOES know, but that linter's own code
  never reads a noqa marker on that construct at all (``stapel_tools.escape.
  RULE_REGISTRY[rule].honors_noqa`` is ``False`` — most of ADO/API/IDX/PO/EXP,
  plus specific rules inside families that are otherwise noqa-aware, like
  CFG002-CFG005 or MIG001-MIG003/MIG005).

Either way the message is the same, because the effect is the same: **this
marker suppresses nothing.**

ESC002 (warning) — STALE ESCAPE. A ``# noqa: <RULE>`` names a rule that IS
noqa-aware, but the rule did not fire on that exact line this run. Checked by
re-running the composed arsenal with the shared grammar disabled (so every
finding a marker would otherwise have hidden becomes visible) and comparing
against the markers this scan found: a marker naming a rule that never fires
there either fixed itself (the marker is now dead weight) or sits on the
wrong line (the marker never suppressed what its author thinks it does).
ESC002 needs the arsenal's raw findings, so it only runs when
:func:`lint_project` is given ``composed`` — the ``stapel-verify`` composition
supplies it; the standalone ``stapel-escape-lint`` CLI runs ESC001 only, and
says so.

Exit codes: 0 clean (warnings allowed), 1 errors present, 2 usage errors.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import escape
from .adoption_lint import SKIP_DIRS

#: File suffixes this scan reads for a noqa marker. Deliberately
#: source-agnostic: `#` is Python's comment token, nginx's, YAML's, TOML's
#: and the shell's — the marker itself is what is being checked, not which
#: of those languages it sits in.
SCAN_SUFFIXES = (".py", ".conf", ".yml", ".yaml", ".toml", ".sh", ".bash", ".zsh")

#: What a marker's rule token has to look like to be in scope at all — a
#: leading letter block, then digits (`SUR002`, `R001`, `EADDR001`). Matching
#: this shape is necessary but not sufficient: ruff's own catalogue skews
#: toward the exact same SHOUTY-prefix-plus-digits shape (`BLE001`, `ANN401`,
#: `SIM108`, ...), so shape alone cannot tell a broken stapel rule id from an
#: entirely foreign one. `check_inert` narrows further, by rule FAMILY (the
#: token's alphabetic prefix) against the families `RULE_REGISTRY` actually
#: owns — see `_rule_family`/`_STAPEL_FAMILIES` below. What is NOT in scope
#: at the token-shape level, before family is even considered, is a bare
#: blanket marker (no rule named) and a chunk that parses to no token at
#: all.
_ID_RE = re.compile(r"^[A-Z]{2,10}\d{3}$")


def _rule_family(rule_id: str) -> str:
    """The alphabetic prefix of a rule id (`SUR002` -> `SUR`, `EADDR001` ->
    `EADDR`) — the unit ESC001 checks an UNKNOWN id against, instead of the
    id itself, so a foreign linter's own code that happens to match
    `_ID_RE`'s shape is never mistaken for a broken stapel rule."""
    match = re.match(r"^[A-Z]+", rule_id)
    return match.group() if match else rule_id


#: Every rule family this version of stapel-tools actually owns, derived
#: from `RULE_REGISTRY` itself — never hand-listed. A ruff/flake8/pyflakes
#: id (`BLE001`, `S101`, `PLR0913`, ...) rides the same shared noqa grammar
#: without being a stapel rule at all, and its family is never one of these;
#: an id in one of OUR families that `RULE_REGISTRY` nonetheless doesn't
#: recognise (a typo or a retired id, `SUR999`) still is ours to flag.
#: Registering a new family in `escape.py` puts it in scope here
#: automatically.
_STAPEL_FAMILIES = frozenset(_rule_family(rule_id) for rule_id in escape.RULE_REGISTRY)


@dataclass(frozen=True)
class Marker:
    path: str
    line: int
    rules: frozenset[str]


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    message: str
    level: str = "error"  # "error" | "warning"

    def __str__(self) -> str:
        tag = self.rule if self.level == "error" else f"{self.rule} warning"
        return f"{self.path}:{self.line}: [{tag}] {self.message}"

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "line": self.line,
            "rule": self.rule,
            "message": self.message,
            "level": self.level,
        }


# ---------------------------------------------------------------------------
# scanning
# ---------------------------------------------------------------------------


def _iter_files(project: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(project):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")
        )
        for fname in sorted(filenames):
            if fname.endswith(SCAN_SUFFIXES):
                yield Path(dirpath) / fname


def scan_markers(project: Path) -> list[Marker]:
    """Every ``# noqa: RULE[, RULE ...]`` marker under *project*.

    A bare blanket ``# noqa`` (no rule named) is out of scope: there is no
    rule id to check for inertness or staleness against. Only tokens shaped
    like a stapel rule id (:data:`_ID_RE`) are kept — this is what keeps a
    ruff/flake8 suppression (``# noqa: F401``) from ever being mistaken for
    an unknown STAPEL rule.
    """
    markers: list[Marker] = []
    for path in _iter_files(project):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for lineno, raw in enumerate(lines, start=1):
            rules = escape.parse_noqa(raw)
            if not rules:
                continue
            named = {r for r in rules if _ID_RE.match(r)}
            if named:
                markers.append(Marker(str(path), lineno, frozenset(named)))
    return markers


# ---------------------------------------------------------------------------
# ESC001 — inert escape
# ---------------------------------------------------------------------------


def check_inert(markers: list[Marker]) -> list[Finding]:
    findings: list[Finding] = []
    for marker in markers:
        for rule_id in sorted(marker.rules):
            info = escape.RULE_REGISTRY.get(rule_id)
            if info is None:
                if _rule_family(rule_id) not in _STAPEL_FAMILIES:
                    # A foreign linter's own id (ruff's BLE001, flake8's
                    # F401, ...) — not a stapel rule family at all, so this
                    # marker is none of ESC001's business either way.
                    continue
                findings.append(Finding(
                    marker.path, marker.line, "ESC001",
                    f"'# noqa: {rule_id}' names a rule id no linter in this "
                    f"stapel-tools version knows — this marker suppresses "
                    f"nothing",
                ))
            elif not info.honors_noqa:
                findings.append(Finding(
                    marker.path, marker.line, "ESC001",
                    f"'# noqa: {rule_id}' names {info.linter}'s {rule_id}, "
                    f"which does not read a noqa marker on {info.construct} "
                    f"— this marker suppresses nothing",
                ))
    return findings


# ---------------------------------------------------------------------------
# ESC002 — stale escape
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _noqa_disabled():
    """Disable the shared grammar for the duration of the block, so a raw,
    pre-suppression pass over the arsenal is possible without changing a
    single linter's public API.

    Every migrated linter reaches ``parse_noqa`` through the ``escape``
    MODULE (``escape.parse_noqa(...)``), never through a name bound at
    import time (``from .escape import parse_noqa``) — that is what makes
    patching the one attribute here reach every call site.
    """
    original = escape.parse_noqa
    escape.parse_noqa = lambda line: None
    try:
        yield
    finally:
        escape.parse_noqa = original


def collect_raw_findings(
    callables: list[Callable[[], object]],
) -> dict[tuple[str, int], set[str]]:
    """Run every zero-arg linter callable with the shared grammar disabled,
    so a finding a ``# noqa`` would otherwise have hidden is visible, and
    return ``{(path, line): {rule, ...}}`` — what actually fires, ignoring
    every noqa marker in the tree.

    A callable may return a :class:`~stapel_tools.verify.LinterReport` (its
    ``findings`` are dicts) or a plain list of a linter's own violation
    dataclass (path/line/rule attributes) — both shapes are accepted so this
    also works against a linter's ``lint_project``/``lint_paths`` directly.
    Any callable that raises is skipped: a raw diagnostic pass must not turn
    one linter's crash into every OTHER linter's finding disappearing.
    """
    raw: dict[tuple[str, int], set[str]] = {}
    with _noqa_disabled():
        for call in callables:
            try:
                result = call()
            except Exception:
                continue
            items = getattr(result, "findings", result)
            for item in items:
                if isinstance(item, dict):
                    path, line, rule = item.get("path"), item.get("line"), item.get("rule")
                else:
                    path = getattr(item, "path", None)
                    line = getattr(item, "line", None)
                    rule = getattr(item, "rule", None)
                if path is None or line is None or rule is None:
                    continue
                raw.setdefault((str(path), int(line)), set()).add(rule)
    return raw


def check_stale(
    markers: list[Marker], raw_findings: dict[tuple[str, int], set[str]],
) -> list[Finding]:
    findings: list[Finding] = []
    for marker in markers:
        fired = raw_findings.get((marker.path, marker.line), set())
        for rule_id in sorted(marker.rules):
            info = escape.RULE_REGISTRY.get(rule_id)
            if info is None or not info.honors_noqa:
                continue  # ESC001's territory, not a staleness question
            if rule_id in fired:
                continue
            findings.append(Finding(
                marker.path, marker.line, "ESC002",
                f"'# noqa: {rule_id}' names {info.linter}'s {rule_id}, which "
                f"is noqa-aware but did not fire on this line this run — "
                f"either the code no longer trips it (drop the marker) or it "
                f"sits on the wrong line (it never suppressed what its "
                f"author thinks it does)",
                level="warning",
            ))
    return findings


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------


def lint_project(
    project: Path,
    *,
    composed: Optional[list[Callable[[], object]]] = None,
    notes: Optional[list[str]] = None,
) -> list[Finding]:
    """ESC001 always; ESC002 only when *composed* (the rest of the arsenal,
    as zero-arg callables) is given — that is what ``stapel-verify`` passes,
    and what makes a raw pre-suppression run possible."""
    project = Path(project).resolve()
    notes = notes if notes is not None else []
    markers = scan_markers(project)
    findings = check_inert(markers)

    honors_aware = [
        m for m in markers
        if any(
            (info := escape.RULE_REGISTRY.get(r)) and info.honors_noqa
            for r in m.rules
        )
    ]
    if not honors_aware:
        return findings
    if composed is None:
        notes.append(
            "stapel-escape-lint: no composed arsenal given — ESC002 (stale "
            "escape) skipped; run through stapel-verify for the full check"
        )
        return findings
    raw = collect_raw_findings(composed)
    findings += check_stale(honors_aware, raw)
    return findings


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-escape-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_dir", nargs="?", default=".", help="Project root.")
    parser.add_argument("--json", action="store_true", help="Machine output.")
    args = parser.parse_args(argv)

    project = Path(args.project_dir)
    if not project.is_dir():
        print(f"Error: not a directory: {project}", file=sys.stderr)
        return 2

    notes: list[str] = []
    findings = lint_project(project, notes=notes)
    errors = sum(1 for f in findings if f.level == "error")

    if args.json:
        import json
        print(json.dumps(
            {
                "ok": errors == 0,
                "errors": errors,
                "warnings": len(findings) - errors,
                "findings": [f.to_dict() for f in findings],
                "notes": notes,
            },
            indent=2, sort_keys=True, ensure_ascii=False,
        ))
    else:
        for note in notes:
            print(note, file=sys.stderr)
        for finding in findings:
            print(finding)
        print(
            f"stapel-escape-lint: {errors} error(s) in {project}"
            if errors else f"stapel-escape-lint: clean ({project})"
        )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
