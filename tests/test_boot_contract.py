"""The deployment layer the scaffold emits, checked on a GENERATED project.

Three artefacts, one meta-gate:

* the boot contract (require/optional + an end-state assertion) — a bootstrap
  that reports success on an unmigrated database must not be expressible;
* the post-condition deploy gate — it checks the RESULT of ``up``, with
  restarts counted as a delta against a baseline taken before it;
* alert rules that can say "wrong", not only "absent".

The meta-gate is the point of this file: a generated project is *checked* to
have these properties rather than trusted to have had them emitted once, and
every check is exercised red as well as green. An unenforced convention decays.
"""
import shutil
import stat
import subprocess

import pytest
import yaml

from stapel_tools.create_project import create_project

BOOT_LIB = "/usr/local/lib/stapel-bootstrap.sh"


def _create(out_dir, ptype="monolith", name="app"):
    create_project(
        name=name, project_type=ptype, title=name.capitalize(),
        url="https://x.dev", company_name="X", company_email="x@x.dev",
        modules=["core"], output_dir=out_dir,
        use_submodules=False, init_git=False,
    )
    return out_dir / name


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    """One generated monolith, reused read-only across the module."""
    return _create(tmp_path_factory.mktemp("gen"))


@pytest.fixture
def project(generated, tmp_path):
    """A writable copy, for the tests that break something on purpose."""
    dest = tmp_path / "app"
    shutil.copytree(generated, dest)
    return dest


def _gate(project):
    return subprocess.run(
        ["sh", "scripts/verify_boot_contract.sh"],
        cwd=project, capture_output=True, text=True,
    )


