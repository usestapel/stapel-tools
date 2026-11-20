"""The React binding type: what delivering one brick's UI on React takes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ReactBinding:
    """One brick's published ``@stapel/<key>-react`` pair.

    Everything here is a React delivery fact; the brick's nav entries and
    embeds themselves (ids, paths, placement, surface, order, neutral icon)
    are the neutral :class:`~stapel_tools.composition.model.BrickUi`.
    """

    package: str
    version: str
    provider: Optional[str] = None
    create_runtime: Optional[str] = None
    register_i18n: Optional[str] = None
    default_component: Optional[str] = None
    base_url: Optional[str] = None
    seam: Optional[dict] = None
    shell_provider: Optional[str] = None
    static_peer_pairs: tuple[str, ...] = ()
    released: bool = True
    # An unreleased pair (no npm version to read a tag from): the stapel-react
    # commit its mirror was taken from, pasted from `git rev-parse`. The nav
    # gate compares the mirror with THAT commit; the moving head is a notice.
    source_commit: Optional[str] = None
    # nav id -> (export, subpath) of the component that renders it
    components: dict = field(default_factory=dict)
    # embed id -> (export, subpath)
    embed_components: dict = field(default_factory=dict)
