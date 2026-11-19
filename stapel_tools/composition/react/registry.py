"""The React binding of the brick registry, and the legacy views built from it.

The neutral registry (``composition.bricks``) says WHAT a brick contributes;
this module says how React delivers it: the antd glyph for each neutral icon
key, the component behind each nav id and embed id (``bindings.py``), how the
container mounts each screen (``mounts.py``), the container's own section
components, the cross-pair pages and the badge hooks.

The pre-split tables stay importable under their old names as COMPUTED
views — ``FRONTEND_REACT_LIBS``, ``NAV_ENTRY_MOUNTS``, ``COMPOSITE_PAGES``,
``MONOLITH_CONTAINER_ROOTS``, ``NAV_BADGE_SOURCES``, ``NAV_ICON_REGISTRY`` —
because the renderers, ``frontend_repo_init``, ``preset_gate``, the sync
script and the tests read them. ``tests/test_registry_split.py`` holds each
view equal to a frozen snapshot of the table it replaced, key order and
container types included.
"""
from __future__ import annotations

from ..bricks import BRICK_UI, CONTAINER_PAGES, CONTAINER_ROOTS
from ..icons import NAV_ICONS
from ..model import BrickUi, EmbedSpec, NavEntrySpec
from .binding import ReactBinding
from .bindings import REACT_BINDINGS
from .mounts import REACT_MOUNTS

# Neutral icon key -> the name the shell's registry resolves
# (`shell-react/src/default/icons.tsx` — `ICONS`). The registry falls back to
# a generic glyph for an unknown name WITHOUT a word, so a typo ships as a
# slightly wrong picture nobody notices; generation refuses one instead.
# Adding an icon to the shell means adding a neutral key (icons.py) and its
# name here, in the same wave.
REACT_ICONS: dict[str, str] = {
    "apps": "AppstoreOutlined",
    "audit": "AuditOutlined",
    "bell": "BellOutlined",
    "cart": "ShoppingCartOutlined",
    "clock": "ClockCircleOutlined",
    "folder": "FolderOutlined",
    "folder-open": "FolderOpenOutlined",
    "heart": "HeartOutlined",
    "home": "HomeOutlined",
    "list-ordered": "OrderedListOutlined",
    "login": "LoginOutlined",
    "message": "MessageOutlined",
    "plus": "PlusOutlined",
    "profile": "ProfileOutlined",
    "qr": "QrcodeOutlined",
    "search": "SearchOutlined",
    "settings": "SettingOutlined",
    "shield-check": "SafetyCertificateOutlined",
    "tag": "TagOutlined",
    "user": "UserOutlined",
}

# The container-local component that renders each container root's landing.
REACT_CONTAINER_ROOTS: dict[str, str] = {
    "account.root": "AccountSection",
    "admin.root": "AdminSection",
}

# The React half of the cross-pair pages (neutral half: bricks.CONTAINER_PAGES):
# the page component the container writes, and per brick the composer
# prop(s) its exports satisfy.
REACT_COMPOSITE_PAGES: dict[str, dict] = {
    "feed.home": {
        "page": "FeedHomePage",
        # The feed composer's picture queue: ONE upload bag, drawn by the
        # gallery field and published from by the composer.
        "props": {"cdn": ("attachments", "attachmentPicker")},
    },
    "listings.compose": {
        "page": "ListingComposePage",
        # `geo` used to live in an `unwired` table instead, because there was
        # no react pair for a geocoder. `@stapel/geo-react` is published and
        # registered now, so the slot is FILLED when the pair is selected and
        # the notice is emitted only when it is not. `currencies` is the same
        # shape one field over: unfilled, the price field states the
        # deployment's currency (the pair's own designed degradation) — so
        # there is no notice for that one, only a picker when the pair is
        # there.
        "props": {
            "categories": ("features", "renderCategoryPicker"),
            "cdn": ("gallerySlot", "images"),
            "geo": ("locationPicker",),
            "currencies": ("renderCurrencyPicker",),
        },
    },
}

# Runtime counts for nav destinations — the `navBadges` channel
# (`@stapel/shell-react` 0.11.0+): (nav id, neutral signal) -> the pair hook
# that yields the count and the variable the container names it. The shell
# fetches nothing; the CONTAINER joins the pair's count to the destination.
# One source today: `useUnreadCount()` is the only count a published pair
# exposes as a count. The hook is session-gated inside its own pair, so it is
# safe on a PUBLIC shell an anonymous visitor sees.
REACT_BADGES: dict[tuple[str, str], dict] = {
    ("notifications.feed", "unread_count"): {
        "hook": "useUnreadCount",
        "var": "unreadNotifications",
    },
}


