"""
Template strings for stapel-new-service and stapel-new-module.

Placeholders:
  {{TITLE}}      - human display name, e.g. "Auth Service"
  {{SLUG}}       - service slug, e.g. "auth"
  {{SLUG_UPPER}} - slug uppercased, e.g. "AUTH"
  {{PREFIX}}     - full directory prefix incl. dash, e.g. "svc-" or "iron-"
  {{DIR}}        - full service directory name = {{PREFIX}}{{SLUG}}, e.g. "svc-auth"
  {{MODULE}}     - Python module name = slug with dashes→underscores, e.g. "auth"
  {{MODULE_CAP}} - CamelCase of module, e.g. "Auth"
  {{DB_NAME}}    - database name, e.g. "stapel_auth"
  {{URL_PREFIX}} - URL prefix, e.g. "auth/"
  {{STAPEL_MODULE_CONFIG}} - rendered STAPEL_<MOD> = {...} settings blocks
                 (only non-default capability axes; "" when no module_config)
"""

# Entrypoint canon (§57 owner directive, live-run postmortem): migrate +
# createsuperuser through Django's OWN --noinput flow + collectstatic, with
# every step classified require/optional — see _boot_templates.py.
from ._boot_templates import BOOTSTRAP_SH  # noqa: F401  (re-exported)

MANAGE_PY = """\
#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.base")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
"""

DOCKERFILE = """\
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
COPY {{DIR}}/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# The bootstrap step runner (require/optional/bootstrap_done) — bootstrap.sh
# sources it from here.
COPY scripts/bootstrap_lib.sh /usr/local/lib/stapel-bootstrap.sh
# stapel_core is vendored as a git submodule at the project root
COPY stapel_core ./stapel_core
COPY {{DIR}} .
ENV DJANGO_SETTINGS_MODULE=config.settings.prod
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
"""

# cdn auto-wiring (cdn-scaffold-autowire.md) — emitted instead of DOCKERFILE
# when the service installs stapel_cdn (new_service.generate_service_files's
# HAS_CDN branch). pyvips ships no binary wheel on PyPI: `pip install
# pyvips` always compiles a cffi extension against the system libvips
# headers (pkg-config), which needs a C compiler. Building the wheel in a
# throwaway `vips-builder` stage keeps gcc/build-essential out of the
# runtime image — mirrors svc-stapel-studio/Dockerfile (the verified
# libvips container precedent): *system* libvips bound by the pip `images`
# extra (requirements.txt's stapel-cdn[images]), never a bundled/vendored
# libvips (that rules out `pyvips[binary]`, which ships its own separate
# libvips copy).
#
# WHICH FORMATS DECODE is a property of THIS build of libvips, not of
# libvips. Debian's libvips is modular (`vips-heif.so`, `vips-magick.so`
# live in vips-modules-8.16/), and HEIC/AVIF decoding sits one level further
# down again, in libheif's codec plugins. stapel-cdn 0.10 made libvips the
# ONLY decoder on the image path — validation and processing now ask the
# same engine — so STAPEL_CDN["ALLOWED_IMAGE_EXTENSIONS"] is a promise this
# image either keeps or breaks, and `stapel_cdn.checks.E004` names the
# formats it breaks at boot. This stage therefore NAMES every package the
# generated project's allowlist (_module_config.WEB_IMAGE_EXTENSIONS:
# .jpg .jpeg .png .gif .webp .avif .heic .heif) rests on instead of
# inheriting them as somebody else's transitive Depends:
#
#   jpeg/png/gif/webp  libvips42t64 itself
#   heic/heif          vips-heif.so + libheif1 + libheif-plugin-libde265
#   avif               the same path via libheif-plugin-dav1d
#
# .bmp is NOT in that allowlist and needs no package here: libvips has no
# native BMP reader at all (it goes through the optional ImageMagick module),
# so the library's own default promised a format the stock pip install cannot
# decode — E004's exact subject. Add both together or neither.
#
# Measured 2026-08-10 in this image (python:3.12-slim-trixie, arm64,
# libvips 8.16.1): every listed format decodes a real file end to end.
#
# The runtime stage installs libvips42t64 (the shared LIBRARY) and NOT
# libvips-dev: headers, and the ~40 -dev packages behind them, are the
# builder stage's business. Same eight formats, 331 MB instead of 662 MB.
#
# The base image is pinned to -trixie for the same reason the packages are
# named: `python:3.12-slim` floated bookworm -> trixie under the fleet, and
# a Debian release is exactly the event that changes a decoder set quietly.
#
# Naming the packages IS the build-time gate: apt fails the build the day
# Debian renames or drops one of them, instead of the image quietly losing a
# format. Whether the assembled image actually honours the deployment's OWN
# ALLOWED_IMAGE_EXTENSIONS (a host may add to it) stays stapel-cdn's boot
# check, E004 — a Django system-check Error, so a service that lost a decoder
# refuses to start rather than telling an uploader their file is invalid.
DOCKERFILE_CDN = """\
FROM python:3.12-slim-trixie AS vips-builder
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libvips-dev pkg-config && rm -rf /var/lib/apt/lists/*
RUN pip wheel --no-cache-dir --wheel-dir /wheels pyvips==3.1.1

FROM python:3.12-slim-trixie
WORKDIR /app
# libvips42t64 + the libheif codec plugins: the decoders behind
# STAPEL_CDN["ALLOWED_IMAGE_EXTENSIONS"]. No compiler and no headers here —
# pyvips arrives precompiled from vips-builder above via --find-links.
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client libvips42t64 libheif-plugin-libde265 libheif-plugin-dav1d && rm -rf /var/lib/apt/lists/*
COPY --from=vips-builder /wheels /wheels
COPY {{DIR}}/requirements.txt .
RUN pip install --no-cache-dir --find-links=/wheels -r requirements.txt && rm -rf /wheels
# The bootstrap step runner (require/optional/bootstrap_done) — bootstrap.sh
# sources it from here.
COPY scripts/bootstrap_lib.sh /usr/local/lib/stapel-bootstrap.sh
# stapel_core is vendored as a git submodule at the project root
COPY stapel_core ./stapel_core
COPY {{DIR}} .
ENV DJANGO_SETTINGS_MODULE=config.settings.prod
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
"""

