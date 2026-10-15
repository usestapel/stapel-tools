"""--auth-model / --auth-kinds / --posture: the auth composition is a scaffold
parameter, rendered as the deployment posture plus the login axes."""
import json
import subprocess
import sys

import pytest

from stapel_tools._auth_model import (
    AUTH_KINDS,
    POSTURE_FLOORS,
    merge_auth_axes,
    render_base_posture,
    render_local_posture,
    resolve,
)
from stapel_tools.create_project import STAPEL_LIBS, create_project

pytest.importorskip("stapel_core")


def _create(tmp_path, *, project_type="monolith", modules=("core",), **kw):
    create_project(
        name="app", project_type=project_type, title="App",
        url="https://app.example", company_name="Acme",
        company_email="hi@app.example", modules=list(modules),
        output_dir=tmp_path, use_submodules=False, init_git=False, **kw,
    )
    return tmp_path / "app"


_RUNNER = """
import json, sys
from django.conf import settings
settings.configure()
ns = {}
for fragment in json.load(sys.stdin):
    exec(compile(fragment, "<settings>", "exec"), ns)
print(json.dumps({k: ns[k] for k in ("STAPEL_POSTURE", "STAPEL_AUTH", "STAPEL_WORKSPACES") if k in ns}))
"""


def _exec_settings(*fragments):
    """Run settings fragments in order against stapel-core's real presets
    (a subprocess: the presets import needs configured Django settings)."""
    out = subprocess.run(
        [sys.executable, "-c", _RUNNER], input=json.dumps(fragments),
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


class TestResolve:
    def test_auth_selected_without_model_is_public_with_library_logins(self):
        comp = resolve(None, auth_selected=True)
        assert comp.model == "public" and comp.kinds is None
        assert comp.auth_axes() == {}

    def test_nothing_selected_is_no_composition(self):
        assert resolve(None, auth_selected=False) is None

    def test_internal_defaults_to_password_and_email(self):
        comp = resolve("internal", auth_selected=False)
        axes = comp.auth_axes()
        assert axes["AUTH_PASSWORD_LOGIN"] is True
        assert axes["AUTH_EMAIL_LOGIN"] is True
        assert axes["AUTH_PHONE_LOGIN"] is False
        assert axes["AUTH_ANONYMOUS"] is False

    def test_kinds_state_every_login_axis(self):
        comp = resolve("public", "phone,oauth", auth_selected=True)
        axes = comp.auth_axes()
        assert set(AUTH_KINDS.values()) <= set(axes)
        assert [k for k, v in axes.items() if v] == ["AUTH_PHONE_LOGIN", "AUTH_OAUTH_LOGIN"]

    def test_kinds_never_touch_registration_doors(self):
        comp = resolve("public", "password", auth_selected=True)
        assert not any(k.endswith("_REGISTRATION") for k in comp.auth_axes())

    @pytest.mark.parametrize("args", [
        ("bogus", None, None),
        ("public", "fax", None),
        ("anonymous", "phone", None),
        ("public", None, "beta"),
        (None, "phone", None),
    ])
    def test_invalid_values_refused(self, args):
        with pytest.raises(ValueError):
            resolve(*args, auth_selected=True)

    def test_explicit_module_config_wins_over_kinds(self):
        comp = resolve("public", "phone", auth_selected=True)
        merged = merge_auth_axes({"auth": {"AUTH_EMAIL_LOGIN": True}}, comp)
        assert merged["auth"]["AUTH_EMAIL_LOGIN"] is True
        assert merged["auth"]["AUTH_PHONE_LOGIN"] is True


class TestPostureSemantics:
    """The rendered fragments, executed against stapel-core's real presets."""

    def test_live_base_pins_mocks_off_and_local_drops_them(self):
        comp = resolve("public", "phone,email", auth_selected=True)
        base = _exec_settings(
            'STAPEL_AUTH = {"AUTH_PHONE_LOGIN": True}', render_base_posture(comp),
        )
        assert base["STAPEL_POSTURE"]["OPTIONS"]["stage"] == "live"
        assert base["STAPEL_AUTH"]["USE_MOCK_SMS_OTP"] is False
        assert base["STAPEL_AUTH"]["AUTH_PHONE_LOGIN"] is True
        local = _exec_settings(
            'STAPEL_AUTH = {"AUTH_PHONE_LOGIN": True}',
            render_base_posture(comp), render_local_posture(comp),
        )
        assert local["STAPEL_POSTURE"]["OPTIONS"]["stage"] == "prototype"
        assert "USE_MOCK_SMS_OTP" not in local["STAPEL_AUTH"]
        assert "USE_MOCK_EMAIL_OTP" not in local["STAPEL_AUTH"]
        assert local["STAPEL_AUTH"]["AUTH_PHONE_LOGIN"] is True

    def test_internal_is_private_space_invite_only(self):
        comp = resolve("internal", auth_selected=False)
        ns = _exec_settings(render_base_posture(comp))
        assert ns["STAPEL_POSTURE"] == {
            "PRESET": "private_space",
            "OPTIONS": {"door": "invite_only", "stage": "live"},
        }
        assert ns["STAPEL_AUTH"]["AUTH_EMAIL_REGISTRATION"] is False
        assert ns["STAPEL_WORKSPACES"]["STREET_LANDING_MODE"] == "none"

    def test_prototype_posture_needs_no_local_override(self):
        comp = resolve("public", stage="prototype", auth_selected=True)
        assert render_local_posture(comp) == ""
        ns = _exec_settings(render_base_posture(comp))
        assert "USE_MOCK_SMS_OTP" not in ns["STAPEL_AUTH"]

    def test_none_and_anonymous_declare_no_posture(self):
        for model in ("none", "anonymous"):
            comp = resolve(model, auth_selected=True)
            assert render_base_posture(comp) == ""
            assert render_local_posture(comp) == ""


class TestGenerated:
    def test_monolith_public_kinds_render_axes_posture_and_login_route(self, tmp_path):
        proj = _create(
            tmp_path, modules=("core",), auth_model="public",
            auth_kinds="phone,email", want_landing=True,
        )
        settings_dir = proj / "svc-app" / "config" / "settings"
        base = (settings_dir / "base.py").read_text()
        dev = (settings_dir / "dev.py").read_text()
        assert '"stapel_auth"' in base  # the model brought the module
        assert '"AUTH_PHONE_LOGIN": True' in base
        assert '"AUTH_QR_LOGIN": False' in base
        assert 'public_space(stage="live")' in base
        assert 'public_space(stage="prototype")' in dev
        assert "USE_MOCK" not in base.replace('"USE_MOCK_SMS_OTP", "USE_MOCK_EMAIL_OTP"', "")
        assert (proj / "frontend" / "src" / "ProtectedRoute.tsx").exists()
        requirements = (proj / "svc-app" / "requirements.txt").read_text()
        assert "stapel-auth[phone]" in requirements

    def test_monolith_anonymous_has_no_login_route(self, tmp_path):
        proj = _create(tmp_path, modules=("core",), auth_model="anonymous")
        base = (proj / "svc-app" / "config" / "settings" / "base.py").read_text()
        assert '"AUTH_ANONYMOUS": True' in base
        assert "STAPEL_POSTURE" not in base
        assert not (proj / "frontend" / "src" / "ProtectedRoute.tsx").exists()

    def test_auth_flag_contradicting_model_is_refused(self, tmp_path):
        with pytest.raises(SystemExit):
            _create(tmp_path, modules=("core",), auth_model="none", want_auth=True)

    def test_minimal_gets_posture_live_only_in_prod(self, tmp_path):
        proj = _create(tmp_path, project_type="minimal", modules=("core", "auth"))
        settings = (proj / "config" / "settings.py").read_text()
        assert 'public_space(stage="live" if _IS_PROD else "prototype")' in settings
        assert "USE_MOCK_SMS_OTP\": True" not in settings
        compile(settings, "settings.py", "exec")

    def test_minimal_refuses_frontend_flags(self, tmp_path):
        with pytest.raises(SystemExit):
            _create(tmp_path, project_type="minimal", modules=("core",), want_landing=True)


def test_registry_pins_carry_the_posture_mechanism():
    for key, floor in POSTURE_FLOORS.items():
        pin = tuple(int(p) for p in STAPEL_LIBS[key]["pin"].split("."))
        assert pin >= floor, f"{key} pin {pin} predates the posture stage {floor}"
        assert not STAPEL_LIBS[key].get("ahead_of_pypi")


def test_monolith_without_submodules_installs_every_library_from_pypi(tmp_path):
    proj = _create(tmp_path, modules=("core", "workspaces"), auth_model="public")
    requirements = (proj / "svc-app" / "requirements.txt").read_text()
    for key in ("core", "auth", "workspaces"):
        pin = STAPEL_LIBS[key]["pin"]
        assert f"stapel-{key}" in requirements and f">={pin}" in requirements
    dockerfile = (proj / "svc-app" / "Dockerfile").read_text()
    assert "COPY stapel_core" not in dockerfile


def test_workspaces_without_billing_declares_unbilled(tmp_path):
    proj = _create(tmp_path, modules=("core", "workspaces"))
    base = (proj / "svc-app" / "config" / "settings" / "base.py").read_text()
    assert '"ALLOW_UNBILLED": True' in base


def test_workspaces_with_billing_stays_billed(tmp_path):
    proj = _create(tmp_path, modules=("core", "workspaces", "billing"))
    base = (proj / "svc-app" / "config" / "settings" / "base.py").read_text()
    assert "ALLOW_UNBILLED" not in base
