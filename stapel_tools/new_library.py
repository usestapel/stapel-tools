"""
stapel-new-library — scaffold a standalone stapel-* package repository.

Materializes the Stapel library standard (docs/library-standard.md in the
stapel workspace): flat-layout packaging, STAPEL_<NAME> settings namespace,
comm surface with JSON schemas, serializer seams, MODULE.md, community
files, CI with the codecov ratchet/floor policy, ruff git hooks.

Two kinds:

- ``module`` (default) — an L2 service-capable Django app: models/views/
  urls/comm surface. Modules never import each other.
- ``library`` — an L1 importable package (like stapel-attributes): no
  models, views, urls or comm surface of its own.

Usage:
    stapel-new-library search
    stapel-new-library attributes --kind library --title "Typed attributes"
    stapel-new-library search --dir ~/Projects/stapel --no-git
"""

import argparse
import datetime
import re
import stat
import subprocess
import sys
from pathlib import Path

from . import _library_templates as T


def render(template: str, ctx: dict) -> str:
    result = template
    for key, value in ctx.items():
        result = result.replace(f"{{{{{key}}}}}", value)
    return result


def build_context(slug: str, title: str) -> dict:
    slug_u = slug.replace("-", "_")
    return {
        "SLUG": slug,
        "SLUG_U": slug_u,
        "PKG": f"stapel_{slug_u}",
        "NAME_DASH": f"stapel-{slug}",
        "NAMESPACE": f"STAPEL_{slug_u.upper()}",
        "TITLE": title,
        "CAMEL": "".join(p.capitalize() for p in slug_u.split("_")),
        "YEAR": str(datetime.date.today().year),
    }


def file_plan(kind: str, ctx: dict) -> dict:
    """Relative path -> rendered content for the chosen kind."""
    module_only = kind == "module"

    # Tests are deliberately NOT listed as a shipped package: they must not
    # land in the wheel/sdist (top-tier hygiene). The flat-layout editable
    # install still resolves ``{{PKG}}.tests.urls`` for the test ROOT_URLCONF.
    packages = [ctx["PKG"]]
    package_data_extra = ""
    if module_only:
        packages.append(f"{ctx['PKG']}.migrations")
        package_data_extra = ', "migrations/*.py", "schemas/**/*.json"'
    ctx = {
        **ctx,
        "PACKAGES": ", ".join(f'"{p}"' for p in packages),
        "PACKAGE_DATA_EXTRA": package_data_extra,
    }

    plan = {
        "pyproject.toml": render(T.PYPROJECT, ctx),
        "__init__.py": render(T.INIT, ctx),
        "conf.py": render(T.CONF, ctx),
        "conftest.py": render(T.CONFTEST, ctx),
        "py.typed": "",
        "tests/__init__.py": T.TESTS_INIT,
        "tests/test_public_api.py": render(T.TEST_PUBLIC_API, ctx),
        "MODULE.md": render(T.MODULE_MD, ctx),
        "README.md": render(T.README, ctx),
        "CHANGELOG.md": render(T.CHANGELOG, ctx),
        "LICENSE": render(T.LICENSE, ctx),
        "CONTRIBUTING.md": render(T.CONTRIBUTING, ctx),
        "SECURITY.md": render(T.SECURITY, ctx),
        "CODE_OF_CONDUCT.md": render(T.CODE_OF_CONDUCT, ctx),
        "codecov.yml": T.CODECOV,
        ".github/workflows/ci.yml": render(T.CI_YML, ctx),
        ".github/workflows/publish.yml": T.PUBLISH_YML,
        ".githooks/pre-commit": T.PRE_COMMIT,
        ".githooks/pre-push": T.PRE_PUSH,
        "setup-hooks.sh": T.SETUP_HOOKS,
        ".gitignore": T.GITIGNORE,
        # README-canon pre-commit hooks (§57 owner directive item 5): the
        # standard `pre-commit` framework, running the REAL stapel gate
        # (stapel-verify) — not a generic linter. Separate from the
        # .githooks/setup-hooks.sh mechanism above (which stays, ruff-only,
        # for repos that don't want the pre-commit framework dependency).
        ".pre-commit-config.yaml": T.PRE_COMMIT_CONFIG,
    }

    if module_only:
        plan.update(
            {
                "apps.py": render(T.APPS, ctx),
                "models.py": render(T.MODELS, ctx),
                "migrations/__init__.py": "",
                "dto.py": render(T.DTO, ctx),
                "serializers.py": render(T.SERIALIZERS, ctx),
                "views.py": render(T.VIEWS, ctx),
                "urls.py": render(T.URLS, ctx),
                "urls_v1.py": render(T.URLS_V1, ctx),
                "errors.py": render(T.ERRORS, ctx),
                "checks.py": render(T.CHECKS, ctx),
                "functions.py": render(T.FUNCTIONS, ctx),
                f"schemas/functions/{ctx['SLUG']}.ping.json": render(
                    T.SCHEMA_PING, ctx
                ),
                "tests/urls.py": render(T.TESTS_URLS, ctx),
                "tests/test_ping.py": render(T.TEST_PING, ctx),
            }
        )
    return plan


