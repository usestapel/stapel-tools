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
    """A version drift the gate can act on: the registry claims a version the
    workspace has never built. That is the stale-mirror direction — the one
    this gate exists for — and it is told apart from the unpublished-bump
    direction below by which side is newer, nothing else.
    """
    _pair(tmp_path, "demo", "@stapel/demo-react", "1.0.0", [ENTRY])
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3", "nav": [ENTRY]}
    })
    assert gate.check(tmp_path) == 1


def test_a_checkout_bumped_ahead_with_identical_entries_is_not_drift(
    gate, tmp_path, monkeypatch, capsys
):
    """The registry pins what npm SERVES. Between a workspace bump and its
    publish the checkout is ahead, and the mirror may not follow — pinning an
    unpublished version is a 404 on `npm install` for every generated
    project. Nothing a container mounts differs, so this is a printed line,
    not a failure."""
    _pair(tmp_path, "demo", "@stapel/demo-react", "1.3.0", [ENTRY])
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3", "nav": [ENTRY]}
    })
    assert gate.check(tmp_path) == 0
    out = capsys.readouterr().out
    assert "UNPUBLISHED BUMP" in out
    assert "demo" in out


def test_a_bump_npm_ALREADY_SERVES_is_a_stale_pin_not_a_pending_publish(
    gate, tmp_path, monkeypatch, capsys
):
    """The hole the forgiving branch above had: it assumed the publish was
    still pending and never asked. Measured 2026-09-14 — all fourteen pairs it
    was reporting had shipped weeks earlier (search pinned 0.15.0 against a
    published 0.48.1). When npm SERVES the checkout's version there is nothing
    to wait for: the pin is stale and every generated project installs a
    version the fleet left behind."""
    _pair(tmp_path, "demo", "@stapel/demo-react", "1.3.0", [ENTRY])
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3", "nav": [ENTRY]}
    })
    asked: list = []

    def served(package, version):
        asked.append((package, version))
        return True

    assert gate.check(
        tmp_path, ask_registry=True, published=served, latest=lambda *_: None,
    ) == 1
    assert asked == [("@stapel/demo-react", "1.3.0")]
    out = capsys.readouterr().out
    assert "STALE PIN" in out
    assert "UNPUBLISHED BUMP" not in out


def test_a_bump_npm_does_NOT_serve_stays_the_benign_unpublished_bump(
    gate, tmp_path, monkeypatch, capsys
):
    """The window the exemption was written for is still real: asked, npm says
    no, and the mirror is as correct as it is allowed to be."""
    _pair(tmp_path, "demo", "@stapel/demo-react", "1.3.0", [ENTRY])
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3", "nav": [ENTRY]}
    })
    assert gate.check(
        tmp_path, ask_registry=True, published=lambda *_: False, latest=lambda *_: None,
    ) == 0
    assert "UNPUBLISHED BUMP" in capsys.readouterr().out


def test_an_UNASKABLE_registry_never_becomes_a_verdict(
    gate, tmp_path, monkeypatch, capsys
):
    """No node, no network, a registry 500 — `npm_published` returns None and
    the benign branch stands. A gate that fails on a question it could not ask
    is a gate somebody turns off; one that PASSES on it would be worse still,
    so the unasked case keeps printing the loud line."""
    _pair(tmp_path, "demo", "@stapel/demo-react", "1.3.0", [ENTRY])
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3", "nav": [ENTRY]}
    })
    assert gate.check(
        tmp_path, ask_registry=True, published=lambda *_: None, latest=lambda *_: None,
    ) == 0
    assert "UNPUBLISHED BUMP" in capsys.readouterr().out


def test_without_the_flag_npm_is_never_asked(gate, tmp_path, monkeypatch):
    """`make check` runs this on a laptop with no node and must not reach for
    the network at all — the flag is the whole switch."""
    _pair(tmp_path, "demo", "@stapel/demo-react", "1.3.0", [ENTRY])
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3", "nav": [ENTRY]}
    })

    def boom(*_):  # pragma: no cover - the point is that it never runs
        raise AssertionError("npm was asked without --registry")

    assert gate.check(tmp_path, published=boom, latest=boom) == 0


def test_npm_published_reports_None_when_npm_is_absent(gate, monkeypatch):
    """The seam itself: an OSError from `npm` missing is 'not asked', never
    'not published'."""
    def missing(*_a, **_k):
        raise FileNotFoundError("npm")

    monkeypatch.setattr(gate.subprocess, "run", missing)
    assert gate.npm_published("@stapel/demo-react", "1.3.0") is None


def test_npm_latest_reports_None_when_npm_is_absent(gate, monkeypatch):
    """Same seam, same contract, for the pin-table walk's own registry
    question: no node is 'not asked', never 'nothing published'."""
    def missing(*_a, **_k):
        raise FileNotFoundError("npm")

    monkeypatch.setattr(gate.subprocess, "run", missing)
    assert gate.npm_latest("@stapel/demo-react") is None


def test_a_NAV_LESS_pair_with_a_stale_pin_FAILS_under_registry(
    gate, tmp_path, monkeypatch
):
    """attributes, cdn, currencies, reviews and vocabularies publish no nav
    manifest at all, so the nav-mirror walk above never reaches them and
    never asks the registry about them either — the exact blind spot that let
    two pairs sit below another pair's declared peer floor. The pin table is
    the WHOLE `FRONTEND_REACT_LIBS`, mirrored or not, and this pair (no "nav"
    key, same shape as attributes/cdn/etc.) is pinned behind what npm serves."""
    (tmp_path / "stapel-react" / "packages").mkdir(parents=True)
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3"},
    })
    assert gate.check(
        tmp_path, ask_registry=True,
        published=lambda *_: None,
        latest=lambda pkg: {"@stapel/demo-react": "1.5.0"}.get(pkg),
    ) == 1


