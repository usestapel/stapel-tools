"""The registry split: neutral ``BrickUi`` + ``ReactBinding``, old names as views.

``tests/registry_snapshot/pre_split.json`` was taken from the tree BEFORE the
split (stapel-tools 0.81.3) with an exact encoder — key order, list vs tuple
and set membership all count, because renderers iterate these tables. Every
legacy view must equal it. The golden scaffold gate proves the rendered
output; this proves the tables the rest of the toolchain imports.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "registry_snapshot"))

from encode import TABLES, encode, snapshot  # noqa: E402

from stapel_tools.composition import bricks, icons  # noqa: E402
from stapel_tools.composition.model import BrickUi, NavEntrySpec  # noqa: E402
from stapel_tools.composition.react import registry  # noqa: E402

FROZEN = json.loads((HERE / "registry_snapshot" / "pre_split.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("module,name", TABLES)
def test_legacy_view_equals_the_pre_split_table(module, name):
    assert snapshot()[f"{module}.{name}"] == FROZEN[f"{module}.{name}"], (
        f"{module}.{name} no longer equals the pre-split table"
    )


def test_old_names_are_the_views():
    from stapel_tools import _frontend_templates as F
    from stapel_tools import create_project as cp

    assert cp.FRONTEND_REACT_LIBS is registry.FRONTEND_REACT_LIBS
    assert F.NAV_ENTRY_MOUNTS is registry.NAV_ENTRY_MOUNTS
    assert F.COMPOSITE_PAGES is registry.COMPOSITE_PAGES
    assert F.MONOLITH_CONTAINER_ROOTS is registry.MONOLITH_CONTAINER_ROOTS
    assert F.NAV_BADGE_SOURCES is registry.NAV_BADGE_SOURCES
    assert F.NAV_ICON_REGISTRY is registry.NAV_ICON_REGISTRY


def test_the_binding_and_the_neutral_registry_agree():
    assert registry.check_bindings() == []


def test_every_neutral_icon_has_a_react_glyph_and_nothing_else():
    assert set(registry.REACT_ICONS) == set(icons.NAV_ICONS)
    assert len(icons.NAV_ICONS) == 20
    assert registry.NAV_ICON_REGISTRY == frozenset(registry.REACT_ICONS.values())


def test_no_react_field_in_the_neutral_registry():
    """Component exports and antd names live behind the binding only."""
    for brick in bricks.BRICK_UI.values():
        for entry in brick.nav:
            assert entry.icon in icons.NAV_ICONS, entry
            assert not entry.icon.endswith("Outlined")
        assert not hasattr(brick, "package")
    text = (Path(bricks.__file__)).read_text(encoding="utf-8")
    assert "Outlined" not in text
    assert "@stapel/" not in text
    assert "subpath" not in text


class TestInversions:
    """The snapshot comparison and the binding check must be able to fail."""

    def test_a_changed_order_value_is_caught_by_the_snapshot(self, monkeypatch):
        auth = bricks.BRICK_UI["auth"]
        moved = BrickUi(
            key=auth.key,
            nav=(NavEntrySpec(**{**auth.nav[0].__dict__, "order": 99}), *auth.nav[1:]),
            embeds=auth.embeds,
        )
        monkeypatch.setitem(bricks.BRICK_UI, "auth", moved)
        assert encode(registry.frontend_react_libs()) != FROZEN["create_project.FRONTEND_REACT_LIBS"]

    def test_reordered_keys_are_caught(self):
        view = registry.frontend_react_libs()
        reordered = dict(reversed(list(view.items())))
        assert reordered == view  # dict equality is blind to order...
        assert encode(reordered) != encode(view)  # ...the encoding is not

    def test_a_binding_naming_an_undeclared_entry_is_refused(self, monkeypatch):
        binding = registry.REACT_BINDINGS["auth"]
        monkeypatch.setitem(
            binding.components, "auth.ghost", ("Ghost", "default"),
        )
        assert any("auth.ghost" in p for p in registry.check_bindings())

    def test_a_neutral_key_without_a_glyph_is_refused(self, monkeypatch):
        monkeypatch.delitem(registry.REACT_ICONS, "bell")
        assert any("bell" in p for p in registry.check_bindings())
        with pytest.raises(registry.RegistryError, match="bell"):
            registry.react_icon("bell", entry_id="x.y")
