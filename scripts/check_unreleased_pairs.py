#!/usr/bin/env python3
"""A React pair marked unreleased is not on npm — or this fails.

A binding with ``released=False`` (``composition/react/bindings.py``) is
written and gated but not installed: the scaffold names it pending. The day
npm serves the package, that one fact is stale and every generated project
keeps going without the pair. This asks the registry (``npm view <pkg>
version``) for each such package and fails, naming the flip, when it is
published.

Exit codes: 0 (every unreleased pair is still unpublished), 1 (at least one
is published), 2 (npm unreachable).
"""
from __future__ import annotations

import subprocess
import sys


def main() -> int:
    from stapel_tools.composition.react.bindings import REACT_BINDINGS

    stale = []
    for key, binding in REACT_BINDINGS.items():
        if binding.released:
            continue
        try:
            proc = subprocess.run(
                ["npm", "view", binding.package, "version"],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"npm unreachable: {exc}", file=sys.stderr)
            return 2
        if proc.returncode == 0 and proc.stdout.strip():
            stale.append((key, binding.package, proc.stdout.strip()))
        elif "E404" not in proc.stderr and "404" not in proc.stderr:
            print(f"npm view {binding.package}: {proc.stderr.strip()}", file=sys.stderr)
            return 2
        else:
            print(f"ok: {binding.package} is not on npm (binding {key!r} stays pending)")
    for key, package, version in stale:
        print(
            f"STALE: {package} {version} is on npm and binding {key!r} says released=False — "
            "drop `released=False`, set `version` to the published one, run "
            "`make golden-update`", file=sys.stderr)
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
