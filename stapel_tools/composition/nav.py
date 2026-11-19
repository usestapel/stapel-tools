"""The nav route planner — pure, deterministic, no I/O.

Which selected nav entries become routes, where container roots are injected,
which entries are orphans: one decision for every container renderer. Moved
here from ``_frontend_templates`` (which re-exports every name) so a second
platform's emitter plans from the same function.

Input entries are the pair projections ``FRONTEND_REACT_LIBS`` holds
(``{"key": <brick>, "nav": [...]}``); output entries are annotated with
``_brick``, never with a platform's package name.
"""
from __future__ import annotations


class NavContractError(ValueError):
    """A nav mirror declares something no container can honour. Raised at
    GENERATION time on purpose: every one of these used to degrade silently —
    an unknown icon rendered a generic glyph, an undeclared parent dropped the
    screen, a `route.index` was copied around and read by nobody — and a
    scaffolded app looked complete while a screen was simply gone."""


# Nav parents no PAIR owns, because no module owns "the account section" or
# "the admin section": the CONTAINER declares them (ACCOUNT_ROOT_ENTRY /
# ADMIN_ROOT_ENTRY below) and every `placement.parentId` pointing at one
# resolves against the container's own one-entry manifest.
CONTAINER_NAV_ROOTS: frozenset[str] = frozenset({"account.root", "admin.root"})


def declared_nav_ids() -> set[str]:
    """Every nav id ANY registered pair declares, plus the container-owned
    roots. The reference set for the parent check: a `parentId` inside it but
    absent from THIS project's selection is a legitimate drop ("that pair is not
    installed"); a `parentId` outside it is a typo or a parent nobody ever
    declared — the `admin.root` case, where two real admin screens vanished from
    every generated container without a word."""
    from ..create_project import FRONTEND_REACT_LIBS

    ids = set(CONTAINER_NAV_ROOTS)
    for pair in FRONTEND_REACT_LIBS.values():
        for entry in pair.get("nav", ()):
            ids.add(entry["id"])
    return ids


def validate_nav_entries(
    entries: list[dict],
    *,
    declared: set[str] | None = None,
    icons: frozenset[str] | None = None,
) -> None:
    """Refuse, at generation time, the three nav declarations that used to
    degrade quietly (shared-layer audit Q3/G5):

    * an `icon` outside the shell's registry — renders a generic glyph today;
    * a submenu `parentId` NO pair and no container ever declares — the screen
      is dropped as an orphan and nothing says so (gdpr's `admin.privacy` and
      video's `admin.usage` were both invisible in every generated container);
    * a `route.index` — the field is DEAD: `resolveNav` copies `route` opaque,
      the shell's `matchesLocation` ignores it, and this generator decides a
      section's index itself (the container owns `/account` and `/admin`). It is
      dropped from the contract rather than half-implemented, so declaring one
      is an error that names the decision instead of a route that never matches.
    """
    known = declared_nav_ids() if declared is None else declared
    if icons is None:
        from .react.registry import NAV_ICON_REGISTRY as icons
    problems: list[str] = []
    for entry in entries:
        icon = entry.get("icon")
        if icon not in icons:
            problems.append(
                f"{entry['id']}: icon {icon!r} is not in the shell's registry "
                f"({', '.join(sorted(icons))}). Add the icon to "
                "shell-react/src/default/icons.tsx AND to REACT_ICONS, or "
                "pick a registered name — an unknown name renders a generic "
                "glyph with no error."
            )
        if "index" in entry.get("route", {}):
            problems.append(
                f"{entry['id']}: route.index is not honoured by anything — "
                "resolveNav copies `route` opaque, the shell's matchesLocation "
                "ignores it, and the container decides its own section index. "
                "Drop the field from the declaration."
            )
        placement = entry.get("placement", {})
        if placement.get("level") == "submenu":
            parent = placement.get("parentId")
            if parent is None or parent not in known:
                problems.append(
                    f"{entry['id']}: placement.parentId {parent!r} is declared by "
                    "no registered pair and by no container root "
                    f"({', '.join(sorted(CONTAINER_NAV_ROOTS))}). A submenu entry "
                    "under a parent nobody declares is dropped as an orphan and "
                    "the screen disappears silently."
                )
    if problems:
        raise NavContractError(
            "nav manifest mirror is not mountable:\n  - " + "\n  - ".join(problems)
        )


def parent_roots_needed(pairs: list[dict]) -> list[str]:
    """Which container roots the selected pairs' entries actually hang from.

    Returned sorted, and EMPTY when nothing needs them: a project with no
    admin-level screen must not grow an empty "Admin" tab, and a project that
    has one must not lose it (gdpr's `admin.privacy` and video's `admin.usage`
    were dropped as orphans in every generated container because no manifest
    declared `admin.root` — the shared-layer audit's Q3 finding)."""
    needed = set()
    for pair in pairs:
        for entry in pair.get("nav", ()):
            placement = entry.get("placement", {})
            if placement.get("level") != "submenu":
                continue
            parent = placement.get("parentId")
            if parent in CONTAINER_NAV_ROOTS:
                needed.add(parent)
    return sorted(needed)


