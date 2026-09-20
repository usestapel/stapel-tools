"""The pre-push gate that refuses a branch not containing the default branch.

Branches cut from a base hundreds of commits old keep arriving; merging one
reverts everything that landed in between and the merge itself looks clean.
The push is the last moment anyone can be asked to resolve that, so the hook
refuses there.

Every test here drives a REAL push into a real bare "origin" — the hook is
run by git, on git's stdin format, with git's arguments, because that is the
only thing that proves the stage fires at all.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from stapel_tools import hooks
from stapel_tools._library_templates import PRE_PUSH

ZERO = "0" * 40

#: The hook's LATER stages are not under test here, and one of them —
#: stapel-exposure-lint — fails closed (EXP000) on a runner where the
#: private-names list is unset, which is every CI runner. Point it at a list
#: holding one name no scratch repo contains, so the stage runs and passes and
#: the only thing that can refuse these pushes is the stage being tested.
PRIVATE_NAMES = "zzz-no-such-private-name\n"


@pytest.fixture(autouse=True)
def _private_names(tmp_path_factory, monkeypatch):
    names = tmp_path_factory.mktemp("names") / "private-names"
    names.write_text(PRIVATE_NAMES, encoding="utf-8")
    monkeypatch.setenv("STAPEL_PRIVATE_NAMES_FILE", str(names))


def run(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"{' '.join(args)} failed ({result.returncode})\n{result.stdout}\n{result.stderr}"
        )
    return result


def output(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


def write_hook(work: Path, text: str) -> None:
    hook = work / ".githooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    with open(hook, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    hook.chmod(0o755)
    run(work, "git", "config", "core.hooksPath", ".githooks")


def commit(work: Path, name: str) -> str:
    (work / name).write_text(f"{name}\n", encoding="utf-8")
    run(work, "git", "add", "--", name)
    run(work, "git", "commit", "-q", "-m", f"add {name}")
    return run(work, "git", "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, Path]:
    """A work tree with the hook installed and a bare origin holding main."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    run(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    run(tmp_path, "git", "init", "-q", "-b", "main", str(work))
    run(work, "git", "config", "user.email", "hook@test.invalid")
    run(work, "git", "config", "user.name", "Hook Test")
    run(work, "git", "config", "commit.gpgsign", "false")
    commit(work, "base.txt")
    run(work, "git", "remote", "add", "origin", str(origin))
    run(work, "git", "push", "-q", "-u", "origin", "main")
    run(work, "git", "remote", "set-head", "origin", "main")
    write_hook(work, PRE_PUSH)
    return work, origin


def advance_main(work: Path, count: int = 5) -> None:
    """Land `count` commits on main (pushes to the default branch are skipped)."""
    for index in range(count):
        commit(work, f"main-{index}.txt")
    run(work, "git", "push", "-q", "origin", "main")


# ---------------------------------------------------------------- the gate


def test_branch_containing_the_default_branch_pushes(repo):
    work, _ = repo
    advance_main(work, 3)
    run(work, "git", "checkout", "-q", "-b", "feature")
    commit(work, "feature.txt")
    result = run(work, "git", "push", "-q", "origin", "feature", check=False)
    assert result.returncode == 0, output(result)
    assert "REFUSING" not in output(result)


def test_stale_branch_is_refused_with_the_count_and_the_commands(repo):
    work, _ = repo
    base = run(work, "git", "rev-parse", "HEAD").stdout.strip()
    advance_main(work, 5)
    run(work, "git", "checkout", "-q", "-b", "stale", base)
    commit(work, "stale.txt")

    result = run(work, "git", "push", "origin", "stale", check=False)
    text = output(result)
    assert result.returncode != 0, text
    assert "REFUSING to push refs/heads/stale" in text
    assert "does not contain" in text
    assert "origin/main" in text
    assert "5 commit(s) behind" in text
    assert "git fetch origin" in text
    assert "git merge origin/main" in text
    assert "git push" in text
    assert "resolve the conflicts" in text
    # The fix is a merge. Say so, because a rebase of a published branch is
    # the reflex and it is the wrong one.
    assert "Rebasing a published branch is NOT the fix" in text
    # and the branch really did not land
    heads = run(work, "git", "ls-remote", "--heads", "origin").stdout
    assert "refs/heads/stale" not in heads


