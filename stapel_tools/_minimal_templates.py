"""Templates for stapel-create-project --type minimal (no Docker, SQLite)."""

MINIMAL_MANAGE = """\
#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
"""

MINIMAL_SETTINGS = """\
\"\"\"Django settings for {{name}} (minimal / SQLite).\"\"\"
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# NOT FOR PRODUCTION BY DEFAULT: this preset (SQLite, no Docker) is meant for
# local development. Nothing stops deploying it as-is, so DJANGO_ENV=prod
# below turns on the same hardening the monolith/microservices presets ship
# (security-programme.md SEC-4/B8) — set it explicitly if you deploy this.
DJANGO_ENV = os.getenv("DJANGO_ENV", "local")
_IS_PROD = DJANGO_ENV == "prod"

# stapel-create-project (SEC-6) writes a freshly generated random value into
# .env at project creation; this fallback only matters for a hand-copied
# .env.example, and DJANGO_ENV=prod refuses to boot on it (see guard_secret
# below) instead of silently running with a guessable key.
SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY and not _IS_PROD:
    SECRET_KEY = "django-insecure-{{slug}}-dev-only"

DEBUG = os.getenv("DEBUG", "false" if _IS_PROD else "true").lower() in ("true", "1", "yes")
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "" if _IS_PROD else "*").split(",")

if _IS_PROD:
    from stapel_core.django.prodguard import guard_secret

    guard_secret("SECRET_KEY", SECRET_KEY)

    DEBUG = False
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "True").lower() == "true"
    # HSTS ramp — conservative default, no subdomains/preload; raise once
    # HTTPS is verified stable (see the monolith/microservices prod.py
    # template comment for the full rationale; security-programme.md SEC-4).
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "86400"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
    SECURE_CONTENT_TYPE_NOSNIFF = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    # Transactional outbox — every stapel_core.comm.emit() writes through it
    # (delivery guarantee + test harness, system-design §7.21).
    "stapel_core.django.outbox",{{STAPEL_APPS}}
    "apps.{{MODULE}}",
]{{STAPEL_MODULE_CONFIG}}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if _IS_PROD:
    # Content-Security-Policy — report-only by default (Django's native CSP
    # middleware, Django>=6; this preset already pins django>=6,<7). Strict
    # enforcement can break django-admin's inline scripts/styles without
    # per-project tuning, so this ships observing violations rather than
    # blocking them — see the monolith/microservices prod.py template for
    # the full rationale (security-programme.md §8.4, open question).
    try:
        from django.utils.csp import CSP

        MIDDLEWARE = MIDDLEWARE + ["django.middleware.csp.ContentSecurityPolicyMiddleware"]
        SECURE_CSP_REPORT_ONLY = {
            "default-src": [CSP.SELF],
            "script-src": [CSP.SELF],
            "style-src": [CSP.SELF, CSP.UNSAFE_INLINE],
            "img-src": [CSP.SELF, "data:"],
            "font-src": [CSP.SELF],
        }
    except ImportError:
        pass

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

STATIC_URL = "/static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "{{title}} API",
    "DESCRIPTION": "{{title}} — built with Stapel",
    "VERSION": "1.0.0",
}

USE_TZ = True

# Inter-service comm + transactional outbox (docs: module-communication.md,
# system-design §7.21). Every emit() writes an outbox row in the caller's
# transaction; delivery runs after commit. Kept in-process for the minimal
# preset — same at-least-once guarantee, no broker.
STAPEL_COMM = {
    "OUTBOX_ENABLED": True,
    "ACTION_TRANSPORT": "inprocess",
}

# File mailtrap (dev/preview + async-consumer tests): outbound mail is written
# to var/mailtrap/ as JSON instead of hitting SMTP, so it stays inspectable.
MAILTRAP_DIR = BASE_DIR / "var" / "mailtrap"
EMAIL_BACKEND = "tests.harness.mailtrap.FileMailtrapBackend"
"""

