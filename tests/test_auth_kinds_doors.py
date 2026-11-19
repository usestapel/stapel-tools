"""``--auth-kinds`` closes the channels it leaves out, on both axes.

stapel-auth keeps a channel open while its ``AUTH_<KIND>_LOGIN`` OR its
``AUTH_<KIND>_REGISTRATION`` is on. Listing kinds used to switch only the
login axes, so a ``--auth-kinds phone`` project reported ``login.email=false``
next to ``registration.email=true`` and a stranger could still register by
email (stapel-auth 0.53 names that pair ``stapel_auth.W015``). The scaffold now
shuts the registration door of every unlisted kind; the public posture reports
that narrowing as ``stapel_core.presets.W001`` (core >= 0.98.1), never E001;
no check at ERROR level is left on a fresh scaffold.

The live test scaffolds the project and asks it: the capabilities, the system
checks, and the excluded doors themselves.
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
out["errors"] = sorted(f.id for f in found if f.is_serious())
out["w001_keys"] = sorted(
    f.msg.split("['", 1)[1].split("']", 1)[0]
    for f in found if f.id == "stapel_core.presets.W001"
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


def test_unlisted_kinds_have_their_registration_door_rendered_shut(project):
    settings = (project / "config" / "settings.py").read_text()
    for kind in ("EMAIL", "OAUTH", "SSO", "PASSWORD"):
        assert f'"AUTH_{kind}_REGISTRATION": False' in settings, kind
    assert "AUTH_PHONE_REGISTRATION" not in settings  # the listed kind keeps the posture's door


@_needs_auth
def test_an_excluded_kind_is_closed_and_the_listed_one_open(project):
    got = _probe(project)
    assert "stapel_auth.W015" not in got["checks"], got["checks"]
    assert "stapel_core.presets.E001" not in got["checks"], got["checks"]
    assert got["errors"] == [], got["errors"]  # nothing at ERROR: migrate/boot proceed
    # The narrowed doors are visible as core's W001, one per door the
    # public posture opens and this project shut (password is shut by both).
    assert got["w001_keys"] == [
        "AUTH_EMAIL_REGISTRATION", "AUTH_OAUTH_REGISTRATION", "AUTH_SSO_REGISTRATION",
    ], got["w001_keys"]
    for kind in ("email", "password", "sso"):
        assert got["login"][kind] is False and got["registration"][kind] is False, (kind, got)
    assert got["login"]["phone"] is True and got["registration"]["phone"] is True
    assert got["phone_request"] == 200, got
    assert got["email_request"] == 403, got
    assert got["password_register"] == 403, got


@_needs_auth
def test_inversion_registration_left_on_reopens_the_excluded_door(project, tmp_path):
    import shutil

    copy = tmp_path / "doors"
    shutil.copytree(project, copy)
    settings = copy / "config" / "settings.py"
    settings.write_text(settings.read_text().replace(
        '    "AUTH_EMAIL_REGISTRATION": False,\n', ""))
    got = _probe(copy)
    assert "stapel_auth.W015" in got["checks"]
    assert got["registration"]["email"] is True
    assert got["email_request"] == 200, got


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
