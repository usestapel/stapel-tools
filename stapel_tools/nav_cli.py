"""Navigation on the command line (navigation-templates spec §3.9).

* the ``--nav*`` flags and ``--composition <file>``, shared by
  ``stapel-create-project`` and the dry run;
* ``stapel-nav-plan`` — prints every platform's ``resolved`` navigation for a
  composition without writing a project (the pure planner);
* ``stapel-nav-regenerate <project>`` — folds the project's own pages
  (``stapel.app-nav.json`` + ``src/app.pages.ts``) and the root
  ``stapel.composition.json`` back into the generated route tree, nav data,
  chrome and gates. Idempotent; never touches a file the project owns.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

#: ``--composition`` keys -> ``create_project`` keyword arguments.
COMPOSITION_KEYS: dict[str, str] = {
    "name": "name", "type": "project_type", "title": "title", "url": "url",
    "companyName": "company_name", "companyEmail": "company_email",
    "modules": "modules", "auth": "want_auth", "landing": "want_landing",
    "authModel": "auth_model", "authKinds": "auth_kinds", "posture": "posture",
    "theme": "theme", "shell": "shell", "slots": "slots", "mobile": "mobile",
    "strict": "strict",
}

_BINDING_RE = re.compile(r"""^\s*["'](app\.[a-z0-9_.-]+)["']\s*:""", re.MULTILINE)


def add_nav_arguments(parser: argparse.ArgumentParser, *, mobile: bool = True,
                      composition: bool = True) -> None:
    from .composition.navigation import CARRIERS, PRESETS, SEMANTIC_SLOTS, SIZE_CLASSES

    group = parser.add_argument_group(
        "navigation (one model for every size class; see stapel.composition.json)")
    group.add_argument(
        "--nav-preset", choices=list(PRESETS), default=None,
        help="Navigation preset: " + ", ".join(PRESETS) + ". Default: by archetype "
             "(storefront composites -> shop, social -> app, otherwise dashboard).")
    group.add_argument(
        "--nav", action="append", metavar="CLASS=CARRIER", default=None,
        help=f"Carrier for a size class ({'/'.join(SIZE_CLASSES)}): "
             + ", ".join(CARRIERS) + ". Repeatable.")
    group.add_argument("--nav-header", choices=("app", "storefront", "none"), default=None,
                       help="Header kind.")
    group.add_argument("--nav-pin", metavar="ID,ID", default=None,
                       help="The destinations capped carriers show, in this order.")
    group.add_argument("--nav-hide", metavar="ID,ID", default=None,
                       help="Destinations removed from every carrier (still routed).")
    group.add_argument("--nav-order", action="append", metavar="ID=N", default=None,
                       help="Order override for a destination. Repeatable.")
    group.add_argument(
        "--nav-slot", action="append", metavar="SLOT=ID|auto|none", default=None,
        help="Semantic slot (" + ", ".join(SEMANTIC_SLOTS) + "). Repeatable.")
    group.add_argument("--nav-guest", choices=("wall", "hide"), default=None,
                       help="Member destinations for a guest: wall (shown, lead to "
                            "sign-in; default) or hide.")
    if mobile:
        group.add_argument("--mobile-nav-preset", choices=list(PRESETS), default=None,
                           help="The mobile app's preset (platforms.kmp).")
        group.add_argument("--mobile-nav", action="append", metavar="CLASS=CARRIER",
                           default=None, help="The mobile app's carrier for a size class.")
    if not composition:
        group.add_argument(
            "--strict", action="store_true", default=None,
            help="Something asked for but not rendered is an error, not a note.")
        return
    group.add_argument(
        "--composition", type=Path, metavar="FILE", default=None,
        help="Read the whole composition (the navigation block included) from JSON: a "
             "project's stapel.composition.json or a flat {name, type, modules, ..., "
             "navigation}. Flags given on top win.")
    group.add_argument(
        "--strict", action="store_true", default=None,
        help="Something asked for but not rendered is an error, not a note (Studio's mode).")


def navigation_from_args(args: argparse.Namespace, base: Optional[dict] = None) -> dict:
    """The navigation block: the file's, with the flags on top."""
    from .composition.navigation import block_from_flags, merge_blocks

    flags = block_from_flags(
        preset=args.nav_preset, carriers=args.nav, header=args.nav_header,
        pin=args.nav_pin, hide=args.nav_hide, order=args.nav_order, slots=args.nav_slot,
        guest=args.nav_guest, mobile_preset=getattr(args, "mobile_nav_preset", None),
        mobile_carriers=getattr(args, "mobile_nav", None),
    )
    if flags.get("preset") and base:
        # A preset on the command line replaces the file's carriers and header.
        base = {k: v for k, v in base.items() if k not in ("preset", "primary", "header")}
    return merge_blocks(base or {}, flags)


def load_composition_file(path: Path) -> dict:
    """``--composition``: ``{create_project kwarg: value}`` plus ``navigation``."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Error: --composition {path}: {exc}") from None
    if not isinstance(doc, dict):
        raise SystemExit(f"Error: --composition {path}: expected a JSON object")
    flat = dict(doc.get("input") or {})
    flat.update({k: v for k, v in doc.items() if k not in ("input", "resolved", "version",
                                                            "generator")})
    out: dict[str, Any] = {}
    unknown = []
    for key, value in flat.items():
        if key == "navigation":
            out["navigation"] = value
        elif key in COMPOSITION_KEYS:
            out[COMPOSITION_KEYS[key]] = value
        elif key not in ("slug", "backendPrefixes", "backendUpstreamDefault"):
            unknown.append(key)
    if unknown:
        raise SystemExit(
            f"Error: --composition {path}: unknown key(s) {', '.join(sorted(unknown))}; "
            f"known: {', '.join([*COMPOSITION_KEYS, 'navigation'])}")
    return out


def bound_app_pages(app_pages_ts: Path) -> set[str]:
    """The own-page ids ``src/app.pages.ts`` binds (``"app.x": Component``)."""
    if not app_pages_ts.exists():
        return set()
    text = re.sub(r"//[^\n]*", "", app_pages_ts.read_text(encoding="utf-8"))
    return set(_BINDING_RE.findall(text))


def load_app_nav(path: Path) -> list[dict]:
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    return list(doc.get("entries") or [])


# ---------------------------------------------------------------------------
# stapel-nav-plan
# ---------------------------------------------------------------------------


def plan_main(argv: Optional[list[str]] = None) -> int:
    """``stapel-nav-plan``: print the resolved navigation, write nothing."""
    from ._auth_model import resolve as resolve_auth_model
    from .composition.emitter import build_composition
    from .composition.model import CompositionError, PlatformRequest
    from .composition.navigation import plan_navigation
    from .create_project import _expand_with_requires

    parser = argparse.ArgumentParser(
        prog="stapel-nav-plan",
        description="Print the resolved navigation of a composition (every platform) "
                    "without writing a project.")
    parser.add_argument("--type", dest="project_type", default=None,
                        choices=("monolith", "minimal"))
    parser.add_argument("--title", default=None)
    parser.add_argument("--modules", nargs="+", default=None)
    parser.add_argument("--landing", action="store_true", default=None)
    parser.add_argument("--auth-model", default=None,
                        choices=("none", "anonymous", "public", "internal"))
    parser.add_argument("--shell", default=None)
    parser.add_argument("--mobile", choices=("kmp",), default=None)
    parser.add_argument("--mobile-shell", default=None)
    parser.add_argument("--app-nav", type=Path, default=None,
                        help="A stapel.app-nav.json to plan the project's own pages from.")
    parser.add_argument("--svg", type=Path, default=None, metavar="DIR",
                        help="Also write one SVG wireframe per platform, audience and size "
                             "class (<platform>-<audience>-<class>.svg) into DIR.")
    parser.add_argument("--labels", type=Path, default=None, metavar="JSON",
                        help="With --svg: {destination id: label} for the wireframes "
                             "(default: the id's last segment).")
    add_nav_arguments(parser)
    args = parser.parse_args(argv)
    base = load_composition_file(args.composition) if args.composition else {}
    project_type = args.project_type or base.get("project_type") or "monolith"
    modules = args.modules or base.get("modules") or ["core"]
    landing = args.landing if args.landing is not None else bool(base.get("want_landing"))
    feats = _expand_with_requires([m for m in modules if m != "core"])
    try:
        auth_comp = resolve_auth_model(
            args.auth_model or base.get("auth_model"), base.get("auth_kinds"),
            base.get("posture"), auth_selected="auth" in feats)
        if auth_comp is not None and auth_comp.needs_auth_module and "auth" not in feats:
            feats = _expand_with_requires([*feats, "auth"])
        want_auth = base.get("want_auth")
        if want_auth is None:
            want_auth = auth_comp.has_login_route if auth_comp else "auth" in feats
        mobile = None
        if (args.mobile or base.get("mobile")) == "kmp":
            mobile = PlatformRequest("kmp", shell=args.mobile_shell)
        comp = build_composition(
            ctx={"slug": "plan", "title": args.title or base.get("title") or "App"},
            project_type=project_type, feature_libs=feats, want_auth=want_auth,
            want_landing=landing, auth_comp=auth_comp,
            shell=args.shell or base.get("shell"), slots=base.get("slots"),
            mobile=mobile, navigation=navigation_from_args(args, base.get("navigation")),
            app_nav=tuple(load_app_nav(args.app_nav)) if args.app_nav else (),
            strict=bool(args.strict if args.strict is not None else base.get("strict")),
        )
        resolved = plan_navigation(comp)
    except (CompositionError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(resolved, indent=2))
    if args.svg is not None:
        from .composition.wireframe import write_wireframes

        labels = json.loads(args.labels.read_text(encoding="utf-8")) if args.labels else None
        written = write_wireframes(resolved, args.svg, labels=labels,
                                   title=args.title or base.get("title") or "")
        print(f"stapel-nav-plan: {len(written)} wireframe(s) in {args.svg}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# stapel-nav-regenerate
# ---------------------------------------------------------------------------

#: Files regenerate rewrites (frontend-relative). Everything else is the project's.
REGENERATED: tuple[str, ...] = (
    "src/routes.tsx", "src/nav.generated.ts", "src/chrome.generated.tsx",
    "src/StorefrontShell.tsx", "src/AppChrome.tsx", "src/embeds.generated.tsx",
    "src/AppPage.tsx", "src/__tests__/nav.test.ts", "src/__tests__/shell.test.tsx",
    "src/__tests__/shell-lint.test.ts", "src/__tests__/generated.test.ts",
    "stapel.composition.json",
)
#: Written only when missing (the page they render may need them).
WRITE_IF_MISSING: tuple[str, ...] = ("src/NavPlaceholder.tsx", "src/app.pages.ts")


def regenerate(project_dir: Path) -> list[str]:
    """Re-plan the frontend from the root composition and the project's own
    pages; rewrite only the generated nav files. Returns the paths written."""
    from ._auth_model import resolve as resolve_auth_model
    from .composition.emitter import build_composition, emitter_for

    root = Path(project_dir)
    doc = json.loads((root / "stapel.composition.json").read_text(encoding="utf-8"))
    generator = doc.get("generator")
    app_nav = load_app_nav(root / "stapel.app-nav.json")
    if generator == "stapel-frontend-repo-init":
        return _regenerate_public(root, doc, app_nav)
    if generator != "stapel-create-project":
        raise SystemExit(
            f"Error: {root / 'stapel.composition.json'} names no generator this tool "
            "regenerates (stapel-create-project, stapel-frontend-repo-init)")
    inp = doc["input"]
    if inp["type"] != "monolith":
        raise SystemExit("Error: only a monolith has a frontend to regenerate")
    front = root / "frontend"
    bound = bound_app_pages(front / "src" / "app.pages.ts")
    feats = list(inp["modules"])
    auth_comp = resolve_auth_model(inp.get("authModel"), inp.get("authKinds"),
                                   inp.get("posture"), auth_selected="auth" in feats)
    from .create_project import _auto_anonymous_actions

    comp = build_composition(
        ctx={"slug": inp["slug"], "title": inp["title"], "url": inp.get("url", "")},
        project_type="monolith", feature_libs=feats, want_auth=inp.get("auth"),
        want_landing=bool(inp.get("landing")), auth_comp=auth_comp, theme=inp.get("theme"),
        slots=inp.get("slots") or None, backend_prefixes=inp.get("backendPrefixes"),
        backend_upstream_default=inp.get("backendUpstreamDefault", ""),
        auto_anonymous_actions=_auto_anonymous_actions(feats, auth_comp, None),
        navigation=doc.get("navigation") or {}, app_nav=tuple(app_nav),
        app_bindings={"react": tuple(sorted(bound))}, strict=bool(inp.get("strict")),
    )
    plan = emitter_for("react").plan(comp, comp.platform("react"))
    written = _write_selected(root, plan.files, prefix="frontend/")
    doc["resolved"]["react"] = json.loads(
        plan.files["frontend/stapel.composition.json"])["navigation"]["resolved"]
    (root / "stapel.composition.json").write_text(json.dumps(doc, indent=2) + "\n",
                                                  encoding="utf-8")
    written.append("stapel.composition.json")
    for note in plan.notes:
        print(f"  note: {note}")
    return written


def _write_selected(root: Path, files: dict, *, prefix: str) -> list[str]:
    written = []
    for rel, content in files.items():
        if not rel.startswith(prefix):
            continue
        local = rel[len(prefix):]
        path = root / rel
        if local in REGENERATED or (local in WRITE_IF_MISSING and not path.exists()):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(rel)
    return written


def _regenerate_public(root: Path, doc: dict, app_nav: list[dict]) -> list[str]:
    from .frontend_repo_init import public_source_plan

    inp = doc["input"]
    bound = bound_app_pages(root / "src" / "app.pages.ts")
    result = public_source_plan(
        name=inp["name"], title=inp["title"], pairs=list(inp["pairs"]),
        locale=inp.get("locale", "en"), realtime=bool(inp.get("realtime")),
        extra_prefixes=list(inp.get("prefixes") or []), doc_type=inp.get("docType", ""),
        navigation=(doc.get("navigation") or {}).get("block") or {}, app_nav=app_nav,
        bound=bound, strict=bool(inp.get("strict")),
    )
    return _write_selected(root, result["files"], prefix="")


def regenerate_main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-nav-regenerate",
        description="Fold the project's own pages (stapel.app-nav.json + src/app.pages.ts) "
                    "and stapel.composition.json back into the generated route tree, nav "
                    "data, chrome and gates. Rewrites only generated files.")
    parser.add_argument("project", type=Path, nargs="?", default=Path("."),
                        help="project root (where stapel.composition.json is)")
    args = parser.parse_args(argv)
    from .composition.model import CompositionError

    try:
        written = regenerate(args.project)
    except CompositionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    for rel in written:
        print(f"  regenerated {rel}")
    return 0

