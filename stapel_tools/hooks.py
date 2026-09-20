"""Install and audit the repository's git hooks (`stapel-hooks`).

The hooks themselves are the easy part. The weak spot is INSTALLATION:
`core.hooksPath` lives in `.git/config`, which is per-clone and not
versioned, so a hook that every repo carries in `.githooks/` is still
inactive in every fresh clone until somebody runs one command — and nobody
finds out, because an inactive hook is indistinguishable from a passing one.

So there are two commands:

  stapel-hooks install   point core.hooksPath at .githooks and verify it
  stapel-hooks doctor    exit non-zero when the hooks are NOT active, or
                         when the installed pre-push predates the template

`doctor` is meant to be wired into whatever a developer already runs (a
Makefile default target, a deploy script) so an unhooked clone announces
itself instead of quietly pushing branches that revert other people's work.

Version marker: every hook this repo ships carries a line of the form

    # stapel-hooks: pre-push v1

Hand-maintained variants in consumer repos carry the same line, which is how
`doctor` can tell a hook that merely exists from a hook that is current.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from . import _library_templates as T

HOOKS_DIR = ".githooks"

#: The marker a hook carries so `doctor` can compare versions.
MARKER_RE = re.compile(r"^#\s*stapel-hooks:\s*(?P<hook>[a-z-]+)\s*v(?P<version>\d+)\s*$", re.M)

#: Hooks this tool knows how to write from scratch.
TEMPLATES = {
    "pre-push": T.PRE_PUSH,
    "pre-commit": T.PRE_COMMIT,
}


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _repo_root(start: Path) -> Path | None:
    try:
        out = _git(start, "rev-parse", "--show-toplevel").stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return Path(out) if out else None


def template_version(hook: str) -> str | None:
    """The version marker of the template this tool ships for `hook`."""
    text = TEMPLATES.get(hook)
    if text is None:
        return None
    for match in MARKER_RE.finditer(text):
        if match.group("hook") == hook:
            return match.group("version")
    return None


def installed_version(path: Path, hook: str) -> str | None:
    """The version marker of the hook file on disk, or None when unmarked."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for match in MARKER_RE.finditer(text):
        if match.group("hook") == hook:
            return match.group("version")
    return None


def _write_hook(path: Path, text: str) -> None:
    # newline="\n" is not decoration: a hook checked out with CRLF endings
    # fails under Git for Windows' sh with a bare "not found", and the repo
    # needs a matching .gitattributes (`.githooks/* text eol=lf`) for the
    # checkout not to undo this.
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    path.chmod(0o755)


def _hooks_path_ok(repo: Path) -> tuple[bool, str]:
    result = _git(repo, "config", "--get", "core.hooksPath", check=False)
    configured = result.stdout.strip()
    if not configured:
        return False, ""
    resolved = (repo / configured).resolve() if not Path(configured).is_absolute() else Path(configured).resolve()
    return resolved == (repo / HOOKS_DIR).resolve(), configured


def doctor(repo: Path, hooks: list[str]) -> int:
    """Report on the hook installation. Returns a process exit code."""
    root = _repo_root(repo)
    if root is None:
        print(f"stapel-hooks doctor: {repo} is not a git working tree.", file=sys.stderr)
        return 1

    problems: list[str] = []

    ok, configured = _hooks_path_ok(root)
    if not ok:
        shown = configured or "<unset>"
        problems.append(
            f"core.hooksPath is {shown}, not {HOOKS_DIR} — the repository's hooks are NOT active.\n"
            f"    Fix: stapel-hooks install --repo {root}   (or: git -C {root} config core.hooksPath {HOOKS_DIR})"
        )

    for hook in hooks:
        path = root / HOOKS_DIR / hook
        if not path.exists():
            problems.append(f"{HOOKS_DIR}/{hook} is missing.\n    Fix: stapel-hooks install --repo {root}")
            continue
        if not path.stat().st_mode & 0o111:
            problems.append(f"{HOOKS_DIR}/{hook} is not executable.\n    Fix: chmod +x {path}")
        want = template_version(hook)
        have = installed_version(path, hook)
        if want is None:
            continue
        if have is None:
            problems.append(
                f"{HOOKS_DIR}/{hook} carries no version marker — it predates the current template (v{want}).\n"
                f"    Fix: bring it up to date and add the line: # stapel-hooks: {hook} v{want}"
            )
        elif have != want:
            problems.append(
                f"{HOOKS_DIR}/{hook} is v{have}; the current template is v{want}.\n"
                f"    Fix: update the hook and its marker line."
            )

    if problems:
        print("stapel-hooks doctor: FAILED", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    versions = ", ".join(
        f"{hook} v{installed_version(root / HOOKS_DIR / hook, hook) or '?'}" for hook in hooks
    )
    print(f"stapel-hooks doctor: OK — core.hooksPath={HOOKS_DIR} ({versions}).")
    return 0


def install(repo: Path, hooks: list[str]) -> int:
    root = _repo_root(repo)
    if root is None:
        print(f"stapel-hooks install: {repo} is not a git working tree.", file=sys.stderr)
        return 1

    for hook in hooks:
        path = root / HOOKS_DIR / hook
        template = TEMPLATES.get(hook)
        if not path.exists():
            if template is None:
                print(f"stapel-hooks install: no template for {hook}.", file=sys.stderr)
                return 1
            _write_hook(path, template)
            print(f"stapel-hooks install: wrote {HOOKS_DIR}/{hook} (v{template_version(hook)}).")
        else:
            # Consumer repos hand-maintain their variants (repo-root build
            # context, extra stages). Never overwrite one — `doctor` reports
            # on it instead.
            if not path.stat().st_mode & 0o111:
                path.chmod(0o755)
            print(f"stapel-hooks install: kept existing {HOOKS_DIR}/{hook} (v{installed_version(path, hook) or '?'}).")

    _git(root, "config", "core.hooksPath", HOOKS_DIR)
    ok, configured = _hooks_path_ok(root)
    if not ok:
        print(
            f"stapel-hooks install: core.hooksPath did not take (reads back as {configured or '<unset>'}).",
            file=sys.stderr,
        )
        return 1
    print(f"stapel-hooks install: core.hooksPath={configured} in {root}.")
    return doctor(root, hooks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-hooks",
        description="Install and audit this repository's git hooks (.githooks/).",
    )
    parser.add_argument("command", choices=["install", "doctor"])
    parser.add_argument("--repo", default=".", help="repository to act on (default: the current directory)")
    parser.add_argument(
        "--hook",
        action="append",
        dest="hooks",
        help="hook to install/check (repeatable; default: pre-push)",
    )
    args = parser.parse_args(argv)

    hooks = args.hooks or ["pre-push"]
    repo = Path(args.repo).resolve()
    if args.command == "install":
        return install(repo, hooks)
    return doctor(repo, hooks)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
