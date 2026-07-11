"""assemble_scaffold — static scaffold assembler tests
(static-scaffold-and-config.md §1.3/§1.4/§6.1).

Covers: idempotent assembly of N libs, core-first INSTALLED_APPS ordering,
green check+boot-smoke on the assembled project, and an unknown lib producing
a structured gap report instead of a crash. The full four-lib proof
(auth/notifications/gdpr/profiles — the exact set T-001/003/005/007 wired by
hand through an LLM cycle) only runs its live check/boot-smoke assertions when
those modules are actually importable in the current interpreter (true in the
shared workspace venv; skipped in a bare stapel-tools-only checkout, same
pattern as test_create_project's TestModuleConfigValidation sibling checks).
"""
import importlib.util
import sys

import pytest

from stapel_tools.assemble_scaffold import assemble_scaffold, main
from stapel_tools.create_project import STAPEL_LIBS

PROOF_LIBS = ["auth", "notifications", "gdpr", "profiles"]


def _importable(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _pip_entry(key: str) -> str:
    """The requirements.txt line _setup_pip_deps renders for a STAPEL_LIBS
    key: an editable sibling-checkout install for ahead-of-PyPI modules (not
    honestly resolvable from PyPI yet), a plain version pin otherwise."""
    info = STAPEL_LIBS[key]
    pypi_name = info["repo"].rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    if info.get("ahead_of_pypi"):
        return f"-e ../{pypi_name}"
    return f"{pypi_name}>={info['pin']}"


def _tree(root):
    return {
        p.relative_to(root): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".env" not in p.name  # .env carries generated secrets
    }


class TestWiringAndGaps:
    def test_unknown_lib_is_a_gap_not_a_crash(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["not-a-real-lib"], output_dir=tmp_path, verify=False
        )
        assert result.libs_unknown == ["not-a-real-lib"]
        assert result.libs_applied == []
        # the project was still created (core only) — a bad lib name in the
        # request doesn't block assembly of the rest.
        assert (result.project_dir / "manage.py").exists()

    def test_known_and_unknown_libs_mixed(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["auth", "ghost-module", "gdpr"],
            output_dir=tmp_path, verify=False,
        )
        assert result.libs_applied == ["auth", "gdpr"]
        assert result.libs_unknown == ["ghost-module"]
        reqs = (result.project_dir / "requirements.txt").read_text()
        assert _pip_entry("auth") in reqs
        assert _pip_entry("gdpr") in reqs
        assert "ghost-module" not in reqs

    def test_core_is_never_duplicated_or_treated_as_unknown(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["core", "auth"], output_dir=tmp_path, verify=False
        )
        assert result.libs_unknown == []
        assert result.libs_applied == ["auth"]

    def test_duplicate_libs_deduped(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["auth", "auth", "gdpr"], output_dir=tmp_path, verify=False
        )
        assert result.libs_applied == ["auth", "gdpr"]

    def test_libs_wired_into_requirements_apps_and_urls(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["auth", "gdpr"], output_dir=tmp_path, verify=False
        )
        settings = (result.project_dir / "config" / "settings.py").read_text()
        urls = (result.project_dir / "config" / "urls.py").read_text()
        reqs = (result.project_dir / "requirements.txt").read_text()
        for key, app in (("auth", "stapel_auth"), ("gdpr", "stapel_gdpr")):
            assert f'"{app}",' in settings
            assert f'include("{app}.urls")' in urls
            assert _pip_entry(key) in reqs

    def test_module_config_reaches_settings(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["auth"],
            config={"auth": {"AUTH_PASSWORD_LOGIN": True}},
            output_dir=tmp_path, verify=False,
        )
        settings = (result.project_dir / "config" / "settings.py").read_text()
        assert "STAPEL_AUTH = {" in settings
        assert '"AUTH_PASSWORD_LOGIN": True,' in settings


class TestRegistryPins:
    """Owner directive: STAPEL_LIBS carries a workspace-local version pin and
    flags whether that pin is ahead of the last-published PyPI release, so a
    project assembled today is documented against what it actually got."""

    def test_every_lib_has_a_pin(self):
        for key, info in STAPEL_LIBS.items():
            assert "pin" in info, key
            assert "ahead_of_pypi" in info, key

    def test_matching_pypi_lib_gets_a_plain_version_pin(self, tmp_path):
        # auth matches the last-published PyPI release — a real, resolvable
        # pin, not a git ref of any kind.
        assert STAPEL_LIBS["auth"]["ahead_of_pypi"] is False
        result = assemble_scaffold("app", libs=["auth"], output_dir=tmp_path, verify=False)
        reqs = (result.project_dir / "requirements.txt").read_text()
        assert f"stapel-auth>={STAPEL_LIBS['auth']['pin']}" in reqs
        assert "git+" not in reqs
        assert "@v" not in reqs

    def test_ahead_of_pypi_lib_is_not_rendered_as_a_working_git_ref(self, tmp_path):
        # vault is ahead of / not yet on PyPI (owner publishes separately) —
        # a `name @ git+...` line would look like a working pin when no vX.Y.Z
        # tag exists upstream for these local-only fixes. The honest,
        # actually-installable-today line is an editable sibling checkout, not
        # a git ref.
        assert STAPEL_LIBS["vault"]["ahead_of_pypi"] is True
        result = assemble_scaffold(
            "app", libs=["vault"], output_dir=tmp_path, verify=False
        )
        reqs = (result.project_dir / "requirements.txt").read_text()
        assert f"v{STAPEL_LIBS['vault']['pin']}" in reqs
        assert "-e ../stapel-vault" in reqs
        assert "git+" not in reqs
        assert "@v" not in reqs


class TestIdempotency:
    def test_same_inputs_produce_byte_identical_trees(self, tmp_path):
        a = assemble_scaffold(
            "app", libs=["auth", "gdpr"], output_dir=tmp_path / "a", verify=False
        )
        b = assemble_scaffold(
            "app", libs=["auth", "gdpr"], output_dir=tmp_path / "b", verify=False
        )
        assert _tree(a.project_dir) == _tree(b.project_dir)

    def test_lib_order_in_call_does_not_change_output(self, tmp_path):
        a = assemble_scaffold(
            "app", libs=["auth", "gdpr", "notifications"],
            output_dir=tmp_path / "a", verify=False,
        )
        b = assemble_scaffold(
            "app", libs=["notifications", "auth", "gdpr"],
            output_dir=tmp_path / "b", verify=False,
        )
        assert _tree(a.project_dir) == _tree(b.project_dir)


class TestInstalledAppsOrder:
    def test_core_outbox_precedes_feature_libs_precedes_local_app(self, tmp_path):
        result = assemble_scaffold(
            "shop", libs=["auth", "gdpr"], output_dir=tmp_path, verify=False
        )
        settings = (result.project_dir / "config" / "settings.py").read_text()
        i_core = settings.index("stapel_core.django.outbox")
        i_auth = settings.index('"stapel_auth"')
        i_gdpr = settings.index('"stapel_gdpr"')
        i_local = settings.index('"apps.shop"')
        assert i_core < i_auth < i_local
        assert i_core < i_gdpr < i_local


class TestStaticVerificationGates:
    """R3/§44 boot-smoke reused as-is (efcb552); manage.py check under the
    project's own settings. Uses core-only assembly so the gate itself is
    exercised in every CI environment (stapel-core is always installed for
    this repo's test suite; stapel-auth/etc. are not)."""

    def test_core_only_assembly_is_green(self, tmp_path):
        result = assemble_scaffold("app", libs=[], output_dir=tmp_path)
        assert result.ok, [g.output for g in result.gates if not g.passed]
        names = {g.name for g in result.gates}
        assert names == {"check", "config-lint", "boot-smoke"}

    def test_assembly_writes_aggregated_config_md(self, tmp_path):
        # §2: the project gets a CONFIG.MD aggregated from the connected libs'
        # registries, and the config-lint gate passes on it.
        result = assemble_scaffold("app", libs=[], output_dir=tmp_path)
        config_md = result.project_dir / "CONFIG.MD"
        assert config_md.is_file()
        text = config_md.read_text()
        assert "## stapel-core" in text
        assert "SECRET_KEY" in text  # core's registry aggregated in
        cfg = next(g for g in result.gates if g.name == "config-lint")
        assert cfg.passed, cfg.output

    def test_verify_false_skips_gates(self, tmp_path):
        result = assemble_scaffold("app", libs=[], output_dir=tmp_path, verify=False)
        assert result.gates == []
        assert result.ok  # vacuously true — nothing ran, nothing failed

    def test_monolith_boot_smoke_reported_as_skipped_not_silently_green(self, tmp_path):
        result = assemble_scaffold(
            "app", project_type="monolith", libs=[], output_dir=tmp_path,
        )
        boot = next(g for g in result.gates if g.name == "boot-smoke")
        assert boot.skipped is True
        assert boot.passed is True  # doesn't fail the whole assembly
        assert "monolith" in boot.output


@pytest.mark.skipif(
    not all(_importable(f"stapel_{lib}") for lib in [*PROOF_LIBS, "core"]),
    reason="proof requires stapel-core/auth/notifications/gdpr/profiles importable "
    "(true in the shared workspace venv; not installed for a bare stapel-tools checkout)",
)
class TestFourLibProof:
    """The literal T-001/003/005/007 replacement: one static call assembles a
    project with all four libs those tasks wired by hand through an LLM cycle,
    and both static gates come back green — in seconds, offline."""

    def test_proof_project_assembles_and_is_green(self, tmp_path):
        result = assemble_scaffold("proof", libs=PROOF_LIBS, output_dir=tmp_path)

        assert result.libs_unknown == []
        assert set(result.libs_applied) == set(PROOF_LIBS)
        assert result.ok, [(g.name, g.output) for g in result.gates if not g.passed]

        settings = (result.project_dir / "config" / "settings.py").read_text()
        urls = (result.project_dir / "config" / "urls.py").read_text()
        reqs = (result.project_dir / "requirements.txt").read_text()
        for lib in PROOF_LIBS:
            app = f"stapel_{lib}"
            assert f'"{app}",' in settings
            assert f'include("{app}.urls")' in urls
            assert _pip_entry(lib) in reqs


class TestCLI:
    def test_cli_reports_gap_and_exits_nonzero_only_on_gate_failure(self, tmp_path, capsys):
        code = main(["app", "--libs", "auth", "bogus-lib", "--output-dir", str(tmp_path)])
        out = capsys.readouterr()
        assert code == 0  # gaps don't fail the run when gates pass
        assert "bogus-lib" in out.err
        assert "PASS" in out.out or "[PASS]" in out.out

    def test_cli_no_verify_skips_gates_and_still_succeeds(self, tmp_path, capsys):
        code = main(["app", "--libs", "auth", "--no-verify", "--output-dir", str(tmp_path)])
        assert code == 0
        proj = tmp_path / "app"
        assert (proj / "manage.py").exists()

    def test_cli_python_module_path(self):
        assert "stapel_tools.assemble_scaffold" in sys.modules


class TestAuthSubfeatureAxes:
    """Defect 3: auth is one package, but its subfeatures (magic_link, mfa
    (totp), sso, passkey, ...) are toggled by STAPEL_AUTH axes
    (stapel-auth/docs/capabilities.json), not installed/uninstalled as
    separate extras. assemble_scaffold's ``config`` param already renders
    STAPEL_<MOD> settings blocks (_module_config.py) validated against each
    module's real capabilities.json — this locks that mechanism in for the
    exact auth subfeatures a future onboarding wizard (§53) would ask about,
    and proves an unknown axis is a hard error rather than silently ignored."""

    SUBFEATURE_CONFIG = {
        "auth": {
            "AUTH_MAGIC_LINK_LOGIN": False,
            "AUTH_TOTP": True,
            "AUTH_SSO_LOGIN": True,
            "AUTH_PASSKEY_LOGIN": True,
        }
    }

    def test_selected_auth_subfeature_axes_reach_settings(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["auth"], config=self.SUBFEATURE_CONFIG,
            output_dir=tmp_path, verify=False,
        )
        settings = (result.project_dir / "config" / "settings.py").read_text()
        assert "STAPEL_AUTH = {" in settings
        assert '"AUTH_MAGIC_LINK_LOGIN": False,' in settings
        assert '"AUTH_TOTP": True,' in settings
        assert '"AUTH_SSO_LOGIN": True,' in settings
        assert '"AUTH_PASSKEY_LOGIN": True,' in settings
        # Not scooped wholesale — an axis NOT in the config is absent, not
        # defaulted-in as a literal settings key (defaults stay in
        # stapel_auth/conf.py).
        assert "AUTH_EMAIL_LOGIN" not in settings

    def test_unknown_auth_axis_is_a_hard_error_not_silently_passed_through(self, tmp_path):
        with pytest.raises(SystemExit):
            assemble_scaffold(
                "app", libs=["auth"],
                config={"auth": {"AUTH_NOT_A_REAL_AXIS": True}},
                output_dir=tmp_path, verify=False,
            )


class TestAuthPipExtras:
    """§20 visibility gap (owner, 2026-07-11): auth is one PyPI package, but
    axes whose feature needs an external dependency (stapel-auth/pyproject.toml
    optional-dependencies: oauth->social-auth-app-django, phone->twilio,
    saml->lxml/signxml) must land their extra in requirements.txt, or the
    project installs fine and crashes at runtime the moment that code path is
    hit. Covers: single axis -> single extra, several axes -> sorted
    de-duplicated extras, no dependency-bearing axis -> a bare pin (no
    ``[...]`` at all — auth's password/email/qr/passkey/magic-link axes ship
    in-package, no extra)."""

    def test_oauth_axis_adds_oauth_extra(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["auth"],
            config={"auth": {"AUTH_OAUTH_LOGIN": True}},
            output_dir=tmp_path, verify=False,
        )
        reqs = (result.project_dir / "requirements.txt").read_text()
        assert "stapel-auth[oauth]>=0.5.4,<0.6" in reqs

    def test_phone_axis_adds_phone_extra(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["auth"],
            config={"auth": {"AUTH_PHONE_REGISTRATION": True}},
            output_dir=tmp_path, verify=False,
        )
        reqs = (result.project_dir / "requirements.txt").read_text()
        assert "stapel-auth[phone]>=0.5.4,<0.6" in reqs

    def test_saml_axis_adds_saml_extra(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["auth"],
            config={"auth": {"AUTH_SSO_LOGIN": True}},
            output_dir=tmp_path, verify=False,
        )
        reqs = (result.project_dir / "requirements.txt").read_text()
        assert "stapel-auth[saml]>=0.5.4,<0.6" in reqs

    def test_multiple_dep_bearing_axes_produce_sorted_deduped_extras(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["auth"],
            config={"auth": {
                "AUTH_OAUTH_LOGIN": True,
                "AUTH_OAUTH_REGISTRATION": True,  # also maps to "oauth" — deduped
                "AUTH_PHONE_LOGIN": True,
            }},
            output_dir=tmp_path, verify=False,
        )
        reqs = (result.project_dir / "requirements.txt").read_text()
        assert "stapel-auth[oauth,phone]>=0.5.4,<0.6" in reqs

    def test_no_dep_bearing_axis_stays_a_bare_pin(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["auth"],
            config={"auth": {"AUTH_PASSWORD_LOGIN": True, "AUTH_TOTP": True}},
            output_dir=tmp_path, verify=False,
        )
        reqs = (result.project_dir / "requirements.txt").read_text()
        assert "stapel-auth>=0.5.4,<0.6" in reqs
        assert "stapel-auth[" not in reqs

    def test_no_config_at_all_stays_a_bare_pin(self, tmp_path):
        # No module_config -> nothing is known to have been turned on, so no
        # extra is claimed (mirrors render_settings_block: an axis absent
        # from module_config renders no settings key either).
        result = assemble_scaffold("app", libs=["auth"], output_dir=tmp_path, verify=False)
        reqs = (result.project_dir / "requirements.txt").read_text()
        assert "stapel-auth>=0.5.4,<0.6" in reqs
        assert "stapel-auth[" not in reqs
