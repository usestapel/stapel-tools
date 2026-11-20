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

* PIN003 (error, every mode): the pin is not a final ``X.Y.Z`` release the
  rendered range ``>=pin,<X.(Y+1)`` admits — the scaffold would render an
  unbounded or self-excluding requirement.

Where each verdict blocks (the release rule)
--------------------------------------------
``--strict`` runs on ``main`` before a tag: CI on every push and on the
daily schedule, and ``make release-check``. A commit whose pins were current
there is publishable. ``--publish`` runs in the tag's publish workflow: a
newer upstream that appeared between the tag and the publish is NOT the
tagged commit's defect, and refusing it strands a pushed tag that can never
be moved — so PIN002 is a notice there (a GitHub annotation, and with
``--report`` a file the workflow turns into a follow-up issue). PIN001 and
PIN003 stay errors in every mode: they make the published scaffold hand out
a requirement nothing satisfies.

Exit codes: 0 clean (or listed without ``--strict``), 1 findings under
``--strict`` or any PIN001/PIN003, 2 PyPI unreachable.
"""
from __future__ import annotations

import argparse
import json
import os
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


def _annotate(level: str, text: str) -> None:
    """A GitHub Actions annotation when running there; nothing elsewhere."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{level} title=check-pypi-pins::{text}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--strict", action="store_true",
                      help="before a tag (main CI, make release-check): a stale ceiling "
                           "(PIN002) fails the run")
    mode.add_argument("--publish", action="store_true",
                      help="in the publish workflow: PIN002 is a notice, PIN001/PIN003 fail")
    parser.add_argument("--report", type=Path,
                        help="with --publish: write the PIN002 notices here (markdown) "
                             "for a follow-up issue; the file is absent when there are none")
    args = parser.parse_args(argv)

    from stapel_tools.create_project import STAPEL_LIBS, _pin_ceiling

    errors = stale = checked = 0
    notices: list[str] = []
    for key, info in STAPEL_LIBS.items():
        if not info.get("pin") or info.get("released") is False:
            continue
        name = distribution(info)
        pin = info["pin"]
        ceiling = _pin_ceiling(pin)
        if not _RELEASE.match(pin) or ceiling is None or _v(pin)[:2] >= _v(ceiling):
            errors += 1
            line = (f"PIN003 {key}: pin {pin!r} of {name} is not a final X.Y.Z release "
                    f"inside its rendered range (ceiling {ceiling!r})")
            print(line)
            _annotate("error", line)
            continue
        try:
            released = _released_versions(name)
        except ConnectionError as exc:
            print(f"check-pypi-pins: PyPI unreachable ({exc})", file=sys.stderr)
            return 2
        checked += 1
        verdict, newest = classify(pin, released)
        if verdict == "missing" and not info.get("ahead_of_pypi"):
            errors += 1
            line = f"PIN001 {key}: pin {pin} of {name} is not on PyPI (newest {newest})"
            print(line)
            _annotate("error", line)
        elif verdict == "stale":
            stale += 1
            line = (f"PIN002 {key}: {name} {newest} is on PyPI, the scaffold renders "
                    f">={pin},<{ceiling} — move the registry pin")
            print(line)
            notices.append(line)
            _annotate("error" if args.strict else "warning", line)
        elif verdict == "patch":
            print(f"note   {key}: {name} {newest} (inside the ceiling of {pin})")
    print(f"check-pypi-pins: {checked} pins checked, {errors} not publishable (PIN001/PIN003), "
          f"{stale} stale ceiling(s)")
    if args.publish and notices:
        print("check-pypi-pins: --publish: the stale ceilings above are a NOTICE here — they "
              "appeared after the tagged commit passed --strict on main; move the pins in the "
              "next release.")
        if args.report:
            args.report.write_text(
                "Newer registry releases appeared after this tag passed the pin gate on `main`;\n"
                "move the pins in the next release (`make pins-check`, then `make release-check`).\n\n"
                + "".join(f"- {line}\n" for line in notices),
                encoding="utf-8",
            )
    if errors or (args.strict and stale):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
