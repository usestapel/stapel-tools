"""The navigation model (navigation-templates spec §3): one platform-neutral
``navigation`` block, resolved once per platform into a ``resolved`` plan.

A shell is a header kind plus one destination carrier per size class plus
semantic slots. The block names a preset, or carriers per size class, or
nothing (the default by archetype, §3.5); this module layers default <
preset < explicit keys, refuses what cannot be drawn (NAV-E01…E09, before a
file is written) and computes, per audience and size class, which
destinations a carrier shows and which go to "More".

Pure: no I/O. The emitters, the CLI's dry run (``stapel-nav-plan``) and Studio
call :func:`plan_platform` / :func:`plan_navigation` and get the same answer.

What a platform can draw TODAY is :data:`PLATFORM_CHROMES`: on React the
shipped ``@stapel/shell-react`` chromes (``PublicShell`` for the storefront
header, ``AppShell`` for the app header); on KMP the two ``stapel-shell``
templates. A combination no shipped chrome draws is NAV-E08 with the queue
item that adds it; a DEFAULT that lands on one falls back to the nearest
drawable preset with a note, because a default must always render.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .model import CompositionError

NAV_VERSION = 1

#: compact = [0, tablet), medium = [tablet, desktop), expanded = [desktop, ∞).
SIZE_CLASSES: tuple[str, ...] = ("compact", "medium", "expanded")
#: The `@stapel/tokens` rung each size class starts at.
SIZE_CLASS_RUNGS: dict[str, str] = {"compact": "phone", "medium": "tablet", "expanded": "desktop"}

#: Carrier table (§1.3). ``cap`` counts positions including "More"; None = no cap.
CARRIERS: dict[str, dict] = {
    "bottom-tabs": {
        "validIn": ["compact", "medium"], "min": 2,
        "cap": {"compact": 5, "medium": 5}, "overflow": "more",
    },
    "island": {
        "validIn": ["compact", "medium", "expanded"], "min": 2,
        "cap": {"compact": 5, "medium": 7, "expanded": 7}, "overflow": "more",
    },
    "drawer": {
        "validIn": ["compact", "medium"], "min": 1,
        "cap": {"compact": None, "medium": None}, "overflow": None,
    },
    "rail": {
        "validIn": ["medium", "expanded"], "min": 2,
        "cap": {"medium": 7, "expanded": 7}, "overflow": "more",
    },
    "side-panel": {
        "validIn": ["medium", "expanded"], "min": 1,
        "cap": {"medium": None, "expanded": None}, "overflow": None,
    },
    "top-tabs": {
        "validIn": ["medium", "expanded"], "min": 2,
        "cap": {"medium": 5, "expanded": 7}, "overflow": "more",
    },
    "stack": {
        "validIn": ["compact", "medium", "expanded"], "min": 0,
        "cap": {"compact": 1, "medium": 1, "expanded": 1}, "overflow": None,
    },
}

#: Why a carrier is refused on a size class (printed with NAV-E01).
INVALID_REASONS: dict[tuple[str, str], str] = {
    ("bottom-tabs", "expanded"): (
        "a bar at the bottom of a wide window is far from every pointer path and "
        "wastes the wide axis"),
    ("drawer", "expanded"): "it hides destinations on the one size with room for them",
    ("rail", "compact"): "a phone has no width for it",
    ("side-panel", "compact"): "a phone has no width for it",
    ("top-tabs", "compact"): "a phone has no width for it",
}

HEADERS: tuple[str, ...] = ("app", "storefront", "none")

SEMANTIC_SLOTS: tuple[str, ...] = (
    "brand", "search", "account", "cart", "notifications", "primaryAction",
    "language", "theme", "browse", "footer",
)
EXCLUSIVE_SLOTS: frozenset[str] = frozenset(
    {"search", "browse", "footer", "primaryAction", "account"})

#: The shell's own fillers for a semantic slot: drawn by the chrome itself.
BUILTIN_SLOTS: dict[str, str] = {
    "brand": "shell.brand",
    "theme": "shell.theme",
    "account": "shell.account_entry",
    "footer": "shell.legal_footer",
}

#: The role each installed embed can fill. W0 stand-in for the manifest
#: field `role` (§3.3), which the pair minors of slice R5 publish; until then
#: the table names today's embeds, and an embed it does not name fills only
#: its physical slot.
EMBED_ROLES: dict[str, str] = {
    "auth.account_menu": "account",
    "notifications.bell": "notifications",
    "translate.language_switcher": "language",
    "orders.cart_badge": "cart",
    "workspaces.switcher": "workspace",
    # The header search field of @stapel/search-react (slice R5); fills the
    # search slot the day the pair's mirror carries it.
    "search.box": "search",
}

#: semantic slot -> the role an embed needs to fill it.
SLOT_ROLES: dict[str, str] = {
    "account": "account", "search": "search", "cart": "cart",
    "notifications": "notifications", "language": "language", "theme": "theme",
    "browse": "browse", "footer": "footer", "primaryAction": "action",
}

#: A destination that is not menu-visible -> the destination a person reaches
#: it from by a click. W0 stand-in for the manifest field `via` (§3.3).
VIA: dict[str, str] = {
    "orders.checkout": "orders.cart",
    "listings.compose": "account.root",
    "reservations.schedule": "reservations.agenda",
    # Their content is composed into the settings page; the own routes are
    # the alternative for a host that prefers three pages (profiles-react).
    "profiles.language": "profiles.settings",
    "profiles.notifications": "profiles.settings",
    "search.ranking": "search.results",
}

#: The staff section: this destination and its subtree are shown to the
#: ``staff`` audience (a member whose session says ``is_staff``) only.
STAFF_ROOT = "admin.root"

#: An own component that fills a semantic slot: ``slots.<slot>: "app.<id>"``,
#: bound in the platform's own-pages file like a page.
OWN_SLOTS: frozenset[str] = frozenset({"search"})
_OWN_PREFIX = "app."

#: Legal destinations every screen must reach (a policy disclosure, the
#: personal-data request form): the shell draws them in its footer region.
LEGAL_ENTRIES: tuple[str, ...] = ("moderation.policy", "public.privacy-request")

#: Destinations entered from OUTSIDE the app by design, and from where.
EXTERNAL_ENTRY: dict[str, str] = {
    "auth.qr_confirm": "a QR code scanned on another device",
}

#: Destinations reached through an embed that has not shipped yet: they are
#: routed, and listed under `resolved.pending` until the embed exists.
PENDING_EMBED_ENTRY: dict[str, str] = {
    "search.results": "search.box (the header search field, @stapel/search-react, slice R5)",
}

PRESETS: dict[str, dict] = {
    "shop": {
        "titleKey": "nav.preset.shop.title", "summaryKey": "nav.preset.shop.summary",
        "header": "storefront",
        "primary": {"compact": "island", "medium": "top-tabs", "expanded": "top-tabs"},
        "platforms": {"kmp": {"primary": {
            "compact": "bottom-tabs", "medium": "rail", "expanded": "side-panel"}}},
        "archetypes": ["shop", "classified", "booking"],
    },
    "app": {
        "titleKey": "nav.preset.app.title", "summaryKey": "nav.preset.app.summary",
        "header": "app",
        "primary": {"compact": "bottom-tabs", "medium": "rail", "expanded": "side-panel"},
        "platforms": {},
        "archetypes": ["social"],
    },
    "dashboard": {
        "titleKey": "nav.preset.dashboard.title",
        "summaryKey": "nav.preset.dashboard.summary",
        "header": "app",
        "primary": {"compact": "drawer", "medium": "rail", "expanded": "side-panel"},
        "platforms": {"kmp": {"primary": {
            "compact": "bottom-tabs", "medium": "rail", "expanded": "side-panel"}}},
        "archetypes": [],
    },
    "site": {
        "titleKey": "nav.preset.site.title", "summaryKey": "nav.preset.site.summary",
        "header": "storefront",
        "primary": {"compact": "drawer", "medium": "top-tabs", "expanded": "top-tabs"},
        "slots": {"browse": "none"},
        "platforms": {"kmp": {"primary": {
            "compact": "bottom-tabs", "medium": "rail", "expanded": "top-tabs"}}},
        "archetypes": [],
    },
    "dock": {
        "titleKey": "nav.preset.dock.title", "summaryKey": "nav.preset.dock.summary",
        "header": "app",
        "primary": {"compact": "island", "medium": "island", "expanded": "island"},
        "platforms": {},
        "archetypes": [],
    },
    "tool": {
        "titleKey": "nav.preset.tool.title", "summaryKey": "nav.preset.tool.summary",
        "header": "app",
        "primary": {"compact": "stack", "medium": "stack", "expanded": "stack"},
        "platforms": {},
        "archetypes": [],
    },
}

#: Legacy template ids, resolved before validation (§3.4).
SHELL_ALIASES: dict[str, dict] = {
    "topbar-sidenav": {"preset": "dashboard"},
    "topbar-only": {"preset": "shop"},
    "sidenav-only": {"preset": "dashboard", "header": "none"},
}
MOBILE_SHELL_ALIASES: dict[str, str] = {"AdaptiveTabs": "app", "Stack": "tool"}

#: What each platform draws today, chrome by chrome: the header kind and the
#: carriers per size class that chrome renders. A resolved plan maps to the
#: first chrome that draws it, or is NAV-E08.
PLATFORM_CHROMES: dict[str, list[dict]] = {
    "react": [
        {
            # PublicShell: top bar + browse bar from `chromeFrom` (768) up; below
            # it the dock (`phoneChrome="dock"`) or the hamburger sheet.
            "chrome": "PublicShell", "template": "topbar-only", "headers": ["storefront"],
            "primary": {"compact": ["island", "drawer"], "medium": ["top-tabs"],
                        "expanded": ["top-tabs"]},
            # Where a destination past the cap is still reached: the wide
            # horizontal menu folds it into its overflow item; the 0.20.1
            # dock shows the first five and has no More.
            "overflow": {"compact": [], "medium": ["top-tabs"], "expanded": ["top-tabs"]},
            # The chrome draws one tree per audience: the staff section is
            # in the staff tree only.
            "staffSection": "hidden",
            "noMore": {"island": "@stapel/shell-react 0.20.1 NavDock has no More "
                                 "(NavShell adds it)"},
        },
        {
            # AppShell: drawer below 768, glyph rail from 768, labelled sider from 1200.
            "chrome": "AppShell", "template": "topbar-sidenav", "headers": ["app"],
            "primary": {"compact": ["drawer"], "medium": ["rail"],
                        "expanded": ["side-panel"]},
            # The glyph rail is a collapsed menu: every destination is in it.
            "overflow": {"compact": [], "medium": ["rail"], "expanded": []},
            # AppShell lists the staff section for everyone, switched off with
            # its staff-only reason for a member (`staff` prop).
            "staffSection": "disabled",
        },
    ],
    "kmp": [
        {
            # stapel-shell AdaptiveTabs: BottomBar / Rail / Drawer (permanent).
            "chrome": "AdaptiveTabs", "template": "AdaptiveTabs",
            "headers": ["app", "storefront"],
            "primary": {"compact": ["bottom-tabs"], "medium": ["rail"],
                        "expanded": ["side-panel"]},
            # BottomBar's last item is "More" on overflow; the rail lists all.
            "overflow": {"compact": ["bottom-tabs"], "medium": ["rail"], "expanded": []},
            "staffSection": "hidden",
        },
        {
            "chrome": "Stack", "template": "Stack", "headers": ["app", "storefront"],
            "primary": {"compact": ["stack"], "medium": ["stack"], "expanded": ["stack"]},
            "overflow": {"compact": [], "medium": [], "expanded": []},
            "staffSection": "hidden",
        },
    ],
}

#: The queue item that makes the rest drawable, per platform (NAV-E08).
QUEUE_ITEMS: dict[str, str] = {
    "react": "NavShell in @stapel/shell-react (navigation slices R3/R4, then T3)",
    "kmp": "plan-driven StapelShell in stapel-kmp (navigation slices K1-K3, then T4)",
}

#: When a DEFAULT preset is not drawable on a platform, the preset used instead.
DEFAULT_FALLBACK: dict[str, dict[str, str]] = {
    "react": {"app": "dashboard", "tool": "dashboard", "dock": "dashboard", "site": "shop"},
    "kmp": {"site": "app", "dock": "app"},
}

STOREFRONT_COMPOSITES: frozenset[str] = frozenset({"shop", "classified", "booking"})
STOREFRONT_BRICKS: frozenset[str] = frozenset({"categories", "listings", "search", "orders"})

_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
_SLOT_VALUE_RE = re.compile(r"^(auto|none|[a-z][a-z0-9_.-]*)$")
_PRESET_RE = re.compile(r"^[a-z][a-z0-9-]*$")


class NavigationError(CompositionError):
    """A navigation block the scaffold refuses; ``code`` is NAV-Exx."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def _carriers_valid_in(size_class: str) -> list[str]:
    return [cid for cid, c in CARRIERS.items() if size_class in c["validIn"]]


