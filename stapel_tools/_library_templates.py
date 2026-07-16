"""Templates for ``stapel-new-library`` — a standalone stapel-* package repo.

Materializes docs/library-standard.md from the stapel workspace: flat-layout
packaging, conf namespace, comm surface with schemas, seams, community files,
CI with the codecov ratchet/floor policy, git hooks.

Placeholders: {{SLUG}} (dash name, e.g. "search"), {{PKG}} (stapel_search),
{{NAME_DASH}} (stapel-search), {{NAMESPACE}} (STAPEL_SEARCH), {{TITLE}},
{{CAMEL}} (Search), {{YEAR}}.
"""

PYPROJECT = '''[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{{NAME_DASH}}"
version = "0.1.0"
description = "{{TITLE}} for the Stapel framework"
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.11"
keywords = ["django", "stapel", "{{SLUG}}"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Framework :: Django",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3 :: Only",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Typing :: Typed",
]
dependencies = [
    "stapel-core>=0.3.0,<0.4",
]

[project.urls]
Homepage = "https://github.com/usestapel/{{NAME_DASH}}"
Repository = "https://github.com/usestapel/{{NAME_DASH}}"
Documentation = "https://github.com/usestapel/{{NAME_DASH}}#readme"
Changelog = "https://github.com/usestapel/{{NAME_DASH}}/blob/main/CHANGELOG.md"
Issues = "https://github.com/usestapel/{{NAME_DASH}}/issues"

[project.optional-dependencies]
all = []

[tool.setuptools]
package-dir = {"{{PKG}}" = "."}
packages = [{{PACKAGES}}]

[tool.setuptools.package-data]
{{PKG}} = ["py.typed"{{PACKAGE_DATA_EXTRA}}]

[tool.ruff]
target-version = "py311"

[tool.ruff.lint]
# Single source for the git hooks and CI (they pass the same flags on the CLI).
select = ["E", "F", "W"]
ignore = ["E501"]

[tool.pytest.ini_options]
django_find_project = false
addopts = "--tb=short -q --import-mode=importlib"

[tool.coverage.run]
omit = [
    "*/tests/*",
    "*/conftest.py",
    "*/migrations/*",
]
'''

INIT = '''"""{{NAME_DASH}} — {{TITLE}} for the Stapel framework.

Public API (lazily exported, PEP 562 — importing this package never pulls
in Django or requires configured settings):

- ``{{SLUG_U}}_settings`` — resolved app settings (``{{PKG}}.conf``).
"""

__all__ = [
    "{{SLUG_U}}_settings",
]

# name -> submodule that defines it. Resolution is deferred until first
# attribute access so that `import {{PKG}}` stays Django-free.
_LAZY_EXPORTS = {
    "{{SLUG_U}}_settings": ".conf",
}


def __getattr__(name):
    if name in _LAZY_EXPORTS:
        from importlib import import_module

        value = getattr(import_module(_LAZY_EXPORTS[name], __name__), name)
        globals()[name] = value  # cache for subsequent lookups
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))
'''

APPS = '''from django.apps import AppConfig


class {{CAMEL}}Config(AppConfig):
    name = "{{PKG}}"
    label = "{{SLUG_U}}"
    verbose_name = "{{TITLE}}"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Import-time side effects: comm functions/actions, system checks,
        # error-key registration. Keep each in its own module.
        from . import checks  # noqa: F401
        from . import errors  # noqa: F401
        from . import functions  # noqa: F401
'''

CONF = '''"""Settings namespace for {{NAME_DASH}}.

All configuration is read through ``{{SLUG_U}}_settings`` (lazily, at call
time) — never via module-level ``os.getenv`` (values would freeze at import).
Resolution order per key: ``settings.{{NAMESPACE}}`` dict -> flat Django
setting of the same name -> environment variable -> default below.

Dotted-path keys listed in ``import_strings`` are resolved with
``import_string`` — the fork-free escape hatch for swappable behavior.
"""
from stapel_core.conf import AppSettings

{{SLUG_U}}_settings = AppSettings(
    "{{NAMESPACE}}",
    defaults={
        # Example knob — replace with real settings, document each in
        # MODULE.md ("Settings" table) as you add them.
        "GREETING": "pong",
    },
    import_strings=(),
)

__all__ = ["{{SLUG_U}}_settings"]
'''

