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
        project (see "Scope" for how a project is recognised). Matching is case-insensitive substring over the
        names in the private list, so a domain (``client.example``), a repo
        (``client-fleet``) and a bare word are all one entry: ``client``.
EXP002  (error) a private name appears in the message of a commit that is
        not yet on any remote — ``--commits`` only; the local pre-push hook
        passes it. A published commit message cannot be un-published without
        rewriting history, which is why this runs BEFORE the push.
EXP000  (error, CI-default) the private-names list resolved to zero names —
        the run above checked nothing, and a green EXP001/EXP002 out of zero
        names is not a pass, it is an unconfigured gate. See "The list"
        below for when this fires.

The list
--------
The names are private too, so they live outside every repository:
``$STAPEL_PRIVATE_NAMES_FILE`` or ``~/.stapel/private-names`` — one name per
line, ``#`` comments allowed. A line starting with ``!`` is an EXCEPTION: a
longer token that merely contains a private name but is not one — a
dictionary word in another language, an option code from a public dataset
(``!acme-widget`` for a name ``acme``). A hit is dropped only when every
occurrence on the line sits inside an excepted token, and the exception lives
in the same owner-held file, never in a repository.

With no list — the file absent, or present and empty — the lint always
prints a one-line note (``checked 0 private names ... — nothing verified``)
and reports zero EXP001/EXP002 findings, because there is nothing to check
them against. What happens to the EXIT CODE from that state depends on
where it runs, because "zero names checked" and "zero names found" look
identical on a terminal, and a CI runner silently missing its secret must
not read as a clean bill of health the way a developer's empty scratch list
does:

* Locally (no ``CI``/``GITHUB_ACTIONS``): exit 0. A developer without the
  owner's list is not blocked from using the rest of the tool.
* In CI (``CI=true`` or ``GITHUB_ACTIONS=true``): exit non-zero with
  ``EXP000`` by default. A CI runner missing ``STAPEL_PRIVATE_NAMES`` was,
  until this rule existed, the exact scenario that made the gate check zero
  names and report success — every OSS repo, every run, forever green,
  verifying nothing.
* ``--require-names`` forces the CI behaviour even outside CI (to exercise
  the gate locally, or from a script that sets neither env var).
* ``--allow-empty`` forces the local behaviour even inside CI — an explicit,
  visible opt-out, never the default.
* ``--strict`` (what the generated pre-push hook passes) implies
  ``--require-names`` and adds EXP003. A new machine without the owner's list
  once pushed a release through a hook that had checked zero names; the hook
  is where that must stop.

The empty state is announced as a banner on stderr naming the path that was
read and why it yielded nothing (missing, unreadable, empty, or exceptions
only), never as a one-liner that scrolls past.

The owner's machine, where every push originates, has the file.

Scope
-----
A project is recognised, in this order, by:

1. ``.stapel-visibility`` at the root, first word ``public`` or ``private`` —
   an explicit declaration for a repository no manifest describes (a
   generated example project, a docs repo);
