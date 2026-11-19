"""The generated service's container healthcheck must pass under production
settings, not only under the dev tier.

The probe used to ask ``http://localhost:8000/<svc>/api/health/``. A real
stand's ``ALLOWED_HOSTS`` is its domain, so Django answered 400
(DisallowedHost); with the host right, ``SECURE_SSL_REDIRECT`` would have
answered 301 to ``https://``. Either way the container read unhealthy on
every production-like stack and healthy on every laptop.

These tests read the probe OUT OF the generated compose file (URL and Host
header) and push exactly that request through the generated prod tier's host
validation and ``SecurityMiddleware``, fed the project's own generated
``.env``. The old probe shape is run through the same harness as the
inversion: it must fail there. The full stack is proven with Docker in the
0.81.3 release notes; this is the part CI can hold.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest

from stapel_tools.create_project import create_project

_PROBE_RE = re.compile(
    r'urlopen\(u\.Request\(\\"(?P<url>http://[^\\]+)\\", headers=\{\\"Host\\": \\"(?P<host>[^\\]+)\\"\}\)'
)

_HARNESS = r"""
import json, sys
import django
django.setup()
from django.core.exceptions import DisallowedHost
from django.http import HttpResponse
from django.middleware.security import SecurityMiddleware
from django.test import RequestFactory

out = []
for host, path in json.loads(sys.argv[1]):
    request = RequestFactory().get(path, HTTP_HOST=host)
    try:
        request.get_host()
    except DisallowedHost:
        out.append(400)
        continue
    response = SecurityMiddleware(lambda r: HttpResponse("ok"))(request)
    out.append(response.status_code)
print(json.dumps(out))
"""


def _read_env(path):
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    out = tmp_path_factory.mktemp("probe")
    create_project(
        name="app", project_type="monolith", title="app",
        url="https://probe.example", company_name="X",
        company_email="x@probe.example", modules=["core"],
        output_dir=out, use_submodules=False, init_git=False,
    )
    return out / "app"


def _probe(project):
    text = (project / "svc-app.yml").read_text(encoding="utf-8")
    match = _PROBE_RE.search(text)
    assert match, "healthcheck probe not found in svc-app.yml:\n" + text
    url = match.group("url")
    path = "/" + url.split("/", 3)[3]
    return match.group("host"), path, url


def _statuses(project, requests):
    env = {
        **os.environ,
        **_read_env(project / ".env"),
        "DJANGO_SETTINGS_MODULE": "config.settings.prod",
    }
    result = subprocess.run(
        [sys.executable, "-c", _HARNESS, json.dumps(requests)],
        cwd=project / "svc-app", env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_probe_targets_ipv4_loopback_and_the_service_name(project):
    host, path, url = _probe(project)
    assert url.startswith("http://127.0.0.1:8000/")
    assert host == "svc-app"
    assert path == "/app/api/health/"


def test_prod_tier_has_a_production_host_list(project):
    """The premise: the generated .env does NOT allow localhost."""
    env = _read_env(project / ".env")
    assert env["ALLOWED_HOSTS"] == "probe.example"


def test_the_probe_passes_prod_host_validation_and_the_https_redirect(project):
    host, path, _ = _probe(project)
    assert _statuses(project, [[host, path]]) == [200]


def test_inversion_the_old_probe_fails_under_prod(project):
    """The pre-0.81.3 shape: Host localhost → 400 under the prod tier."""
    _, path, _ = _probe(project)
    assert _statuses(project, [["localhost", path]]) == [400]


def test_inversion_the_exemption_is_scoped_to_health(project):
    """Only the probe surface skips the redirect; the API still upgrades."""
    host, _, _ = _probe(project)
    assert _statuses(project, [[host, "/app/api/v1/anything/"]]) == [301]