MODELS = '''"""Models for {{NAME_DASH}}.

House rules (docs/library-standard.md §3.8):
- cross-service references are UUID fields, not FKs;
- the user model is only ``settings.AUTH_USER_MODEL``;
- index names must be <= 30 characters (models.E034);
- journal-style models get a read-only ModelAdmin.
"""
# from django.db import models
'''

DTO = '''"""Dataclass DTOs — the API models of {{NAME_DASH}} (never ORM instances)."""
from dataclasses import dataclass


@dataclass
class PingResponse:
    """Response of the scaffold ping endpoint — replace with real DTOs."""

    greeting: str
'''

SERIALIZERS = '''"""Serializers for the {{NAME_DASH}} API."""
from stapel_core.django.api.serializers import StapelDataclassSerializer

from .dto import PingResponse


class PingResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = PingResponse
'''

VIEWS = '''"""DRF views for {{NAME_DASH}}."""
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.views import APIView
from stapel_core.django.api.errors import StapelResponse

from .conf import {{SLUG_U}}_settings
from .dto import PingResponse
from .serializers import PingResponseSerializer


class SerializerSeamMixin:
    """Overridable serializer seam for every {{NAME_DASH}} APIView.

    Host projects can swap the request/response serializer of any view by
    subclassing and setting ``request_serializer_class`` /
    ``response_serializer_class`` (or overriding the getters for
    per-request decisions) — no need to rewrite the HTTP method bodies.
    """

    request_serializer_class = None
    response_serializer_class = None

    def get_request_serializer_class(self):
        return self.request_serializer_class

    def get_response_serializer_class(self):
        return self.response_serializer_class


@extend_schema(tags=["{{TITLE}}"])
class PingView(SerializerSeamMixin, APIView):
    """Scaffold example — replace with real views, keep the seam."""

    permission_classes = [permissions.AllowAny]
    response_serializer_class = PingResponseSerializer

    @extend_schema(responses={200: PingResponseSerializer})
    def get(self, request):
        response_cls = self.get_response_serializer_class()
        return StapelResponse(
            response_cls(PingResponse(greeting={{SLUG_U}}_settings.GREETING))
        )
'''

URLS = '''"""Root URLconf for {{NAME_DASH}} — v1 canon mount (api-versioning.md §2).

Canon: ``/<mod>/api/v1/...`` — the version segment sits right after ``api/``;
bare ``/<mod>/api/...`` paths do not exist. The host project mounts this
module root:

    path("{{SLUG}}/", include("{{PKG}}.urls"))   # -> /{{SLUG}}/api/v1/...

The actual v1 URL set lives in ``urls_v1.py``; a ``v2`` appears only when a
classified breaking change forces it (api-versioning.md §3).
"""
from django.urls import include, path

urlpatterns = [
    path("api/v1/", include("{{PKG}}.urls_v1")),
]
'''

URLS_V1 = '''"""v1 URL set — paths here are relative to the ``api/v1/`` mount
contributed by the root ``urls.py`` (api-versioning.md §2).
"""
from django.urls import path

from .views import PingView

urlpatterns = [
    path("ping", PingView.as_view(), name="{{SLUG_U}}-ping"),
]
'''

ERRORS = '''"""i18n error keys of {{NAME_DASH}}.

Only ``error.<status>.<slug>`` keys leave this package — human-readable
strings are translations, never literals in responses.
"""
from stapel_core.django.api.errors import register_service_errors

ERR_400_EXAMPLE = "error.400.{{SLUG_U}}_example"

{{NAMESPACE}}_ERRORS = {
    ERR_400_EXAMPLE: "Example error — replace with real keys",
}

register_service_errors({{NAMESPACE}}_ERRORS)

__all__ = ["{{NAMESPACE}}_ERRORS", "ERR_400_EXAMPLE"]
'''

