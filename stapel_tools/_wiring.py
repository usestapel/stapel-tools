"""Settings, middleware, schedules and the ASGI router a selection implies.

Everything here is DERIVED from ``STAPEL_LIBS`` (the registry keys
``composite``, ``middleware``, ``beat`` and the ``realtime`` entry) and
rendered into the generated settings at one point, the ``{{STAPEL_WIRING}}``
token at the end of the base settings, after every ``STAPEL_<MOD>`` block the
scaffold writes. One renderer serves the monolith service tier and the
minimal single-file settings, so the two cannot wire a selection differently.

What a selection gets (AUDIT-BRICKS-2, 2026-10-22):

* a composite's ``preset.SETTINGS_DEFAULTS``, imported from the installed
  package at settings time and merged two levels deep UNDER any explicit
  value (SO-1: social's reviews ``TARGET_TYPES`` reached no generated app);
* stapel-realtime's host wiring whenever a socket-serving module selected it
  (CH-8): the ``channels`` signal transport, ``CHANNEL_LAYERS``, the origin
  allowlist, ``ASGI_APPLICATION`` and a ``config/asgi.py`` built by
  ``build_websocket_application``;
* ``CELERY_BEAT_SCHEDULE`` from every selected module's beat helper (MD-1);
* each selected module's ``middleware`` appended to ``MIDDLEWARE`` (GD-3).
"""
from __future__ import annotations


def _libs():
    from .create_project import STAPEL_LIBS

    return STAPEL_LIBS


def keys_for_apps(apps: list[str]) -> list[str]:
    """Registry keys for a list of Django app/dir names (unknown names dropped)."""
    by_dir = {info["dir"]: key for key, info in _libs().items()}
    return [by_dir[app] for app in apps if app in by_dir]


def composites(libs: list[str]) -> list[str]:
    reg = _libs()
    return [k for k in reg if k in libs and reg[k].get("composite")]


def realtime_selected(libs: list[str]) -> bool:
    return "realtime" in libs


def beat_sources(libs: list[str]) -> list[tuple[str, str]]:
    reg = _libs()
    return [tuple(reg[k]["beat"]) for k in reg if k in libs and reg[k].get("beat")]


def middleware(libs: list[str]) -> list[str]:
    reg = _libs()
    out: list[str] = []
    for key in reg:
        if key in libs:
            out.extend(m for m in reg[key].get("middleware", []) if m not in out)
    return out


_PRESET_MERGE = '''
# ─── Composite scenario defaults ────────────────────────────────────────────
# Each selected composite's preset.SETTINGS_DEFAULTS, imported from the
# installed package so these values cannot drift from the release this project
# pins. Merged two levels deep (STAPEL_<MOD> -> key -> registry entry) UNDER
# anything set above: an explicit STAPEL_<MOD> key, or one entry of a registry
# such as STAPEL_REVIEWS["TARGET_TYPES"], wins over the preset's. Later
# composites refine earlier ones ({composites_doc}).
def _stapel_merge_preset_defaults(namespace, preset_modules):
    from importlib import import_module

    def merge(base, over):
        merged = dict(base)
        for key, value in over.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {{**merged[key], **value}}
            else:
                merged[key] = value
        return merged

    combined = {{}}
    for module in preset_modules:
        for name, defaults in import_module(module).SETTINGS_DEFAULTS.items():
            combined[name] = merge(combined.get(name, {{}}), defaults)
    for name, defaults in combined.items():
        namespace[name] = merge(defaults, namespace.get(name) or {{}})


_stapel_merge_preset_defaults(globals(), [{modules}])
'''


def render_preset_defaults(libs: list[str]) -> str:
    keys = composites(libs)
    if not keys:
        return ""
    reg = _libs()
    modules = ", ".join(f'"{reg[k]["dir"]}.preset"' for k in keys)
    return _PRESET_MERGE.format(modules=modules, composites_doc=" -> ".join(keys))


