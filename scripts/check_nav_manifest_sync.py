#!/usr/bin/env python
"""Drift gate for the scripted-fullstack navigation (P1) nav-manifest
mirror: ``create_project.FRONTEND_REACT_LIBS[<key>]["nav"]`` is a MANUALLY
PINNED MIRROR of each ``@stapel/<key>-react`` pair's own
``nav-manifest.json`` (same discipline the version pins right above it in
``create_project.py`` document — see that dict's module docstring). A
mirror this task hand-writes can silently drift from the real file the next
time a pair's nav surface changes (a new entry, a renamed icon, a
re-ordered menu) — this script is the machine-checkable half of that
discipline, a peer of the pin-verification comments (which are read by a
human at pin time; this is read by CI/pre-commit on every run).

Where the real files come from
------------------------------
``$SIBLING_ROOT`` (default ``..`` — the workspace convention every generated
``gen:*`` invocation already uses, e.g.
``API_SCHEMA=${SIBLING_ROOT:-..}/stapel-search/docs/schema.json``), then
``stapel-react/packages/<key>-react/nav-manifest.json``. Overriding it is
what lets CI check against a checkout that is not literally a sibling of
this repo, and what makes this script testable against a fixture tree
instead of the developer's own machine.

Two skips and one failure — the distinction is the point
--------------------------------------------------------
* No ``stapel-react`` checkout at all → SKIP, exit 0. There is nothing to
  compare against, which is not a defect of a checkout that does not carry
  stapel-react (the same convention ``tests/test_frontend_scaffold.py``'s
  eslint-plugin check uses).
* A pair with no ``"nav"`` mirror → not checked. It claims nothing.
* A pair that DOES carry a ``"nav"`` mirror but whose real
  ``nav-manifest.json`` is missing → **FAILURE** (spec §3.8). This used to be
  a silent skip, and a silent skip is exactly the shape of the bug the gate
  exists for: the mirror claims a nav surface that the package no longer
  publishes, and every scaffolded project keeps mounting routes for screens
  that are not there.

    python scripts/check_nav_manifest_sync.py
    SIBLING_ROOT=/path/to/workspace python scripts/check_nav_manifest_sync.py

Exit 0 = every mirrored ``"nav"`` entry matches its package's real
``nav-manifest.json`` (as parsed JSON — key order doesn't matter, content
does), and every mirrored version matches too. Exit 1 = at least one
mismatch, printed with both sides so the fix is obvious (update the mirror
in create_project.py's FRONTEND_REACT_LIBS to match the real file, or vice
versa if the mirror caught a real regression in the pair itself).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def sibling_root() -> Path:
    """The workspace root the sibling checkouts live in. ``$SIBLING_ROOT``
    when set, else this repo's parent — the same default the generated
    ``gen:*`` invocations spell as ``${SIBLING_ROOT:-..}``."""
    env = os.environ.get("SIBLING_ROOT")
    return Path(env).expanduser().resolve() if env else REPO_ROOT.parent


def packages_root(root: Path | None = None) -> Path:
    return (root or sibling_root()) / "stapel-react" / "packages"


def _load_real_manifest(packages: Path, key: str, package: str) -> dict | None:
    """The real ``packages/<key>-react/nav-manifest.json`` this project's
    mirror claims to track — keyed by the FRONTEND_REACT_LIBS dict key,
    which is always the ``<key>`` in ``@stapel/<key>-react``."""
    path = packages / f"{key}-react" / "nav-manifest.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    assert data.get("package") == package, (
        f"{path} declares package {data.get('package')!r}, mirror expected {package!r}"
    )
    return data


def check(root: Path | None = None) -> int:
    packages = packages_root(root)
    if not packages.is_dir():
        print(
            "check_nav_manifest_sync: sibling stapel-react checkout not "
            f"found at {packages} — skipping (nothing to compare "
            "against). Set SIBLING_ROOT to point at the workspace that "
            "carries it.",
        )
        return 0

    sys.path.insert(0, str(REPO_ROOT))
    from stapel_tools.create_project import FRONTEND_REACT_LIBS

    mismatches: list[str] = []
    checked = 0
    for key, info in FRONTEND_REACT_LIBS.items():
        mirrored_entries = info.get("nav")
        if mirrored_entries is None:
            continue
        real = _load_real_manifest(packages, key, info["package"])
        if real is None:
            # Spec §3.8: a REGISTERED pair with no real manifest is drift,
            # not an absence. The mirror is a claim about a published file.
            mismatches.append(
                f"{key}: FRONTEND_REACT_LIBS carries a \"nav\" mirror, but "
                f"{packages / f'{key}-react' / 'nav-manifest.json'} does not "
                "exist. Either the pair stopped publishing a nav manifest "
                "(drop the mirror) or the checkout is stale (`pnpm gen:nav`)."
            )
            continue
        checked += 1
        if real.get("version") != info["version"]:
            mismatches.append(
                f"{key}: mirrored version {info['version']!r} != real "
                f"nav-manifest.json version {real.get('version')!r}"
            )
        real_entries = real.get("entries", [])
        if mirrored_entries != real_entries:
            mismatches.append(
                f"{key}: mirrored \"nav\" entries differ from the real "
                f"nav-manifest.json:\n"
                f"  mirror: {json.dumps(mirrored_entries, indent=2, sort_keys=True)}\n"
                f"  real:   {json.dumps(real_entries, indent=2, sort_keys=True)}"
            )

    if mismatches:
        print("check_nav_manifest_sync: FRONTEND_REACT_LIBS nav mirror drift found:\n")
        for m in mismatches:
            print(m, "\n")
        return 1

    print(
        f"check_nav_manifest_sync: {checked} nav-bearing pair(s) match their "
        f"real nav-manifest.json (under {packages})."
    )
    return 0


def main() -> None:
    sys.exit(check())


if __name__ == "__main__":
    main()