def test_merging_the_default_branch_unblocks_the_push(repo):
    work, _ = repo
    base = run(work, "git", "rev-parse", "HEAD").stdout.strip()
    advance_main(work, 5)
    run(work, "git", "checkout", "-q", "-b", "stale", base)
    commit(work, "stale.txt")
    assert run(work, "git", "push", "origin", "stale", check=False).returncode != 0

    run(work, "git", "fetch", "-q", "origin")
    run(work, "git", "merge", "-q", "--no-edit", "origin/main")
    result = run(work, "git", "push", "origin", "stale", check=False)
    assert result.returncode == 0, output(result)
    assert "Branch contains origin/main." in output(result)


def test_the_stage_is_what_refuses_the_stale_branch(repo):
    """Red on the old template: without the stage, the stale push sails."""
    work, _ = repo
    old = PRE_PUSH.replace(stage_one_text(), "")
    assert "branch-contains-default" not in old
    write_hook(work, old)

    base = run(work, "git", "rev-parse", "HEAD").stdout.strip()
    advance_main(work, 5)
    run(work, "git", "checkout", "-q", "-b", "stale", base)
    commit(work, "stale.txt")
    result = run(work, "git", "push", "origin", "stale", check=False)
    assert result.returncode == 0, output(result)


# ------------------------------------------------------------- the skips


def test_tag_push_is_not_judged_by_the_stage(repo):
    work, _ = repo
    base = run(work, "git", "rev-parse", "HEAD").stdout.strip()
    advance_main(work, 5)
    run(work, "git", "checkout", "-q", "-b", "stale", base)
    commit(work, "stale.txt")
    run(work, "git", "tag", "v0.0.1")

    result = run(work, "git", "push", "origin", "v0.0.1", check=False)
    text = output(result)
    assert result.returncode == 0, text
    assert "does not contain" not in text


def test_branch_deletion_is_not_judged_by_the_stage(repo):
    work, _ = repo
    advance_main(work, 2)
    run(work, "git", "checkout", "-q", "-b", "doomed")
    commit(work, "doomed.txt")
    run(work, "git", "push", "-q", "origin", "doomed")

    result = run(work, "git", "push", "origin", ":refs/heads/doomed", check=False)
    text = output(result)
    assert result.returncode == 0, text
    assert "REFUSING" not in text


def test_push_to_the_default_branch_is_skipped(repo):
    work, _ = repo
    commit(work, "on-main.txt")
    result = run(work, "git", "push", "origin", "main", check=False)
    text = output(result)
    assert result.returncode == 0, text
    # git already enforces fast-forward here; the stage must not even fetch.
    assert "Branch contains" not in text


def test_origin_head_unset_falls_back_to_main(repo):
    work, _ = repo
    base = run(work, "git", "rev-parse", "HEAD").stdout.strip()
    advance_main(work, 4)
    run(work, "git", "remote", "set-head", "origin", "-d")
    assert not (work / ".git" / "refs" / "remotes" / "origin" / "HEAD").exists()
    run(work, "git", "checkout", "-q", "-b", "stale", base)
    commit(work, "stale.txt")

    result = run(work, "git", "push", "origin", "stale", check=False)
    text = output(result)
    assert result.returncode != 0, text
    assert "origin/main" in text
    assert "4 commit(s) behind" in text


