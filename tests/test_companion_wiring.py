"""Companion bricks wire themselves (SPEC-BRICKS-WAVE §2, orders CONTRACT-DELTAS 19).

A registry row with ``companion_of`` (promotions, delivery, inventory and
courses next to orders) gets its ``preset.SETTINGS_DEFAULTS`` merged into the
generated settings by the same block that merges composite presets, when a
target is selected too. CFG011 flags a companion installed with no
registration, and a registration naming a Function nobody provides.
"""
from __future__ import annotations

import sys
import textwrap
import types
from pathlib import Path

import pytest

from stapel_tools import _wiring
from stapel_tools._companion_lint import read_fragment
from stapel_tools.config_lint import lint_project
from stapel_tools.create_project import STAPEL_LIBS, create_project
from stapel_tools.preset_gate import companion_drift, companion_keys

COMPANIONS = ["promotions", "delivery", "inventory", "courses"]


def _create(tmp_path, name, modules, **kw):
    create_project(
        name=name, project_type="minimal", title=name, url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=["core", *modules],
        output_dir=tmp_path, use_submodules=False, init_git=False, **kw,
    )
    return tmp_path / name


# ─── the registry ───────────────────────────────────────────────────────────


class TestRegistry:
    def test_companions_name_registered_targets(self):
        assert companion_keys() == COMPANIONS
        for key in companion_keys():
            for target in STAPEL_LIBS[key]["companion_of"]:
                assert target in STAPEL_LIBS, (key, target)

    def test_selected_alone_nothing_is_merged(self):
        assert _wiring.preset_modules(["promotions"]) == []

    def test_selected_with_its_target_it_is_merged(self):
        assert _wiring.preset_modules(["orders", "promotions"]) == ["promotions"]

    def test_merge_order_is_the_registry_order_not_the_callers(self):
        a = _wiring.preset_modules(["shop", "inventory", "orders", "promotions"])
        b = _wiring.preset_modules(["promotions", "orders", "shop", "inventory"])
        assert a == b == ["promotions", "inventory", "shop"]

    def test_without_companions_the_block_is_the_composite_block(self):
        """Regression: a selection with no companion renders what it did before."""
        for libs in (["shop", "orders"], ["shop", "classified"], ["social"], ["orders"]):
            assert _wiring.preset_modules(libs) == _wiring.composites(libs)


# ─── the merge, run as generated code ───────────────────────────────────────


class TestMerge:
    def _run(self, presets: dict[str, dict], explicit: dict) -> dict:
        mods = []
        for name, defaults in presets.items():
            mod = types.ModuleType(name)
            mod.SETTINGS_DEFAULTS = defaults
            sys.modules[name] = mod
            mods.append(name)
        try:
            block = _wiring._PRESET_MERGE.format(
                modules=", ".join(f'"{m}"' for m in mods), composites_doc=" -> ".join(mods))
            ns = dict(explicit)
            exec(block, ns)  # noqa: S102 - the generated settings code under test
        finally:
            for name in mods:
                del sys.modules[name]
        return ns

    def test_two_companions_land_side_by_side_explicit_wins_none_survives(self):
        ns = self._run(
            {
                "_fake_promotions": {"STAPEL_ORDERS": {
                    "ADJUSTERS": {"promotions": {"quote": "promotions.quote", "order": 100}},
                    "CHECKOUT_HOOKS": {"promotions": {"reserve": "promotions.reserve"}},
                }},
                "_fake_inventory": {"STAPEL_ORDERS": {
                    "CHECKOUT_HOOKS": {"inventory": {"reserve": "inventory.checkout.reserve"}},
                }},
            },
            {"STAPEL_ORDERS": {"PAYMENT": "none", "ADJUSTERS": {"promotions": None},
                               "CHECKOUT_HOOKS": {"inventory": {"reserve": "custom.reserve"}}}},
        )
        orders = ns["STAPEL_ORDERS"]
        assert orders["PAYMENT"] == "none"
        # None is the host's removal; the owning library's merge registry drops it.
        assert orders["ADJUSTERS"] == {"promotions": None}
        assert orders["CHECKOUT_HOOKS"] == {
            "promotions": {"reserve": "promotions.reserve"},
            "inventory": {"reserve": "custom.reserve"},
        }


# ─── generated projects ─────────────────────────────────────────────────────


def _merge_line(proj: Path) -> str:
    text = (proj / "config" / "settings.py").read_text()
    lines = [ln for ln in text.splitlines() if ln.startswith("_stapel_merge_preset_defaults(globals()")]
    return lines[0] if lines else ""


