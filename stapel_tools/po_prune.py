"""
stapel-po-prune — make a product catalogue a projection of its own sources.

The rule this enforces
-----------------------
**A catalogue is a projection of its own sources; it is never a place to park
somebody else's strings.** A library's strings live in the library's catalogue
and ship inside its wheel, where Django's app-locale discovery merges them at
load. A product translates its own templates and its own code. An entry parked
in the product catalogue for a string the product does not contain cannot
survive the next extraction — ``makemessages`` will demote it to fuzzy or
obsolete, gettext will skip it, and the string will quietly ship in its source
language with a green test suite.

What this does
---------------
Runs the extraction the way the product would — ``makemessages --all`` inside a
**scratch copy**, so the real catalogues are never touched — and uses its
output as the authority on what this tree contains. Every entry of every
extracted catalogue then falls into one of three buckets, and ``--apply``
removes exactly one of them:

``sourced``    the extraction found it. Kept, untouched.
``shadow``     the extraction *does* find it, but an installed package owns the
               same msgid — the entry is a shadow of a library string that
               survives only because something in this tree quotes the literal
               (a test asserting the override). Kept and reported with the
               same relocation as ``foreign``.
``foreign``    the extraction did not find it, but it is a msgid of an
               **installed package's own catalogue** — the product is
               overriding a library string by shadowing it through
               ``LOCALE_PATHS``. Kept by default and reported with the
               override rewritten into the owning library's documented seam,
               because deleting it would silently hand the string back to the
               library default. Removed only with ``--relocate-applied``, i.e.
               once that seam is in place.
``dead``      the extraction did not find it and no installed package owns it —
               a string whose code was rewritten and whose entry nobody
               deleted. **Removed** on ``--apply``.

Asking the extractor, rather than grepping the sources for the literal, is the
difference between right and plausible: a ``{% blocktranslate %}`` msgid is not
a literal anybody typed (``{{ name }}`` is extracted as ``%(name)s``), so a
source scan calls live entries dead. The scan survives as ``--mode heuristic``
for trees where Django cannot be run, and it errs toward keeping entries.

Dry run is the default. ``--apply`` rewrites the files by deleting whole entry
blocks from the raw text, so every surviving byte is untouched and the result
is reviewable as a plain ``git diff``.

Idempotence
-----------
A second run over an already-pruned tree finds no ``dead`` entries and writes
nothing. The dry run **proves** it rather than claiming it: it applies to a
scratch copy, classifies again, and reports what a second apply would remove.

Exit codes: 0 clean/applied, 2 usage error.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import po_lint

#: Source extensions worth searching for a surviving literal.
_SOURCE_SUFFIXES = (".py", ".html", ".txt", ".md", ".json", ".yml", ".yaml")


def _looks_like_key(ref: str) -> bool:
    """True for a ``#:`` reference that names a translation key, not a file.

    The fleet's library catalogues put the key there
    (``#: notification.otp_code.subject``); makemessages puts a path, with or
    without a line number. A key has no path separator and no source suffix.
    """
    if po_lint._PATH_REF.match(ref):
        return False
    return "/" not in ref and not ref.endswith(_SOURCE_SUFFIXES)


@dataclass
class Classified:
    catalog: Path
    lineno: int
    msgid: str
    msgstr: str
    bucket: str
    owner: str = ""
    keys: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "catalog": str(self.catalog),
            "line": self.lineno,
            "msgid": self.msgid,
            "msgstr": self.msgstr,
            "bucket": self.bucket,
            "owner": self.owner,
            "keys": list(self.keys),
        }


# ---------------------------------------------------------------------------
# the tree, and the catalogues of installed packages
# ---------------------------------------------------------------------------


def _tree_text(project: Path) -> str:
    """Every source file in the project, concatenated once.

    One pass, one blob: the question asked of it is only ever "does this
    literal appear anywhere", asked a few dozen times.
    """
    chunks = []
    for dirpath, dirnames, filenames in os.walk(project):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in po_lint.SKIP_DIRS and not d.endswith(".egg-info")
        )
        if Path(dirpath).name == "LC_MESSAGES":
            continue
        for name in sorted(filenames):
            if not name.endswith(_SOURCE_SUFFIXES):
                continue
            try:
                chunks.append((Path(dirpath) / name).read_text(
                    encoding="utf-8", errors="ignore"))
            except OSError:  # pragma: no cover - defensive
                continue
    return "\n".join(chunks)


def default_search_roots(project: Path) -> list:
    """Where installed packages' own catalogues are looked for."""
    roots = []
    for candidate in (project / ".venv", project / "venv"):
        if candidate.is_dir():
            roots.append(candidate)
    for entry in sys.path:
        if entry.endswith("site-packages") and Path(entry).is_dir():
            roots.append(Path(entry))
    return roots


def _iter_package_catalogs(root: Path):
    """``(package_name, po_path)`` for every catalogue under ``root``.

    The package is the directory that owns the ``locale/`` dir — for a wheel
    installed into ``site-packages`` that is the import name.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in {"__pycache__", ".git"})
        if Path(dirpath).name != "LC_MESSAGES":
            continue
        package = Path(dirpath).parent.parent.parent.name
        for name in sorted(filenames):
            if name.endswith(".po"):
                yield package, Path(dirpath) / name


def _key_defaults(package_dir: Path) -> dict:
    """``english default -> [keys]`` read out of a package's key registry.

    A library whose keys are declared as ``{key: english default}`` (the fleet
    convention — ``translation_keys.NOTIFICATION_KEYS``) can have **several
    keys share one default**, and gettext cannot: a ``.po`` holds one entry per
    msgid. So the catalogue alone under-reports the override — the product's
    own note said as much ("one library msgid covers both"). Reading the
    registry recovers every key the msgid stands for.

    Read by AST, never imported: this must work without Django settings.
    """
    out: dict = {}
    for source in sorted(package_dir.glob("translation_keys.py")):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover - defensive
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key_node, value_node in zip(node.keys, node.values):
                if not (isinstance(key_node, ast.Constant)
                        and isinstance(value_node, ast.Constant)):
                    continue
                key, default = key_node.value, value_node.value
                if isinstance(key, str) and isinstance(default, str) and key and default:
                    out.setdefault(default, [])
                    if key not in out[default]:
                        out[default].append(key)
    return out


def library_msgids(search_roots) -> dict:
    """``msgid -> (package, [semantic keys])`` across installed catalogues.

    The fleet's library catalogues carry the translation key in the ``#:``
    slot (``#: notification.otp_code.subject``) rather than a source path,
    which is what lets ``--relocate`` name the override seam's key without
    anybody typing it.
    """
    owned: dict = {}
    for root in search_roots:
        for package, po_path in _iter_package_catalogs(Path(root)):
            try:
                entries = po_lint.parse_po(po_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive
                continue
            registry = _key_defaults(po_path.parent.parent.parent.parent)
            for entry in entries:
                if entry.obsolete or entry.is_header:
                    continue
                keys = [r for r in entry.refs if _looks_like_key(r)]
                for key in registry.get(entry.msgid, []):
                    if key not in keys:
                        keys.append(key)
                existing = owned.get(entry.msgid)
                if existing is None:
                    owned[entry.msgid] = (package, list(keys))
                else:
                    for key in keys:
                        if key not in existing[1]:
                            existing[1].append(key)
    return owned


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------


def extraction_msgids(project: Path, *, python: Optional[str] = None) -> Optional[set]:
    """The msgids ``makemessages`` finds in ``project`` — ground truth.

    Runs the extraction inside a **scratch copy**, so the project's own
    catalogues are never touched, and returns the set of msgids the run left
    live (not obsolete, not fuzzy). ``None`` when the extraction could not be
    run at all (no ``manage.py``, no Django, settings unimportable) — the
    caller then falls back to the source-scan heuristic and says so.

    Why not scan the sources ourselves: a ``{% blocktranslate %}`` msgid is not
    a literal anybody typed — ``{{ name }}`` becomes ``%(name)s`` — so
    "is this string in the tree" answers *no* for entries that are perfectly
    alive. Asking the extractor is the only answer that cannot be wrong about
    its own output.
    """
    project = Path(project).resolve()
    if not (project / "manage.py").is_file():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / project.name
        shutil.copytree(
            project, copy,
            ignore=shutil.ignore_patterns(*sorted(po_lint.SKIP_DIRS)),
            symlinks=True,
        )
        # The venv is skipped by the copy (a SKIP_DIR); point the interpreter
        # at the original one so the project's own Django is what runs.
        cmd = [python or sys.executable, "manage.py", "makemessages", "--all",
               "--extension", "html,txt,py"]
        try:
            result = subprocess.run(
                cmd, cwd=str(copy), capture_output=True, text=True, timeout=600,
            )
        except (OSError, subprocess.SubprocessError):  # pragma: no cover
            return None
        if result.returncode != 0:
            return None
        live = set()
        for po_path in po_lint.find_catalogs(copy):
            for entry in po_lint.parse_po(po_path.read_text(encoding="utf-8")):
                if entry.obsolete or entry.is_header or "fuzzy" in entry.flags:
                    continue
                live.add(entry.msgid)
        return live


def classify(
    project: Path,
    *,
    search_roots: Optional[list] = None,
    mode: str = "auto",
    python: Optional[str] = None,
    live_msgids: Optional[set] = None,
) -> tuple:
    """``(findings, mode_used)``.

    ``live_msgids`` supplies the extraction result directly — what
    ``makemessages`` finds in this tree — for callers that already have it.

    ``extract`` asks ``makemessages`` what this tree actually contains — the
    only authority on its own output. ``heuristic`` falls back to "is the msgid
    literal anywhere in the sources", which is right for ``gettext("...")``
    call sites and wrong for ``{% blocktranslate %}``, so it errs toward
    keeping entries rather than deleting them.
    """
    project = Path(project).resolve()
    owned = library_msgids(
        search_roots if search_roots is not None else default_search_roots(project)
    )

    live: Optional[set] = live_msgids
    if live is None and mode in ("auto", "extract"):
        live = extraction_msgids(project, python=python)
        if live is None and mode == "extract":
            raise RuntimeError(
                "extraction mode requested but makemessages could not be run "
                "(no manage.py, or Django/settings unavailable to the chosen "
                "interpreter — pass --python)"
            )
    mode_used = "extract" if live is not None else "heuristic"
    blob = _tree_text(project) if live is None else ""

    out: list = []
    for po_path in po_lint.find_catalogs(project):
        entries = po_lint.parse_po(po_path.read_text(encoding="utf-8"))
        roots = po_lint._candidate_roots(po_path, project)
        if not po_lint._catalog_is_extracted(entries, roots):
            continue
        for entry in entries:
            if entry.obsolete or entry.is_header:
                continue
            msgstr = entry.msgstrs[0] if entry.msgstrs else ""
            if live is not None:
                in_tree = entry.msgid in live
            else:
                in_tree = bool(entry.msgid) and entry.msgid in blob

            if in_tree and entry.msgid in owned:
                # The extraction does find it — but an installed package owns
                # the same msgid, so what this catalogue holds is a shadow of
                # a library string, not a string of its own. It survives today
                # because something in this tree happens to quote the literal
                # (a test asserting the override, typically); the day that
                # quote moves, the override dies silently.
                bucket = "shadow"
                owner, keys = owned[entry.msgid]
            elif in_tree:
                bucket, owner, keys = "sourced", "", []
            elif entry.msgid in owned:
                bucket = "foreign"
                owner, keys = owned[entry.msgid]
            elif po_lint._entry_is_sourced(entry, roots) and live is None:
                # heuristic mode only: refs still resolve, so keep it and let
                # the next real extraction decide.
                bucket, owner, keys = "stale-ref", "", []
            else:
                bucket, owner, keys = "dead", "", []
            out.append(Classified(
                catalog=po_path, lineno=entry.lineno, msgid=entry.msgid,
                msgstr=msgstr, bucket=bucket, owner=owner, keys=keys,
            ))
    return out, mode_used


# ---------------------------------------------------------------------------
# rewriting: delete whole blocks out of the raw text
# ---------------------------------------------------------------------------


def prune_text(text: str, drop_msgids) -> str:
    """Remove the entry blocks whose msgid is in ``drop_msgids``.

    Blocks are separated by blank lines in every catalogue gettext writes, and
    each is removed whole — comments, flags, references, msgid, msgstr — so
    every surviving byte is untouched and the diff reads as pure deletion.
    """
    drop = set(drop_msgids)
    if not drop:
        return text
    blocks = text.split("\n\n")
    kept = []
    for block in blocks:
        entries = po_lint.parse_po(block)
        if len(entries) == 1 and not entries[0].obsolete and not entries[0].is_header:
            if entries[0].msgid in drop:
                continue
        kept.append(block)
    return "\n\n".join(kept)


def apply_prune(project: Path, findings, *, buckets=("dead",)) -> dict:
    """Rewrite each catalogue, dropping the entries in ``buckets``.

    Returns ``{catalog: removed_count}``.
    """
    removed: dict = {}
    by_catalog: dict = {}
    for finding in findings:
        if finding.bucket in buckets:
            by_catalog.setdefault(finding.catalog, []).append(finding.msgid)
    for catalog, msgids in by_catalog.items():
        original = catalog.read_text(encoding="utf-8")
        pruned = prune_text(original, msgids)
        if pruned != original:
            catalog.write_text(pruned, encoding="utf-8")
        removed[catalog] = len(msgids)
    return removed


# ---------------------------------------------------------------------------
# the relocation snippet — the override seam of the owning library
# ---------------------------------------------------------------------------


def _setting_name(package: str) -> str:
    return package.upper()


def relocation_snippet(findings) -> str:
    """Python to paste into settings for every ``foreign`` entry.

    Groups by owning package, and by the translation key the library's own
    catalogue names, merging the per-language texts the product had parked in
    its ``.po``. This is the same override the parked entry was performing,
    expressed through the seam the library documents instead of through
    ``LOCALE_PATHS`` shadowing a msgid the product does not own.
    """
    by_owner: dict = {}
    for finding in findings:
        if finding.bucket not in ("foreign", "shadow"):
            continue
        lang = finding.catalog.parent.parent.name
        for key in (finding.keys or [f"<no key in {finding.owner}'s catalogue>"]):
            by_owner.setdefault(finding.owner, {}).setdefault(key, {})[lang] = finding.msgstr
    if not by_owner:
        return ""

    lines = []
    for owner in sorted(by_owner):
        lines.append(f"{_setting_name(owner)} = {{")
        lines.append('    "TEXT": {')
        for key in sorted(by_owner[owner]):
            lines.append(f'        "{key}": {{')
            for lang in sorted(by_owner[owner][key]):
                text = by_owner[owner][key][lang].replace('"', '\\"')
                lines.append(f'            "{lang}": "{text}",')
            lines.append("        },")
        lines.append("    },")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# idempotence proof
# ---------------------------------------------------------------------------


def idempotence_report(
    project: Path,
    *,
    search_roots: Optional[list] = None,
    mode: str = "auto",
    python: Optional[str] = None,
    live_msgids: Optional[set] = None,
) -> dict:
    """Apply to a scratch copy, classify again, and report the second pass.

    A catalogue tool that is not idempotent is a foot-gun of the same family
    as the one being removed, so the dry run proves it rather than claiming it.
    """
    project = Path(project).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / project.name
        shutil.copytree(
            project, copy,
            ignore=shutil.ignore_patterns(*sorted(po_lint.SKIP_DIRS)),
            symlinks=True,
        )
        roots = search_roots if search_roots is not None else default_search_roots(project)
        first, _ = classify(copy, search_roots=roots, mode=mode, python=python,
                            live_msgids=live_msgids)
        apply_prune(copy, first)
        second, _ = classify(copy, search_roots=roots, mode=mode, python=python,
                             live_msgids=live_msgids)
        removed_second = [f for f in second if f.bucket == "dead"]
        before = {f.catalog.name: 0 for f in first}
        for f in first:
            before[f.catalog.name] += 1
        after: dict = {}
        for f in second:
            after[f.catalog.name] = after.get(f.catalog.name, 0) + 1
        return {
            "entries_before": len(first),
            "entries_after_first_apply": len(second),
            "would_remove_on_second_apply": len(removed_second),
            "idempotent": len(removed_second) == 0,
            "per_catalog_before": before,
            "per_catalog_after": after,
        }


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def _summarise(findings) -> dict:
    counts: dict = {}
    for finding in findings:
        counts[finding.bucket] = counts.get(finding.bucket, 0) + 1
    return counts


def _print_report(project: Path, findings, idem: Optional[dict], sample: int,
                  stream, mode: str = "extract") -> None:
    counts = _summarise(findings)
    catalogs = sorted({f.catalog for f in findings})
    print(f"stapel-po-prune: {project}", file=stream)
    print(f"mode: {mode}"
          + ("  (makemessages run in a scratch copy — ground truth)"
             if mode == "extract"
             else "  (source scan — Django unavailable; errs toward keeping)"),
          file=stream)
    print(f"catalogues: {len(catalogs)}", file=stream)
    for catalog in catalogs:
        print(f"  {catalog}", file=stream)
    print(file=stream)
    print(f"{'bucket':<12} {'entries':>7}   action", file=stream)
    print("-" * 46, file=stream)
    for bucket, action in (
        ("sourced", "keep"),
        ("stale-ref", "keep (heuristic mode only)"),
        ("shadow", "keep; a library owns this msgid — relocate"),
        ("foreign", "keep; relocate to the owner's seam"),
        ("dead", "REMOVE"),
    ):
        if bucket in ("stale-ref", "shadow") and not counts.get(bucket):
            continue
        print(f"{bucket:<12} {counts.get(bucket, 0):>7}   {action}", file=stream)
    print(file=stream)

    for bucket in ("dead", "foreign", "shadow", "stale-ref"):
        rows = [f for f in findings if f.bucket == bucket]
        if not rows:
            continue
        print(f"--- {bucket} ({len(rows)}) — showing {min(sample, len(rows))}", file=stream)
        for finding in rows[:sample]:
            tail = f"   [{finding.owner}: {', '.join(finding.keys)}]" if finding.owner else ""
            print(f"  {finding.catalog.parent.parent.name}:{finding.lineno}  "
                  f"{finding.msgid[:70]!r}{tail}", file=stream)
        print(file=stream)

    snippet = relocation_snippet(findings)
    if snippet:
        print("--- relocation: paste into the product's settings, then re-run "
              "with --relocate-applied to drop the parked entries", file=stream)
        print(snippet, file=stream)

    if idem is not None:
        print("--- idempotence (applied to a scratch copy, then re-classified)",
              file=stream)
        print(f"  entries before          : {idem['entries_before']}", file=stream)
        print(f"  entries after one apply : {idem['entries_after_first_apply']}", file=stream)
        print(f"  removed by a 2nd apply  : {idem['would_remove_on_second_apply']}", file=stream)
        print(f"  idempotent              : {idem['idempotent']}", file=stream)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-po-prune",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_dir", nargs="?", default=".")
    parser.add_argument("--apply", action="store_true",
                        help="Rewrite the catalogues (default is a dry run).")
    parser.add_argument("--relocate-applied", action="store_true",
                        help="The owning library's override seam is in place — "
                             "also remove the 'foreign' entries.")
    parser.add_argument("--search-root", action="append", default=None, metavar="DIR",
                        help="Where to look for installed packages' catalogues "
                             "(repeatable). Default: <project>/.venv and this "
                             "interpreter's site-packages.")
    parser.add_argument("--mode", choices=("auto", "extract", "heuristic"),
                        default="auto",
                        help="auto (default): run makemessages if it can be "
                             "run, else fall back to the source scan and say "
                             "so. extract: fail if it cannot be run.")
    parser.add_argument("--python", default=None, metavar="EXE",
                        help="Interpreter that has this project's Django and "
                             "dependencies (default: the running one).")
    parser.add_argument("--sample", type=int, default=10,
                        help="How many entries per bucket to print (default 10).")
    parser.add_argument("--no-idempotence-check", action="store_true",
                        help="Skip the scratch-copy second-pass proof.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    project = Path(args.project_dir)
    if not project.is_dir():
        print(f"stapel-po-prune: no such directory: {project}", file=sys.stderr)
        return 2

    roots = [Path(r) for r in args.search_root] if args.search_root else None
    try:
        findings, mode = classify(
            project, search_roots=roots, mode=args.mode, python=args.python)
    except RuntimeError as exc:
        print(f"stapel-po-prune: {exc}", file=sys.stderr)
        return 2

    idem = None
    if not args.apply and not args.no_idempotence_check:
        idem = idempotence_report(
            project, search_roots=roots, mode=args.mode, python=args.python)

    if args.json:
        payload = {
            "project": str(project.resolve()),
            "mode": mode,
            "counts": _summarise(findings),
            "findings": [f.to_dict() for f in findings],
            "relocation_snippet": relocation_snippet(findings),
        }
        if idem is not None:
            payload["idempotence"] = idem
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_report(project.resolve(), findings, idem, args.sample, sys.stdout, mode)

    # Under --json the only thing on stdout is the document: this output is
    # read by CI and by agents, and a human sentence appended to it makes the
    # whole payload unparseable. The same sentence goes to stderr instead.
    stream = sys.stderr if args.json else sys.stdout
    if args.apply:
        buckets = ("dead", "foreign", "shadow") if args.relocate_applied else ("dead",)
        removed = apply_prune(project, findings, buckets=buckets)
        total = sum(removed.values())
        print(f"\nstapel-po-prune: removed {total} entr(ies) from "
              f"{len(removed)} catalogue(s). Review with `git diff`.", file=stream)
    else:
        counts = _summarise(findings)
        print(f"\nstapel-po-prune: dry run — {counts.get('dead', 0)} entr(ies) "
              f"would be removed. Nothing written. Re-run with --apply.",
              file=stream)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