CHECKS = '''"""Django system checks for {{NAME_DASH}} configuration.

Policy (docs/library-standard.md §3.7): E-level for configuration the
service cannot run with; W-level for entries that degrade lazily (a broken
*unused* dotted path must not block deploys).

Example:

    from django.core import checks

    @checks.register(checks.Tags.compatibility)
    def check_default_provider(app_configs, **kwargs):
        if ...:
            return [checks.Error("...", id="{{PKG}}.E001")]
        return []
"""
'''

FUNCTIONS = '''"""comm surface of {{NAME_DASH}}.

Every Function/Action carries a JSON schema in ``schemas/`` — tests run
with ``VALIDATE_SCHEMAS`` on, so a payload drifting from its schema fails
loudly. Registration happens on import from ``apps.py:ready()``; re-imports
are no-ops.
"""
from stapel_core.comm import function

from .conf import {{SLUG_U}}_settings


@function("{{SLUG}}.ping")
def ping(payload):
    """Scaffold example Function — replace with the real comm surface.

    Input: ``{}``; output: ``{"greeting": str}``.
    """
    return {"greeting": {{SLUG_U}}_settings.GREETING}
'''

SCHEMA_PING = '''{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "{{SLUG}}.ping",
  "description": "Scaffold example Function - replace with the real contract.",
  "type": "object",
  "properties": {},
  "additionalProperties": false
}
'''

CONFTEST = '''def pytest_configure(config):
    from django.conf import settings
    if not settings.configured:
        settings.configure(
            SECRET_KEY="test-secret-key-not-for-production",
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "django.contrib.sessions",
                "django.contrib.admin",
                "django.contrib.messages",
                "stapel_core.django.users",
                "rest_framework",
                "{{PKG}}",
            ],
            AUTH_USER_MODEL="users.User",
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            USE_TZ=True,
            ROOT_URLCONF="{{PKG}}.tests.urls",
            CACHES={
                "default": {
                    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                }
            },
            # Synchronous in-process comm with schema validation ON, so the
            # committed contracts in schemas/ are enforced by the tests.
            STAPEL_BUS_BACKEND="stapel_core.bus.backends.memory.MemoryBus",
            STAPEL_COMM={
                "OUTBOX_ENABLED": False,
                "ACTION_TRANSPORT": "inprocess",
                "VALIDATE_SCHEMAS": True,
            },
            MIGRATION_MODULES={
                "users": None,
                "{{SLUG_U}}": None,
            },
        )
        import django
        django.setup()

        from stapel_core.comm.schemas import autoload_schemas
        autoload_schemas()


import pytest  # noqa: E402


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient
    return APIClient()
'''

TESTS_INIT = ""

TESTS_URLS = '''from django.urls import include, path

urlpatterns = [
    path("{{SLUG}}/", include("{{PKG}}.urls")),
]
'''

TEST_PUBLIC_API = '''"""Package-level public API (PEP 562 lazy exports) and import hygiene."""
import os
import subprocess
import sys

import {{PKG}}


class TestLazyExports:
    def test_all_declares_public_api(self):
        assert {{PKG}}.__all__ == [
            "{{SLUG_U}}_settings",
        ]

    def test_settings_resolve(self):
        from {{PKG}}.conf import {{SLUG_U}}_settings

        assert {{PKG}}.{{SLUG_U}}_settings is {{SLUG_U}}_settings

    def test_unknown_attribute_raises(self):
        try:
            {{PKG}}.nonexistent_export
        except AttributeError as exc:
            assert "nonexistent_export" in str(exc)
        else:
            raise AssertionError("expected AttributeError")


class TestImportWithoutDjangoSettings:
    def test_package_import_is_django_free(self):
        """`import {{PKG}}` must not import Django nor require settings."""
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
        code = (
            "import sys\\n"
            "import {{PKG}}\\n"
            'polluted = [m for m in sys.modules if m == "django" or m.startswith("django.")]\\n'
            'assert not polluted, f"django imported at package import time: {polluted}"\\n'
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            cwd=os.path.dirname(sys.executable),
        )
        assert result.returncode == 0, result.stderr
'''