class TestGenerated:
    def test_companions_join_the_merge_call(self, tmp_path):
        proj = _create(tmp_path, "p", ["shop", "orders", "promotions", "delivery"],
                       allow_unreleased=True)
        assert _merge_line(proj) == (
            '_stapel_merge_preset_defaults(globals(), ["stapel_promotions.preset", '
            '"stapel_delivery.preset", "stapel_shop.preset"])')

    def test_rerun_is_byte_identical(self, tmp_path):
        a = _create(tmp_path / "a", "p", ["orders", "inventory", "courses"], allow_unreleased=True)
        b = _create(tmp_path / "b", "p", ["courses", "inventory", "orders"], allow_unreleased=True)
        assert (a / "config" / "settings.py").read_text() == (b / "config" / "settings.py").read_text()

    def test_companion_without_its_target_merges_nothing(self, tmp_path):
        proj = _create(tmp_path, "p", ["promotions"], allow_unreleased=True)
        assert _merge_line(proj) == ""


class TestPrototypeDev:
    def test_mock_carrier_only_for_a_prototype_local_module(self):
        assert _wiring.render_dev_wiring(["orders", "delivery"]) == ""
        text = _wiring.render_dev_wiring(["orders", "delivery"], prototype=True)
        assert 'STAPEL_DELIVERY = {**{\'DEFAULT_CARRIER\': \'mock\'}, **(globals().get("STAPEL_DELIVERY") or {})}' in text

    def test_explicit_value_wins(self):
        ns = {"STAPEL_DELIVERY": {"DEFAULT_CARRIER": "manual"}}
        exec(_wiring.render_prototype_dev(["delivery"]), ns)  # noqa: S102
        assert ns["STAPEL_DELIVERY"]["DEFAULT_CARRIER"] == "manual"
        ns = {}
        exec(_wiring.render_prototype_dev(["delivery"]), ns)  # noqa: S102
        assert ns["STAPEL_DELIVERY"]["DEFAULT_CARRIER"] == "mock"


# ─── the preset gate for companions ─────────────────────────────────────────


class TestCompanionGate:
    def test_a_fragment_for_its_target_is_equal(self):
        preset = types.SimpleNamespace(SETTINGS_DEFAULTS={
            "STAPEL_ORDERS": {"ADJUSTERS": {"promotions": {"quote": "promotions.quote"}}}})
        assert companion_drift("promotions", preset) == []

    def test_inversion_a_fragment_outside_companion_of_is_flagged(self):
        preset = types.SimpleNamespace(SETTINGS_DEFAULTS={
            "STAPEL_ORDERS": {}, "STAPEL_BILLING": {"X": 1}})
        assert any("STAPEL_BILLING" in f for f in companion_drift("promotions", preset))

    def test_inversion_no_fragment_is_flagged(self):
        assert companion_drift("inventory", types.SimpleNamespace())
        missing = types.SimpleNamespace(SETTINGS_DEFAULTS={"STAPEL_X": {}})
        assert any("no 'STAPEL_ORDERS' fragment" in f for f in companion_drift("courses", missing))

    def test_a_mount_off_the_registry_is_flagged(self):
        preset = types.SimpleNamespace(
            SETTINGS_DEFAULTS={"STAPEL_ORDERS": {}},
            URL_INCLUDES=[("stock/", "stapel_inventory.urls")])
        assert any("'stock/'" in f for f in companion_drift("inventory", preset))


# ─── CFG011 ─────────────────────────────────────────────────────────────────


def _package(root: Path, name: str, files: dict[str, str]) -> None:
    pkg = root / f"stapel-{name}"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    for fname, body in files.items():
        (pkg / fname).write_text(textwrap.dedent(body))


