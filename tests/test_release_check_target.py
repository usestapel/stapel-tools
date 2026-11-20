"""``make release-check``: one target runs every pre-tag gate and exits on its
real status. 0.85.1's tag was cut after the pin gate failed: its output was
piped into ``tail``, whose exit status the chain read instead."""
import importlib.util
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="needs make")


def _stub_python(tmp_path: Path, fail_on: str | None) -> Path:
    """A PYTHON that records each gate it is asked to run and fails one."""
    log = tmp_path / "ran.log"
    stub = tmp_path / "python"
    fail = f'case "$*" in *{fail_on}*) exit 1;; esac' if fail_on else ""
    stub.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\n{fail}\nexit 0\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub


def _run(tmp_path: Path, fail_on: str | None) -> subprocess.CompletedProcess:
    stub = _stub_python(tmp_path, fail_on)
    return subprocess.run(
        ["make", "-C", str(ROOT), "release-check", f"PYTHON={stub}", "RELEASE_CHECK_SH=true"],
        capture_output=True, text=True, env={**os.environ, "MAKEFLAGS": ""},
    )


def test_every_gate_green_is_exit_0_and_runs_them_all(tmp_path):
    result = _run(tmp_path, None)
    assert result.returncode == 0, result.stdout + result.stderr
    ran = (tmp_path / "ran.log").read_text()
    for gate in ("check_release_version.py", "check_pypi_pins.py --strict",
                 "stapel_tools.hooks doctor", "check_nav_manifest_sync.py",
                 "stapel_tools.composition.kmp.vendor --check", "golden_scaffold.py", "pytest"):
        assert gate in ran, gate
    assert "every pre-tag gate is green" in result.stdout


@pytest.mark.parametrize("gate", ["check_pypi_pins.py", "check_release_version.py",
                                  "check_nav_manifest_sync.py", "pytest"])
def test_one_red_gate_is_a_non_zero_exit(tmp_path, gate):
    result = _run(tmp_path, gate)
    assert result.returncode != 0
    assert "every pre-tag gate is green" not in result.stdout


def test_the_recipe_pipes_nothing():
    text = (ROOT / "Makefile").read_text()
    recipe = text.split("\nrelease-check:\n", 1)[1].split("\n\n", 1)[0]
    assert "|" not in recipe


def _version_script():
    spec = importlib.util.spec_from_file_location(
        "check_release_version", ROOT / "scripts" / "check_release_version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


@pytest.fixture
def project(tmp_path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1.2.0"\n')
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.0] — today\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "pyproject.toml", "CHANGELOG.md")
    _git(repo, "commit", "-q", "-m", "1.2.0")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "fetch", "-q", "origin")
    return repo


def test_a_ready_commit_passes(project):
    assert _version_script().problems(project) == []


def test_a_used_version_unpushed_head_and_missing_changelog_are_refused(project):
    _git(project, "tag", "-a", "v1.2.0", "-m", "burned")
    _git(project, "push", "-q", "origin", "v1.2.0")
    (project / "CHANGELOG.md").write_text("# Changelog\n")
    _git(project, "commit", "-q", "-am", "local only")
    found = "\n".join(_version_script().problems(project))
    assert "v1.2.0 already exists locally" in found
    assert "v1.2.0 already exists on origin" in found
    assert "is not origin/main" in found
    assert "no '## [1.2.0]' section" in found
