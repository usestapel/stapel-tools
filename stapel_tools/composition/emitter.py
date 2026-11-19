"""The emitter seam: one :class:`Composition`, one ``plan()`` per platform.

``Emitter.plan()`` is PURE — no filesystem, no network, no clock. That is the
lever for everything around it: the golden gate hashes a plan without a temp
directory, Studio's intake can ask "what would this composition produce"
without writing, and two emitters cannot disagree about the composition
because neither computes it. :func:`write_plan` is the only writer.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar, Optional, Protocol

from .model import (
    BrickUi,
    Composition,
    CompositionError,
    EmitPlan,
    PlatformRequest,
)

__all__ = [
    "EMITTERS",
    "Emitter",
    "build_composition",
    "emitter_for",
    "write_plan",
]


class Emitter(Protocol):
    platform: ClassVar[str]

    def validate(self, comp: Composition, request: PlatformRequest) -> None:
        """Raise :class:`CompositionError` with the CLI hint, or return."""

    def plan(self, comp: Composition, request: PlatformRequest) -> EmitPlan:
        """Every file this platform wants, computed without touching disk."""


def _emitters() -> dict[str, Emitter]:
    from .kmp.emitter import KmpEmitter
    from .react.emitter import ReactEmitter

    return {"react": ReactEmitter(), "kmp": KmpEmitter()}


#: platform -> emitter. Filled lazily so importing the model costs nothing.
EMITTERS: dict[str, Emitter] = {}


def emitter_for(platform: str) -> Emitter:
    if not EMITTERS:
        EMITTERS.update(_emitters())
    try:
        return EMITTERS[platform]
    except KeyError:
        raise CompositionError(
            f"no emitter for platform {platform!r} (known: {', '.join(sorted(EMITTERS))})"
        ) from None


def bricks_for(modules: tuple[str, ...]) -> tuple[BrickUi, ...]:
    """The selected bricks that have a UI, in selection order."""
    from .bricks import BRICK_UI

    return tuple(BRICK_UI[key] for key in modules if key in BRICK_UI)


def build_composition(
    *,
    ctx: dict,
    project_type: str,
    feature_libs: Optional[list[str]] = None,
    want_auth: Optional[bool] = None,
    want_landing: bool = False,
    auth_comp=None,
    theme: Optional[str] = None,
    shell: Optional[str] = None,
    slots: Optional[dict[str, str]] = None,
    backend_prefixes: Optional[list[str]] = None,
    backend_upstream_default: str = "",
    auto_anonymous_actions: tuple[str, ...] = (),
    mobile: Optional[PlatformRequest] = None,
) -> Composition:
    """The only place the scaffold's CLI values are interpreted.

    Defaults are resolved here, once: ``want_auth`` follows the selection
    when not given; ``backend_prefixes`` falls back to the project's own
    prefix plus ``admin``; the theme is rendered to the JSON every platform
    writes byte-for-byte."""
    from .._theme_presets import render_theme_json

    modules = tuple(feature_libs or ())
    if want_auth is None:
        want_auth = "auth" in modules
    react = PlatformRequest(
        "react",
        shell=shell,
        slot_overrides=tuple((slots or {}).items()),
    )
    return Composition(
        slug=ctx["slug"],
        title=ctx["title"],
        url=ctx.get("url", ""),
        project_type=project_type,
        modules=modules,
        bricks=bricks_for(modules),
        auth=auth_comp,
        want_auth=bool(want_auth),
        landing=bool(want_landing),
        theme=theme,
        theme_json=render_theme_json(ctx["title"], theme),
        backend_prefixes=tuple(backend_prefixes or (ctx["slug"], "admin")),
        backend_upstream_default=backend_upstream_default,
        platforms=(react,) if mobile is None else (react, mobile),
        auto_anonymous_actions=tuple(auto_anonymous_actions),
    )


def write_plan(project_dir: Path, plan: EmitPlan) -> None:
    """Write *plan* under *project_dir*, in plan order. The only writer."""
    for rel, content in plan.files.items():
        path = Path(project_dir) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        mode = plan.modes.get(rel)
        if mode is not None:
            os.chmod(path, mode)
