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
"""

MANAGE_PY = """\
#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.base")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
"""

BOOTSTRAP_SH = """\
#!/bin/sh
set -e
DB_HOST_DIRECT="${POSTGRES_HOST_DIRECT:-db}"
DB_PORT_DIRECT="${POSTGRES_PORT_DIRECT:-5432}"
echo "Waiting for database..."
until pg_isready -h "$DB_HOST_DIRECT" -p "$DB_PORT_DIRECT" -U "$POSTGRES_USER"; do sleep 1; done
echo "Applying migrations..."
POSTGRES_HOST="$DB_HOST_DIRECT" POSTGRES_PORT="$DB_PORT_DIRECT" python manage.py migrate --noinput
echo "Collecting static..."
python manage.py collectstatic --noinput --clear --verbosity 0
echo "Bootstrap done."
"""

DOCKERFILE = """\
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
COPY {{DIR}}/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# stapel_core is vendored as a git submodule at the project root
COPY stapel_core ./stapel_core
COPY {{DIR}} .
ENV DJANGO_SETTINGS_MODULE=core.settings.prod
CMD ["gunicorn", "core.wsgi:application", "--bind", "0.0.0.0:8000"]
"""

ASGI_PY = """\
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.base")
application = get_asgi_application()
"""

WSGI_PY = """\
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.base")
application = get_wsgi_application()
"""

URLS_PY = """\
from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls import include
from stapel_core.django.api.routers import OptionalSlashRouter
from stapel_core.django import setup_centralized_admin_login, get_admin_logout_urlpattern, get_health_urls
from stapel_core.django.openapi.swagger import get_dev_urls
from stapel_core.django.openapi.mcp import build_mcp_schema_view

url_prefix = settings.URL_PREFIX
service_name = settings.SERVICE_NAME

admin.site.site_header = f"{service_name} Admin"
admin.site.site_title = f"{service_name} Admin"
admin.site.index_title = f"{service_name} Admin — v{settings.APP_VERSION_NUMBER}"

auth_prefix = getattr(settings, "AUTH_SERVICE_PREFIX", "")
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

SERVICE_NAME = "{{TITLE}}"
URL_PREFIX = "{{URL_PREFIX}}"
CSRF_COOKIE_NAME = "csrftoken_{{SLUG}}"
SESSION_COOKIE_NAME = "stapel_sid_{{SLUG}}"
BASE_DIR = Path(__file__).resolve().parent.parent.parent

with open(BASE_DIR / "version.txt") as v_file:
    APP_VERSION_NUMBER = v_file.read().strip()

STATIC_ROOT = f"/app/staticfiles/{{SLUG}}/"
STATIC_URL = f"/staticfiles/{{SLUG}}/"
STATICFILES_DIRS = get_staticfiles_dirs(BASE_DIR)
MEDIA_ROOT = f"/app/media/{{SLUG}}/"
MEDIA_URL = f"/media/{{SLUG}}/"

# Dev fallbacks live in dev.py; prod.py refuses to start without real values.
SECRET_KEY = os.getenv("SECRET_KEY", "")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", SECRET_KEY)
ALLOWED_HOSTS = ALLOWED_HOSTS + ["{{DIR}}"]  # type: ignore[name-defined]

# Prefix of the dedicated auth service (e.g. "auth") when running in a
# multi-service stack. Leave empty to use Django's own admin login.
AUTH_SERVICE_PREFIX = os.getenv("AUTH_SERVICE_PREFIX", "")

INSTALLED_APPS = COMMON_INSTALLED_APPS + [{{STAPEL_APPS}}
    "{{MODULE}}",
]

MIDDLEWARE = COMMON_MIDDLEWARE

ROOT_URLCONF = "core.urls"
TEMPLATES = get_common_templates(BASE_DIR)
WSGI_APPLICATION = "core.wsgi.application"

DATABASES = {
    "default": get_default_database("{{DB_NAME}}"),
}

CACHES = {
    "default": {
        **DEFAULT_CACHE,
        "KEY_PREFIX": "{{SLUG}}",
    }
}

