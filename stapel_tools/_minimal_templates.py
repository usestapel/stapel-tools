"""Templates for stapel-create-project --type minimal (no Docker, SQLite)."""

MINIMAL_MANAGE = """\
#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
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

SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-change-this-in-production")
DEBUG = os.getenv("DEBUG", "true").lower() in ("true", "1", "yes")
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "*").split(",")

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
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"
WSGI_APPLICATION = "core.wsgi.application"

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
.DS_Store
"""

MINIMAL_README = """\
# {{title}}

Minimal Stapel/Django project — SQLite, no Docker.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

API docs: http://localhost:8000/api/docs/
"""

MINIMAL_MAKEFILE = """\
# {{title}} — project controls.
# Override the interpreter: make controls PYTHON=/path/to/python
PYTHON ?= python

.PHONY: controls lint test openapi run

controls: lint test

lint:
\t$(PYTHON) -m ruff check .

test:
\t$(PYTHON) -m pytest tests -q

openapi:
\t$(PYTHON) manage.py spectacular --format openapi-json --file openapi.json --validate

run:
\t$(PYTHON) manage.py migrate && $(PYTHON) manage.py runserver
"""

MINIMAL_PYPROJECT = """\
[tool.ruff]
line-length = 110
target-version = "py312"
exclude = ["migrations"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP"]

[tool.ruff.lint.isort]
known-first-party = ["core", "apps", "tests"]

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "core.settings"
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