CELERY_APP_PY = """\
\"\"\"Project Celery app — standard Django-Celery wiring.

Without this module every ``@shared_task`` in an installed stapel lib binds
to Celery's DEFAULT, UNCONFIGURED app (broker amqp://localhost) — the first
code path that calls ``.delay()`` (e.g. stapel-auth's login-notification
task) then 500s at runtime even though CELERY_* settings exist. Found live
by the e2e circle; part of the "generated project drives out of the box"
gate. config/__init__.py imports ``celery_app`` so the binding happens on
Django startup, and ``celery -A config worker`` (the --celery compose
blocks) finds the app.
\"\"\"
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.base")

app = Celery("{{MODULE}}")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
"""

CONFIG_INIT_PY = """\
# Standard Django-Celery wiring: bind every @shared_task to this project's
# configured app at startup (see config/celery.py). Guarded so a project
# whose dependency set carries no celery still boots.
try:
    from .celery import app as celery_app
except ModuleNotFoundError:  # celery not installed — nothing uses tasks
    celery_app = None

__all__ = ("celery_app",)
"""

ASGI_PY = """\
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.base")
application = get_asgi_application()
"""

WSGI_PY = """\
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.base")
application = get_wsgi_application()
"""

URLS_PY = """\
from django.conf import settings
from django.conf.urls import include
from django.contrib import admin
from django.urls import path
from stapel_core.django import get_admin_logout_urlpattern, get_health_urls, setup_centralized_admin_login
from stapel_core.django.api.routers import OptionalSlashRouter
from stapel_core.django.openapi.mcp import build_mcp_schema_view
from stapel_core.django.openapi.swagger import get_dev_urls

from .schema_health import register_schema_check

url_prefix = settings.URL_PREFIX
service_name = settings.SERVICE_NAME

# Schema drift on /api/health/ (named, non-critical) and /api/metrics/. A
# service that is up on a schema behind its code is invisible without this.
register_schema_check()

admin.site.site_header = f"{service_name} Admin"
admin.site.site_title = f"{service_name} Admin"
admin.site.index_title = f"{service_name} Admin — v{settings.APP_VERSION_NUMBER}"

auth_prefix = getattr(settings, "STAPEL_AUTH_SERVICE_PREFIX", "")
if auth_prefix:
    setup_centralized_admin_login(admin.site, auth_service_prefix=auth_prefix)

router = OptionalSlashRouter()

mcp_schema_view = build_mcp_schema_view(
    title="{{TITLE}} API",
    description="{{TITLE}} service API",
    version="1.0.0",
)

urlpatterns = [
    *get_health_urls(url_prefix),
    *([get_admin_logout_urlpattern(url_prefix, auth_prefix)] if auth_prefix else []),{{STAPEL_URL_INCLUDES}}
    path(f"{url_prefix}api/", include(router.urls)),
    path(f"{url_prefix}admin/", admin.site.urls),
    *get_dev_urls(url_prefix, mcp_schema_view),
]
"""

