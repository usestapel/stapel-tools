"""
stapel-create-project — interactive project wizard.

Creates a complete Stapel project from scratch. Without flags, launches
an interactive wizard. All wizard steps can be bypassed via CLI flags.

Project types:
  monolith      Single Django service, Docker Compose (recommended)
  microservices Multiple services, NATS (or Kafka) bus, Docker Compose
  minimal       Single service, no Docker, SQLite, pip install only

Usage (interactive wizard):
    stapel-create-project

Usage (non-interactive):
    stapel-create-project my-app \\
        --type monolith \\
        --title "My App" \\
        --url https://myapp.com \\
        --company-name "ACME Corp" \\
        --company-email hello@myapp.com \\
        --modules auth \\
        --output-dir ~/Projects
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# Registry pins — checked against each module's workspace-local pyproject.toml
# `version` on 2026-07-11 (static-scaffold-and-config.md §1.3/§6.1, owner
# directive: raise the registry to CURRENT local versions before wiring
# assemble_scaffold, not stale ones). `ahead_of_pypi=True` means the local
# checkout carries fixes the PyPI release does not yet have (compared against
# the live `pypi.org/pypi/<name>/json` version at the same date) — publishing
# those is the module owner's act, not stapel-tools'; this registry only
# records the fact so a scaffold assembled today is documented against what it
# actually got wired against. `pin` is NOT rendered as a git ref (no v{pin}
# tag exists upstream yet for the ahead-of-PyPI modules — inventing one would
# 404 on `pip install`); it is emitted as a comment above each requirements.txt
# line so a human/CI diff can see what "current" meant at generation time.
# The "upgrade libs to a newer pin" flow (re-stamp an existing project) is
# §25/§52 scope — NOT built here; see `assemble_scaffold`'s module docstring
# for the interface point future work should hang off.
STAPEL_LIBS = {
    "core": {
        "repo": "https://github.com/usestapel/stapel-core.git",
        "dir": "stapel_core",
        "required": True,
        "description": "Core framework (required)",
        "pin": "0.10.0",
        "ahead_of_pypi": False,  # matches PyPI 0.10.0 @ 2026-07-11
    },
    "auth": {
        "repo": "https://github.com/usestapel/stapel-auth.git",
        "dir": "stapel_auth",
        "required": False,
        "description": "Authentication (JWT, OAuth, OTP)",
        "default": True,
        "pin": "0.5.4",
        "ahead_of_pypi": False,  # matches PyPI 0.5.4 @ 2026-07-11
    },
    "billing": {
        "repo": "https://github.com/usestapel/stapel-billing.git",
        "dir": "stapel_billing",
        "required": False,
        "description": "Billing & subscriptions",
        "default": False,
        "pin": "0.4.9",
        "ahead_of_pypi": False,  # matches PyPI 0.4.9 @ 2026-07-11
    },
    "cdn": {
        "repo": "https://github.com/usestapel/stapel-cdn.git",
        "dir": "stapel_cdn",
        "required": False,
        "description": "File uploads & CDN",
        "default": False,
        "pin": "0.5.1",
        "ahead_of_pypi": False,  # matches PyPI 0.5.1 @ 2026-07-11
    },
    "notifications": {
        "repo": "https://github.com/usestapel/stapel-notifications.git",
        "dir": "stapel_notifications",
        "required": False,
        "description": "Email/push notifications",
        "default": False,
        "pin": "0.3.7",
        "ahead_of_pypi": False,  # matches PyPI 0.3.7 @ 2026-07-11
    },
    "profiles": {
        "repo": "https://github.com/usestapel/stapel-profiles.git",
        "dir": "stapel_profiles",
        "required": False,
        "description": "User profiles",
        "default": False,
        "pin": "0.3.12",
        "ahead_of_pypi": False,  # matches PyPI 0.3.12 @ 2026-07-11
    },
    "translate": {
        "repo": "https://github.com/usestapel/stapel-translate.git",
        "dir": "stapel_translate",
        "required": False,
        "description": "Translations & i18n",
        "default": False,
        "pin": "0.4.7",
        "ahead_of_pypi": False,  # matches PyPI 0.4.7 @ 2026-07-11
    },
    "workspaces": {
        "repo": "https://github.com/usestapel/stapel-workspaces.git",
        "dir": "stapel_workspaces",
        "required": False,
        "description": "Workspaces & multi-tenancy",
        "default": False,
        "pin": "0.4.1",
        "ahead_of_pypi": False,  # matches PyPI 0.4.1 @ 2026-07-11
    },
    "gdpr": {
        "repo": "https://github.com/usestapel/stapel-gdpr.git",
        "dir": "stapel_gdpr",
        "required": False,
        "description": "GDPR / data export & deletion",
        "default": False,
        "pin": "0.3.5",
        "ahead_of_pypi": False,  # matches PyPI 0.3.5 @ 2026-07-11
    },
    # --- second onboarding wave (2026-07-11, static-scaffold-and-config.md §2
    # follow-up): 14 sibling checkouts brought into the registry. Dict order
    # here IS the INSTALLED_APPS/requirements.txt emission order (known_libs
    # in assemble_scaffold is normalized to registry order) — alphabetical
    # placement happens to satisfy the one real inter-module dependency
    # ("attributes" < "categories"/"listings"/"tasks"), so no manual reorder
    # was needed; a future entry with a *hard* requires that sorts AFTER its
    # dependency would need explicit reordering (see "requires" below).
    # Publish update (2026-07-11): agent, attributes, calendar, categories,
    # currencies, listings, mailtrap, recordings and tasks are now on PyPI
    # (ahead_of_pypi=False, real pins below) — geo/chat/reviews/vault/video
    # remain unpublished (editable, see each entry's comment for why).
    "agent": {
        "repo": "https://github.com/usestapel/stapel-agent.git",
        "dir": "stapel_agent",
        "required": False,
        "description": "LLM facade (complete/translate/transcribe/summarize/image)",
        "default": False,
        "pin": "0.2.7",
        "ahead_of_pypi": False,  # matches PyPI 0.2.7 @ 2026-07-11
        "http": True,
        "url_prefix": "agent/",
        "requires": [],
    },
    "attributes": {
        "repo": "https://github.com/usestapel/stapel-attributes.git",
        "dir": "stapel_attributes",
        "required": False,
        "description": "Typed attributes engine (feature-def/DTO/DAO) — L1 library",
        "default": False,
        "pin": "0.3.3",
        "ahead_of_pypi": False,  # matches PyPI 0.3.3 @ 2026-07-11
        # No models/migrations/views/urls/comm surface of its own (its own
        # __init__.py: "This is an L1 library"). Not a Django app: absent from
        # INSTALLED_APPS, mounted nowhere — a pure pip dependency for
        # categories/listings/tasks to import.
        "http": False,
        "django_app": False,
        "requires": [],
    },
    "calendar": {
        "repo": "https://github.com/usestapel/stapel-calendar.git",
        "dir": "stapel_calendar",
        "required": False,
        "description": "Calendar, events & scheduling",
        "default": False,
        "pin": "0.3.2",
        "ahead_of_pypi": False,  # matches PyPI 0.3.2 @ 2026-07-11
        "http": True,
        "url_prefix": "calendar/",
        "requires": [],
    },
    "categories": {
        "repo": "https://github.com/usestapel/stapel-categories.git",
        "dir": "stapel_categories",
        "required": False,
        "description": "Category tree & feature schema",
        "default": False,
        "pin": "0.4.1",
        "ahead_of_pypi": False,  # matches PyPI 0.4.1 @ 2026-07-11
        "http": True,
        "url_prefix": "categories/",
        "requires": ["attributes"],  # pyproject dependency, hard
    },
    "chat": {
        "repo": "https://github.com/usestapel/stapel-chat.git",
        "dir": "stapel_chat",
        "required": False,
        "description": "Chat & messaging (direct/group/support)",
        "default": False,
        "pin": "0.1.0",
        "ahead_of_pypi": True,  # not published to PyPI yet
        "http": True,
        "url_prefix": "chat/",
        "requires": [],
    },
    "currencies": {
        "repo": "https://github.com/usestapel/stapel-currencies.git",
        "dir": "stapel_currencies",
        "required": False,
        "description": "Currencies & exchange rates",
        "default": False,
        "pin": "0.1.3",
        "ahead_of_pypi": False,  # matches PyPI 0.1.3 @ 2026-07-11
        "http": True,
        "url_prefix": "currencies/",
        "requires": [],
    },
    "geo": {
        "repo": "https://github.com/usestapel/stapel-geo.git",
        "dir": "stapel_geo",
        "required": False,
        "description": "Geographic locations, geofiles & geocoding",
        "default": False,
        "pin": "0.2.1",
        # PyPI has 0.2.1, but the workspace checkout is 0.2.2 with a fix the
        # release lacks and a failing test blocks re-publishing — kept editable
        # (ahead of the last publishable release) until that test is green.
        "ahead_of_pypi": True,
        "http": True,
        "url_prefix": "geo/",
        "requires": [],
    },
    "listings": {
        "repo": "https://github.com/usestapel/stapel-listings.git",
        "dir": "stapel_listings",
        "required": False,
        "description": "Listings & catalog",
        "default": False,
        "pin": "0.3.1",
        "ahead_of_pypi": False,  # matches PyPI 0.3.1 @ 2026-07-11
        "http": True,
        "url_prefix": "listings/",
        "requires": ["attributes"],  # pyproject dependency, hard
    },
    "mailtrap": {
        "repo": "https://github.com/usestapel/stapel-mailtrap.git",
        "dir": "stapel_mailtrap",
        "required": False,
        "description": "Mail trap (dev/test outbound email capture)",
        "default": False,
        "pin": "0.1.2",
        "ahead_of_pypi": False,  # matches PyPI 0.1.2 @ 2026-07-11
        "http": True,
        "url_prefix": "mailtrap/",
        "requires": [],
    },
    "recordings": {
        "repo": "https://github.com/usestapel/stapel-recordings.git",
        "dir": "stapel_recordings",
        "required": False,
        "description": "Recording lifecycle, normalization & transcription",
        "default": False,
        "pin": "0.3.1",
        "ahead_of_pypi": False,  # matches PyPI 0.3.1 @ 2026-07-11
        "http": True,
        "url_prefix": "recordings/",
        "requires": [],
    },
    "reviews": {
        "repo": "https://github.com/usestapel/stapel-reviews.git",
        "dir": "stapel_reviews",
        "required": False,
        "description": "Reviews & ratings (target-generic)",
        "default": False,
        "pin": "0.1.0",
        "ahead_of_pypi": True,  # not published to PyPI yet
        "http": True,
        "url_prefix": "reviews/",
        "requires": [],
    },
    "tasks": {
        "repo": "https://github.com/usestapel/stapel-tasks.git",
        "dir": "stapel_tasks",
        "required": False,
        "description": "Tasks & kanban boards",
        "default": False,
        "pin": "0.1.2",
        "ahead_of_pypi": False,  # matches PyPI 0.1.2 @ 2026-07-11
        "http": True,
        "url_prefix": "tasks/",
        # attributes is a SOFT/optional seam here (features.py: import guarded
        # by attributes_available(), degrades to pass-through when absent) —
        # not declared as a hard "requires" (no pyproject dependency on it).
        "requires": [],
    },
    "vault": {
        "repo": "https://github.com/usestapel/stapel-vault.git",
        "dir": "stapel_vault",
        "required": False,
        "description": "Production secret storage (OpenBao/Vault facade)",
        "default": False,
        "pin": "0.1.0",
        "ahead_of_pypi": True,  # not published to PyPI yet
        # A facade activated via STAPEL_SECRETS_PROVIDER (env), not a Django
        # app: no models/urls of its own. Not mounted in INSTALLED_APPS.
        "http": False,
        "django_app": False,
        "requires": [],
    },
    "video": {
        "repo": "https://github.com/usestapel/stapel-video.git",
        "dir": "stapel_video",
        "required": False,
        "description": "Video calls (rooms, lobby, LiveKit-backed by default)",
        "default": False,
        "pin": "0.1.0",
        "ahead_of_pypi": True,  # not published to PyPI yet
        "http": True,
        "url_prefix": "video/",
        "requires": [],
    },
    # ── Composites (projections-and-composition §3/§4) ─────────────────────
    # A composite writes NO business logic: an INSTALLED_APPS/urls/config
    # preset over member modules + cross-domain Projection glue. Two flags
    # matter (grabli §5.8): "http": False — a composite mounts NO urls of its
    # own — but "django_app": True is REQUIRED anyway: the glue (Projection
    # declarations in the composite's app) only registers if the composite
    # itself is in INSTALLED_APPS. http=False does NOT imply django_app=False
    # here (unlike attributes/vault, which are not apps at all).
    # Composites sort AFTER their dependencies in this registry (required for
    # _expand_with_requires' registry-order output — see comment at the top).
    "shop": {
        "repo": "https://github.com/usestapel/stapel-shop.git",
        "dir": "stapel_shop",
        "required": False,
        "description": "Composite: shop — categories + attributes + listings + reviews (glue: reviews→listings rating projection); likes wait for stapel-engagement",
        "default": False,
        "pin": "0.1.0",
        "ahead_of_pypi": True,  # not published to PyPI yet
        "http": False,          # mounts no /shop/api/ — glue only
        "django_app": True,     # app slot REQUIRED: Projection glue lives there
        "url_prefix": None,
        "requires": ["categories", "attributes", "listings", "reviews"],
        # "engagement" joins requires when the module exists (minor bump).
    },
    "classified": {
        "repo": "https://github.com/usestapel/stapel-classified.git",
        "dir": "stapel_classified",
        "required": False,
        "description": "Composite: classified ads — shop + geo (coordinates are listing fields, no extra glue)",
        "default": False,
        "pin": "0.1.0",
        "ahead_of_pypi": True,  # not published to PyPI yet
        "http": False,
        "django_app": True,     # reserved glue slot (see shop comment)
        "url_prefix": None,
        "requires": ["shop", "geo"],
    },
    "booking": {
        "repo": "https://github.com/usestapel/stapel-booking.git",
        "dir": "stapel_booking",
        "required": False,
        "description": "Composite: bookable resources — calendar + listings",
        "default": False,
        "pin": "0.1.0",
        "ahead_of_pypi": True,  # not published to PyPI yet
        "http": False,
        "django_app": True,     # reserved glue slot (see shop comment)
        "url_prefix": None,
        "requires": ["calendar", "listings"],
    },
    "social": {
        "repo": "https://github.com/usestapel/stapel-social.git",
        "dir": "stapel_social",
        "required": False,
        "description": "Composite: social surface — chat + profiles + reviews; likes wait for stapel-engagement",
        "default": False,
        "pin": "0.1.0",
        "ahead_of_pypi": True,  # not published to PyPI yet
        "http": False,
        "django_app": True,     # reserved glue slot (see shop comment)
        "url_prefix": None,
        "requires": ["chat", "profiles", "reviews"],
    },
}


# Broker per project type: minimal never gets one, microservices requires one
# (services exchange events), monolith defaults to in-process + outbox — the
# outbox table already gives at-least-once without extra infra; NATS is the
# opt-in for worker isolation or a planned service split.
BROKER_DEFAULTS = {"monolith": "none", "microservices": "nats", "minimal": "none"}
BROKER_ALLOWED = {
    "monolith": ("none", "nats"),
    "microservices": ("nats", "kafka"),
    "minimal": ("none",),
}

# Dedicated broker for long-running Tasks (--task-broker). "none" means
# Tasks ride the Action transport (today's default). A monolith can keep
# Actions in-process while task.* events go through NATS to a worker
# (STAPEL_COMM["TASK_DISPATCH"]="bus"); microservices can split Tasks onto
# Kafka while events stay on NATS (STAPEL_BUS_BACKEND=routing).
TASK_BROKER_ALLOWED = {
    "monolith": ("none", "nats"),
    "microservices": ("none", "nats", "kafka"),
    "minimal": ("none",),
}


def _transports_for(broker: str) -> tuple[str, str]:
    """(ACTION_TRANSPORT, FUNCTION_TRANSPORT) for a broker choice."""
    if broker == "nats":
        return "bus", "nats"
    if broker == "kafka":
        # Kafka carries events; sync Functions fall back to internal HTTP
        # (configure FUNCTION_ROUTES) since there is no request-reply broker.
        return "bus", "http"
    return "inprocess", "inprocess"

PROJECT_TYPES = {
    "monolith": "Docker Compose monolith — single service, full infra (recommended)",
    "microservices": "Docker Compose microservices — multiple services, NATS/Kafka bus",
    "minimal": "Minimal — no Docker, SQLite, pip install only",
}


# ---------------------------------------------------------------------------
# Wizard helpers
# ---------------------------------------------------------------------------


def _ask(prompt: str, default: Optional[str] = None, required: bool = True) -> str:
    display = f"{prompt} [{default}]: " if default else f"{prompt}: "
    while True:
        value = input(display).strip()
        if not value and default is not None:
            return default
        if value or not required:
            return value
        print("  This field is required.")


def _ask_choice(prompt: str, choices: dict[str, str], default: Optional[str] = None) -> str:
    print(f"\n{prompt}")
    keys = list(choices)
    for i, key in enumerate(keys, 1):
        marker = " (recommended)" if key == default else ""
        print(f"  {i}. {choices[key]}{marker}")
    while True:
        raw = input(f"Choice [{'1' if default is None else str(keys.index(default) + 1)}]: ").strip()
        if not raw and default is not None:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(keys):
            return keys[int(raw) - 1]
        print(f"  Enter a number between 1 and {len(keys)}.")


def _ask_modules() -> list[str]:
    print("\nWhich modules to include?")
    selected = []
    for key, info in STAPEL_LIBS.items():
        if info["required"]:
            print(f"  [✓] {key:15} — {info['description']} (required)")
            selected.append(key)
        else:
            default_yes = info.get("default", False)
            hint = "Y/n" if default_yes else "y/N"
            choice = input(f"  [ ] {key:15} — {info['description']} [{hint}]: ").strip().lower()
            if (not choice and default_yes) or choice in ("y", "yes"):
                selected.append(key)
    return selected


def _ask_confirm(prompt: str) -> bool:
    answer = input(f"{prompt} [Y/n]: ").strip().lower()
    return answer in ("", "y", "yes")


# ---------------------------------------------------------------------------
# Project generation
# ---------------------------------------------------------------------------


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _add_submodule(project_dir: Path, repo: str, target_dir: str):
    result = subprocess.run(
        ["git", "submodule", "add", repo, target_dir],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"    Warning: git submodule add {target_dir} failed: {result.stderr.strip()}")
    else:
        print(f"    + submodule {target_dir}")


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9\-]", "-", name.lower()).strip("-")


def _expand_with_requires(modules: list[str]) -> list[str]:
    """Transitive closure of each module's hard ``requires`` (e.g. categories
    -> attributes), then re-ordered to STAPEL_LIBS registry order.

    A hard requires is a real pyproject dependency (categories/listings on
    attributes) — without this expansion a project that only requested
    "categories" would ship a requirements.txt whose stapel-categories
    entry declares ``stapel-attributes>=0.3,<0.4`` that pip cannot resolve
    (attributes is not published to PyPI; only a project-local requirements
    line satisfies it). Silent, deterministic, idempotent: requesting the
    dependency explicitly too changes nothing.
    """
    needed = set(modules)
    changed = True
    while changed:
        changed = False
        for key in list(needed):
            for dep in STAPEL_LIBS.get(key, {}).get("requires", []):
                if dep not in needed:
                    needed.add(dep)
                    changed = True
    return [key for key in STAPEL_LIBS if key in needed]


# ---------------------------------------------------------------------------
# Project type generators
# ---------------------------------------------------------------------------


def _write_shared_infra(project_dir: Path):
    """Configs referenced by the compose templates: nginx vhost and the
    postgres script that creates POSTGRES_MULTIPLE_DATABASES on startup."""
    from ._compose_templates import NGINX_CONF, POSTGRES_ENSURE_DATABASES

    _write(project_dir / "service-configs" / "nginx" / "nginx.conf", NGINX_CONF)
    ensure_script = project_dir / "service-configs" / "postgres" / "ensure-databases.sh"
    _write(ensure_script, POSTGRES_ENSURE_DATABASES)
    ensure_script.chmod(0o755)


def _create_monolith(project_dir: Path, ctx: dict, stapel_apps: list[str], broker: str, task_broker: str = "none", module_config: dict | None = None):
    from ._compose_templates import (
        MONOLITH_COMPOSE_BASE,
        MONOLITH_COMPOSE_DEV,
        MONOLITH_COMPOSE_LOCAL,
        MONOLITH_ENV_TEMPLATE,
        MONOLITH_GITIGNORE,
        render_compose_base,
        render_env,
    )
    from .new_service import scaffold_service

    slug = ctx["slug"]
    action_transport, function_transport = _transports_for(broker)
    # A task broker in a broker-less monolith flips only the Task dispatch:
    # Actions stay in-process, task.* events travel through the broker.
    task_dispatch = "bus" if broker == "none" and task_broker != "none" else "action"
    if task_broker == broker:
        task_broker = "none"  # same broker — nothing extra to wire

    # docker-compose files
    _write(project_dir / "docker-compose.base.yml", render_compose_base(MONOLITH_COMPOSE_BASE, broker, task_broker))
    _write(project_dir / "docker-compose.yml", MONOLITH_COMPOSE_LOCAL)
    _write(project_dir / "docker-compose.dev.yml", MONOLITH_COMPOSE_DEV)
    _write(project_dir / ".env.example", render_env(MONOLITH_ENV_TEMPLATE, broker, ctx, task_broker))
    _write(project_dir / ".gitignore", MONOLITH_GITIGNORE)
    _write(project_dir / "services.conf", f"{slug}\n")
    _write_shared_infra(project_dir)

    # Scaffold the main service with the selected feature modules wired in
    scaffold_service(
        slug=slug,
        title=ctx["title"],
        prefix="svc-",
        project_root=project_dir,
        celery=False,
        dry_run=False,
        stapel_apps=stapel_apps,
        action_transport=action_transport,
        function_transport=function_transport,
        task_dispatch=task_dispatch,
        module_config=module_config,
    )


def _create_minimal(project_dir: Path, ctx: dict, feature_modules: list[str] | None = None, module_config: dict | None = None):
    from ._minimal_templates import (
        MINIMAL_BOOT_SMOKE_SETTINGS,
        MINIMAL_CONFTEST,
        MINIMAL_ENV_EXAMPLE,
        MINIMAL_GITIGNORE,
        MINIMAL_MAKEFILE,
        MINIMAL_MANAGE,
        MINIMAL_PYPROJECT,
        MINIMAL_README,
        MINIMAL_REQUIREMENTS,
        MINIMAL_REQUIREMENTS_DEV,
        MINIMAL_SETTINGS,
        MINIMAL_TEST_SMOKE,
        MINIMAL_URLS,
    )
    from ._module_config import render_settings_block
    slug = ctx["slug"]
    module = slug.replace("-", "_")
    module_cap = "".join(p.capitalize() for p in module.split("_"))

    # Wire selected Stapel feature modules into INSTALLED_APPS + urls so the
    # dependency written into requirements.txt is actually mounted (a module
    # in requirements but absent from INSTALLED_APPS is dead weight). Each
    # module is mounted under its own /<key>/api/ prefix, mirroring how
    # stapel-example-monolith wires per-module url includes.
    feature_modules = feature_modules or []
    stapel_apps_block = "".join(
        f'\n    "{STAPEL_LIBS[key]["dir"]}",' for key in feature_modules
        if STAPEL_LIBS[key].get("django_app", True)
    )
    # Mount prefix: a lib's own registry entry wins (the newer modules already
    # bake their canonical "/<mod>/" mount — some with an internal "api/"
    # segment, some without — see each urls.py docstring); libs with no
    # "url_prefix" declared keep the legacy "<key>/api/" mount (the first 8
    # onboarded libs, whose own urls.py has no "api/" segment of its own).
    # Headless libs ("http": False, e.g. attributes/vault) get no url row at
    # all — they mount nowhere, not even bare.
    url_includes = "".join(
        f'\n    path("{STAPEL_LIBS[key].get("url_prefix", f"{key}/api/")}", '
        f'include("{STAPEL_LIBS[key]["dir"]}.urls")),'
        for key in feature_modules
        if STAPEL_LIBS[key].get("http", True)
    )

    render_ctx = {
        **ctx,
        "MODULE": module,
        "MODULE_CAP": module_cap,
        "STAPEL_APPS": stapel_apps_block,
        "STAPEL_URL_INCLUDES": url_includes,
        "STAPEL_MODULE_CONFIG": render_settings_block(module_config),
    }

    def r(s):
        for k, v in render_ctx.items():
            s = s.replace(f"{{{{{k}}}}}", str(v))
        return s

    _write(project_dir / "manage.py", r(MINIMAL_MANAGE))
    _write(project_dir / "requirements.txt", r(MINIMAL_REQUIREMENTS))
    _write(project_dir / "requirements-dev.txt", MINIMAL_REQUIREMENTS_DEV)
    _write(project_dir / ".gitignore", MINIMAL_GITIGNORE)
    _write(project_dir / "README.md", r(MINIMAL_README))
    # .env.example keeps a placeholder SECRET_KEY (committed); the shared
    # _write_env_from_ctx() call in create_project() turns it into a real
    # .env with a freshly generated secret (SEC-6), same as monolith/
    # microservices.
    _write(project_dir / ".env.example", MINIMAL_ENV_EXAMPLE)
    _write(project_dir / "config" / "__init__.py", "")
    _write(project_dir / "config" / "settings.py", r(MINIMAL_SETTINGS))
    # boot-smoke gate (R3/§44, `make boot-smoke` — part of `make controls`):
    # dummy-DB overlay over this project's own settings; see its docstring.
    _write(project_dir / "config" / "settings_boot_smoke.py", MINIMAL_BOOT_SMOKE_SETTINGS)
    _write(project_dir / "config" / "urls.py", r(MINIMAL_URLS))
    _write(project_dir / "config" / "wsgi.py",
           "import os\n\n"
           "from django.core.wsgi import get_wsgi_application\n\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')\n"
           "application = get_wsgi_application()\n")
    # apps/ is a REGULAR package (apps/__init__.py present) so INSTALLED_APPS
    # can carry the full dotted path "apps.<module>" (Django ticket #24801);
    # a namespace package there would break AppConfig discovery.
    _write(project_dir / "apps" / "__init__.py", "")
    _write(project_dir / "apps" / module / "__init__.py", "")
    _write(project_dir / "apps" / module / "models.py",
           "# Add your models here.\n# from django.db import models\n")
    _write(project_dir / "apps" / module / "views.py", "")
    _write(project_dir / "apps" / module / "urls.py", "urlpatterns = []\n")

    # Controls (G7's outbox/mailtrap integration test HARNESS is a
    # monolith/example-project concern — not shipped in minimal; see
    # _minimal_templates.MINIMAL_CONFTEST's docstring). A small non-harness
    # smoke test (AUTH_USER_MODEL + admin mount) ships instead so `make test`
    # has something real to collect out of the box.
    _write(project_dir / "Makefile", r(MINIMAL_MAKEFILE))
    _write(project_dir / "pyproject.toml", MINIMAL_PYPROJECT)
    _write(project_dir / "tests" / "__init__.py", "")
    _write(project_dir / "tests" / "conftest.py", r(MINIMAL_CONFTEST))
    _write(project_dir / "tests" / "test_project_smoke.py", MINIMAL_TEST_SMOKE)


def _create_microservices(project_dir: Path, ctx: dict, broker: str, task_broker: str = "none"):
    from ._compose_templates import (
        MICRO_COMPOSE_BASE,
        MICRO_COMPOSE_LOCAL,
        MICRO_ENV_TEMPLATE,
        MONOLITH_GITIGNORE,
        render_compose_base,
        render_env,
    )
    if task_broker == broker:
        task_broker = "none"  # same broker — nothing extra to wire
    _write(project_dir / "docker-compose.base.yml", render_compose_base(MICRO_COMPOSE_BASE, broker, task_broker))
    _write(project_dir / "docker-compose.yml", MICRO_COMPOSE_LOCAL)
    _write(project_dir / ".env.example", render_env(MICRO_ENV_TEMPLATE, broker, ctx, task_broker))
    _write(project_dir / ".gitignore", MONOLITH_GITIGNORE)
    _write(project_dir / "services.conf", "")
    _write_shared_infra(project_dir)
    print("  Created microservices base. Use 'stapel-new-service' to add services.")


# ---------------------------------------------------------------------------
# Submodule setup
# ---------------------------------------------------------------------------


def _setup_submodules(project_dir: Path, modules: list[str], is_git: bool, service_dir: str = ""):
    """Add module repos as submodules.

    stapel_core always lands at the project root (shared by all services,
    copied into images by each Dockerfile). Feature modules land INSIDE the
    service directory that uses them — the service dir is then self-contained
    and `COPY <service-dir> .` brings the apps into the image.
    """
    if not is_git:
        return
    print("\nAdding submodules...")
    for key in modules:
        info = STAPEL_LIBS[key]
        if key == "core" or not service_dir:
            target = info["dir"]
        else:
            target = f"{service_dir}/{info['dir']}"
        _add_submodule(project_dir, info["repo"], target)


def _pypi_name(info: dict) -> str:
    """The PyPI project name for a STAPEL_LIBS entry — derived from its repo
    URL (``.../usestapel/stapel-core.git`` -> ``stapel-core``), which is also
    the dash-form pyproject.toml ``name`` for every module (checked against
    each module's own pyproject.toml, not just the import ``dir``)."""
    return info["repo"].rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")


def _pin_ceiling(pin: str) -> str | None:
    """``"0.5.4"`` -> ``"0.6"`` (next-minor exclusive ceiling), mirroring the
    ``>=X.Y,<X.(Y+1)`` style each module's own pyproject.toml uses for its
    stapel-* dependencies (e.g. stapel-auth pins ``stapel-core>=0.8,<0.9``)."""
    parts = pin.split(".")
    if len(parts) < 2 or not all(p.isdigit() for p in parts[:2]):
        return None
    major, minor = int(parts[0]), int(parts[1])
    return f"{major}.{minor + 1}"


# §20 visibility gap (owner, 2026-07-11): auth is NOT split into separate
# PyPI packages — its modularity is (a) STAPEL_AUTH config axes (rendered
# into settings by _module_config.render_settings_block, untouched here) and
# (b) pip EXTRAS for the axes whose feature needs an external dependency
# (stapel-auth/pyproject.toml [project.optional-dependencies]: phone->twilio,
# oauth->social-auth-app-django, saml->lxml/signxml). Selecting an axis in
# module_config without also landing its extra in requirements.txt is
# exactly the bug this table closes: `AUTH_OAUTH_LOGIN: True` with a bare
# `stapel-auth>=...` pin installs fine but crashes at runtime (ModuleNotFound
# for the extra's dependency) the moment that feature's code path is hit.
#
# This is an EXPLICIT table, not derived from capabilities.json — the
# capabilities.json axis entries (stapel-auth/docs/capabilities.json) carry
# no axis->extra field today. Follow-up (generic mechanism, not just auth):
# teach each module's docs/capabilities.json to annotate the axes that need
# an extra (e.g. an `"extra": "oauth"` key on the axis object) and derive
# this table from that artifact for every onboarded module, the same way
# known_config_keys() already derives the axis/extension surface.
PIP_EXTRA_AXES: dict[str, dict[str, str]] = {
    "auth": {
        "AUTH_OAUTH_LOGIN": "oauth",
        "AUTH_OAUTH_REGISTRATION": "oauth",
        "AUTH_PHONE_LOGIN": "phone",
        "AUTH_PHONE_REGISTRATION": "phone",
        "AUTH_SSO_LOGIN": "saml",
    },
}


def _extras_for_lib(key: str, module_config: dict[str, dict] | None) -> list[str]:
    """Pip extras ``key``'s requirements.txt line needs, given the axes this
    project's ``module_config`` actually turned on for it.

    Deliberately reads ONLY the axes present in ``module_config`` (the same
    values ``render_settings_block`` renders into ``STAPEL_<MOD>`` — an axis
    left at its library default is not represented in the generated settings
    either, so an extra it would need is not claimed here). Sorted,
    de-duplicated; empty for a lib with no entry in ``PIP_EXTRA_AXES`` or
    when none of its dependency-bearing axes were turned on."""
    axis_map = PIP_EXTRA_AXES.get(key)
    if not axis_map or not module_config:
        return []
    config = module_config.get(key) or {}
    extras = {extra for axis, extra in axis_map.items() if config.get(axis)}
    return sorted(extras)


def _setup_pip_deps(
    project_dir: Path,
    modules: list[str],
    module_config: dict[str, dict] | None = None,
):
    reqs = project_dir / "requirements.txt"
    if not reqs.exists():
        return
    lines = reqs.read_text().splitlines()
    for key in modules:
        info = STAPEL_LIBS[key]
        pin = info.get("pin")
        pypi_name = _pypi_name(info)
        extras = _extras_for_lib(key, module_config)
        extras_suffix = f"[{','.join(extras)}]" if extras else ""
        if info.get("ahead_of_pypi"):
            # NOT resolvable from PyPI at this pin today (owner publishes
            # separately — some of these have no PyPI release at all yet).
            # Rendering a `name @ git+https://...` line here would look like
            # a working pin when it is not one: no vX.Y.Z tag exists upstream
            # for these local-only fixes, so `pip install` either 404s or
            # silently resolves an OLDER published version than what this
            # project was actually generated against. The honest, actually
            # -installable-today option is an editable install of the
            # sibling workspace checkout (this project's own directory is a
            # sibling of stapel-core/, stapel-auth/, ... under the same
            # workspace root) — NOT a claim that a pip/PyPI install works.
            # Extras compose the same way on an editable local install
            # (`-e ../stapel-auth[oauth]`) as on a published one.
            entry = f"-e ../{pypi_name}{extras_suffix}"
            note = (
                f"# {info['dir']} — v{pin} is NOT YET installable from PyPI "
                "(workspace-local, ahead of the last published release or "
                "not published at all; owner publishes separately). Editable "
                "install from the sibling checkout below; once published, "
                f"replace with: {pypi_name}{extras_suffix}>={pin},<{_pin_ceiling(pin) or '?'}"
            )
        else:
            ceiling = _pin_ceiling(pin) if pin else None
            entry = (
                f"{pypi_name}{extras_suffix}>={pin},<{ceiling}"
                if pin and ceiling else f"{pypi_name}{extras_suffix}"
            )
            note = f"# {info['dir']} — matches last-published PyPI v{pin}"
        if entry not in lines:
            lines.append(note)
            lines.append(entry)
    reqs.write_text("\n".join(lines) + "\n")


def _write_env_from_ctx(project_dir: Path, ctx: dict):
    env_example = project_dir / ".env.example"
    if not env_example.exists():
        return
    env = project_dir / ".env"
    if not env.exists():
        # .env.example keeps placeholders (it gets committed); the real .env
        # gets freshly generated secrets.
        text = env_example.read_text()
        text = text.replace(
            "SECRET_KEY=change_me_to_a_long_random_string",
            f"SECRET_KEY={_random_secret()}",
        )
        text = text.replace(
            "JWT_SECRET_KEY=change_me_to_another_long_random_string",
            f"JWT_SECRET_KEY={_random_secret()}",
        )
        text = text.replace(
            "POSTGRES_PASSWORD=change_me",
            f"POSTGRES_PASSWORD={_random_secret(24)}",
        )
        env.write_text(text)
        print("  created .env from .env.example with generated secrets")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def create_project(
    name: str,
    project_type: str,
    title: str,
    url: str,
    company_name: str,
    company_email: str,
    modules: list[str],
    output_dir: Path,
    use_submodules: bool = True,
    init_git: bool = True,
    broker: str | None = None,
    task_broker: str | None = None,
    module_config: dict[str, dict] | None = None,
):
    if not re.fullmatch(r"[a-zA-Z0-9_\-]+", name):
        print("Error: project name must contain only letters, numbers, dashes, underscores", file=sys.stderr)
        sys.exit(1)

    broker = broker or BROKER_DEFAULTS[project_type]
    if broker not in BROKER_ALLOWED[project_type]:
        allowed = ", ".join(BROKER_ALLOWED[project_type])
        print(
            f"Error: broker '{broker}' is not valid for {project_type} projects "
            f"(allowed: {allowed})",
            file=sys.stderr,
        )
        sys.exit(1)

    task_broker = task_broker or "none"
    if task_broker not in TASK_BROKER_ALLOWED[project_type]:
        allowed = ", ".join(TASK_BROKER_ALLOWED[project_type])
        print(
            f"Error: task broker '{task_broker}' is not valid for {project_type} "
            f"projects (allowed: {allowed})",
            file=sys.stderr,
        )
        sys.exit(1)

    if module_config:
        if project_type == "microservices":
            print(
                "Error: --module-config applies to monolith/minimal projects; "
                "for microservices pass it to 'stapel-new-service' per service",
                file=sys.stderr,
            )
            sys.exit(1)
        from ._module_config import validate_module_config

        # Fail before any file is written: unknown module, or a key outside the
        # module's capabilities.json axes+extension surface, is a hard error.
        validate_module_config(module_config, selected=[*modules, "core"])

    slug = _slugify(name)
    project_dir = output_dir / name

    if project_dir.exists():
        print(f"Error: {project_dir} already exists", file=sys.stderr)
        sys.exit(1)

    print(f"\nCreating {project_type} project '{name}' in {project_dir}/")
    project_dir.mkdir(parents=True)

    # Init git first so submodule add works
    is_git = False
    if init_git:
        result = subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
        is_git = result.returncode == 0
        if is_git:
            print("  git init")

    clean_url = url.rstrip("/")
    domain = urlparse(clean_url).netloc or clean_url
    ctx = {
        "name": name,
        "slug": slug,
        "title": title,
        "url": clean_url,
        "domain": domain,
        "company_name": company_name,
        "company_email": company_email,
        "service_dir_name": f"svc-{slug}",
    }

    # Ensure core is always first, then close over any hard "requires" (a lib
    # requested without a dependency it hard-needs would ship a broken pip
    # install — see _expand_with_requires).
    feature_only = _expand_with_requires([m for m in modules if m != "core"])
    modules = ["core", *feature_only]

    feature_apps = [
        STAPEL_LIBS[key]["dir"] for key in modules
        if key != "core" and STAPEL_LIBS[key].get("django_app", True)
    ]

    # Generate project structure
    if project_type == "monolith":
        _create_monolith(project_dir, ctx, stapel_apps=feature_apps, broker=broker, task_broker=task_broker, module_config=module_config)
    elif project_type == "minimal":
        _create_minimal(project_dir, ctx, feature_modules=[k for k in modules if k != "core"], module_config=module_config)
        use_submodules = False  # minimal uses pip
    elif project_type == "microservices":
        _create_microservices(project_dir, ctx, broker=broker, task_broker=task_broker)
        if len(modules) > 1:
            print(
                "  Note: feature modules are wired per-service. After "
                "'stapel-new-service <name> --stapel-apps stapel_<mod>', add the "
                "module as a submodule inside the service dir."
            )
            modules = ["core"]  # only shared core lands at the project root

    # Wire in stapel libraries
    if use_submodules and is_git:
        service_dir = ctx["service_dir_name"] if project_type == "monolith" else ""
        _setup_submodules(project_dir, modules, is_git, service_dir=service_dir)
    else:
        _setup_pip_deps(project_dir, modules, module_config)

    _write_env_from_ctx(project_dir, ctx)

    if is_git and (project_dir / ".gitmodules").exists():
        subprocess.run(["git", "add", "."], cwd=project_dir, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "chore: initialize project"],
            cwd=project_dir, capture_output=True,
        )
        print("  initial commit created")

    print(f"\nProject '{name}' created at {project_dir}/")
    # .env was already created from .env.example with freshly generated
    # secrets (SEC-6) — don't tell the user to re-copy .env.example over it,
    # that would clobber the generated secrets with the shipped placeholders.
    if project_type == "minimal":
        print("  cd", project_dir)
        print("  python -m venv .venv && source .venv/bin/activate")
        print("  pip install -r requirements.txt")
        print("  python manage.py migrate && python manage.py runserver")
    else:
        print("  cd", project_dir)
        print("  # .env already created with generated secrets (SECRET_KEY, "
              "JWT_SECRET_KEY, POSTGRES_PASSWORD) — review, then:")
        print("  docker compose up -d")


