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
    # Common Stapel plumbing (admin-suite AS-3/AS-4 visibility+nav wiring,
    # system checks) — same app every other preset installs via
    # stapel_core.django.settings.COMMON_INSTALLED_APPS; listed explicitly
    # here since minimal doesn't import that module wholesale.
    "stapel_core.django.apps.CommonDjangoConfig",
    # Stapel's own swappable User model (AUTH_USER_MODEL below) — every
    # feature module that references a user (auth, profiles, ...) expects
    # settings.AUTH_USER_MODEL / get_user_model() to resolve here, not to
    # django.contrib.auth's default User.
    "stapel_core.django.users",
    # Transactional outbox — every stapel_core.comm.emit() writes through it
    # (delivery guarantee + test harness, system-design §7.21).
    "stapel_core.django.outbox",{{STAPEL_APPS}}
    "apps.{{MODULE}}",
]{{STAPEL_MODULE_CONFIG}}

# Stapel's swappable user model (stapel_core/django/users/models.py) — set
# BEFORE the first migrate (scaffold-first: no migrations have run yet, so
# swapping AUTH_USER_MODEL post-hoc never applies here). Every stapel-* module
# that ships a user FK (auth, profiles, ...) references settings.AUTH_USER_MODEL
# / get_user_model(), never django.contrib.auth's own User — this must point
# at the Stapel model for those modules to work at all.
AUTH_USER_MODEL = "users.User"

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

# Reuses stapel-core's own template config (DIRS points at stapel_core's
# admin/base_site.html override + registers the nav context processor,
# admin-suite AS-4/§37) instead of hand-rolling a second copy that would drift
# — this is the same helper monolith/microservices call via
# `from stapel_core.django.settings import *`.
from stapel_core.django.settings import get_common_templates  # noqa: E402

TEMPLATES = get_common_templates(BASE_DIR)

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

# Celery (relevant only when an installed lib ships @shared_task, e.g.
# stapel-auth's login-notification task): minimal has no broker BY DESIGN,
# so tasks run inline — a lib's .delay() must never 500 for lack of one.
# config/celery.py binds the app; these two keep it broker-less.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False

# Cross-service admin nav (admin-suite AS-4/§37, stapel_core.django.nav): a
# minimal project is always a single service, so this is the one-row form of
# the same STAPEL_SERVICES registry the monolith/microservices presets seed
# via .env.example — set directly here since minimal has no dotenv loader.
# The "All Services" section collapses automatically for a single entry;
# per-installed-app navigation + the aggregate Swagger (api/docs/ above) are
# the per-module story until per-module swagger mounts ship (§9b follow-up).
STAPEL_SERVICES = [{"name": "{{title}}", "prefix": ""}]

# Dev-only outbound mail: writes to the console instead of hitting SMTP.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
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
# Runtime dependencies only. Django / djangorestframework / drf-spectacular
# are DELIBERATELY not pinned here directly — they are transitive
# dependencies of stapel_core's own pyproject.toml (Django>=5.1,
# djangorestframework>=3.14, drf-spectacular>=0.27) and pip resolves them
# from there. Pinning them again here would drift out of sync with what
# stapel_core actually declares. Dev/controls-only tooling lives in
# requirements-dev.txt, not here.
#
# The Stapel lib line(s) below are appended by stapel-create-project /
# stapel-assemble (STAPEL_LIBS registry) — stapel_core is always present.
"""

MINIMAL_REQUIREMENTS_DEV = """\
# Dev/controls tooling for `make controls` (lint + test) — kept out of
# requirements.txt (the runtime dependency set) so a production install
# doesn't pull test/lint machinery.
pytest>=7.4
pytest-django>=4.7
ruff>=0.4
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
pip install -r requirements.txt -r requirements-dev.txt
python manage.py migrate
python manage.py runserver
```

`requirements-dev.txt` is only needed for `make controls` (lint + test); a
production install only needs `requirements.txt`.

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

The outbox/mailtrap integration test HARNESS (tests/harness/, drain_outbox,
mailtrap fixtures) is a monolith/example-project concern, not shipped in the
minimal preset — see the harness's own docstring (system-design §7.21) if you
add stapel-tools' harness_files() back in by hand for a specific project.
"""
import pytest
from rest_framework.test import APIClient


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
'''

MINIMAL_TEST_SMOKE = '''\
"""Sanity checks for the generated project's own settings — not the
outbox/mailtrap harness (monolith/example projects only)."""
import pytest
from django.contrib.auth import get_user_model


def test_auth_user_model_is_the_stapel_user():
    """Every stapel-* feature module (auth, profiles, ...) references
    settings.AUTH_USER_MODEL / get_user_model() expecting Stapel's own
    swappable user (stapel_core/django/users/models.py), not Django's
    default django.contrib.auth.models.User."""
    from django.conf import settings

    assert settings.AUTH_USER_MODEL == "users.User"
    assert get_user_model()._meta.label == "users.User"


@pytest.mark.django_db
def test_admin_login_is_mounted(api_client):
    response = api_client.get("/admin/login/")
    assert response.status_code == 200
'''