BASE_SETTINGS = """\
\"\"\"Django settings for {{DIR}} service.\"\"\"
from stapel_core.django.settings import *  # type: ignore  # noqa
import os
from pathlib import Path

from stapel_core.django.openapi.swagger import get_spectacular_settings

SERVICE_NAME = "{{TITLE}}"
URL_PREFIX = "{{URL_PREFIX}}"
CSRF_COOKIE_NAME = "csrftoken_{{SLUG}}"
SESSION_COOKIE_NAME = "stapel_sid_{{SLUG}}"
BASE_DIR = Path(__file__).resolve().parent.parent.parent

with open(BASE_DIR / "version.txt") as v_file:
    APP_VERSION_NUMBER = v_file.read().strip()

STATIC_ROOT = "/app/staticfiles/{{SLUG}}/"
STATIC_URL = "/staticfiles/{{SLUG}}/"
STATICFILES_DIRS = get_staticfiles_dirs(BASE_DIR)
MEDIA_ROOT = "/app/media/{{SLUG}}/"
MEDIA_URL = "/media/{{SLUG}}/"

# Dev fallbacks live in dev.py; prod.py refuses to start without real values.
SECRET_KEY = os.getenv("SECRET_KEY", "")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", SECRET_KEY)
ALLOWED_HOSTS = ALLOWED_HOSTS + ["{{DIR}}"]  # type: ignore[name-defined]

# Public origin of THIS project's frontend — the base every off-session
# redirect a Stapel module builds: stapel-auth's SSO callback, magic link, QR
# account-conflict, OTP-challenge continuation and security email/phone
# verification links; stapel-billing's Stripe checkout/portal return URLs;
# stapel-notifications' link rendering. All three resolve it through the same
# ladder (STAPEL_<MOD>["FRONTEND_URL"] -> this flat setting -> the FRONTEND_URL
# env var), so one value here wires all of them.
#
# NO fallback in this shared base ON PURPOSE: prod.py and staging inherit this
# module, and a dev-friendly default here (http://localhost:3000 and friends)
# would silently send real users' auth redirects to a developer's laptop
# instead of failing loudly. That is exactly what stapel_auth.E003 refuses to
# boot on with DEBUG=False — the dev fallback lives in dev.py, the real origin
# comes from .env (FRONTEND_URL, written at project creation).
FRONTEND_URL = os.getenv("FRONTEND_URL", "")

# Prefix of the dedicated auth service (e.g. "auth") when running in a
# multi-service stack. Leave empty to use Django's own admin login. Canonical
# name (read by stapel_core.django.mounts / AdminLoginRedirectMiddleware) —
# do not rename without updating both sides.
STAPEL_AUTH_SERVICE_PREFIX = os.getenv("STAPEL_AUTH_SERVICE_PREFIX", "")

# ─── Identity trust: whose users are these? ─────────────────────────────────
# What happens when a VALID token names a user_id this database has no row
# for. There are exactly two roles and this service must state which it is:
#
#   ISSUER   — it mints the tokens and owns the user table (it installs
#              stapel_auth). An unknown subject in a token it signed itself is
#              a STALE token; materialising a row from it would forge an
#              account nobody registered. -> False
#   CONSUMER — it verifies a neighbouring auth service's tokens and has no
#              sign-up of its own. The local row is a shadow copy keyed by the
#              issuer's id; refusing to create it answers 401 to every user
#              who registered after this service last saw them. -> True
#
# This service is a {{IDENTITY_ROLE}}, hence the value below. Flip it the day
# the roles swap — i.e. if this service starts (or stops) being the one that
# issues tokens and owns the accounts.
#
# Stated here ALWAYS, in both roles, on purpose. stapel-core flipped this
# default in a MINOR release (0.24: True -> False) and every service that had
# never answered the question changed mode on a version bump with no boot-time
# signal: seven of eight services began answering 401 to every new sign-up,
# visible only as "JWT Auth Failed - User creation failed" once per request
# (app.ironmemo.com, 2026-08-15..16). A service must say which it is, and keep
# saying it across core versions — stapel-config-lint CFG007 refuses one that
# mounts Stapel JWT auth and leaves this line out.
JWT_CREATE_USERS_FROM_TOKEN = {{JWT_CREATE_USERS_FROM_TOKEN}}

# Base the OAuth callback URL is built on — the value registered with Google /
# GitHub / any provider as an allowed redirect_uri. NOT derived from the
# request: build_absolute_uri() reads Host and X-Forwarded-Proto, so behind a
# proxy whose forwarded header the deployment has not opted into trusting
# (STAPEL_TRUST_PROXY_SSL_HEADER, see .env) it composes http:// and the
# provider refuses the handshake — the second half of the same 2026-08-16
# incident. A contract with a third party is configuration, not a header.
# Empty = fall back to the request-derived URI (fine for a single-origin dev
# run; set it on every stand).
OAUTH_CALLBACK_BASE_URL = os.getenv("OAUTH_CALLBACK_BASE_URL", "")

INSTALLED_APPS = COMMON_INSTALLED_APPS + [{{STAPEL_APPS}}
    "apps.{{MODULE}}",
]{{STAPEL_MODULE_CONFIG}}

MIDDLEWARE = COMMON_MIDDLEWARE

ROOT_URLCONF = "config.urls"
TEMPLATES = get_common_templates(BASE_DIR)
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": get_default_database("{{DB_NAME}}"),
}

CACHES = {
    "default": {
        **DEFAULT_CACHE,
        "KEY_PREFIX": "{{SLUG}}",
    }
}

# URL *name*, not a hardcoded path (house convention: absolute paths break
# under a mount prefix; Django's resolve_url() reverses names lazily).
LOGIN_REDIRECT_URL = "admin:index"
AUTH_USER_MODEL = "users.User"

FILE_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024

# Inter-module communication (see docs: module-communication.md).
# Action/Function transports follow the project's broker choice; env vars
# override for per-deployment tuning.
STAPEL_COMM = {
    "ACTION_TRANSPORT": os.getenv("STAPEL_ACTION_TRANSPORT", "{{ACTION_TRANSPORT}}"),
    "FUNCTION_TRANSPORT": os.getenv("STAPEL_FUNCTION_TRANSPORT", "{{FUNCTION_TRANSPORT}}"),
    # "bus" sends task.* events through the broker (STAPEL_BUS_BACKEND)
    # even when Actions stay in-process — long-running Tasks execute in a
    # dedicated worker instead of the web process.
    "TASK_DISPATCH": os.getenv("STAPEL_TASK_DISPATCH", "{{TASK_DISPATCH}}"),
    "NATS_URL": os.getenv("NATS_URL", "nats://nats:4222"),
}

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://redis:6379/0"))
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_TASK_DEFAULT_QUEUE = "{{SLUG}}"

SPECTACULAR_SETTINGS = get_spectacular_settings(
    title="{{TITLE}} API",
    description="{{TITLE}} service API",
    version="1.0.0",
)
"""

