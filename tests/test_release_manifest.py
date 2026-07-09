"""release.json builder tests — schema, determinism, contracts, digest,
sha verification, and the scaffolded-project end-to-end."""
import json
import subprocess

import pytest
from test_migration_lint import CREATE_ORDER, make_app, make_migration

from stapel_tools.create_project import create_project
from stapel_tools.release import (
    SCHEMA_VERSION,
    build_manifest,
    check_contract_freshness,
    collect_contracts,
    compute_config_digest,
    main,
    to_json,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_project(tmp_path, name="demo"):
    """Bare project skeleton: manage.py + settings + one app with migrations."""
    proj = tmp_path / name
    proj.mkdir()
    (proj / "manage.py").write_text("#!/usr/bin/env python\n")
    core = proj / "core"
    core.mkdir()
    (core / "settings.py").write_text(
        "DEBUG = False\n"
        'STAPEL_COMM = {"OUTBOX_ENABLED": True, "ACTION_TRANSPORT": "inprocess"}\n'
        'STAPEL_AUTH = {"MFA": "totp"}\n'
    )
    (proj / "requirements.txt").write_text(
        "django>=6,<7\n"
        "stapel-core==0.3.1\n"
        "stapel-auth>=0.3.2,<0.4\n"
        "stapel_profiles @ git+https://github.com/usestapel/stapel-profiles.git@v0.3.1\n"
    )
    app = make_app(proj, "shop")
    make_migration(app, "0001_initial", [CREATE_ORDER])
    make_migration(
        app, "0002_backfill", ["migrations.RunPython(forward)"],
        marker="irreversible",
    )
    return proj


def build(proj, **kwargs):
    defaults = dict(
        release="r1",
        git_sha="a" * 40,
        images={"app": "reg.example/t/demo/app:r1"},
        created_at="2026-07-09T00:00:00Z",
        verify_sha=False,
    )
    defaults.update(kwargs)
    return build_manifest(proj, **defaults)


# ---------------------------------------------------------------------------
# Schema + determinism
# ---------------------------------------------------------------------------


class TestManifest:
    def test_schema_fields(self, tmp_path):
        manifest = build(make_project(tmp_path))
        assert sorted(manifest) == [
            "config_digest", "contracts", "created_at", "gates", "git_sha",
            "images", "migrations", "notes", "project", "release",
            "reversible_floor", "schema_version",
        ]
        assert manifest["schema_version"] == SCHEMA_VERSION
        assert manifest["release"] == "r1"
        assert manifest["project"] == "demo"
        assert manifest["images"] == {"app": "reg.example/t/demo/app:r1"}
        assert manifest["notes"] is None

    def test_watermark_and_floor(self, tmp_path):
        manifest = build(make_project(tmp_path))
        assert manifest["migrations"] == {"shop": "0002_backfill"}
        assert manifest["reversible_floor"] == {"shop": "0002_backfill"}

    def test_floor_zero_when_reversible(self, tmp_path):
        proj = make_project(tmp_path)
        (proj / "shop" / "migrations" / "0002_backfill.py").unlink()
        manifest = build(proj)
        assert manifest["reversible_floor"] == {"shop": "zero"}

    def test_contracts_from_requirements(self, tmp_path):
        manifest = build(make_project(tmp_path))
        assert manifest["contracts"] == {
            "stapel-core": "0.3.1",             # == pin → exact
            "stapel-auth": ">=0.3.2,<0.4",      # range pin → verbatim
            "stapel-profiles": "0.3.1",         # git tag → exact (v stripped)
        }

    def test_gates_default_and_lint_pass(self, tmp_path):
        manifest = build(make_project(tmp_path))
        assert manifest["gates"] == {
            "prodguard": None,
            "handover_scan": None,
            "migration_lint": "pass",
        }

    def test_lint_failure_recorded_not_fatal(self, tmp_path, capsys):
        proj = make_project(tmp_path)
        make_migration(
            proj / "shop", "0003_bad",
            ["migrations.RemoveField(model_name='order', name='note')"],
        )
        manifest = build(proj)
        assert manifest["gates"]["migration_lint"] == "fail"
        assert "MIG001" in capsys.readouterr().err

    def test_gate_overrides(self, tmp_path):
        manifest = build(
            make_project(tmp_path),
            gate_overrides={"prodguard": "pass", "handover_scan": "fail"},
        )
        assert manifest["gates"]["prodguard"] == "pass"
        assert manifest["gates"]["handover_scan"] == "fail"

    def test_migration_lint_gate_not_overridable(self, tmp_path):
        with pytest.raises(SystemExit, match="not overridable"):
            build(make_project(tmp_path), gate_overrides={"migration_lint": "pass"})

    def test_release_format_enforced(self, tmp_path):
        with pytest.raises(SystemExit, match="r<N>"):
            build(make_project(tmp_path), release="1.2.3")

    def test_deterministic_bytes(self, tmp_path):
        proj = make_project(tmp_path)
        first = to_json(build(proj))
        second = to_json(build(proj))
        assert first == second
        assert first.endswith("\n")
        # sorted keys at every level
        payload = json.loads(first)
        assert list(payload) == sorted(payload)
        assert list(payload["contracts"]) == sorted(payload["contracts"])

    def test_source_date_epoch(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "1750000000")
        manifest = build(make_project(tmp_path), created_at=None)
        assert manifest["created_at"] == "2025-06-15T15:06:40Z"


# ---------------------------------------------------------------------------
# contracts — vendored checkouts
# ---------------------------------------------------------------------------


class TestContracts:
    def test_vendored_checkout_wins_over_requirement_spec(self, tmp_path):
        proj = make_project(tmp_path)
        vendored = proj / "svc-demo" / "stapel_auth"
        vendored.mkdir(parents=True)
        (vendored / "pyproject.toml").write_text(
            '[project]\nname = "stapel-auth"\nversion = "0.5.4"\n'
        )
        contracts = collect_contracts(proj)
        assert contracts["stapel-auth"] == "0.5.4"

    def test_vendored_module_internal_pins_are_not_project_pins(self, tmp_path):
        proj = make_project(tmp_path)
        vendored = proj / "stapel_billing"
        vendored.mkdir()
        (vendored / "pyproject.toml").write_text(
            '[project]\nname = "stapel-billing"\nversion = "0.4.0"\n'
            'dependencies = ["stapel-currencies>=9.9"]\n'
        )
        # a requirements file INSIDE the vendored module must not leak either
        (vendored / "requirements-dev.txt").write_text("stapel-geo==9.9.9\n")
        contracts = collect_contracts(proj)
        assert contracts["stapel-billing"] == "0.4.0"
        assert "stapel-currencies" not in contracts
        assert "stapel-geo" not in contracts

    def test_unpinned_git_requirement(self, tmp_path):
        proj = tmp_path / "p"
        proj.mkdir()
        (proj / "requirements.txt").write_text(
            "stapel_core @ git+https://github.com/usestapel/stapel-core.git\n"
        )
        contracts = collect_contracts(proj)
        assert contracts["stapel-core"].startswith("@ git+")


# ---------------------------------------------------------------------------
# config_digest
# ---------------------------------------------------------------------------


class TestConfigDigest:
    def test_stable_and_sensitive(self, tmp_path):
        proj = make_project(tmp_path)
        digest_a = compute_config_digest(proj)
        assert digest_a == compute_config_digest(proj)
        assert digest_a.startswith("sha256:")

        settings = proj / "core" / "settings.py"
        settings.write_text(
            settings.read_text().replace('"MFA": "totp"', '"MFA": "webauthn"')
        )
        assert compute_config_digest(proj) != digest_a

    def test_non_stapel_settings_do_not_move_the_digest(self, tmp_path):
        proj = make_project(tmp_path)
        digest_a = compute_config_digest(proj)
        settings = proj / "core" / "settings.py"
        settings.write_text(settings.read_text().replace(
            "DEBUG = False", "DEBUG = True\nEXTRA = 42"
        ))
        assert compute_config_digest(proj) == digest_a

    def test_settings_package_layout(self, tmp_path):
        # monolith layout: core/settings/base.py
        proj = tmp_path / "m"
        (proj / "core" / "settings").mkdir(parents=True)
        (proj / "manage.py").write_text("")
        (proj / "core" / "settings" / "base.py").write_text(
            'STAPEL_COMM = {"ACTION_TRANSPORT": "bus"}\n'
        )
        assert compute_config_digest(proj) != compute_config_digest(tmp_path / "m2")


# ---------------------------------------------------------------------------
# contract artifact freshness — REL001/REL002
# ---------------------------------------------------------------------------


def make_module(
    tmp_path,
    *,
    name="stapel-demo",
    version="0.2.3",
    cap_version="0.2.3",
    include_capabilities=True,
    contract_target=False,
):
    """Bare stapel-* module repo shape: root pyproject.toml + docs/*.json —
    NOT a scaffolded customer project. release-manifest is also run directly
    against a module's own checkout when cutting THAT module's release
    (the calendar/recordings release process bug this gate closes)."""
    proj = tmp_path / name
    proj.mkdir()
    (proj / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "{version}"\n'
    )
    docs = proj / "docs"
    docs.mkdir()
    if include_capabilities:
        (docs / "capabilities.json").write_text(json.dumps(
            {"module": name, "version": cap_version, "provides": "x"}
        ))
    # schema.json's OpenAPI version is nested under "info" — a drf-spectacular
    # placeholder, never wired to the module version — and stays "0.0.0"
    # regardless of the module's actual version (real shape, see e.g.
    # stapel-calendar/docs/schema.json).
    (docs / "schema.json").write_text(json.dumps(
        {"openapi": "3.0.3", "info": {"title": "", "version": "0.0.0"}, "paths": {}}
    ))
    (docs / "flows.json").write_text(json.dumps([]))
    (docs / "errors.json").write_text(json.dumps([]))
    if contract_target:
        (proj / "Makefile").write_text("contract:\n\t@echo emit\n")
    return proj


class TestContractFreshness:
    def test_clean_when_versions_match(self, tmp_path):
        proj = make_module(tmp_path, version="0.2.3", cap_version="0.2.3")
        assert check_contract_freshness(proj) == []

    def test_stale_capabilities_version_is_blocking(self, tmp_path):
        proj = make_module(tmp_path, version="0.2.3", cap_version="0.2.2")
        violations = check_contract_freshness(proj)
        assert len(violations) == 1
        assert violations[0].rule == "REL001"
        assert violations[0].level == "error"
        assert "0.2.2" in violations[0].message
        assert "0.2.3" in violations[0].message
        assert "capabilities.json" in violations[0].path

    def test_schema_json_nested_version_is_never_checked(self, tmp_path):
        # schema.json's info.version is stuck at "0.0.0" by design (see
        # make_module) — it must never be flagged, only a top-level envelope
        # version counts.
        proj = make_module(tmp_path, version="9.9.9", cap_version="9.9.9")
        assert check_contract_freshness(proj) == []

    def test_missing_capabilities_with_contract_target_is_warning(self, tmp_path):
        proj = make_module(tmp_path, include_capabilities=False, contract_target=True)
        violations = check_contract_freshness(proj)
        assert len(violations) == 1
        assert violations[0].rule == "REL002"
        assert violations[0].level == "warning"

    def test_missing_capabilities_without_contract_target_is_silent(self, tmp_path):
        proj = make_module(tmp_path, include_capabilities=False, contract_target=False)
        assert check_contract_freshness(proj) == []

    def test_no_pyproject_is_silent(self, tmp_path):
        # a scaffolded customer project has no root pyproject.toml — the
        # artifacts belong to its vendored stapel-* module checkouts instead.
        proj = tmp_path / "no-pyproject"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "capabilities.json").write_text('{"version": "9.9.9"}')
        assert check_contract_freshness(proj) == []

    def test_build_manifest_fails_on_stale_artifact(self, tmp_path, capsys):
        proj = make_module(tmp_path, version="0.2.3", cap_version="0.2.2")
        with pytest.raises(SystemExit, match="contract artifact"):
            build_manifest(
                proj, release="r1", git_sha="a" * 40, images={}, verify_sha=False,
            )
        assert "REL001" in capsys.readouterr().err

    def test_build_manifest_clean_when_fresh(self, tmp_path):
        proj = make_module(tmp_path, version="0.2.3", cap_version="0.2.3")
        manifest = build_manifest(
            proj, release="r1", git_sha="a" * 40, images={}, verify_sha=False,
            created_at="2026-07-09T00:00:00Z",
        )
        assert manifest["release"] == "r1"

    def test_build_manifest_warns_but_does_not_fail_on_missing_artifact(
        self, tmp_path, capsys,
    ):
        proj = make_module(tmp_path, include_capabilities=False, contract_target=True)
        manifest = build_manifest(
            proj, release="r1", git_sha="a" * 40, images={}, verify_sha=False,
            created_at="2026-07-09T00:00:00Z",
        )
        assert manifest["release"] == "r1"
        assert "REL002" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# git sha verification
