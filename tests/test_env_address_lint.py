"""stapel-env-address-lint — EADDR001-003 (docs/pending/env-address-class-v2.md).

Each rule is exercised in BOTH directions (violation -> red, fix -> green),
plus the legitimate forms the incident measurement (v1 §1) singled out as the
false-positive traps a naive "any private IP" rule fell into: CIDR ranges
with a mask, loopback/resolver addresses, `0.0.0.0` binds, and the
`@host-gateway` sentinel.
"""
import json
from pathlib import Path

from stapel_tools import env_address_lint as eal


def _rules(findings):
    return {f.rule for f in findings}


# ---------------------------------------------------------------------------
# EADDR001 — literal RFC1918 endpoint in a deploy-class file
# ---------------------------------------------------------------------------


def _lint(text, name="prod.conf"):
    return eal.lint_file_eaddr001(Path(name), src=text)


class TestEADDR001Violations:
    """The incident's own three copies (v1 §1) — all a docker-gateway-shaped
    `set $livekit_upstream 172.17.0.1:7880` — must fire, both in a plain
    `.conf` and an envsubst `.conf.template`."""

    def test_gateway_shaped_literal_in_conf(self):
        findings = _lint("set $livekit_upstream 172.17.0.1:7880;\n")
        assert _rules(findings) == {"EADDR001"}
        assert "Docker bridge gateway" in findings[0].message

    def test_literal_in_conf_template(self):
        findings = _lint(
            "proxy_pass http://172.20.5.9:7880;\n", name="default.conf.template"
        )
        assert _rules(findings) == {"EADDR001"}

    def test_literal_in_yaml(self):
        findings = _lint("node_ip: 172.20.1.1\n", name="deploy/livekit.yaml")
        assert _rules(findings) == {"EADDR001"}

    def test_literal_in_env_file(self):
        findings = _lint("LIVEKIT_UPSTREAM=192.168.1.55:7880\n", name=".env.prod")
        assert _rules(findings) == {"EADDR001"}

    def test_10_slash_8_range_endpoint(self):
        findings = _lint("upstream backend { server 10.4.4.4:8000; }\n")
        assert _rules(findings) == {"EADDR001"}

    def test_non_gateway_shaped_message_has_no_gateway_language(self):
        # 10.x.x.x is RFC1918 but not the `172.16-31.x.1` gateway shape —
        # gets the generic message, not the intensified one.
        findings = _lint("server 10.4.4.4:8000;\n")
        assert "Docker bridge gateway" not in findings[0].message

    def test_fix_removes_the_finding(self):
        """Same file, address replaced by the compose-DNS name — the fix
        that actually shipped for eil meet's nginx/prod.conf."""
        broken = "set $livekit_upstream 172.17.0.1:7880;\n"
        fixed = "set $livekit_upstream ${LIVEKIT_UPSTREAM};\n"
        assert _lint(broken) != []
        assert _lint(fixed) == []


class TestEADDR001LegitimateForms:
    """The exact false-positive traps the v1 measurement found (83 -> 3
    true) — none of these may fire."""

    def test_cidr_with_mask_is_not_an_endpoint(self):
        assert _lint("allow 172.16.0.0/12;\n") == []
        assert _lint("set_real_ip_from 10.0.0.0/8;\n") == []

    def test_loopback_and_resolver(self):
        assert _lint("resolver 127.0.0.11 valid=10s;\n") == []
        assert _lint("listen 127.0.0.1:8000;\n") == []

    def test_bind_address_0000(self):
        assert _lint("listen 0.0.0.0:80;\n") == []

    def test_public_ip_out_of_scope(self):
        # node_ip: 91.193.43.65 — the SECOND incident family (v1/v2 §4/§5);
        # EADDR001 is deliberately RFC1918-only.
        assert _lint("node_ip: 91.193.43.65\n") == []

    def test_host_gateway_sentinel_never_matches(self):
        assert _lint("LIVEKIT_UPSTREAM=@host-gateway:7880\n") == []
        assert _lint("set $x @host-gateway:7880;\n") == []

    def test_noqa_marker_suppresses(self):
        assert _lint(
            "set $x 172.17.0.1:7880; # stapel: env-address-ok fixed test double\n"
        ) == []

    def test_noqa_rule_scoped_marker(self):
        assert _lint("set $x 172.17.0.1:7880; # noqa: EADDR001\n") == []