def test_unreachable_remote_refuses_the_push(repo, tmp_path):
    """Fail closed: unverified freshness is not a pass."""
    work, _ = repo
    advance_main(work, 2)
    run(work, "git", "checkout", "-q", "-b", "feature")
    head = commit(work, "feature.txt")
    # git contacts the remote before it runs pre-push, so drive the hook the
    # way git drives it — same argv, same stdin — against a dead URL.
    run(work, "git", "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    result = subprocess.run(
        [".githooks/pre-push", "origin", str(tmp_path / "gone.git")],
        cwd=str(work),
        input=f"refs/heads/feature {head} refs/heads/feature {ZERO}\n",
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    text = output(result)
    assert result.returncode != 0, text
    assert "could not be verified" in text


# ------------------------------------------------------- portability of it


def stage_one_text() -> str:
    """The stage as it appears in the template, for the portability checks."""
    start = PRE_PUSH.index("    # ── Stage 1 — branch-contains-default")
    end = PRE_PUSH.index("    echo \"Running ruff lint check")
    return PRE_PUSH[start:end]


def test_the_stage_is_posix_sh_with_no_bashisms():
    stage = stage_one_text()
    assert stage, "stage markers moved"
    forbidden = {
        "substring expansion": re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:\d+:"),
        "[[ ]] test": re.compile(r"\[\["),
        "(( )) arithmetic": re.compile(r"\(\("),
        "local keyword": re.compile(r"^\s*local\s", re.M),
        "function keyword": re.compile(r"^\s*function\s", re.M),
        "bash arrays": re.compile(r"=\("),
        "== inside [": re.compile(r"\[\s[^]]*\s==\s"),
        "&> redirect": re.compile(r"&>"),
        "echo -e": re.compile(r"echo\s+-[en]"),
        "GNU-only timeout(1)": re.compile(r"\btimeout\s+\d"),
        "process substitution": re.compile(r"<\("),
        "pipefail inside the stage": re.compile(r"pipefail"),
    }
    for name, pattern in forbidden.items():
        assert not pattern.search(stage), f"{name} in a stage that must run under Git for Windows' sh"
    assert "\r" not in PRE_PUSH, "the template must be LF-only"


@pytest.mark.skipif(shutil.which("dash") is None, reason="dash not installed")
def test_the_stage_parses_under_dash():
    stage = stage_one_text()
    script = "set -e\nzero=0\nremote_name=origin\nlocal_ref=x\nlocal_sha=y\nremote_ref=z\n" + stage
    result = subprocess.run(["dash", "-n"], input=script, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_there_is_no_escape_hatch():
    stage = stage_one_text()
    assert "SKIP" not in stage.upper().replace("SKIPPED", "")
    assert not re.search(r"\$\{?[A-Z_]*(FORCE|SKIP|NO_)[A-Z_]*", stage)


# ------------------------------------------------- installer and doctor


def test_template_carries_a_version_marker():
    assert hooks.template_version("pre-push") is not None
    assert re.search(r"^# stapel-hooks: pre-push v\d+$", PRE_PUSH, re.M)


def test_install_sets_hooks_path_and_writes_the_hook(tmp_path):
    work = tmp_path / "fresh"
    run(tmp_path, "git", "init", "-q", "-b", "main", str(work))
    assert hooks.main(["install", "--repo", str(work)]) == 0
    hook = work / ".githooks" / "pre-push"
    assert hook.exists() and hook.stat().st_mode & 0o111
    assert "\r" not in hook.read_text(encoding="utf-8")
    assert run(work, "git", "config", "--get", "core.hooksPath").stdout.strip() == ".githooks"


def test_install_keeps_a_hand_maintained_hook(tmp_path):
    work = tmp_path / "fleet"
    run(tmp_path, "git", "init", "-q", "-b", "main", str(work))
    hook = work / ".githooks" / "pre-push"
    hook.parent.mkdir()
    hook.write_text("#!/bin/sh\n# stapel-hooks: pre-push v1\necho mine\n", encoding="utf-8")
    hook.chmod(0o755)
    assert hooks.main(["install", "--repo", str(work)]) == 0
    assert "echo mine" in hook.read_text(encoding="utf-8")


def test_doctor_fails_when_hooks_are_not_active(tmp_path, capsys):
    work = tmp_path / "unhooked"
    run(tmp_path, "git", "init", "-q", "-b", "main", str(work))
    (work / ".githooks").mkdir()
    hook = work / ".githooks" / "pre-push"
    hook.write_text(PRE_PUSH, encoding="utf-8")
    hook.chmod(0o755)
    assert hooks.main(["doctor", "--repo", str(work)]) == 1
    assert "NOT active" in capsys.readouterr().err


def test_doctor_fails_on_an_outdated_hook(tmp_path, capsys):
    work = tmp_path / "outdated"
    run(tmp_path, "git", "init", "-q", "-b", "main", str(work))
    hook = work / ".githooks" / "pre-push"
    hook.parent.mkdir()
    hook.write_text("#!/bin/sh\n# stapel-hooks: pre-push v0\n", encoding="utf-8")
    hook.chmod(0o755)
    run(work, "git", "config", "core.hooksPath", ".githooks")
    assert hooks.main(["doctor", "--repo", str(work)]) == 1
    err = capsys.readouterr().err
    assert "is v0" in err


def test_doctor_fails_on_an_unmarked_hook(tmp_path, capsys):
    work = tmp_path / "unmarked"
    run(tmp_path, "git", "init", "-q", "-b", "main", str(work))
    hook = work / ".githooks" / "pre-push"
    hook.parent.mkdir()
    hook.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
    hook.chmod(0o755)
    run(work, "git", "config", "core.hooksPath", ".githooks")
    assert hooks.main(["doctor", "--repo", str(work)]) == 1
    assert "no version marker" in capsys.readouterr().err


def test_doctor_passes_on_a_correct_install(tmp_path, capsys):
    work = tmp_path / "good"
    run(tmp_path, "git", "init", "-q", "-b", "main", str(work))
    assert hooks.main(["install", "--repo", str(work)]) == 0
    capsys.readouterr()
    assert hooks.main(["doctor", "--repo", str(work)]) == 0
    assert "OK" in capsys.readouterr().out