MINIMAL_BOOT_SMOKE_SETTINGS = """\
\"\"\"Boot-smoke settings (R3/§44) — `make boot-smoke`, part of `make controls`.

Layers over this project's own config/settings.py and swaps DATABASES for
Django's `dummy` backend, which raises loudly on the first real connection
attempt. `manage.py check` under this module exercises exactly the phase a
generated project's client would hit on first boot — INSTALLED_APPS import
plus every AppConfig.ready() — with no live database reachable, proving that
phase never needs one. It would have caught the class of bug where a stray
ready()/import-time DB query (or a harness/env leak clobbering this project's
DJANGO_SETTINGS_MODULE with someone else's settings) turned "generate a
project" into a client-visible 500 before a single test ran.

Never point real traffic at this module — it has no usable database.
\"\"\"
from .settings import *  # noqa: F401,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}
"""

MINIMAL_URLS = """\
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/", include("apps.{{MODULE}}.urls")),{{STAPEL_URL_INCLUDES}}
]
"""

MINIMAL_REQUIREMENTS = """\
django>=6,<7
djangorestframework>=3.14,<4
drf-spectacular>=0.27

# Controls (make controls): lint + the outbox/mailtrap test harness.
pytest>=7.4
pytest-django>=4.7
ruff>=0.4

# Stapel core (choose one):
# Option A — pip from GitHub:
# stapel_core @ git+https://github.com/usestapel/stapel-core.git
# Option B — local submodule (add to sys.path in settings):
# already on PYTHONPATH if submodule is at ./stapel_core
"""

MINIMAL_GITIGNORE = """\
.env
*.pyc
__pycache__/
.venv/
venv/
*.egg-info/
db.sqlite3
media/
staticfiles/
var/mailtrap/*.json
# build artifact of `make release-manifest` — lives in the image/registry,
# never in the checkout
release.json
.DS_Store
"""

MINIMAL_ENV_EXAMPLE = """\
# .env is created automatically from this file at project creation, with a
# freshly generated SECRET_KEY (security-programme.md SEC-6). This file
# (.env.example) keeps a placeholder and is safe to commit; .env is
# gitignored.
SECRET_KEY=change_me_to_a_long_random_string

# local (default) — DEBUG on, ALLOWED_HOSTS=* — this preset is NOT FOR
# PRODUCTION by default (SQLite, no Docker). Deploying it is possible but
# not the intended path; if you do, set DJANGO_ENV=prod: DEBUG turns off,
# SECURE_SSL_REDIRECT/HSTS/CSP-report-only turn on, and SECRET_KEY is
# guarded against placeholders/short values (see config/settings.py).
DJANGO_ENV=local
DEBUG=true
ALLOWED_HOSTS=*
"""

MINIMAL_README = """\
# {{title}}

Minimal Stapel/Django project — SQLite, no Docker.

**NOT FOR PRODUCTION by default.** This preset is meant for local
development; nothing stops deploying it, but the settings module only turns
on TLS/HSTS/CSP hardening and the placeholder-secret guard when
`DJANGO_ENV=prod` is set (security-programme.md SEC-4/B8) — see `.env.example`.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

`.env` was already created from `.env.example` with a freshly generated
`SECRET_KEY`. API docs: http://localhost:8000/api/docs/
"""

