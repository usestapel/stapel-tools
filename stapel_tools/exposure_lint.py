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
(``!acme-widget`` for a name ``acme``). A hit is dropped only when every
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

Modes
-----
``stapel-exposure-lint [DIR] [--commits]``
    The working tree (EXP001) and, with ``--commits``, the messages of
    commits no remote holds (EXP002). For a person at a keyboard.

``stapel-exposure-lint [DIR] --pushed LOCAL_SHA [--remote REMOTE_SHA]``
    What is being PUSHED, and nothing else: EXP001 over the committed tree
    at ``LOCAL_SHA`` (read with ``git ls-tree``/``git cat-file``, including
    the ``pyproject.toml``/``package.json`` that decide whether the project
    is public), EXP002 over ``REMOTE_SHA..LOCAL_SHA`` — or, for a new branch
    (no ``--remote``, or the all-zero sha git passes), over the commits
    reachable from ``LOCAL_SHA`` that no remote holds. The filesystem is
    never read. This is the mode the generated ``.githooks/pre-push`` uses:
    two sessions sharing one worktree once had a peer's UNCOMMITTED files
    fail the other's push, because the hook scanned ``.`` — a pre-push hook
    judges the commits being pushed, never the working tree.
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


def _pyproject_name_of(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    m = re.search(r'(?m)^\s*name\s*=\s*["\']([^"\']+)["\']', text)
    return m.group(1) if m else None


def _package_json_is_public(text: Optional[str]) -> bool:
    if text is None:
        return False
    try:
        data = json.loads(text)
    except ValueError:
        return False
    name = data.get("name") or ""
    return name.startswith("@stapel/") and not data.get("private", False)


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _pyproject_name(project: Path) -> Optional[str]:
    return _pyproject_name_of(_read(project / "pyproject.toml"))


def _package_json_public(path: Path) -> bool:
    return _package_json_is_public(_read(path))


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


# ---------------------------------------------------------------------------
# the pushed commits — the only honest input for a pre-push hook
# ---------------------------------------------------------------------------

ZERO_SHA = "0" * 40


def _is_zero(sha: Optional[str]) -> bool:
    return not sha or set(sha) == {"0"}


def _git(project: Path, args: list[str], *, stdin: bytes = b"") -> Optional[bytes]:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=project, input=stdin,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return proc.stdout


def _tree_paths(project: Path, sha: str) -> list[str]:
    """Every path in the committed tree at *sha*, scannable ones only."""
    out = _git(project, ["ls-tree", "-r", "-z", "--name-only", sha])
    if out is None:
        return []
    paths = []
    for raw in out.split(b"\0"):
        if not raw:
            continue
        try:
            path = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        parts = path.split("/")
        if any(part in _SKIP_DIRS for part in parts[:-1]):
            continue
        if Path(parts[-1]).suffix.lower() in _SKIP_SUFFIXES:
            continue
        paths.append(path)
    return paths


def _blob(project: Path, sha: str, path: str) -> Optional[str]:
    out = _git(project, ["cat-file", "blob", f"{sha}:{path}"])
    if out is None:
        return None
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _blobs(project: Path, sha: str, paths: list[str]) -> Iterable[tuple[str, str]]:
    """``(path, text)`` for the tree's blobs, in one ``git cat-file --batch``."""
    if not paths:
        return
    stdin = b"".join(f"{sha}:{p}\n".encode("utf-8") for p in paths)
    buf = _git(project, ["cat-file", "--batch"], stdin=stdin)
    if buf is None:
        return
    pos = 0
    for path in paths:
        nl = buf.find(b"\n", pos)
        if nl == -1:
            return
        header = buf[pos:nl].split()
        pos = nl + 1
        if len(header) < 3:  # "<object> missing"
            continue
        try:
            size = int(header[2])
        except ValueError:
            return
        raw, pos = buf[pos:pos + size], pos + size + 1
        try:
            yield path, raw.decode("utf-8")
        except UnicodeDecodeError:
            continue


def is_public_tree(project: Path, sha: str, paths: Optional[list[str]] = None) -> bool:
    """``is_public_project`` decided from the committed tree, not from disk."""
    if paths is None:
        paths = _tree_paths(project, sha)
    known = set(paths)
    if "pyproject.toml" in known:
        name = _pyproject_name_of(_blob(project, sha, "pyproject.toml"))
        if name and name.startswith("stapel-"):
            return True
    if "package.json" in known and _package_json_is_public(
        _blob(project, sha, "package.json")
    ):
        return True
    return any(
        p.startswith("packages/") and p.count("/") == 2
        and p.endswith("/package.json")
        and _package_json_is_public(_blob(project, sha, p))
        for p in paths
    )


def lint_tree_at(
    project: Path, sha: str, names: list[str],
    paths: Optional[list[str]] = None,
) -> list[Finding]:
    if paths is None:
        paths = _tree_paths(project, sha)
    findings: list[Finding] = []
    for path, text in _blobs(project, sha, paths):
        for lineno, name in _hits(text, names):
            findings.append(Finding(
                path, lineno, "EXP001",
                f"private name {name!r} in a public project — a client's "
                f"name, domain or deploy path does not belong in a published "
                f"tree; say 'a client fleet' / 'the storefront spec' instead "
                f"(generated? fix the source and regenerate)",
            ))
    return findings


def _range_commits(
    project: Path, local_sha: str, remote_sha: Optional[str] = None
) -> list[tuple[str, str]]:
    """``(sha, message)`` for the commits this push would publish."""
    fmt = ["--format=%H%x00%B%x1e"]
    # a new branch: everything on local_sha that no remote already holds
    unpushed = ["log", *fmt, local_sha, "--not", "--remotes"]
    if _is_zero(remote_sha):
        out = _git(project, unpushed)
    else:
        out = _git(project, ["log", *fmt, f"{remote_sha}..{local_sha}"])
        if out is None:
            # the remote sha is not an object here (a stale ref, a force-push
            # from elsewhere): fall back rather than check nothing
            out = _git(project, unpushed)
    if out is None:
        return []
    commits = []
    for chunk in out.decode("utf-8", "replace").split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        sha, _, message = chunk.partition("\x00")
        commits.append((sha.strip(), message))
    return commits


def lint_pushed(
    project: Path,
    local_sha: str,
    remote_sha: Optional[str] = None,
    *,
    notes: Optional[list[str]] = None,
    names: Optional[list[str]] = None,
) -> list[Finding]:
    """EXP001 over the committed tree at *local_sha*, EXP002 over the commit
    messages this push would publish. The working tree is never read."""
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
    paths = _tree_paths(project, local_sha)
    if not is_public_tree(project, local_sha, paths):
        if notes is not None:
            notes.append(
                "stapel-exposure-lint: not a public stapel distribution — "
                "not applicable"
            )
        return []
    findings: list[Finding] = lint_tree_at(project, local_sha, names, paths)
    for sha, message in _range_commits(project, local_sha, remote_sha):
        for lineno, name in _hits(message, names):
            findings.append(Finding(
                f"commit {sha[:10]}", lineno, "EXP002",
                f"private name {name!r} in a commit message being pushed — a "
                f"published message cannot be taken back without rewriting "
                f"history; reword it now (git commit --amend / rebase)",
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
    parser.add_argument(
        "--pushed", metavar="LOCAL_SHA",
        help="Push mode: scan the committed tree at LOCAL_SHA (EXP001) and the "
             "messages of the commits this push publishes (EXP002). The "
             "working tree is never read — see the module docstring.",
    )
    parser.add_argument(
        "--remote", metavar="REMOTE_SHA",
        help="With --pushed: the sha the remote already holds. The all-zero "
             "sha (a new branch) means 'whatever no remote holds'.",
    )
    parser.add_argument("--json", action="store_true", help="Machine output")
    args = parser.parse_args(argv)

    if args.remote and not args.pushed:
        parser.error("--remote requires --pushed")
    if args.commits and args.pushed:
        parser.error("--commits is the working-tree mode; --pushed covers EXP002")

    project = Path(args.project_dir)
    if not project.is_dir():
        print(f"Error: not a directory: {project}", file=sys.stderr)
        return 2

    notes: list[str] = []
    if args.pushed:
        if _git(project, ["rev-parse", "--verify", "--quiet",
                          f"{args.pushed}^{{commit}}"]) is None:
            # a gate that cannot read the commit must say so, not pass
            print(f"Error: not a commit in {project}: {args.pushed}",
                  file=sys.stderr)
            return 2
        findings = lint_pushed(project, args.pushed, args.remote, notes=notes)
    else:
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
