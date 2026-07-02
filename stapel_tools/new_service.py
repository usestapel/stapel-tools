"""
stapel-new-service — scaffold a new Stapel/Django microservice.

Creates the full directory structure for a service and wires it into
compose files, nginx, prometheus, and VSCode if those files are found.

Usage:
    stapel-new-service auth
    stapel-new-service auth --title "Auth Service" --prefix iron
    stapel-new-service auth --celery
    stapel-new-service auth --project-root /path/to/monorepo
"""

import argparse
import json
import keyword
import re
import sys
from pathlib import Path
from typing import Optional

from ._templates import (
    ADMIN_PY, APP_PY, ASGI_PY, BASE_SETTINGS, BOOTSTRAP_SH, CONFTEST_PY,
    DEV_SETTINGS, DOCKERFILE, LOCAL_SETTINGS, MANAGE_PY, MODELS_PY,
    PROD_SETTINGS, PYTEST_INI, REQUIREMENTS_TXT, SERVICE_YML, TEST_MODELS_PY,
    URLS_PY, VERSION_TXT, WSGI_PY,
)


# ---------------------------------------------------------------------------
# Root detection
# ---------------------------------------------------------------------------


def find_project_root(start: Path) -> Optional[Path]:
    markers = {"docker-compose.yml", "docker-compose.base.yml", "services.conf"}
    current = start.resolve()
    for _ in range(6):
        if any((current / m).exists() for m in markers):
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


def render(template: str, ctx: dict) -> str:
    result = template
    for key, value in ctx.items():
        result = result.replace(f"{{{{{key}}}}}", value)
    return result


def make_context(
    slug: str,
    title: str,
    prefix: str,
    stapel_apps: list[str] | None = None,
    action_transport: str = "inprocess",
    function_transport: str = "inprocess",
    task_dispatch: str = "action",
) -> dict:
    module = slug.replace("-", "_")
    module_cap = "".join(p.capitalize() for p in module.split("_"))
    dir_name = f"{prefix}{slug}" if prefix else slug
    apps = stapel_apps or []
    stapel_apps_block = "".join(f'\n    "{app}",' for app in apps)
    url_includes = "".join(
        f'\n    path(f"{{url_prefix}}api/", include("{app}.urls")),' for app in apps
    )
    return {
        "TITLE": title,
        "SLUG": slug,
        "SLUG_UPPER": slug.upper().replace("-", "_"),
        "MODULE": module,
        "MODULE_CAP": module_cap,
        "PREFIX": prefix,
        "DIR": dir_name,
        "DB_NAME": f"stapel_{module}",
        "URL_PREFIX": f"{slug}/",
        "STAPEL_APPS": stapel_apps_block,
        "STAPEL_URL_INCLUDES": url_includes,
        "ACTION_TRANSPORT": action_transport,
        "FUNCTION_TRANSPORT": function_transport,
        "TASK_DISPATCH": task_dispatch,
    }




def _read_project_env(root: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for candidate in (".env", ".env.example"):
        f = root / candidate
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env.setdefault(key.strip(), value.strip())
    return env


def _detect_transports(root: Path) -> tuple[str, str, str]:
    """Infer comm transports from the project's declared broker.

    Reads STAPEL_BUS_BACKEND / STAPEL_TASK_DISPATCH / STAPEL_BUS_ROUTES from
    .env / .env.example so that services added later match the choice made at
    project creation. Returns (action_transport, function_transport,
    task_dispatch).
    """
    env = _read_project_env(root)
    broker = env.get("STAPEL_BUS_BACKEND", "")
    if env.get("STAPEL_TASK_DISPATCH", "") == "bus":
        # The broker is dedicated to Tasks — Actions stay in-process.
        return "inprocess", "inprocess", "bus"
    if broker == "nats":
        return "bus", "nats", "action"
    if broker == "kafka":
        return "bus", "http", "action"
    if broker == "routing":
        # Functions follow the DEFAULT event route ("" prefix).
        import json

        default = ""
        try:
            default = json.loads(env.get("STAPEL_BUS_ROUTES", "") or "{}").get("", "")
        except ValueError:
            pass
        return "bus", ("nats" if default == "nats" else "http"), "action"
    return "inprocess", "inprocess", "action"


# ---------------------------------------------------------------------------
# File generation
# ---------------------------------------------------------------------------


def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"{path} already exists")
    path.write_text(content, encoding="utf-8")


