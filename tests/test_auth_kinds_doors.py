"""``--auth-kinds`` switches login axes only; the registration doors of the
kinds it leaves out stay the posture's.

stapel-auth keeps a channel open while its ``AUTH_<KIND>_LOGIN`` OR its
``AUTH_<KIND>_REGISTRATION`` is on, and since 0.53 it names that half-closed
pair ``stapel_auth.W015``. Shutting the excluded doors from the scaffold is not
possible on stapel-core 0.98.0: ``stapel_core.presets.E001`` reports any shut
door ``public_space`` opens as a security override (a deploy blocker). Until
core distinguishes narrowing from weakening, a fresh ``--auth-kinds phone``
project says so out loud: W015 per excluded kind with a registration axis,
no E001, and the excluded email door still answers. These tests pin exactly
that state so nobody mistakes it for closed.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

import pytest

from stapel_tools.create_project import _is_celery_requirement, _setup_pip_deps, create_project

_HAVE = all(importlib.util.find_spec(m) for m in ("stapel_auth", "celery"))
_needs_auth = pytest.mark.skipif(
    not _HAVE, reason="stapel-auth/celery not importable (CI's test extra installs both)",
)

_PROBE = r"""
import json
import django
django.setup()
from django.core import checks
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from stapel_auth.fictional import fictional_phone

call_command("migrate", "--noinput", verbosity=0, skip_checks=True)
found = checks.run_checks()
out = {"checks": sorted({f.id for f in found})}
out["w015"] = sorted(
    f.msg.split("AUTH_", 1)[1].split("_LOGIN", 1)[0].lower()
    for f in found if f.id == "stapel_auth.W015"
)
c = Client(raise_request_exception=False)
cap = c.get(reverse("capabilities")).json()
out["login"], out["registration"] = cap["login"], cap["registration"]
def post(name, payload):
    return c.post(reverse(name), payload, content_type="application/json").status_code
out["phone_request"] = post("phone_request", {"phone": fictional_phone()})
out["email_request"] = post("email_request", {"email": "stranger@doors.example"})
out["password_register"] = post(
    "password_register", {"email": "stranger@doors.example", "password": "Str0ng-pass-phrase!"})
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    out = tmp_path_factory.mktemp("doors")
    create_project(
        name="doors", project_type="minimal", title="Doors", url="https://doors.example",
        company_name="X", company_email="x@doors.example", modules=["core", "auth"],
        output_dir=out, use_submodules=False, init_git=False,
        auth_model="public", auth_kinds="phone", posture="prototype",
    )
    return out / "doors"


def _probe(project) -> dict:
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings",
           "PYTHONPATH": str(project)}
    env.pop("CELERY_BROKER_URL", None)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=project, env=env,
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-4000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_no_registration_axis_is_rendered(project):
    settings = (project / "config" / "settings.py").read_text()
    assert "_REGISTRATION" not in settings.split("# ─── Deployment posture")[0]


@_needs_auth
def test_excluded_kinds_are_reported_by_w015_not_blocked(project):
    got = _probe(project)
    assert "stapel_core.presets.E001" not in got["checks"], got["checks"]
    assert got["w015"] == ["email", "oauth", "sso"], got["w015"]
    # login off, registration on: the half-closed state W015 describes
    assert got["login"]["email"] is False and got["registration"]["email"] is True
    assert got["login"]["phone"] is True and got["registration"]["phone"] is True
    assert got["phone_request"] == 200, got
    assert got["email_request"] == 200, got  # the excluded door still answers
    assert got["password_register"] == 403, got  # shut by the posture itself


@pytest.mark.parametrize(("line", "expected"), [
    ("celery>=5.3", True),
    ("celery[redis]>=5.3", True),
    ("Celery", True),
    ("# celery>=5.3", False),
    ("celery-singleton>=0.3", False),
    ("django-celery-beat>=2.5", False),
])
def test_celery_requirement_line(line, expected):
    assert _is_celery_requirement(line) is expected


def test_no_celery_no_auth_celery_extra(tmp_path):
    (tmp_path / "requirements.txt").write_text("Django>=5.1\n")
    _setup_pip_deps(tmp_path, ["core", "auth"])
    auth = [ln for ln in (tmp_path / "requirements.txt").read_text().splitlines()
            if ln.startswith("stapel-auth")]
    assert len(auth) == 1 and "[" not in auth[0], auth


def test_celery_adds_the_extra_only_to_libraries_that_declare_one(tmp_path):
    (tmp_path / "requirements.txt").write_text("celery>=5.3\n")
    _setup_pip_deps(tmp_path, ["core", "auth"], {"auth": {"AUTH_OAUTH_LOGIN": True}})
    lines = (tmp_path / "requirements.txt").read_text().splitlines()
    assert any(ln.startswith("stapel-auth[celery,oauth]>=") for ln in lines), lines
    assert any(ln.startswith("stapel-core>=") for ln in lines), lines