LOGIN_REDIRECT_URL = "/{{SLUG}}/admin/"
AUTH_USER_MODEL = "users.User"

FILE_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024

# Inter-module communication (see docs: module-communication.md).
# Action/Function transports follow the project's broker choice; env vars
# override for per-deployment tuning.
STAPEL_COMM = {
    "ACTION_TRANSPORT": os.getenv("STAPEL_ACTION_TRANSPORT", "{{ACTION_TRANSPORT}}"),
    "FUNCTION_TRANSPORT": os.getenv("STAPEL_FUNCTION_TRANSPORT", "{{FUNCTION_TRANSPORT}}"),
    "NATS_URL": os.getenv("NATS_URL", "nats://nats:4222"),
}

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://redis:6379/0"))
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_TASK_DEFAULT_QUEUE = "{{SLUG}}"

from stapel_core.django.openapi.swagger import get_spectacular_settings
SPECTACULAR_SETTINGS = get_spectacular_settings(
    title="{{TITLE}} API",
    description="{{TITLE}} service API",
    version="1.0.0",
)
"""

DEV_SETTINGS = """\
from .base import *  # noqa

DEBUG = True
ALLOWED_HOSTS += ["dev.{{SLUG}}.local"]

if not SECRET_KEY:
    SECRET_KEY = "django-insecure-{{SLUG}}-dev-only"
    JWT_SECRET_KEY = JWT_SECRET_KEY or SECRET_KEY

INSTALLED_APPS += ["debug_toolbar"]
MIDDLEWARE = MIDDLEWARE + ["debug_toolbar.middleware.DebugToolbarMiddleware"]
INTERNAL_IPS = ["127.0.0.1", "localhost"]


def show_toolbar(request):
    return request.method == "GET"


DEBUG_TOOLBAR_CONFIG = {
    "SHOW_TOOLBAR_CALLBACK": show_toolbar,
    "INTERCEPT_REDIRECTS": False,
}

import os
if os.environ.get("RUN_MAIN") or os.environ.get("WERKZEUG_RUN_MAIN"):
    try:
        import debugpy
        debugpy.listen(("0.0.0.0", 5678))
        print("debugpy listening on 5678")
    except Exception:
        pass
"""

LOCAL_SETTINGS = """\
from .dev import *  # noqa

HOSTNAME = "http://localhost"
ALLOWED_HOSTS = ["*"]
MEDIA_URL = "http://localhost/media/{{SLUG}}/"
INTERNAL_IPS = ["127.0.0.1", "localhost"]
"""

PROD_SETTINGS = """\
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa

DEBUG = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

if not SECRET_KEY or SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured("SECRET_KEY environment variable must be set in production")
if not JWT_SECRET_KEY or JWT_SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured("JWT_SECRET_KEY environment variable must be set in production")
"""

APP_PY = """\
from django.apps import AppConfig


class {{MODULE_CAP}}Config(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "{{MODULE}}"
    verbose_name = "{{TITLE}}"
"""

MODELS_PY = """\
from django.db import models

# Add service-specific models here
"""

ADMIN_PY = """\
from django.contrib import admin

# Register service models here
"""

PYTEST_INI = """\
[pytest]
DJANGO_SETTINGS_MODULE = core.settings.local
python_files = tests.py test_*.py *_test.py
python_classes = Test* *Tests
python_functions = test_*
testpaths = {{MODULE}}
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

# Dev / test (used by core.settings.dev and pytest)
django-debug-toolbar>=4.2
debugpy>=1.8
pytest>=7.4
pytest-django>=4.7

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
    verbose_name = "{{TITLE}}"
"""

MODULE_MODELS = """\
from django.db import models


# Add {{MODULE}} models here
"""

MODULE_ADMIN = """\
from django.contrib import admin

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

MODULE_VIEWS = """\
from rest_framework.viewsets import GenericViewSet
from drf_spectacular.utils import extend_schema
from stapel_core.django.errors import StapelResponse


class {{MODULE_CAP}}ViewSet(GenericViewSet):
    pass
"""

MODULE_URLS = """\
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
