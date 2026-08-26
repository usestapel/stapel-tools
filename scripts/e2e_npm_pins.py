#!/usr/bin/env python3
"""Pin a generated project's frontend to versions that EXIST on npm.

Why this exists
----------------
The 0.54.0 publish failed in the e2e job on

    npm error notarget No matching version found for @stapel/auth-react@^0.16.1

The pin was not wrong in any interesting way: ``FRONTEND_REACT_LIBS["auth"]
["version"]`` is a MIRROR of the sibling `stapel-react` checkout's own
`package.json`, and that checkout had 0.16.1 in its tree with the publish still
pending. npm had 0.16.0. Everything about the scaffold was correct; the release
of the frontend pair simply had not happened yet, and the e2e job — which runs
``npm install`` against the real registry — is the one consumer for which a
version that exists only in a sibling working tree does not exist at all.

Two things follow, and this script is both of them:

1. **The e2e installs what npm has.** For every dependency of the generated
   frontend, ask the registry whether anything satisfies the declared range
   (``npm view "<pkg>@<range>" version``, which is precisely the resolution
   ``npm install`` would perform). If something does, keep the range — the
   mirror is the pin, and staying on it is the point. If nothing does, fall
   back to the newest published version and rewrite that one range. The e2e
   proves the generated project BUILDS; it cannot also prove that a package
   nobody published exists.

2. **A listing gate names every mirror ahead of npm.** Falling back silently
   would turn "the pair has not shipped yet" into a fact nobody sees until the
   scaffold hands a user a `package.json` that does not resolve. So each
   fallback is printed as a line naming the package, the mirrored version and
   the published one — and ``--strict`` turns the listing into a failure for
   the release path, where a mirror ahead of npm means the publish order is
   wrong.

Exit codes: 0 (pins resolved; fallbacks listed), 1 (``--strict`` with at least
one mirror ahead of npm), 2 (usage, or npm unreachable).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

#: The dependency families this script speaks for. Everything else in the
#: generated `package.json` (react, vite, typescript) is a third-party range
#: this fleet does not mirror, so a miss there is a real defect and must NOT be
#: papered over by a fallback.
MIRRORED_PREFIXES = ("@stapel/",)

#: The keys of `package.json` that carry versions to check.
DEP_SECTIONS = ("dependencies", "devDependencies")


def _npm(args: list) -> "tuple[int, str]":
    proc = subprocess.run(
        ["npm", *args], capture_output=True, text=True, check=False,
    )
    return proc.returncode, (proc.stdout or "").strip()


def resolves(package: str, spec: str, runner=None) -> str:
    """Newest published version satisfying ``spec``, or "" if there is none.

    ``npm view "<pkg>@<range>" version`` prints nothing (exit 0) when the range
    matches no published version, and one or more lines when it does — the last
    line is the newest match. A non-zero exit means the PACKAGE is unknown,
    which is a different failure and is reported as such by the caller.
    """
    code, out = (runner or _npm)(["view", f"{package}@{spec}", "version"])
    if code != 0 or not out:
        return ""
    # `npm view` prints `@stapel/x@0.4.0 '0.4.0'` per match when several match.
    last = out.splitlines()[-1].strip()
    if " " in last:
        last = last.rsplit(" ", 1)[-1]
    return last.strip().strip("'\"")


def latest(package: str, runner=None) -> str:
    code, out = (runner or _npm)(["view", package, "version"])
    if code != 0 or not out:
        return ""
    return out.splitlines()[-1].strip().strip("'\"")


def resolve_pins(manifest: dict, runner=None) -> "tuple[dict, list, list]":
    """Return ``(rewrites, ahead, unknown)`` for one parsed ``package.json``.

    ``rewrites``  {(section, package): new_spec} — only where the declared
                  range resolves to nothing on npm.
    ``ahead``     [(package, declared_spec, published)] — the listing gate.
    ``unknown``   [package] — npm has never heard of it (a typo, or a package
                  that was never published under this name at all).
    """
    rewrites: dict = {}
    ahead: list = []
    unknown: list = []
    for section in DEP_SECTIONS:
        deps = manifest.get(section) or {}
        for package in sorted(deps):
            spec = deps[package]
            if not isinstance(spec, str) or not spec:
                continue
            if not package.startswith(MIRRORED_PREFIXES):
                continue
            if resolves(package, spec, runner):
                continue
            published = latest(package, runner)
            if not published:
                unknown.append(package)
                continue
            rewrites[(section, package)] = f"^{published}"
            ahead.append((package, spec, published))
    return rewrites, ahead, unknown


def apply_pins(path: Path, runner=None) -> "tuple[list, list]":
    """Resolve and REWRITE ``path`` in place. Returns ``(ahead, unknown)``."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    rewrites, ahead, unknown = resolve_pins(manifest, runner)
    for (section, package), spec in rewrites.items():
        manifest[section][package] = spec
    if rewrites:
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return ahead, unknown


def report(ahead: list, unknown: list) -> None:
    if ahead:
        print("e2e_npm_pins: MIRRORS AHEAD OF NPM — the e2e installs what is published:")
        for package, spec, published in ahead:
            print(f"  {package}: mirror {spec}, npm has {published} -> installing ^{published}")
        print(
            "  Each line is a pair whose stapel-react publish has not happened "
            "yet. That is a release-ORDER fact, not a scaffold defect: the pin "
            "stays as it is, and the generated project will resolve it the "
            "moment the pair ships."
        )
    else:
        print("e2e_npm_pins: every mirrored pin resolves on npm")
    for package in unknown:
        print(f"e2e_npm_pins: npm does not know {package} at all — check the name", file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="e2e_npm_pins.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("package_json", help="the generated frontend's package.json")
    parser.add_argument(
        "--strict", action="store_true",
        help="exit 1 when any mirrored pin is ahead of npm (the release path)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="report only; do not rewrite the file",
    )
    args = parser.parse_args(argv)

    path = Path(args.package_json)
    if not path.is_file():
        print(f"e2e_npm_pins: no such file: {path}", file=sys.stderr)
        return 2

    if args.check:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        _, ahead, unknown = resolve_pins(manifest)
    else:
        ahead, unknown = apply_pins(path)

    report(ahead, unknown)
    if unknown:
        return 2
    if ahead and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
