"""stapel-feed and stapel-reservations in the scaffold (SPEC-NEXT-BRICKS §A.7, §B.5).

Both bricks and both pairs are built but unreleased. Their rows are inert
until released: a released composite never pulls them in, and
``--allow-unreleased`` installs snapshots of the workspace checkouts INSIDE
the project (an sdist of the brick's HEAD, an npm-packed pair), so the
service image builds. Social gets feed as a companion, booking gets
reservations; a preset fragment for a module the project does not install is
not merged (social's moderation targets without moderation).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from stapel_tools import _frontend_templates as F
from stapel_tools import _unreleased, _wiring
from stapel_tools.create_project import (
    FRONTEND_COMPOSITES,
    FRONTEND_REACT_LIBS,
    STAPEL_LIBS,
    _expand_with_requires,
    composite_pairs,
    composite_report,
    create_project,
    unreleased,
)

BRICKS = ("feed", "reservations")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _fake_brick(root: Path, key: str, version: str = "0.1.0") -> Path:
    repo = root / f"stapel-{key}"
    repo.mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "stapel-{key}"\nversion = "{version}"\n')
    _git(repo, "init", "-q")
    _git(repo, "add", "pyproject.toml")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    # An uncommitted edit is not part of the snapshot.
    (repo / "dirty.txt").write_text("not committed")
    return repo


def _fake_pair(root: Path, key: str) -> Path:
    package = root / "stapel-react" / "packages" / f"{key}-react"
    (package / "dist").mkdir(parents=True)
    (package / "dist" / "index.js").write_text("export {};\n")
    (package / "package.json").write_text(json.dumps({
        "name": f"@stapel/{key}-react", "version": "0.0.0", "files": ["dist"],
        "main": "./dist/index.js"}))
    return package


def _monolith(tmp_path: Path, name: str, modules: list[str], **kw) -> Path:
    create_project(
        name=name, project_type="monolith", title=name, url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=["core", *modules],
        output_dir=tmp_path, use_submodules=False, init_git=False, **kw)
    return tmp_path / name


def _minimal(tmp_path: Path, name: str, modules: list[str], **kw) -> Path:
    create_project(
        name=name, project_type="minimal", title=name, url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=["core", *modules],
        output_dir=tmp_path, use_submodules=False, init_git=False, **kw)
    return tmp_path / name


# ─── the rows ───────────────────────────────────────────────────────────────


class TestRows:
    def test_backend_rows_are_inert_until_released(self):
        for key in BRICKS:
            info = STAPEL_LIBS[key]
            assert info["released"] is False and info["ahead_of_pypi"] is True, key
            assert info["dir"] == f"stapel_{key}"
            assert info["url_prefix"] == f"{key}/api/"
            assert info["http"] is True

    def test_feed_needs_the_graph_and_the_pictures(self):
        assert STAPEL_LIBS["feed"]["requires"] == ["profiles", "cdn"]
        assert "beat" not in STAPEL_LIBS["feed"]
        assert _expand_with_requires(["feed"]) == ["cdn", "profiles", "feed"]

    def test_reservations_beat_and_posture_mock(self):
        info = STAPEL_LIBS["reservations"]
        assert info["requires"] == []
        assert info["beat"] == ("stapel_reservations.beat", "get_reservations_beat_schedule")
        assert info["prototype_dev"] == {"PAYMENT": "mock"}

    def test_pins_the_bricks_stand_on(self):
        # profiles.social_graph (0.23.0) and listings.bookables (0.29.0).
        assert STAPEL_LIBS["profiles"]["pin"] == "0.23.0"
        assert STAPEL_LIBS["listings"]["pin"] == "0.29.0"

    def test_pairs_mirror_and_are_unreleased(self):
        for key in BRICKS:
            pair = FRONTEND_REACT_LIBS[key]
            assert pair["released"] is False
            assert pair["package"] == f"@stapel/{key}-react"
            for entry in pair["nav"]:
                assert entry["id"] in F.NAV_ENTRY_MOUNTS, entry["id"]
        assert [e["id"] for e in FRONTEND_REACT_LIBS["feed"]["embeds"]] == [
            "feed.author_posts_action", "feed.compose_fab"]
        assert [e["id"] for e in FRONTEND_REACT_LIBS["reservations"]["embeds"]] == [
            "reservations.book_action", "reservations.upcoming_badge"]

    def test_mirrors_mount(self):
        entries = [{"key": k, **FRONTEND_REACT_LIBS[k]} for k in ("cdn", "profiles", *BRICKS)]
        for key in BRICKS:
            F.validate_nav_entries(FRONTEND_REACT_LIBS[key]["nav"])
        manifests = F.public_nav_manifests(F.public_nav_pairs(entries), app_package="app")
        plan = F.build_public_route_plan(manifests)
        mounts = F.public_mount_plan(plan, {}, pairs=("cdn", "profiles", *BRICKS))
        assert "src/pages/FeedHomePage.tsx" in mounts["pages"]
        assert mounts["elements"]["feed.home"] == "<FeedHomePage />"


# ─── companions ─────────────────────────────────────────────────────────────


class TestCompanions:
    @pytest.mark.parametrize("composite,brick", [("social", "feed"), ("booking", "reservations")])
    def test_released_composite_does_not_pull_the_unreleased_brick(self, composite, brick):
        assert brick in STAPEL_LIBS[composite]["companions"]
        assert brick not in _expand_with_requires([composite])
        assert unreleased(_expand_with_requires([composite])) == []

    @pytest.mark.parametrize("composite,brick", [("social", "feed"), ("booking", "reservations")])
    def test_allowed_it_joins(self, composite, brick):
        assert brick in _expand_with_requires([composite], unreleased_companions=True)

    def test_not_a_member_of_other_composites(self):
        for composite in ("shop", "classified"):
            closure = _expand_with_requires([composite], unreleased_companions=True)
            assert "feed" not in closure and "reservations" not in closure

    def test_frontend_pairs_and_report(self):
        assert "feed" in FRONTEND_COMPOSITES["social"]["pairs"]
        assert "reservations" in FRONTEND_COMPOSITES["booking"]["pairs"]
        assert "feed" not in composite_pairs("social")
        assert "feed" in composite_pairs("social", allow_unreleased=True)
        assert any("NOT INSTALLED — feed" in line for line in composite_report("social"))

    def test_explicit_selection_still_refused_without_the_flag(self, tmp_path):
        with pytest.raises(SystemExit):
            _minimal(tmp_path, "p", ["social", "feed"])

    def test_social_alone_scaffolds_without_feed(self, tmp_path):
        proj = _minimal(tmp_path, "p", ["social"])
        settings = (proj / "config" / "settings.py").read_text()
        assert "stapel_feed" not in settings
        assert "stapel-feed" not in (proj / "requirements.txt").read_text()


# ─── preset fragments for modules outside the selection ─────────────────────


class TestUnselectedFragments:
    def test_moderation_fragment_skipped_without_moderation(self):
        assert "STAPEL_MODERATION" in _wiring.unselected_namespaces(["social", "chat"])
        assert "STAPEL_MODERATION" not in _wiring.unselected_namespaces(
            ["social", "chat", "moderation"])

    def _run(self, defaults: dict, unselected: list[str]) -> dict:
        mod = types.ModuleType("_fake_social_preset")
        mod.SETTINGS_DEFAULTS = defaults
        sys.modules[mod.__name__] = mod
        try:
            block = _wiring._UNSELECTED.format(
                unselected="".join(f'    "{n}",\n' for n in unselected)
            ) + _wiring._PRESET_MERGE.format(modules=f'"{mod.__name__}"', composites_doc="social")
            ns: dict = {}
            exec(block, ns)  # noqa: S102 - the generated settings code under test
        finally:
            del sys.modules[mod.__name__]
        return ns

    def test_generated_merge_skips_only_the_unselected(self):
        defaults = {
            "STAPEL_REVIEWS": {"TARGET_TYPES": {"user": {}}},
            "STAPEL_MODERATION": {"TARGET_TYPES": {"post": {"id_field": "post_id"}}},
        }
        ns = self._run(defaults, ["STAPEL_MODERATION"])
        assert "STAPEL_MODERATION" not in ns
        assert ns["STAPEL_REVIEWS"] == {"TARGET_TYPES": {"user": {}}}
        ns = self._run(defaults, [])
        assert ns["STAPEL_MODERATION"]["TARGET_TYPES"]["post"]["id_field"] == "post_id"

    def test_order_independent(self):
        assert _wiring.render_preset_defaults(["social", "feed", "chat"]) == \
            _wiring.render_preset_defaults(["chat", "feed", "social"])


# ─── cdn asset types ────────────────────────────────────────────────────────


def _cdn_asset_types(proj: Path) -> str:
    text = (proj / "config" / "settings.py").read_text()
    start = text.index("STAPEL_CDN")
    return text[start:text.index("}", start)]


class TestAssetTypes:
    def test_feed_adds_post(self, tmp_path):
        proj = _minimal(tmp_path, "p", ["feed"], allow_unreleased=True)
        block = _cdn_asset_types(proj)
        assert "'avatar'" in block and "'post'" in block and "'product'" not in block

    def test_merged_with_listings_order_independent(self, tmp_path):
        a = _minimal(tmp_path / "a", "p", ["feed", "listings"], allow_unreleased=True)
        b = _minimal(tmp_path / "b", "p", ["listings", "feed"], allow_unreleased=True)
        assert _cdn_asset_types(a) == _cdn_asset_types(b)
        assert "('avatar', 'product', 'post')" in _cdn_asset_types(a)

    def test_explicit_list_is_merged_not_replaced(self, tmp_path):
        proj = _minimal(tmp_path, "p", ["feed"], allow_unreleased=True,
                        module_config={"cdn": {"ASSET_TYPES": ["avatar", "banner", "post"]}})
        assert "('avatar', 'banner', 'post')" in _cdn_asset_types(proj)


# ─── snapshots of unreleased checkouts ──────────────────────────────────────


class TestVendoring:
    def test_backend_snapshot_is_head_not_the_working_tree(self, tmp_path):
        _fake_brick(tmp_path / "ws", "feed")
        snap = _unreleased.snapshot_backend(tmp_path / "ws", "feed", tmp_path / "out")
        assert snap.path.name == "stapel-feed-0.1.0.tar.gz"
        names = subprocess.run(["tar", "-tzf", str(snap.path)], capture_output=True,
                               text=True, check=True).stdout.split()
        assert "stapel-feed-0.1.0/pyproject.toml" in names
        assert not any(n.endswith("dirty.txt") for n in names)
        assert len(snap.commit) == 40

    def test_dockerfile_copies_vendor_before_install(self, tmp_path):
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM x\nCOPY svc-p/requirements.txt .\nRUN pip install -r requirements.txt\n")
        _unreleased.vendor_dockerfile(dockerfile)
        _unreleased.vendor_dockerfile(dockerfile)  # idempotent
        assert dockerfile.read_text() == (
            "FROM x\nCOPY svc-p/vendor ./vendor\nCOPY svc-p/requirements.txt .\n"
            "RUN pip install -r requirements.txt\n")

    def test_missing_checkout_keeps_the_editable_line_and_says_so(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("SIBLING_ROOT", str(tmp_path / "nowhere"))
        proj = _minimal(tmp_path, "p", ["feed"], allow_unreleased=True)
        assert "-e ../stapel-feed" in (proj / "requirements.txt").read_text()
        assert "no workspace checkout" in capsys.readouterr().err

    @pytest.mark.skipif(shutil.which("npm") is None, reason="npm packs the pair")
    def test_monolith_vendors_both_halves(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        _fake_brick(ws, "feed")
        _fake_pair(ws, "feed")
        monkeypatch.setenv("SIBLING_ROOT", str(ws))
        proj = _monolith(tmp_path, "p", ["social"], allow_unreleased=True)
        svc = proj / "svc-p"
        reqs = (svc / "requirements.txt").read_text()
        assert "./vendor/stapel-feed-0.1.0.tar.gz" in reqs
        assert "-e ../stapel-feed" not in reqs
        assert (svc / "vendor" / "stapel-feed-0.1.0.tar.gz").is_file()
        assert "COPY svc-p/vendor ./vendor" in (svc / "Dockerfile").read_text()
        deps = json.loads((proj / "frontend" / "package.json").read_text())["dependencies"]
        assert deps["@stapel/feed-react"] == "file:vendor/stapel-feed-react-0.0.0.tgz"
        assert (proj / "frontend" / "vendor" / "stapel-feed-react-0.0.0.tgz").is_file()
        assert (proj / "frontend" / "src" / "pages" / "FeedHomePage.tsx").is_file()
        base = (svc / "config" / "settings" / "base.py").read_text()
        assert '"stapel_feed"' in base or "'stapel_feed'" in base


# ─── the CI flow ────────────────────────────────────────────────────────────


class TestBrickFlows:
    ROOT = Path(__file__).resolve().parent.parent

    def test_script_names_both_flows(self):
        result = subprocess.run(
            [sys.executable, str(self.ROOT / "scripts" / "e2e_brick_flows.py")],
            capture_output=True, text=True)
        assert result.returncode == 2
        assert "feed|reservations" in result.stderr

    def test_ci_runs_them_per_composite(self):
        ci = (self.ROOT / ".github" / "workflows" / "ci.yml").read_text()
        assert '"social feed" "booking reservations"' in ci
        assert "scripts/e2e_brick_flows.py" in ci