DEV_SETTINGS = """\
import os

from .base import *  # noqa

DEBUG = True
ALLOWED_HOSTS += ["dev.{{SLUG}}.local"]

# ─── Cookie transport, stated for the plain-HTTP tier ───────────────────────
# prod.py forces all three to True; this tier serves over plain HTTP (including
# dev.{{SLUG}}.local just added above, a LAN IP, an http staging host), so all
# three are False — and SAID so rather than inherited. A browser silently drops
# a Secure cookie on a non-localhost http origin, and the failure has no error
# in it: the admin login form 403s on a CSRF cookie that was never stored, and
# a successful JWT login is followed by an anonymous next request. These are
# exactly the defaults stapel-core has been moving (#349), and a tier that
# never states them changes behaviour on a library bump.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
JWT_COOKIE_SECURE = False

# ─── Browser WebSocket origins (stapel-core >=0.44.2) ───────────────────────
# A cookie is ambient authority: the browser attaches it to a WebSocket
# handshake started by ANY page, and sockets are protected by neither the
# same-origin policy nor CORS. So core gates the COOKIE branch on an origin
# allowlist and FAILS CLOSED — an empty list is a misconfiguration, not a
# wildcard: handshakes close 4403 and `manage.py check` reports
# `stapel_core.jwt.E001`.
#
# Core reads `STAPEL_WS_ALLOWED_ORIGINS` from the env (comma-separated), which
# the generated .env files set for the compose stack. This default covers a
# NATIVE run (Vite on :5173 talking to runserver on :8000) where no .env is
# loaded, so a developer never meets a socket that closes for a reason the
# stack could have known. prod.py inherits nothing from here.
if not STAPEL_WS_ALLOWED_ORIGINS:  # type: ignore[name-defined]  # noqa: F405
    STAPEL_WS_ALLOWED_ORIGINS = [
        "http://localhost:5173",  # Vite dev server
        "http://localhost:8000",  # runserver, when the SPA is served by Django
        "http://localhost",       # the local compose stack's nginx
    ]

# Local machine: run Celery tasks INLINE (no broker/worker required for the
# local stack to be complete — a lib's .delay() executes eagerly; errors are
# recorded on the result, never turned into a request 500).
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False

# File mailtrap for local/dev + async-consumer tests (see tests/harness):
# outbound mail is written to var/mailtrap/ as JSON instead of hitting SMTP.
MAILTRAP_DIR = BASE_DIR / "var" / "mailtrap"
EMAIL_BACKEND = "tests.harness.mailtrap.FileMailtrapBackend"

if not SECRET_KEY:
    SECRET_KEY = "django-insecure-{{SLUG}}-dev-only"
    JWT_SECRET_KEY = JWT_SECRET_KEY or SECRET_KEY

# Dev-only frontend origin — the local stack serves the SPA through nginx on
# http://localhost (docker-compose.local.yml; the Vite dev server itself sits
# behind it on :5173, so point this at http://localhost:5173 for a native
# frontend run). Deliberately HERE and not in base.py: base is what prod.py
# and staging inherit, and a dev default leaking into them is the failure
# stapel_auth.E003 exists to catch. .env/.env.local's FRONTEND_URL wins over
# this — base already read it.
FRONTEND_URL = FRONTEND_URL or "http://localhost"

INSTALLED_APPS += ["debug_toolbar"]
MIDDLEWARE = MIDDLEWARE + ["debug_toolbar.middleware.DebugToolbarMiddleware"]
INTERNAL_IPS = ["127.0.0.1", "localhost"]


def show_toolbar(request):
    return request.method == "GET"


DEBUG_TOOLBAR_CONFIG = {
    "SHOW_TOOLBAR_CALLBACK": show_toolbar,
    "INTERCEPT_REDIRECTS": False,
}

if os.environ.get("RUN_MAIN") or os.environ.get("WERKZEUG_RUN_MAIN"):
    try:
        import debugpy
        debugpy.listen(("0.0.0.0", 5678))
        print("debugpy listening on 5678")
    except Exception:
        pass
{{DEV_MOCK_PROVIDERS}}"""

