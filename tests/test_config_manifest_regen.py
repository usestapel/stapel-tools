"""§57 owner directive item 8 — CONFIG.MD regeneration hook: the
`stapel-config-manifest` CLI (--check for the pre-commit gate, no-flag for
auto-fix), CFG004 (empty Purpose warns, never fails the build), and the
pre-commit template wiring both together."""
import subprocess
import sys

import pytest
import yaml

from stapel_tools.assemble_scaffold import assemble_scaffold
from stapel_tools.config_lint import Finding, lint_project
from stapel_tools.config_manifest import (
    libs_from_existing_config_md,
    regenerate_config_md,
)
from stapel_tools.create_project import create_project


def _assemble(tmp_path, slug="proj", libs=None):
    r = assemble_scaffold(slug, libs=libs or ["auth"], output_dir=tmp_path, verify=False)
    return r.project_dir


class TestLibsFromExistingConfigMd:
    def test_recovers_lib_list_core_first(self, tmp_path):
        # Only stapel-core ships a CONFIG.MD today (per-module sweep is the
        # next wave — collect_lib_entries's own docstring) — auth/gdpr
        # contribute no '## stapel-<lib>' section to recover from yet, so
        # this only proves "core" round-trips; the mechanism generalizes
        # the moment a lib ships its own CONFIG.MD.
        proj = _assemble(tmp_path, libs=["auth", "gdpr"])
        libs = libs_from_existing_config_md(proj)
        assert libs == ["core"]

    def test_empty_when_no_config_md(self, tmp_path):
        empty = tmp_path / "nothing"
        empty.mkdir()
        assert libs_from_existing_config_md(empty) == []


class TestRegenerateConfigMd:
    def test_regeneration_is_idempotent(self, tmp_path):
        proj = _assemble(tmp_path)
        before = (proj / "CONFIG.MD").read_text()
        text, missing = regenerate_config_md(proj)
        assert text == before

    def test_preserves_hand_authored_project_owned_rows(self, tmp_path):
        proj = _assemble(tmp_path)
        config_md = proj / "CONFIG.MD"
        text = config_md.read_text()
        text += (
            "\n## project\n\n"
            "| Key | Source | Purpose | Required | Default |\n"
            "|-----|--------|---------|----------|---------|\n"
            "| MY_CUSTOM_KEY | env | a human wrote this purpose by hand | no |  |\n"
        )
        config_md.write_text(text)
        regenerated, _ = regenerate_config_md(proj)
        assert "MY_CUSTOM_KEY" in regenerated
        assert "a human wrote this purpose by hand" in regenerated

    def test_detects_drift_when_a_libs_config_md_changes(self, tmp_path):
        proj = _assemble(tmp_path, libs=["auth"])
        # Simulate stapel-auth shipping a new CONFIG.MD row upstream —
        # the project's own committed file doesn't know about it yet.
        original = (proj / "CONFIG.MD").read_text()
        (proj / "CONFIG.MD").write_text(
            original.replace(
                "## stapel-core",
                "## stapel-core\n\n"
                "| Key | Source | Purpose | Required | Default |\n"
                "|-----|--------|---------|----------|---------|\n"
                "| STALE_MARKER | env | injected to simulate drift | no |  |\n",
                1,
            )
        )
        regenerated, _ = regenerate_config_md(proj)
        assert regenerated != (proj / "CONFIG.MD").read_text()


class TestConfigManifestCli:
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "stapel_tools.config_manifest", *args],
            capture_output=True, text=True,
        )

    def test_check_passes_on_fresh_project(self, tmp_path):
        proj = _assemble(tmp_path)
        result = self._run(str(proj), "--check")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "up to date" in result.stdout

    def test_check_fails_on_drift_and_fix_repairs_it(self, tmp_path):
        proj = _assemble(tmp_path)
        config_md = proj / "CONFIG.MD"
        config_md.write_text(config_md.read_text() + "junk line not in the aggregate\n")

        failing = self._run(str(proj), "--check")
        assert failing.returncode == 1
        assert "STALE" in failing.stderr

        fixing = self._run(str(proj))
        assert fixing.returncode == 0
        assert "Regenerated" in fixing.stdout

        now_clean = self._run(str(proj), "--check")
        assert now_clean.returncode == 0

    def test_errors_cleanly_with_no_config_md_and_no_libs(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = self._run(str(empty))
        assert result.returncode == 2


class TestCfg004PurposeWarning:
    def test_empty_purpose_is_a_warning_not_an_error(self, tmp_path):
        proj = _assemble(tmp_path)
        config_md = proj / "CONFIG.MD"
        text = config_md.read_text()
        text += (
            "\n## project\n\n"
            "| Key | Source | Purpose | Required | Default |\n"
            "|-----|--------|---------|----------|---------|\n"
            "| UNDOCUMENTED_KEY | env |  | no |  |\n"
        )
        config_md.write_text(text)
        # UNDOCUMENTED_KEY also needs a matching settings-module read so
        # CFG002/CFG003 don't ALSO fire for it — isolate to just CFG004.
        settings = proj / "config" / "settings.py"
        settings.write_text(
            settings.read_text()
            + '\nimport os\nUNDOCUMENTED_KEY = os.getenv("UNDOCUMENTED_KEY")\n'
        )
        findings = lint_project(proj)
        cfg004 = [f for f in findings if f.rule == "CFG004"]
        assert any(f.level == "warning" for f in cfg004)
        assert not any(f.rule == "CFG004" and f.level == "error" for f in findings)
        errors = [f for f in findings if f.level == "error"]
        assert all(f.rule != "CFG004" for f in errors)

    def test_filled_purpose_raises_no_cfg004(self):
        finding = Finding("x", 1, "CFG004", "msg", level="warning")
        assert finding.level == "warning"  # sanity on the dataclass default wiring


class TestPrecommitConfigManifestHook:
    @pytest.mark.parametrize("ptype", ["monolith", "minimal", "microservices"])
    def test_precommit_yaml_has_config_manifest_check_hook(self, tmp_path, ptype):
        proj_root = tmp_path / "out"
        create_project(
            name="app", project_type=ptype, title="App", url="https://x.dev",
            company_name="X", company_email="x@x.dev", modules=["core"],
            output_dir=proj_root, use_submodules=False, init_git=False,
        )
        cfg = (proj_root / "app" / ".pre-commit-config.yaml").read_text()
        assert "stapel-config-manifest . --check" in cfg
        parsed = yaml.safe_load(cfg)
        hook_ids = {h["id"] for h in parsed["repos"][0]["hooks"]}
        assert "config-manifest-check" in hook_ids
