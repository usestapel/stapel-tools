#!/usr/bin/env python3
"""The scaffold's pinned `@stapel/*` substrate satisfies every pair's PEER ranges.

Why this exists
----------------
The 0.55.5 CI run died in ``e2e-generated-project`` on an ERESOLVE, not on a
missing version::

    npm error Could not resolve dependency:
    npm error peer @stapel/core@">=0.18.1 <1.0.0" from @stapel/auth-react@0.17.1

Every pin in the scaffold existed on npm, so ``scripts/e2e_npm_pins.py`` — the
gate over "is this version published?" — was green and had nothing to say. The
17 pair pins had been raised to the 2026-08 release; the SUBSTRATE they stand
on had not (``@stapel/core`` 0.17.0, ``@stapel/tokens-antd`` 0.5.0). A pair's
peer floor is raised by the PAIR's release, in another repo, with no commit
here at all — which is why this cannot be a code-review rule and has to be a
gate that asks the registry.

So this is the second half of the pin story, and the two halves are different
questions:

* ``e2e_npm_pins.py``     — does the pinned version EXIST on npm?
* ``check_npm_peer_graph.py`` — do the pinned versions AGREE with each other?

What it does
------------
1. Collects every dependency the scaffold pins, from the generator's own
   constants (never a copy typed out here): the pair table, the support/antd/
   router/shell/image pins, and the two ``package.json`` dev-dep tables. One
   package may be declared by more than one source with different ranges (the
   minimal template and the public-storefront template each carry their own
   dev deps); each declaration is a real project's install and is checked.
2. Resolves each declaration against the registry exactly as ``npm install``
   would (``npm view "<pkg>@<range>" version`` — newest published match).
3. Reads the resolved ``@stapel/*`` version's ``peerDependencies`` and, for
   every peer the scaffold ALSO pins, asserts the version npm would install
   satisfies the peer range. That is the FAILING rule — it is the one this
   repo can always fix, by moving a pin.
4. A peer the scaffold does not pin is npm's job, with one exception worth
   printing: a REQUIRED peer (not marked ``optional`` in the pair's
   ``peerDependenciesMeta``) that the registry cannot resolve AT ALL. npm
   installs missing peers, so an unpublished required peer is a hard E404 for
   every project that selects the pair — ``@stapel/chat-react@0.4.0`` peers
   ``@stapel/realtime`` and does not mark it optional, while its two siblings
   that peer the same package do. That is a publish-order fact in ANOTHER
   repo, not a pin this scaffold can move (there is no version to pin), so it
   is LISTED by name rather than failed — the same call ``e2e_npm_pins.py``
   makes about a mirror ahead of npm.

Range satisfaction is implemented here (:func:`satisfies`) rather than shelled
out to node, because this repo has no node dependency and the forms the fleet
publishes are a small, closed set: ``>=a.b.c <x.y.z``, ``^a.b.c``, ``~a.b.c``,
exact versions, partials (``>=19``, ``<7``), ``*`` and ``||`` alternatives. An
UNRECOGNISED form raises — a gate that silently passes what it cannot parse is
the failure mode this whole file exists to end.

Exit codes: 0 (every peer edge holds), 1 (at least one violation, or a pin
that resolves to nothing), 2 (usage, or npm unreachable / unparsable range).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ─────────────────────────── semver, the subset npm uses ───────────────────

class RangeSyntaxError(ValueError):
    """A range form this gate does not implement. Never silently a pass."""


def parse_version(text: str) -> tuple:
    """``"1.2.3-rc.1"`` -> ``((1, 2, 3), ("rc", 1))``. Build metadata ignored.

    Returns ``(core, prerelease)``; ``prerelease`` is ``()`` for a release.
    """
    text = text.strip().lstrip("v=").strip()
    text = text.split("+", 1)[0]
    core, _, pre = text.partition("-")
    parts = core.split(".")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise RangeSyntaxError(f"not a version: {text!r}")
    numbers = tuple(int(p) for p in parts) + (0,) * (3 - len(parts))
    identifiers = tuple(
        int(chunk) if chunk.isdigit() else chunk
        for chunk in (pre.split(".") if pre else [])
    )
    return numbers, identifiers


def _compare_prerelease(left: tuple, right: tuple) -> int:
    """semver §11.4 — numeric identifiers < alphanumeric, fewer fields first."""
    if not left and not right:
        return 0
    if not left:
        return 1  # a release outranks any prerelease of the same core
    if not right:
        return -1
    for a, b in zip(left, right):
        if a == b:
            continue
        if isinstance(a, int) and isinstance(b, int):
            return -1 if a < b else 1
        if isinstance(a, int):
            return -1
        if isinstance(b, int):
            return 1
        return -1 if str(a) < str(b) else 1
    return (len(left) > len(right)) - (len(left) < len(right))


def compare(left: str, right: str) -> int:
    """-1 / 0 / 1, semver precedence."""
    lc, lp = parse_version(left)
    rc, rp = parse_version(right)
    if lc != rc:
        return -1 if lc < rc else 1
    return _compare_prerelease(lp, rp)


def _partial(text: str) -> "tuple[list, bool, str]":
    """``(numbers, is_partial, prerelease)`` — ``"1.2"`` -> ``([1, 2], True, "")``,
    ``"1.2.3"`` -> ``([1, 2, 3], False, "")``, ``"1.x"`` -> ``([1], True, "")``."""
    text = text.strip().lstrip("v=").strip().split("+", 1)[0]
    core, _, pre = text.partition("-")
    numbers = []
    wildcard = False
    for chunk in core.split("."):
        if chunk in ("", "*", "x", "X"):
            wildcard = True
            break
        if not chunk.isdigit():
            raise RangeSyntaxError(f"not a version part: {text!r}")
        numbers.append(int(chunk))
    return numbers, (wildcard or len(numbers) < 3), pre


def _bounds(operator: str, operand: str) -> list:
    """One comparator -> a list of ``(op, version)`` pairs with FULL versions.

    npm's own expansion: ``^`` and ``~`` and every partial become a
    ``>=`` / ``<`` pair, so the matcher below only ever handles four operators.
    """
    numbers, partial, pre = _partial(operand)
    if not numbers:  # `*`, `x`, or an empty operand — any version
        return []
    floor = (numbers + [0, 0, 0])[:3]
    floor_text = ".".join(str(n) for n in floor) + (f"-{pre}" if pre else "")

    def ceiling(bump_index: int) -> str:
        capped = list(floor)
        capped[bump_index] += 1
        for i in range(bump_index + 1, 3):
            capped[i] = 0
        # `-0` is the lowest possible prerelease: `<2.0.0-0` excludes 2.0.0's
        # own prereleases too, which is what npm means by "the next major".
        return ".".join(str(n) for n in capped) + "-0"

    if operator == "^":
        # npm: bump the LEFTMOST NON-ZERO component — ^1.2.3 <2.0.0,
        # ^0.18.1 <0.19.0 (a 0.x minor is a major), ^0.0.3 <0.0.4. All-zero
        # falls back to the last component actually written (^0.0 -> <0.1.0).
        for index in (0, 1, 2):
            if floor[index]:
                return [(">=", floor_text), ("<", ceiling(index))]
        return [(">=", floor_text), ("<", ceiling(min(len(numbers), 3) - 1))]
    if operator == "~":
        if len(numbers) == 1:
            return [(">=", floor_text), ("<", ceiling(0))]
        return [(">=", floor_text), ("<", ceiling(1))]
    if operator in (">=", ">", "<=", "<"):
        # A partial in an inequality means the zero-filled version: `>=19` is
        # `>=19.0.0`, `<7` is `<7.0.0`.
        return [(operator, floor_text)]
    if operator in ("", "="):
        if not partial:
            return [("=", floor_text)]
        # `1.2` is an X-range: >=1.2.0 <1.3.0
        return [(">=", floor_text), ("<", ceiling(len(numbers) - 1))]
    raise RangeSyntaxError(f"unsupported operator {operator!r}")


def _comparators(part: str) -> list:
    """Split one range part (``">=0.18.1 <1.0.0"``) into expanded bounds."""
    if " - " in part:
        raise RangeSyntaxError(f"hyphen ranges are not implemented: {part!r}")
    bounds = []
    for token in part.split():
        for prefix in (">=", "<=", "^", "~", ">", "<", "="):
            if token.startswith(prefix):
                bounds.extend(_bounds(prefix, token[len(prefix):]))
                break
        else:
            bounds.extend(_bounds("", token))
    return bounds


def satisfies(version: str, spec: str) -> bool:
    """Does ``version`` satisfy the npm range ``spec``?

    Implements the forms this fleet publishes; anything else raises
    :class:`RangeSyntaxError` rather than guessing.
    """
    spec = (spec or "").strip()
    if spec in ("", "*", "x", "X", "latest"):
        return True
    if spec.startswith(("npm:", "file:", "link:", "git+", "http")):
        raise RangeSyntaxError(f"non-registry spec: {spec!r}")
    _core, prerelease = parse_version(version)
    for part in spec.split("||"):
        part = part.strip()
        if not part:
            continue
        bounds = _comparators(part)
        if not bounds:
            return True
        if not all(_holds(version, op, target) for op, target in bounds):
            continue
        if prerelease and not _allows_prerelease(version, bounds):
            continue
        return True
    return False


def _holds(version: str, operator: str, target: str) -> bool:
    order = compare(version, target)
    return {
        ">=": order >= 0, ">": order > 0,
        "<=": order <= 0, "<": order < 0,
        "=": order == 0,
    }[operator]


def _allows_prerelease(version: str, bounds: list) -> bool:
    """npm: a prerelease only matches a range that names a prerelease of the
    SAME ``major.minor.patch`` — otherwise ``>=1.0.0`` would quietly accept
    ``2.0.0-alpha``."""
    core, _ = parse_version(version)
    for _op, target in bounds:
        target_core, target_pre = parse_version(target)
        if target_pre and target_core == core:
            return True
    return False


# ────────────────────────────── the registry ───────────────────────────────

def _npm(args: list) -> "tuple[int, str]":
    proc = subprocess.run(["npm", *args], capture_output=True, text=True, check=False)
    return proc.returncode, (proc.stdout or "").strip()


class Registry:
    """``npm view``, memoised — one process per (package, spec) at most."""

    def __init__(self, runner=None):
        self._run = runner or _npm
        self._cache: dict = {}

    def resolve(self, package: str, spec: str) -> "tuple[str, dict, dict]":
        """``(version npm would install, peerDependencies, peerDependenciesMeta)``.

        ``("", {}, {})`` when the range matches nothing published.
        """
        key = (package, spec)
        if key in self._cache:
            return self._cache[key]
        code, out = self._run([
            "view", f"{package}@{spec}",
            "version", "peerDependencies", "peerDependenciesMeta", "--json",
        ])
        result = ("", {}, {})
        if code == 0 and out:
            try:
                payload = json.loads(out)
            except ValueError:
                payload = None
            if isinstance(payload, list):
                payload = payload[-1] if payload else None
            if isinstance(payload, str):  # single field, no peers published
                payload = {"version": payload}
            if isinstance(payload, dict) and payload.get("version"):
                result = (
                    payload["version"],
                    payload.get("peerDependencies") or {},
                    payload.get("peerDependenciesMeta") or {},
                )
        self._cache[key] = result
        return result


# ─────────────────────── what the scaffold actually pins ───────────────────

def declarations() -> list:
    """``[(package, spec, source)]`` — every dependency the generator writes.

    Read from the generator's constants, so a pin raised in one place and not
    the other is visible here rather than in a user's ``npm install``.
    """
    from stapel_tools import _frontend_templates as templates
    from stapel_tools import create_project as gen

    found: list = []

    def add(package: str, spec: str, source: str) -> None:
        found.append((package, spec, source))

    for key, entry in gen.FRONTEND_REACT_LIBS.items():
        add(entry["package"], f"^{entry['version']}", f"FRONTEND_REACT_LIBS[{key!r}]")
    for name, version in gen.FRONTEND_REACT_CORE_DEPS.items():
        add(name, f"^{version}", "FRONTEND_REACT_CORE_DEPS")
    for name, version in gen.FRONTEND_REACT_ANTD_DEPS.items():
        add(name, f"^{version}", "FRONTEND_REACT_ANTD_DEPS")
    for name, version in gen.FRONTEND_ROUTER_DEPS.items():
        add(name, f"^{version}", "FRONTEND_ROUTER_DEPS")
    add(gen.FRONTEND_SHELL_REACT_PACKAGE, f"^{gen.FRONTEND_SHELL_REACT_VERSION}",
        "FRONTEND_SHELL_REACT_VERSION")
    add(gen.FRONTEND_IMAGE_PACKAGE, f"^{gen.FRONTEND_IMAGE_VERSION}",
        "FRONTEND_IMAGE_VERSION")

    template = json.loads(templates.PACKAGE_JSON.replace("{{SLUG}}", "app"))
    for section in ("dependencies", "devDependencies"):
        for name, spec in (template.get(section) or {}).items():
            add(name, spec, f"PACKAGE_JSON.{section}")
    for name, spec in templates.PUBLIC_DEV_DEPS.items():
        add(name, spec, "PUBLIC_DEV_DEPS")
    return found


def pinned_specs(found: list) -> dict:
    """``{package: {spec: [sources]}}`` — every distinct range per package."""
    table: dict = {}
    for package, spec, source in found:
        table.setdefault(package, {}).setdefault(spec, []).append(source)
    return table


# ───────────────────────────────── the gate ────────────────────────────────

def check(registry: Registry, found: list | None = None) -> "tuple[list, list, list, list]":
    """``(violations, unresolved, unpinned_peers, unpublished_peers)``.

    ``violations``        [(subject, subject_version, peer, peer_range,
                           peer_spec, peer_version, sources)] — the failing rule.
    ``unresolved``        [(package, spec, sources)] — nothing published matches.
    ``unpinned_peers``    [(subject, peer, range)] — npm picks one; informational.
    ``unpublished_peers`` [(subject, peer, range)] — REQUIRED and not on npm at
                          all, i.e. every project selecting that pair gets an
                          E404. Listed, not failed: no pin here can fix it.
    """
    found = declarations() if found is None else found
    table = pinned_specs(found)

    resolved: dict = {}
    unresolved: list = []
    for package, specs in sorted(table.items()):
        for spec, sources in sorted(specs.items()):
            version, peers, meta = registry.resolve(package, spec)
            if not version:
                unresolved.append((package, spec, sources))
                continue
            resolved[(package, spec)] = (version, peers, meta)

    violations: list = []
    unpinned: list = []
    unpublished: list = []
    for (package, spec), (version, peers, meta) in sorted(resolved.items()):
        if not package.startswith("@stapel/"):
            continue  # third-party peer tables are not this fleet's contract
        for peer, peer_range in sorted(peers.items()):
            peer_specs = table.get(peer)
            if not peer_specs:
                subject = f"{package}@{version}"
                optional = bool((meta.get(peer) or {}).get("optional"))
                if not optional and not registry.resolve(peer, peer_range)[0]:
                    unpublished.append((subject, peer, peer_range))
                else:
                    unpinned.append((subject, peer, peer_range))
                continue
            for peer_spec in sorted(peer_specs):
                entry = resolved.get((peer, peer_spec))
                if entry is None:
                    continue  # already reported as unresolved
                peer_version = entry[0]
                if satisfies(peer_version, peer_range):
                    continue
                violations.append((
                    package, version, peer, peer_range, peer_spec, peer_version,
                    peer_specs[peer_spec],
                ))
    return violations, unresolved, unpinned, unpublished


def report(violations: list, unresolved: list, unpinned: list,
           unpublished: list, *, verbose: bool) -> None:
    for package, spec, sources in unresolved:
        print(
            f"check_npm_peer_graph: {package}@{spec} resolves to NOTHING on npm "
            f"({', '.join(sources)}) — see scripts/e2e_npm_pins.py",
            file=sys.stderr,
        )
    for subject, version, peer, peer_range, peer_spec, peer_version, sources in violations:
        print(
            f"check_npm_peer_graph: {subject}@{version} needs "
            f'{peer}@"{peer_range}" — the scaffold pins {peer_spec} '
            f"(npm resolves it to {peer_version}) in {', '.join(sources)}",
            file=sys.stderr,
        )
    if violations:
        print(
            "\nRaise the pin to a published version inside the peer range "
            "(`npm view <pkg> version` — the pin is the command's output). "
            "An `npm install` of a generated project fails with ERESOLVE "
            "until this holds.",
            file=sys.stderr,
        )
    if unpublished:
        print(
            "check_npm_peer_graph: REQUIRED PEERS THAT ARE NOT ON NPM — every "
            "project selecting these pairs fails `npm install` with E404:",
            file=sys.stderr,
        )
        for subject, peer, peer_range in unpublished:
            print(f'  {subject} needs {peer}@"{peer_range}" — npm has no such package',
                  file=sys.stderr)
        print(
            "  No pin here can fix this: there is no published version to pin. "
            "It is fixed in the PAIR's repo, by publishing the peer or by "
            "marking it optional in peerDependenciesMeta the way the pair's "
            "siblings already do. Listed rather than failed for the same "
            "reason e2e_npm_pins.py lists a mirror ahead of npm — a "
            "release-order fact in another repo is not a defect of this pin "
            "table.",
            file=sys.stderr,
        )
    if verbose:
        for subject, peer, peer_range in unpinned:
            print(f"  (not pinned by the scaffold; npm resolves it) {subject} -> {peer}@{peer_range}")
    if not violations and not unresolved:
        print("check_npm_peer_graph: every pinned @stapel/* peer range is satisfied")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_npm_peer_graph.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="also list peers the scaffold does not pin (npm resolves those)",
    )
    args = parser.parse_args(argv)

    code, _out = _npm(["--version"])
    if code != 0:
        print("check_npm_peer_graph: npm is not runnable here", file=sys.stderr)
        return 2

    try:
        violations, unresolved, unpinned, unpublished = check(Registry())
    except RangeSyntaxError as exc:
        print(f"check_npm_peer_graph: {exc}", file=sys.stderr)
        return 2
    report(violations, unresolved, unpinned, unpublished, verbose=args.verbose)
    return 1 if (violations or unresolved) else 0


if __name__ == "__main__":
    sys.exit(main())