LOCAL_SETTINGS = """\
from .dev import *  # noqa

HOSTNAME = "http://localhost"
ALLOWED_HOSTS = ["*"]
MEDIA_URL = "http://localhost/media/{{SLUG}}/"
INTERNAL_IPS = ["127.0.0.1", "localhost"]
"""

TEST_SETTINGS = """\
from stapel_core.templates import strict_template_variables

from .local import *  # noqa

# A missing template variable renders as a visible marker here instead of
# Django's default empty string.
#
# The default is what turns a renamed context variable into a hole in a
# delivered email: the template still renders, the suite still passes, and only
# the message that went out has a blank where the OTP code was. 200 OK, no
# exception, nobody can log in. Scoped to tests on purpose — it is not a
# production rendering change, and a marker in a dev page is a surprise nobody
# asked for.
#
# In a test that renders, assert on it:
#
#     from stapel_core.templates import assert_no_missing_variables
#     assert_no_missing_variables(html)
#
# Note this is the NET, not the closure: it catches the variable a test happens
# to exercise. The closure for a template you override from a library is that
# library's docs/templates.json, asserted in CI
# (stapel_tools.template_contract.declared_for).
TEMPLATES = strict_template_variables(TEMPLATES)  # noqa: F405
"""


PROD_SETTINGS = """\
import os

from stapel_core.django.prodguard import guard_db_password, guard_secret

from .base import *  # noqa

DEBUG = False

# Cookies: HttpOnly/SameSite already come from stapel_core.django.settings;
# base/dev leave Secure off so plain-HTTP local dev still works — force it
# here (security-programme.md gap B3).
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
JWT_COOKIE_SECURE = True

# ─── Transport hardening (security-programme.md SEC-4 / gap B1) ───────────
# SECURE_PROXY_SSL_HEADER is NOT set by the library on its own — since
# stapel-core 0.24 it is opt-in, and the deployment states the opt-in with
# STAPEL_TRUST_PROXY_SSL_HEADER in .env (the generated stack sets it True
# because nginx is the only way in and it overwrites X-Forwarded-Proto
# itself). Nothing here can decide that: this file cannot know what is in
# front of the process. Without the opt-in every absolute URI this service
# builds comes out http:// behind an https proxy — which is how OAuth
# redirect_uri stopped matching what Google and GitHub had registered
# (2026-08-16).
SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "True").lower() == "true"

# HSTS ramp: start conservative — 1 day, no subdomains, no preload — and
# raise SECURE_HSTS_SECONDS to 31536000 (1 year) once HTTPS has been verified
# stable for every host this cookie covers. include_subdomains and preload
# are both one-way doors for the whole domain (preload especially — near
# impossible to reverse once a domain ships in browser preload lists), so
# neither is enabled by default; raising them is a deliberate decision for
# the deploying team, not a default we should force.
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "86400"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True

# Content-Security-Policy — report-only by default (Django's native CSP
# middleware, Django>=6; older Django simply skips the header). A strict
# enforced policy can break django-admin's and Vite's inline scripts/styles
# without per-project tuning, so this ships observing violations rather than
# blocking them. Switch to enforce (rename SECURE_CSP_REPORT_ONLY below to
# SECURE_CSP) once the real source list for this project's frontend is
# known — open question, security-programme.md §8.4.
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

# ─── Prod-guard (security-programme.md gap B2/B6) ──────────────────────────
# Refuses to boot on a placeholder/too-short secret or the shipped default DB
# password — stapel-create-project (SEC-6) writes real generated values into
# .env at project creation, so this only fires on a copy-pasted .env.example.
guard_secret("SECRET_KEY", SECRET_KEY)
guard_secret("JWT_SECRET_KEY", JWT_SECRET_KEY)
guard_db_password(DATABASES["default"].get("PASSWORD"))
"""

