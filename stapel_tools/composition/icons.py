"""The neutral nav icon vocabulary.

A brick names an icon by one of these keys, never by a UI library's name;
each platform maps every key to its own glyph (``react.registry.REACT_ICONS``
for the antd set the shell renders). The registry gate refuses a platform
mapping that does not cover every key, and generation refuses an entry whose
key is not here.
"""
from __future__ import annotations

NAV_ICONS: tuple[str, ...] = (
    "apps",
    "audit",
    "bell",
    "cart",
    "clock",
    "folder",
    "folder-open",
    "heart",
    "home",
    "list-ordered",
    "login",
    "message",
    "plus",
    "profile",
    "qr",
    "search",
    "settings",
    "shield-check",
    "tag",
    "user",
)