# ---------------------------------------------------------------------------


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
             "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo)},
    )


class TestShaVerification:
    def _repo_project(self, tmp_path):
        proj = make_project(tmp_path)
        _git(proj, "init", "-q")
        _git(proj, "add", ".")
        _git(proj, "commit", "-q", "-m", "x")
        out = subprocess.run(
            ["git", "-C", str(proj), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        )
        return proj, out.stdout.strip()

    def test_matching_head_passes(self, tmp_path):
        proj, head = self._repo_project(tmp_path)
        manifest = build(proj, git_sha=head, verify_sha=True)
        assert manifest["git_sha"] == head

    def test_mismatch_is_fatal(self, tmp_path):
        proj, _head = self._repo_project(tmp_path)
        with pytest.raises(SystemExit, match="does not match"):
            build(proj, git_sha="b" * 40, verify_sha=True)

    def test_mismatch_allowed_when_disabled(self, tmp_path):
        proj, _head = self._repo_project(tmp_path)
        manifest = build(proj, git_sha="b" * 40, verify_sha=False)
        assert manifest["git_sha"] == "b" * 40

    def test_non_repo_accepts_sha(self, tmp_path):
        manifest = build(make_project(tmp_path), verify_sha=True)
        assert manifest["git_sha"] == "a" * 40


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_stdout_and_image_args(self, tmp_path, capsys):
        proj = make_project(tmp_path)
        code = main([
            str(proj), "--release", "r2", "--git-sha", "c" * 40,
            "--image", "app=reg/x/app:r2", "--image", "worker=reg/x/worker:r2",
            "--no-verify-sha", "--created-at", "2026-07-09T00:00:00Z",
        ])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["images"] == {
            "app": "reg/x/app:r2", "worker": "reg/x/worker:r2",
        }

    def test_out_file_and_images_json(self, tmp_path, capsys):
        proj = make_project(tmp_path)
        images_file = tmp_path / "images.json"
        images_file.write_text('{"app": "reg/x/app:r1"}')
        out = tmp_path / "release.json"
        code = main([
            str(proj), "--release", "r1", "--git-sha", "c" * 40,
            "--images-json", str(images_file), "--out", str(out),
            "--no-verify-sha", "--gate", "prodguard=pass",
            "--notes", "first cut",
        ])
        assert code == 0
        payload = json.loads(out.read_text())
        assert payload["images"] == {"app": "reg/x/app:r1"}
        assert payload["gates"]["prodguard"] == "pass"
        assert payload["notes"] == "first cut"
        assert "r1" in capsys.readouterr().err

    def test_bad_gate_value_rejected(self, tmp_path):
        proj = make_project(tmp_path)
        with pytest.raises(SystemExit, match="pass"):
            main([str(proj), "--release", "r1", "--git-sha", "c" * 40,
                  "--gate", "prodguard=maybe", "--no-verify-sha"])


# ---------------------------------------------------------------------------
# End-to-end: scaffolded minimal project → lint + manifest
# ---------------------------------------------------------------------------


class TestScaffoldedProjectEndToEnd:
    def test_minimal_scaffold_lints_and_builds(self, tmp_path, capsys):
        create_project(
            name="e2e",
            project_type="minimal",
            title="E2E",
            url="https://e2e.dev",
            company_name="X",
            company_email="x@e2e.dev",
            modules=["core", "auth"],
            output_dir=tmp_path,
            use_submodules=False,
            init_git=False,
        )
        proj = tmp_path / "e2e"
        assert (proj / "manage.py").exists()

        # give the local app a real migration history
        app = proj / "apps" / "e2e"
        make_migration(app, "0001_initial", [CREATE_ORDER])

        from stapel_tools.migration_lint import main as lint_main
        assert lint_main([str(proj)]) == 0
        capsys.readouterr()

        manifest = build(proj, project="e2e")
        # local app discovered with its watermark; scaffold settings carry a
        # STAPEL_COMM block → non-trivial digest; pip modules land as pins
        assert manifest["migrations"]["e2e"] == "0001_initial"
        assert manifest["reversible_floor"]["e2e"] == "zero"
        assert "stapel-core" in manifest["contracts"]
        assert "stapel-auth" in manifest["contracts"]
        assert manifest["config_digest"].startswith("sha256:")
        assert manifest["gates"]["migration_lint"] == "pass"

        # byte-determinism against the scaffold, twice
        assert to_json(manifest) == to_json(build(proj, project="e2e"))

        # the generated Makefile carries the release seam (R-1 §3 hook: the
        # platform bake step calls these; standalone use documented inline)
        makefile = (proj / "Makefile").read_text()
        assert "release-manifest:" in makefile
        assert "stapel-release-manifest . --release $(RELEASE)" in makefile
        assert "migration-lint:" in makefile
        assert "stapel-migration-lint ." in makefile
        # the artifact itself never lands in the checkout
        assert "release.json" in (proj / ".gitignore").read_text()
