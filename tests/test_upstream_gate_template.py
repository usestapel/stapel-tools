"""The frozen upstream-gate v2 script (docs/pending/env-address-class-v2.md
§3.4) — cheap regression checks on top of the LIVE container verification
that produced it (this repo cannot spin up docker in CI, so these checks are
static: syntax, and the invariants the design depends on)."""
import shutil
import subprocess

import pytest

from stapel_tools._upstream_gate_template import UPSTREAM_GATE_SH


def test_is_valid_posix_sh():
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("no /bin/sh on this runner")
    proc = subprocess.run(
        [sh, "-n", "/dev/stdin"], input=UPSTREAM_GATE_SH, text=True, capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_shebang():
    assert UPSTREAM_GATE_SH.startswith("#!/bin/sh\n")


def test_startup_always_exits_zero_on_the_success_path():
    """The central v2 invariant, textually: the final fallthrough exit is 0
    (nginx always starts), reachable only for environment conditions — every
    config-error branch above it returns/exits before reaching it."""
    assert "\nexit 0\n" in UPSTREAM_GATE_SH
    assert UPSTREAM_GATE_SH.rstrip().endswith("exit 0")


def test_config_errors_exit_nonzero():
    assert 'log "CONFIG ERROR: UPSTREAM_GATE_TARGETS is empty' in UPSTREAM_GATE_SH
    assert "exit 1" in UPSTREAM_GATE_SH


def test_no_warn_mode_lever():
    """v2 kills UPSTREAM_GATE_MODE (env-address-class-v2.md §2/§6) —
    degradation is the only semantics, no enforce/warn switch survives."""
    assert "UPSTREAM_GATE_MODE" not in UPSTREAM_GATE_SH


def test_host_gateway_sentinel_resolved_from_proc_net_route():
    """@host-gateway is computed from THIS container's own routing table —
    never delegated to `host.docker.internal`/dockerd's --host-gateway-ip
    (a documentation-only mention of that name, explaining why it was
    rejected, is fine; it must never appear as something the script
    actually resolves through)."""
    assert "@host-gateway" in UPSTREAM_GATE_SH
    assert "/proc/net/route" in UPSTREAM_GATE_SH
    assert 'case "$1" in\n        @host-gateway)' in UPSTREAM_GATE_SH
    for line in UPSTREAM_GATE_SH.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "host.docker.internal" not in line, line


def test_four_triage_branches_present():
    assert "is not in /etc/hosts and no DNS answer" in UPSTREAM_GATE_SH
    assert "is the gateway of a DIFFERENT docker network" in UPSTREAM_GATE_SH
    assert "packets are being dropped silently" in UPSTREAM_GATE_SH
    assert "ufw allow from 172.16.0.0/12 to any port" in UPSTREAM_GATE_SH
    assert "CONNECTION REFUSED" in UPSTREAM_GATE_SH


def test_once_mode_self_heals_via_reload():
    assert "nginx -s reload" in UPSTREAM_GATE_SH
    assert 'ONCE=1' in UPSTREAM_GATE_SH


def test_degraded_include_serves_fast_503_with_marker_header():
    assert "return 503" in UPSTREAM_GATE_SH
    assert "X-Stapel-Degraded" in UPSTREAM_GATE_SH
