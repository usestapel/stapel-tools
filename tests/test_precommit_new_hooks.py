"""api-docs-check / gen-client-check pre-commit wiring (owner directive:
"в pre-commit должен быть регенератор ВСЕГО, что можно" — item 4 of the
task that added `stapel-docs`/`stapel-gen-client`).

`stapel-verify`/`config-manifest-check`/eslint wiring is already covered by
test_frontend_scaffold.py; this file only covers the two NEW hooks: present
in every project type (api-docs-check is backend-only-safe), gen-client-
check only where there's a frontend/ to write into, and both hooks are
real, runnable commands against the freshly generated project (a no-op
exit 0 — neither has a source/override yet)."""
import subprocess
import sys

import yaml

from stapel_tools.create_project import create_project


def _create(tmp_path, name, project_type):
    create_project(
        name=name, project_type=project_type, title=name.capitalize(),
        url="https://x.dev", company_name="X", company_email="x@x.dev",
        modules=["core"], output_dir=tmp_path, use_submodules=False, init_git=False,
    )
    return tmp_path / name


def _hooks_by_id(project_dir):
    cfg = (project_dir / ".pre-commit-config.yaml").read_text()
    parsed = yaml.safe_load(cfg)
    return {h["id"]: h for h in parsed["repos"][0]["hooks"]}


class TestApiDocsHookWiredEverywhere:
    def test_monolith_has_api_docs_check(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        hooks = _hooks_by_id(proj)
        assert "api-docs-check" in hooks
        assert hooks["api-docs-check"]["entry"] == "stapel-docs . --check"

    def test_minimal_has_api_docs_check(self, tmp_path):
        proj = _create(tmp_path, "app", "minimal")
        hooks = _hooks_by_id(proj)
        assert "api-docs-check" in hooks

    def test_microservices_has_api_docs_check(self, tmp_path):
        proj = _create(tmp_path, "app", "microservices")
        hooks = _hooks_by_id(proj)
        assert "api-docs-check" in hooks


class TestGenClientHookFrontendOnly:
    def test_monolith_has_gen_client_check(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        hooks = _hooks_by_id(proj)
        assert "gen-client-check" in hooks
        assert hooks["gen-client-check"]["entry"] == "stapel-gen-client . --check"

    def test_minimal_has_no_gen_client_check(self, tmp_path):
        proj = _create(tmp_path, "app", "minimal")
        hooks = _hooks_by_id(proj)
        assert "gen-client-check" not in hooks

    def test_microservices_has_no_gen_client_check(self, tmp_path):
        proj = _create(tmp_path, "app", "microservices")
        hooks = _hooks_by_id(proj)
        assert "gen-client-check" not in hooks


class TestHooksAreRealNoOpsOnAFreshScaffold:
    """A freshly generated project has no schema.json (stapel-codegen never
    ran) and no override — both hooks must be a clean, green no-op, exit 0,
    not a false-positive blocker on day one."""

    def test_api_docs_check_passes_on_fresh_monolith(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        result = subprocess.run(
            [sys.executable, "-m", "stapel_tools.api_docs", str(proj), "--check"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_gen_client_check_passes_on_fresh_monolith(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        result = subprocess.run(
            [sys.executable, "-m", "stapel_tools.gen_client", str(proj), "--check"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
