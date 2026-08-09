"""A tag without a release is not a release (stapel-registry-check, task #232).

The fixtures below are three REAL incidents from this fleet, not made up:

* four libs carried version tags that were never on PyPI — publish had
  failed on its own trusted-publisher config (#48);
* 0.15.2 and 0.15.3 never left the machine at all: `--follow-tags` silently
  doesn't push a lightweight tag, and push reported success because it had
  nothing to complain about;
* 2026-08-06 — GitHub Actions in `major_outage`: commits and tags exist on
  the remote, no workflow ran, nothing reached PyPI.

All three were found by hand. The gate costs one HTTP request.

The MOST IMPORTANT class of test here is the last one: an unreachable
registry does NOT count as success. A publish gate that goes green when it
couldn't ask is exactly the defect this module exists to catch.
"""
import json
import subprocess

import pytest

from stapel_tools import registry_check as rc


@pytest.fixture
def repo(tmp_path):
    """An initialized git repo with a pyproject."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "stapel-demo"\nversion = "0.3.0"\n', encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def tag(repo, *versions):
    """Stamp version tags (as empty commits — content doesn't matter)."""
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit",
         "-q", "--allow-empty", "-m", "x"],
        cwd=repo, check=True,
    )
    for version in versions:
        subprocess.run(["git", "tag", f"v{version}"], cwd=repo, check=True)


def published(monkeypatch, *versions, kind="pypi"):
    monkeypatch.setattr(rc, "released_versions", lambda name, k: set(versions))


def codes(findings):
    return [f.rule for f in findings]


# ---------------------------------------------------------------------------
# REG001 — the rule that would have caught all three incidents
# ---------------------------------------------------------------------------


class TestTagWithoutRelease:
    def test_tag_not_in_registry_is_an_error(self, repo, monkeypatch):
        tag(repo, "0.3.0")
        published(monkeypatch)  # empty registry — publish never happened
        findings = rc.check_repo(repo)
        assert codes(findings) == ["REG001"]
        assert findings[0].level == "error"

    def test_message_names_the_version_and_the_registry(self, repo, monkeypatch):
        tag(repo, "0.15.2")
        published(monkeypatch, "0.15.1")
        (finding,) = [f for f in rc.check_repo(repo) if f.rule == "REG001"]
        assert "0.15.2" in finding.message and "PyPI" in finding.message

    def test_released_tag_is_silent(self, repo, monkeypatch):
        tag(repo, "0.3.0")
        published(monkeypatch, "0.3.0")
        assert rc.check_repo(repo) == []

    def test_every_dead_tag_is_named_separately(self, repo, monkeypatch):
        # Incident #48: four libs, not one. Collapsing them into "there are
        # problems" would leave a human to work out the list by hand.
        tag(repo, "0.1.0", "0.2.0", "0.3.0")
        published(monkeypatch, "0.1.0")
        assert codes(rc.check_repo(repo)).count("REG001") == 2


class TestPrehistory:
    """Tags older than the first release aren't a broken release — the
    package just didn't exist yet.

    Fleet measurement (2026-08-07): 100 missing tags, 60 of them prehistory.
    Without this cutoff the real forty (including that day's stuck
    stapel-core 0.19.0) drown in noise, and a gate nobody reads is a
    missing gate.
    """

    def test_tags_before_the_first_release_are_not_errors(self, repo, monkeypatch):
        tag(repo, "0.1.0", "0.2.0", "0.3.0")
        published(monkeypatch, "0.3.0")  # first release is 0.3.0
        assert rc.check_repo(repo) == []

    def test_but_the_count_is_always_said_out_loud(self, repo, monkeypatch):
        tag(repo, "0.1.0", "0.2.0", "0.3.0")
        published(monkeypatch, "0.3.0")
        notes = []
        rc.check_repo(repo, notes=notes)
        assert notes and "2 tag" in notes[0] and "--all-history" in notes[0]

    def test_a_hole_after_the_first_release_stays_an_error(self, repo, monkeypatch):
        # Exactly the #48 class: early versions published, the middle one isn't.
        tag(repo, "0.1.0", "0.2.0", "0.3.0")
        published(monkeypatch, "0.1.0", "0.3.0")
        (finding,) = rc.check_repo(repo)
        assert finding.rule == "REG001" and "0.2.0" in finding.message

    def test_all_history_brings_them_back(self, repo, monkeypatch):
        tag(repo, "0.1.0", "0.2.0", "0.3.0")
        published(monkeypatch, "0.3.0")
        assert len(rc.check_repo(repo, all_history=True)) == 2

    def test_nothing_published_at_all_keeps_every_tag(self, repo, monkeypatch):
        # An empty registry isn't "prehistory" but a release that never
        # happened at all: there's nothing to cut off from, and staying
        # silent would be the worst outcome.
        tag(repo, "0.1.0", "0.2.0")
        published(monkeypatch)
        assert codes(rc.check_repo(repo)).count("REG001") == 2

    def test_version_key_orders_numerically_not_lexically(self):
        # '0.9.0' > '0.10.0' as a string — the cutoff would eat a real hole here.
        assert rc.version_key("0.9.0") < rc.version_key("0.10.0")
        assert rc.version_key("1.0.0") > rc.version_key("0.99.99")

    def test_unparsable_version_does_not_crash_the_gate(self):
        assert isinstance(rc.version_key("0.1.0rc1"), tuple)
        assert isinstance(rc.version_key("weird"), tuple)