_REALTIME_MONOLITH = '''
# ─── WebSocket substrate (stapel-realtime) ──────────────────────────────────
# Selected because a module here serves a socket (chat, video). Chat refuses to
# boot without each piece (stapel_chat.E010-E014): the app above, a channel
# layer, the "channels" signal transport, an origin allowlist, and the ASGI
# router in config/asgi.py (build_websocket_application, which discovers every
# installed module's routing.websocket_urlpatterns behind core's JWT stack).
ASGI_APPLICATION = "config.asgi.application"
STAPEL_COMM = {**STAPEL_COMM, "SIGNAL_TRANSPORT": "channels"}  # noqa: F405
# The socket origin guard compares WITH the port; the same list core's cookie
# handshake reads (STAPEL_WS_ALLOWED_ORIGINS, from the env). dev.py re-syncs it
# after its own local default.
STAPEL_REALTIME = {
    # Socket routes live under /ws/<module>/ (chat: ws/chat/...). Stated, not
    # left to the bare URL_PREFIX above, which is this service's HTTP mount
    # (realtime.W004 judges routes against this value, W007 asks for it).
    "URL_PREFIX": "ws",
    "ALLOWED_ORIGINS": list(STAPEL_WS_ALLOWED_ORIGINS),  # noqa: F405
    **(globals().get("STAPEL_REALTIME") or {}),
}
# One layer for every process: fan-out crosses gunicorn/daphne workers only
# through redis. socket_timeout None keeps a consumer parked in BZPOPMIN alive
# (realtime.E002).
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [{
                "address": os.getenv("CHANNEL_LAYER_URL", os.getenv("REDIS_URL", "redis://redis:6379/0")),
                "socket_timeout": None,
            }],
        },
    },
}
'''

_REALTIME_MINIMAL = '''
# ─── WebSocket substrate (stapel-realtime) ──────────────────────────────────
# Selected because a module here serves a socket (chat, video); chat refuses to
# boot without each piece (stapel_chat.E010-E014). This preset is one process,
# so the in-memory layer is enough (realtime.E001 refuses it under several
# workers). Serve config/asgi.py (`daphne config.asgi:application`) for the
# sockets; runserver serves HTTP only.
ASGI_APPLICATION = "config.asgi.application"
STAPEL_COMM = {**STAPEL_COMM, "SIGNAL_TRANSPORT": "channels"}
STAPEL_REALTIME = {
    # Socket routes live under /ws/<module>/ (chat: ws/chat/...). Stated, not
    # left to the bare URL_PREFIX above, which is this service's HTTP mount
    # (realtime.W004 judges routes against this value, W007 asks for it).
    "URL_PREFIX": "ws",
    "ALLOWED_ORIGINS": [
        o.strip()
        for o in os.getenv(
            "STAPEL_WS_ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:8000"
        ).split(",")
        if o.strip()
    ],
    **(globals().get("STAPEL_REALTIME") or {}),
}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
'''

#: dev.py tail: the local origin default dev.py sets AFTER base.py built
#: STAPEL_REALTIME, plus daphne so `runserver` serves the ASGI app (sockets
#: included) instead of WSGI.
REALTIME_DEV = '''
# The socket origin guard follows the local default set above, and daphne
# turns `runserver` into the ASGI server so the chat socket answers locally.
STAPEL_REALTIME = {**STAPEL_REALTIME, "ALLOWED_ORIGINS": list(STAPEL_WS_ALLOWED_ORIGINS)}  # noqa: F405
INSTALLED_APPS = ["daphne", *INSTALLED_APPS]  # noqa: F405
'''

ASGI_REALTIME_PY = """\
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{settings}")
# Django first: the router below imports consumers, which need the app registry.
http_application = get_asgi_application()

from stapel_realtime.asgi import build_websocket_application  # noqa: E402

# Origin guard over core's JWT stack over every installed module's
# routing.websocket_urlpatterns (stapel-realtime). Never hand-write this stack.
application = build_websocket_application(http_application=http_application)
"""


#: stapel-core's own beat entry (the comm Task sweep). A process that runs a
#: beat schedule without it trips stapel_taskstore.W001, so it joins every
#: schedule the service tier writes (the minimal preset does not install the
#: taskstore app and runs no beat).
TASKSTORE_BEAT = ("stapel_core.django.taskstore.beat", "get_taskstore_beat_schedule")


def render_beat_schedule(libs: list[str], *, flavor: str = "monolith") -> str:
    sources = beat_sources(libs)
    if not sources:
        return ""
    if flavor == "monolith":
        sources = [TASKSTORE_BEAT, *sources]
    sources = sorted(sources)  # one import block, in the order ruff's isort keeps
    imports = "\n".join(f"from {mod} import {func}  # noqa: E402" for mod, func in sources)
    spreads = "\n".join(f"    **{func}()," for _mod, func in sources)
    return f'''
# ─── Scheduled jobs (celery beat) ───────────────────────────────────────────
# Every selected module's own beat helper, written HERE so `manage.py check`
# reads what beat will run (stapel_moderation.W004, stapel_search.W003 fire on
# an absent schedule). Each helper imports settings and nothing else. Run beat
# (`celery -A config beat`) next to a worker; a cron deployment schedules the
# same task names and silences the two warnings instead.
{imports}

CELERY_BEAT_SCHEDULE = {{
    **(globals().get("CELERY_BEAT_SCHEDULE") or {{}}),
{spreads}
}}
'''