APP_PY = """\
from django.apps import AppConfig


class {{MODULE_CAP}}Config(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    # Full dotted path — the service's own app lives under the apps/ regular
    # package (apps/__init__.py present), same as every stapel-new-module app
    # (Django ticket #24801).
    name = "apps.{{MODULE}}"
    # Explicit, collision-proof label: a service named after a hosted Stapel
    # module (e.g. "auth", "profiles") would otherwise take the bare "{{MODULE}}"
    # label and clash with django.contrib.auth / stapel_{{MODULE}} (which sets
    # label="{{MODULE}}"), raising ImproperlyConfigured before any test collects.
    # The "_local" suffix marks this as the SERVICE'S OWN app (vs. the hosted
    # stapel_* module) and mirrors the config.settings.local naming convention.
    label = "{{MODULE}}_local"
    verbose_name = "{{TITLE}}"
"""

MODELS_PY = """\
from django.db import models  # noqa: F401 — import kept ready for the first model

# Add service-specific models here
"""

ADMIN_PY = """\
from django.contrib import admin  # noqa: F401 — import kept ready for the first registration

# Register service models here
"""

PYTEST_INI = """\
[pytest]
DJANGO_SETTINGS_MODULE = config.settings.test
python_files = tests.py test_*.py *_test.py
python_classes = Test* *Tests
python_functions = test_*
testpaths = apps tests
"""

SVC_PYPROJECT = """\
[tool.ruff]
line-length = 110
target-version = "py312"
exclude = ["migrations"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP"]

[tool.ruff.lint.per-file-ignores]
# Django settings tiers star-import the layer below by design (base ->
# dev -> local, base -> prod — Django's own convention): every name a lower
# tier defines looks "undefined" to a linter reading a higher tier's file in
# isolation. Not a bug — silence F403/F405 for this directory only.
"config/settings/*.py" = ["F403", "F405"]

[tool.ruff.lint.isort]
known-first-party = ["config", "apps", "tests"]
"""

# Boot-smoke settings (R3/§44) — the service-dir counterpart of the minimal
# preset's config/settings_boot_smoke.py (_minimal_templates.MINIMAL_BOOT_SMOKE_SETTINGS).
# Layers over config.settings.base — the SAME tier assemble_scaffold's own
# "check" gate already runs monolith against — rather than .dev/.local:
# those add django-debug-toolbar to INSTALLED_APPS, and the toolbar's SQL
# panel unconditionally imports django.contrib.gis at ready()-time (a real,
# observed crash on hosts with a broken/missing GDAL native lib) — an
# environment fragility this gate must not inherit. base's INSTALLED_APPS is
# also the closer analogue of what a client's first prod boot actually
# loads (prod.py layers over base too, not dev). Same dummy-DATABASES
# contract either way: `manage.py check` under this module must never touch
# a live database, proving app-loading (INSTALLED_APPS import + every
# AppConfig.ready()) is safe before a single request lands.
BOOT_SMOKE_SETTINGS = """\
\"\"\"Boot-smoke settings (R3/§44) — `make boot-smoke`, part of `make controls`.

Layers over this service's own config.settings.base (deliberately NOT
.dev/.local — see BOOT_SMOKE_SETTINGS's comment in stapel-tools) and swaps
DATABASES for Django's `dummy` backend, which raises loudly on the first
real connection attempt. `manage.py check` under this module exercises
exactly the phase a generated project's client would hit on first boot —
INSTALLED_APPS import plus every AppConfig.ready() — with no live database
reachable, proving that phase never needs one. Mirrors the minimal preset's
own config/settings_boot_smoke.py (same contract, different settings-tier
shape).

base.py's own SECRET_KEY has no fallback (only dev.py's does — "Dev
fallbacks live in dev.py; prod.py refuses to start without real values", per
base.py's own comment) and stapel_core's config.E001 system check refuses to
boot without one — resolved via ``os.environ`` directly (get_config()'s own
contract), NOT via django.conf.settings, so a plain Python assignment here
would not satisfy it. This gate must run standalone (no shell-sourced .env,
no docker) straight after `stapel-assemble`/`create-project`, so it seeds
``os.environ`` itself (only when unset — never overrides a real secret
already exported) rather than depending on dev.py's (which would also pull
in django-debug-toolbar's INSTALLED_APPS entry — see above).

What it seeds is FRESHLY GENERATED per process, not a constant. This tier
runs with DEBUG=False, which is exactly when stapel_core's prodguard demands
a real secret: it rejects anything empty, shorter than 50 characters, or
starting with `django-insecure-`/`change_me` (prodguard.E001). A constant
chosen to slip past that rule would satisfy the gate while being precisely
what the gate exists to forbid — and, sitting in a template every generated
project carries, it is the kind of value that eventually reaches a real
deploy. A random per-process value cannot: it satisfies the gate by BEING
what the gate asks for, it dies with the process, and there is nothing to
copy. Nothing is weakened by it — this module serves no traffic and its
DATABASES is Django's `dummy` backend.

FRONTEND_URL is the same story one setting over: base.py reads it from the
environment with no fallback (a default there would leak into prod — see its
comment and stapel_auth.E003), and this gate runs with DEBUG=False, which is
precisely when E003 demands a value. It is a plain Django setting, not a
get_config key, so this layer supplies it as an assignment below rather than
through os.environ — the same dev-layer-only containment the check's hint
asks for.

Never point real traffic at this module — it has no usable database.
\"\"\"
import os
import secrets

os.environ.setdefault("SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("JWT_SECRET_KEY", os.environ["SECRET_KEY"])

from .base import *  # noqa: E402,F401,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}
FRONTEND_URL = FRONTEND_URL or "http://localhost"  # noqa: F405 — gate-only, see docstring
"""