class TestEADDR001FileClassScope:
    """Only deploy-class files are scanned — code/doc/data files are not,
    even carrying the same literal (v1 §1: 'code (*.py, *.ts — test
    fixtures)', '*.md prose', '*.json grafana dumps')."""

    def test_python_file_not_scanned_by_walker(self, tmp_path):
        (tmp_path / "conftest.py").write_text("HOST = '172.17.0.1'\n")
        findings = eal.lint_eaddr001(tmp_path)
        assert findings == []

    def test_markdown_not_scanned(self, tmp_path):
        (tmp_path / "NOTES.md").write_text("gateway is 172.17.0.1\n")
        assert eal.lint_eaddr001(tmp_path) == []

    def test_json_not_scanned(self, tmp_path):
        (tmp_path / "grafana.json").write_text('{"ip": "172.17.0.1"}')
        assert eal.lint_eaddr001(tmp_path) == []

    def test_yaml_is_scanned(self, tmp_path):
        (tmp_path / "docker-compose.yml").write_text("x: 172.17.0.1\n")
        findings = eal.lint_eaddr001(tmp_path)
        assert _rules(findings) == {"EADDR001"}

    def test_dockerfile_is_scanned(self, tmp_path):
        (tmp_path / "Dockerfile.prod").write_text("ENV UPSTREAM=172.17.0.1\n")
        findings = eal.lint_eaddr001(tmp_path)
        assert _rules(findings) == {"EADDR001"}

    def test_tests_dir_skipped(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "fixture.yaml").write_text("x: 172.17.0.1\n")
        assert eal.lint_eaddr001(tmp_path) == []

    def test_venv_dir_skipped(self, tmp_path):
        venv_dir = tmp_path / ".venv" / "pkg"
        venv_dir.mkdir(parents=True)
        (venv_dir / "vendored.yaml").write_text("x: 172.17.0.1\n")
        assert eal.lint_eaddr001(tmp_path) == []


# ---------------------------------------------------------------------------
# EADDR002 / EADDR003 — env-boundary nginx upstream + the v2 gate
# ---------------------------------------------------------------------------


ENV_BOUNDARY_CONF = """\
server {
    location /rtc {
        proxy_pass http://172.17.0.1:7880;
        proxy_http_version 1.1;
    }
    location /api/ {
        proxy_pass http://backend:8000;
    }
}
"""

COMPOSE_NO_GATE = """\
services:
  backend:
    image: x
  nginx:
    image: nginx:alpine
    volumes:
      - ./service-configs/nginx:/etc/nginx/conf.d:ro
"""

COMPOSE_WITH_GATE = """\
services:
  backend:
    image: x
  nginx:
    image: nginx:alpine
    volumes:
      - ./service-configs/nginx:/etc/nginx/conf.d:ro
      - ./service-configs/nginx/40-upstream-gate.sh:/docker-entrypoint.d/40-upstream-gate.sh:ro
    healthcheck:
      test: ["CMD", "/docker-entrypoint.d/40-upstream-gate.sh", "--once"]
"""


def _make_project(tmp_path, conf_text, compose_text):
    nginx_dir = tmp_path / "service-configs" / "nginx"
    nginx_dir.mkdir(parents=True)
    (nginx_dir / "prod.conf").write_text(conf_text)
    (tmp_path / "docker-compose.yml").write_text(compose_text)
    return tmp_path


class TestEADDR002GateTransport:
    def test_env_boundary_without_gate_mount_fires(self, tmp_path):
        proj = _make_project(tmp_path, ENV_BOUNDARY_CONF, COMPOSE_NO_GATE)
        findings = eal.lint_eaddr002_003(proj)
        assert "EADDR002" in _rules(findings)
        finding = next(f for f in findings if f.rule == "EADDR002")
        assert "40-upstream-gate.sh" in finding.message
        assert "docker-entrypoint.d" in finding.message

    def test_env_boundary_with_gate_mount_is_clean(self, tmp_path):
        proj = _make_project(tmp_path, ENV_BOUNDARY_CONF, COMPOSE_WITH_GATE)
        findings = eal.lint_eaddr002_003(proj)
        assert "EADDR002" not in _rules(findings)

    def test_v2_include_idiom_counts_as_env_boundary_and_needs_the_gate(self, tmp_path):
        conf = """\
server {
    location /rtc {
        include /etc/nginx/stapel-gate/rtc.conf;
        proxy_connect_timeout 5s;
    }
}
"""
        proj = _make_project(tmp_path, conf, COMPOSE_NO_GATE)
        findings = eal.lint_eaddr002_003(proj)
        assert "EADDR002" in _rules(findings)

    def test_pure_in_network_proxy_is_never_flagged(self, tmp_path):
        conf = """\
server {
    location /api/ {
        proxy_pass http://backend:8000;
    }
}
"""
        proj = _make_project(tmp_path, conf, COMPOSE_NO_GATE)
        assert eal.lint_eaddr002_003(proj) == []

    def test_envsubst_var_resolving_to_compose_service_is_not_env_boundary(self, tmp_path):
        conf = """\
server {
    set $stapel_backend http://${BACKEND_UPSTREAM};
    location /api/ {
        proxy_pass $stapel_backend;
    }
}
"""
        compose = """\
services:
  backend:
    image: x
  nginx:
    image: nginx:alpine
    environment:
      - BACKEND_UPSTREAM=${BACKEND_UPSTREAM:-backend:8000}
"""
        proj = _make_project(tmp_path, conf, compose)
        assert eal.lint_eaddr002_003(proj) == []

    def test_envsubst_var_defaulting_to_host_docker_internal_is_env_boundary(self, tmp_path):
        conf = """\
server {
    location /rtc {
        proxy_pass http://${LIVEKIT_UPSTREAM};
    }
}
"""
        compose = """\
services:
  nginx:
    image: nginx:alpine
    environment:
      - LIVEKIT_UPSTREAM=${LIVEKIT_UPSTREAM:-host.docker.internal:7880}
"""
        proj = _make_project(tmp_path, conf, compose)
        findings = eal.lint_eaddr002_003(proj)
        assert "EADDR002" in _rules(findings)


