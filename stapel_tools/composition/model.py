"""The platform-neutral composition model — frozen dataclasses, no I/O.

One scaffold invocation is interpreted ONCE, into a :class:`Composition`, and
every platform emitter (React today, KMP next) plans its files from that
object. Neither emitter computes the composition, so the two cannot disagree
about which modules were chosen, which auth model applies, or which theme the
product wears.

The brick registry's neutral half lives here too (:class:`BrickUi`,
:class:`NavEntrySpec`, :class:`EmbedSpec`, :class:`ContainerRoot`,
:class:`CompositePage`): ids, paths, placement, surface, order, slots and
neutral icon keys — the facts that must have ONE value on every platform so
one URL and one Studio address work everywhere. What a platform needs to
deliver a brick (an npm package and its component exports; a Maven artifact
and its Koin factory) is that platform's binding, never a field here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from .._auth_model import AuthComposition


class CompositionError(ValueError):
    """A composition an emitter cannot honour; the message is the CLI hint."""


@dataclass(frozen=True)
class NavEntrySpec:
    """One navigable screen a brick contributes, platform-free."""

    id: str
    label_key: str
    icon: str                      # neutral key from NAV_ICONS, never a UI-library name
    path: str
    level: str                     # "top" | "submenu"
    parent_id: Optional[str]
    menu_visible_default: bool
    requires_auth: bool
    order: int
    surface: Optional[str] = None  # "public" | "member" | None (derived from requires_auth)
    short_label_key: Optional[str] = None


@dataclass(frozen=True)
class EmbedSpec:
    """A control a brick offers to a shell slot (or to another entry's page)."""

    id: str
    slot: str
    order: int
    surface: Optional[str] = None  # "public" | "member" | "any"
    breakpoints: Optional[tuple[str, ...]] = None
    props: tuple[tuple[str, object], ...] = ()
    opens: Optional[str] = None
    scope_nav_entry: Optional[str] = None
    route_props: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class BrickUi:
    """One backend brick's UI contribution, platform-free."""

    key: str                       # STAPEL_LIBS key
    nav: tuple[NavEntrySpec, ...] = ()
    embeds: tuple[EmbedSpec, ...] = ()
    # entry id -> the page slots that entry's page renders
    page_slots: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # entry id -> the signal its badge counts ("unread_count")
    badges: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ContainerRoot:
    """A nav parent no brick owns ("the account section"): the container's."""

    id: str
    label_key: str
    icon: str
    path: str
    order: int
    requires_auth: bool = True
    surface: Optional[str] = "member"
    menu_visible_default: bool = True


@dataclass(frozen=True)
class CompositePage:
    """A screen the container assembles from several bricks."""

    entry_id: str
    requires: tuple[str, ...]      # brick keys without which the page is a named placeholder
    optional: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlatformRequest:
    """What one platform was asked for on the command line."""

    platform: str                  # "react" | "kmp"
    shell: Optional[str] = None    # template id of that platform, None = its default
    slot_overrides: tuple[tuple[str, str], ...] = ()
    options: tuple[tuple[str, object], ...] = ()

    def option(self, name: str, default: object = None) -> object:
        return dict(self.options).get(name, default)


@dataclass(frozen=True)
class Composition:
    """The single input of every emitter."""

    slug: str
    title: str
    url: str
    project_type: str
    modules: tuple[str, ...]       # feature libs as selected (requires closed), order preserved
    bricks: tuple[BrickUi, ...]    # the selected bricks that have any UI, registry order
    auth: Optional["AuthComposition"]
    want_auth: bool
    landing: bool
    theme: Optional[str]
    theme_json: str                # render_theme_json output, identical for every platform
    backend_prefixes: tuple[str, ...]
    backend_upstream_default: str
    platforms: tuple[PlatformRequest, ...] = ()
    # anonymous actions the auth model turns on for the frontend runtime
    auto_anonymous_actions: tuple[str, ...] = ()
    # the navigation block (navigation-templates spec §3.2), as given; {} = defaults
    navigation: dict = field(default_factory=dict)
    # the project's own pages (stapel.app-nav.json entries, neutral shape)
    app_nav: tuple = ()
    # platform -> the own-page ids that platform has a binding for
    app_bindings: dict = field(default_factory=dict)
    # notes that mean "something asked for is not rendered" become refusals
    strict: bool = False
    # the project's own label texts ({locale: {labelKey: text}}, src/app.labels.json)
    app_labels: dict = field(default_factory=dict)
    # the product in one line (the home page's lead); "" = not given
    pitch: str = ""

    @property
    def auth_model(self) -> Optional[str]:
        return self.auth.model if self.auth is not None else None

    def platform(self, name: str) -> Optional[PlatformRequest]:
        return next((p for p in self.platforms if p.platform == name), None)


@dataclass(frozen=True)
class Gate:
    """A command that proves an emitted tree works; a missing tool is "not run"."""

    name: str
    argv: tuple[str, ...]
    cwd: str                       # relative to the project dir
    needs: tuple[str, ...] = ()    # "jdk", "xcode", "docker", "node"


@dataclass(frozen=True)
class EmitPlan:
    """Everything one emitter wants written, computed without touching disk."""

    platform: str
    root: str                      # "frontend" | "mobile"
    files: dict = field(default_factory=dict)   # project-relative path -> str | bytes
    modes: dict = field(default_factory=dict)   # project-relative path -> mode bits
    composition: dict = field(default_factory=dict)
    notes: tuple[str, ...] = ()    # every dropped embed / pending brick, by name
    frontends: tuple = ()          # _compose_templates.Frontend delivery entries
    nginx_locations: tuple[str, ...] = ()
    reserved_paths: tuple[str, ...] = ()
    gates: tuple[Gate, ...] = ()