class RegistryError(ValueError):
    """The neutral registry and the React binding disagree."""


def react_icon(key: str, *, entry_id: str) -> str:
    try:
        return REACT_ICONS[key]
    except KeyError:
        raise RegistryError(
            f"{entry_id}: icon {key!r} has no React glyph (REACT_ICONS); neutral "
            f"keys: {', '.join(NAV_ICONS)}"
        ) from None


# ---------------------------------------------------------------------------
# projections: neutral spec + binding -> the shapes the React renderers read
# ---------------------------------------------------------------------------


def project_nav_entry(entry: NavEntrySpec, binding: ReactBinding) -> dict:
    """One nav entry as the pair's ``nav-manifest.json`` spells it."""
    try:
        export, subpath = binding.components[entry.id]
    except KeyError:
        raise RegistryError(
            f"{entry.id}: no React component in {binding.package}'s binding"
        ) from None
    out: dict = {"id": entry.id, "labelKey": entry.label_key}
    if entry.short_label_key is not None:
        out["shortLabelKey"] = entry.short_label_key
    out["icon"] = react_icon(entry.icon, entry_id=entry.id)
    out["route"] = {"path": entry.path}
    out["component"] = {"export": export, "subpath": subpath}
    placement: dict = {"level": entry.level}
    if entry.parent_id is not None:
        placement["parentId"] = entry.parent_id
    out["placement"] = placement
    out["menuVisibleDefault"] = entry.menu_visible_default
    out["requiresAuth"] = entry.requires_auth
    if entry.surface is not None:
        out["surface"] = entry.surface
    out["order"] = entry.order
    return out


def project_embed(embed: EmbedSpec, binding: ReactBinding) -> dict:
    """One embed as the pair's ``nav-manifest.json`` spells it."""
    try:
        export, subpath = binding.embed_components[embed.id]
    except KeyError:
        raise RegistryError(
            f"{embed.id}: no React component in {binding.package}'s binding"
        ) from None
    out: dict = {
        "id": embed.id,
        "slot": embed.slot,
        "component": {"export": export, "subpath": subpath},
        "order": embed.order,
    }
    if embed.surface is not None:
        out["surface"] = embed.surface
    if embed.scope_nav_entry is not None:
        out["scope"] = {"navEntry": embed.scope_nav_entry}
    if embed.route_props:
        out["routeProps"] = dict(embed.route_props)
    if embed.breakpoints is not None:
        out["breakpoints"] = list(embed.breakpoints)
    if embed.opens is not None:
        out["opens"] = embed.opens
    if embed.props:
        out["props"] = dict(embed.props)
    return out


def project_react(brick: BrickUi | None, binding: ReactBinding) -> dict:
    """One ``FRONTEND_REACT_LIBS`` entry: the binding plus the brick's UI.

    A nav id or embed id the binding does not name is not delivered on
    React; a binding naming an id the brick does not declare is refused by
    :func:`check_bindings`."""
    out: dict = {"package": binding.package, "version": binding.version}
    if not binding.released:
        out["released"] = False
    if binding.static_peer_pairs:
        out["static_peer_pairs"] = list(binding.static_peer_pairs)
    for name in ("provider", "create_runtime", "register_i18n", "default_component",
                 "base_url"):
        value = getattr(binding, name)
        if value is not None:
            out[name] = value
    if binding.seam is not None:
        out["seam"] = dict(binding.seam)
    if brick is not None:
        nav = [project_nav_entry(e, binding) for e in brick.nav if e.id in binding.components]
        if nav:
            out["nav"] = nav
        embeds = [project_embed(e, binding) for e in brick.embeds
                  if e.id in binding.embed_components]
        if embeds:
            out["embeds"] = embeds
    if binding.shell_provider is not None:
        out["shell_provider"] = binding.shell_provider
    return out


