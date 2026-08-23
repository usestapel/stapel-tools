"""``scripts/check_nav_manifest_sync.py`` — the drift gate over the nav mirror
(storefront spec §3.8).

Why this file exists at all: the script had been in the repo since the
scripted-navigation wave and was wired into NOTHING — no Makefile, no CI, no
pre-commit hook. The mirror it guards drifted five minors of `@stapel/auth-react`
(pinned 0.10.1 against a published 0.16.0) and lost an entry (`auth.qr_confirm`)
without a word, and every scaffolded project inherited the stale menu. So the
gate now has tests, a `make check` target and a CI step.
"""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_nav_manifest_sync.py"


@pytest.fixture
def gate():
    spec = importlib.util.spec_from_file_location("check_nav_manifest_sync", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pair(root: Path, key: str, package: str, version: str, entries: list) -> None:
    pkg_dir = root / "stapel-react" / "packages" / f"{key}-react"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "nav-manifest.json").write_text(
        json.dumps({"package": package, "version": version, "entries": entries}, indent=2)
        + "\n"
    )


ENTRY = {
    "id": "demo.screen",
    "labelKey": "demo.nav.screen",
    "icon": "AppstoreOutlined",
    "route": {"path": "demo"},
    "component": {"export": "DemoScreen", "subpath": "default"},
    "placement": {"level": "top"},
    "menuVisibleDefault": True,
    "requiresAuth": True,
    "order": 10,
}


def _registry(monkeypatch, mapping):
    import stapel_tools.create_project as cp

    monkeypatch.setattr(cp, "FRONTEND_REACT_LIBS", mapping)


def test_matching_mirror_passes(gate, tmp_path, monkeypatch):
    _pair(tmp_path, "demo", "@stapel/demo-react", "1.2.3", [ENTRY])
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3", "nav": [ENTRY]}
    })
    assert gate.check(tmp_path) == 0


def test_version_drift_fails(gate, tmp_path, monkeypatch):
    _pair(tmp_path, "demo", "@stapel/demo-react", "1.2.3", [ENTRY])
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.0.0", "nav": [ENTRY]}
    })
    assert gate.check(tmp_path) == 1


def test_entry_drift_fails(gate, tmp_path, monkeypatch):
    _pair(tmp_path, "demo", "@stapel/demo-react", "1.2.3", [ENTRY])
    stale = [{**ENTRY, "order": 99}]
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3", "nav": stale}
    })
    assert gate.check(tmp_path) == 1


def test_a_mirrored_pair_with_no_real_manifest_now_FAILS(gate, tmp_path, monkeypatch):
    """This used to be a silent skip, and a silent skip is the exact shape of
    the bug the gate exists for: the mirror claims a nav surface the package no
    longer publishes, and every scaffolded project keeps mounting routes for
    screens that are not there."""
    (tmp_path / "stapel-react" / "packages").mkdir(parents=True)
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3", "nav": [ENTRY]}
    })
    assert gate.check(tmp_path) == 1


def test_a_pair_with_no_mirror_is_not_checked(gate, tmp_path, monkeypatch):
    """A pair that claims nothing is not drifting from anything — cdn, reviews
    and attributes each ship without a nav manifest for a reason recorded in
    the pair itself."""
    (tmp_path / "stapel-react" / "packages").mkdir(parents=True)
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3"}
    })
    assert gate.check(tmp_path) == 0


def test_absent_checkout_skips_rather_than_fails(gate, tmp_path, monkeypatch):
    """A checkout that does not carry stapel-react has nothing to compare
    against, which is not itself a defect."""
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3", "nav": [ENTRY]}
    })
    assert gate.check(tmp_path) == 0


def test_sibling_root_env_overrides_the_default(gate, tmp_path, monkeypatch):
    """The workspace convention every generated `gen:*` invocation already
    uses (`${SIBLING_ROOT:-..}`) — and what lets CI point the gate at a
    checkout that is not literally this repo's sibling."""
    monkeypatch.setenv("SIBLING_ROOT", str(tmp_path))
    assert gate.packages_root() == tmp_path / "stapel-react" / "packages"
    monkeypatch.delenv("SIBLING_ROOT")
    assert gate.packages_root() == SCRIPT.parents[1].parent / "stapel-react" / "packages"


def test_the_live_mirror_is_in_sync(gate):
    """The real one. Skips when the sibling checkout is absent (that is the
    script's own contract), so this is a gate on a developer machine and in CI
    — where the workflow fetches the checkout — and inert elsewhere."""
    assert gate.check() == 0
