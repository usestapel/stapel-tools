"""The platform-neutral composition seam (D11): Composition -> Emitter.plan ->
write_plan, the planners' new home, the registry export and the sync gate's
neutral and KMP rows."""
from __future__ import annotations

import builtins
import importlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from stapel_tools.composition import export
from stapel_tools.composition.emitter import build_composition, emitter_for, write_plan
from stapel_tools.composition.model import CompositionError, EmitPlan

ROOT = Path(__file__).resolve().parents[1]
CTX = {"slug": "acme", "title": "Acme", "url": "https://acme.example"}


def _comp(**kw):
    return build_composition(
        ctx=CTX, project_type="monolith",
        feature_libs=kw.pop("feature_libs", ["auth", "notifications", "profiles"]),
        backend_upstream_default="svc-acme:8000", **kw,
    )


class TestBuildComposition:
    def test_defaults_are_resolved_once(self):
        comp = _comp()
        assert comp.want_auth is True                      # follows the selection
        assert comp.backend_prefixes == ("acme", "admin")  # the old fallback
        assert [b.key for b in comp.bricks] == ["auth", "notifications", "profiles"]
        assert comp.platform("react") is not None
        assert comp.platform("kmp") is None
        assert json.loads(comp.theme_json)

    def test_no_auth_selected_means_not_wanted(self):
        assert _comp(feature_libs=["profiles"]).want_auth is False

    def test_slot_overrides_keep_their_order(self):
        comp = _comp(slots={"topbar.end": "none", "topbar.start": "none"})
        assert comp.platform("react").slot_overrides == (
            ("topbar.end", "none"), ("topbar.start", "none"))

    def test_unknown_platform_is_refused(self):
        with pytest.raises(CompositionError, match="no emitter for platform 'kmp'"):
            emitter_for("kmp")


class TestThePlanIsPure:
    def _guard(self, monkeypatch):
        real_open = builtins.open

        def guarded(file, mode="r", *a, **k):
            if any(flag in mode for flag in "wax+"):
                raise AssertionError(f"plan() opened {file!r} for writing")
            return real_open(file, mode, *a, **k)

        monkeypatch.setattr(builtins, "open", guarded)
        monkeypatch.setattr(io, "open", guarded)

    def test_inversion_the_guard_catches_a_writing_plan(self, tmp_path, monkeypatch):
        from stapel_tools.composition.react.emitter import ReactEmitter

        real_plan = ReactEmitter.plan

        def leaky(self, comp, request):
            with open(tmp_path / "leak.txt", "w") as fh:
                fh.write("x")
            return real_plan(self, comp, request)

        monkeypatch.setattr(ReactEmitter, "plan", leaky)
        self._guard(monkeypatch)
        comp = _comp()
        with pytest.raises(AssertionError, match="for writing"):
            emitter_for("react").plan(comp, comp.platform("react"))

    def test_plan_touches_no_filesystem(self, tmp_path, monkeypatch):
        """R2's promise: plan() is computed without I/O — run it with a
        read-only cwd and with every write-mode open() refused."""
        comp = _comp(want_landing=True)
        # warm-up: the lazy imports inside plan() may write bytecode caches on
        # a fresh checkout; that is the import system, not the plan
        emitter_for("react").plan(comp, comp.platform("react"))
        ro = tmp_path / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        monkeypatch.chdir(ro)
        self._guard(monkeypatch)
        monkeypatch.setattr(Path, "write_text", lambda *a, **k: pytest.fail("write_text"))
        monkeypatch.setattr(Path, "mkdir", lambda *a, **k: pytest.fail("mkdir"))
        try:
            plan = emitter_for("react").plan(comp, comp.platform("react"))
        finally:
            ro.chmod(0o700)
        assert isinstance(plan, EmitPlan)
        assert plan.root == "frontend"
        assert all(path.startswith("frontend/") for path in plan.files)
        assert "frontend/src/routes.tsx" in plan.files
        assert os.listdir(ro) == []

    def test_plan_is_deterministic(self):
        comp = _comp()
        one = emitter_for("react").plan(comp, comp.platform("react"))
        two = emitter_for("react").plan(comp, comp.platform("react"))
        assert one.files == two.files and one.notes == two.notes

    def test_write_plan_writes_exactly_the_plan(self, tmp_path):
        plan = EmitPlan(platform="x", root="x", files={"x/a.txt": "A", "x/b/c.bin": b"\0"},
                        modes={"x/a.txt": 0o755})
        write_plan(tmp_path, plan)
        assert (tmp_path / "x/a.txt").read_text() == "A"
        assert (tmp_path / "x/b/c.bin").read_bytes() == b"\0"
        assert os.stat(tmp_path / "x/a.txt").st_mode & 0o777 == 0o755
        assert sorted(p.relative_to(tmp_path).as_posix()
                      for p in tmp_path.rglob("*") if p.is_file()) == ["x/a.txt", "x/b/c.bin"]

    def test_validate_raises_the_cli_hint(self):
        comp = _comp(slots={"topbar.end": "nobody.here"})
        with pytest.raises(CompositionError, match="nobody.here"):
            emitter_for("react").validate(comp, comp.platform("react"))


#: Every name that moved, with the module callers import it from.
OLD_NAMES = {
    "stapel_tools._frontend_templates": [
        "CONTAINER_NAV_ROOTS", "NavContractError", "build_nav_route_plan",
        "declared_nav_ids", "nav_entry_surface", "nav_wired_pairs",
        "parent_roots_needed", "validate_nav_entries", "NAV_ENTRY_MOUNTS",
        "COMPOSITE_PAGES", "MONOLITH_CONTAINER_ROOTS", "NAV_BADGE_SOURCES",
        "NAV_ICON_REGISTRY", "page_slots", "nav_entry_paths", "monolith_mount_plan",
    ],
    "stapel_tools._shell_slots": [
        "NONE", "PAGE_SLOTS", "SlotPlan", "SlotOffer", "PageEmbedPlan",
        "offer_slots", "plan_slots", "plan_page_embeds", "parse_slot_args",
    ],
    "stapel_tools.create_project": [
        "FRONTEND_REACT_LIBS", "FRONTEND_COMPOSITES", "_write_frontend_scaffold",
        "_frontend_react_entries",
    ],
}


