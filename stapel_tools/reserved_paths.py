"""stapel-reserved-paths — regenerate/verify a generated project's
``reserved-paths.json``.

Schema (agreed with ``@stapel/eslint-plugin``'s ``no-reserved-backend-route``
rule — stapel-react/packages/eslint-plugin/lib/data.js
``loadReservedPathCatalog`` + that package's own README "reserved-paths.json"
section; do not change without updating both sides)::

    {"reservedPathPrefixes": ["/admin", "/staticfiles", "/media",
                              "/<mod>/api", "/<mod>/swagger", ...]}

This is the SAME projection ``create_project._write_reserved_paths_json``
writes at generation time (nginx-local/prod-nginx/Vite all render their
backend location blocks from the sibling, richer
``create_project._reserved_paths_manifest`` — this file is the flat subset
of that manifest the frontend lint rule reads). A bare module root
(``/<mod>``) must never appear — roots belong to the frontend SPA by canon
(the "/calendar page vs backend" collision this whole mechanism exists to
prevent).

``--check`` is the pre-commit drift gate (``reserved-paths-check``, wired
into a monolith's ``.pre-commit-config.yaml`` next to
``config-manifest-check``/``presenter-catalog-check``): it recovers the
module set already committed in the file (its ``admin``/``staticfiles``/
``media`` fixed entries are left alone), then re-renders each module's
sub-surfaces from stapel-tools' CURRENT definition. Drift here means
stapel-tools' own sub-surface list changed since this project was generated
or last regenerated — NOT a full lib-selection re-derivation (that is
``stapel-assemble``'s re-stamp scope; the project's actual INSTALLED_APPS
selection is not reliably recoverable from a generated project's files
across every install mode (pip vs git submodule), so this tool deliberately
does not attempt it — the same "recover from what's already committed"
discipline ``config_manifest.libs_from_existing_config_md`` uses).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RESERVED_PATHS_JSON = "reserved-paths.json"

# Framework-wide, independent of any lib selection — never touched by
# regeneration (see create_project._reserved_paths_manifest's "fixed" list).
_FIXED = ("/admin", "/staticfiles", "/media")

# Sub-surfaces our canon's generic per-service URLconf (URLS_PY) mounts under
# a lib's own prefix — see create_project._MODULE_SUB_SURFACES (the two
# constants are kept in sync by hand today; a future refactor could import
# one from the other, but stapel-tools' own create_project module carries
# import-time side effects this standalone CLI doesn't want to pull in).
_MODULE_SUB_SURFACES = ("api", "swagger", "schema.json", "admin")

# A module whose backend surface is NOT fully described by the generic
# `/<mod>/<sub>` pattern above registers the rest here, once, and both
# consumers below (the flat `reservedPathPrefixes` array and the Vite dev
# proxy, which additionally needs to know which entries want `ws: true`)
# read off the same declaration instead of drifting apart the way
# `/video/api` and a hand-added `/ws/video` did on a real storefront
# (stapel-video, LiveKit-backed, 2026-09-06).
#
# ``ws_prefixes``: this module's OWN websocket endpoint(s), mounted outside
# its `/<mod>/` namespace under the fleet's `/ws/<mod>` ASGI convention.
# ``extra_paths``: upstream paths with no module-prefix relationship at all
# — entries are either a bare string (a directory-style surface, trailing
# slash appended same as a sub-surface) or a ``{"path", "ws", "exact"}``
# dict. `"exact": True` suppresses the trailing slash (a single endpoint,
# not a subtree) and `"ws": True` marks it as a websocket target for the
# Vite proxy. stapel-video's LiveKit signalling channel is mounted at the
# fleet root (LiveKit's own protocol expects `/rtc` exactly, not a
# subtree) and is itself a websocket handshake.
_MODULE_EXTRA_SURFACES: dict[str, dict] = {
    "video": {
        "ws_prefixes": ["ws/video"],
        "extra_paths": [{"path": "rtc", "ws": True, "exact": True}],
    },
}


def _module_extra_entries(mod: str) -> list[dict]:
    """*mod*'s extra surfaces (see ``_MODULE_EXTRA_SURFACES``) normalized to
    ``{"path", "ws", "exact"}`` dicts, in declaration order."""
    extra = _MODULE_EXTRA_SURFACES.get(mod)
    if not extra:
        return []
    entries = [
        {"path": p, "ws": True, "exact": False} for p in extra.get("ws_prefixes", ())
    ]
    for item in extra.get("extra_paths", ()):
        if isinstance(item, str):
            entries.append({"path": item, "ws": False, "exact": False})
        else:
            entries.append(
                {
                    "path": item["path"],
                    "ws": bool(item.get("ws", False)),
                    "exact": bool(item.get("exact", False)),
                }
            )
    return entries


# Reverse lookup: an extra surface's own path -> the module that declared it,
# so `modules_from_existing` attributes a committed `/ws/video` or `/rtc`
# back to `video` instead of inventing bogus modules `ws`/`rtc` that would
# then explode into their own (wrong) api/swagger/schema.json/admin
# sub-surfaces on the next regenerate.
_EXTRA_SURFACE_OWNER: dict[str, str] = {
    entry["path"]: mod
    for mod in _MODULE_EXTRA_SURFACES
    for entry in _module_extra_entries(mod)
}


def modules_from_existing(prefixes: list[str]) -> list[str]:
    """The module first-segments already reserved in a committed
    ``reservedPathPrefixes`` list (fixed entries excluded), in first-seen
    order. This is the recovery step — see the module docstring for why it
    reads the committed file rather than re-deriving a project's lib
    selection from scratch.

    A path a module registered as an EXTRA surface (``/ws/video``, ``/rtc``)
    is attributed to that module rather than treated as a first-segment
    module of its own — see ``_EXTRA_SURFACE_OWNER``."""
    mods: list[str] = []
    for p in prefixes:
        if p in _FIXED:
            continue
        stripped = p.lstrip("/")
        owner = _EXTRA_SURFACE_OWNER.get(stripped)
        if owner:
            if owner not in mods:
                mods.append(owner)
            continue
        mod = stripped.split("/", 1)[0]
        if mod and mod not in mods:
            mods.append(mod)
    return mods


def reserved_prefixes_for(modules: list[str]) -> list[str]:
    """``reservedPathPrefixes`` for a module set: the framework-wide fixed
    entries plus each module's named sub-surfaces (and any extra surfaces it
    declares — see ``_MODULE_EXTRA_SURFACES``), in order, deduplicated.

    The ONE place that shape is spelled outside ``create_project`` (which
    renders nginx off its own STAPEL_LIBS-driven manifest). A bare module
    root is never emitted — roots belong to the frontend SPA by canon,
    which is what makes ``/listings/12345`` a listing page instead of a JSON
    document from the backend."""
    return [e["path"] for e in proxy_targets_for(modules)]


def proxy_targets_for(modules: list[str]) -> list[dict]:
    """Same module set as ``reserved_prefixes_for``, but keeps the
    per-entry ``ws``/``exact`` flags a flat ``reservedPathPrefixes`` array
    cannot carry — what the Vite dev proxy needs to render ``ws: true`` and
    to know which surfaces are a single endpoint rather than a subtree.
    Every entry's ``"path"`` is ``/``-leading and never trailing-slashed
    (the renderer decides that); ``reserved_prefixes_for`` is this list with
    only the ``"path"`` kept."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(path: str, *, ws: bool = False, exact: bool = False) -> None:
        if path not in seen:
            seen.add(path)
            out.append({"path": path, "ws": ws, "exact": exact})

    for f in _FIXED:
        add(f)
    for mod in modules:
        for sub in _MODULE_SUB_SURFACES:
            add(f"/{mod}/{sub}", exact=sub.endswith(".json"))
        for entry in _module_extra_entries(mod):
            add(f"/{entry['path']}", ws=entry["ws"], exact=entry["exact"])
    return out


