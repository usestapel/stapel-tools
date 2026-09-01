"""Three deploy gates born from three live incidents (see the module docstring
of stapel_tools/_deploy_templates.py):

  (a) a static deploy driven from a stale memory note instead of the host's
      own facts, with `rsync --delete` over the release root that held the
      `current` symlink -> the live release was deleted (15 min of 404s);
  (b) a `while read domain; do docker compose run certbot ...; done < list`
      whose inner command ate the loop's stdin, so only the FIRST domain got
      a certificate and one success was accepted for all;
  (c) one vhost's config living in two layers - a file in the repo and a
      hand-edited file on the host - which drifted.

The templates are executed with dash where the box has it (never bash), so the
no-bashisms rule is tested rather than asserted, and DEPLOY_HOST is unset so
the target is a local path.
"""
import os
import re
import shutil
import subprocess

import pytest

from stapel_tools import _deploy_templates as T

# dash where the box has it: /bin/sh on darwin is bash in POSIX mode, which is
# far more forgiving than the shells these scripts actually run on.
SH = shutil.which("dash") or "sh"

SCRIPTS = {
    "release-static.sh": "RELEASE_STATIC_SH",
    "each.sh": "EACH_SH",
    "verify-host-config.sh": "VERIFY_HOST_CONFIG_SH",
}


def _deploy_dir(tmp_path):
    """Write the templates as a real deploy/ dir, executable, and return it."""
    d = tmp_path / "deploy"
    d.mkdir(parents=True, exist_ok=True)
    for name, attr in SCRIPTS.items():
        p = d / name
        p.write_text(getattr(T, attr))
        p.chmod(0o755)
    return d


def _env():
    env = dict(os.environ)
    env.pop("DEPLOY_HOST", None)  # local mode: target-root is a local path
    return env


def _sh(script, *args, cwd=None, env=None, extra_env=None):
    e = env or _env()
    if extra_env:
        e.update(extra_env)
    return subprocess.run(
        [SH, str(script), *[str(a) for a in args]],
        cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, env=e, timeout=120,
    )


def _built(tmp_path, name="build", body="<html>v1</html>"):
    src = tmp_path / name
    src.mkdir(parents=True, exist_ok=True)
    (src / "index.html").write_text(body)
    (src / "app.js").write_text("console.log('%s')" % body)
    return src


# ── (a) release-static.sh ────────────────────────────────────────────────
class TestReleaseStatic:
    def test_first_deploy_creates_release_and_current_symlink(self, tmp_path):
        d = _deploy_dir(tmp_path)
        src = _built(tmp_path)
        root = tmp_path / "www"
        r = _sh(d / "release-static.sh", src, root, "r001")
        assert r.returncode == 0, r.stdout + r.stderr
        rel = root / "releases" / "r001"
        assert (rel / "index.html").read_text() == "<html>v1</html>"
        cur = root / "current"
        assert cur.is_symlink()
        assert os.readlink(cur) == "releases/r001"

    def test_prints_before_and_after_facts_read_from_the_target(self, tmp_path):
        d = _deploy_dir(tmp_path)
        src = _built(tmp_path)
        root = tmp_path / "www"
        first = _sh(d / "release-static.sh", src, root, "r001")
        out = first.stdout + first.stderr
        assert "current(before)=" in out, out
        assert "current(after)=releases/r001" in out, out

        src2 = _built(tmp_path, "build2", "<html>v2</html>")
        second = _sh(d / "release-static.sh", src2, root, "r002")
        out2 = second.stdout + second.stderr
        # the SECOND deploy's "before" is a fact read from the host, not a note
        assert "current(before)=releases/r001" in out2, out2
        assert "current(after)=releases/r002" in out2, out2

    def test_second_deploy_flips_current_and_keeps_the_old_release(self, tmp_path):
        d = _deploy_dir(tmp_path)
        root = tmp_path / "www"
        assert _sh(d / "release-static.sh", _built(tmp_path), root, "r001").returncode == 0
        r = _sh(d / "release-static.sh",
                _built(tmp_path, "build2", "<html>v2</html>"), root, "r002")
        assert r.returncode == 0, r.stdout + r.stderr
        assert os.readlink(root / "current") == "releases/r002"
        # the incident: the previous release must still be on disk
        assert (root / "releases" / "r001" / "index.html").read_text() == "<html>v1</html>"
        assert (root / "releases" / "r002" / "index.html").read_text() == "<html>v2</html>"

    def test_current_that_is_a_real_directory_is_refused_without_changes(self, tmp_path):
        d = _deploy_dir(tmp_path)
        root = tmp_path / "www"
        (root / "current").mkdir(parents=True)
        (root / "current" / "index.html").write_text("live")
        r = _sh(d / "release-static.sh", _built(tmp_path), root, "r001")
        assert r.returncode != 0, r.stdout
        assert not (root / "releases").exists(), "refused run must not touch the layout"
        assert (root / "current" / "index.html").read_text() == "live"

    def test_keep_releases_prunes_only_inside_releases(self, tmp_path):
        d = _deploy_dir(tmp_path)
        root = tmp_path / "www"
        for i in (1, 2, 3, 4):
            rid = "r%03d" % i
            r = _sh(d / "release-static.sh",
                    _built(tmp_path, "b%d" % i, "<html>v%d</html>" % i), root, rid,
                    extra_env={"KEEP_RELEASES": "2"})
            assert r.returncode == 0, r.stdout + r.stderr
        kept = sorted(p.name for p in (root / "releases").iterdir())
        assert kept == ["r003", "r004"], kept
        assert os.readlink(root / "current") == "releases/r004"

    def test_delete_over_root_guard_is_present_and_named(self, tmp_path):
        text = T.RELEASE_STATIC_SH
        assert "refuse_delete_over_root" in text
        # rsync --delete must never be aimed at the root that holds `current`
        assert "--delete" in text

    def test_a_destination_resolving_to_the_root_is_refused(self, tmp_path):
        # THE incident, reproduced: a release id that makes the rsync --delete
        # destination resolve to the release root - the root holds `current`
        # and the other releases, and --delete there is the 15 minutes of 404s.
        d = _deploy_dir(tmp_path)
        root = tmp_path / "www"
        assert _sh(d / "release-static.sh", _built(tmp_path), root, "r001").returncode == 0
        r = _sh(d / "release-static.sh",
                _built(tmp_path, "build2", "<html>v2</html>"), root, "..")
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "REFUSING" in out, out
        # the live release is untouched and still served
        assert (root / "releases" / "r001" / "index.html").read_text() == "<html>v1</html>"
        assert os.readlink(root / "current") == "releases/r001"

    def test_flip_does_not_rely_on_a_single_mv_dialect(self):
        # Verified on darwin while writing this: `mv -Tf` is an illegal option
        # on BSD mv, and plain `mv -f newlink current` FOLLOWS a current that
        # points at a directory and drops the new link INSIDE the old release
        # (the site then silently stays on the old release). So the flip tries
        # the GNU form and falls back to `ln -sfn` (GNU/BSD/busybox), and the
        # post-fact readlink is what actually decides.
        text = T.RELEASE_STATIC_SH
        assert "mv -Tf" in text
        assert "ln -sfn" in text
        assert 'current(after)=' in text


