"""One project-owned backend module's contract, for the mobile app's codegen.

Run inside the project's Django (``manage.py shell -c`` — the generated
``mobile/Makefile`` target ``contracts`` does it): emits the OpenAPI document
in contract profile 2 (the profile the stapel-kmp generator reads), keeps only
the paths under the module's mount prefix, prunes the components to what
those paths reference, and writes ``<out>/schema.json`` plus ``errors.json``.
The installed bricks' endpoints are left out on purpose: their clients ship
inside the stapel-kmp brick libraries.

A module with no endpoint yet writes nothing and says so; the mobile build
skips a module without ``schema.json`` (listed, never half-wired).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REF = "#/components/"


def _refs(node, out: set[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(_REF):
            out.add(ref[len(_REF):])
        for value in node.values():
            _refs(value, out)
    elif isinstance(node, list):
        for value in node:
            _refs(value, out)


def slice_schema(schema: dict, prefix: str, exclude: tuple[str, ...] = ()) -> dict:
    """*schema* restricted to the paths under *prefix* (minus *exclude*), components pruned."""
    paths = {p: v for p, v in schema.get("paths", {}).items()
             if p.startswith(prefix) and not any(p.startswith(x) for x in exclude)}
    components = schema.get("components", {})
    wanted: set[str] = set()
    _refs(paths, wanted)
    # security schemes are referenced by name, not by $ref
    keep: dict[str, set[str]] = {"securitySchemes": set(components.get("securitySchemes", {}))}
    pending = list(wanted)
    while pending:
        ref = pending.pop()
        kind, _, name = ref.partition("/")
        if name in keep.setdefault(kind, set()):
            continue
        keep[kind].add(name)
        found: set[str] = set()
        _refs(components.get(kind, {}).get(name, {}), found)
        pending.extend(found - {f"{k}/{n}" for k, names in keep.items() for n in names})
    out = {k: v for k, v in schema.items() if k not in ("paths", "components")}
    out["paths"] = paths
    out["components"] = {
        kind: {name: body for name, body in entries.items() if name in keep.get(kind, set())}
        for kind, entries in components.items()
        if keep.get(kind)
    }
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--module", required=True)
    p.add_argument("--prefix", required=True, help="the module's mount, e.g. /api/")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--exclude", action="append", default=[],
                   help="a path prefix under --prefix that is not the module's (repeatable)")
    args = p.parse_args(argv)

    import tempfile

    from ...codegen import _stable_json, emit_errors, emit_schema

    with tempfile.TemporaryDirectory() as tmp:
        full = Path(tmp) / "schema.json"
        emit_schema(full, contract_profile=2)
        schema = json.loads(full.read_text(encoding="utf-8"))
        sliced = slice_schema(schema, args.prefix, tuple(args.exclude))
        if not sliced["paths"]:
            print(f"contracts: {args.module} has no endpoint under {args.prefix} yet — "
                  "nothing written; the mobile build skips it", file=sys.stderr)
            return 0
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "schema.json").write_text(_stable_json(sliced), encoding="utf-8")
        emit_errors(args.out / "errors.json")
    print(f"contracts: {args.module}: {len(sliced['paths'])} path(s) -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
