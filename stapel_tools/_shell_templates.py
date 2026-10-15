"""Shell templates and the slot vocabulary — a mirror of
``@stapel/shell-react``'s published ``shell-templates.json`` (composition
spec §2), drift-gated by ``tests/test_shell_templates_sync.py`` the same way
the nav mirror is gated by ``scripts/check_nav_manifest_sync.py``.

A template is a named chrome plus the closed list of slots it renders. A
capacity-one slot is listed under ``exclusive``: two embeds claiming it is a
conflict the caller resolves with ``--slot``, never a silent first-wins.
"""
from __future__ import annotations

SHELL_SLOT_IDS: tuple[str, ...] = (
    "topbar.start",
    "topbar.center",
    "topbar.end",
    "sidenav.head",
    "sidenav.foot",
    "sheet.foot",
    "bottombar",
    "browsebar",
    "footer",
    "page.actions",
)

SHELL_TEMPLATES: dict[str, dict] = {
    "topbar-sidenav": {
        "chrome": "AppShell",
        "slots": [
            "topbar.start",
            "topbar.center",
            "topbar.end",
            "sidenav.head",
            "sidenav.foot",
            "sheet.foot",
        ],
        "exclusive": ["topbar.center"],
        "status": "shipped",
    },
    "topbar-only": {
        "chrome": "PublicShell",
        "slots": [
            "topbar.start",
            "topbar.center",
            "topbar.end",
            "browsebar",
            "footer",
            "bottombar",
        ],
        "exclusive": ["topbar.center", "browsebar", "footer"],
        "status": "shipped",
    },
    "sidenav-only": {
        "chrome": None,
        "slots": ["sidenav.head", "sidenav.foot", "sheet.foot", "page.actions"],
        "exclusive": [],
        "status": "queued",
    },
}

DEFAULT_SHELL_TEMPLATE = "topbar-sidenav"

# The templates the monolith route renderer can emit. `topbar-only` is the
# storefront generator's chrome (`render_public_routes_tsx`); making it a
# monolith option is its own queue item.
MONOLITH_RENDERABLE: frozenset[str] = frozenset({"topbar-sidenav"})

QUEUE_ITEMS: dict[str, str] = {
    "topbar-only": "monolith --shell topbar-only",
    "sidenav-only": "sidenav-only chrome in shell-react",
}
