"""The React half of the navigation model: which shipped chrome draws the
resolved plan, the ONE route tree every route hangs from, every routed
destination's absolute address, and how each mounted route is reached.

Two shapes, chosen by the resolved chrome (``navigation.PLATFORM_CHROMES``):

* ``storefront`` — ``<PublicShell/>`` over the public route plan
  (``build_public_route_plan``): public routes, then the member subtree behind
  ``<MemberGate/>``, all children of one layout route. The same plan the
  split-repository storefront has always used; the monolith now emits it too.
* ``app`` — ``<AppShell/>`` over the monolith plan (``build_nav_route_plan``):
  ``/``, the sign-in route and every public route are children of the same
  layout route as the gated ``/app`` subtree.

Pure: inputs are the composition and the React pair entries; nothing here
reads a file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .. import navigation as N
from ..icons import NAV_ICONS
from ..model import Composition

APP_PACKAGE = "app"
APP_PAGE_EXPORT = "AppPage"
LAYOUT_ROUTE_ID = "stapel-shell"
SIGN_IN_ENTRY = "auth.login"
#: Where a MONOLITH's storefront mounts the admin section: `/admin` is the
#: backend's reserved prefix in a monolith (nginx §57 canon, the
#: `stapel/no-reserved-backend-route` lint), a split storefront owns its paths.
MONOLITH_STOREFRONT_ADMIN_PATH = "/staff"
_APP_ID_PREFIX = "app."
_APP_KEYS = (
    "id", "labelKey", "shortLabelKey", "icon", "path", "level", "parentId",
    "menuVisibleDefault", "surface", "order", "action", "via",
)


@dataclass
class ReactShellPlan:
    """Everything the React emitter needs to write the shell and its routes."""

    nav: N.PlatformNavigation
    shape: str                          # the chrome: "storefront" | "app"
    routes: str                         # the route plan: "public" | "app"
    nav_pairs: list[dict]
    manifests: list[dict]
    route_plan: dict
    paths: dict[str, str]
    reach: dict[str, str]
    sign_in_path: Optional[str]
    has_accounts: bool
    guest_wall: bool
    app_entries: list[dict] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    overrides: dict = field(default_factory=dict)

    @property
    def resolved(self) -> dict:
        return self.nav.resolved

    @property
    def legal_links(self) -> list[tuple[str, str]]:
        """``(path, labelKey)`` of the mounted legal destinations, in table order."""
        labels = {e["id"]: e["labelKey"] for m in self.manifests for e in m["entries"]}
        return [(self.paths[i], labels[i]) for i in N.LEGAL_ENTRIES if i in self.paths]


# ---------------------------------------------------------------------------
# the project's own pages (stapel.app-nav.json)
# ---------------------------------------------------------------------------


def validate_app_nav(entries) -> list[dict]:
    """Check ``stapel.app-nav.json``'s entries (neutral shape); returns them."""
    out: list[dict] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries or ()):
        where = f"stapel.app-nav.json entries[{i}]"
        if not isinstance(entry, dict):
            raise N.NavigationError("NAV-E00", f"{where}: expected an object")
        unknown = [k for k in entry if k not in _APP_KEYS]
        if unknown:
            raise N.NavigationError(
                "NAV-E00", f"{where}: unknown key(s) {', '.join(unknown)}; known: "
                + ", ".join(_APP_KEYS))
        entry_id = entry.get("id", "")
        if not entry_id.startswith(_APP_ID_PREFIX) or not N._ID_RE.match(entry_id):
            raise N.NavigationError(
                "NAV-E06", f"{where}: id {entry_id!r} must be app.<screen> (lower case)")
        if entry_id in seen:
            raise N.NavigationError("NAV-E06", f"{where}: id {entry_id} repeats")
        seen.add(entry_id)
        for key in ("labelKey", "path"):
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise N.NavigationError("NAV-E00", f"{where} ({entry_id}): {key} is required")
        icon = entry.get("icon", "apps")
        if icon not in NAV_ICONS:
            raise N.NavigationError(
                "NAV-E06", f"{where} ({entry_id}): icon {icon!r} is not a neutral icon key; "
                f"keys: {', '.join(NAV_ICONS)}")
        level = entry.get("level", "top")
        if level not in ("top", "submenu"):
            raise N.NavigationError("NAV-E00", f"{where} ({entry_id}): level is top or submenu")
        if level == "submenu" and not entry.get("parentId"):
            raise N.NavigationError(
                "NAV-E06", f"{where} ({entry_id}): a submenu entry names its parentId")
        if entry.get("surface", "public") not in ("public", "member"):
            raise N.NavigationError(
                "NAV-E00", f"{where} ({entry_id}): surface is public or member")
        out.append(dict(entry))
    return out