TEST_PING = '''"""Scaffold example tests — replace alongside the ping example.

They demonstrate the three house test layers: HTTP endpoint, comm Function
under schema validation, and the settings seam.
"""
import pytest
from django.test import override_settings

from stapel_core.comm import call


@pytest.mark.django_db
class TestPingEndpoint:
    def test_ping(self, api_client):
        resp = api_client.get("/{{SLUG}}/api/v1/ping")
        assert resp.status_code == 200
        assert resp.json()["greeting"] == "pong"

    @override_settings({{NAMESPACE}}={"GREETING": "hi"})
    def test_greeting_is_a_setting(self, api_client):
        assert api_client.get("/{{SLUG}}/api/v1/ping").json()["greeting"] == "hi"


class TestPingFunction:
    def test_call_in_process(self):
        assert call("{{SLUG}}.ping", {}) == {"greeting": "pong"}
'''

MODULE_MD = '''# {{NAME_DASH}} — MODULE.md

> Agent-facing map of this module: what it provides, where to extend it
> without forking, and what not to do. Kept in the same PR as any change
> to a seam. See also README.md and CHANGELOG.md.

## What this module provides

- TODO: 3-5 bullets — domain, models, API, comm surface.

## Extension points (fork-free)

### Settings — `{{NAMESPACE}}` namespace (`conf.py`)

Resolution order per key: `settings.{{NAMESPACE}}[key]` -> flat Django setting
of the same name -> environment variable -> default. Read lazily at call
time; caches invalidate on `setting_changed`.

| Key | Default | What it customizes |
|---|---|---|
| `GREETING` | `"pong"` | Scaffold example — replace. |

State for every registry-style key whether it MERGES over built-ins
(open registry) or REPLACES a single strategy (dotted path).

### Serializer seams (`views.py`)

`SerializerSeamMixin` — subclass a view, set
`request_serializer_class` / `response_serializer_class`, remount the URL.

| View | Request serializer | Response serializer |
|---|---|---|
| `PingView` | — | `PingResponseSerializer` |

### Events & functions (comm surface)

| Kind | Name | Payload | Schema |
|---|---|---|---|
| Function (provides) | `{{SLUG}}.ping` | `{}` -> `{greeting}` | `schemas/functions/{{SLUG}}.ping.json` |

## Anti-patterns

- **Don't fork to change behavior** — every knob above is a seam; if a
  change is impossible without editing this package, that is an upstream
  bug: open an issue/contribution instead.
- **Don't import other stapel modules** — cross-module communication is
  comm (Actions/Functions) by string name only.
- **Don't bypass the settings namespace** with `os.getenv` at import time.

## App-layer override vs upstream contribution — rule of thumb

**App-layer** (host project, no fork) if the change fits a seam above: a
settings key, a subclass + URL remount, a comm subscriber.

**Upstream contribution** if it needs new model fields/migrations, new
endpoints, a new settings key or seam, or changes a committed schema.

Litmus test: if you'd have to monkeypatch or edit code inside
`{{PKG}}/` — it's upstream. If a setting, subclass, receiver or comm
call gets you there — it's app-layer.
'''