EXECUTABLE = {".githooks/pre-commit", ".githooks/pre-push", "setup-hooks.sh"}


def scaffold_library(
    slug: str,
    title: str,
    parent_dir: Path,
    kind: str = "module",
    git: bool = True,
) -> Path:
    ctx = build_context(slug, title)
    target = parent_dir / ctx["NAME_DASH"]
    if target.exists():
        print(f"Error: {target} already exists", file=sys.stderr)
        sys.exit(1)

    print(f"Creating {kind} {ctx['NAME_DASH']} in {parent_dir}/")
    for rel, content in file_plan(kind, ctx).items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if rel in EXECUTABLE:
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        print(f"  created {rel}")

    if git:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=target, check=True)
        subprocess.run(
            ["git", "config", "core.hooksPath", ".githooks"], cwd=target, check=True
        )
        print("  git init (main) + hooksPath configured")

    print(
        f"\nDone. Next steps:\n"
        f"  cd {ctx['NAME_DASH']}\n"
        f"  pip install -e . && pip install pytest pytest-django ruff\n"
        f"  pytest tests/   # scaffold suite is green out of the box\n"
        f"  # replace the ping example with your domain; keep MODULE.md in sync\n"
        f"  # BEFORE the first vX.Y.Z tag: register a PyPI *pending* trusted publisher\n"
        f"  #   (owner usestapel, this repo, workflow publish.yml, env pypi),\n"
        f"  #   otherwise publish.yml fails with invalid-publisher\n"
    )
    if kind == "module":
        print(
            "Reminder: real models need `makemigrations` and the ping "
            "example (dto/views/functions/schemas) is meant to be replaced."
        )
    return target


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("name", help="Package slug, e.g. 'search' -> stapel-search")
    parser.add_argument("--title", help="Display name, e.g. 'Search and ranking'")
    parser.add_argument(
        "--kind",
        choices=("module", "library"),
        default="module",
        help="module = service-capable Django app (default); "
        "library = importable L1 package without models/views/comm",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path.cwd(),
        help="Parent directory for the new repo (default: CWD)",
    )
    parser.add_argument(
        "--no-git", action="store_true", help="Skip git init + hooks configuration"
    )
    args = parser.parse_args()

    slug = args.name.removeprefix("stapel-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]*", slug):
        print(
            "Error: name must be lowercase alphanumeric with optional dashes",
            file=sys.stderr,
        )
        sys.exit(1)

    title = args.title or " ".join(
        p.capitalize() for p in slug.replace("-", " ").split()
    )
    scaffold_library(slug, title, args.dir, kind=args.kind, git=not args.no_git)


if __name__ == "__main__":
    main()
