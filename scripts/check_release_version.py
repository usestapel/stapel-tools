#!/usr/bin/env python3
"""The commit about to be tagged is the release it claims to be.

Refuses (exit 1), naming each reason:

* the working tree has changes — the tag names a commit, not the files on disk;
* HEAD is not ``origin/main`` — CI ran on what was pushed, the publish waits
  for CI on the tagged commit;
* ``v<version>`` (``pyproject.toml``) already exists locally or on origin — a
  pushed tag is never moved, a burned version is followed by the next patch;
* ``CHANGELOG.md`` has no ``## [<version>]`` section.

    python scripts/check_release_version.py          # in the repository root
"""
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def problems(root: Path, *, ask_remote: bool = True) -> list[str]:
    out: list[str] = []
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    tag = f"v{version}"
    dirty = _git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    if dirty:
        out.append(f"the working tree has uncommitted changes:\n{dirty}")
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    main = _git(root, "rev-parse", "--verify", "--quiet", "origin/main^{commit}").stdout.strip()
    if not main:
        out.append("no origin/main to compare with (git fetch origin)")
    elif head != main:
        out.append(f"HEAD {head[:12]} is not origin/main {main[:12]} — push first, let CI run on it")
    if _git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}").returncode == 0:
        out.append(f"{tag} already exists locally — bump the version (a tag is never moved)")
    if ask_remote:
        remote = _git(root, "ls-remote", "--tags", "origin", f"refs/tags/{tag}")
        if remote.returncode != 0:
            out.append(f"could not ask origin for {tag}: {remote.stderr.strip()}")
        elif remote.stdout.strip():
            out.append(f"{tag} already exists on origin — bump the version (a tag is never moved)")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^## \[{re.escape(version)}\]", changelog, re.M):
        out.append(f"CHANGELOG.md has no '## [{version}]' section")
    return out


def main() -> int:
    root = Path.cwd()
    found = problems(root)
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    if found:
        print(f"check-release-version: v{version} is NOT ready to tag:")
        for p in found:
            print(f"  - {p}")
        return 1
    print(f"check-release-version: v{version} is untagged, CHANGELOG has it, HEAD is origin/main, tree clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