def navigation_schema() -> dict:
    """The JSON Schema of the block (draft 2020-12), generated from
    :data:`CARRIERS`; ``data/navigation.schema.json`` is this, written."""
    primary = {
        "type": "object", "additionalProperties": False,
        "properties": {c: {"enum": _carriers_valid_in(c)} for c in SIZE_CLASSES},
    }
    slot_value = {"type": "string", "pattern": _SLOT_VALUE_RE.pattern}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://usestapel.dev/schema/navigation.v1.json",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "version": {"const": NAV_VERSION},
            "preset": {"type": "string", "pattern": _PRESET_RE.pattern},
            "header": {"enum": list(HEADERS)},
            "primary": primary,
            "destinations": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "pinned": {"$ref": "#/$defs/ids"},
                    "hidden": {"$ref": "#/$defs/ids"},
                    "order": {"type": "object", "additionalProperties": {"type": "integer"}},
                    "groups": {"type": "array", "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["labelKey", "items"],
                        "properties": {"labelKey": {"type": "string"},
                                       "items": {"$ref": "#/$defs/ids"}}}},
                },
            },
            "slots": {
                "type": "object", "additionalProperties": False,
                "properties": {slot: slot_value for slot in SEMANTIC_SLOTS},
            },
            "guest": {"type": "object", "additionalProperties": False,
                      "properties": {"memberDestinations": {"enum": ["wall", "hide"]},
                                     "wall": {"$ref": "#/$defs/ids"},
                                     "hide": {"$ref": "#/$defs/ids"}}},
            "secondary": {"enum": ["auto", "breadcrumbs", "none"]},
            "platforms": {
                "type": "object", "additionalProperties": False,
                "properties": {p: {"$ref": "#/$defs/override"} for p in ("react", "kmp")},
            },
        },
        "$defs": {
            "ids": {"type": "array", "uniqueItems": True,
                    "items": {"type": "string", "pattern": _ID_RE.pattern}},
            "override": {"type": "object", "additionalProperties": False,
                         "properties": {"header": {"$ref": "#/properties/header"},
                                        "primary": {"$ref": "#/properties/primary"},
                                        "slots": {"$ref": "#/properties/slots"}}},
        },
    }


