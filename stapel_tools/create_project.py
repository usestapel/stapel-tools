"""
stapel-create-project — interactive project wizard.

Creates a complete Stapel project from scratch. Without flags, launches
an interactive wizard. All wizard steps can be bypassed via CLI flags.

Project types:
  monolith      Single Django service, Docker Compose (recommended)
  microservices Multiple services, shared Kafka bus, Docker Compose
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
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Optional

STAPEL_LIBS = {
    "core": {
        "repo": "https://github.com/usestapel/stapel-core.git",
        "dir": "stapel_core",
        "required": True,
        "description": "Core framework (required)",
    },
    "auth": {
        "repo": "https://github.com/usestapel/stapel-auth.git",
        "dir": "stapel_auth",
        "required": False,
        "description": "Authentication (JWT, OAuth, OTP)",
        "default": True,
    },
    "billing": {
        "repo": "https://github.com/usestapel/stapel-billing.git",
        "dir": "stapel_billing",
        "required": False,
        "description": "Billing & subscriptions",
        "default": False,
    },
    "cdn": {
        "repo": "https://github.com/usestapel/stapel-cdn.git",
        "dir": "stapel_cdn",
        "required": False,
        "description": "File uploads & CDN",
        "default": False,
    },
    "notifications": {
        "repo": "https://github.com/usestapel/stapel-notifications.git",
        "dir": "stapel_notifications",
        "required": False,
        "description": "Email/push notifications",
        "default": False,
    },
    "profiles": {
        "repo": "https://github.com/usestapel/stapel-profiles.git",
        "dir": "stapel_profiles",
        "required": False,
        "description": "User profiles",
        "default": False,
    },
    "translate": {
        "repo": "https://github.com/usestapel/stapel-translate.git",
        "dir": "stapel_translate",
        "required": False,
        "description": "Translations & i18n",
        "default": False,
    },
    "workspaces": {
        "repo": "https://github.com/usestapel/stapel-workspaces.git",
        "dir": "stapel_workspaces",
        "required": False,
        "description": "Workspaces & multi-tenancy",
        "default": False,
    },
    "gdpr": {
        "repo": "https://github.com/usestapel/stapel-gdpr.git",
        "dir": "stapel_gdpr",
        "required": False,
        "description": "GDPR / data export & deletion",
        "default": False,
    },
}

PROJECT_TYPES = {
    "monolith": "Docker Compose monolith — single service, full infra (recommended)",
    "microservices": "Docker Compose microservices — multiple services, Kafka bus",
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


# ---------------------------------------------------------------------------
# Project type generators
# ---------------------------------------------------------------------------


def _create_monolith(project_dir: Path, ctx: dict):
    from .new_service import scaffold_service
    from ._compose_templates import (
        MONOLITH_COMPOSE_BASE,
        MONOLITH_COMPOSE_LOCAL,
        MONOLITH_COMPOSE_DEV,
        MONOLITH_ENV_TEMPLATE,
        MONOLITH_GITIGNORE,
    )

    slug = ctx["slug"]
    dir_name = ctx["service_dir_name"]  # e.g. "svc-app" or just "app"

    # docker-compose files
    _write(project_dir / "docker-compose.base.yml", MONOLITH_COMPOSE_BASE)
    _write(project_dir / "docker-compose.yml", MONOLITH_COMPOSE_LOCAL)
    _write(project_dir / "docker-compose.dev.yml", MONOLITH_COMPOSE_DEV)
    _write(project_dir / ".env.example", MONOLITH_ENV_TEMPLATE.format(**ctx))
    _write(project_dir / ".gitignore", MONOLITH_GITIGNORE)
    _write(project_dir / "services.conf", f"{slug}\n")

    # Scaffold the main service
    scaffold_service(
        slug=slug,
        title=ctx["title"],
        prefix="svc-",
        project_root=project_dir,
        celery=False,
        dry_run=False,
    )


def _create_minimal(project_dir: Path, ctx: dict):
    from ._minimal_templates import (
        MINIMAL_MANAGE,
        MINIMAL_SETTINGS,
        MINIMAL_URLS,
        MINIMAL_REQUIREMENTS,
        MINIMAL_GITIGNORE,
        MINIMAL_README,
    )
    slug = ctx["slug"]
    module = slug.replace("-", "_")
    module_cap = "".join(p.capitalize() for p in module.split("_"))

    render_ctx = {**ctx, "MODULE": module, "MODULE_CAP": module_cap}

    def r(s):
        for k, v in render_ctx.items():
            s = s.replace(f"{{{{{k}}}}}", str(v))
        return s

    _write(project_dir / "manage.py", r(MINIMAL_MANAGE))
    _write(project_dir / "requirements.txt", r(MINIMAL_REQUIREMENTS))
    _write(project_dir / ".gitignore", MINIMAL_GITIGNORE)
    _write(project_dir / "README.md", r(MINIMAL_README))
    _write(project_dir / "core" / "__init__.py", "")
    _write(project_dir / "core" / "settings.py", r(MINIMAL_SETTINGS))
    _write(project_dir / "core" / "urls.py", r(MINIMAL_URLS))
    _write(project_dir / "core" / "wsgi.py",
           "import os\nfrom django.core.wsgi import get_wsgi_application\n"
           "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')\n"
           "application = get_wsgi_application()\n")
    _write(project_dir / "apps" / module / "__init__.py", "")
    _write(project_dir / "apps" / module / "models.py", "from django.db import models\n")
    _write(project_dir / "apps" / module / "views.py", "")
    _write(project_dir / "apps" / module / "urls.py", "urlpatterns = []\n")


def _create_microservices(project_dir: Path, ctx: dict):
    from ._compose_templates import (
        MICRO_COMPOSE_BASE,
        MICRO_COMPOSE_LOCAL,
        MICRO_ENV_TEMPLATE,
        MONOLITH_GITIGNORE,
    )
    _write(project_dir / "docker-compose.base.yml", MICRO_COMPOSE_BASE)
    _write(project_dir / "docker-compose.yml", MICRO_COMPOSE_LOCAL)
    _write(project_dir / ".env.example", MICRO_ENV_TEMPLATE.format(**ctx))
    _write(project_dir / ".gitignore", MONOLITH_GITIGNORE)
    _write(project_dir / "services.conf", "")
    print("  Created microservices base. Use 'stapel-new-service' to add services.")


# ---------------------------------------------------------------------------
# Submodule setup
# ---------------------------------------------------------------------------


def _setup_submodules(project_dir: Path, modules: list[str], is_git: bool):
    if not is_git:
        return
    print("\nAdding submodules...")
    for key in modules:
        info = STAPEL_LIBS[key]
        _add_submodule(project_dir, info["repo"], info["dir"])


def _setup_pip_deps(project_dir: Path, modules: list[str]):
    reqs = project_dir / "requirements.txt"
    if not reqs.exists():
        return
    lines = reqs.read_text().splitlines()
    for key in modules:
        info = STAPEL_LIBS[key]
        entry = f"{info['dir']} @ git+{info['repo']}"
        if entry not in lines:
            lines.append(entry)
    reqs.write_text("\n".join(lines) + "\n")


def _write_env_from_ctx(project_dir: Path, ctx: dict):
    env_example = project_dir / ".env.example"
    if not env_example.exists():
        return
    env = project_dir / ".env"
    if not env.exists():
        env.write_text(env_example.read_text())
        print("  created .env from .env.example (fill in secrets before running)")


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
):
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

    ctx = {
        "name": name,
        "slug": slug,
        "title": title,
        "url": url.rstrip("/"),
        "company_name": company_name,
        "company_email": company_email,
        "service_dir_name": f"svc-{slug}",
        "SECRET_KEY": _random_secret(),
    }

    # Ensure core is always first
    if "core" not in modules:
        modules = ["core"] + modules

    # Generate project structure
    if project_type == "monolith":
        _create_monolith(project_dir, ctx)
    elif project_type == "minimal":
        _create_minimal(project_dir, ctx)
        use_submodules = False  # minimal uses pip
    elif project_type == "microservices":
        _create_microservices(project_dir, ctx)

    # Wire in stapel libraries
    if use_submodules and is_git:
        _setup_submodules(project_dir, modules, is_git)
    else:
        _setup_pip_deps(project_dir, modules)

    _write_env_from_ctx(project_dir, ctx)

    if is_git and (project_dir / ".gitmodules").exists():
        subprocess.run(["git", "add", "."], cwd=project_dir, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "chore: initialize project"],
            cwd=project_dir, capture_output=True,
        )
        print("  initial commit created")

    print(f"\nProject '{name}' created at {project_dir}/")
    if project_type == "minimal":
        print("  cd", project_dir)
        print("  python -m venv .venv && source .venv/bin/activate")
        print("  pip install -r requirements.txt")
        print("  python manage.py migrate && python manage.py runserver")
    else:
        print("  cd", project_dir)
        print("  cp .env.example .env  # fill in secrets")
        print("  docker compose up -d")


def _random_secret(length: int = 50) -> str:
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*(-_=+)"
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
    parser.add_argument("--output-dir", type=Path, default=Path.cwd(), help="Parent directory for the project")
    parser.add_argument("--no-submodules", action="store_true", help="Use pip install instead of git submodules")
    parser.add_argument("--no-git", action="store_true", help="Skip git init")
    args = parser.parse_args()

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
    )


if __name__ == "__main__":
    main()
