"""
stapel-po-lint — gettext catalogue gate.

Why this exists
----------------
``manage.py makemessages`` is the canonical tool: the first thing anybody
reaches for the moment they add a translatable string. Run bare over a product
tree it does exactly what it promises — it rewrites the catalogue as a
projection of the sources it could find — and everything it could *not* find is
demoted. Demotion has two forms and gettext skips both:

* **obsolete** (``#~``) — the entry is moved to the end of the file, commented
  out. Visible, at least, to whoever opens the file.
* **fuzzy** (``#, fuzzy``) — the entry stays *among the live entries*, keeps its
  translation, and **looks translated**. ``msgfmt`` drops it from the ``.mo``
  anyway, so the string ships in its source language. This is the more
  dangerous of the two by a wide margin, and it is produced by changes as small
  as a format-flag flip (``python-format`` → ``python-brace-format``), with no
  edit to the msgid at all.

Neither shows up in a test run unless the string happens to be asserted
somewhere, and almost none are. So the loss is silent: a product's letters
revert to their library defaults and CI stays green.

Rules
-----
PO001  (error)    A **fuzzy** entry. gettext skips it; it sits among the live
                  entries and looks translated.
PO002  (error)    An **obsolete** (``#~``) entry. Either the string is dead and
                  the entry should be deleted, or it is alive and its
                  translation has just been switched off.
PO003  (warning)  An **untranslated** entry (empty ``msgstr``). Normal as an
                  intermediate state right after extraction, which is why it is
                  a warning — but a catalogue that ships this way ships the
                  source language.
PO004  (warning)  An **unowned** entry in an extracted catalogue: not one of its
                  ``#:`` references resolves to a file in this tree. The general
                  rule it enforces — *a catalogue is a projection of its own
                  sources; it is never a place to park somebody else's strings.*
                  A library's strings belong in the library's catalogue, which
                  ships inside its wheel and is merged at load; a product
                  translates its own templates and code. An entry parked here
                  for a string this tree does not contain cannot survive the
                  next extraction, and survives today only by accident (a test
                  file that happens to quote the literal, say).

Extracted vs authored catalogues
---------------------------------
PO004 applies only to **extracted** catalogues, and the discriminator is
mechanical: a catalogue holding at least one reference in makemessages' own
``path:line`` form is a projection of sources and every entry in it must be
sourced. A catalogue whose references are semantic keys instead
(``#: notification.otp_code.subject`` — how the fleet's own library catalogues
are authored) is not a projection of anything and PO004 stays silent on it.
PO001-PO003 apply to every catalogue: fuzzy and obsolete are skipped by gettext
regardless of who wrote the file.

Baselines
---------
``--max-fuzzy N`` / ``--max-obsolete N`` allow a known count to stand while a
sweep is in progress, so the gate still fails the moment the count *rises*.
Both default to 0.

Exit codes: 0 clean, 1 violations found, 2 usage/environment error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".hg",
    ".tox",
    ".venv",
    "venv",
    "node_modules",
    "htmlcov",
    "build",
    "dist",
    ".claude",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "worktrees",
    "site-packages",
}

#: a makemessages source reference: ``path/to/file.py:123``
_PATH_REF = re.compile(r"^(?P<path>[^\s:]+):(?P<line>\d+)$")

#: repo-root markers — resolution of a ``#:`` path walks up no further
_ROOT_MARKERS = ("manage.py", "pyproject.toml", "setup.py", ".git")


@dataclass
class Violation:
    path: str
    line: int
    rule: str
    message: str
    level: str = "error"

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
# a small .po parser — no third-party dependency (this package has none)
# ---------------------------------------------------------------------------


@dataclass
class Entry:
    """One catalogue entry. ``lineno`` points at its ``msgid`` line."""

    lineno: int = 0
    msgid: str = ""
    msgid_plural: Optional[str] = None
    msgstrs: list = field(default_factory=list)
    flags: set = field(default_factory=set)
    refs: list = field(default_factory=list)
    obsolete: bool = False

    @property
    def is_header(self) -> bool:
        return self.msgid == "" and self.msgid_plural is None

    @property
    def untranslated(self) -> bool:
        return not self.msgstrs or any(s == "" for s in self.msgstrs)


def _unquote(fragment: str) -> str:
    """Decode one ``"..."`` fragment of a po string."""
    fragment = fragment.strip()
    if not (fragment.startswith('"') and fragment.endswith('"')):
        return fragment
    body = fragment[1:-1]
    out = []
    i = 0
    escapes = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            out.append(escapes.get(body[i + 1], body[i + 1]))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def parse_po(text: str) -> list:
    """Parse ``text`` into :class:`Entry` objects, obsolete blocks included."""
    entries: list = []
    cur = Entry()
    slot: Optional[str] = None  # which field continuation lines append to
    started = False

    def flush():
        nonlocal cur, slot, started
        if started:
            entries.append(cur)
        cur = Entry()
        slot = None
        started = False

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            flush()
            continue

        obsolete = line.startswith("#~")
        if obsolete:
            # "#~ msgid ..." and the previous-msgid form "#~| msgid ..."
            if line.startswith("#~|"):
                slot = None
                continue
            line = line[2:].lstrip()
            if not line:
                continue

        if not obsolete and line.startswith("#"):
            if line.startswith("#,"):
                cur.flags |= {f.strip() for f in line[2:].split(",") if f.strip()}
            elif line.startswith("#:"):
                cur.refs.extend(line[2:].split())
            # "#|" (previous msgid), "#." (extracted), "# " (translator): ignored
            slot = None
            continue

        if line.startswith("msgid_plural"):
            started = True
            cur.msgid_plural = _unquote(line[len("msgid_plural"):])
            slot = "msgid_plural"
        elif line.startswith("msgid"):
            started = True
            cur.obsolete = obsolete
            cur.lineno = number
            cur.msgid = _unquote(line[len("msgid"):])
            slot = "msgid"
        elif line.startswith("msgstr["):
            started = True
            cur.msgstrs.append(_unquote(line.split("]", 1)[1]))
            slot = "msgstr[]"
        elif line.startswith("msgstr"):
            started = True
            cur.msgstrs.append(_unquote(line[len("msgstr"):]))
            slot = "msgstr"
        elif line.startswith('"') and slot:
            piece = _unquote(line)
            if slot == "msgid":
                cur.msgid += piece
            elif slot == "msgid_plural":
                cur.msgid_plural = (cur.msgid_plural or "") + piece
            else:
                cur.msgstrs[-1] += piece

    flush()
    return entries


# ---------------------------------------------------------------------------
# ownership: does a "#:" reference name a file in this tree?
# ---------------------------------------------------------------------------


def _candidate_roots(po_path: Path, scan_root: Path) -> list:
    """Directories a ``#:`` path may be relative to.

    makemessages writes references relative to the directory it ran in — the
    one holding ``manage.py`` — while the catalogue itself may sit in
    ``<project>/locale`` or in ``<project>/<app>/locale``. So resolution tries
    the ``locale/`` parent first and then walks up to ``scan_root``.
    """
    locale_dir = po_path.parent.parent.parent  # <root>/locale/<lang>/LC_MESSAGES
    roots = [locale_dir.parent]
    here = locale_dir.parent
    try:
        scan_root = scan_root.resolve()
    except OSError:  # pragma: no cover - defensive
        return roots
    while here != here.parent:
        here = here.parent
        roots.append(here)
        if here == scan_root or any((here / m).exists() for m in _ROOT_MARKERS):
            break
    return roots


def _ref_path(ref: str) -> str:
    """The path part of a ``#:`` reference.

    makemessages writes ``path/to/file.py:123``; a catalogue whose line numbers
    have been stripped (hand-maintained, or normalised to keep diffs quiet)
    carries the bare path. Both are references to a file and both are honoured.
    """
    match = _PATH_REF.match(ref)
    return match.group("path") if match else ref


def _ref_resolves(ref: str, roots: list) -> bool:
    rel = _ref_path(ref)
    if not rel:
        return False
    for root in roots:
        try:
            if (root / rel).is_file():
                return True
        except OSError:  # pragma: no cover - defensive (bad path characters)
            continue
    return False


def _entry_is_sourced(entry: Entry, roots: list) -> bool:
    return any(_ref_resolves(ref, roots) for ref in entry.refs)


def _catalog_is_extracted(entries: Iterable, roots: Optional[list] = None) -> bool:
    """True when the file is a projection of sources rather than authored copy.

    The discriminator is that at least one ``#:`` reference **resolves to a
    real file in this tree** — that is what a makemessages projection looks
    like, with or without line numbers. A hand-authored catalogue that uses
    ``#:`` for semantic keys instead (``#: notification.otp_code.subject`` —
    how the fleet's library catalogues are written) resolves nothing, and
    PO004 does not apply to it.
    """
    entries = list(entries)
    if roots is None:
        return any(_PATH_REF.match(ref) for e in entries for ref in e.refs)
    return any(_entry_is_sourced(e, roots) for e in entries if not e.obsolete)


# ---------------------------------------------------------------------------
# discovery + linting
# ---------------------------------------------------------------------------


def find_catalogs(root: Path) -> list:
    """Every ``locale/<lang>/LC_MESSAGES/*.po`` under ``root``."""
    root = Path(root)
    if root.is_file() and root.suffix == ".po":
        return [root]
    found: list = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")
        )
        if Path(dirpath).name != "LC_MESSAGES":
            continue
        for name in sorted(filenames):
            if name.endswith(".po"):
                found.append(Path(dirpath) / name)
    return found


def lint_catalog(
    po_path: Path,
    *,
    scan_root: Optional[Path] = None,
    max_fuzzy: int = 0,
    max_obsolete: int = 0,
) -> list:
    """Violations for one catalogue file."""
    try:
        text = po_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:  # pragma: no cover - defensive
        return [Violation(str(po_path), 1, "PO000", f"cannot read catalogue: {exc}")]

    entries = parse_po(text)
    roots = _candidate_roots(po_path, scan_root or po_path.parent)
    extracted = _catalog_is_extracted(entries, roots)
    shown = str(po_path)

    fuzzy = [e for e in entries if "fuzzy" in e.flags and not e.is_header]
    obsolete = [e for e in entries if e.obsolete]

    violations: list = []

    for entry in fuzzy[max_fuzzy:] if max_fuzzy else fuzzy:
        violations.append(Violation(
            shown, entry.lineno, "PO001",
            f"fuzzy entry {entry.msgid[:60]!r} — gettext skips it, so the string "
            f"ships untranslated while the file still shows a translation. "
            f"Review it and drop the fuzzy flag, or fix the msgid.",
        ))

    for entry in obsolete[max_obsolete:] if max_obsolete else obsolete:
        violations.append(Violation(
            shown, entry.lineno, "PO002",
            f"obsolete entry {entry.msgid[:60]!r} — commented out (#~), so its "
            f"translation is switched off. Delete it if the string is dead; if "
            f"it is alive, the extraction did not find its source.",
        ))

    for entry in entries:
        if entry.obsolete or entry.is_header:
            continue
        if entry.untranslated:
            violations.append(Violation(
                shown, entry.lineno, "PO003",
                f"untranslated entry {entry.msgid[:60]!r} — an empty msgstr "
                f"ships the source language.",
                level="warning",
            ))

    if extracted:
        for entry in entries:
            if entry.obsolete or entry.is_header:
                continue
            if not _entry_is_sourced(entry, roots):
                violations.append(Violation(
                    shown, entry.lineno, "PO004",
                    f"unowned entry {entry.msgid[:60]!r} — no #: reference "
                    f"resolves to a file in this tree. A catalogue is a "
                    f"projection of its own sources; it is never a place to "
                    f"park somebody else's strings. Move it to the catalogue of "
                    f"the package that owns the string (it is merged at load), "
                    f"or delete it — the next extraction will demote it anyway.",
                    level="warning",
                ))

    return violations


def lint_paths(
    paths: Iterable,
    *,
    max_fuzzy: int = 0,
    max_obsolete: int = 0,
) -> list:
    violations: list = []
    for raw in paths:
        root = Path(raw)
        for po_path in find_catalogs(root):
            violations.extend(lint_catalog(
                po_path,
                scan_root=root if root.is_dir() else root.parent,
                max_fuzzy=max_fuzzy,
                max_obsolete=max_obsolete,
            ))
    return violations


def lint_project(project, notes: Optional[list] = None) -> list:
    """``stapel-verify`` entry point: lint every catalogue under ``project``.

    Silent by design in a project that ships no gettext catalogue at all — the
    note says so rather than the gate inventing a finding.
    """
    project = Path(project)
    catalogs = find_catalogs(project)
    if notes is not None and not catalogs:
        notes.append("no locale/<lang>/LC_MESSAGES/*.po under this project — nothing to check")
    return lint_paths([project])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-po-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("paths", nargs="*", default=["."],
                        help="Directories (or .po files) to check (default: .)")
    parser.add_argument("--max-fuzzy", type=int, default=0, metavar="N",
                        help="Tolerate N fuzzy entries per catalogue (default 0) — "
                             "the gate still fails when the count rises.")
    parser.add_argument("--max-obsolete", type=int, default=0, metavar="N",
                        help="Tolerate N obsolete entries per catalogue (default 0).")
    parser.add_argument("--strict", action="store_true",
                        help="Warnings (PO003, PO004) fail the run too.")
    parser.add_argument("--json", action="store_true",
                        help="Machine output for agents/CI.")
    args = parser.parse_args(argv)

    paths = args.paths or ["."]
    for raw in paths:
        if not Path(raw).exists():
            print(f"stapel-po-lint: no such path: {raw}", file=sys.stderr)
            return 2

    violations = lint_paths(
        paths, max_fuzzy=args.max_fuzzy, max_obsolete=args.max_obsolete
    )
    errors = [v for v in violations if v.level == "error"]
    warnings = [v for v in violations if v.level != "error"]

    if args.json:
        print(json.dumps({
            "errors": len(errors),
            "warnings": len(warnings),
            "findings": [v.to_dict() for v in violations],
        }, ensure_ascii=False, indent=2))
    else:
        for violation in violations:
            print(violation)
        if not violations:
            checked = sum(len(find_catalogs(Path(p))) for p in paths)
            print(f"stapel-po-lint: {checked} catalogue(s) clean — "
                  f"no fuzzy, no obsolete, no unowned entries.")
        else:
            print(f"\n{len(errors)} error(s), {len(warnings)} warning(s).")

    if errors or (args.strict and warnings):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