# svc-<slug>/Makefile — this service's own controls, runnable standalone from
# inside the directory OR delegated into from the project-root Makefile
# (`make -C {{DIR}} <target>` — see MONOLITH_MAKEFILE in _compose_templates.py).
# Target names/semantics match the minimal preset's Makefile 1:1 (controls:
# lint boot-smoke test) so the studio controls contract is preset-agnostic.
SVC_MAKEFILE = """\
# {{TITLE}} service ({{DIR}}) — controls.
PYTHON ?= python

.PHONY: controls lint test boot-smoke messages messages-check

controls: lint boot-smoke test

lint:
\t$(PYTHON) -m ruff check .

# boot-smoke (R3/§44) — see config/settings/boot_smoke.py's own docstring.
boot-smoke:
\tDJANGO_SETTINGS_MODULE=config.settings.boot_smoke $(PYTHON) manage.py check

test:
\t$(PYTHON) -m pytest -q
"""

CONFTEST_PY = """\
import pytest


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient
    return APIClient()
"""

TEST_MODELS_PY = '''\
"""Tests for {{MODULE}} models."""
import pytest


@pytest.mark.django_db
class Test{{MODULE_CAP}}Models:
    def test_placeholder(self):
        assert True
'''

SERVICE_YML = """\
services:
  {{DIR}}:
    env_file: ".env"
    image: ${IMAGE_TAG_{{SLUG_UPPER}}}
    restart: unless-stopped
    build:
      context: .
      dockerfile: {{DIR}}/Dockerfile
    command: >
      sh -c "sh bootstrap.sh && ${RUN_CMD}"
    environment:
      POSTGRES_DB: "{{DB_NAME}}"
    volumes:
      - static-content:/app/staticfiles
      - media-content:/app/media
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_started
    healthcheck:
      test: ["CMD-SHELL", "python3 -c 'import urllib.request; urllib.request.urlopen(\\"http://localhost:8000/{{URL_PREFIX}}api/health/\\")'"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 30s

  # Uncomment when FUNCTION_TRANSPORT=nats: serves this service's comm
  # Functions over NATS request-reply (see manage.py serve_functions).
  # {{DIR}}-functions:
  #   env_file: ".env"
  #   image: ${IMAGE_TAG_{{SLUG_UPPER}}}
  #   command: sh -c "python manage.py serve_functions"
  #   restart: unless-stopped
  #   environment:
  #     POSTGRES_DB: "{{DB_NAME}}"
  #   depends_on:
  #     - nats
  #     - db
"""

REQUIREMENTS_TXT = """\
# Runtime dependencies of a Stapel service.
# stapel_core itself is vendored as a git submodule at the project root
# and copied into the image by the Dockerfile; these are its dependencies
# plus the service runtime.
Django>=5.1
djangorestframework>=3.14
djangorestframework-dataclasses>=1.2
drf-spectacular>=0.27
django-cors-headers>=4.3
django-redis>=5.4
PyJWT>=2.8
cryptography>=41.0
requests>=2.31
psycopg[binary]>=3.1
gunicorn>=21.2
celery>=5.3

# Dev / test / controls (used by config.settings.dev, pytest, `make lint`)
django-debug-toolbar>=4.2
debugpy>=1.8
pytest>=7.4
pytest-django>=4.7
ruff>=0.4

# Add service-specific dependencies below.
"""

VERSION_TXT = "0.1.0\n"

# ---------------------------------------------------------------------------
# New module (Django app) templates
# ---------------------------------------------------------------------------

MODULE_INIT = ""

MODULE_APPS = """\
from django.apps import AppConfig


class {{MODULE_CAP}}Config(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "{{APP_PATH}}"
    # Explicit, collision-proof label: a module named after a hosted Stapel app
    # (e.g. "auth", "profiles") would otherwise take the bare "{{MODULE}}" label
    # and clash with django.contrib.auth / stapel_{{MODULE}} (which sets
    # label="{{MODULE}}"), raising ImproperlyConfigured before any test collects.
    # The "_local" suffix marks this as a project-local app (vs. a hosted
    # stapel_* module).
    label = "{{MODULE}}_local"
    verbose_name = "{{TITLE}}"
"""

MODULE_MODELS = """\
from django.db import models  # noqa: F401 — import kept ready for the first model


# Add {{MODULE}} models here
"""

