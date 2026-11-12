#!/usr/bin/env python3
"""Every registry pin's ceiling admits what PyPI has released.

Why this exists
---------------
``STAPEL_LIBS[key]["pin"]`` renders as ``>=<pin>,<<next minor>`` in every
generated ``requirements.txt``. The pin is hand-maintained; a library that
ships a new minor is invisible to a scaffold whose pin was not moved, so
stapel-tools 0.79.1 pinned ``stapel-listings>=0.28.0,<0.29`` with 0.29.0 on
PyPI (the registry comment still said "PyPI 0.28.0"). No neighbour held it
back; only the row did. Nothing compared the row with the index.

What it checks, per released registry row (``released`` not False), asking
PyPI's JSON API (the index, never a tag or a sibling checkout):

* PIN001 (error): the pin is not on PyPI, and the row does not say
  ``ahead_of_pypi`` — the scaffold hands out a requirement nothing satisfies;
* PIN002 (error under ``--strict``, listed otherwise): PyPI has a newer
  release OUTSIDE the rendered ceiling (a new minor or major) — a stale
  ceiling; the scaffold cannot install it;
* a newer patch inside the ceiling is printed as a note: the resolver
  already takes it.

What it cannot see: whether the newer release is COMPATIBLE with the
scaffold's templates and the other pins (that is the composite gates' job
after the bump), and a neighbour's own ceiling on the bumped package (run
``uv pip compile`` on a generated requirements.txt for that).

Exit codes: 0 clean (or listed without ``--strict``), 1 findings under
``--strict`` or any PIN001, 2 PyPI unreachable.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_RELEASE = re.compile(r"^\d+\.\d+\.\d+$")


def _v(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split("."))


def distribution(info: dict) -> str:
    return info["repo"].rstrip("/").split("/")[-1].removesuffix(".git")


def classify(pin: str, released: list[str]) -> tuple[str, str | None]:
    """``(verdict, newest)`` for one pin against the index's release list.

    verdict: ``missing`` (pin not released), ``stale`` (a newer release outside
    ``>=pin,<next minor``), ``patch`` (newer, inside), ``current``."""
    finals = sorted((r for r in released if _RELEASE.match(r)), key=_v)
    if pin not in finals:
        return "missing", finals[-1] if finals else None
    newest = finals[-1]
    if _v(newest)[:2] != _v(pin)[:2] and _v(newest) > _v(pin):
        return "stale", newest
    if _v(newest) > _v(pin):
        return "patch", newest
    return "current", newest


def _released_versions(name: str, attempts: int = 5) -> list[str]:
    url = f"https://pypi.org/pypi/{name}/json"
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                data = json.load(resp)
            return [
                ver for ver, files in data["releases"].items()
                if files and not all(f.get("yanked") for f in files)
            ]
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            last = exc
        except (urllib.error.URLError, OSError) as exc:
            last = exc
        time.sleep(2 * (attempt + 1))
    raise ConnectionError(f"{url}: {last}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--strict", action="store_true",
                        help="a stale ceiling (PIN002) fails the run")
    args = parser.parse_args(argv)

    from stapel_tools.create_project import STAPEL_LIBS

    errors = stale = checked = 0
    for key, info in STAPEL_LIBS.items():
        if not info.get("pin") or info.get("released") is False:
            continue
        name = distribution(info)
        try:
            released = _released_versions(name)
        except ConnectionError as exc:
            print(f"check-pypi-pins: PyPI unreachable ({exc})", file=sys.stderr)
            return 2
        checked += 1
        verdict, newest = classify(info["pin"], released)
        if verdict == "missing" and not info.get("ahead_of_pypi"):
            errors += 1
            print(f"PIN001 {key}: pin {info['pin']} of {name} is not on PyPI (newest {newest})")
        elif verdict == "stale":
            stale += 1
            print(f"PIN002 {key}: {name} {newest} is on PyPI, the scaffold renders "
                  f">={info['pin']},<{_v(info['pin'])[0]}.{_v(info['pin'])[1] + 1} "
                  f"— move the registry pin")
        elif verdict == "patch":
            print(f"note   {key}: {name} {newest} (inside the ceiling of {info['pin']})")
    print(f"check-pypi-pins: {checked} pins checked, {errors} not on PyPI, {stale} stale ceiling(s)")
    if errors or (args.strict and stale):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
