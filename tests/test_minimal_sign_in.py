"""A freshly scaffolded minimal project must SIGN SOMEONE IN, not only boot.

Found by an end-to-end run (`--type minimal --modules auth --auth-model
public --posture prototype`): the email OTP verify answered 500 twice over.

1. ``requirements.txt`` declared no ``celery`` while stapel-auth's
   ``tasks.py`` imports it at module level and session issuance calls
   ``evaluate_login_notification.delay`` — ``ModuleNotFoundError`` on the
   first verify.
2. With celery installed, a request served on another thread (gunicorn
   ``--threads``, runserver) used Celery's process DEFAULT app instead of the
   project's: constructing the app makes it current only in the importing
   thread, and the Django fixup makes it the default only when nothing touched
   ``celery.current_app`` first. The default app is unconfigured
   (amqp://localhost) → ConnectionRefused → 500. ``config/celery.py`` now
   calls ``app.set_default()``.

The live test reproduces the bad ordering on purpose (touches
``celery.current_app`` before Django starts) and signs in from a worker
thread; it is red on 0.81.3's template (see the inversion below).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

import pytest

from stapel_tools.create_project import create_project

_HAVE = all(importlib.util.find_spec(m) for m in ("stapel_auth", "celery"))
_needs_auth = pytest.mark.skipif(
    not _HAVE, reason="stapel-auth/celery not importable (CI's test extra installs both)",
)

_SIGN_IN = r"""
import json, os, sys, threading
import celery
celery.current_app.conf.broker_url  # an earlier touch: the fallback app exists first
import django
django.setup()
from django.core.management import call_command
call_command("migrate", "--noinput", verbosity=0)
from django.test import Client
from django.urls import reverse
from stapel_auth.conf import auth_settings

out = {}
def sign_in():
    c = Client(raise_request_exception=False)
    email = "someone@mini.example"
    out["request"] = c.post(reverse("email_request"), {"email": email},
                            content_type="application/json").status_code
    r = c.post(reverse("email_verify"), {"email": email, "code": auth_settings.MOCK_OTP_CODE},
               content_type="application/json")
    out["verify"] = r.status_code
    out["body"] = r.content[:300].decode("utf-8", "replace")
t = threading.Thread(target=sign_in)
t.start(); t.join()
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    out = tmp_path_factory.mktemp("mini")
    create_project(
        name="mini", project_type="minimal", title="Mini", url="https://mini.example",
        company_name="X", company_email="x@mini.example", modules=["core", "auth"],
        output_dir=out, use_submodules=False, init_git=False,
        auth_model="public", auth_kinds="phone,email,password", posture="prototype",
    )
    return out / "mini"


def _sign_in(project) -> dict:
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"}
    env.pop("CELERY_BROKER_URL", None)
    result = subprocess.run(
        [sys.executable, "-c", _SIGN_IN], cwd=project, env=env,
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-4000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_requirements_declare_celery(project):
    lines = [ln.strip() for ln in (project / "requirements.txt").read_text().splitlines()]
    assert any(ln.startswith("celery") for ln in lines if not ln.startswith("#"))


def test_celery_app_is_the_default_for_every_thread(project):
    assert "app.set_default()" in (project / "config" / "celery.py").read_text()


@_needs_auth
def test_a_generated_minimal_project_signs_someone_in_from_a_worker_thread(project):
    got = _sign_in(project)
    assert got["request"] == 200, got
    assert got["verify"] == 200, got


@_needs_auth
def test_inversion_without_set_default_the_thread_gets_the_unconfigured_app(project, tmp_path):
    import shutil

    copy = tmp_path / "mini"
    shutil.copytree(project, copy)
    celery_py = copy / "config" / "celery.py"
    celery_py.write_text(celery_py.read_text().replace("app.set_default()", ""))
    assert _sign_in(copy)["verify"] == 500