PROMOTIONS = {
    "preset.py": """
        NAME = "promotions"
        SETTINGS_DEFAULTS = {"STAPEL_ORDERS": {
            "ADJUSTERS": {NAME: {"quote": "promotions.quote", "order": 100}},
            "CHECKOUT_HOOKS": {NAME: {"reserve": "promotions.reserve",
                                      "commit": "promotions.commit",
                                      "release": "promotions.release"}},
        }}
    """,
    "functions.py": """
        from stapel_core.comm import function
        FN = "promotions.release"
        @function("promotions.quote")
        def q(p): ...
        @function("promotions.reserve")
        def r(p): ...
        @function("promotions.commit")
        def c(p): ...
        @function(FN)
        def rel(p): ...
    """,
}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A project beside fake sibling checkouts; the real stapel_promotions, if
    any is installed, must not answer for the fake."""
    import importlib.util

    real = importlib.util.find_spec

    def find_spec(name, *a, **kw):
        if name.startswith(("stapel_promotions", "stapel_delivery")):
            return None
        return real(name, *a, **kw)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    _package(tmp_path, "promotions", PROMOTIONS)
    project = tmp_path / "proj"
    (project / "config").mkdir(parents=True)
    return project


def _settings(project: Path, body: str) -> None:
    (project / "config" / "settings.py").write_text(textwrap.dedent(body))


def _cfg011(project: Path) -> list:
    return [f for f in lint_project(project) if f.rule == "CFG011"]


APPS = 'INSTALLED_APPS = ["django.contrib.auth", "stapel_orders", "stapel_promotions"]\n'


class TestCFG011:
    def test_inversion_companion_next_to_target_without_registration_fires(self, workspace):
        _settings(workspace, APPS)
        found = _cfg011(workspace)
        assert len(found) == 1
        assert found[0].line == 1 and "stapel_promotions is installed next to stapel_orders" in found[0].message

    def test_the_generated_merge_is_a_registration(self, workspace):
        _settings(workspace, APPS + '_stapel_merge_preset_defaults(globals(), ["stapel_promotions.preset"])\n')
        assert _cfg011(workspace) == []

    def test_an_import_of_the_preset_is_a_registration(self, workspace):
        _settings(workspace, APPS + "from stapel_promotions.preset import SETTINGS_DEFAULTS\n")
        assert _cfg011(workspace) == []

    def test_a_hand_pasted_fragment_is_a_registration(self, workspace):
        _settings(workspace, APPS + textwrap.dedent("""
            STAPEL_ORDERS = {
                "ADJUSTERS": {"promotions": {"quote": "promotions.quote"}},
                "CHECKOUT_HOOKS": {"promotions": {"reserve": "promotions.reserve",
                    "commit": "promotions.commit", "release": "promotions.release"}},
            }
        """))
        assert _cfg011(workspace) == []

    def test_a_partial_paste_is_not(self, workspace):
        _settings(workspace, APPS + 'STAPEL_ORDERS = {"ADJUSTERS": {"promotions": {"quote": "promotions.quote"}}}\n')
        assert len(_cfg011(workspace)) == 1

    def test_companion_without_its_target_is_silent(self, workspace):
        _settings(workspace, 'INSTALLED_APPS = ["stapel_promotions"]\n')
        assert _cfg011(workspace) == []

    def test_inversion_registration_naming_a_function_nobody_provides(self, workspace):
        _settings(workspace, APPS + textwrap.dedent("""
            _stapel_merge_preset_defaults(globals(), ["stapel_promotions.preset"])
            STAPEL_ORDERS = {"ADJUSTERS": {"delivery": {"quote": "delivery.quote"}}}
        """))
        found = _cfg011(workspace)
        assert [f.line for f in found] == [4]
        assert "'delivery.quote'" in found[0].message

    def test_the_merged_fragment_is_checked_too(self, workspace):
        (workspace.parent / "stapel-promotions" / "functions.py").write_text(
            "from stapel_core.comm import function\n@function('promotions.quote')\ndef q(p): ...\n")
        _settings(workspace, APPS + '_stapel_merge_preset_defaults(globals(), ["stapel_promotions.preset"])\n')
        missing = sorted(f.message.split("'")[1] for f in _cfg011(workspace))
        assert missing == ["promotions.commit", "promotions.release", "promotions.reserve"]

    def test_an_unlocatable_provider_is_a_note_not_a_verdict(self, workspace):
        _settings(workspace, 'INSTALLED_APPS = ["stapel_orders", "stapel_widgets"]\n'
                  'STAPEL_ORDERS = {"ADJUSTERS": {"widgets": {"quote": "widgets.quote"}}}\n')
        notes: list[str] = []
        found = [f for f in lint_project(workspace, notes=notes) if f.rule == "CFG011"]
        assert found == []
        assert any("cannot verify 'widgets.quote'" in n for n in notes)

    def test_fragment_reader_resolves_names_and_unpacking(self, tmp_path):
        (tmp_path / "preset.py").write_text(
            'HOOK = {"reserve": "x.reserve"}\nBASE = {"a": 1}\n'
            'SETTINGS_DEFAULTS = {"STAPEL_ORDERS": {"CHECKOUT_HOOKS": {"x": HOOK}, **BASE}}\n')
        assert read_fragment(tmp_path) == {
            "STAPEL_ORDERS": {"CHECKOUT_HOOKS": {"x": {"reserve": "x.reserve"}}, "a": 1}}


@pytest.mark.parametrize("key", COMPANIONS)
def test_local_brick_preset_equals_the_registry(key):
    """Reads the brick checkout beside this repo when the workspace has one
    (bricks with no release are not installable on a runner)."""
    checkout = Path(__file__).resolve().parents[2] / f"stapel-{key}"
    if not (checkout / "preset.py").is_file():
        checkout = Path(__file__).resolve().parents[3] / f"stapel-{key}"
    if not (checkout / "preset.py").is_file():
        pytest.skip(f"no stapel-{key} checkout beside this repo")
    fragment = read_fragment(checkout)
    assert fragment is not None
    assert companion_drift(key, types.SimpleNamespace(SETTINGS_DEFAULTS=fragment)) == []