2. ``pyproject.toml`` — public when ``name`` starts with ``stapel-``;
3. ``package.json`` — public when ``name`` starts with ``@stapel/`` and it is
   not ``private``; a monorepo is public when one of ``packages/*`` is;
4. ``settings.gradle.kts`` / ``settings.gradle`` — public when
   ``rootProject.name`` starts with ``stapel-``.

A recognised project that is not public (a client fleet, a studio checkout)
is where the names are SUPPOSED to be; the lint answers "not applicable"
there rather than failing a repository for containing its own name. A
directory none of these describe is NOT RECOGNISED, which is a different
answer: before 0.81.3 it read "not applicable" too, and a public Gradle
repository passed every push having checked nothing. Generated artifacts are not
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

Every run also prints a NOTE (never a finding, never an exit code) when THIS
checkout's own ``.githooks/pre-push`` still hands the tool a directory. A note
rather than a rule because on the day it shipped 38 of the fleet's 39
repositories carried the old hook, and failing all of their pushes to say so
would have taught one thing only: ``--no-verify``.
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

# A gate whose input is "whatever the environment handed it" must not read as
# green when that input is empty. CI unset the secret (STAPEL_PRIVATE_NAMES,
# translated by the workflow into $STAPEL_PRIVATE_NAMES_FILE) is exactly that
# case: zero names checked, zero findings, historically a silent pass.
_CI_ENV_VARS = ("CI", "GITHUB_ACTIONS")


def _ci_active() -> bool:
    return any(os.environ.get(v) == "true" for v in _CI_ENV_VARS)


def names_state(path: Optional[Path] = None) -> str:
    """Why the list resolved to zero names: missing | unreadable | empty |
    exceptions-only. Only meaningful when it did."""
    path = path or list_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    lines = [ln.split("#", 1)[0].strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    if lines and all(ln.startswith("!") for ln in lines):
        return "exceptions-only"
    return "empty"


def _empty_names_note() -> str:
    """The banner printed whenever the list resolves to zero names.

    Several lines on purpose: the one-line form scrolled past on a new
    machine and a whole push went out having checked zero names."""
    path = list_path()
    state = names_state(path)
    where = f"{path}" + ("" if os.environ.get(LIST_ENV) else f" (default; ${LIST_ENV} unset)")
    why = {
        "missing": "the file does not exist",
        "unreadable": "the file cannot be read",
        "empty": "the file holds no names",
        "exceptions-only": "the file holds only !exceptions, no names",
    }[state]
    bar = "!" * 72
    return (
        f"{bar}\n"
        f"stapel-exposure-lint: checked 0 private names — NOTHING VERIFIED\n"
        f"  list: {where}\n"
        f"  why:  {why}\n"
        f"  A green run from here proves nothing. Restore the owner's list\n"
        f"  (one name per line) before pushing; --strict refuses (EXP000).\n"
        f"{bar}"
    )


def _plain_names(names: Optional[list[str]]) -> list[str]:
    return [n for n in names or () if not n.startswith("!")]


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


PUBLIC = "public"
PRIVATE = "private"
UNRECOGNISED = "unrecognised"

VISIBILITY_FILE = ".stapel-visibility"
GRADLE_SETTINGS = ("settings.gradle.kts", "settings.gradle")


@dataclass(frozen=True)
class Scope:
    """What the lint decided about a project, and from which file."""

    kind: str          # PUBLIC | PRIVATE | UNRECOGNISED
    source: str        # the file that decided it, or "" when nothing did

    @property
    def public(self) -> bool:
        return self.kind == PUBLIC


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
    if not isinstance(data, dict):
        return False
    name = data.get("name") or ""
    return name.startswith("@stapel/") and not data.get("private", False)


def _gradle_root_name_of(text: Optional[str]) -> Optional[str]:
    """``rootProject.name`` of a Gradle settings script (Kotlin or Groovy)."""
    if text is None:
        return None
    m = re.search(r'(?m)^\s*rootProject\.name\s*=\s*["\']([^"\']+)["\']', text)
    return m.group(1) if m else None


def _visibility_of(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    for raw in text.splitlines():
        word = raw.split("#", 1)[0].strip().lower()
        if word:
            return word if word in (PUBLIC, PRIVATE) else None
    return None


def classify(read, package_manifests: Iterable[str] = ()) -> Scope:
    """Decide the scope from *read* (relative path -> text or None).

    One function for the working tree and for a committed tree, so the two
    modes cannot disagree about what "public" means."""
    marker = read(VISIBILITY_FILE)
    if marker is not None:
        kind = _visibility_of(marker)
        if kind is not None:
            return Scope(kind, VISIBILITY_FILE)
    recognised = ""
    pyproject = read("pyproject.toml")
    if pyproject is not None:
        recognised = recognised or "pyproject.toml"
        name = _pyproject_name_of(pyproject)
        if name and name.startswith("stapel-"):
            return Scope(PUBLIC, "pyproject.toml")
    package = read("package.json")
    if package is not None:
        recognised = recognised or "package.json"
        if _package_json_is_public(package):
            return Scope(PUBLIC, "package.json")
    for manifest in package_manifests:
        recognised = recognised or manifest
        if _package_json_is_public(read(manifest)):
            return Scope(PUBLIC, manifest)
    for settings in GRADLE_SETTINGS:
        text = read(settings)
        if text is None:
            continue
        recognised = recognised or settings
        name = _gradle_root_name_of(text)
        if name and name.startswith("stapel-"):
            return Scope(PUBLIC, settings)
    if recognised:
        return Scope(PRIVATE, recognised)
    return Scope(UNRECOGNISED, "")


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _pyproject_name(project: Path) -> Optional[str]:
    return _pyproject_name_of(_read(project / "pyproject.toml"))


def _package_json_public(path: Path) -> bool:
    return _package_json_is_public(_read(path))


def project_scope(project: Path) -> Scope:
    packages = project / "packages"
    manifests = []
    if packages.is_dir():
        manifests = sorted(
            f"packages/{p.name}/package.json" for p in packages.iterdir()
            if (p / "package.json").is_file()
        )
    return classify(lambda rel: _read(project / rel), manifests)


def is_public_project(project: Path) -> bool:
    """A ``stapel-*`` distribution, a public ``@stapel/*`` package (or a
    monorepo whose ``packages/*`` contain one), a ``stapel-*`` Gradle build,
    or a directory that declares itself public in ``.stapel-visibility``."""
    return project_scope(project).public


def scope_note(scope: Scope) -> Optional[str]:
    """The note a non-public scope prints; None for a public one."""
    if scope.kind == PRIVATE:
        return (
            f"stapel-exposure-lint: not a public stapel project ({scope.source}) "
            "— not applicable"
        )
    if scope.kind == UNRECOGNISED:
        return (
            "stapel-exposure-lint: NOT RECOGNISED — no .stapel-visibility, "
            "pyproject.toml, package.json or settings.gradle(.kts) here, so "
            "whether this tree is public was not decided and nothing was "
            f"checked. Declare it: echo public > {VISIBILITY_FILE} (or "
            "private). --strict refuses this state (EXP003)."
        )
    return None


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


def tree_scope(project: Path, sha: str, paths: Optional[list[str]] = None) -> Scope:
    """``project_scope`` decided from the committed tree, not from disk."""
    if paths is None:
        paths = _tree_paths(project, sha)
    known = set(paths)
    manifests = sorted(
        p for p in paths
        if p.startswith("packages/") and p.count("/") == 2
        and p.endswith("/package.json")
    )

    def read(rel: str) -> Optional[str]:
        return _blob(project, sha, rel) if rel in known else None

    return classify(read, manifests)


def is_public_tree(project: Path, sha: str, paths: Optional[list[str]] = None) -> bool:
    """``is_public_project`` decided from the committed tree, not from disk."""
    return tree_scope(project, sha, paths).public


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
    scope: Optional[Scope] = None,
) -> list[Finding]:
    """EXP001 over the committed tree at *local_sha*, EXP002 over the commit
    messages this push would publish. The working tree is never read."""
    project = project.resolve()
    if names is None:
        names = load_private_names()
    paths = _tree_paths(project, local_sha)
    if scope is None:
        scope = tree_scope(project, local_sha, paths)
    if not _plain_names(names):
        if notes is not None:
            notes.append(_empty_names_note())
        return []
    if not scope.public:
        if notes is not None:
            notes.append(scope_note(scope))
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


#: A pre-push hook that hands this tool a DIRECTORY is scanning the working
#: tree — every untracked scratch file in the checkout, including another
#: session's. `--pushed <sha>` judges the commits being pushed instead. The
#: canonical hook is stapel_tools._library_templates.PRE_PUSH.
_WORKTREE_HOOK_RE = re.compile(
    r"^\s*stapel-exposure-lint(?![^\n]*--pushed\b)[ \t]+[^\s|&;]", re.MULTILINE
)


def stale_hook_note(project: Path) -> Optional[str]:
    """A note when THIS checkout's pre-push hook still scans the working tree.

    A note and not a finding, deliberately. On the day this shipped 38 of the
    fleet's 39 repositories carried the old hook, and turning every one of
    their pushes red to tell them so would have taught exactly one thing:
    ``--no-verify``. The note costs nothing, is printed on every run in the
    repository that has the problem, and names the one line to change.
    """
    hook = project / ".githooks" / "pre-push"
    try:
        text = hook.read_text(encoding="utf-8")
    except OSError:
        return None
    if not _WORKTREE_HOOK_RE.search(text):
        return None
    return (
        "stapel-exposure-lint: .githooks/pre-push scans the WORKING TREE "
        "(`stapel-exposure-lint <dir>`), so an untracked file — a peer's, in a "
        "shared checkout — can fail a push that does not contain it. Use "
        "`--pushed \"$local_sha\" [--remote \"$remote_sha\"]` over the refs git "
        "names on stdin (stapel_tools._library_templates.PRE_PUSH)."
    )


def lint_project(
    project: Path,
    *,
    commits: bool = False,
    notes: Optional[list[str]] = None,
    names: Optional[list[str]] = None,
    scope: Optional[Scope] = None,
) -> list[Finding]:
    project = project.resolve()
    if names is None:
        names = load_private_names()
    if scope is None:
        scope = project_scope(project)
    if not _plain_names(names):
        if notes is not None:
            notes.append(_empty_names_note())
        return []
    if not scope.public:
        if notes is not None:
            notes.append(scope_note(scope))
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
    parser.add_argument(
        "--require-names", action="store_true",
        help="Exit non-zero (EXP000) when the private-names list resolves to "
             "zero names — the CI default already does this; use this flag "
             "to get the same behaviour outside CI (e.g. to test the gate, "
             "or in a workflow that sets neither $CI nor $GITHUB_ACTIONS).",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="What hooks pass: --require-names, and also exit non-zero "
             "(EXP003) when the directory is not recognised as any project "
             "— see 'Scope' above.",
    )
    parser.add_argument(
        "--allow-empty", action="store_true",
        help="Exit 0 with a warning when the private-names list resolves to "
             "zero names, even in CI. An explicit opt-out of the CI default "
             "— never pass this to silence a secret you simply haven't set.",
    )
    args = parser.parse_args(argv)

    if args.remote and not args.pushed:
        parser.error("--remote requires --pushed")
    if args.commits and args.pushed:
        parser.error("--commits is the working-tree mode; --pushed covers EXP002")
    if (args.require_names or args.strict) and args.allow_empty:
        parser.error("--require-names/--strict and --allow-empty are mutually exclusive")

    project = Path(args.project_dir)
    if not project.is_dir():
        print(f"Error: not a directory: {project}", file=sys.stderr)
        return 2

    notes: list[str] = []
    hook_note = stale_hook_note(project)
    if hook_note is not None:
        notes.append(hook_note)
    names = load_private_names()
    if args.pushed:
        if _git(project, ["rev-parse", "--verify", "--quiet",
                          f"{args.pushed}^{{commit}}"]) is None:
            # a gate that cannot read the commit must say so, not pass
            print(f"Error: not a commit in {project}: {args.pushed}",
                  file=sys.stderr)
            return 2
        scope = tree_scope(project.resolve(), args.pushed)
        findings = lint_pushed(project, args.pushed, args.remote, notes=notes,
                               names=names, scope=scope)
    else:
        scope = project_scope(project.resolve())
        findings = lint_project(project, commits=args.commits, notes=notes,
                                names=names, scope=scope)

    empty = not _plain_names(names)
    if empty and not args.allow_empty and (
        args.require_names or args.strict or _ci_active()
    ):
        findings = [
            *findings,
            Finding(
                "<config>", 0, "EXP000",
                f"no private names configured ({names_state()} list at "
                f"{list_path()}) — the gate verified nothing",
            ),
        ]
    if scope.kind == UNRECOGNISED:
        if empty:
            # the names banner returned early; the scope still gets its say
            notes.append(scope_note(scope))
        if args.strict:
            findings = [
                *findings,
                Finding(
                    "<scope>", 0, "EXP003",
                    "not recognised as a project — cannot tell whether this "
                    f"tree is public; add {VISIBILITY_FILE} (public|private) "
                    "or the manifest the project is built from",
                ),
            ]

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
