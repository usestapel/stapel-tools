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

One skip and three failures — the distinction is the point
----------------------------------------------------------
The gate walks EVERY key of ``FRONTEND_REACT_LIBS``, not only the ones that
already carry a mirror. That is the whole shape of the defect it was blind
to until now: it iterated the MIRRORS and asked whether each matched a real
file, so a pair that started publishing a nav manifest the registry had
never mirrored was not "drift" to it — it was invisible. Five registered
pairs (billing, calendar, forms, recordings, workspaces) published a
combined 15 entries that no generated container mounted, and this script
printed a green line the entire time. A gate that cannot see the thing it
guards is worse than no gate, because it is believed.

* No ``stapel-react`` checkout at all → SKIP, exit 0. There is nothing to
  compare against, which is not a defect of a checkout that does not carry
  stapel-react (the same convention ``tests/test_frontend_scaffold.py``'s
  eslint-plugin check uses). This is the ONLY skip.
* A pair with no ``"nav"`` mirror AND no published (or empty)
  ``nav-manifest.json`` → in sync. It claims nothing and the pair publishes
  nothing: cdn, reviews and attributes each ship that way for a reason
  recorded in the pair itself.
* A pair whose real ``nav-manifest.json`` HAS entries while the registry
  carries no mirror (or an empty one) → **FAILURE**. Those screens exist and
  no scaffolded container mounts them.
* A pair whose mirror differs from the real file (entries or version) →
  **FAILURE**.
* A pair that DOES carry a ``"nav"`` mirror but whose real
  ``nav-manifest.json`` is missing or empty → **FAILURE** (spec §3.8). This
  used to be a silent skip too: the mirror claims a nav surface that the
  package no longer publishes, and every scaffolded project keeps mounting
  routes for screens that are not there.

    python scripts/check_nav_manifest_sync.py
    SIBLING_ROOT=/path/to/workspace python scripts/check_nav_manifest_sync.py

Exit 0 = for every registered pair, mirror and real ``nav-manifest.json``
agree about whether there is a nav surface and about what is on it (as
parsed JSON — key order doesn't matter, content does), version included.
Exit 1 = at least one disagreement, printed with both sides so the fix is
obvious (update the mirror in create_project.py's FRONTEND_REACT_LIBS to
match the real file — and add its ``NAV_ENTRY_MOUNTS`` rows, which
``tests/test_public_surface.py::TestMountTable`` insists on — or drop the
mirror if the pair really did retire the surface).
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
        real = _load_real_manifest(packages, key, info["package"])
        real_published = bool((real or {}).get("entries"))
        if not mirrored_entries:
            # The blind spot this gate had: iterating the MIRRORS meant a pair
            # that grew a nav surface the registry never mirrored was not
            # compared against anything. A registered pair publishing entries
            # is a claim on the container, whether or not anyone mirrored it.
            if real_published:
                mismatches.append(
                    f"{key}: {packages / f'{key}-react' / 'nav-manifest.json'} "
                    f"publishes {len(real['entries'])} entry(ies) "
                    f"({', '.join(e.get('id', '?') for e in real['entries'])}) "
                    "and FRONTEND_REACT_LIBS carries NO \"nav\" mirror, so "
                    "every scaffolded container is missing those screens. "
                    "Mirror the entries and add their NAV_ENTRY_MOUNTS rows:\n"
                    f"  real: {json.dumps(real['entries'], indent=2, sort_keys=True)}"
                )
            continue
        if not real_published:
            # Spec §3.8: a REGISTERED pair with no real manifest is drift,
            # not an absence. The mirror is a claim about a published file.
            mismatches.append(
                f"{key}: FRONTEND_REACT_LIBS carries a \"nav\" mirror, but "
                f"{packages / f'{key}-react' / 'nav-manifest.json'} does not "
                "exist or publishes no entries. Either the pair stopped "
                "publishing a nav manifest (drop the mirror) or the checkout "
                "is stale (`pnpm gen:nav`)."
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

    # Both numbers, because the second is what the old gate could not see: a
    # green line naming only the mirrors it compared was true and useless.
    print(
        f"check_nav_manifest_sync: {checked} nav-bearing pair(s) match their "
        f"real nav-manifest.json, and the other "
        f"{len(FRONTEND_REACT_LIBS) - checked} registered pair(s) publish no "
        f"nav manifest (walked all {len(FRONTEND_REACT_LIBS)}, under "
        f"{packages})."
    )
    return 0


def main() -> None:
    sys.exit(check())


if __name__ == "__main__":
    main()
