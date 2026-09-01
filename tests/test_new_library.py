"""stapel-new-library scaffold tests: file plan, rendering hygiene, kinds."""
import compileall
import json
import shutil
import subprocess

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

    def test_generated_views_inherit_the_core_seam_instead_of_copying_it(self, tmp_path):
        """stapel-core 0.37.0 extracted `SerializerSeamMixin` / `StapelAPIView`
        precisely because twenty-three modules plus this template had each
        hand-written the same four-line mixin. A scaffold that still embeds a
        copy is not a leftover — it is the machine that mints the next one, so
        the absence of a local definition is the thing worth asserting."""
        target = scaffold_library("search", "Search", tmp_path, git=False)
        views = (target / "views.py").read_text()

        assert "from stapel_core.django.api.views import StapelAPIView" in views
        assert "class PingView(StapelAPIView):" in views
        assert "class SerializerSeamMixin" not in views
        # the seam is inherited, so the view still names its serializer the
        # same way a host project overrides it
        assert "response_serializer_class = PingResponseSerializer" in views

        # a library that inherits the core primitive must pin the release that
        # ships it, or the scaffold generates an ImportError against core 0.36
        py = (target / "pyproject.toml").read_text()
        assert '"stapel-core>=0.37.0,<1.0"' in py

        module_md = (target / "MODULE.md").read_text()
        assert "StapelAPIView" in module_md

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


def test_scaffolded_pyproject_ships_the_contract_documents_in_the_wheel():
    """A module whose contract documents stay repo-only publishes code no
    installed-environment reader can see (`stapel-catalog --from-installed`).
    The scaffold must not be able to produce such a module."""
    import tomllib

    for kind in ("module", "library"):
        ctx = build_context("demo", "Demo")
        plan = file_plan(kind, ctx)
        data = tomllib.loads(plan["pyproject.toml"])
        package_data = data["tool"]["setuptools"]["package-data"]["stapel_demo"]
        for entry in ("docs/capabilities.json", "docs/flows.json",
                      "docs/errors.json", "CONFIG.MD"):
            assert entry in package_data, f"{kind}: {entry} missing"


# ---------------------------------------------------------------------------
# the pre-push hook scans the pushed commits, never the working tree
# ---------------------------------------------------------------------------


class TestPrePushScansWhatIsPushed:
    """A shared worktree turned the old `ruff check .` / `stapel-exposure-lint
    . --commits` hook into a cross-session blocker: a peer's uncommitted files
    failed someone else's push. The hook must read stdin and judge the
    committed trees named there."""

    def test_template_no_longer_scans_the_working_tree(self):
        from stapel_tools._library_templates import PRE_PUSH

        assert "ruff check ." not in PRE_PUSH
        assert "stapel-exposure-lint . --commits" not in PRE_PUSH
        # reads the standard pre-push stdin lines
        assert "read -r local_ref local_sha remote_ref remote_sha" in PRE_PUSH
        assert "git archive" in PRE_PUSH
        assert "--pushed" in PRE_PUSH

    def _git(self, root, *args):
        return subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=root, capture_output=True, text=True,
        )

    def _repo_with_hook(self, tmp_path):
        from stapel_tools._library_templates import PRE_PUSH

        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        root = tmp_path / "work"
        root.mkdir()
        assert self._git(root, "init", "-q", "-b", "main").returncode == 0
        hooks = root / ".githooks"
        hooks.mkdir()
        hook = hooks / "pre-push"
        hook.write_text(PRE_PUSH, encoding="utf-8")
        hook.chmod(0o755)
        self._git(root, "config", "core.hooksPath", ".githooks")
        self._git(root, "remote", "add", "origin", str(remote))
        return root

    def test_dirty_worktree_does_not_fail_a_clean_push(self, tmp_path, monkeypatch):
        if not (shutil.which("ruff") and shutil.which("git")):
            import pytest
            pytest.skip("ruff/git unavailable")
        monkeypatch.setenv(
            "STAPEL_PRIVATE_NAMES_FILE", str(tmp_path / "no-such-list")
        )
        root = self._repo_with_hook(tmp_path)
        (root / "clean.py").write_text("VALUE = 1\n", encoding="utf-8")
        self._git(root, "add", "clean.py")
        self._git(root, "commit", "-q", "-m", "feat: a clean file")
        # a peer's uncommitted, untracked file with a real ruff error
        (root / "peer_wip.py").write_text(
            "import os\nimport sys\n", encoding="utf-8"
        )

        pushed = self._git(root, "push", "-q", "origin", "main")
        assert pushed.returncode == 0, pushed.stdout + pushed.stderr

    def test_a_committed_ruff_error_still_fails_the_push(self, tmp_path, monkeypatch):
        if not (shutil.which("ruff") and shutil.which("git")):
            import pytest
            pytest.skip("ruff/git unavailable")
        monkeypatch.setenv(
            "STAPEL_PRIVATE_NAMES_FILE", str(tmp_path / "no-such-list")
        )
        root = self._repo_with_hook(tmp_path)
        (root / "broken.py").write_text("def f(:\n", encoding="utf-8")
        self._git(root, "add", "broken.py")
        self._git(root, "commit", "-q", "-m", "feat: a syntax error")

        pushed = self._git(root, "push", "-q", "origin", "main")
        assert pushed.returncode != 0

    def test_exposure_lint_judges_the_pushed_tree_only(self, tmp_path, monkeypatch):
        """EXP001 through the hook: committed hit blocks, uncommitted does not."""
        import pytest
        if not (shutil.which("ruff") and shutil.which("git")
                and shutil.which("stapel-exposure-lint")):
            pytest.skip("ruff/git/stapel-exposure-lint unavailable")
        names = tmp_path / "private-names"
        names.write_text("acme\n", encoding="utf-8")
        monkeypatch.setenv("STAPEL_PRIVATE_NAMES_FILE", str(names))

        root = self._repo_with_hook(tmp_path)
        (root / "pyproject.toml").write_text(
            '[project]\nname = "stapel-thing"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        (root / "clean.py").write_text("VALUE = 1\n", encoding="utf-8")
        self._git(root, "add", "-A")
        self._git(root, "commit", "-q", "-m", "feat: a clean file")
        # a peer's untracked note naming a client must not block this push
        (root / "peer_notes.md").write_text("acme.example\n", encoding="utf-8")
        ok = self._git(root, "push", "-q", "origin", "main")
        assert ok.returncode == 0, ok.stdout + ok.stderr

        # the same name, committed, does block it
        (root / "clean.py").write_text(
            "VALUE = 1  # seen on acme.example\n", encoding="utf-8"
        )
        self._git(root, "add", "-A")
        self._git(root, "commit", "-q", "-m", "chore: a note")
        blocked = self._git(root, "push", "-q", "origin", "main")
        assert blocked.returncode != 0
        assert "EXP001" in blocked.stdout + blocked.stderr
