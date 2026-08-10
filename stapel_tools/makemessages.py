"""
stapel-makemessages — the wrapper around Django's ``makemessages``.

Why this exists
----------------
``makemessages`` is the command everybody reaches for the moment they add a
translatable string, and run bare it will happily demote every entry whose
source it could not find: obsolete (``#~``, parked at the end of the file) or
**fuzzy** (left among the live entries, still showing a translation, and
skipped by ``msgfmt`` all the same). Neither shows up in a test run unless the
string is asserted somewhere, and almost none are.

A note in the translations directory does not stop this. A wrapper does: the
command people reach for becomes ours, it runs the extraction with the right
arguments, and then it **runs the gate on the result and puts the catalogues
back the way it found them if the gate goes red**. A run that would silently
un-translate strings therefore leaves no trace in the working tree — it leaves
a report instead.

Usage::

    stapel-makemessages                 # all locales, correct ignores, gated
    stapel-makemessages --locale ru --locale en
    stapel-makemessages --accept-losses # keep the result anyway (retiring strings)

``--accept-losses`` is the deliberate escape hatch: when strings really are
being retired, the demotions are the point. It prints what it kept.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import po_lint

#: Directories that never hold translatable source but do hold enormous
#: amounts of text. Django skips dot-directories itself (``.venv`` included);
#: these are the ones it does not.
DEFAULT_IGNORE = (
    "venv",
    "node_modules",
    "staticfiles",
    "media",
    "build",
    "dist",
    "htmlcov",
    "*.egg-info",
)

#: Django's own default is ``html,txt,py``; naming it keeps the invocation
#: honest about what is scanned rather than leaving it to a version default.
DEFAULT_EXTENSIONS = "html,txt,py"


def _snapshot(catalogs) -> dict:
    return {p: p.read_bytes() for p in catalogs}


def _restore(snapshot: dict) -> None:
    for path, blob in snapshot.items():
        path.write_bytes(blob)


def build_command(
    *,
    manage: Optional[Path],
    locales: list,
    extensions: str,
    ignore: list,
    extra: list,
) -> list:
    if manage is not None:
        cmd = [sys.executable, str(manage), "makemessages"]
    else:
        cmd = [sys.executable, "-m", "django", "makemessages"]
    if locales:
        for locale in locales:
            cmd += ["--locale", locale]
    else:
        cmd.append("--all")
    cmd += ["--extension", extensions]
    for pattern in ignore:
        cmd += ["--ignore", pattern]
    cmd += extra
    return cmd


def run(
    project: Path,
    *,
    locales: Optional[list] = None,
    extensions: str = DEFAULT_EXTENSIONS,
    ignore: Optional[list] = None,
    extra: Optional[list] = None,
    accept_losses: bool = False,
    max_fuzzy: int = 0,
    max_obsolete: int = 0,
    stream=sys.stdout,
) -> int:
    """Extract, gate, and roll back on failure. Returns a process exit code."""
    project = Path(project).resolve()
    manage = project / "manage.py"
    manage = manage if manage.is_file() else None

    before = po_lint.find_catalogs(project)
    snapshot = _snapshot(before)

    cmd = build_command(
        manage=manage,
        locales=list(locales or []),
        extensions=extensions,
        ignore=list(ignore if ignore is not None else DEFAULT_IGNORE),
        extra=list(extra or []),
    )
    print(f"stapel-makemessages: {' '.join(cmd)}", file=stream)
    result = subprocess.run(cmd, cwd=str(project))
    if result.returncode != 0:
        print("stapel-makemessages: extraction failed — catalogues untouched",
              file=stream)
        _restore(snapshot)
        return result.returncode

    violations = po_lint.lint_paths(
        [str(project)], max_fuzzy=max_fuzzy, max_obsolete=max_obsolete
    )
    errors = [v for v in violations if v.level == "error"]

    if not errors:
        print("stapel-makemessages: catalogues extracted and clean "
              "(no fuzzy, no obsolete).", file=stream)
        return 0

    for violation in errors:
        print(violation, file=stream)

    if accept_losses:
        print(f"\nstapel-makemessages: {len(errors)} demoted entr(ies) KEPT "
              f"(--accept-losses). Review the diff before committing — every "
              f"one of them is a string gettext will now skip.", file=stream)
        return 0

    _restore(snapshot)
    print(
        f"\nstapel-makemessages: {len(errors)} entr(ies) would be demoted to "
        f"fuzzy/obsolete — gettext skips both, so those strings would ship "
        f"untranslated. The catalogues have been RESTORED; nothing changed.\n"
        f"\n"
        f"Either the string is dead (delete the entry — stapel-po-prune does "
        f"it mechanically), or the catalogue holds a string this tree does not "
        f"own (move it to the owning package's catalogue or its override seam "
        f"— a catalogue is a projection of its own sources). Re-run with "
        f"--accept-losses when the demotion is the point.",
        file=stream,
    )
    return 1


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-makemessages",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_dir", nargs="?", default=".",
                        help="Directory holding manage.py (default: .)")
    parser.add_argument("--locale", action="append", default=[], metavar="LANG",
                        help="Locale to update (repeatable). Default: --all.")
    parser.add_argument("--extension", default=DEFAULT_EXTENSIONS,
                        help=f"Extensions to scan (default: {DEFAULT_EXTENSIONS}).")
    parser.add_argument("--ignore", action="append", default=None, metavar="PATTERN",
                        help="Override the default ignore set (repeatable).")
    parser.add_argument("--accept-losses", action="store_true",
                        help="Keep the result even when entries were demoted "
                             "to fuzzy/obsolete (use when retiring strings).")
    parser.add_argument("--max-fuzzy", type=int, default=0, metavar="N")
    parser.add_argument("--max-obsolete", type=int, default=0, metavar="N")
    args, extra = parser.parse_known_args(argv)

    project = Path(args.project_dir)
    if not project.is_dir():
        print(f"stapel-makemessages: no such directory: {project}", file=sys.stderr)
        return 2

    return run(
        project,
        locales=args.locale,
        extensions=args.extension,
        ignore=args.ignore,
        extra=extra,
        accept_losses=args.accept_losses,
        max_fuzzy=args.max_fuzzy,
        max_obsolete=args.max_obsolete,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
