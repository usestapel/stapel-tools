"""The freshness check: does the suite measure the repo it is run in?

The defect: `stapel_profiles` was installed non-editable at 0.20.2 in a shared
dev venv while the repo sat at 0.20.4, so every test run in that repo imported
a month-old library and reported green about it. Nothing said so, and it
surfaced only because a NEW name failed to import — a change to an existing
function would have silently exercised the old body.
"""
from pathlib import Path

import pytest

from stapel_tools import freshness


def _pyproject(tmp_path: Path, name: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        f'[build-system]\nrequires = ["setuptools"]\n\n'
        f'[project]\nname = "{name}"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    return tmp_path


class TestProjectPackage:
    def test_it_reads_the_name_and_normalises_dashes(self, tmp_path):
        root = _pyproject(tmp_path, "stapel-profiles")
        assert freshness.project_package(root) == "stapel_profiles"

    def test_a_non_stapel_project_is_not_asserted(self, tmp_path):
        """A service repo or a scratch dir has no 'own package' to check."""
        root = _pyproject(tmp_path, "some-app")
        assert freshness.project_package(root) is None

    def test_no_pyproject_is_not_an_error(self, tmp_path):
        assert freshness.project_package(tmp_path) is None

    def test_a_name_outside_the_project_table_is_ignored(self, tmp_path):
        """`[tool.poetry] name = ...` must not be mistaken for the project."""
        (tmp_path / "pyproject.toml").write_text(
            '[tool.poetry]\nname = "stapel-decoy"\n\n[project]\nname = "stapel-real"\n',
            encoding="utf-8",
        )
        assert freshness.project_package(tmp_path) == "stapel_real"


class TestResolvePackage:
    def test_it_locates_without_importing(self):
        """find_spec, not import_module: importing a Django app this early
        can raise ImproperlyConfigured and turn the check into a boot error."""
        resolved = freshness.resolve_package("stapel_tools")
        assert resolved is not None
        assert resolved.name in ("__init__.py", "stapel_tools")

    def test_an_absent_package_is_none_not_a_crash(self):
        assert freshness.resolve_package("stapel_definitely_not_installed") is None


class TestTheReport:
    def test_it_names_the_stale_path_and_the_fresh_one(self, tmp_path):
        stale = Path("/opt/venv/lib/python3.12/site-packages/stapel_profiles/__init__.py")
        message = freshness.stale_report(tmp_path, "stapel_profiles", stale)
        assert str(stale) in message
        assert str(tmp_path) in message
        # and tells the reader how to fix the ENVIRONMENT, not the test
        assert "pip install -e" in message
        assert freshness.ENV_SKIP in message


class TestTheGate:
    """`pytest_sessionstart` over a fabricated config."""

    class _Config:
        def __init__(self, rootpath, skip=False):
            self.rootpath = rootpath
            self._skip = skip

        def getoption(self, name, default=None):
            return self._skip if name == "--no-freshness-check" else default

    class _Session:
        def __init__(self, config):
            self.config = config

    def _run(self, root, skip=False):
        session = self._Session(self._Config(root, skip))
        freshness.pytest_sessionstart(session)

    def test_a_package_outside_the_repo_stops_the_session(self, tmp_path, monkeypatch):
        root = _pyproject(tmp_path, "stapel-profiles")
        monkeypatch.setattr(
            freshness, "resolve_package",
            lambda pkg: Path("/opt/venv/lib/python3.12/site-packages/stapel_profiles/__init__.py"),
        )
        # pytest.exit raises Exit (a BaseException), not SystemExit — caught
        # here so the fabricated failure aborts this assertion and not the
        # whole session running it.
        from _pytest.outcomes import Exit

        with pytest.raises(Exit) as exc:
            self._run(root)
        assert "STALE PACKAGE" in str(exc.value)
        assert "site-packages" in str(exc.value)

    def test_a_package_inside_the_repo_passes(self, tmp_path, monkeypatch):
        root = _pyproject(tmp_path, "stapel-profiles")
        monkeypatch.setattr(
            freshness, "resolve_package", lambda pkg: root / "__init__.py"
        )
        self._run(root)  # no raise

    def test_the_opt_out_is_honoured(self, tmp_path, monkeypatch):
        root = _pyproject(tmp_path, "stapel-profiles")
        monkeypatch.setattr(
            freshness, "resolve_package",
            lambda pkg: Path("/elsewhere/stapel_profiles/__init__.py"),
        )
        self._run(root, skip=True)  # flag
        monkeypatch.setenv(freshness.ENV_SKIP, "1")
        self._run(root)  # env

    def test_a_non_stapel_repo_is_not_asserted(self, tmp_path, monkeypatch):
        root = _pyproject(tmp_path, "some-app")
        monkeypatch.setattr(
            freshness, "resolve_package",
            lambda pkg: pytest.fail("must not resolve for a non-stapel project"),
        )
        self._run(root)

    def test_an_unimportable_package_is_left_to_the_suite(self, tmp_path, monkeypatch):
        """A missing package fails far more clearly on its own."""
        root = _pyproject(tmp_path, "stapel-profiles")
        monkeypatch.setattr(freshness, "resolve_package", lambda pkg: None)
        self._run(root)