def regenerate(prefixes: list[str]) -> list[str]:
    """Re-render ``reservedPathPrefixes`` from the module set already
    committed in *prefixes*, against stapel-tools' CURRENT sub-surface
    definition."""
    return reserved_prefixes_for(modules_from_existing(prefixes))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-reserved-paths",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "project_dir", nargs="?", default=".",
        help="Project directory (default: .) — must already have a "
             "reserved-paths.json (minimal/microservices projects don't "
             "ship one; nothing to do there).",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Do not write — exit 1 if regenerating would change the file "
             "(drift: stapel-tools' sub-surface definition changed since "
             "this project was generated/last regenerated). Exit 0 when "
             "already up to date, or when there is no reserved-paths.json.",
    )
    args = parser.parse_args(argv)

    path = Path(args.project_dir) / RESERVED_PATHS_JSON
    if not path.exists():
        print(
            "stapel-reserved-paths: no reserved-paths.json here — nothing "
            "to do (not a monolith-with-frontend project?)"
        )
        return 0

    try:
        current = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        print(f"stapel-reserved-paths: {path} is not valid JSON: {exc}", file=sys.stderr)
        return 1

    existing = current.get("reservedPathPrefixes")
    if not isinstance(existing, list):
        print(
            f"stapel-reserved-paths: {path} has no 'reservedPathPrefixes' "
            "array — malformed, refusing to guess",
            file=sys.stderr,
        )
        return 1

    rendered = json.dumps({"reservedPathPrefixes": regenerate(existing)}, indent=2) + "\n"

    if args.check:
        if path.read_text() != rendered:
            print(
                "stapel-reserved-paths: reserved-paths.json is stale — run "
                "`stapel-reserved-paths .` (no --check) and commit the result.",
                file=sys.stderr,
            )
            return 1
        print("stapel-reserved-paths: reserved-paths.json is up to date")
        return 0

    path.write_text(rendered)
    print("stapel-reserved-paths: wrote reserved-paths.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