MINIMAL_MAKEFILE = """\
# {{title}} — project controls.
# Override the interpreter: make controls PYTHON=/path/to/python
PYTHON ?= python

.PHONY: controls lint test boot-smoke openapi run migration-lint release-manifest

controls: lint boot-smoke test

lint:
\t$(PYTHON) -m ruff check .

# boot-smoke (R3/§44) — app loading (INSTALLED_APPS import + every
# AppConfig.ready()) must never need a live database. Runs `manage.py check`
# under config/settings_boot_smoke.py, which layers over this project's own
# settings and swaps DATABASES for Django's `dummy` backend (raises loudly on
# ANY connection attempt). Catches both a stray ready()/import-time DB query
# in a stapel-* module AND a harness/env leak overriding this project's own
# settings with someone else's database — either way, a generated project
# must never 500 a client because *loading* the app touched a database.
boot-smoke:
\tDJANGO_SETTINGS_MODULE=config.settings_boot_smoke $(PYTHON) manage.py check

test:
\t$(PYTHON) -m pytest tests -q

openapi:
\t$(PYTHON) manage.py spectacular --format openapi-json --file openapi.json --validate

run:
\t$(PYTHON) manage.py migrate && $(PYTHON) manage.py runserver

# --- Release seam (stapel docs/release-management.md, R-1) ------------------
# Both targets need stapel-tools on PATH (pip install stapel-tools).
#
# migration-lint — expand/contract gate over this project's migrations.
# Pass BASE_SHA=<previous release sha> to also verify nothing destroyed by a
# new migration is still referenced by the code of the previous release.
migration-lint:
\tstapel-migration-lint . $(if $(BASE_SHA),--base-sha $(BASE_SHA),)

# release-manifest — describe this checkout as a release artifact
# (release.json: migration watermarks, reversible floors, stapel-* contract
# pins, config digest, gate results). The platform bake step invokes this
# during image build and bakes the file into the image; standalone:
#   make release-manifest RELEASE=r1 [IMAGES=images.json] [BASE_SHA=<sha>]
release-manifest:
\ttest -n "$(RELEASE)" || (echo "usage: make release-manifest RELEASE=r<N>" >&2; exit 2)
\tstapel-release-manifest . --release $(RELEASE) --git-sha $$(git rev-parse HEAD) \\
\t\t$(if $(IMAGES),--images-json $(IMAGES),) $(if $(BASE_SHA),--base-sha $(BASE_SHA),) \\
\t\t--out release.json
"""

MINIMAL_PYPROJECT = """\
[tool.ruff]
line-length = 110
target-version = "py312"
exclude = ["migrations"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP"]

[tool.ruff.lint.isort]
known-first-party = ["config", "apps", "tests"]

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "config.settings"
python_files = ["test_*.py"]
testpaths = ["tests"]
"""

MINIMAL_CONFTEST = '''\
"""Shared fixtures for the {{title}} test suite (architect-owned).

Includes the outbox/mailtrap integration harness (system-design §7.21):
`drain_outbox`, `mailtrap`, and in-process comm with the outbox enabled.
"""
import pytest
from rest_framework.test import APIClient

from tests.harness import clear_mailtrap, read_mailtrap
from tests.harness import drain_outbox as _drain_outbox


@pytest.fixture
def api_client():
    """Unauthenticated DRF API client."""
    return APIClient()


@pytest.fixture(autouse=True)
def _reset_comm_registries():
    """Isolate action subscribers between tests (the registry is process-global)."""
    from stapel_core.comm import action_registry, function_registry

    action_registry.clear()
    function_registry.clear()
    yield
    action_registry.clear()
    function_registry.clear()


@pytest.fixture(autouse=True)
def outbox_comm(settings):
    """Run comm in-process with the transactional outbox ENABLED against the
    test DB — the production delivery path (emit -> outbox row -> dispatch),
    so producer/consumer/atomicity assertions exercise real behaviour."""
    settings.STAPEL_COMM = {
        **getattr(settings, "STAPEL_COMM", {}),
        "OUTBOX_ENABLED": True,
        "ACTION_TRANSPORT": "inprocess",
    }


@pytest.fixture
def drain_outbox():
    """Synchronously flush pending outbox rows through delivery (the test-time
    stand-in for ``manage.py dispatch_outbox``). Returns rows delivered."""
    return _drain_outbox


@pytest.fixture
def mailtrap(settings):
    """File mailtrap: force the file email backend, clear var/mailtrap/, then
    yield read_mailtrap(). pytest-django swaps EMAIL_BACKEND to locmem by
    default; async-consumer tests assert on the on-disk trap instead."""
    settings.EMAIL_BACKEND = "tests.harness.mailtrap.FileMailtrapBackend"
    clear_mailtrap()
    yield read_mailtrap
    clear_mailtrap()
'''
