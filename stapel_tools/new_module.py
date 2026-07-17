"""
stapel-new-module — scaffold a new Django app module inside a service.

Creates: apps/{name}/ with models, views, serializers, dto, errors, urls, admin, tests.
Must be run from inside a service directory (the one with manage.py).

Usage:
    cd svc-auth/
    stapel-new-module users
    stapel-new-module billing --title "Billing"
    stapel-new-module billing --apps-dir custom_apps/
"""

import argparse
import re
import sys
from pathlib import Path

from ._templates import (
    MODULE_ADMIN,
    MODULE_APPS,
    MODULE_DTO,
    MODULE_ERRORS,
    MODULE_INIT,
    MODULE_MODELS,
    MODULE_PRESENTERS,
    MODULE_SERIALIZERS,
    MODULE_TEST_MODELS,
    MODULE_TESTS_INIT,
    MODULE_URLS,
    MODULE_URLS_V1,
    MODULE_VIEWS,
)


def render(template: str, ctx: dict) -> str:
    result = template
    for key, value in ctx.items():
        result = result.replace(f"{{{{{key}}}}}", value)
    return result


def find_service_root(start: Path) -> Path:
    current = start.resolve()
    for _ in range(4):
        if (current / "manage.py").exists():
            return current
        if current.parent == current:
            break
        current = current.parent
    return start.resolve()


def scaffold_module(
    slug: str,
    title: str,
    service_dir: Path,
    apps_dir_name: str = "apps",
):
    module = slug.replace("-", "_")
    module_cap = "".join(p.capitalize() for p in module.split("_"))
    app_path = f"{apps_dir_name}.{module}" if apps_dir_name else module

    ctx = {
        "TITLE": title,
        "SLUG": slug,
        "MODULE": module,
        "MODULE_CAP": module_cap,
        "MODULE_UPPER": module.upper(),
        "APP_PATH": app_path,
    }

    target = service_dir / apps_dir_name / module
    if target.exists():
        print(f"Error: {target} already exists", file=sys.stderr)
        sys.exit(1)

    files = {
        # apps/ must be a REGULAR package so INSTALLED_APPS can carry the full
        # dotted path "apps.<module>" (Django ticket #24801). Freshly generated
        # services already ship apps/__init__.py; write it defensively so this
        # also works in a service scaffolded before that was the default.
        service_dir / apps_dir_name / "__init__.py": "",
        target / "__init__.py": MODULE_INIT,
        target / "apps.py": render(MODULE_APPS, ctx),
        target / "models.py": render(MODULE_MODELS, ctx),
        target / "admin.py": render(MODULE_ADMIN, ctx),
        target / "dto.py": render(MODULE_DTO, ctx),
        target / "serializers.py": render(MODULE_SERIALIZERS, ctx),
        # Presenter-canonical from birth (§55): the DTO is only instantiated
        # in presenters.py; views consume get_<module>_presenter().
        target / "presenters.py": render(MODULE_PRESENTERS, ctx),
        target / "errors.py": render(MODULE_ERRORS, ctx),
        target / "views.py": render(MODULE_VIEWS, ctx),
        target / "urls.py": render(MODULE_URLS, ctx),
        target / "urls_v1.py": render(MODULE_URLS_V1, ctx),
        target / "tests" / "__init__.py": MODULE_TESTS_INIT,
        target / "tests" / "test_models.py": render(MODULE_TEST_MODELS, ctx),
    }

    print(f"Creating module {app_path} in {service_dir}/")
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            print(f"  skipped (exists): {path.relative_to(service_dir)}")
            continue
        path.write_text(content, encoding="utf-8")
        print(f"  created {path.relative_to(service_dir)}")

    print(f"\nDone. Add '{app_path}' to INSTALLED_APPS in config/settings/base.py")
    print(f"Then include urls: path('{slug}/', include('{app_path}.urls'))")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="Module slug, e.g. 'users' or 'billing-plans'")
    parser.add_argument("--title", help="Display name, e.g. 'User Management'")
    parser.add_argument("--service-dir", type=Path, help="Service root directory (default: auto-detect from CWD)")
    parser.add_argument("--apps-dir", default="apps", help="Apps subdirectory inside service (default: apps)")
    args = parser.parse_args()

    slug = args.name
    if not re.fullmatch(r"[a-z0-9_\-]+", slug):
        print("Error: name must be lowercase alphanumeric with optional dashes/underscores", file=sys.stderr)
        sys.exit(1)

    service_dir = args.service_dir or find_service_root(Path.cwd())
    title = args.title or " ".join(p.capitalize() for p in slug.replace("-", " ").replace("_", " ").split())

    scaffold_module(slug, title, service_dir, args.apps_dir)


if __name__ == "__main__":
    main()