MODULE_ADMIN = """\
from django.contrib import admin  # noqa: F401 — import kept ready for the first registration

# from .models import MyModel
# admin.site.register(MyModel)
"""

MODULE_DTO = """\
from dataclasses import dataclass


@dataclass
class {{MODULE_CAP}}Response:
    \"\"\"{{TITLE}} response schema.

    Attributes:
        id: Object identifier. Example: 1
    \"\"\"

    id: int
"""

MODULE_SERIALIZERS = """\
from stapel_core.django.api.serializers import StapelDataclassSerializer
from .dto import {{MODULE_CAP}}Response


class {{MODULE_CAP}}ResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = {{MODULE_CAP}}Response
"""

MODULE_ERRORS = """\
from stapel_core.django.errors import register_service_errors

ERR_404_{{MODULE_UPPER}}_NOT_FOUND = "error.404.{{SLUG}}.not_found"

_ERRORS = {
    ERR_404_{{MODULE_UPPER}}_NOT_FOUND: "{{TITLE}} not found.",
}
register_service_errors(_ERRORS)
"""

MODULE_PRESENTERS = """\
\"\"\"Presenters for {{MODULE}} — the DTO-building layer (§55 presenter canon).

Views NEVER instantiate a `dto.py` dataclass directly (SWAP002) and never
import a concrete presenter class registered as a swap default (SWAP001) —
they call :func:`get_{{MODULE}}_presenter`, so a host project can replace
the presentation via ``STAPEL_SWAP`` without forking this app.

Once this app has a real DAO model, base the presenter on stapel-core's
DAO→DTO primitive (``stapel_core.django.api.presenters.Presenter`` — model/
fields/custom_fields declaration, auto-generated DTO + serializer, listed in
the auto-catalog PRESENTERS.MD). Etalon:
stapel_core/django/users/presenters.py.
\"\"\"
from stapel_core.django.swappable import declare_swap, get_presenter

from .dto import {{MODULE_CAP}}Response

#: Swap key for the host presenter override (STAPEL_SWAP registry).
PRESENTER_KEY = "{{MODULE_UPPER}}_PRESENTER"

#: Dotted path of the default presenter — single source for both the
#: declare_swap() catalog registration and the get_presenter() fallback.
DEFAULT_PRESENTER = "{{APP_PATH}}.presenters.{{MODULE_CAP}}Presenter"

# Import-time declaration: makes the swap point visible to the auto-catalog
# (PRESENTERS.MD, `manage.py presenter_catalog`).
declare_swap(PRESENTER_KEY, DEFAULT_PRESENTER)


class {{MODULE_CAP}}Presenter:
    \"\"\"Builds {{MODULE_CAP}}Response DTOs — the only place they are
    instantiated. Swap to stapel-core's Presenter base once a DAO model
    exists (see module docstring).\"\"\"

    def present(self, obj) -> {{MODULE_CAP}}Response:
        return {{MODULE_CAP}}Response(id=obj.id)


def get_{{MODULE}}_presenter() -> type:
    \"\"\"The active (possibly host-swapped) {{MODULE}} presenter — consume
    this, never import {{MODULE_CAP}}Presenter directly (SWAP001).\"\"\"
    return get_presenter(PRESENTER_KEY, default=DEFAULT_PRESENTER)
"""

MODULE_VIEWS = """\
\"\"\"Views for {{MODULE}} — presenter-canonical from birth (§55).

The canon for every endpoint you add here:

    from .presenters import get_{{MODULE}}_presenter
    from .serializers import {{MODULE_CAP}}ResponseSerializer

    dto = get_{{MODULE}}_presenter()().present(obj)
    return StapelResponse({{MODULE_CAP}}ResponseSerializer(dto))

NEVER this (SWAP002 — bypasses the presenter, kills the host's swap seam):

    from .dto import {{MODULE_CAP}}Response
    return StapelResponse(serializer({{MODULE_CAP}}Response(id=obj.id)))
\"\"\"
from rest_framework.viewsets import GenericViewSet
from drf_spectacular.utils import extend_schema
from stapel_core.django.errors import StapelResponse


class {{MODULE_CAP}}ViewSet(GenericViewSet):
    pass
"""

MODULE_URLS = """\
# v1 canon mount (api-versioning.md §2): the version segment sits right
# after api/; the actual URL set lives in urls_v1.py.
from django.urls import include, path

urlpatterns = [
    path("api/v1/", include("{{APP_PATH}}.urls_v1")),
]
"""

MODULE_URLS_V1 = """\
from django.urls import path
from .views import {{MODULE_CAP}}ViewSet

urlpatterns: list = []
"""

MODULE_TESTS_INIT = ""

MODULE_TEST_MODELS = """\
\"\"\"Tests for {{MODULE}} models.\"\"\"
import pytest


@pytest.mark.django_db
class Test{{MODULE_CAP}}:
    def test_placeholder(self):
        assert True
"""