def nav_wired_pairs(react_entries: list[dict], *, auth_wired: bool) -> list[dict]:
    """The selected react pairs (``create_project._frontend_react_entries``
    output) that actually join the scripted nav/route tree — every selected
    pair carrying a ``nav`` mirror, EXCEPT auth's when ``auth_wired`` is
    False (the ``--no-auth`` escape hatch: a project can still wire the auth
    RUNTIME via ``modules.tsx`` — e.g. to drive `AuthPanel` by hand
    somewhere of its own choosing — without any of auth's screens joining
    "/login"/the nav menu)."""
    return [e for e in react_entries if e.get("nav") and (e["key"] != "auth" or auth_wired)]


def nav_entry_surface(entry: dict) -> str:
    """The entry's resolved surface — declared, or derived from
    ``requiresAuth``. The same derivation ``@stapel/shell-react``'s
    ``navEntrySurface`` does, in ONE place here: both container renderers
    route by surface, and two copies of the rule is how they came to
    disagree about where a public screen belongs."""
    return entry.get("surface") or ("member" if entry["requiresAuth"] else "public")


def build_nav_route_plan(
    nav_pairs: list[dict],
    *,
    container_roots: dict[str, dict] | None = None,
    icons: frozenset[str] | None = None,
    declared: set[str] | None = None,
) -> dict:
    """Pure, deterministic route-tree plan ``render_routes_tsx`` turns into
    react-router v7 route objects — the SCRIPTED (no-LLM) decision tree over
    the selected pairs' mirrored nav entries (registry order).

    Two kinds of entry become a route:

    - a TOP entry whose ``route.path`` is ABSOLUTE (starts with "/", e.g.
      auth.login's "/login") mounts as its own top-level sibling route —
      NEVER nested under "/app" (a sign-in screen is reachable regardless of
      session state, the opposite of what "/app" protects).
    - a TOP entry with a RELATIVE path, or a SUBMENU entry whose
      ``placement.parentId`` resolves among the selected TOP entries, nests
      as a child of "/app" (its full path is ``"<parent-path>/<own-path>"``
      for a submenu entry — e.g. auth.security under profiles.settings
      becomes "settings/security"). A submenu entry whose parent isn't
      among the selected TOP entries is DROPPED — the exact orphan-drop
      rule ``@stapel/shell-react``'s own ``resolveNav`` documents, mirrored
      here so routing and the nav menu never disagree about what "installed"
      means.

    A CONTAINER ROOT (`account.root`, `admin.root`) is injected as a top
    whenever a selected entry hangs from one: no module owns "the account
    section" or "the admin section", so without the container declaring them
    those screens were dropped as orphans and vanished with no log.

    Returns ``{"absolute_routes": [...], "app_children": [...],
    "container_roots": [...]}``; each route is ``{"path": <route path>,
    "entry": <mirrored NavEntry dict>}`` — entries carry a ``"_brick"`` key
    (the brick that declares them; a platform's renderer resolves its own
    package from it) or a ``"_local"`` key for a page the container itself
    writes. ``container_roots`` and ``icons`` default to the React binding's
    (``MONOLITH_CONTAINER_ROOTS``, ``NAV_ICON_REGISTRY``); ``declared`` (the
    ids a ``parentId`` may name) to :func:`declared_nav_ids`.
    """
    if container_roots is None:
        from .react.registry import MONOLITH_CONTAINER_ROOTS as container_roots
    all_entries = [{**entry, "_brick": pair.get("key")} for pair in nav_pairs for entry in pair["nav"]]
    validate_nav_entries(all_entries, declared=declared, icons=icons)
    roots = parent_roots_needed(nav_pairs)
    all_entries.extend(container_roots[root] for root in roots)
    tops = {e["id"]: e for e in all_entries if e["placement"]["level"] == "top"}
    children_by_parent: dict[str, list[dict]] = {}
    for e in all_entries:
        if e["placement"]["level"] != "submenu":
            continue
        parent_id = e["placement"].get("parentId")
        if parent_id is None or parent_id not in tops:
            continue  # orphan — dropped, not thrown (mirrors resolveNav)
        children_by_parent.setdefault(parent_id, []).append(e)

    absolute_routes: list[dict] = []
    app_children: list[dict] = []
    for top in sorted(tops.values(), key=lambda e: (e["order"], e["id"])):
        path = top["route"]["path"]
        if path.startswith("/"):
            absolute_routes.append({"path": path, "entry": top})
            continue
        if nav_entry_surface(top) == "public":
            # A PUBLIC-surface screen with a relative path is not an "/app"
            # child: "/app" is the member area, and this container gates it
            # whenever auth is wired. The public renderer has always hoisted
            # such a screen to a root-level sibling
            # (build_public_route_plan's own rule); this plan did not, so
            # gdpr's `public.privacy-request` — the DSAR form a person with
            # no account is supposed to be able to open — sat behind the
            # login wall in every generated monolith, and recordings'
            # `share/:linkToken` would have joined it. A sibling route still
            # honours its own `requiresAuth` (render_routes_tsx), so this
            # relocates the screen without loosening anything.
            absolute_routes.append({"path": f"/{path}", "entry": top})
            for child in sorted(
                children_by_parent.get(top["id"], []), key=lambda e: (e["order"], e["id"])
            ):
                absolute_routes.append(
                    {"path": f'/{path}/{child["route"]["path"]}', "entry": child}
                )
            continue
        app_children.append({"path": path, "entry": top})
        for child in sorted(children_by_parent.get(top["id"], []), key=lambda e: (e["order"], e["id"])):
            app_children.append({"path": f'{path}/{child["route"]["path"]}', "entry": child})

    return {
        "absolute_routes": absolute_routes,
        "app_children": app_children,
        "container_roots": roots,
    }