def generate_service_files(root: Path, ctx: dict) -> dict[Path, str]:
    d = ctx["DIR"]
    m = ctx["MODULE"]
    files = {
        root / d / "manage.py": render(MANAGE_PY, ctx),
        root / d / "version.txt": VERSION_TXT,
        root / d / "requirements.txt": REQUIREMENTS_TXT,
        root / d / "bootstrap.sh": render(BOOTSTRAP_SH, ctx),
        root / d / "Dockerfile": render(DOCKERFILE, ctx),
        root / d / "core" / "__init__.py": "",
        root / d / "core" / "asgi.py": render(ASGI_PY, ctx),
        root / d / "core" / "wsgi.py": render(WSGI_PY, ctx),
        root / d / "core" / "urls.py": render(URLS_PY, ctx),
        root / d / "core" / "settings" / "__init__.py": "",
        root / d / "core" / "settings" / "base.py": render(BASE_SETTINGS, ctx),
        root / d / "core" / "settings" / "dev.py": render(DEV_SETTINGS, ctx),
        root / d / "core" / "settings" / "local.py": render(LOCAL_SETTINGS, ctx),
        root / d / "core" / "settings" / "prod.py": render(PROD_SETTINGS, ctx),
        root / d / m / "__init__.py": "",
        root / d / m / "apps.py": render(APP_PY, ctx),
        root / d / m / "models.py": render(MODELS_PY, ctx),
        root / d / m / "admin.py": render(ADMIN_PY, ctx),
        root / d / m / "tests" / "__init__.py": "",
        root / d / m / "tests" / "test_models.py": render(TEST_MODELS_PY, ctx),
        root / d / "pytest.ini": render(PYTEST_INI, ctx),
        root / d / "conftest.py": render(CONFTEST_PY, ctx),
        root / f"{d}.yml": render(SERVICE_YML, ctx),
    }
    return files


# ---------------------------------------------------------------------------
# Config file updates
# ---------------------------------------------------------------------------


def _update_services_conf(root: Path, slug: str):
    conf = root / "services.conf"
    if not conf.exists():
        return
    lines = conf.read_text().splitlines()
    if slug not in lines:
        lines.append(slug)
        conf.write_text("\n".join(lines) + "\n")
        print("  updated services.conf")


def _update_stapel_services(root: Path, slug: str, title: str):
    """Update STAPEL_SERVICES in any project-owned config.py."""
    candidates = [
        cfg for cfg in root.rglob("config.py")
        # Skip vendored library checkouts (stapel_* submodules) — patching
        # framework source from the scaffolder corrupts the submodule tree.
        if not any(part.startswith("stapel_") for part in cfg.relative_to(root).parts[:-1])
    ]
    for cfg in candidates:
        text = cfg.read_text()
        if "STAPEL_SERVICES" not in text:
            continue
        if slug in text:
            continue
        pattern = r"(STAPEL_SERVICES\s*=\s*\[\s*)([^\]]*)(\])"
        m = re.search(pattern, text, re.DOTALL)
        if not m:
            continue
        svc_name = "".join(p.capitalize() for p in slug.replace("-", "_").split("_"))
        entry = f"    {{'name': '{svc_name}', 'prefix': '{slug}'}},\n"
        new_block = m.group(1) + m.group(2) + entry + m.group(3)
        text = re.sub(pattern, new_block, text, flags=re.DOTALL)
        cfg.write_text(text)
        print(f"  updated {cfg.relative_to(root)}")
        break