def _random_secret(length: int = 64) -> str:
    import secrets
    import string
    # Letters and digits only: values land in .env files, where '#', '$'
    # and quotes are unsafe.
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def run_wizard() -> dict:
    print("\n" + "=" * 60)
    print("  stapel-create-project wizard")
    print("=" * 60)

    name = _ask("Project name (slug)", required=True)
    while not re.fullmatch(r"[a-zA-Z0-9_\-]+", name):
        print("  Use only letters, numbers, dashes, underscores.")
        name = _ask("Project name (slug)", required=True)

    title = _ask("Display name", default=" ".join(w.capitalize() for w in name.replace("-", " ").replace("_", " ").split()))
    project_type = _ask_choice("Project type", PROJECT_TYPES, default="monolith")

    broker = BROKER_DEFAULTS[project_type]
    if project_type == "microservices":
        broker = _ask_choice(
            "Event/RPC broker",
            {
                "nats": "NATS — JetStream events + request-reply RPC, one tiny binary",
                "kafka": "Kafka — heavyweight; pick for hard retention/replay needs",
            },
            default="nats",
        )
    elif project_type == "monolith":
        broker = _ask_choice(
            "Communication transport",
            {
                "none": "In-process + outbox — no broker, same delivery guarantees",
                "nats": "NATS — isolate event handlers in a worker / prepare a service split",
            },
            default="none",
        )

    # Task broker — asked only where the answer differs from the event broker
    # choice in a meaningful way.
    task_broker = "none"
    if project_type == "monolith" and broker == "none":
        task_broker = _ask_choice(
            "Task broker (broker for long-running Tasks)?",
            {
                "none": "none (in-process) — Tasks run where the requested-event lands",
                "nats": "NATS — task.* events go through a broker to a dedicated worker",
            },
            default="none",
        )
    elif project_type == "microservices" and broker != "kafka":
        task_broker = _ask_choice(
            "Task broker?",
            {
                "none": f"same as events ({broker})",
                "kafka": "Kafka — dedicated broker for task.* (hard retention/replay)",
            },
            default="none",
        )
    url = _ask("Site URL", default=f"https://{_slugify(name)}.com")
    company_name = _ask("Company / sender name for emails", default=title)
    company_email = _ask("Company email address", default=f"hello@{_slugify(name)}.com")

    modules: list[str] = []
    if project_type != "minimal":
        modules = _ask_modules()
    else:
        # minimal always gets core + whatever user picks, but no submodules
        print("\nMinimal project uses pip, not submodules. Core is always included.")
        modules = ["core"]
        for key, info in STAPEL_LIBS.items():
            if info["required"]:
                continue
            choice = input(f"  Include {key} ({info['description']})? [y/N]: ").strip().lower()
            if choice in ("y", "yes"):
                modules.append(key)

    print()
    return {
        "name": name,
        "project_type": project_type,
        "title": title,
        "url": url,
        "company_name": company_name,
        "company_email": company_email,
        "modules": modules,
        "broker": broker,
        "task_broker": task_broker,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", nargs="?", help="Project name / slug")
    parser.add_argument("--type", dest="project_type", choices=list(PROJECT_TYPES), help="Project type")
    parser.add_argument("--title", help="Display name")
    parser.add_argument("--url", help="Site URL")
    parser.add_argument("--company-name", help="Company name for emails")
    parser.add_argument("--company-email", help="Company email address")
    parser.add_argument(
        "--modules", nargs="+",
        choices=list(STAPEL_LIBS), metavar="MODULE",
        help="Modules to include (besides core which is always included). "
             f"Available: {', '.join(k for k, v in STAPEL_LIBS.items() if not v['required'])}",
    )
    parser.add_argument(
        "--broker", choices=["none", "nats", "kafka"],
        help="Event/RPC broker: minimal=none; monolith=none (default) or nats; "
             "microservices=nats (default) or kafka",
    )
    parser.add_argument(
        "--task-broker", choices=["none", "nats", "kafka"],
        help="Dedicated broker for long-running Tasks (default: none — Tasks "
             "ride the Action transport). monolith: nats keeps Actions "
             "in-process while task.* goes through NATS to a worker; "
             "microservices: a broker differing from --broker routes task.* "
             "to it via STAPEL_BUS_BACKEND=routing; minimal: none only",
    )
    parser.add_argument(
        "--module-config", type=Path, metavar="PATH",
        help="JSON file {module: {SETTING_KEY: value}} rendered as "
             "STAPEL_<MOD> = {...} blocks in the generated settings "
             "(non-default capability axes only, e.g. from the CTO brief; "
             "keys validated against the module's docs/capabilities.json "
             "when a sibling checkout has one). Monolith/minimal only.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path.cwd(), help="Parent directory for the project")
    parser.add_argument("--no-submodules", action="store_true", help="Use pip install instead of git submodules")
    parser.add_argument("--no-git", action="store_true", help="Skip git init")
    args = parser.parse_args()

    module_config = None
    if args.module_config:
        from ._module_config import load_module_config_file

        module_config = load_module_config_file(args.module_config)

    # Determine if wizard is needed
    non_interactive = (
        args.name
        and args.project_type
        and args.title
        and args.url
        and args.company_name
        and args.company_email
        and args.modules is not None
    )

    if non_interactive:
        params = {
            "name": args.name,
            "project_type": args.project_type,
            "title": args.title,
            "url": args.url,
            "company_name": args.company_name,
            "company_email": args.company_email,
            "modules": args.modules or [],
            "broker": args.broker,
            "task_broker": args.task_broker,
        }
    else:
        # Wizard — pre-fill known values, ask for the rest
        try:
            params = run_wizard()
            # Override with any CLI args that were explicitly provided
            if args.name:
                params["name"] = args.name
            if args.project_type:
                params["project_type"] = args.project_type
            if args.title:
                params["title"] = args.title
            if args.url:
                params["url"] = args.url
            if args.company_name:
                params["company_name"] = args.company_name
            if args.company_email:
                params["company_email"] = args.company_email
            if args.modules:
                params["modules"] = args.modules
            if args.broker:
                params["broker"] = args.broker
            if args.task_broker:
                params["task_broker"] = args.task_broker
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(0)

    create_project(
        name=params["name"],
        project_type=params["project_type"],
        title=params["title"],
        url=params["url"],
        company_name=params["company_name"],
        company_email=params["company_email"],
        modules=params["modules"],
        output_dir=args.output_dir,
        use_submodules=not args.no_submodules,
        init_git=not args.no_git,
        broker=params.get("broker"),
        task_broker=params.get("task_broker"),
        module_config=module_config,
    )


if __name__ == "__main__":
    main()