# ── (b) each.sh ──────────────────────────────────────────────────────────
class TestEach:
    def _list(self, tmp_path, items=("a", "b", "c")):
        p = tmp_path / "list.txt"
        p.write_text("# a comment\n\n" + "\n".join(items) + "\n")
        return p

    def test_one_success_never_passes_for_all(self, tmp_path):
        d = _deploy_dir(tmp_path)
        only_b = tmp_path / "only-b.sh"
        only_b.write_text('#!/bin/sh\n[ "$1" = "b" ]\n')
        only_b.chmod(0o755)
        r = _sh(d / "each.sh", self._list(tmp_path), only_b)
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "1/3" in out, out

    def test_all_pass_exits_zero_with_full_tally(self, tmp_path):
        d = _deploy_dir(tmp_path)
        r = _sh(d / "each.sh", self._list(tmp_path), "true")
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert "3/3" in out, out

    def test_stdin_is_detached_per_item_in_the_template(self, tmp_path):
        assert "</dev/null" in T.EACH_SH

    def test_a_stdin_eating_command_does_not_swallow_the_list(self, tmp_path):
        d = _deploy_dir(tmp_path)
        eater = tmp_path / "eater.sh"
        # the certbot shape: the inner command reads stdin greedily
        eater.write_text("#!/bin/sh\nhead -c 1000000 >/dev/null\nexit 0\n")
        eater.chmod(0o755)
        r = _sh(d / "each.sh", self._list(tmp_path), eater)
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert "3/3" in out, out

    def test_every_item_sees_the_same_empty_stdin(self, tmp_path):
        d = _deploy_dir(tmp_path)
        r = _sh(d / "each.sh", self._list(tmp_path), "sh", "-c", "read x; exit 1")
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "0/3" in out, out  # 0-then-fail, never 1-then-hang


