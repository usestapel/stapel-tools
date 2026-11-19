"""Exact, order- and type-preserving JSON encoding of the registry tables.

``json.dumps`` alone would compare a tuple equal to a list and a set by
iteration order; the views must reproduce the pre-split tables exactly —
key order included, because renderers iterate them."""
from __future__ import annotations


def encode(value):
    if isinstance(value, dict):
        return {"__dict__": [[k, encode(v)] for k, v in value.items()]}
    if isinstance(value, list):
        return {"__list__": [encode(v) for v in value]}
    if isinstance(value, tuple):
        return {"__tuple__": [encode(v) for v in value]}
    if isinstance(value, (set, frozenset)):
        return {"__set__": sorted(encode(v) for v in value)}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"unexpected {type(value).__name__} in a registry table: {value!r}")


TABLES = (
    ("create_project", "FRONTEND_REACT_LIBS"),
    ("create_project", "FRONTEND_COMPOSITES"),
    ("_frontend_templates", "NAV_ENTRY_MOUNTS"),
    ("_frontend_templates", "COMPOSITE_PAGES"),
    ("_frontend_templates", "MONOLITH_CONTAINER_ROOTS"),
    ("_frontend_templates", "NAV_BADGE_SOURCES"),
    ("_frontend_templates", "NAV_ICON_REGISTRY"),
)


def snapshot() -> dict:
    import importlib

    out = {}
    for module, name in TABLES:
        mod = importlib.import_module(f"stapel_tools.{module}")
        out[f"{module}.{name}"] = encode(getattr(mod, name))
    return out