README = '''# {{NAME_DASH}}

{{TITLE}} for the [Stapel framework](https://github.com/usestapel) —
composable Django apps that deploy as a monolith or as microservices
without changing module code.

## Install

```bash
pip install {{NAME_DASH}}
```

```python
INSTALLED_APPS = [
    # ...
    "{{PKG}}",
]

# urls.py
path("{{SLUG}}/", include("{{PKG}}.urls"))
```

## Settings

All configuration lives in the `{{NAMESPACE}}` namespace (dict setting,
flat setting, or env var — resolved lazily):

| Key | Default | Meaning |
|---|---|---|
| `GREETING` | `"pong"` | Scaffold example — replace. |

## comm surface

| Kind | Name | Contract |
|---|---|---|
| Function | `{{SLUG}}.ping` | `{}` -> `{"greeting": str}` |

## Extension points

See [MODULE.md](MODULE.md) — the agent-facing map of every fork-free seam
(settings, serializer seams, registries, comm surface).

## Development

```bash
pip install -e . && pip install pytest pytest-django ruff
./setup-hooks.sh
pytest tests/
```

## Checks

Install the pre-commit hooks once:

```bash
pip install pre-commit
pre-commit install
```

Every commit then runs `stapel-verify .` — R001-R007, SWAP001-002,
CFG001-003, URL001, ADO-codes, MIG-codes, DOC001. Run the full suite on
demand with `pre-commit run --all-files`.

## License

MIT
'''

CHANGELOG = '''# Changelog

All notable changes to {{NAME_DASH}} are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Pre-1.0 semver: **minor = breaking**, patch = compatible.

## [Unreleased]

### Added
- Initial scaffold (`stapel-new-library`).
'''

LICENSE = '''MIT License

Copyright (c) {{YEAR}} Stapel contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''

CONTRIBUTING = '''# Contributing to {{NAME_DASH}}