def render_middleware(libs: list[str]) -> str:
    entries = middleware(libs)
    if not entries:
        return ""
    lines = "".join(f'    "{m}",\n' for m in entries)
    return f'''
# ─── Middleware the selected modules require ────────────────────────────────
# After authentication, so request.user is resolved. stapel-gdpr's closure
# guard refuses every request of an account in its deletion grace period or
# being erased, not only the gdpr endpoints (gdpr.W015).
MIDDLEWARE = [
    *MIDDLEWARE,  # noqa: F405
{lines}]
'''


def render_wiring(libs: list[str], *, flavor: str) -> str:
    """The ``{{STAPEL_WIRING}}`` fragment for a selection. ``flavor`` is
    ``"monolith"`` (service settings tier) or ``"minimal"`` (one file)."""
    parts = [render_preset_defaults(libs)]
    if realtime_selected(libs):
        parts.append(_REALTIME_MONOLITH if flavor == "monolith" else _REALTIME_MINIMAL)
    parts.append(render_beat_schedule(libs, flavor=flavor))
    parts.append(render_middleware(libs))
    return "".join(p for p in parts if p)


def render_dev_wiring(libs: list[str]) -> str:
    return REALTIME_DEV if realtime_selected(libs) else ""


#: Appended to the service tier's boot_smoke.py when stapel-search is
#: selected. The gate swaps DATABASES for Django's `dummy` backend, and
#: stapel_search.E003 compares the configured Postgres backend with the
#: connection's vendor, which a no-database gate cannot have. `check` under
#: base.py still runs E003 against the real database settings.
BOOT_SMOKE_SEARCH = """
# stapel_search.E003 compares the Postgres search backend with the database
# vendor; this gate has no database by design (see above). The plain `check`
# gate runs it against the real DATABASES.
SILENCED_SYSTEM_CHECKS = [*globals().get("SILENCED_SYSTEM_CHECKS", []), "stapel_search.E003"]
"""


def boot_smoke_extra(libs: list[str]) -> str:
    return BOOT_SMOKE_SEARCH if "search" in libs else ""


def asgi_py(libs: list[str], *, settings_module: str) -> str | None:
    """A realtime ``config/asgi.py`` for the selection, or None (keep default)."""
    if not realtime_selected(libs):
        return None
    return ASGI_REALTIME_PY.format(settings=settings_module)


def seed_commands(libs: list[str]) -> list[str]:
    reg = _libs()
    return [cmd for k in reg if k in libs for cmd in reg[k].get("seed", [])]


def render_bootstrap_seed(libs: list[str]) -> str:
    """``optional`` bootstrap steps for each selected module's idempotent seed
    (reference data a fresh app needs: currencies, a starter category tree)."""
    lines = []
    for cmd in seed_commands(libs):
        name = "seed-" + cmd.split()[2].replace("_", "-")
        lines.append(f'optional "{name}" {cmd}')
    return ("\n" + "\n".join(lines) + "\n") if lines else ""


#: Starter category trees, one per composite (a generic one otherwise). No
#: features: a category with none publishes without attribute answers, and a
#: project adds its own fields through the admin or its fixtures.
_STARTER_TREES: dict[str, list[tuple[str, str | None, str]]] = {
    "booking": [
        ("spaces", None, "Spaces"), ("meeting-rooms", "spaces", "Meeting rooms"),
        ("desks", "spaces", "Desks"), ("equipment", None, "Equipment"),
        ("services", None, "Services"),
    ],
    "classified": [
        ("electronics", None, "Electronics"), ("phones", "electronics", "Phones"),
        ("computers", "electronics", "Computers"), ("home", None, "Home and garden"),
        ("vehicles", None, "Vehicles"), ("other", None, "Other"),
    ],
}
_STARTER_GENERIC = [
    ("electronics", None, "Electronics"), ("phones", "electronics", "Phones"),
    ("computers", "electronics", "Computers"), ("home", None, "Home"),
    ("clothing", None, "Clothing"), ("other", None, "Other"),
]


def starter_catalog(libs: list[str]) -> dict[str, list] | None:
    """``{"features.json": [], "categories.json": [...]}`` for the selection,
    or None when categories is not selected."""
    if "categories" not in libs:
        return None
    tree = next((_STARTER_TREES[k] for k in composites(libs)[::-1] if k in _STARTER_TREES), _STARTER_GENERIC)
    cats = [
        {"slug": slug, "parent_slug": parent, "name": name, "active": True, "features": []}
        for slug, parent, name in tree
    ]
    return {"features.json": [], "categories.json": cats}