# ---------------------------------------------------------------------------
# REG002/REG003 — warnings, not blockers
# ---------------------------------------------------------------------------


class TestWarnings:
    def test_release_without_tag_is_a_warning(self, repo, monkeypatch):
        tag(repo, "0.3.0")
        published(monkeypatch, "0.3.0", "0.2.9")
        (finding,) = [f for f in rc.check_repo(repo) if f.rule == "REG002"]
        assert finding.level == "warning" and "0.2.9" in finding.message

    def test_untagged_unreleased_manifest_version_is_stated_once(self, repo, monkeypatch):
        published(monkeypatch)  # no tags, no releases — ordinary development
        assert codes(rc.check_repo(repo)) == ["REG003"]

    def test_registry_version_newer_than_local_is_not_a_rule(self, repo, monkeypatch):
        # A stale checkout isn't a release defect; flagging it here would
        # redden the build for anyone who hasn't fetched.
        tag(repo, "0.3.0")
        published(monkeypatch, "0.3.0", "0.4.0")
        assert codes(rc.check_repo(repo)) == ["REG002"]


# ---------------------------------------------------------------------------
# Unreachable registry != "nothing published"
# ---------------------------------------------------------------------------


class TestUnreachable:
    def test_unreachable_registry_refuses_to_report_success(self, repo, monkeypatch):
        tag(repo, "0.3.0")

        def boom(name, kind):
            raise rc.RegistryUnreachable("no network")

        monkeypatch.setattr(rc, "released_versions", boom)
        with pytest.raises(rc.RegistryUnreachable):
            rc.check_repo(repo)

    def test_cli_exits_nonzero_when_it_could_not_ask(self, repo, monkeypatch, capsys):
        tag(repo, "0.3.0")
        monkeypatch.setattr(
            rc, "released_versions",
            lambda n, k: (_ for _ in ()).throw(rc.RegistryUnreachable("no network")),
        )
        assert rc.main([str(repo)]) == 1
        assert "Refusing to report success" in capsys.readouterr().err

    def test_offline_ok_passes_but_says_so_loudly(self, repo, monkeypatch, capsys):
        tag(repo, "0.3.0")
        monkeypatch.setattr(
            rc, "released_versions",
            lambda n, k: (_ for _ in ()).throw(rc.RegistryUnreachable("no network")),
        )
        assert rc.main([str(repo), "--offline-ok"]) == 0
        # Silent green must not happen even under an explicit opt-out.
        assert "cannot reach" in capsys.readouterr().err

    def test_404_means_no_releases_yet_not_unreachable(self, monkeypatch):
        import urllib.error

        def not_found(url, timeout=None):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        monkeypatch.setattr(rc.urllib.request, "urlopen", not_found)
        assert rc.released_versions("stapel-brand-new", "pypi") == set()

    def test_500_is_unreachable_not_empty(self, monkeypatch):
        import urllib.error

        def server_error(url, timeout=None):
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)

        monkeypatch.setattr(rc.urllib.request, "urlopen", server_error)
        with pytest.raises(rc.RegistryUnreachable):
            rc.released_versions("stapel-core", "pypi")


# ---------------------------------------------------------------------------
# What wasn't checked is reported as not checked
# ---------------------------------------------------------------------------


class TestNotSilent:
    def test_repo_without_a_manifest_is_reported_not_skipped(self, tmp_path):
        notes = []
        assert rc.check_repo(tmp_path, notes=notes) == []
        assert notes and "nothing publishable" in notes[0]

    def test_nothing_found_is_not_a_pass(self, tmp_path, capsys):
        assert rc.main([str(tmp_path)]) == 1
        assert "NOTHING WAS VERIFIED" in capsys.readouterr().err

    def test_strict_turns_warnings_into_failure(self, repo, monkeypatch):
        published(monkeypatch)
        assert rc.main([str(repo)]) == 0
        assert rc.main([str(repo), "--strict"]) == 1


# ---------------------------------------------------------------------------
# npm and fleet discovery
# ---------------------------------------------------------------------------


class TestNpmAndDiscovery:
    def test_npm_package_is_understood(self, tmp_path, monkeypatch):
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "@stapel/auth-react", "version": "0.12.2"}),
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        tag(tmp_path, "0.12.2")
        seen = {}

        def capture(name, kind):
            seen["kind"] = kind
            return set()

        monkeypatch.setattr(rc, "released_versions", capture)
        (finding,) = rc.check_repo(tmp_path)
        assert seen["kind"] == "npm"
        assert "npm" in finding.message

    def test_discovery_walks_stapel_dirs(self, tmp_path):
        for name in ("stapel-core", "stapel-auth", "not-ours"):
            package = tmp_path / name
            package.mkdir()
            (package / "pyproject.toml").write_text(
                f'[project]\nname = "{name}"\nversion = "0.1.0"\n', encoding="utf-8"
            )
        assert [p.name for p in rc.discover_repos(tmp_path)] == [
            "stapel-auth", "stapel-core"
        ]

    def test_a_package_target_checks_itself(self, repo):
        assert rc.discover_repos(repo) == [repo]


# ---------------------------------------------------------------------------
# machine-readable output — for cron
# ---------------------------------------------------------------------------


def test_json_output_carries_findings_and_what_was_checked(repo, monkeypatch, capsys):
    tag(repo, "0.3.0")
    published(monkeypatch)
    assert rc.main([str(repo), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["checked"] == [repo.name]
    assert payload["findings"][0]["rule"] == "REG001"
