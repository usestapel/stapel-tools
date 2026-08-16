#!/usr/bin/env python
"""E2E live circle over a freshly generated monolith (owner directive: the
"it drives out of the box" gate — every release of stapel-tools must prove a
generated project actually works, not just lints).

Run from the generated project's SERVICE directory (svc-<slug>/), with the
project's Python deps importable and a reachable Postgres:

    python scripts/e2e_live_circle.py <slug>

The circle (all live, Django test client over the real wired app):
  1. loads ../.env.local (the committed local env — the same file a
     developer's `docker compose up` uses) + DJANGO_SETTINGS_MODULE=
     config.settings.dev (mock providers on);
  2. migrate;
  3. POST <auth email_request> → the OTP code must appear in the LOG (mock
     provider — stapel_auth logs, never sends; this proves the §57 item-7
     mock canon end to end);
  4. POST <auth email_verify> with the code from the log → REGISTERED
     (registration completed);
  5. authenticated GET <auth me> → 200 (login circle closed).

Every URL above is resolved with ``reverse()`` against the generated
project's own urlconf rather than assembled from the slug. That is not
tidiness: this script used to build ``/<slug>/api/v1/...`` by hand, and when
commit 03163f6 fixed the monolith mismount (auth mounts at ``auth/api/``,
its own key, not under the hosting service's slug) the gate went on asking
for a path that no longer existed and failed on 404 for five days. A live
gate that hardcodes the thing it is meant to be testing cannot report the
truth about it.

Exit 0 = the generated project drives; any assertion = non-zero.
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
from pathlib import Path

# Running this script by absolute path puts the SCRIPT's dir on sys.path,
# not the cwd — but `config`/`apps` live in the cwd (the service dir).
sys.path.insert(0, os.getcwd())


def load_env_local(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        # Explicit CI/DB overrides win over the committed file.
        os.environ.setdefault(key.strip(), value.strip())


def find_key(obj, names: tuple[str, ...]):
    """Depth-first search for the first value under any of *names*."""
    if isinstance(obj, dict):
        for name in names:
            if name in obj and isinstance(obj[name], str) and obj[name]:
                return obj[name]
        for value in obj.values():
            found = find_key(value, names)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_key(value, names)
            if found:
                return found
    return None


def main() -> int:
    import time

    # The slug is no longer used to build URLs (see the module docstring —
    # they are reversed against the real urlconf now); it stays accepted so
    # existing callers and CI invocations keep working unchanged.
    _slug = sys.argv[1] if len(sys.argv) > 1 else "e2e"
    # Unique per run — rerunnable against a non-fresh DB (the OTP request
    # rate limit is per-email/30s; a crashed previous run must not 429 us).
    email = f"olga+{int(time.time())}@example.com"

    load_env_local(Path("..") / ".env.local")
    os.environ["DJANGO_SETTINGS_MODULE"] = os.environ.get(
        "E2E_SETTINGS", "config.settings.dev"
    )
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

    import django

    django.setup()

    from django.core.management import call_command
    from django.test import Client

    print("e2e: migrate...")
    call_command("migrate", "--noinput", verbosity=0)

    # Capture the whole log stream — not for the code (see below), but to prove
    # the mock branch is the one that ran.
    log_buffer = io.StringIO()
    handler = logging.StreamHandler(log_buffer)
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)

    client = Client()

    # Resolved, never assembled — see the module docstring. A NoReverseMatch
    # here means auth is not wired into the generated project at all, which
    # is itself the finding this gate exists to surface.
    from django.urls import reverse

    def auth_url(route: str) -> str:
        return reverse(route)

    print("e2e: request email OTP...")
    r = client.post(
        auth_url("email_request"), {"email": email}, content_type="application/json"
    )
    assert r.status_code in (200, 201), (r.status_code, r.content[:500])

    # The code comes from the setting, not from the log. In mock mode it is
    # MOCK_OTP_CODE by construction, and stapel-auth stopped printing it with
    # 0.22.1 — logging a live credential to say what the setting already says.
    # Scraping it back out of the log would ask the library to keep doing that.
    #
    # What the log is still good for is proving the mock branch ran at all: a
    # deployment with mock mode off would otherwise sail past this gate on a
    # code that happens to match nothing.
    log_text = log_buffer.getvalue()
    assert "Mock OTP mode" in log_text, (
        "the mock OTP branch did not run — this gate verifies with "
        "MOCK_OTP_CODE, which only means anything in mock mode "
        f"(captured log follows):\n{log_text[-2000:]}"
    )
    from stapel_auth.conf import auth_settings

    code = auth_settings.MOCK_OTP_CODE
    print(f"e2e: OTP code from MOCK_OTP_CODE: {code}")

    print("e2e: verify email OTP (registration)...")
    r = client.post(
        auth_url("email_verify"),
        {"email": email, "code": code},
        content_type="application/json",
    )
    assert r.status_code in (200, 201), (r.status_code, r.content[:500])
    body = json.loads(r.content)
    status = find_key(body, ("status",))
    print(f"e2e: verify response status field: {status!r}")
    assert status in ("REGISTERED", "LOGGED_IN", None) or "REGISTER" in str(status), body

    print("e2e: authenticated /me/ (login circle)...")
    token = find_key(body, ("access", "access_token", "token"))
    kwargs = {}
    if token:
        kwargs["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    r = client.get(auth_url("me"), **kwargs)
    assert r.status_code == 200, (r.status_code, r.content[:500])
    me = json.loads(r.content)
    assert email in json.dumps(me), me
    print("e2e: OK — register -> OTP from log -> verify -> authenticated /me 200")
    return 0


if __name__ == "__main__":
    sys.exit(main())