class TestEmitted:
    @pytest.mark.parametrize("rel", [
        "scripts/bootstrap_lib.sh",
        "scripts/service_schema_health.py",
        "scripts/verify_boot_contract.sh",
        "deploy/verify-stand-state.sh",
        "deploy/smoke-services.sh",
        "service-configs/prometheus/prometheus.yml",
        "service-configs/grafana/provisioning/datasources/prometheus.yaml",
        "service-configs/grafana/provisioning/alerting/rules.yaml",
        "docker-compose.monitoring.yml",
    ])
    def test_monolith_emits_the_deployment_layer(self, generated, rel):
        assert (generated / rel).exists(), rel

    def test_microservices_emits_it_too(self, tmp_path):
        proj = _create(tmp_path, "microservices", "micro")
        for rel in ("scripts/bootstrap_lib.sh", "scripts/verify_boot_contract.sh",
                    "deploy/verify-stand-state.sh", "deploy/smoke-services.sh",
                    "service-configs/grafana/provisioning/alerting/rules.yaml"):
            assert (proj / rel).exists(), rel

    def test_minimal_gets_none_of_it(self, tmp_path):
        # minimal ships no docker/prod compose — nothing to deploy or watch.
        proj = _create(tmp_path, "minimal", "tiny")
        assert not (proj / "deploy").exists()
        assert not (proj / "scripts" / "bootstrap_lib.sh").exists()
        assert not (proj / "docker-compose.monitoring.yml").exists()

    @pytest.mark.parametrize("rel", [
        "scripts/verify_boot_contract.sh",
        "deploy/verify-stand-state.sh",
        "deploy/smoke-services.sh",
    ])
    def test_scripts_are_executable_and_valid_posix_sh(self, generated, rel):
        path = generated / rel
        assert path.stat().st_mode & stat.S_IXUSR, rel
        proc = subprocess.run(["sh", "-n", str(path)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr

    def test_service_bootstrap_and_lib_are_valid_posix_sh(self, generated):
        for path in (generated / "svc-app" / "bootstrap.sh",
                     generated / "scripts" / "bootstrap_lib.sh"):
            proc = subprocess.run(["sh", "-n", str(path)], capture_output=True, text=True)
            assert proc.returncode == 0, proc.stderr

    def test_service_probe_is_byte_identical_to_the_canonical_copy(self, generated):
        assert (generated / "svc-app" / "config" / "schema_health.py").read_bytes() == \
            (generated / "scripts" / "service_schema_health.py").read_bytes()

    def test_image_bakes_the_step_runner(self, generated):
        dockerfile = (generated / "svc-app" / "Dockerfile").read_text()
        assert f"COPY scripts/bootstrap_lib.sh {BOOT_LIB}" in dockerfile


class TestBootContractShape:
    """Neither shape a hand-written bootstrap ends up in is correct: no
    `set -e` steps over a failed migration, a blanket `set -e` makes a failed
    collectstatic fatal. Two verbs plus an end-state assertion instead."""

    def test_no_blanket_set_e(self, generated):
        code = [
            ln for ln in (generated / "svc-app" / "bootstrap.sh").read_text().splitlines()
            if not ln.strip().startswith("#")
        ]
        assert not any(ln.strip().startswith("set -e") for ln in code)

    def test_migrations_are_required_static_is_optional(self, generated):
        script = (generated / "svc-app" / "bootstrap.sh").read_text()
        assert 'require "migrations"' in script
        assert 'optional "collectstatic"' in script
        assert 'optional "superuser"' in script

    def test_closer_asserts_the_end_state(self, generated):
        # The end state, not the step statuses: statuses get lost, and
        # "bootstrap succeeded on an unmigrated database" must not be
        # expressible.
        assert "bootstrap_done" in (generated / "svc-app" / "bootstrap.sh").read_text()
        assert "migrate --check" in (generated / "scripts" / "bootstrap_lib.sh").read_text()

    def test_compose_chains_bootstrap_into_the_server(self, generated):
        # `sh bootstrap.sh; $RUN_CMD` would start the server regardless.
        assert "sh bootstrap.sh &&" in (generated / "svc-app.yml").read_text()


class TestBootContractRuns:
    """The verbs, exercised against a manage.py shim. Nothing here needs a
    database, a container or Django — the contract is a shell contract."""

    @pytest.fixture
    def runner(self, generated, tmp_path):
        binaries = tmp_path / "bin"
        binaries.mkdir()
        (binaries / "pg_isready").write_text("#!/bin/sh\nexit 0\n")
        (binaries / "python").write_text(
            "#!/bin/sh\n"
            'case "$2" in\n'
            '  migrate) [ "$3" = "--check" ] && exit "${CHECK_RC:-0}"; exit "${MIGRATE_RC:-0}" ;;\n'
            '  createsuperuser) exit "${SUPERUSER_RC:-0}" ;;\n'
            '  collectstatic) exit "${STATIC_RC:-0}" ;;\n'
            "esac\nexit 0\n"
        )
        for f in binaries.iterdir():
            f.chmod(0o755)

        def run(**env):
            return subprocess.run(
                ["sh", str(generated / "svc-app" / "bootstrap.sh")],
                cwd=tmp_path, capture_output=True, text=True,
                env={
                    "PATH": f"{binaries}:/usr/bin:/bin",
                    "POSTGRES_USER": "x",
                    "DJANGO_SUPERUSER_USERNAME": "a",
                    "DJANGO_SUPERUSER_PASSWORD": "b",
                    "STAPEL_BOOTSTRAP_LIB": str(generated / "scripts" / "bootstrap_lib.sh"),
                    **env,
                },
            )

        return run

    def test_all_steps_ok(self, runner):
        result = runner()
        assert result.returncode == 0, result.stderr
        assert "all ok" in result.stdout

    def test_failed_migration_refuses_to_serve(self, runner):
        # The incident: without this the server starts on an unmigrated schema.
        result = runner(MIGRATE_RC="1")
        assert result.returncode == 1
        assert "FATAL: migrations failed" in result.stderr

    def test_failed_collectstatic_only_degrades(self, runner):
        # One CSS file must not take the API down.
        result = runner(STATIC_RC="1")
        assert result.returncode == 0, result.stderr
        assert "DEGRADED" in result.stdout

    def test_success_on_an_unmigrated_schema_is_not_expressible(self, runner):
        # Every step "passed"; the end-state assertion still refuses.
        result = runner(CHECK_RC="1")
        assert result.returncode == 1
        assert "schema is behind the code" in result.stderr

    def test_degraded_does_not_soften_the_end_state(self, runner):
        result = runner(STATIC_RC="1", CHECK_RC="1")
        assert result.returncode == 1


class TestMetaGate:
    """A generated project must be CHECKED to have the contract. Each case
    below is the gate going red on a project that lost one property."""

    def test_a_freshly_generated_project_passes(self, generated):
        result = _gate(generated)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "satisfy the boot contract" in result.stdout

    def test_microservices_project_with_no_services_passes(self, tmp_path):
        proj = _create(tmp_path, "microservices", "micro")
        assert _gate(proj).returncode == 0

    def test_no_bootstrap_at_all(self, project):
        (project / "svc-app" / "bootstrap.sh").unlink()
        result = _gate(project)
        assert result.returncode == 1
        assert "no bootstrap.sh" in result.stderr

    def test_step_runner_not_sourced(self, project):
        boot = project / "svc-app" / "bootstrap.sh"
        boot.write_text(
            "\n".join(ln for ln in boot.read_text().splitlines() if BOOT_LIB not in ln)
        )
        assert "does not source" in _gate(project).stderr

    def test_end_state_assertion_removed(self, project):
        boot = project / "svc-app" / "bootstrap.sh"
        boot.write_text(boot.read_text().replace("\nbootstrap_done\n", "\n"))
        assert "no bootstrap_done" in _gate(project).stderr

    def test_blanket_set_e_reintroduced(self, project):
        boot = project / "svc-app" / "bootstrap.sh"
        boot.write_text(boot.read_text().replace("#!/bin/sh\n", "#!/bin/sh\nset -e\n"))
        assert "blanket 'set -e'" in _gate(project).stderr

    def test_migrations_downgraded_to_optional(self, project):
        boot = project / "svc-app" / "bootstrap.sh"
        boot.write_text(boot.read_text().replace('require "migrations"', 'optional "migrations"'))
        assert "migrations are not a require step" in _gate(project).stderr

    def test_step_smuggled_past_the_verbs(self, project):
        boot = project / "svc-app" / "bootstrap.sh"
        boot.write_text(boot.read_text() + "python manage.py loaddata seed\n")
        assert "outside require/optional" in _gate(project).stderr

    def test_image_stops_baking_the_step_runner(self, project):
        dockerfile = project / "svc-app" / "Dockerfile"
        dockerfile.write_text(
            "\n".join(ln for ln in dockerfile.read_text().splitlines()
                      if "bootstrap_lib.sh" not in ln)
        )
        assert "does not COPY scripts/bootstrap_lib.sh" in _gate(project).stderr

    def test_compose_steps_over_a_failed_bootstrap(self, project):
        compose = project / "svc-app.yml"
        compose.write_text(compose.read_text().replace("sh bootstrap.sh &&", "sh bootstrap.sh ;"))
        assert "not chained into the server command with &&" in _gate(project).stderr

    def test_probe_diverged_from_the_canonical_copy(self, project):
        probe = project / "svc-app" / "config" / "schema_health.py"
        probe.write_text(probe.read_text() + "# local tweak\n")
        assert "diverged from" in _gate(project).stderr

    def test_probe_missing(self, project):
        (project / "svc-app" / "config" / "schema_health.py").unlink()
        assert "schema drift would be invisible" in _gate(project).stderr

    def test_probe_not_registered(self, project):
        urls = project / "svc-app" / "config" / "urls.py"
        urls.write_text(
            "\n".join(ln for ln in urls.read_text().splitlines()
                      if "register_schema_check()" not in ln)
        )
        assert "does not call register_schema_check()" in _gate(project).stderr


class TestDeployGateIsAPostCondition:
    def test_deploy_takes_a_baseline_before_up_and_gates_after(self, generated):
        text = (generated / "deploy" / "deploy.sh").read_text()
        baseline = text.index("--baseline")
        up = text.index("up -d")
        gate = text.rindex("verify-stand-state.sh")
        smoke = text.index("smoke-services.sh")
        # A restart counter read without a pre-`up` baseline is a lifetime
        # number reported as "since this deploy" — a claim nothing measured.
        assert baseline < up < gate
        assert up < smoke

    def test_the_gate_owns_both_ends_of_the_baseline_format(self, generated):
        # One script writes the snapshot and one script reads it, so the
        # format cannot drift into disagreement.
        text = (generated / "deploy" / "verify-stand-state.sh").read_text()
        assert "--baseline" in text
        assert "RESTART_BASELINE" in text

    def test_the_gate_asks_the_running_code_about_the_schema(self, generated):
        text = (generated / "deploy" / "verify-stand-state.sh").read_text()
        assert "exec -T" in text and "migrate --check" in text
        code = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
        # --skip-checks silences every check, not the one in the way.
        assert not any("--skip-checks" in ln for ln in code)

    def test_smoke_probes_every_service_even_when_all_are_unreachable(self, project):
        """The trap: a smoke loop that `set -e`s out on the first unreachable
        service reports two of eight and exits zero. Port 1 refuses instantly."""
        (project / "services.conf").write_text("app\nghost\nthird\n")
        result = subprocess.run(
            ["sh", "deploy/smoke-services.sh", "127.0.0.1:1"],
            cwd=project, capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "CURL_TIMEOUT": "2"},
        )
        assert result.returncode == 1
        assert len([ln for ln in result.stdout.splitlines() if " 000 " in ln]) == 3
        assert "3 of 3" in result.stderr


@pytest.fixture(scope="module")
def rules(generated):
    doc = yaml.safe_load(
        (generated / "service-configs" / "grafana" / "provisioning"
         / "alerting" / "rules.yaml").read_text()
    )
    return {r["uid"]: r for g in doc["groups"] for r in g["rules"]}


class TestAlertRules:
    def test_reachability_rules_are_kept(self, rules):
        # Not replaced: an OOM kill does not crash-loop, and reachability is
        # the only thing that catches it.
        assert "stapel-container-down" in rules
        assert "stapel-service-down" in rules

    def test_crash_loop_rule_is_keyed_on_a_monotonic_counter(self, rules):
        # A reachability rule on a container that restarts every minute
        # crosses its threshold in both directions and notifies per crossing.
        expr = rules["stapel-container-restarting"]["data"][0]["model"]["expr"]
        assert "changes(container_start_time_seconds" in expr
        assert "container_last_seen" not in expr

    def test_correctness_rule_exists(self, rules):
        # The container that caused the harm was up and healthy.
        expr = rules["stapel-schema-behind"]["data"][0]["model"]["expr"]
        assert expr.strip() == "stapel_schema_at_head{}"

    def test_blindness_is_a_separate_fact_with_its_own_sentence(self, rules):
        behind = rules["stapel-schema-behind"]["annotations"]["summary"]
        blind = rules["stapel-schema-probe-blind"]["annotations"]["summary"]
        assert rules["stapel-schema-probe-blind"]["data"][0]["model"]["expr"].strip() \
            == "stapel_schema_probe_ok{}"
        assert behind != blind
        assert "behind" not in blind

    def test_no_data_defaults_to_nodata(self, rules):
        """A dead exporter must not turn the board green, and must not fire a
        rule's own summary for something it never measured."""
        for uid, rule in rules.items():
            if uid == "stapel-schema-behind":
                continue
            assert rule["noDataState"] == "NoData", uid
            assert rule["execErrState"] == "Error", uid

    def test_the_one_ok_is_the_one_whose_absence_is_designed(self, rules):
        # stapel_schema_at_head is deliberately NOT emitted when the state
        # could not be determined, so NoData here would fire on every database
        # restart. The gap is covered by the probe-blind rule, whose series is
        # emitted unconditionally.
        assert rules["stapel-schema-behind"]["noDataState"] == "OK"
        assert rules["stapel-schema-probe-blind"]["noDataState"] == "NoData"

    def test_container_selection_is_by_label_not_by_a_name_list(self, rules):
        # A selector that does not name a container is silent about it
        # forever, which reads exactly like healthy.
        for uid in ("stapel-container-down", "stapel-container-restarting"):
            expr = rules[uid]["data"][0]["model"]["expr"]
            assert "container_label_com_docker_compose_project" in expr
            # one-shot writers are supposed to be gone
            assert "frontend-build" in expr

    def test_the_monitoring_overlay_reads_the_provisioning(self, generated):
        overlay = yaml.safe_load((generated / "docker-compose.monitoring.yml").read_text())
        grafana = overlay["services"]["grafana"]
        assert any("service-configs/grafana/provisioning" in v for v in grafana["volumes"])
        prom = overlay["services"]["prometheus"]
        assert any("service-configs/prometheus/prometheus.yml" in v for v in prom["volumes"])
        # cAdvisor carries the restart counter both container rules rest on.
        assert "cadvisor" in overlay["services"]

    def test_prometheus_scrapes_the_generated_service(self, generated):
        doc = yaml.safe_load(
            (generated / "service-configs" / "prometheus" / "prometheus.yml").read_text()
        )
        jobs = {j["job_name"]: j for j in doc["scrape_configs"]}
        assert "cadvisor" in jobs
        assert "svc-app" in jobs, "stapel-new-service must have appended this service's job"
        assert jobs["svc-app"]["metrics_path"] == "/app/api/metrics/"
