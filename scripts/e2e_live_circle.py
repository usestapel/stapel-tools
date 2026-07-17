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
  3. POST /<slug>/api/v1/email/request/  → the OTP code must appear in the
     LOG (mock provider — stapel_auth logs, never sends; this proves the
     §57 item-7 mock canon end to end);
  4. POST /<slug>/api/v1/email/verify/ with the code from the log →
     REGISTERED (registration completed);
  5. authenticated GET /<slug>/api/v1/me/ → 200 (login circle closed).

Exit 0 = the generated project drives; any assertion = non-zero.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
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

    slug = sys.argv[1] if len(sys.argv) > 1 else "e2e"
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

    # Capture the whole log stream — the mock OTP provider LOGS the code
    # (stapel_auth/otp/services.py), which is exactly what a developer reads.
    log_buffer = io.StringIO()
    handler = logging.StreamHandler(log_buffer)
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)

    client = Client()
    base = f"/{slug}/api/v1"

    print("e2e: request email OTP...")
    r = client.post(
        f"{base}/email/request/", {"email": email}, content_type="application/json"
    )
    assert r.status_code in (200, 201), (r.status_code, r.content[:500])

    log_text = log_buffer.getvalue()
    match = re.search(
        rf"[Vv]erification code for {re.escape(email)}[:\s]+(\w+)", log_text
    )
    assert match, (
        "OTP code not found in the log — the mock-provider canon is broken "
        f"(captured log follows):\n{log_text[-2000:]}"
    )
    code = match.group(1)
    print(f"e2e: OTP code found in log: {code}")

    print("e2e: verify email OTP (registration)...")
    r = client.post(
        f"{base}/email/verify/",
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
    r = client.get(f"{base}/me/", **kwargs)
    assert r.status_code == 200, (r.status_code, r.content[:500])
    me = json.loads(r.content)
    assert email in json.dumps(me), me
    print("e2e: OK — register -> OTP from log -> verify -> authenticated /me 200")
    return 0


if __name__ == "__main__":
    sys.exit(main())