Part of the [Stapel framework](https://github.com/usestapel) — composable
Django apps for monolith-or-microservices deployments. The normative
package standard lives in the stapel workspace
(`docs/library-standard.md`); the short version is below.

## Dev setup

```bash
git clone https://github.com/usestapel/{{NAME_DASH}}.git && cd {{NAME_DASH}}
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all]" || pip install -e .
pip install pytest pytest-django ruff
./setup-hooks.sh   # enables the ruff pre-commit/pre-push hooks
```

## Running tests

```bash
pytest tests/
```

## Lint

```bash
ruff check . --select E,F,W --ignore E501
```

The pre-push hook runs the same command; CI rejects anything it flags.

## Design rules (the short version)

- **No new hardcoded behavior.** Anything a host project might want to
  change goes through the package's settings namespace
  (`{{NAMESPACE}}` dict; see `conf.py`) — with a dotted-path
  `import_string` escape hatch for swappable classes.
- **Modules never import each other.** Cross-module communication uses
  `stapel_core.comm`: Actions (`emit`/`@on_action`, transactional outbox),
  Functions (`call`/`@function`), Tasks for long-running work.
- **Every event/function has a JSON Schema** in `schemas/` — tests
  validate payloads against them.
- **Every seam is documented in MODULE.md** — in the same PR that adds
  or changes it.

## Commit style

Conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`); one logical
change per commit; add a CHANGELOG entry under **Unreleased**.

## Coverage policy (CI)

Two Codecov statuses with different semantics (see `codecov.yml`):

- **`codecov/project` — ratchet.** Total coverage must not drop by more
  than 0.5%.
- **`codecov/patch` — floor (80%).** New code needs tests, but a diff is
  measured against a fixed floor, not against the project average.

If a legitimately hard-to-test diff trips the floor, split the testable
part or justify in the PR — do not lower the floor in `codecov.yml`.
'''

SECURITY = '''# Security policy

## Reporting a vulnerability

Please report vulnerabilities **privately** via
[GitHub Security Advisories](https://github.com/usestapel/{{NAME_DASH}}/security/advisories/new)
— do not open public issues for security problems.

You can expect an acknowledgement within 72 hours. Please include a
minimal reproduction and the affected version/commit.

## Supported versions

The `main` branch and the latest release receive security fixes.
'''

CODE_OF_CONDUCT = '''# Code of Conduct

This project follows the
[Contributor Covenant v2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/).

In short: be respectful, assume good faith, no harassment of any kind.
Report unacceptable behavior privately to the maintainers via GitHub.
'''

CODECOV = '''# Coverage gate policy (codified in the stapel workspace,
# docs/system-design.md §7.13.5):
#
# - `project` is the RATCHET: total coverage must not degrade silently.
# - `patch` is a FLOOR, not a ratchet: new code needs solid coverage, but
#   a diff is not judged against the historical project average.
#
# Migrations and test scaffolding are excluded from coverage entirely.
coverage:
  status:
    project:
      default:
        target: auto
        threshold: 0.5%
    patch:
      default:
        target: 80%

ignore:
  - "**/migrations/**"
  - "**/tests/**"
  - "conftest.py"
  - "setup-hooks.sh"

comment:
  layout: "condensed_header, diff, files"
  require_changes: true
'''

CI_YML = '''name: CI
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip

      - name: Install stapel-core
        run: pip install git+https://github.com/usestapel/stapel-core.git

      - name: Install
        run: pip install ".[all]" || pip install .

      - name: Lint
        run: |
          pip install ruff --quiet
          ruff check . --select E,F,W --ignore E501

      - name: Import check
        run: python -c "import {{PKG}}; print('import OK')"

      - name: Install test dependencies
        run: pip install pytest pytest-django pytest-cov

      - name: Tests with coverage
        run: |
          pytest tests/ \\
            --cov={{PKG}} \\
            --cov-report=term-missing \\
            --cov-report=xml \\
            --junitxml=junit.xml \\
            -o junit_family=legacy \\
            -v

      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v5
        if: ${{ !cancelled() }}
        continue-on-error: true
        with:
          files: coverage.xml
          flags: {{NAME_DASH}}
          token: ${{ secrets.CODECOV_TOKEN }}

      - name: Upload test results to Codecov
        uses: codecov/test-results-action@v1
        if: ${{ !cancelled() }}
        continue-on-error: true
        with:
          token: ${{ secrets.CODECOV_TOKEN }}
'''

PUBLISH_YML = '''name: Publish to PyPI
on:
  push:
    tags: ["v*"]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Build
        run: |
          pip install build --quiet
          python -m build
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  publish:
    needs: build
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      - uses: pypa/gh-action-pypi-publish@release/v1
'''

PRE_COMMIT = '''#!/usr/bin/env bash
set -e
echo "Running ruff lint check..."
ruff check . --select E,F,W --ignore E501
echo "Lint check passed."
'''

PRE_PUSH = '''#!/usr/bin/env bash
set -e
echo "Running ruff lint check before push..."
ruff check . --select E,F,W --ignore E501
echo "Lint check passed."
'''

SETUP_HOOKS = '''#!/usr/bin/env bash
git config core.hooksPath .githooks
echo "Git hooks configured. Hooks directory: .githooks/"
'''

# README-canon pre-commit hooks (§57 owner directive item 5) — the standard
# `pre-commit` framework, not the bespoke .githooks/ mechanism above. Runs
# stapel-verify (R/SWAP/CFG/URL/ADO/MIG/DOC codes) — the same real gate a
# generated project's own .pre-commit-config.yaml runs, not a generic linter.
PRE_COMMIT_CONFIG = '''# Install: pip install pre-commit && pre-commit install
# Run on demand: pre-commit run --all-files
repos:
  - repo: local
    hooks:
      - id: stapel-verify
        name: stapel-verify (R/SWAP/CFG/URL/ADO/MIG/DOC codes)
        entry: stapel-verify .
        language: system
        pass_filenames: false
        always_run: true
'''

GITIGNORE = '''__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.venv/
.env
*.sqlite3
.coverage
coverage.xml
htmlcov/
junit.xml
.pytest_cache/
.ruff_cache/
.mypy_cache/

# editor / OS cruft
.DS_Store

# build/test stderr scratch
*.err
'''
