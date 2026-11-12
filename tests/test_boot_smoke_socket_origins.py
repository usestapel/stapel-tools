"""A fresh chat composite's boot-smoke is green on socket origins.

``make boot-smoke`` layers over base.py with no env, and base.py reads the
socket origin allowlist from the env, so a classified or social scaffold
failed its own gate on ``stapel_core.jwt.E001`` + ``stapel_chat.E014`` (an
empty allowlist is an error by design). The gate layer now states the local
stack's origins, as it already does FRONTEND_URL. The test runs the gate's
own tail over the namespace base.py leaves with no env.
"""
import re

import pytest

from stapel_tools.create_project import create_project


def _boot_smoke(tmp_path, modules):
    create_project(
        name="app", project_type="monolith", title="App", url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=modules,
        output_dir=tmp_path, use_submodules=False, init_git=False,
    )
    return (tmp_path / "app" / "svc-app" / "config" / "settings" / "boot_smoke.py").read_text()


def _run_tail(text, namespace):
    tail = text.split("from .base import *", 1)[1].split("\n", 1)[1]
    tail = re.sub(r"^DATABASES = .*$", "", tail, flags=re.M)
    exec(compile(tail, "boot_smoke.py", "exec"), namespace)
    return namespace


@pytest.mark.parametrize("composite", ["classified", "social"])
def test_chat_composite_gate_states_socket_origins(tmp_path, composite):
    text = _boot_smoke(tmp_path, [composite])
    # base.py with no env: core's empty allowlist, realtime copied from it.
    ns = _run_tail(text, {
        "STAPEL_WS_ALLOWED_ORIGINS": [],
        "STAPEL_REALTIME": {"URL_PREFIX": "ws", "ALLOWED_ORIGINS": []},
        "FRONTEND_URL": "",
    })
    assert ns["STAPEL_WS_ALLOWED_ORIGINS"] == ["http://localhost", "http://localhost:5173"]
    assert ns["STAPEL_REALTIME"]["ALLOWED_ORIGINS"] == ns["STAPEL_WS_ALLOWED_ORIGINS"]
    assert ns["STAPEL_REALTIME"]["URL_PREFIX"] == "ws"
    assert "jwt.E001" not in "".join(ns.get("SILENCED_SYSTEM_CHECKS", []))
    assert "E014" not in "".join(ns.get("SILENCED_SYSTEM_CHECKS", []))


def test_env_origins_win(tmp_path):
    text = _boot_smoke(tmp_path, ["classified"])
    ns = _run_tail(text, {
        "STAPEL_WS_ALLOWED_ORIGINS": ["https://x.dev"],
        "STAPEL_REALTIME": {"ALLOWED_ORIGINS": ["https://x.dev"]},
        "FRONTEND_URL": "",
    })
    assert ns["STAPEL_WS_ALLOWED_ORIGINS"] == ["https://x.dev"]
    assert ns["STAPEL_REALTIME"]["ALLOWED_ORIGINS"] == ["https://x.dev"]


def test_no_socket_selection_gets_no_origins(tmp_path):
    assert "STAPEL_WS_ALLOWED_ORIGINS" not in _boot_smoke(tmp_path, ["shop"])