def app_manifest_entries(app_nav: list[dict]) -> list[dict]:
    """The project's own entries in nav-manifest shape (``_app`` marks them)."""
    from .registry import react_icon

    out = []
    for e in app_nav:
        surface = e.get("surface", "public")
        entry: dict = {"id": e["id"], "labelKey": e["labelKey"]}
        if e.get("shortLabelKey"):
            entry["shortLabelKey"] = e["shortLabelKey"]
        entry["icon"] = react_icon(e.get("icon", "apps"), entry_id=e["id"])
        entry["route"] = {"path": e["path"]}
        entry["component"] = {"export": APP_PAGE_EXPORT, "subpath": "."}
        placement: dict = {"level": e.get("level", "top")}
        if e.get("parentId"):
            placement["parentId"] = e["parentId"]
        entry["placement"] = placement
        entry["menuVisibleDefault"] = bool(e.get("menuVisibleDefault", True))
        entry["requiresAuth"] = surface == "member"
        entry["surface"] = surface
        entry["order"] = int(e.get("order", 50))
        entry["_app"] = True
        if e.get("action"):
            entry["_action"] = True
        if e.get("via"):
            entry["_via"] = e["via"]
        out.append(entry)
    return out


def strip_private(entry: dict) -> dict:
    """A manifest entry without the planner's ``_`` annotations."""
    return {k: v for k, v in entry.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------


def _destinations(entries: list[dict]) -> list[N.Destination]:
    out = N.destinations_from_manifest(entries)
    actions = {e["id"] for e in entries if e.get("_action")}
    return [
        N.Destination(**{**d.__dict__, "action": d.id in actions}) if d.id in actions else d
        for d in out
    ]


def _embed_ids(react_entries: list[dict]) -> list[str]:
    return [emb["id"] for e in react_entries for emb in e.get("embeds") or ()]


def _sign_in(entries: list[dict]) -> Optional[dict]:
    return next((e for e in entries if e["id"] == SIGN_IN_ENTRY), None)


def plan_react_shell(  # noqa: C901
    comp: Composition,
    react_entries: list[dict],
    *,
    app_nav: Optional[list[dict]] = None,
    bound: Optional[set[str]] = None,
    app_package: str = APP_PACKAGE,
    strict: Optional[bool] = None,
    routes: Optional[str] = None,
) -> ReactShellPlan:
    """Resolve ``comp.navigation`` for React and lay out the route tree."""
    from ... import _frontend_templates as F

    app_nav = validate_app_nav(comp.app_nav if app_nav is None else app_nav)
    bound = set(comp.app_bindings.get("react", ()) if bound is None else bound)
    strict = comp.strict if strict is None else strict
    auth_wired = bool(comp.want_auth) and any(e["key"] == "auth" for e in react_entries)
    has_accounts = auth_wired and comp.auth_model not in ("none",)
    auth_model = (comp.auth_model or "public") if has_accounts else None
    nav_pairs = F.nav_wired_pairs(react_entries, auth_wired=auth_wired)
    own = app_manifest_entries(app_nav)
    pending = [e["id"] for e in own if e["id"] not in bound]
    own_pair = (
        [{"key": app_package, "package": app_package, "version": "0.0.0", "nav": own}]
        if own else []
    )
    declared = F.declared_nav_ids() | {e["id"] for e in own}

    # Pass one: shape-independent destinations decide the chrome.
    probe_entries = [e for p in (*nav_pairs, *own_pair) for e in p["nav"]]
    if not has_accounts:
        probe_entries = [e for e in probe_entries if F.nav_entry_surface(e) == "public"]
    else:
        probe_entries += [F.ACCOUNT_ROOT_ENTRY, F.ADMIN_ROOT_ENTRY]
    size_classes = N.size_classes_from_theme(comp.theme_json)
    common = dict(
        platform="react", embeds=_embed_ids(react_entries), modules=comp.modules,
        auth_model=auth_model, has_sign_in=auth_wired and has_accounts,
        sign_in_id=SIGN_IN_ENTRY, landing=comp.landing, size_classes=size_classes,
        strict=strict, pending=pending,
    )
    probe = N.plan_platform(comp.navigation, destinations=_destinations(probe_entries), **common)
    shape = "storefront" if probe.chrome == "PublicShell" else "app"
    routes = routes or ("public" if shape == "storefront" else "app")

    # Pass two: the route plan's own manifests.
    if routes == "public":
        manifests = _storefront_manifests(
            nav_pairs, own, has_accounts, app_package,
            admin_path=MONOLITH_STOREFRONT_ADMIN_PATH if comp.project_type == "monolith"
            else None)
        route_plan = F.build_public_route_plan(manifests, declared=declared)
        paths = _storefront_paths(route_plan)
    else:
        all_pairs = [*nav_pairs, *own_pair]
        route_plan = F.build_nav_route_plan(all_pairs, declared=declared)
        manifests = _app_manifests(nav_pairs, own, app_package)
        paths = F.nav_entry_paths(route_plan)
    entries = [e for m in manifests for e in m["entries"]]
    nav = N.plan_platform(comp.navigation, destinations=_destinations(entries), **common)

    sign_in = _sign_in(entries)
    sign_in_path = paths.get(SIGN_IN_ENTRY) if (sign_in and has_accounts) else None
    if nav.resolved["signIn"] and sign_in_path is None:
        nav.resolved["signIn"] = None
    guest_wall = nav.resolved["guest"]["memberDestinations"] == "wall"
    overrides = _overrides(comp.navigation or {}, nav, entries)
    reach, unreachable, waiting = _reach(
        entries, paths, react_entries, nav, has_accounts=has_accounts,
        sign_in_path=sign_in_path, hidden=set(
            ((comp.navigation or {}).get("destinations") or {}).get("hidden", [])),
    )
    from ...create_project import FRONTEND_REACT_LIBS, pending_react_pairs

    unpublished = [
        f"{key}: {FRONTEND_REACT_LIBS[key]['package']} is not on npm yet — its screens are "
        "the project's own pages (stapel.app-nav.json) until it is"
        for key in pending_react_pairs(comp.modules)
    ]
    nav.resolved["pending"] = [*nav.resolved["pending"], *waiting, *unpublished]
    for entry_id, path in unreachable:
        nav.notes.append(
            f"unreachable: {entry_id} at {path} — nothing in the shell links it; make it "
            "menu-visible, give it `via`, or hide it deliberately")
    if nav.resolved["source"] != "default" and (comp.navigation or {}).get(
            "destinations", {}).get("groups"):
        nav.notes.append(
            "destinations.groups: recorded; @stapel/shell-react 0.20.1 draws no group "
            "headings (NavShell does)")
        if strict:
            raise N.NavigationError(
                "NAV-E08", "destinations.groups are not drawn on react yet (queue item: "
                + N.QUEUE_ITEMS["react"] + ")")
    if strict and unreachable:
        raise N.NavigationError(
            "NAV-E06", "unreachable route(s): " + ", ".join(
                f"{i} ({p})" for i, p in unreachable)
            + " — make each menu-visible, give it `via`, or hide it")
    nav.resolved["notes"] = list(nav.notes)
    nav.resolved["reach"] = reach
    nav.resolved["paths"] = dict(paths)
    return ReactShellPlan(
        nav=nav, shape=shape, routes=routes, nav_pairs=nav_pairs, manifests=manifests,
        route_plan=route_plan, paths=paths, reach=reach, sign_in_path=sign_in_path,
        has_accounts=has_accounts, guest_wall=guest_wall, app_entries=own,
        pending=pending, overrides=overrides,
    )


def _storefront_manifests(nav_pairs, own, has_accounts, app_package, *,
                          admin_path: Optional[str] = None) -> list[dict]:
    from ... import _frontend_templates as F

    if has_accounts:
        manifests = F.public_nav_manifests(nav_pairs, app_package=app_package)
        if admin_path is not None:
            for manifest in manifests:
                manifest["entries"] = [
                    {**e, "route": {**e["route"], "path": admin_path}}
                    if e["id"] == F.ADMIN_ROOT_ENTRY["id"] else e
                    for e in manifest["entries"]
                ]
    else:
        manifests = [
            {"package": p["package"], "version": p["version"],
             "entries": [e for e in p["nav"] if F.nav_entry_surface(e) == "public"]}
            for p in nav_pairs
        ]
        manifests = [m for m in manifests if m["entries"]]
    if own:
        entries = own if has_accounts else [e for e in own if e["surface"] == "public"]
        app = next((m for m in manifests if m["package"] == app_package), None)
        if app is None:
            manifests.append({"package": app_package, "version": "0.0.0", "entries": entries})
        else:
            app["entries"] = [*app["entries"], *entries]
    return manifests


def _app_manifests(nav_pairs, own, app_package) -> list[dict]:
    from ... import _frontend_templates as F

    manifests = [
        {"package": p["package"], "version": p["version"], "entries": p["nav"]}
        for p in nav_pairs
    ]
    roots = F.parent_roots_needed([*nav_pairs, {"nav": own}])
    app_entries = [
        {k: v for k, v in F.MONOLITH_CONTAINER_ROOTS[root].items()}
        for root in roots
    ] + own
    if app_entries:
        manifests.append({"package": app_package, "version": "0.0.0", "entries": app_entries})
    return manifests


def _storefront_paths(plan: dict) -> dict[str, str]:
    paths = {r["entry"]["id"]: r["path"] for r in (*plan["public"], *plan["member_absolute"])}
    account = plan.get("account_entry")
    base = account["route"]["path"] if account else "/account"
    if account:
        paths[account["id"]] = base
    for r in plan["account_children"]:
        paths[r["entry"]["id"]] = f'{base.rstrip("/")}/{r["path"]}'
    return paths


def _menu_ids(entries: list[dict], *, audience_member: bool, guest_wall: bool,
              hidden: set[str]) -> set[str]:
    """Ids the resolved menu shows to some audience (resolveNav's rules)."""
    tops = {e["id"]: e for e in entries if e["placement"]["level"] == "top"}
    visible: set[str] = set()

    def shown(e: dict) -> bool:
        surface = e.get("surface") or ("member" if e.get("requiresAuth") else "public")
        if e["id"] in hidden or not e.get("menuVisibleDefault", True):
            return False
        return audience_member or surface == "public" or guest_wall

    for e in tops.values():
        if shown(e):
            visible.add(e["id"])
    for e in entries:
        parent = e["placement"].get("parentId")
        if e["placement"]["level"] == "submenu" and parent in visible and shown(e):
            visible.add(e["id"])
    return visible


def _reach(entries, paths, react_entries, nav, *, has_accounts, sign_in_path, hidden):
    """Mounted route pattern -> how a person reaches it from the shell."""
    reach: dict[str, str] = {"/": "home"}
    guest_wall = nav.resolved["guest"]["memberDestinations"] == "wall"
    menu = _menu_ids(entries, audience_member=has_accounts, guest_wall=guest_wall,
                     hidden=hidden)
    opened = {}
    excluded = set(nav.excluded_embeds)
    for pair in react_entries:
        for emb in pair.get("embeds") or ():
            if emb.get("opens") and emb["id"] not in excluded and emb["slot"] != "page.actions":
                opened.setdefault(emb["opens"], emb["id"])
    via = {e["id"]: e["_via"] for e in entries if e.get("_via")}
    via.update({k: v for k, v in N.VIA.items() if k not in via})
    unreachable: list[tuple[str, str]] = []
    waiting: list[str] = []
    pending_via: list[tuple[str, str, str]] = []
    for entry in entries:
        entry_id = entry["id"]
        path = paths.get(entry_id)
        if path is None:
            continue
        if path == sign_in_path:
            reason = "signIn"
        elif entry_id in menu:
            reason = f"menu:{entry_id}"
        elif entry_id in opened:
            reason = f"embed:{opened[entry_id]}"
        elif entry_id in hidden:
            reason = "hidden"
        elif entry_id in via and via[entry_id] in paths:
            pending_via.append((entry_id, path, via[entry_id]))
            continue
        elif entry_id in N.LEGAL_ENTRIES:
            reason = "footer"
        elif entry_id in N.EXTERNAL_ENTRY:
            reason = "external"
        elif entry_id in N.PENDING_EMBED_ENTRY:
            reason = f"pending:{N.PENDING_EMBED_ENTRY[entry_id].split(' ')[0]}"
            waiting.append(f"{entry_id}: reached through {N.PENDING_EMBED_ENTRY[entry_id]}")
        elif ":" in path:
            reason = "deep"
        else:
            unreachable.append((entry_id, path))
            continue
        reach.setdefault(path, reason)
    for entry_id, path, target in pending_via:
        if paths[target] in reach:
            reach.setdefault(path, f"via:{target}")
        else:
            unreachable.append((entry_id, path))
    return dict(sorted(reach.items())), unreachable, waiting


def _overrides(block: dict, nav: N.PlatformNavigation, entries: list[dict]) -> dict:
    """``stapel.nav.json`` overrides that carry the block's destination
    choices to the runtime resolver (hidden, order, pinned)."""
    dest = block.get("destinations") or {}
    out: dict[str, dict] = {}
    for entry_id, order in (dest.get("order") or {}).items():
        out.setdefault(entry_id, {})["order"] = order
    for i, entry_id in enumerate(dest.get("pinned") or ()):
        out.setdefault(entry_id, {})["order"] = -100 + i
    for entry_id in dest.get("hidden") or ():
        out.setdefault(entry_id, {})["menuVisible"] = False
    return out