def _schema_error(path: str, message: str) -> NavigationError:
    return NavigationError("NAV-E00", f"navigation{path}: {message}")


def _check_keys(value: Any, allowed: Iterable[str], path: str) -> dict:
    if not isinstance(value, dict):
        raise _schema_error(path, "expected an object")
    allowed = list(allowed)
    unknown = [k for k in value if k not in allowed]
    if unknown:
        raise _schema_error(
            path, f"unknown key(s) {', '.join(map(repr, unknown))}; known: {', '.join(allowed)}")
    return value


def _check_ids(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _schema_error(path, "expected a list of ids")
    bad = [v for v in value if not _ID_RE.match(v)]
    if bad:
        raise _schema_error(path, f"not an id: {', '.join(map(repr, bad))}")
    if len(set(value)) != len(value):
        raise _schema_error(path, "ids repeat")
    return value


def _check_primary(value: Any, path: str) -> None:
    _check_keys(value, SIZE_CLASSES, path)
    for size_class, carrier in value.items():
        if carrier not in CARRIERS:
            raise NavigationError(
                "NAV-E06",
                f"navigation{path}.{size_class}: {carrier!r} is not a carrier; carriers: "
                + ", ".join(CARRIERS))
        if size_class not in CARRIERS[carrier]["validIn"]:
            raise _invalid_carrier(carrier, size_class, f"navigation{path}.{size_class}")


def _invalid_carrier(carrier: str, size_class: str, where: str) -> NavigationError:
    reason = INVALID_REASONS.get((carrier, size_class), "it is not valid there")
    return NavigationError(
        "NAV-E01",
        f"{where}: {carrier} is not valid on {size_class} ({reason}); valid on "
        f"{size_class}: {', '.join(_carriers_valid_in(size_class))}")


def _check_slots(value: Any, path: str) -> None:
    _check_keys(value, SEMANTIC_SLOTS, path)
    for slot, choice in value.items():
        if not isinstance(choice, str) or not _SLOT_VALUE_RE.match(choice):
            raise _schema_error(f"{path}.{slot}", "expected auto, none or an id")


def validate_block(block: Any) -> dict:
    """Structural check of a navigation block (the schema, in code, plus
    NAV-E01 for a carrier outside its size class). Returns the block."""
    if block is None:
        return {}
    _check_keys(block, navigation_schema()["properties"], "")
    if "version" in block and block["version"] != NAV_VERSION:
        raise _schema_error(".version", f"expected {NAV_VERSION}")
    if "preset" in block:
        if not isinstance(block["preset"], str) or not _PRESET_RE.match(block["preset"]):
            raise _schema_error(".preset", "expected a preset id")
        if block["preset"] not in PRESETS:
            raise NavigationError(
                "NAV-E06", f"navigation.preset {block['preset']!r} is not a preset; presets: "
                + ", ".join(PRESETS))
    if "header" in block and block["header"] not in HEADERS:
        raise _schema_error(".header", f"one of {', '.join(HEADERS)}")
    if "primary" in block:
        _check_primary(block["primary"], ".primary")
    dest = block.get("destinations")
    if dest is not None:
        _check_keys(dest, ("pinned", "hidden", "order", "groups"), ".destinations")
        for key in ("pinned", "hidden"):
            if key in dest:
                _check_ids(dest[key], f".destinations.{key}")
        order = dest.get("order", {})
        if not isinstance(order, dict) or not all(
                isinstance(v, int) and not isinstance(v, bool) for v in order.values()):
            raise _schema_error(".destinations.order", "expected {id: integer}")
        groups = dest.get("groups", [])
        if not isinstance(groups, list):
            raise _schema_error(".destinations.groups", "expected a list")
        for i, group in enumerate(groups):
            _check_keys(group, ("labelKey", "items"), f".destinations.groups[{i}]")
            if not isinstance(group.get("labelKey"), str) or "items" not in group:
                raise _schema_error(f".destinations.groups[{i}]", "needs labelKey and items")
            _check_ids(group["items"], f".destinations.groups[{i}].items")
    if "slots" in block:
        _check_slots(block["slots"], ".slots")
    guest = block.get("guest")
    if guest is not None:
        _check_keys(guest, ("memberDestinations", "wall", "hide"), ".guest")
        if guest.get("memberDestinations", "wall") not in ("wall", "hide"):
            raise _schema_error(".guest.memberDestinations", "wall or hide")
        for key in ("wall", "hide"):
            if key in guest:
                _check_ids(guest[key], f".guest.{key}")
        both = set(guest.get("wall", ())) & set(guest.get("hide", ()))
        if both:
            raise _schema_error(".guest", f"{', '.join(sorted(both))} is both walled and hidden")
    if block.get("secondary", "auto") not in ("auto", "breadcrumbs", "none"):
        raise _schema_error(".secondary", "auto, breadcrumbs or none")
    platforms = block.get("platforms")
    if platforms is not None:
        _check_keys(platforms, ("react", "kmp"), ".platforms")
        for name, override in platforms.items():
            _check_keys(override, ("header", "primary", "slots"), f".platforms.{name}")
            if "header" in override and override["header"] not in HEADERS:
                raise _schema_error(f".platforms.{name}.header", f"one of {', '.join(HEADERS)}")
            if "primary" in override:
                _check_primary(override["primary"], f".platforms.{name}.primary")
            if "slots" in override:
                _check_slots(override["slots"], f".platforms.{name}.slots")
    return block


# ---------------------------------------------------------------------------
# flags and aliases
# ---------------------------------------------------------------------------


def block_from_flags(
    *,
    preset: Optional[str] = None,
    carriers: Optional[list[str]] = None,
    header: Optional[str] = None,
    pin: Optional[str] = None,
    hide: Optional[str] = None,
    order: Optional[list[str]] = None,
    slots: Optional[list[str]] = None,
    guest: Optional[str] = None,
    mobile_preset: Optional[str] = None,
    mobile_carriers: Optional[list[str]] = None,
) -> dict:
    """The block the ``--nav*`` flags describe (only the keys given)."""
    block: dict = {}
    if preset:
        block["preset"] = preset
    if header:
        block["header"] = header
    primary = _parse_pairs(carriers, "--nav", "CLASS=CARRIER")
    if primary:
        block["primary"] = primary
    dest: dict = {}
    if pin:
        dest["pinned"] = [p.strip() for p in pin.split(",") if p.strip()]
    if hide:
        dest["hidden"] = [p.strip() for p in hide.split(",") if p.strip()]
    if order:
        parsed = _parse_pairs(order, "--nav-order", "ID=N")
        try:
            dest["order"] = {k: int(v) for k, v in parsed.items()}
        except ValueError:
            raise _schema_error(".destinations.order", "--nav-order expects ID=N") from None
    if dest:
        block["destinations"] = dest
    slot_choices = _parse_pairs(slots, "--nav-slot", "SLOT=ID|auto|none")
    if slot_choices:
        block["slots"] = slot_choices
    if guest:
        block["guest"] = {"memberDestinations": guest}
    kmp: dict = {}
    if mobile_preset:
        if mobile_preset not in PRESETS:
            raise NavigationError(
                "NAV-E06", f"--mobile-nav-preset {mobile_preset!r} is not a preset; presets: "
                + ", ".join(PRESETS))
        kmp["primary"] = dict(_preset_platform(mobile_preset, "kmp")["primary"])
    mobile_primary = _parse_pairs(mobile_carriers, "--mobile-nav", "CLASS=CARRIER")
    if mobile_primary:
        kmp.setdefault("primary", {}).update(mobile_primary)
    if kmp:
        block["platforms"] = {"kmp": kmp}
    return block


def _parse_pairs(values: Optional[list[str]], flag: str, shape: str) -> dict:
    out: dict = {}
    for raw in values or ():
        key, sep, value = raw.partition("=")
        if not sep or not key.strip() or not value.strip():
            raise _schema_error("", f"{flag} {raw!r}: expected {shape}")
        out[key.strip()] = value.strip()
    return out


def merge_blocks(base: dict, top: dict) -> dict:
    """*top* over *base*, key by key (dicts merge, everything else replaces)."""
    out = copy.deepcopy(base)
    for key, value in (top or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_blocks(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def alias_block(shell: Optional[str] = None, mobile_shell: Optional[str] = None) -> dict:
    """The block a legacy ``--shell`` / ``--mobile-shell`` stands for."""
    block: dict = {}
    if shell is not None:
        if shell not in SHELL_ALIASES:
            raise NavigationError(
                "NAV-E06", f"--shell {shell!r} is not a shell template; one of "
                + ", ".join(SHELL_ALIASES))
        block.update(SHELL_ALIASES[shell])
    if mobile_shell is not None:
        preset = MOBILE_SHELL_ALIASES.get(mobile_shell)
        if preset is None:
            raise NavigationError(
                "NAV-E06", f"--mobile-shell {mobile_shell!r}: one of "
                + ", ".join(MOBILE_SHELL_ALIASES))
        block["platforms"] = {"kmp": {"primary": dict(
            _preset_platform(preset, "kmp")["primary"])}}
    return block


# ---------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------


def default_preset(
    *, modules: Iterable[str], has_accounts: bool, landing: bool, candidates: int,
) -> str:
    """§3.5, first match: storefront → ``shop``; social → ``app``; no
    accounts and at most one destination → ``tool`` (``site`` with a
    landing); otherwise ``dashboard``."""
    selected = set(modules)
    if selected & STOREFRONT_COMPOSITES or len(selected & STOREFRONT_BRICKS) >= 2:
        return "shop"
    if "social" in selected or (
            selected & {"chat", "feed"} and not selected & STOREFRONT_BRICKS):
        return "app"
    if not has_accounts and candidates <= 1:
        return "site" if landing else "tool"
    return "dashboard"


def _preset_platform(preset: str, platform: str) -> dict:
    spec = PRESETS[preset]
    out = {"header": spec["header"], "primary": dict(spec["primary"]),
           "slots": dict(spec.get("slots", {}))}
    override = spec.get("platforms", {}).get(platform, {})
    for key in ("header", "primary", "slots"):
        if key in override:
            out[key] = merge_blocks(out[key], override[key]) if isinstance(
                out[key], dict) else override[key]
    return out


def chrome_for(platform: str, header: str, primary: dict) -> Optional[dict]:
    """The shipped chrome that draws *header* + *primary* on *platform*."""
    for chrome in PLATFORM_CHROMES[platform]:
        if header not in chrome["headers"]:
            continue
        if all(primary[c] in chrome["primary"][c] for c in SIZE_CLASSES):
            return chrome
    return None


# ---------------------------------------------------------------------------
# destinations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Destination:
    """One nav entry as the planner reads it (platform-neutral)."""

    id: str
    top: bool
    parent: Optional[str]
    menu_visible: bool
    surface: str            # "public" | "member"
    order: int
    path: str = ""
    short_label: bool = False
    action: bool = False


def destinations_from_manifest(entries: Iterable[dict]) -> list[Destination]:
    """Destinations from nav-manifest-shaped entries (React's mirrors)."""
    out = []
    for e in entries:
        placement = e.get("placement", {})
        surface = e.get("surface") or ("member" if e.get("requiresAuth") else "public")
        out.append(Destination(
            id=e["id"], top=placement.get("level", "top") == "top",
            parent=placement.get("parentId"),
            menu_visible=bool(e.get("menuVisibleDefault", True)),
            surface="member" if surface == "member" else "public",
            order=int(e.get("order", 0)), path=e.get("route", {}).get("path", ""),
            short_label="shortLabelKey" in e, action=bool(e.get("action")),
        ))
    return out


def _walled(entry_id: str, guest_mode: str, wall: frozenset = frozenset(),
            hide: frozenset = frozenset()) -> bool:
    """Does a guest see this member destination (as a wall to sign-in)?"""
    if entry_id in wall:
        return True
    return guest_mode == "wall" and entry_id not in hide


def _candidates(dests: list[Destination], audience: str, guest_mode: str,
                hidden: set[str], order: dict[str, int], action_id: Optional[str],
                *, staff: frozenset = frozenset(), wall: frozenset = frozenset(),
                hide: frozenset = frozenset()) -> list[str]:
    tops = [d for d in dests if d.top and d.menu_visible and d.id not in hidden
            and d.id != action_id]
    if audience != "staff":
        tops = [d for d in tops if d.id not in staff]
    if audience == "guest":
        tops = [d for d in tops
                if d.surface == "public" or _walled(d.id, guest_mode, wall, hide)]
    tops.sort(key=lambda d: (order.get(d.id, d.order), d.id))
    return [d.id for d in tops]


def _fit(candidates: list[str], carrier: Optional[str], size_class: str,
         pinned: list[str]) -> tuple[list[str], list[str]]:
    if carrier is None:
        return [], list(candidates)
    cap = CARRIERS[carrier]["cap"].get(size_class)
    if cap is None or carrier == "stack":
        return list(candidates), []
    if pinned:
        visible = [p for p in pinned if p in candidates]
        return visible, [c for c in candidates if c not in visible]
    if len(candidates) <= cap:
        return list(candidates), []
    return candidates[:cap - 1], candidates[cap - 1:]


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------


@dataclass
class PlatformNavigation:
    """One platform's resolved navigation."""

    platform: str
    preset: Optional[str]
    source: str                        # "default" | "preset" | "explicit"
    chrome: str
    template: str
    header: str
    primary: dict
    resolved: dict
    excluded_embeds: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def effective(block: dict, platform: str, default: str) -> tuple[dict, Optional[str], str]:
    """The block layered default < preset < explicit, the platform override
    applied; returns ``(effective, preset id, source)``."""
    block = block or {}
    preset = block.get("preset")
    source = "preset" if preset else (
        "explicit" if ("primary" in block or "header" in block) else "default")
    base_id = preset or default
    eff = merge_blocks(_preset_platform(base_id, platform),
                       {k: v for k, v in block.items()
                        if k in ("header", "primary", "slots", "destinations", "guest",
                                 "secondary")})
    override = (block.get("platforms") or {}).get(platform)
    if override:
        eff = merge_blocks(eff, override)
    if preset is None and source == "explicit":
        matching = [pid for pid in PRESETS
                    if _preset_platform(pid, platform)["primary"] == eff["primary"]
                    and _preset_platform(pid, platform)["header"] == eff["header"]]
        preset = matching[0] if matching else None
    elif preset is None:
        preset = default
    return eff, preset, source


def plan_platform(  # noqa: C901
    block: Optional[dict],
    *,
    platform: str,
    destinations: list[Destination],
    embeds: Iterable[str] = (),
    modules: Iterable[str] = (),
    auth_model: Optional[str] = None,
    has_sign_in: bool = False,
    sign_in_id: Optional[str] = None,
    landing: bool = False,
    size_classes: Optional[dict] = None,
    strict: bool = False,
    pending: Iterable[str] = (),
    own_bound: Optional[Iterable[str]] = None,
) -> PlatformNavigation:
    """Resolve *block* for *platform* over *destinations*; raises
    :class:`NavigationError` (NAV-E01…E10) for what cannot be drawn.

    *own_bound* is the set of own ids the platform binds (``app.pages.ts``),
    which an own slot component (``slots.search: "app.<id>"``) must be in;
    ``None`` = the platform has no own-component binding at all."""
    block = validate_block(block or {})
    has_accounts = auth_model not in (None, "none")
    embeds = list(embeds)
    probe_candidates = _candidates(destinations, "member" if has_accounts else "guest",
                                   "wall", set(), {}, None)
    default = default_preset(modules=modules, has_accounts=has_accounts, landing=landing,
                             candidates=len(probe_candidates))
    # Ruling 7 and its addendum: a guest of a storefront archetype sees the
    # public destinations only; elsewhere member destinations stay as a wall.
    guest_mode_default = "hide" if auth_model == "internal" or default == "shop" else "wall"
    notes: list[str] = []
    eff, preset, source = effective(block, platform, default)
    if source == "default":
        if chrome_for(platform, eff["header"], eff["primary"]) is None:
            fallback = DEFAULT_FALLBACK.get(platform, {}).get(default)
            if fallback is not None:
                notes.append(
                    f"default preset {default} is not drawn on {platform} yet "
                    f"(queued: {QUEUE_ITEMS[platform]}); {fallback} is used")
                eff, preset, _ = effective(block, platform, fallback)
                default = fallback

    header = eff["header"]
    primary = eff["primary"]
    for size_class in SIZE_CLASSES:
        carrier = primary.get(size_class)
        if carrier is None:
            raise _schema_error(f".primary.{size_class}", "no carrier")
        if size_class not in CARRIERS[carrier]["validIn"]:
            raise _invalid_carrier(carrier, size_class, f"primary.{size_class}")

    # NAV-E05: a header-less shell needs a carrier that carries brand and account.
    if header == "none" and not any(primary[c] in ("rail", "side-panel") for c in SIZE_CLASSES):
        raise NavigationError(
            "NAV-E05",
            "header: none needs rail or side-panel on some size class (they carry the "
            "brand and the account entry themselves); chosen: "
            + ", ".join(f"{c}={primary[c]}" for c in SIZE_CLASSES))

    dest = eff.get("destinations") or {}
    pinned = list(dest.get("pinned", []))
    hidden = set(dest.get("hidden", []))
    order = dict(dest.get("order", {}))
    known = {d.id: d for d in destinations}
    home_id = "shell.home"

    # NAV-E06: unknown or misplaced ids.
    for key, ids in (("pinned", pinned), ("hidden", sorted(hidden)), ("order", list(order))):
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise NavigationError(
                "NAV-E06",
                f"destinations.{key}: unknown id(s) {', '.join(unknown)}; destinations: "
                + (", ".join(sorted(known)) or "none"))
    for group in dest.get("groups", []):
        unknown = [i for i in group["items"] if i not in known]
        if unknown:
            raise NavigationError(
                "NAV-E06", f"destinations.groups {group['labelKey']}: unknown id(s) "
                + ", ".join(unknown))
    not_top = [p for p in pinned if not known[p].top]
    if not_top:
        raise NavigationError(
            "NAV-E06", f"destinations.pinned: {', '.join(not_top)} is not a top-level "
            "destination; only top-level destinations sit on a bar")
    forbidden = [i for i in hidden if i == sign_in_id or i == home_id]
    if forbidden:
        raise NavigationError(
            "NAV-E06", f"destinations.hidden: {', '.join(forbidden)} cannot be hidden "
            "(the home destination and the sign-in route are always reachable)")

    # Semantic slots.
    slot_choice = {s: "auto" for s in SEMANTIC_SLOTS}
    slot_choice.update(eff.get("slots") or {})
    roles: dict[str, list[str]] = {}
    for embed_id in embeds:
        role = EMBED_ROLES.get(embed_id)
        if role:
            roles.setdefault(role, []).append(embed_id)
    action_ids = [d.id for d in destinations if d.action]
    if slot_choice["account"] == "none" and has_sign_in:
        raise NavigationError(
            "NAV-E04",
            "slots.account: none — a product with accounts shows its sign-in entry; "
            f"the auth model {auth_model} has a sign-in route")
    resolved_slots: dict[str, Optional[str]] = {}
    excluded: list[str] = []
    for slot in SEMANTIC_SLOTS:
        choice = slot_choice[slot]
        role = SLOT_ROLES.get(slot)
        offered = roles.get(role, []) if role else []
        if slot == "primaryAction":
            offered = action_ids
        if choice == "none":
            resolved_slots[slot] = None
            excluded.extend(offered)
            continue
        if choice != "auto" and choice.startswith(_OWN_PREFIX):
            if slot not in OWN_SLOTS:
                raise NavigationError(
                    "NAV-E06", f"slots.{slot}={choice}: an own component fills only "
                    + ", ".join(sorted(OWN_SLOTS)))
            if own_bound is None:
                notes.append(f"{slot}: {choice} is an own component, which {platform} cannot "
                             "bind yet — slot empty")
                resolved_slots[slot] = None
            elif choice in set(own_bound):
                resolved_slots[slot] = choice
            elif strict:
                raise NavigationError(
                    "NAV-E09", f"slots.{slot}={choice}: no binding — add {choice} to "
                    "src/app.pages.ts (APP_PAGES)")
            else:
                notes.append(f"{slot}: {choice} has no binding in src/app.pages.ts — slot "
                             "empty until it does")
                resolved_slots[slot] = None
            excluded.extend(offered)
            continue
        if choice != "auto":
            if slot == "primaryAction":
                if choice not in known:
                    raise NavigationError(
                        "NAV-E06", f"slots.primaryAction={choice}: not a destination")
                resolved_slots[slot] = choice
                continue
            if choice in BUILTIN_SLOTS.values() and BUILTIN_SLOTS.get(slot) == choice:
                resolved_slots[slot] = choice
                continue
            if choice not in embeds:
                raise NavigationError(
                    "NAV-E06",
                    f"slots.{slot}={choice}: no installed embed {choice!r}; offered for "
                    f"{slot}: {', '.join(offered) or 'none'}")
            if EMBED_ROLES.get(choice) != role:
                raise NavigationError(
                    "NAV-E06",
                    f"slots.{slot}={choice}: that embed's role is "
                    f"{EMBED_ROLES.get(choice) or 'none'}, not {role}")
            resolved_slots[slot] = choice
            excluded.extend(o for o in offered if o != choice)
            continue
        if slot in EXCLUSIVE_SLOTS and len(offered) > 1:
            raise NavigationError(
                "NAV-E07",
                f"slots.{slot}: {', '.join(sorted(offered))} all claim it; pick one with "
                f"--nav-slot {slot}=<id> or none")
        if offered:
            resolved_slots[slot] = offered[0]
        elif slot == "account":
            resolved_slots[slot] = BUILTIN_SLOTS["account"] if has_sign_in else None
        elif slot == "footer":
            resolved_slots[slot] = BUILTIN_SLOTS["footer"] if header == "storefront" else None
        elif slot in BUILTIN_SLOTS:
            resolved_slots[slot] = BUILTIN_SLOTS[slot]
        else:
            resolved_slots[slot] = None
            if slot in ("search", "browse") and header == "storefront":
                notes.append(f"{slot}: no installed embed with role {role} — slot empty")
            elif slot in ("cart", "notifications"):
                pass
    if resolved_slots.get("account") and resolved_slots["account"] != BUILTIN_SLOTS["account"]:
        notes.append(
            f"account: {resolved_slots['account']} for a member; a guest sees the shell's "
            "sign-in entry")
    action_id = resolved_slots.get("primaryAction")

    # Audiences.
    guest_block = eff.get("guest") or {}
    guest_mode = guest_block.get("memberDestinations", guest_mode_default)
    guest_wall = frozenset(guest_block.get("wall", ()))
    guest_hide = frozenset(guest_block.get("hide", ()))
    unknown = sorted(i for i in guest_wall | guest_hide if i not in known)
    if unknown:
        raise NavigationError(
            "NAV-E06", f"guest.wall / guest.hide: unknown id(s) {', '.join(unknown)}")
    early_chrome = chrome_for(platform, header, primary)
    staff_root = known.get(STAFF_ROOT)
    staff = frozenset({STAFF_ROOT}) if (
        has_accounts and staff_root is not None and staff_root.top
        and (early_chrome is None or early_chrome["staffSection"] == "hidden")) else frozenset()
    audience_names = ["guest", "member"] if has_accounts else ["guest"]
    if staff:
        audience_names.append("staff")
    capped = [
        (c, CARRIERS[primary[c]]["cap"][c]) for c in SIZE_CLASSES
        if CARRIERS[primary[c]]["overflow"] == "more"
    ]
    if pinned and capped:
        smallest = min(cap for _, cap in capped)
        if len(pinned) > smallest:
            where = ", ".join(f"{c}={primary[c]} (cap {cap})" for c, cap in capped)
            raise NavigationError(
                "NAV-E02",
                f"destinations.pinned has {len(pinned)} entries; the capped carriers allow "
                f"{smallest}: {where} — use drawer / side-panel, or pin fewer")

    def candidates_of(audience: str) -> list[str]:
        if audience == "guest" and auth_model == "internal":
            return []
        return _candidates(destinations, audience, guest_mode, hidden, order, action_id,
                           staff=staff, wall=guest_wall, hide=guest_hide)

    def fit_all(primary: dict) -> tuple[dict, dict, list[str]]:
        audiences: dict[str, dict] = {}
        short_missing: dict[str, None] = {}
        fit_notes: list[str] = []
        for audience in audience_names:
            internal_guest = audience == "guest" and auth_model == "internal"
            cands = candidates_of(audience)
            per_class: dict[str, dict] = {}
            for size_class in SIZE_CLASSES:
                carrier = "stack" if internal_guest else primary[size_class]
                if carrier == "stack" and len(cands) >= 2:
                    raise NavigationError(
                        "NAV-E03",
                        f"{size_class}=stack, and the {audience} has {len(cands)} destinations "
                        f"({', '.join(cands)}) — choose a carrier or hide destinations")
                drawn: Optional[str] = carrier
                if carrier != "stack" and len(cands) < CARRIERS[carrier]["min"]:
                    fit_notes.append(
                        f"{audience} {size_class}: {carrier} is not drawn — {len(cands)} "
                        f"destination(s), it needs {CARRIERS[carrier]['min']}; the header "
                        "carries the brand and the account entry")
                    drawn = None
                pins = pinned if drawn and CARRIERS[drawn]["overflow"] == "more" else []
                cap = CARRIERS[drawn]["cap"][size_class] if drawn else None
                if pins and cap is not None and len(pins) > cap - 1 and len(cands) > len(pins):
                    raise NavigationError(
                        "NAV-E02",
                        f"destinations.pinned has {len(pins)} entries and {size_class}={drawn} "
                        f"has {cap} positions; with destinations left over one goes to More — "
                        "pin fewer")
                visible, more = _fit(cands, drawn, size_class, pins)
                per_class[size_class] = {"carrier": drawn, "visible": visible, "more": more}
                for entry_id in visible:
                    if drawn and drawn != "stack" and CARRIERS[drawn]["cap"][size_class] \
                            and not known[entry_id].short_label:
                        short_missing.setdefault(entry_id, None)
            audiences[audience] = per_class
            if internal_guest:
                fit_notes.append("guest: internal accounts — the sign-in screen inside the "
                                 "shell, header only")
        return audiences, short_missing, fit_notes

    audiences, short_missing, fit_notes = fit_all(primary)
    # A destination past a carrier's cap is reached only where the chrome draws
    # an overflow for that carrier. Where it does not, a default or preset
    # carrier gives way to one that lists everything; an explicit one is refused.
    if early_chrome is not None:
        for size_class in SIZE_CLASSES:
            stranded = _stranded(audiences, early_chrome, size_class)
            if not stranded:
                continue
            carrier = primary[size_class]
            reason = (early_chrome.get("noMore") or {}).get(
                carrier, f"{early_chrome['chrome']} draws no More for {carrier}")
            alternative = next(
                (c for c in early_chrome["primary"][size_class]
                 if c != carrier and CARRIERS[c]["cap"].get(size_class) is None), None)
            explicit = size_class in ((block.get("primary") or {})) or size_class in (
                ((block.get("platforms") or {}).get(platform) or {}).get("primary") or {})
            if explicit or alternative is None:
                raise NavigationError(
                    "NAV-E10",
                    f"primary.{size_class}={carrier}: {', '.join(stranded)} would be "
                    f"unreachable on {size_class} — {reason}; choose "
                    + (alternative or "a carrier that lists every destination")
                    + f" for {size_class}, pin or hide destinations")
            notes.append(
                f"{size_class}: {carrier} -> {alternative} — {', '.join(stranded)} would be "
                f"past the cap of {CARRIERS[carrier]['cap'][size_class]} and {reason}")
            primary = {**primary, size_class: alternative}
            audiences, short_missing, fit_notes = fit_all(primary)
    notes.extend(fit_notes)

    if short_missing:
        notes.append(
            "no shortLabelKey on a destination a capped carrier shows (the full label "
            "is used): " + ", ".join(short_missing))

    # Platform renderability (NAV-E08).
    chrome = chrome_for(platform, header, primary)
    if chrome is None:
        raise NavigationError(
            "NAV-E08",
            f"{platform}: header {header} with "
            + ", ".join(f"{c}={primary[c]}" for c in SIZE_CLASSES)
            + f" is queued on this platform (queue item: {QUEUE_ITEMS[platform]}); drawable "
            f"today: {', '.join(drawable_presets(platform))}")
    if platform == "kmp" and header == "storefront":
        notes.append("kmp: the storefront header is drawn as the app top bar (until K3)")
    _check_reachable(audiences, chrome)

    pending = list(pending)
    if strict and pending:
        raise NavigationError(
            "NAV-E09",
            "own page(s) with no binding: " + ", ".join(pending)
            + " — add each to src/app.pages.ts (APP_PAGES)")

    resolved = {
        "platform": platform,
        "preset": preset,
        "source": source,
        "chrome": chrome["chrome"],
        "sizeClasses": dict(size_classes or {}),
        "header": header,
        "primary": dict(primary),
        "guest": {"memberDestinations": guest_mode,
                  **({"wall": sorted(guest_wall)} if guest_wall else {}),
                  **({"hide": sorted(guest_hide)} if guest_hide else {})},
        "audiences": audiences,
        **({"staff": {"section": STAFF_ROOT, "audience": "staff"}} if staff else {}),
        "slots": resolved_slots,
        "home": home_id,
        "signIn": sign_in_id if has_sign_in else None,
        "bare": [],
        "pending": pending,
        "notes": list(notes),
    }
    return PlatformNavigation(
        platform=platform, preset=preset, source=source, chrome=chrome["chrome"],
        template=chrome["template"], header=header, primary=dict(primary),
        resolved=resolved, excluded_embeds=sorted(set(excluded)), notes=list(notes),
    )


def _stranded(audiences: dict, chrome: dict, size_class: str) -> list[str]:
    """Destinations put in "More" on *size_class* where *chrome* draws none."""
    out: dict[str, None] = {}
    for per_class in audiences.values():
        cell = per_class[size_class]
        if cell["carrier"] is None:
            continue
        if cell["more"] and cell["carrier"] not in chrome["overflow"].get(size_class, ()):
            out.update(dict.fromkeys(cell["more"]))
    return list(out)


def _check_reachable(audiences: dict, chrome: dict) -> None:
    """The planner's invariant: every destination of every audience is
    reachable at every size class — on the carrier, or in an overflow the
    chrome draws. A carrier not drawn for lack of destinations (§3.3 rule 5)
    is the one exception: the header carries what is left."""
    for audience, per_class in audiences.items():
        for size_class, cell in per_class.items():
            if cell["carrier"] is None:
                continue
            stranded = [i for i in cell["more"]
                        if cell["carrier"] not in chrome["overflow"].get(size_class, ())]
            if stranded:
                raise NavigationError(
                    "NAV-E10",
                    f"{audience} {size_class}: {', '.join(stranded)} unreachable — "
                    f"{chrome['chrome']} draws no overflow for {cell['carrier']}")


def drawable_presets(platform: str) -> list[str]:
    """Presets a platform draws today (no NAV-E08)."""
    return [pid for pid in PRESETS
            if chrome_for(platform, _preset_platform(pid, platform)["header"],
                          _preset_platform(pid, platform)["primary"]) is not None]


def size_classes_from_theme(theme_json: str) -> dict:
    """``{compact, medium, expanded}`` -> px, read from the pinned tokens'
    ``scales.breakpoints`` the theme file carries (never a number here)."""
    import json

    breakpoints = json.loads(theme_json)["scales"]["breakpoints"]
    return {c: breakpoints[SIZE_CLASS_RUNGS[c]] for c in SIZE_CLASSES}


def presets_document() -> dict:
    """``nav-presets.json`` (§3.4): the preset table, for Studio's intake."""
    return {
        "version": 1,
        "presets": copy.deepcopy(PRESETS),
        "drawable": {p: drawable_presets(p) for p in ("react", "kmp")},
    }


# ---------------------------------------------------------------------------
# per-composition entry points
# ---------------------------------------------------------------------------


def neutral_destinations(comp) -> list[Destination]:
    """The composition's destinations from the neutral brick registry (the
    platform-free view; a platform may deliver a subset)."""
    from .bricks import CONTAINER_ROOTS

    has_accounts = comp.auth is not None and comp.auth.model != "none" or (
        comp.auth is None and comp.want_auth)
    out: list[Destination] = []
    for brick in comp.bricks:
        for e in brick.nav:
            surface = e.surface or ("member" if e.requires_auth else "public")
            if surface == "member" and not has_accounts:
                continue
            out.append(Destination(
                id=e.id, top=e.level == "top", parent=e.parent_id,
                menu_visible=e.menu_visible_default,
                surface="member" if surface == "member" else "public",
                order=e.order, path=e.path, short_label=e.short_label_key is not None,
            ))
    parents = {d.parent for d in out if not d.top}
    for root_id, root in CONTAINER_ROOTS.items():
        if root_id in parents:
            out.append(Destination(
                id=root.id, top=True, parent=None, menu_visible=root.menu_visible_default,
                surface="member" if root.requires_auth else "public", order=root.order,
                path=root.path,
            ))
    return out


def plan_kmp_navigation(comp) -> PlatformNavigation:
    """The KMP plan of ``comp.navigation`` (echoed into
    ``mobile/stapel.composition.json``; the emitter draws its chrome)."""
    dests = neutral_destinations(comp)
    auth_model = comp.auth.model if comp.auth is not None else None
    has_sign_in = bool(comp.auth is not None and comp.auth.has_login_route)
    nav = plan_platform(
        comp.navigation, platform="kmp", destinations=dests,
        embeds=[e.id for brick in comp.bricks for e in brick.embeds],
        modules=comp.modules, auth_model=auth_model, has_sign_in=has_sign_in,
        sign_in_id="auth.login" if any(d.id == "auth.login" for d in dests) else None,
        landing=comp.landing, size_classes=size_classes_from_theme(comp.theme_json),
        strict=comp.strict,
    )
    return nav


def plan_navigation(comp) -> dict:
    """``{platform: resolved}`` for every platform *comp* requests — the pure
    planner the CLI's dry run (``stapel-nav-plan``) and Studio call. Raises
    :class:`NavigationError` exactly where the scaffold would."""
    from .react.registry import react_entries
    from .react.shell import plan_react_shell

    out: dict = {}
    if comp.project_type == "monolith":
        out["react"] = plan_react_shell(comp, react_entries(comp.modules)).resolved
    if comp.platform("kmp") is not None:
        out["kmp"] = plan_kmp_navigation(comp).resolved
    return out