def _update_compose_base(root: Path, slug: str, dir_name: str):
    path = root / "docker-compose.base.yml"
    if not path.exists():
        return
    lines = path.read_text().splitlines()
    db_name = f"stapel_{slug.replace('-', '_')}"

    # Update POSTGRES_MULTIPLE_DATABASES, keeping the value one quoted scalar
    for i, line in enumerate(lines):
        if "POSTGRES_MULTIPLE_DATABASES" in line and db_name not in line:
            prefix, value = line.split(":", 1)
            value = value.strip().strip('"').strip("'")
            value = f"{value},{db_name}" if value else db_name
            lines[i] = f'{prefix}: "{value}"'
            break

    # Update nginx depends_on
    for i, line in enumerate(lines):
        if line.strip().startswith("nginx:"):
            j = i
            while j < len(lines) and "depends_on:" not in lines[j]:
                j += 1
            if j < len(lines):
                indent = len(lines[j]) - len(lines[j].lstrip())
                item = " " * (indent + 2) + f"- {dir_name}"
                if lines[j].strip() in ("depends_on: []", "depends_on: [ ]"):
                    lines[j] = " " * indent + "depends_on:"
                    lines.insert(j + 1, item)
                else:
                    k = j + 1
                    exists = any(dir_name in lines[x] for x in range(k, min(k + 20, len(lines))))
                    if not exists:
                        lines.insert(j + 1, item)
            break

    path.write_text("\n".join(lines) + "\n")
    print("  updated docker-compose.base.yml")


def _update_compose_file(root: Path, filename: str, slug: str, dir_name: str, debug_port: int = 0):
    path = root / filename
    if not path.exists():
        return
    text = path.read_text()
    if f"{dir_name}:" in text:
        return
    if debug_port:
        block = (
            f"\n  {dir_name}:\n"
            f"    extends:\n"
            f"      file: {dir_name}.yml\n"
            f"      service: {dir_name}\n"
            f"    ports:\n"
            f'      - "{debug_port}:5678"\n'
            f"    volumes:\n"
            f"      - ./{dir_name}:/app\n"
            f"      - ./stapel_core:/app/stapel_core\n"
        )
    else:
        block = (
            f"\n  {dir_name}:\n"
            f"    extends:\n"
            f"      file: {dir_name}.yml\n"
            f"      service: {dir_name}\n"
        )
    path.write_text(text.rstrip() + "\n" + block + "\n")
    print(f"  updated {filename}")


def _get_next_debug_port(root: Path) -> int:
    path = root / "docker-compose.yml"
    if not path.exists():
        return 5684
    text = path.read_text()
    used = {int(m) for m in re.findall(r'"(\d+):5678"', text)}
    port = 5679
    while port in used:
        port += 1
    return port


def _update_nginx(root: Path, slug: str, dir_name: str):
    nginx_dir = root / "service-configs" / "nginx"
    if not nginx_dir.exists():
        return
    block = (
        f"\n  location /{slug} {{\n"
        f"    set $upstream_{slug.replace('-', '_')} {dir_name}:8000;\n"
        f"    proxy_pass http://$upstream_{slug.replace('-', '_')};\n"
        f"    proxy_set_header Host $host;\n"
        f"    proxy_set_header X-Real-IP $remote_addr;\n"
        f"    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        f"    proxy_set_header X-Forwarded-Proto $scheme;\n"
        f"    proxy_redirect off;\n"
        f"  }}\n"
    )
    for conf_name in ("nginx.conf", "nginx.ssl.conf"):
        conf = nginx_dir / conf_name
        if not conf.exists():
            continue
        text = conf.read_text()
        if f"location /{slug} " in text:
            continue
        insert_pos = text.rfind("}")
        if insert_pos == -1:
            continue
        conf.write_text(text[:insert_pos] + block + text[insert_pos:])
        print(f"  updated service-configs/nginx/{conf_name}")


def _update_prometheus(root: Path, slug: str, dir_name: str):
    path = root / "service-configs" / "prometheus" / "prometheus.yml"
    if not path.exists():
        return
    text = path.read_text()
    if f"job_name: '{dir_name}'" in text:
        return
    job = (
        f"\n  - job_name: '{dir_name}'\n"
        f"    static_configs:\n"
        f"      - targets: ['{dir_name}:8000']\n"
        f"        labels:\n"
        f"          service: '{slug}'\n"
        f"    metrics_path: '/{slug}/api/metrics/'\n"
        f"    scrape_interval: 30s\n"
    )
    # Insert before Redis/PostgreSQL comments or at end
    for marker in ("  # Redis", "  # PostgreSQL"):
        pos = text.find(marker)
        if pos != -1:
            text = text[:pos] + job + "\n" + text[pos:]
            break
    else:
        text = text.rstrip() + job
    path.write_text(text)
    print("  updated prometheus.yml")


