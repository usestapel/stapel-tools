"""stapel-new-library scaffold tests: file plan, rendering hygiene, kinds."""
import compileall
import json

from stapel_tools.new_library import build_context, file_plan, scaffold_library

REQUIRED_EVERYWHERE = [
    "pyproject.toml",
    "__init__.py",
    "conf.py",
    "conftest.py",
    "py.typed",
    "MODULE.md",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "codecov.yml",
    ".github/workflows/ci.yml",
    ".github/workflows/publish.yml",
    ".githooks/pre-commit",
    ".githooks/pre-push",
    "setup-hooks.sh",
    ".gitignore",
    "tests/test_public_api.py",
]

MODULE_ONLY = [
    "apps.py",
    "models.py",
    "migrations/__init__.py",
    "dto.py",
    "serializers.py",
    "views.py",
    "urls.py",
    "errors.py",
    "checks.py",
    "functions.py",
    "schemas/functions/search.ping.json",
    "tests/urls.py",
    "tests/test_ping.py",
]


class TestFilePlan:
    def test_module_kind_has_full_inventory(self):
        plan = file_plan("module", build_context("search", "Search"))
        for rel in REQUIRED_EVERYWHERE + MODULE_ONLY:
            assert rel in plan, rel

    def test_library_kind_drops_service_files(self):
        plan = file_plan("library", build_context("attributes", "Attributes"))
        for rel in REQUIRED_EVERYWHERE:
            assert rel in plan, rel
        for rel in MODULE_ONLY:
            assert rel not in plan, rel

    def test_no_unrendered_placeholders(self):
        for kind, slug in (("module", "search"), ("library", "attributes")):
            for rel, content in file_plan(kind, build_context(slug, "T")).items():
                for token in (
                    "{{SLUG}}", "{{SLUG_U}}", "{{PKG}}", "{{NAME_DASH}}",
                    "{{NAMESPACE}}", "{{TITLE}}", "{{CAMEL}}", "{{YEAR}}",
                    "{{PACKAGES}}", "{{PACKAGE_DATA_EXTRA}}",
                ):
                    assert token not in content, f"{kind}:{rel} leaks {token}"

    def test_github_actions_expressions_survive_rendering(self):
        ci = file_plan("module", build_context("search", "Search"))[
            ".github/workflows/ci.yml"
        ]
        assert "${{ matrix.python-version }}" in ci
        assert "${{ secrets.CODECOV_TOKEN }}" in ci

    def test_dashed_slug_naming(self):
        ctx = build_context("support-chat", "Support chat")
        assert ctx["PKG"] == "stapel_support_chat"
        assert ctx["NAME_DASH"] == "stapel-support-chat"
        assert ctx["NAMESPACE"] == "STAPEL_SUPPORT_CHAT"
        assert ctx["CAMEL"] == "SupportChat"


class TestScaffold:
    def test_scaffold_module_compiles_and_is_wired(self, tmp_path):
        target = scaffold_library("search", "Search", tmp_path, git=False)

        assert (target / "views.py").exists()
        # every generated .py is syntactically valid
        assert compileall.compile_dir(str(target), quiet=2, force=True)
        # schema is valid JSON
        schema = json.loads(
            (target / "schemas/functions/search.ping.json").read_text()
        )
        assert schema["title"] == "search.ping"
        # packaging maps the flat layout
        py = (target / "pyproject.toml").read_text()
        assert 'package-dir = {"stapel_search" = "."}' in py
        assert '"stapel_search.migrations"' in py
        # hooks are executable
        assert (target / ".githooks/pre-push").stat().st_mode & 0o100

    def test_scaffold_library_kind(self, tmp_path):
        target = scaffold_library("attributes", "Attributes", tmp_path,
                                  kind="library", git=False)
        assert not (target / "views.py").exists()
        assert not (target / "schemas").exists()
        py = (target / "pyproject.toml").read_text()
        assert '"stapel_attributes.migrations"' not in py
        assert compileall.compile_dir(str(target), quiet=2, force=True)

    def test_refuses_existing_target(self, tmp_path):
        scaffold_library("search", "Search", tmp_path, git=False)
        import pytest

        with pytest.raises(SystemExit):
            scaffold_library("search", "Search", tmp_path, git=False)


class TestGeneratedRepoEndToEnd:
    """The generated repo must be green out of the box: its own pytest
    suite passes and ruff (hook flags) is clean. Skipped where the test
    environment lacks the runtime deps (tools CI installs none)."""

    def _run_suite(self, target):
        import os
        import subprocess
        import sys

        env_probe = subprocess.run(
            [sys.executable, "-c", "import django, rest_framework, stapel_core"],
            capture_output=True,
        )
        if env_probe.returncode != 0:
            import pytest

            pytest.skip("django/DRF/stapel-core not installed in this env")

        # Flat layout: the repo dir is the package content but carries the
        # dashed repo name. Instead of pip-installing into the shared env,
        # expose the package via a symlink on PYTHONPATH.
        pkg_name = target.name.replace("-", "_")
        pypath = target.parent / "pypath"
        pypath.mkdir(exist_ok=True)
        (pypath / pkg_name).symlink_to(target)

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider"],
            cwd=target,
            capture_output=True,
            text=True,
            env={
                "PYTHONPATH": str(pypath),
                "PATH": os.environ["PATH"],
                "HOME": os.environ.get("HOME", "/tmp"),
            },
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_module_kind_suite_is_green(self, tmp_path):
        self._run_suite(scaffold_library("demo", "Demo", tmp_path, git=False))

    def test_library_kind_suite_is_green(self, tmp_path):
        self._run_suite(
            scaffold_library("attrs-demo", "Attrs", tmp_path,
                             kind="library", git=False)
        )