def test_a_SUBSTRATE_pin_with_a_stale_pin_FAILS_under_registry(
    gate, tmp_path, monkeypatch, capsys
):
    """The substrate every nav-wired project also installs — core,
    shell-react, tokens-antd, tokens, image, eslint-plugin — never went
    through the nav-mirror walk at all (they are not `FRONTEND_REACT_LIBS`
    entries), so a raised peer floor in any of them was invisible to
    `--registry` too. `full_pin_table()` walks them by name."""
    (tmp_path / "stapel-react" / "packages").mkdir(parents=True)
    _registry(monkeypatch, {})

    def latest(pkg):
        return {"@stapel/core": "9.9.9"}.get(pkg)

    assert gate.check(
        tmp_path, ask_registry=True,
        published=lambda *_: None,
        latest=latest,
    ) == 1
    out = capsys.readouterr().out
    assert "STALE PIN" in out
    assert "@stapel/core" in out


def test_pin_table_walk_stays_benign_when_npm_cannot_be_asked(
    gate, tmp_path, monkeypatch
):
    """Missing npm/network on any one package must not become a verdict —
    the same `None`-is-not-a-verdict contract `npm_published` already keeps,
    now over the whole pin table."""
    (tmp_path / "stapel-react" / "packages").mkdir(parents=True)
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3"},
    })
    assert gate.check(
        tmp_path, ask_registry=True,
        published=lambda *_: None,
        latest=lambda *_: None,
    ) == 0


def test_full_pin_table_covers_the_substrate_and_every_registered_pair(gate):
    """`full_pin_table()` itself: every FRONTEND_REACT_LIBS key is in it
    (nav-bearing or not — the real registry is not monkeypatched here), plus
    the six named substrate packages."""
    table = gate.full_pin_table()
    packages = {pkg for _src, pkg, _v in table}
    import stapel_tools.create_project as cp

    for info in cp.FRONTEND_REACT_LIBS.values():
        assert info["package"] in packages
    for expected in (
        "@stapel/core", "@stapel/shell-react", "@stapel/tokens-antd",
        "@stapel/tokens", "@stapel/image", "@stapel/eslint-plugin",
    ):
        assert expected in packages, expected


def test_a_checkout_bumped_ahead_with_DIFFERENT_entries_still_fails(
    gate, tmp_path, monkeypatch
):
    """The narrow exemption is for a version and nothing else. The moment the
    entries disagree, the checkout and the registry disagree about what a
    container mounts and only a person can say which is right."""
    moved = {**ENTRY, "order": 99}
    _pair(tmp_path, "demo", "@stapel/demo-react", "1.3.0", [moved])
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3", "nav": [ENTRY]}
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


def test_a_mirrored_pair_whose_real_manifest_went_empty_FAILS(gate, tmp_path, monkeypatch):
    """An `entries: []` file is the same claim as a missing one — the pair
    publishes no nav surface — so it gets the same verdict. Treating "the file
    parses" as "the surface is there" is how a retired screen keeps a route."""
    _pair(tmp_path, "demo", "@stapel/demo-react", "1.2.3", [])
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3", "nav": [ENTRY]}
    })
    assert gate.check(tmp_path) == 1


def test_a_pair_with_no_mirror_AND_no_real_manifest_is_in_sync(gate, tmp_path, monkeypatch):
    """A pair that claims nothing against a pair that publishes nothing — cdn,
    reviews and attributes each ship that way for a reason recorded in the pair
    itself. This is the ONLY shape a mirror-less pair may have."""
    (tmp_path / "stapel-react" / "packages").mkdir(parents=True)
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3"}
    })
    assert gate.check(tmp_path) == 0


def test_a_registered_pair_that_publishes_entries_with_NO_mirror_FAILS(
    gate, tmp_path, monkeypatch
):
    """The blind spot: the gate used to iterate the MIRRORS, so a registered
    pair that grew a nav surface nobody mirrored was compared against nothing
    and the gate printed a green line. Five pairs (billing, calendar, forms,
    recordings, workspaces) sat that way with 15 published entries no
    scaffolded container mounted."""
    _pair(tmp_path, "demo", "@stapel/demo-react", "1.2.3", [ENTRY])
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3"}
    })
    assert gate.check(tmp_path) == 1


def test_an_EMPTY_mirror_against_a_publishing_pair_FAILS(gate, tmp_path, monkeypatch):
    """`"nav": []` is not a way to opt out of the walk: it says "this pair has
    no screens", which is a claim, and it is false here."""
    _pair(tmp_path, "demo", "@stapel/demo-react", "1.2.3", [ENTRY])
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3", "nav": []}
    })
    assert gate.check(tmp_path) == 1


def test_every_registered_pair_is_walked_not_only_the_mirrored_ones(
    gate, tmp_path, monkeypatch, capsys
):
    """The count the gate prints is over the WHOLE registry, so a reader can
    tell the walk covered the pairs that carry no mirror rather than skipping
    them."""
    _pair(tmp_path, "demo", "@stapel/demo-react", "1.2.3", [ENTRY])
    (tmp_path / "stapel-react" / "packages" / "quiet-react").mkdir()
    _registry(monkeypatch, {
        "demo": {"package": "@stapel/demo-react", "version": "1.2.3", "nav": [ENTRY]},
        "quiet": {"package": "@stapel/quiet-react", "version": "0.1.0"},
    })
    assert gate.check(tmp_path) == 0
    assert "walked all 2" in capsys.readouterr().out


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