def _update_vscode(root: Path, slug: str, dir_name: str, title: str, debug_port: int):
    settings_path = root / ".vscode" / "settings.json"
    launch_path = root / ".vscode" / "launch.json"
    service_path = f"${{workspaceFolder}}/{dir_name}"

    if settings_path.exists():
        data = json.loads(settings_path.read_text())
        changed = False
        for key in ("cursorpyright.analysis.extraPaths", "python.analysis.extraPaths"):
            paths = data.get(key, [])
            if service_path not in paths:
                paths.append(service_path)
                data[key] = paths
                changed = True
        if changed:
            settings_path.write_text(json.dumps(data, indent=2) + "\n")
            print("  updated .vscode/settings.json")

    if launch_path.exists():
        data = json.loads(launch_path.read_text())
        configs = data.get("configurations", [])
        name = f"{title} - Debug"
        if not any(c.get("name") == name for c in configs):
            configs.append({
                "name": name,
                "type": "debugpy",
                "request": "attach",
                "connect": {"host": "localhost", "port": debug_port},
                "pathMappings": [
                    {"localRoot": f"${{workspaceFolder}}/{dir_name}", "remoteRoot": "/app"},
                ],
            })
            data["configurations"] = configs
            launch_path.write_text(json.dumps(data, indent=4))
            print("  updated .vscode/launch.json")


def _update_pyrightconfig(root: Path, dir_name: str):
    path = root / "pyrightconfig.json"
    if not path.exists():
        return
    data = json.loads(path.read_text())
    extra = data.get("extraPaths", [])
    if dir_name not in extra:
        extra.append(dir_name)
        data["extraPaths"] = extra
        path.write_text(json.dumps(data, indent=2) + "\n")
        print("  updated pyrightconfig.json")


def _update_run_tests(root: Path, slug: str):
    path = root / "run_tests.sh"
    if not path.exists():
        return
    text = path.read_text()
    if slug in text:
        return
    pattern = r'(PYTHON_SERVICES=")([^"]*)'
    m = re.search(pattern, text)
    if m:
        path.write_text(re.sub(pattern, f"\\g<1>{m.group(2)} {slug}", text))
        print("  updated run_tests.sh")


def _add_celery_to_service_yml(root: Path, slug: str, dir_name: str):
    path = root / f"{dir_name}.yml"
    if not path.exists():
        return
    text = path.read_text()
    db_name = f"stapel_{slug.replace('-', '_')}"
    slug_upper = slug.upper().replace("-", "_")
    block = f"""
  {dir_name}-celery:
    env_file: ".env"
    image: ${{IMAGE_TAG_{slug_upper}}}
    build:
      context: .
      dockerfile: {dir_name}/Dockerfile
    command: >
      sh -c "celery -A core worker --loglevel=info ${{CELERY_WORKER_OPTS:-}}"
    restart: unless-stopped
    environment:
      POSTGRES_DB: "{db_name}"
    depends_on:
      redis:
        condition: service_started
      {dir_name}:
        condition: service_started

  {dir_name}-celery-beat:
    env_file: ".env"
    image: ${{IMAGE_TAG_{slug_upper}}}
    build:
      context: .
      dockerfile: {dir_name}/Dockerfile
    command: >
      sh -c "celery -A core beat --loglevel=info"
    restart: unless-stopped
    environment:
      POSTGRES_DB: "{db_name}"
    depends_on:
      redis:
        condition: service_started
      {dir_name}-celery:
        condition: service_started
"""
    path.write_text(text.rstrip() + "\n" + block + "\n")
    print(f"  added celery to {dir_name}.yml")


# ---------------------------------------------------------------------------
# Main scaffold function
# ---------------------------------------------------------------------------


