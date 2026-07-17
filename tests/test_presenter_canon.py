"""§55 presenter discipline as part of the OSS generators (owner directive):
a generated project/module is presenter-canonical FROM BIRTH — views build
DTOs only through get_presenter(...), presenters.py ships with declare_swap,
PRESENTERS.MD is generated through core's exported hook, and the whole
generated tree passes stapel-verify (SWAP001/SWAP002 included) from scratch."""
import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from stapel_tools.assemble_scaffold import assemble_scaffold
from stapel_tools.new_library import scaffold_library
from stapel_tools.new_module import scaffold_module
from stapel_tools.swap_lint import lint_paths as swap_lint_paths


def _swap_findings(root: Path):
    return [v for v in swap_lint_paths([str(root)])]


class TestLibraryScaffoldPresenterCanon:
    @pytest.fixture()
    def lib(self, tmp_path):
        return scaffold_library("widgets", "Widgets", tmp_path, kind="module", git=False)

    def test_presenters_py_scaffolded_with_declare_swap(self, lib):
        text = (lib / "presenters.py").read_text()
        assert 'PING_PRESENTER_KEY = "STAPEL_WIDGETS_PING_PRESENTER"' in text
        assert 'declare_swap(PING_PRESENTER_KEY, DEFAULT_PING_PRESENTER)' in text
        assert '"stapel_widgets.presenters.PingPresenter"' in text
        assert "def get_ping_presenter" in text
        # the DTO is instantiated here — and only here
        assert "PingResponse(greeting=" in text

    def test_view_goes_through_get_presenter_not_the_dto(self, lib):
        views = (lib / "views.py").read_text()
        assert "from .presenters import get_ping_presenter" in views
        assert "get_ping_presenter()().present()" in views
        # never instantiates the DTO in the view (SWAP002 shape)
        assert "PingResponse(" not in views
        assert "from .dto import" not in views

    def test_scaffolded_library_is_swap_lint_clean(self, lib):
        findings = _swap_findings(lib)
        assert findings == [], [str(f) for f in findings]

    def test_scaffolded_templates_compile(self, lib):
        for name in ("presenters.py", "views.py", "dto.py", "serializers.py"):
            compile((lib / name).read_text(), name, "exec")


class TestModuleScaffoldPresenterCanon:
    @pytest.fixture()
    def svc(self, tmp_path):
        service = tmp_path / "svc-app"
        (service / "apps").mkdir(parents=True)
        (service / "manage.py").write_text("# manage\n")
        scaffold_module("orders", "Orders", service)
        return service

    def test_presenters_py_scaffolded(self, svc):
        text = (svc / "apps" / "orders" / "presenters.py").read_text()
        assert 'PRESENTER_KEY = "ORDERS_PRESENTER"' in text
        assert "declare_swap(PRESENTER_KEY, DEFAULT_PRESENTER)" in text
        assert '"apps.orders.presenters.OrdersPresenter"' in text
        assert "def get_orders_presenter" in text
        compile(text, "presenters.py", "exec")

    def test_views_document_the_canon_and_compile(self, svc):
        views = (svc / "apps" / "orders" / "views.py").read_text()
        assert "get_orders_presenter" in views  # the canon snippet
        assert "SWAP002" in views               # the anti-pattern named
        compile(views, "views.py", "exec")

    def test_scaffolded_module_is_swap_lint_clean(self, svc):
        findings = _swap_findings(svc)
        assert findings == [], [str(f) for f in findings]


class TestGeneratedProjectPassesStapelVerify:
    """Item 4 — THE gate: a project generated via stapel-assemble passes
    stapel-verify (all composed linters, SWAP001/SWAP002 included) from
    scratch, zero debt."""

    def test_assembled_project_verify_exit_zero(self, tmp_path):
        from stapel_tools.verify import main as verify_main

        result = assemble_scaffold("app", libs=["auth"], output_dir=tmp_path, verify=False)
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            code = verify_main([str(result.project_dir)])
        assert code == 0, buf_out.getvalue() + buf_err.getvalue()

    def test_assembled_project_has_presenters_md_from_core_hook(self, tmp_path):
        import importlib.util

        # find_spec, not import: importing the catalog module pulls Django
        # settings into THIS test process (REST_FRAMEWORK access at import).
        core_importable = importlib.util.find_spec("stapel_core") is not None
        result = assemble_scaffold("app", libs=[], output_dir=tmp_path, verify=False)
        presenters_md = result.project_dir / "PRESENTERS.MD"
        if not core_importable:
            pytest.skip("stapel-core not importable — generation degrades to a note")
        assert presenters_md.exists()
        text = presenters_md.read_text()
        # rendered by core's render_presenters_md — carries its section canon
        assert "## Swap points" in text or "## Presenters" in text

    def test_presenter_catalog_check_hook_in_precommit(self, tmp_path):
        import yaml

        result = assemble_scaffold("app", libs=[], output_dir=tmp_path, verify=False)
        cfg = (result.project_dir / ".pre-commit-config.yaml").read_text()
        parsed = yaml.safe_load(cfg)
        hooks = {h["id"]: h for h in parsed["repos"][0]["hooks"]}
        assert "presenter-catalog-check" in hooks
        assert "presenter_catalog --check" in hooks["presenter-catalog-check"]["entry"]

    def test_agents_md_carries_right_wrong_presenter_snippets(self, tmp_path):
        result = assemble_scaffold("app", libs=[], output_dir=tmp_path, verify=False)
        agents = (result.project_dir / "AGENTS.md").read_text()
        assert "get_thing_presenter()().present(obj)" in agents  # CORRECT shape
        assert "WRONG (SWAP002" in agents                        # anti-pattern named
        assert "presenter-catalog-check" in agents

    def test_generated_presenters_md_is_fresh_by_its_own_check(self, tmp_path):
        """The freshness hook the pre-commit config wires must be green on a
        fresh generation (no false positive on day one)."""
        import os
        import subprocess

        import importlib.util

        if importlib.util.find_spec("stapel_core") is None:
            pytest.skip("stapel-core not importable")
        from stapel_tools.assemble_scaffold import _load_dotenv

        result = assemble_scaffold("app", libs=[], output_dir=tmp_path, verify=False)
        proc = subprocess.run(
            [sys.executable, "manage.py", "presenter_catalog", "--check"],
            cwd=result.project_dir, capture_output=True, text=True,
            # manage.py runs the full system-check pass first — core's
            # config.E001 needs the project's own .env (SECRET_KEY), same
            # reason assemble_scaffold's gates load it.
            env={**os.environ, **_load_dotenv(result.project_dir),
                 "DJANGO_SETTINGS_MODULE": "config.settings",
                 "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
