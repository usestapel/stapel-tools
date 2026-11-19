"""Export the neutral registry for non-Python consumers.

``stapel_tools/data/composition-registry.json`` is what stapel-kmp's parity
task (``checkNavParity``) and Studio read: every brick's neutral nav entries
and embeds, the container roots, the composites, the icon and slot
vocabularies, and per platform which ids that platform delivers. It is
generated from the registry and committed; ``--check`` (wired into
``make check``) fails when the committed file differs from what the code
exports.

    python -m stapel_tools.composition.export           # rewrite
    python -m stapel_tools.composition.export --check   # drift gate
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .model import EmbedSpec, NavEntrySpec

#: Bumped when a consumer would have to change how it reads the file.
FORMAT_VERSION = 1

DATA = Path(__file__).resolve().parents[1] / "data"
OUT = DATA / "composition-registry.json"
ROUTE_PLANS = DATA / "route-plans"

#: Selections the shared route-plan fixtures cover: name -> (modules, auth wired).
#: A platform's own nav resolver (stapel-kmp's ``resolveNav``) is tested
#: against these files, so both platforms route one selection the same way.
ROUTE_PLAN_CASES: dict[str, tuple[tuple[str, ...], bool]] = {
    "auth": (("auth",), True),
    "auth-unwired": (("auth", "profiles"), False),
    "auth-profiles": (("auth", "profiles"), True),
    "auth-notifications-workspaces": (("auth", "notifications", "workspaces"), True),
    "shop": (("auth", "attributes", "categories", "cdn", "currencies", "listings",
              "profiles", "reviews", "search", "vocabularies"), True),
    "gdpr-video-admin": (("auth", "gdpr", "video"), True),
    "every-pair": ((), True),  # filled with every registered brick at run time
}


def _nav(entry: NavEntrySpec) -> dict:
    out = {
        "id": entry.id,
        "labelKey": entry.label_key,
        "icon": entry.icon,
        "path": entry.path,
        "level": entry.level,
        "parentId": entry.parent_id,
        "menuVisibleDefault": entry.menu_visible_default,
        "requiresAuth": entry.requires_auth,
        "surface": entry.surface,
        "order": entry.order,
    }
    if entry.short_label_key is not None:
        out["shortLabelKey"] = entry.short_label_key
    return out


def _embed(embed: EmbedSpec) -> dict:
    out: dict = {"id": embed.id, "slot": embed.slot, "order": embed.order,
                 "surface": embed.surface}
    if embed.opens is not None:
        out["opens"] = embed.opens
    if embed.scope_nav_entry is not None:
        out["scopeNavEntry"] = embed.scope_nav_entry
    if embed.route_props:
        out["routeProps"] = dict(embed.route_props)
    if embed.breakpoints is not None:
        out["breakpoints"] = list(embed.breakpoints)
    if embed.props:
        out["props"] = dict(embed.props)
    return out


def registry_document() -> dict:
    from .._shell_templates import SHELL_SLOT_IDS
    from ..create_project import FRONTEND_COMPOSITES
    from .bricks import BRICK_UI, CONTAINER_PAGES, CONTAINER_ROOTS
    from .icons import NAV_ICONS
    from .kmp.registry import kmp_bindings
    from .react.bindings import REACT_BINDINGS

    bricks: dict[str, dict] = {}
    for key in sorted(set(BRICK_UI) | set(REACT_BINDINGS)):
        brick = BRICK_UI.get(key)
        doc: dict = {
            "nav": [_nav(e) for e in brick.nav] if brick else [],
            "embeds": [_embed(e) for e in brick.embeds] if brick else [],
        }
        if brick and brick.page_slots:
            doc["pageSlots"] = {k: list(v) for k, v in brick.page_slots}
        if brick and brick.badges:
            doc["badges"] = {k: v for k, v in brick.badges}
        platforms: dict = {}
        binding = REACT_BINDINGS.get(key)
        if binding is not None:
            platforms["react"] = {
                "package": binding.package,
                "version": binding.version,
                "released": binding.released,
                "entries": list(binding.components),
                "embeds": list(binding.embed_components),
            }
        kmp = kmp_bindings().get(key)
        if kmp is not None and kmp.ui_artifact and kmp.descriptor_present:
            platforms["kmp"] = {
                "ui": kmp.ui_artifact,
                "version": kmp.version,
                "released": kmp.released,
                "entries": list(kmp.entries),
                "embeds": list(kmp.embeds),
            }
        doc["platforms"] = platforms
        bricks[key] = doc
    return {
        "version": FORMAT_VERSION,
        "icons": list(NAV_ICONS),
        "slots": list(SHELL_SLOT_IDS),
        "containerRoots": [
            {"id": r.id, "labelKey": r.label_key, "icon": r.icon, "path": r.path,
             "order": r.order, "requiresAuth": r.requires_auth, "surface": r.surface}
            for r in CONTAINER_ROOTS.values()
        ],
        "containerPages": {
            p.entry_id: {"requires": list(p.requires), "optional": list(p.optional)}
            for p in CONTAINER_PAGES.values()
        },
        "bricks": bricks,
        "composites": {
            name: {"backend": spec["backend"], "bricks": list(spec["pairs"])}
            for name, spec in FRONTEND_COMPOSITES.items()
        },
    }


def route_plan_document(name: str) -> dict:
    """One shared route-plan fixture: the neutral route plan of a selection."""
    from ..create_project import FRONTEND_REACT_LIBS
    from .nav import build_nav_route_plan, nav_wired_pairs
    from .react.registry import react_entries

    modules, auth_wired = ROUTE_PLAN_CASES[name]
    if not modules:
        modules = tuple(k for k, v in FRONTEND_REACT_LIBS.items() if v.get("released", True))
    pairs = nav_wired_pairs(react_entries(modules), auth_wired=auth_wired)
    plan = build_nav_route_plan(pairs) if pairs else {
        "absolute_routes": [], "app_children": [], "container_roots": []}

    def route(r: dict) -> dict:
        entry = r["entry"]
        return {"id": entry["id"], "path": r["path"],
                "brick": entry.get("_brick"), "containerRoot": "_local" in entry}

    return {
        "version": FORMAT_VERSION,
        "modules": list(modules),
        "authWired": auth_wired,
        "absoluteRoutes": [route(r) for r in plan["absolute_routes"]],
        "appChildren": [route(r) for r in plan["app_children"]],
        "containerRoots": list(plan["container_roots"]),
    }


def outputs() -> dict[Path, str]:
    """Every exported file -> its content."""
    files = {OUT: render()}
    for name in ROUTE_PLAN_CASES:
        files[ROUTE_PLANS / f"{name}.json"] = (
            json.dumps(route_plan_document(name), indent=2, ensure_ascii=False) + "\n")
    return files


def render() -> str:
    return json.dumps(registry_document(), indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m stapel_tools.composition.export")
    p.add_argument("--check", action="store_true",
                   help="exit 1 when the committed file differs from the export")
    args = p.parse_args(argv)
    files = outputs()
    if args.check:
        stale = [path for path, text in files.items()
                 if not path.exists() or path.read_text(encoding="utf-8") != text]
        known = set(files)
        extra = sorted(p for p in ROUTE_PLANS.glob("*.json") if p not in known)
        for path in stale:
            print(f"{path}: differs from the registry export")
        for path in extra:
            print(f"{path}: no route-plan case produces it — delete it")
        if stale or extra:
            print("run `python -m stapel_tools.composition.export` and commit the result.")
            return 1
        doc = json.loads(files[OUT])
        print(f"composition registry export: in sync ({len(doc['bricks'])} bricks, "
              f"{sum(len(b['nav']) for b in doc['bricks'].values())} nav entries, "
              f"{len(ROUTE_PLAN_CASES)} route-plan fixtures)")
        return 0
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