class TestEADDR003ConnectTimeout:
    def test_missing_timeout_on_env_boundary_location_warns(self, tmp_path):
        proj = _make_project(tmp_path, ENV_BOUNDARY_CONF, COMPOSE_WITH_GATE)
        findings = eal.lint_eaddr002_003(proj)
        assert "EADDR003" in _rules(findings)
        finding = next(f for f in findings if f.rule == "EADDR003")
        assert finding.level == "warning"

    def test_explicit_short_timeout_clears_it(self, tmp_path):
        conf = ENV_BOUNDARY_CONF.replace(
            "proxy_pass http://172.17.0.1:7880;",
            "proxy_pass http://172.17.0.1:7880;\n        proxy_connect_timeout 5s;",
        )
        proj = _make_project(tmp_path, conf, COMPOSE_WITH_GATE)
        findings = eal.lint_eaddr002_003(proj)
        assert "EADDR003" not in _rules(findings)

    def test_timeout_over_10s_still_warns(self, tmp_path):
        conf = ENV_BOUNDARY_CONF.replace(
            "proxy_pass http://172.17.0.1:7880;",
            "proxy_pass http://172.17.0.1:7880;\n        proxy_connect_timeout 30s;",
        )
        proj = _make_project(tmp_path, conf, COMPOSE_WITH_GATE)
        findings = eal.lint_eaddr002_003(proj)
        assert "EADDR003" in _rules(findings)

    def test_non_boundary_location_never_warns(self, tmp_path):
        conf = """\
server {
    location /api/ {
        proxy_pass http://backend:8000;
    }
}
"""
        proj = _make_project(tmp_path, conf, COMPOSE_NO_GATE)
        assert eal.lint_eaddr002_003(proj) == []


class TestNoNginxConfProject:
    def test_project_without_nginx_confs_is_clean(self, tmp_path):
        assert eal.lint_eaddr002_003(tmp_path) == []


# ---------------------------------------------------------------------------
# lint_project / CLI
# ---------------------------------------------------------------------------


def test_lint_project_combines_both(tmp_path):
    _make_project(tmp_path, ENV_BOUNDARY_CONF, COMPOSE_NO_GATE)
    (tmp_path / ".env").write_text("X=172.17.0.1\n")
    findings = eal.lint_project(tmp_path)
    assert {"EADDR001", "EADDR002", "EADDR003"} <= _rules(findings)


def test_main_json_shape(tmp_path, capsys):
    _make_project(tmp_path, ENV_BOUNDARY_CONF, COMPOSE_NO_GATE)
    code = eal.main([str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["errors"] >= 1
    assert "findings" in payload


def test_main_clean_project_exit_zero(tmp_path, capsys):
    code = eal.main([str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "No env-address issues found" in out


def test_main_missing_directory_exit_2(tmp_path, capsys):
    code = eal.main([str(tmp_path / "nope")])
    err = capsys.readouterr().err
    assert code == 2
    assert "not a directory" in err


def test_main_fully_fixed_project_is_clean(tmp_path, capsys):
    """Gate mounted, connect_timeout set, AND the literal replaced by the
    envsubst var (so EADDR001's own file scan is clean too) -> exit 0."""
    conf = ENV_BOUNDARY_CONF.replace(
        "proxy_pass http://172.17.0.1:7880;",
        "proxy_pass http://${LIVEKIT_UPSTREAM};\n        proxy_connect_timeout 5s;",
    )
    compose = COMPOSE_WITH_GATE + "      - LIVEKIT_UPSTREAM=${LIVEKIT_UPSTREAM:-host.docker.internal:7880}\n"
    _make_project(tmp_path, conf, compose)
    assert eal.main([str(tmp_path)]) == 0