# ── (c) verify-host-config.sh ────────────────────────────────────────────
class TestVerifyHostConfig:
    def _project(self, tmp_path, repo_body="csp: repo\n"):
        proj = tmp_path / "proj"
        _deploy_dir(proj)
        repo_file = proj / "service-configs" / "nginx" / "site.conf"
        repo_file.parent.mkdir(parents=True, exist_ok=True)
        repo_file.write_text(repo_body)
        host_dir = tmp_path / "host" / "etc" / "nginx"
        host_dir.mkdir(parents=True, exist_ok=True)
        return proj, repo_file, host_dir

    def _manifest(self, proj, *lines):
        (proj / "deploy" / "host-config.manifest").write_text("\n".join(lines) + "\n")

    def _pair(self, host_dir, name="site.conf"):
        return "service-configs/nginx/site.conf %s/%s" % (host_dir, name)

    def test_no_manifest_is_a_clean_pass(self, tmp_path):
        proj, _, _ = self._project(tmp_path)
        r = _sh(proj / "deploy" / "verify-host-config.sh", cwd=proj)
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert "nothing declared" in out, out

    def test_matching_target_passes(self, tmp_path):
        proj, repo_file, host_dir = self._project(tmp_path)
        self._manifest(proj, "# repo-relative   target-absolute",
                       self._pair(host_dir))
        (host_dir / "site.conf").write_text(repo_file.read_text())
        r = _sh(proj / "deploy" / "verify-host-config.sh", cwd=proj)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_target_content_drift_fails(self, tmp_path):
        proj, repo_file, host_dir = self._project(tmp_path)
        self._manifest(proj, self._pair(host_dir))
        (host_dir / "site.conf").write_text("csp: hand-edited on the host\n")
        r = _sh(proj / "deploy" / "verify-host-config.sh", cwd=proj)
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "DRIFT" in out, out

    def test_missing_target_file_is_drift(self, tmp_path):
        proj, _, host_dir = self._project(tmp_path)
        self._manifest(proj, self._pair(host_dir))
        r = _sh(proj / "deploy" / "verify-host-config.sh", cwd=proj)
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "DRIFT" in out, out

    def test_unmanaged_file_in_a_managed_dir_is_reported(self, tmp_path):
        proj, repo_file, host_dir = self._project(tmp_path)
        self._manifest(proj, self._pair(host_dir))
        (host_dir / "site.conf").write_text(repo_file.read_text())
        # the vhost incident: a second layer of config nobody's repo owns
        (host_dir / "csp-extra.conf").write_text("add_header Content-Security-Policy ...;\n")
        r = _sh(proj / "deploy" / "verify-host-config.sh", cwd=proj)
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "csp-extra.conf" in out, out
        assert "outside the repo" in out, out

    def test_reports_every_finding_before_the_verdict(self, tmp_path):
        proj, _, host_dir = self._project(tmp_path)
        self._manifest(proj, self._pair(host_dir))
        (host_dir / "site.conf").write_text("drifted\n")
        (host_dir / "csp-extra.conf").write_text("second layer\n")
        r = _sh(proj / "deploy" / "verify-host-config.sh", cwd=proj)
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "DRIFT" in out and "csp-extra.conf" in out, out


# ── wiring / docs ────────────────────────────────────────────────────────
class TestWiring:
    def test_deploy_sh_runs_verify_host_config_after_check_env(self):
        text = T.DEPLOY_SH
        assert "verify-host-config.sh" in text
        assert text.index("check-env.sh") < text.index("verify-host-config.sh")
        # before build: the drift must surface while the old stand still serves
        assert text.index("verify-host-config.sh") < text.index("compose --env-file")

    def test_deploy_sh_does_not_wire_the_standalone_tools(self):
        assert "release-static.sh" not in T.DEPLOY_SH
        assert "each.sh" not in T.DEPLOY_SH

    def test_module_docstring_lists_the_new_scripts(self):
        doc = T.__doc__ or ""
        for name in ("release-static.sh", "each.sh", "verify-host-config.sh"):
            assert name in doc, name

    @pytest.mark.parametrize("attr", sorted(SCRIPTS.values()))
    def test_templates_are_posix_sh(self, attr, tmp_path):
        text = getattr(T, attr)
        assert text.startswith("#!/bin/sh")
        # comments are prose (they name the incidents); scan the CODE lines.
        # [[:space:]] is a POSIX bracket expression, not a bash test.
        bashisms = (r"\[\[\s", r"\s\]\]", r"\bfunction\s+\w+\s*\(",
                    r"\bdeclare\s+-", r"^\s*local\s", r"(^|\s)\$'",
                    "<<<", "&>", r"\[\s[^]\n]*\s==\s")
        for ln in text.splitlines():
            if ln.lstrip().startswith("#"):
                continue
            for bashism in bashisms:
                assert not re.search(bashism, ln), f"{attr}: bashism {bashism!r}: {ln}"
        p = tmp_path / "t.sh"
        p.write_text(text)
        # -n: parses under a strict POSIX shell
        r = subprocess.run([SH, "-n", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


# ── the scaffold writes the whole family ─────────────────────────────────────
# A template that exists but is not registered in _write_deploy_scripts is a
# mechanism no generated project receives — the registration is the consumer.


def test_write_deploy_scripts_emits_the_whole_family(tmp_path):
    from stapel_tools.create_project import _write_deploy_scripts

    _write_deploy_scripts(tmp_path)
    expected = {
        "check-env.sh", "deploy.sh", "preflight.sh", "verify-stand-state.sh",
        "smoke-services.sh", "release-static.sh", "each.sh",
        "verify-host-config.sh",
    }
    written = {p.name for p in (tmp_path / "deploy").iterdir()}
    assert expected <= written
    for name in expected:
        assert (tmp_path / "deploy" / name).stat().st_mode & 0o111, name
