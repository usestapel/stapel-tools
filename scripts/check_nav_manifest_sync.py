#!/usr/bin/env python
"""Drift gate for the scripted-fullstack navigation (Ф1) nav-manifest
mirror: ``create_project.FRONTEND_REACT_LIBS[<key>]["nav"]`` is a MANUALLY
PINNED MIRROR of each ``@stapel/<key>-react`` pair's own
``nav-manifest.json`` (same discipline the version pins right above it in
``create_project.py`` document — see that dict's module docstring). A
mirror this task hand-writes can silently drift from the real file the next
time a pair's nav surface changes (a new entry, a renamed icon, a
re-ordered menu) — this script is the machine-checkable half of that
discipline, a peer of the pin-verification comments (which are read by a
human at pin time; this is read by CI/pre-commit on every run).

Run from the stapel-tools repo root, with the sibling ``stapel-react``
checkout present (same convention as e.g.
``tests/test_frontend_scaffold.py``'s eslint-plugin check — this script
SKIPS, exit 0, when the sibling checkout isn't there; it has nothing to
compare against, which is not itself a failure for a checkout that doesn't
carry stapel-react at all):

    python scripts/check_nav_manifest_sync.py

Exit 0 = every mirrored ``"nav"`` entry matches its package's real
``nav-manifest.json`` byte-for-byte (as parsed JSON — key order doesn't
matter, content does). Exit 1 = at least one mismatch, printed with a diff
of the two dicts so the fix is obvious (update the mirror in
create_project.py's FRONTEND_REACT_LIBS to match the real file, or vice
versa if the mirror caught a real regression in the pair itself).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STAPEL_REACT_ROOT = REPO_ROOT.parent / "stapel-react" / "packages"


def _load_real_manifest(key: str, package: str) -> dict | None:
    """The real ``packages/<key>-react/nav-manifest.json`` this project's
    mirror claims to track — keyed by the FRONTEND_REACT_LIBS dict key,
    which is always the ``<key>`` in ``@stapel/<key>-react``."""
    path = STAPEL_REACT_ROOT / f"{key}-react" / "nav-manifest.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    assert data.get("package") == package, (
        f"{path} declares package {data.get('package')!r}, mirror expected {package!r}"
    )
    return data


def check() -> int:
    if not STAPEL_REACT_ROOT.is_dir():
        print(
            "check_nav_manifest_sync: sibling stapel-react checkout not "
            f"found at {STAPEL_REACT_ROOT} — skipping (nothing to compare "
            "against).",
        )
        return 0

    from stapel_tools.create_project import FRONTEND_REACT_LIBS

    mismatches: list[str] = []
    checked = 0
    for key, info in FRONTEND_REACT_LIBS.items():
        mirrored_entries = info.get("nav")
        if not mirrored_entries:
            continue
        real = _load_real_manifest(key, info["package"])
        if real is None:
            print(
                f"check_nav_manifest_sync: {key} — no real nav-manifest.json "
                f"found at {STAPEL_REACT_ROOT / f'{key}-react' / 'nav-manifest.json'} "
                "to compare against, skipping this pair.",
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

    print(f"check_nav_manifest_sync: {checked} nav-bearing pair(s) match their real nav-manifest.json.")
    return 0


def main() -> None:
    sys.exit(check())


if __name__ == "__main__":
    main()
