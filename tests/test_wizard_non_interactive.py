"""With no terminal, a call missing identity flags fails non-zero and names them.

The wizard read EOF from a closed stdin, printed "Aborted." and exited 0 with
no project, so a script or an agent saw success.
"""
import subprocess
import sys

FULL = ["--type", "monolith", "--title", "T", "--url", "https://t.example.com",
        "--company-name", "T", "--company-email", "t@t.example.com",
        "--modules", "core", "--no-submodules", "--no-git"]


def _run(tmp_path, *args):
    return subprocess.run(
        [sys.executable, "-m", "stapel_tools.create_project", *args, "--output-dir", str(tmp_path)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, cwd=tmp_path,
    )


def test_missing_identity_flags_fail_and_are_named(tmp_path):
    result = _run(tmp_path, "app", "--type", "monolith", "--modules", "shop")
    assert result.returncode == 2
    for flag in ("--title", "--url", "--company-name", "--company-email"):
        assert flag in result.stderr
    assert "--modules" not in result.stderr.split("error:", 1)[1]
    assert not (tmp_path / "app").exists()


def test_missing_name_and_modules_are_named(tmp_path):
    result = _run(tmp_path, "--title", "T")
    assert result.returncode == 2
    tail = result.stderr.split("error:", 1)[1]
    assert "<name>" in tail and "--modules" in tail and "--type" in tail
    assert "--title" not in tail


def test_complete_call_still_creates_the_project(tmp_path):
    result = _run(tmp_path, "app", *FULL)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "app" / "Makefile").is_file()