@pytest.mark.parametrize("module,name", [(m, n) for m, ns in OLD_NAMES.items() for n in ns])
def test_every_old_name_still_imports(module, name):
    assert hasattr(importlib.import_module(module), name), f"{module}.{name}"


def test_moved_planners_are_the_same_objects():
    from stapel_tools import _frontend_templates as F
    from stapel_tools import _shell_slots
    from stapel_tools.composition import nav, slots

    assert F.CONTAINER_NAV_ROOTS is nav.CONTAINER_NAV_ROOTS
    assert _shell_slots.plan_slots is slots.plan_slots
    assert slots.SHELL_TEMPLATES_BY_PLATFORM["kmp"] == {}


def test_patching_the_old_declared_ids_still_reaches_the_planner():
    """stapel-kmp's route-plan fixture generator widens the declared ids with
    ``mock.patch.object(_frontend_templates, "declared_nav_ids", ...)`` and
    calls ``_frontend_templates.build_nav_route_plan``; the move must not
    turn that patch into a no-op."""
    from unittest import mock

    from stapel_tools import _frontend_templates as F

    pairs = [{"package": "@fixture/synthetic", "version": "0.0.0", "nav": [
        {"id": "x.top", "labelKey": "x", "icon": "AppstoreOutlined",
         "route": {"path": "x"}, "placement": {"level": "top"},
         "menuVisibleDefault": True, "requiresAuth": True, "order": 0},
        {"id": "x.child", "labelKey": "x", "icon": "AppstoreOutlined",
         "route": {"path": "c"}, "placement": {"level": "submenu", "parentId": "x.top"},
         "menuVisibleDefault": True, "requiresAuth": True, "order": 1},
    ]}]
    with pytest.raises(F.NavContractError):  # inversion: x.top is declared by nobody
        F.build_nav_route_plan(pairs)
    widened = F.declared_nav_ids() | {"x.top", "x.child"}
    with mock.patch.object(F, "declared_nav_ids", lambda: widened):
        plan = F.build_nav_route_plan(pairs)
    assert [c["path"] for c in plan["app_children"]] == ["x", "x/c"]


def test_planners_annotate_the_brick_not_a_package():
    from stapel_tools.composition.nav import build_nav_route_plan, nav_wired_pairs
    from stapel_tools.composition.react.registry import react_entries

    plan = build_nav_route_plan(nav_wired_pairs(react_entries(["auth"]), auth_wired=True))
    login = plan["absolute_routes"][0]["entry"]
    assert login["_brick"] == "auth" and "_package" not in login


class TestRegistryExport:
    def test_committed_export_is_current(self, capsys):
        assert export.main(["--check"]) == 0, capsys.readouterr().out

    def test_drift_is_caught(self, tmp_path, monkeypatch, capsys):
        """Inversion: a stale committed file fails the check."""
        out = tmp_path / "composition-registry.json"
        out.write_text("{}\n", encoding="utf-8")
        monkeypatch.setattr(export, "OUT", out)
        monkeypatch.setattr(export, "ROUTE_PLANS", tmp_path / "route-plans")
        assert export.main(["--check"]) == 1
        assert "differs" in capsys.readouterr().out

    def test_document_is_neutral(self):
        doc = export.registry_document()
        auth = doc["bricks"]["auth"]
        assert auth["nav"][0]["icon"] == "login"
        assert "component" not in auth["nav"][0]
        assert auth["platforms"]["react"]["package"] == "@stapel/auth-react"
        assert set(doc["icons"]) == set(export.registry_document()["icons"])
        assert doc["composites"]["shop"]["backend"] == "shop"

    def test_route_plan_fixture_names_bricks_and_container_roots(self):
        plan = export.route_plan_document("auth-profiles")
        ids = [r["id"] for r in plan["appChildren"]]
        assert "auth.security" in ids and "admin.root" in ids
        root = next(r for r in plan["appChildren"] if r["id"] == "admin.root")
        assert root["containerRoot"] is True and root["brick"] is None


class TestSyncGateRows:
    SCRIPT = ROOT / "scripts" / "check_nav_manifest_sync.py"

    def _run(self, sibling_root: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.SCRIPT)], capture_output=True, text=True,
            env={**os.environ, "SIBLING_ROOT": str(sibling_root)},
        )

    def test_neutral_row_runs_without_any_sibling(self, tmp_path):
        result = self._run(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "neutral registry and React binding agree" in result.stdout
        assert "kmp: no stapel-kmp checkout" in result.stdout

    def test_kmp_row_says_not_armed_when_a_checkout_exists(self, tmp_path):
        (tmp_path / "stapel-kmp").mkdir()
        result = self._run(tmp_path)
        assert result.returncode == 0
        assert "kmp: NOT ARMED" in result.stdout

    def test_neutral_row_fails_on_a_binding_disagreement(self, monkeypatch, capsys):
        """Inversion: an icon key without a React glyph turns the gate red."""
        sys.path.insert(0, str(ROOT / "scripts"))
        import check_nav_manifest_sync as gate

        from stapel_tools.composition.react import registry

        monkeypatch.delitem(registry.REACT_ICONS, "bell")
        assert gate.check_neutral() == 1
        assert "bell" in capsys.readouterr().out
