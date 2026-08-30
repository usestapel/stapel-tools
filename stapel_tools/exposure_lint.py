"""``stapel-exposure-lint`` — a private name must not reach a public repository.

The rule
--------
The ``stapel-*`` libraries and the ``@stapel/*`` packages are open source; the
products built on them and the fleets that deploy them are private clients.
A client's name, domain or deploy path has no business in a library's
changelog, a docstring, a test fixture or a commit message — and yet that is
exactly where it lands, because the person filing a fix names the place they
found it. Twice now the fleet has had to scrub a client name out of published
trees (one of them by rewriting history), so the check is a gate rather than
a reminder.

Rules
-----
EXP001  (error) a private name appears in a tracked text file of a PUBLIC
        project (a ``pyproject.toml`` whose ``name`` starts with ``stapel-``,
        or a ``package.json`` whose ``name`` starts with ``@stapel/`` and is
        not ``private``). Matching is case-insensitive substring over the
        names in the private list, so a domain (``client.example``), a repo
        (``client-fleet``) and a bare word are all one entry: ``client``.
EXP002  (error) a private name appears in the message of a commit that is
        not yet on any remote — ``--commits`` only; the local pre-push hook
        passes it. A published commit message cannot be un-published without
        rewriting history, which is why this runs BEFORE the push.

The list
--------
The names are private too, so they live outside every repository:
``$STAPEL_PRIVATE_NAMES_FILE`` or ``~/.stapel/private-names`` — one name per
line, ``#`` comments allowed. A line starting with ``!`` is an EXCEPTION: a
longer token that merely contains a private name but is not one — a
dictionary word in another language, an option code from a public dataset
(``!otdayu-besplatno`` for a name ``besplatno``). A hit is dropped only when every
occurrence on the line sits inside an excepted token, and the exception lives
in the same owner-held file, never in a repository. With no list the lint
emits a note and no findings: a CI runner without the file cannot check, and
must not pretend it did. The owner's machine, where every push originates,
has the file.

Scope
-----
A private project (a client fleet, a studio checkout) is where the names are
SUPPOSED to be; the lint answers "not applicable" there rather than failing
a repository for containing its own name. Generated artifacts are not
skipped: ``docs/schema.json`` carries docstrings into the published wheel,
so a hit there is exactly the one that matters — fix the docstring and
regenerate.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_LIST = Path.home() / ".stapel" / "private-names"
LIST_ENV = "STAPEL_PRIVATE_NAMES_FILE"

_SKIP_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", "coverage", ".turbo",
})
_SKIP_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2",
    ".ttf", ".otf", ".pdf", ".zip", ".gz", ".whl", ".pyc", ".so", ".dylib",
    ".mo", ".lock",
})


@dataclass
class Finding:
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
            "path": self.path, "line": self.line, "rule": self.rule,
            "message": self.message, "level": self.level,
        }


# ---------------------------------------------------------------------------
# the private list
# ---------------------------------------------------------------------------


def list_path() -> Path:
    override = os.environ.get(LIST_ENV)
    return Path(override).expanduser() if override else DEFAULT_LIST


def load_private_names(path: Optional[Path] = None) -> Optional[list[str]]:
    """The names, lower-cased and de-duplicated; None when there is no list."""
    path = path or list_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    names: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().lower()
        if line and line not in names:
            names.append(line)
    return names


# ---------------------------------------------------------------------------
# scope: is this a public project?
# ---------------------------------------------------------------------------


def _pyproject_name(project: Path) -> Optional[str]:
    try:
        text = (project / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'(?m)^\s*name\s*=\s*["\']([^"\']+)["\']', text)
    return m.group(1) if m else None


def _package_json_public(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    name = data.get("name") or ""
    return name.startswith("@stapel/") and not data.get("private", False)


def is_public_project(project: Path) -> bool:
    """A ``stapel-*`` distribution, a public ``@stapel/*`` package, or a
    monorepo whose ``packages/*`` contain one."""
    name = _pyproject_name(project)
    if name and name.startswith("stapel-"):
        return True
    if _package_json_public(project / "package.json"):
        return True
    packages = project / "packages"
    if packages.is_dir():
        return any(
            _package_json_public(p / "package.json") for p in packages.iterdir()
        )
    return False


# ---------------------------------------------------------------------------
# the scans
# ---------------------------------------------------------------------------


def _walk_text(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fname in sorted(filenames):
            p = Path(dirpath) / fname
            if p.suffix.lower() in _SKIP_SUFFIXES:
                continue
            yield p


_TOKEN = re.compile(r"[a-z0-9._-]+")


def _split_names(names: list[str]) -> tuple[list[str], set[str]]:
    """Private names and the ``!``-prefixed excepted tokens, separately."""
    plain = [n for n in names if not n.startswith("!")]
    exceptions = {n[1:] for n in names if n.startswith("!") and len(n) > 1}
    return plain, exceptions


def _excepted(low: str, name: str, exceptions: set[str]) -> bool:
    """True when every occurrence of *name* on the line is inside an excepted token."""
    if not exceptions:
        return False
    carriers = [tok for tok in _TOKEN.findall(low) if name in tok]
    # An occurrence outside any token (e.g. spanning punctuation the token
    # class excludes) is not carried by an exception: count them too.
    return bool(carriers) and low.count(name) == sum(tok.count(name) for tok in carriers) \
        and all(tok in exceptions for tok in carriers)


def _hits(text: str, names: list[str]) -> Iterable[tuple[int, str]]:
    plain, exceptions = _split_names(names)
    for lineno, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        for name in plain:
            if name in low and not _excepted(low, name, exceptions):
                yield lineno, name
                break


def lint_tree(project: Path, names: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for py in _walk_text(project):
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, name in _hits(text, names):
            findings.append(Finding(
                str(py), lineno, "EXP001",
                f"private name {name!r} in a public project — a client's "
                f"name, domain or deploy path does not belong in a published "
                f"tree; say 'a client fleet' / 'the storefront spec' instead "
                f"(generated? fix the source and regenerate)",
            ))
    return findings


def _unpushed_commits(project: Path) -> list[tuple[str, str]]:
    """``(sha, message)`` for commits on HEAD that no remote ref contains."""
    try:
        out = subprocess.run(
            ["git", "log", "--format=%H%x00%B%x1e", "HEAD", "--not", "--remotes"],
            cwd=project, capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    commits = []
    for chunk in out.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        sha, _, message = chunk.partition("\x00")
        commits.append((sha.strip(), message))
    return commits


def lint_commits(project: Path, names: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for sha, message in _unpushed_commits(project):
        for lineno, name in _hits(message, names):
            findings.append(Finding(
                f"commit {sha[:10]}", lineno, "EXP002",
                f"private name {name!r} in an unpushed commit message — a "
                f"published message cannot be taken back without rewriting "
                f"history; reword it now (git commit --amend)",
            ))
            break
    return findings


def lint_project(
    project: Path,
    *,
    commits: bool = False,
    notes: Optional[list[str]] = None,
    names: Optional[list[str]] = None,
) -> list[Finding]:
    project = project.resolve()
    if names is None:
        names = load_private_names()
    if names is None:
        if notes is not None:
            notes.append(
                f"stapel-exposure-lint: no private-names list at {list_path()} "
                f"(or ${LIST_ENV}) — nothing checked"
            )
        return []
    if not is_public_project(project):
        if notes is not None:
            notes.append(
                "stapel-exposure-lint: not a public stapel distribution — "
                "not applicable"
            )
        return []
    findings = lint_tree(project, names)
    if commits:
        findings.extend(lint_commits(project, names))
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-exposure-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "project_dir", nargs="?", default=".",
        help="Project directory to lint (default: .)",
    )
    parser.add_argument(
        "--commits", action="store_true",
        help="Also check the messages of commits not yet on any remote (EXP002)",
    )
    parser.add_argument("--json", action="store_true", help="Machine output")
    args = parser.parse_args(argv)

    project = Path(args.project_dir)
    if not project.is_dir():
        print(f"Error: not a directory: {project}", file=sys.stderr)
        return 2

    notes: list[str] = []
    findings = lint_project(project, commits=args.commits, notes=notes)
    if args.json:
        print(json.dumps(
            {
                "ok": not findings,
                "errors": len(findings),
                "warnings": 0,
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
        if findings:
            print(f"\n{len(findings)} exposure(s) found.", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