def check_bindings() -> list[str]:
    """Every disagreement between the neutral registry and the React binding."""
    problems: list[str] = []
    missing_icons = [k for k in NAV_ICONS if k not in REACT_ICONS]
    if missing_icons:
        problems.append(f"REACT_ICONS lacks neutral key(s): {', '.join(missing_icons)}")
    extra_icons = [k for k in REACT_ICONS if k not in NAV_ICONS]
    if extra_icons:
        problems.append(f"REACT_ICONS maps unknown key(s): {', '.join(extra_icons)}")
    for key, binding in REACT_BINDINGS.items():
        brick = BRICK_UI.get(key)
        nav_ids = {e.id for e in brick.nav} if brick else set()
        embed_ids = {e.id for e in brick.embeds} if brick else set()
        for nav_id in binding.components:
            if nav_id not in nav_ids:
                problems.append(f"{key}: binding names nav id {nav_id!r} the brick does not declare")
        for embed_id in binding.embed_components:
            if embed_id not in embed_ids:
                problems.append(
                    f"{key}: binding names embed id {embed_id!r} the brick does not declare")
        if brick is not None:
            for entry in brick.nav:
                if entry.icon not in NAV_ICONS:
                    problems.append(f"{entry.id}: icon {entry.icon!r} is not a neutral key")
    for root_id, root in CONTAINER_ROOTS.items():
        if root_id not in REACT_CONTAINER_ROOTS:
            problems.append(f"container root {root_id!r} has no React section component")
        if root.icon not in NAV_ICONS:
            problems.append(f"{root_id}: icon {root.icon!r} is not a neutral key")
    for entry_id in CONTAINER_PAGES:
        if entry_id not in REACT_COMPOSITE_PAGES:
            problems.append(f"container page {entry_id!r} has no React page")
    return problems


# ---------------------------------------------------------------------------
# the legacy views
# ---------------------------------------------------------------------------


def frontend_react_libs() -> dict[str, dict]:
    return {key: project_react(BRICK_UI.get(key), b) for key, b in REACT_BINDINGS.items()}


def nav_entry_mounts() -> dict[str, dict]:
    page_slots = {
        entry_id: slots for brick in BRICK_UI.values() for entry_id, slots in brick.page_slots
    }
    out: dict[str, dict] = {}
    for entry_id, mount in REACT_MOUNTS.items():
        if entry_id not in page_slots:
            out[entry_id] = mount
            continue
        merged: dict = {}
        for name, value in mount.items():
            merged[name] = value
            if name == "route_params":
                merged["page_slots"] = page_slots[entry_id]
        if "page_slots" not in merged:
            merged = {"page_slots": page_slots[entry_id], **merged}
        out[entry_id] = merged
    return out


def composite_pages() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry_id, page in CONTAINER_PAGES.items():
        react = REACT_COMPOSITE_PAGES[entry_id]
        props = react["props"]
        out[entry_id] = {
            "page": react["page"],
            "pairs": {key: props[key] for key in page.requires},
            "optional": {key: props[key] for key in page.optional},
        }
    return out


def monolith_container_roots() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for root_id, root in CONTAINER_ROOTS.items():
        local = REACT_CONTAINER_ROOTS[root_id]
        out[root_id] = {
            "id": root.id,
            "labelKey": root.label_key,
            "icon": react_icon(root.icon, entry_id=root.id),
            "route": {"path": root.path},
            "component": {"export": local, "subpath": "."},
            "placement": {"level": "top"},
            "menuVisibleDefault": root.menu_visible_default,
            "requiresAuth": root.requires_auth,
            "surface": root.surface,
            "order": root.order,
            "_local": local,
        }
    return out


def nav_badge_sources() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key, brick in BRICK_UI.items():
        for entry_id, signal in brick.badges:
            react = REACT_BADGES[(entry_id, signal)]
            out[entry_id] = {
                "pair": key,
                "package": REACT_BINDINGS[key].package,
                "hook": react["hook"],
                "var": react["var"],
            }
    return out


FRONTEND_REACT_LIBS: dict[str, dict] = frontend_react_libs()
NAV_ENTRY_MOUNTS: dict[str, dict] = nav_entry_mounts()
COMPOSITE_PAGES: dict[str, dict] = composite_pages()
MONOLITH_CONTAINER_ROOTS: dict[str, dict] = monolith_container_roots()
NAV_BADGE_SOURCES: dict[str, dict] = nav_badge_sources()
NAV_ICON_REGISTRY: frozenset[str] = frozenset(REACT_ICONS.values())


def react_entries(modules) -> list[dict]:
    """The selected modules' React pairs, in selection order, in the shape the
    React renderers consume (``{"key": <brick>, **pair}``).

    Read through ``create_project.FRONTEND_REACT_LIBS`` (the same object as
    the view above) so a caller that substitutes the table there is honoured."""
    from ...create_project import _frontend_react_entries

    return _frontend_react_entries(list(modules))