def scaffold_service(
    slug: str,
    title: str,
    prefix: str = "",
    project_root: Optional[Path] = None,
    celery: bool = False,
    dry_run: bool = False,
    stapel_apps: Optional[list[str]] = None,
    action_transport: Optional[str] = None,
    function_transport: Optional[str] = None,
    task_dispatch: Optional[str] = None,
):
    """Scaffold a service. stapel_apps — Django app names of Stapel feature
    modules (e.g. ["stapel_auth"]) to wire into INSTALLED_APPS and urls;
    their packages are expected inside the service dir (git submodule).
    Transports default to whatever broker the project's .env declares."""
    cwd = Path.cwd()
    root = project_root or find_project_root(cwd) or cwd
    if action_transport is None or function_transport is None or task_dispatch is None:
        detected_action, detected_function, detected_dispatch = _detect_transports(root)
        action_transport = action_transport or detected_action
        function_transport = function_transport or detected_function
        task_dispatch = task_dispatch or detected_dispatch
    ctx = make_context(
        slug, title, prefix,
        stapel_apps=stapel_apps,
        action_transport=action_transport,
        function_transport=function_transport,
        task_dispatch=task_dispatch,
    )
    dir_name = ctx["DIR"]

    if (root / dir_name).exists():
        print(f"Error: {root / dir_name} already exists", file=sys.stderr)
        sys.exit(1)

    files = generate_service_files(root, ctx)

    if dry_run:
        print(f"[dry-run] Would create {len(files)} files in {root / dir_name}/")
        for path in sorted(files):
            print(f"  {path.relative_to(root)}")
        return

    print(f"Creating {dir_name} in {root}/")
    for path, content in files.items():
        write_file(path, content)
        print(f"  created {path.relative_to(root)}")

    print("\nUpdating project configs...")
    _update_services_conf(root, slug)
    _update_stapel_services(root, slug, title)
    _update_compose_base(root, slug, dir_name)

    debug_port = _get_next_debug_port(root)
    _update_compose_file(root, "docker-compose.yml", slug, dir_name, debug_port)
    _update_compose_file(root, "docker-compose.dev.yml", slug, dir_name)
    _update_nginx(root, slug, dir_name)
    _update_prometheus(root, slug, dir_name)
    _update_vscode(root, slug, dir_name, title, debug_port)
    _update_pyrightconfig(root, dir_name)
    _update_run_tests(root, slug)

    if celery:
        _add_celery_to_service_yml(root, slug, dir_name)

    print(f"\nService {dir_name} created. Next steps:")
    print("  1. Add secrets to .env")
    print(f"  2. Add {dir_name} submodule deps if needed")
    print(f"  3. Run migrations: docker compose run --rm {dir_name} python manage.py migrate")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="Service slug, e.g. 'auth' or 'my-service'")
    parser.add_argument("--title", help="Display name, e.g. 'Auth Service'")
    parser.add_argument(
        "--prefix", default="",
        help="Directory prefix including dash, e.g. 'iron-' creates 'iron-{name}/' (default: none)",
    )
    parser.add_argument("--celery", action="store_true", help="Add Celery worker and beat containers")
    parser.add_argument("--project-root", type=Path, help="Explicit project root (default: auto-detect)")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would be created")
    parser.add_argument(
        "--stapel-apps", nargs="*", default=[], metavar="APP",
        help="Stapel feature apps to wire in (e.g. stapel_auth stapel_gdpr); "
             "add each as a git submodule inside the service dir afterwards",
    )
    args = parser.parse_args()

    slug = args.name
    if not re.fullmatch(r"[a-z0-9\-]+", slug):
        print("Error: name must be lowercase alphanumeric with optional dashes", file=sys.stderr)
        sys.exit(1)
    module = slug.replace("-", "_")
    if not module.isidentifier() or keyword.iskeyword(module):
        print(
            f"Error: '{slug}' maps to Python module '{module}', which is not a valid module name "
            "(must not start with a digit or be a keyword)",
            file=sys.stderr,
        )
        sys.exit(1)
    if args.prefix and not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*-", args.prefix):
        print("Error: prefix must be lowercase alphanumeric ending with a dash, e.g. 'svc-'", file=sys.stderr)
        sys.exit(1)

    title = args.title or " ".join(p.capitalize() for p in slug.replace("-", " ").split())
    if not title.endswith("Service") and not args.title:
        title = f"{title} Service"

    scaffold_service(
        slug=slug,
        title=title,
        prefix=args.prefix,
        project_root=args.project_root,
        celery=args.celery,
        dry_run=args.dry_run,
        stapel_apps=args.stapel_apps,
    )


if __name__ == "__main__":
    main()
