"""The `.githooks/pre-commit` Docker-build gate (PRE_COMMIT in
_library_templates.py) must never touch docker on a commit that stages no
Dockerfile, and must never hang when docker IS staged but the daemon is
unresponsive.

Incident: Docker Desktop installed but its daemon unresponsive — the OLD
template probed `docker info` before checking for a staged Dockerfile at
all, `docker info` hung indefinitely (no built-in timeout), and every
commit in every consumer repo hung for minutes until someone killed the
probe by hand. Two agents hit this on the same laptop in one session.

Fix, in order:
  1. Compute the staged-Dockerfile list FIRST. Empty -> never touch docker.
  2. Only when a Dockerfile is staged, probe docker under a hard 15s
     watchdog (plain bash job control, since this hook's shebang is bash
     and `timeout(1)` is not on stock macOS). Timeout/failure -> skip the
     build gate with one line, exit 0 (CI builds it for real).
  3. Docker available -> the build runs exactly as before.

Each test shadows `docker` on PATH with a fake script so the real docker
daemon (or its absence) never enters into it, and bounds the subprocess
with a generous outer `timeout=` so a regression that reintroduces the
hang fails the test suite instead of hanging it.
"""
import os
import subprocess
import time

import pytest

from stapel_tools._library_templates import PRE_COMMIT

# A `docker` that never returns within the test's lifetime — stands in for
# an unresponsive Docker Desktop daemon.
SLEEPER_DOCKER = "#!/bin/sh\nsleep 60\nexit 0\n"

# A `docker` that returns instantly and logs every invocation to stdout, so
# a test can prove the build path really ran (and with what arguments)
# rather than merely that the hook exited 0.
FAST_DOCKER = '#!/bin/sh\necho "DOCKER_CALL $@"\nexit 0\n'


def _write_hook(tmp_path):
    hook = tmp_path / "pre-commit"
    hook.write_text(PRE_COMMIT)
    hook.chmod(0o755)
    return hook


def _fake_bin_dir(tmp_path, docker_script, name="bin"):
    bindir = tmp_path / name
    bindir.mkdir(exist_ok=True)
    docker = bindir / "docker"
    docker.write_text(docker_script)
    docker.chmod(0o755)
    return bindir


def _repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.dev"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    return repo


def _stage_dockerfile(repo):
    svc = repo / "svc"
    svc.mkdir()
    (svc / "Dockerfile").write_text("FROM scratch\n")
    subprocess.run(["git", "add", "svc/Dockerfile"], cwd=repo, check=True)


def _run_hook(hook, repo, bindir, timeout):
    """Run the hook with `bindir` shadowing PATH. `timeout` is an outer
    safety bound — a regression that reintroduces the unbounded hang must
    fail this test, not freeze the test run."""
    env = dict(os.environ)
    env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [str(hook)], cwd=str(repo), capture_output=True, text=True,
            env=env, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - t0
        pytest.fail(
            "hook did not return within the %ss outer bound (hung); "
            "elapsed=%.1fs stdout=%r stderr=%r"
            % (timeout, elapsed, exc.stdout, exc.stderr)
        )
    elapsed = time.monotonic() - t0
    return proc, elapsed


def test_no_staged_dockerfile_never_touches_docker(tmp_path):
    """(a) No Dockerfile staged, `docker` shadowed by a 60s sleeper ->
    the hook must return almost instantly, proving docker was never
    invoked at all."""
    hook = _write_hook(tmp_path)
    repo = _repo(tmp_path)
    (repo / "file.txt").write_text("x\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    bindir = _fake_bin_dir(tmp_path, SLEEPER_DOCKER)

    proc, elapsed = _run_hook(hook, repo, bindir, timeout=30)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert elapsed < 2, (
        "hook took %.2fs with no staged Dockerfile — it must never touch "
        "docker in this case" % elapsed
    )


def test_staged_dockerfile_with_hung_docker_skips_after_15s(tmp_path):
    """(b) A Dockerfile IS staged and `docker` is shadowed by the same 60s
    sleeper -> the hook must bail out around the 15s watchdog, print the
    explicit skip line, and exit 0 (never fail the commit over a probe
    timeout)."""
    hook = _write_hook(tmp_path)
    repo = _repo(tmp_path)
    _stage_dockerfile(repo)
    bindir = _fake_bin_dir(tmp_path, SLEEPER_DOCKER)

    proc, elapsed = _run_hook(hook, repo, bindir, timeout=30)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "docker unavailable (timeout/failure)" in proc.stdout, proc.stdout
    assert 13 <= elapsed <= 21, (
        "expected the ~15s watchdog to bound this, got %.2fs" % elapsed
    )


def test_staged_dockerfile_with_working_docker_runs_the_build(tmp_path):
    """(c) A Dockerfile is staged and `docker` responds immediately ->
    the build path must actually run (not just exit 0), proving the fix
    did not also gut the working path."""
    hook = _write_hook(tmp_path)
    repo = _repo(tmp_path)
    _stage_dockerfile(repo)
    bindir = _fake_bin_dir(tmp_path, FAST_DOCKER)

    proc, elapsed = _run_hook(hook, repo, bindir, timeout=30)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DOCKER_CALL build" in proc.stdout, proc.stdout
    assert elapsed < 5, "fast docker should not need anywhere near the watchdog bound"
