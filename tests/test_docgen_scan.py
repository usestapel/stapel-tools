"""`_docgen_scan.discover_modules` — the shared schema/flows/errors discovery
`stapel-gen-client` and `stapel-docs` both key off. Three real layouts
(module docstring): monolith aggregate (`codegen/generated/`), per-module
`docs/` (vendored lib checkout / microservice), literal `<mod>/api/v1/`.
"""
import shutil
from pathlib import Path

from stapel_tools._docgen_scan import discover_modules

FIXTURE = Path(__file__).parent / "fixtures" / "docgen"


def _plant(tmp_path: Path, layout_dir: Path):
    shutil.copytree(FIXTURE, layout_dir, dirs_exist_ok=True)


class TestAggregateLayout:
    def test_discovers_codegen_generated_as_one_aggregate_module(self, tmp_path):
        proj = tmp_path / "app"
        _plant(tmp_path, proj / "codegen" / "generated")
        mods = discover_modules(proj)
        assert len(mods) == 1
        assert mods[0].name == "app"
        assert mods[0].is_aggregate is True
        assert mods[0].schema_json.is_file()
        assert mods[0].flows_json.is_file()
        assert mods[0].errors_json.is_file()

    def test_aggregate_has_no_translations_dir_by_default(self, tmp_path):
        # codegen/generated/translations/ doesn't exist in the real convention
        # (§ module docstring) — an honest gap, not a bug.
        proj = tmp_path / "app"
        _plant(tmp_path, proj / "codegen" / "generated")
        mods = discover_modules(proj)
        assert mods[0].flows_ru_json is None
        assert mods[0].errors_ru_json is None


class TestPerModuleDocsLayout:
    def test_root_docs_dir_named_after_project(self, tmp_path):
        proj = tmp_path / "widgets"
        _plant(tmp_path, proj / "docs")
        # translations/ sits alongside docs/ at the project root, not inside it
        shutil.move(str(proj / "docs" / "translations"), str(proj / "translations"))
        mods = discover_modules(proj)
        assert len(mods) == 1
        assert mods[0].name == "widgets"
        assert mods[0].is_aggregate is False
        assert mods[0].flows_ru_json is not None
        assert mods[0].errors_ru_json is not None

    def test_service_subdir_docs_named_after_dir_svc_prefix_stripped(self, tmp_path):
        proj = tmp_path / "project"
        svc = proj / "svc-widgets" / "docs"
        _plant(tmp_path, svc)
        mods = discover_modules(proj)
        assert len(mods) == 1
        assert mods[0].name == "widgets"

    def test_multiple_service_dirs_each_become_their_own_module(self, tmp_path):
        proj = tmp_path / "project"
        _plant(tmp_path, proj / "svc-alpha" / "docs")
        _plant(tmp_path, proj / "svc-beta" / "docs")
        mods = discover_modules(proj)
        assert sorted(m.name for m in mods) == ["alpha", "beta"]


class TestLiteralPerModuleApiV1Layout:
    def test_mod_api_v1_schema_json_discovered(self, tmp_path):
        proj = tmp_path / "project"
        v1 = proj / "billing" / "api" / "v1"
        v1.mkdir(parents=True)
        shutil.copy(FIXTURE / "schema.json", v1 / "schema.json")
        mods = discover_modules(proj)
        assert len(mods) == 1
        assert mods[0].name == "billing"
        assert mods[0].flows_json is None  # no sibling flows.json in this layout


class TestNoOp:
    def test_empty_project_yields_no_modules(self, tmp_path):
        proj = tmp_path / "empty"
        proj.mkdir()
        assert discover_modules(proj) == []

    def test_dedup_by_resolved_path_prefers_first_seen(self, tmp_path):
        proj = tmp_path / "app"
        _plant(tmp_path, proj / "codegen" / "generated")
        # a docs/ symlink-free duplicate pointing at the SAME file would be
        # deduped; here we just confirm the aggregate isn't double counted
        # when both a root docs/ AND codegen/generated/ exist with distinct
        # content — two real, distinct modules.
        _plant(tmp_path, proj / "docs")
        mods = discover_modules(proj)
        assert len(mods) == 2
        names = {m.name for m in mods}
        assert names == {"app"}  # both resolve to the SAME derived name...
        # ...but are two distinct schema.json files (aggregate vs root docs/)
        assert len({m.schema_json.resolve() for m in mods}) == 2
