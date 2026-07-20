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
    ADMIN_PY,
    APP_PY,
    ASGI_PY,
    BASE_SETTINGS,
    BOOT_SMOKE_SETTINGS,
    BOOTSTRAP_SH,
    CELERY_APP_PY,
    CONFIG_INIT_PY,
    DEV_SETTINGS,
    DOCKERFILE,
    LOCAL_SETTINGS,
    MANAGE_PY,
    MODELS_PY,
    PROD_SETTINGS,
    PYTEST_INI,
    REQUIREMENTS_TXT,
    SERVICE_YML,
    SVC_MAKEFILE,
    SVC_PYPROJECT,
    TEST_MODELS_PY,
    URLS_PY,
    VERSION_TXT,
    WSGI_PY,
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
    module_config: dict[str, dict] | None = None,
) -> dict:
    from ._module_config import render_settings_block

    module = slug.replace("-", "_")
    module_cap = "".join(p.capitalize() for p in module.split("_"))
    dir_name = f"{prefix}{slug}" if prefix else slug
    apps = stapel_apps or []
    stapel_apps_block = "".join(f'\n    "{app}",' for app in apps)

    def _url_include(app: str) -> str:
        # Per-lib mount (mismount fix, 2026-07-20): each Stapel feature lib
        # mounts at its OWN canonical prefix (stapel_tools._url_mounts,
        # derived from create_project.STAPEL_LIBS — cross-checked against
        # each lib's own urls.py) — NOT this service's shared
        # {url_prefix}api/. Mounting every lib at the shared prefix collided
        # every lib's routes onto the same path the instant a service (e.g.
        # a monolith) hosted more than one — cdn used to be the one
        # hand-special-cased exception; it is now just one entry in the same
        # map, nothing special about it anymore.
        from ._url_mounts import known_apps, url_mount_for

        if app in known_apps():
            mount = url_mount_for(app)
            if mount is None:
                return ""  # headless lib (composite glue / pure-pip dep): no url row
            return f'\n    path("{mount}", include("{app}.urls")),'
        # Not a registered Stapel lib (a project-local/custom app passed via
        # --stapel-apps): legacy behavior — mount under the service's own
        # shared prefix at Django runtime, since we have no data to derive
        # anything more precise for it.
        return f'\n    path(f"{{url_prefix}}api/", include("{app}.urls")),'

    url_includes = "".join(_url_include(app) for app in apps)
    dev_mock_providers = ""
    if "stapel_auth" in apps:
        from ._local_env_templates import DEV_MOCK_OTP_BLOCK
        dev_mock_providers = DEV_MOCK_OTP_BLOCK
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
        "STAPEL_MODULE_CONFIG": render_settings_block(module_config),
        # Not a render() token (no template spells "{{HAS_CDN}}") — a plain
        # string flag generate_service_files() branches on to pick the
        # libvips-enabled Dockerfile variant (cdn-scaffold-autowire.md).
        # String, not bool: render() blindly .replace()s every ctx value
        # against every template it's called on; a bool would TypeError the
        # instant it reached a template with no matching placeholder to
        # short-circuit on.
        "HAS_CDN": "true" if "stapel_cdn" in apps else "",
        "ACTION_TRANSPORT": action_transport,
        "FUNCTION_TRANSPORT": function_transport,
        "TASK_DISPATCH": task_dispatch,
        "DEV_MOCK_PROVIDERS": dev_mock_providers,
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
    from ._harness_templates import HARNESS_CONFTEST_FIXTURES, harness_files
    from ._templates import DOCKERFILE_CDN

    d = ctx["DIR"]
    m = ctx["MODULE"]
    # cdn auto-wiring (cdn-scaffold-autowire.md): a service that installs
    # stapel_cdn needs pyvips (stapel-cdn's [images] extra) importable at
    # runtime, which needs the SYSTEM libvips built somewhere — the
    # multi-stage vips-builder Dockerfile mirrors svc-stapel-studio's own
    # precedent. Every other service keeps the exact prior single-stage
    # Dockerfile (regression: no cdn -> byte-identical).
    dockerfile_template = DOCKERFILE_CDN if ctx.get("HAS_CDN") else DOCKERFILE
    # Service conftest = shared outbox/mailtrap harness fixtures + api_client.
    service_conftest = HARNESS_CONFTEST_FIXTURES + (
        "\n\n@pytest.fixture\n"
        "def api_client():\n"
        "    from rest_framework.test import APIClient\n\n"
        "    return APIClient()\n"
    )
    files = {
        root / d / "manage.py": render(MANAGE_PY, ctx),
        root / d / "version.txt": VERSION_TXT,
        root / d / "requirements.txt": REQUIREMENTS_TXT,
        root / d / "bootstrap.sh": render(BOOTSTRAP_SH, ctx),
        root / d / "Dockerfile": render(dockerfile_template, ctx),
        root / d / "config" / "__init__.py": CONFIG_INIT_PY,
        root / d / "config" / "celery.py": render(CELERY_APP_PY, ctx),
        root / d / "config" / "asgi.py": render(ASGI_PY, ctx),
        root / d / "config" / "wsgi.py": render(WSGI_PY, ctx),
        root / d / "config" / "urls.py": render(URLS_PY, ctx),
        root / d / "config" / "settings" / "__init__.py": "",
        root / d / "config" / "settings" / "base.py": render(BASE_SETTINGS, ctx),
        root / d / "config" / "settings" / "dev.py": render(DEV_SETTINGS, ctx),
        root / d / "config" / "settings" / "local.py": render(LOCAL_SETTINGS, ctx),
        root / d / "config" / "settings" / "prod.py": render(PROD_SETTINGS, ctx),
        # Boot-smoke gate (R3/§44, `make boot-smoke` — part of `make
        # controls`): the service-dir counterpart of the minimal preset's
        # config/settings_boot_smoke.py.
        root / d / "config" / "settings" / "boot_smoke.py": BOOT_SMOKE_SETTINGS,
        # Ruff config (this service's own controls surface — svc-<slug>/Makefile
        # below, delegated into from the project root Makefile for monolith).
        root / d / "pyproject.toml": SVC_PYPROJECT,
        root / d / "Makefile": render(SVC_MAKEFILE, ctx),
        # The service's own app lives under apps/ (regular package) just like
        # every stapel-new-module app, so INSTALLED_APPS carries "apps.<module>"
        # uniformly (Django ticket #24801).
        root / d / "apps" / "__init__.py": "",
        root / d / "apps" / m / "__init__.py": "",
        root / d / "apps" / m / "apps.py": render(APP_PY, ctx),
        root / d / "apps" / m / "models.py": render(MODELS_PY, ctx),
        root / d / "apps" / m / "admin.py": render(ADMIN_PY, ctx),
        root / d / "apps" / m / "tests" / "__init__.py": "",
        root / d / "apps" / m / "tests" / "test_models.py": render(TEST_MODELS_PY, ctx),
        root / d / "pytest.ini": render(PYTEST_INI, ctx),
        root / d / "conftest.py": service_conftest,
        root / d / "var" / "mailtrap" / ".gitkeep": "",
        root / f"{d}.yml": render(SERVICE_YML, ctx),
    }
    # Outbox/mailtrap integration harness (G7, system-design §7.12.3 / §7.21).
    files.update(harness_files(root / d / "tests"))
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
    """Append this service to the STAPEL_SERVICES env-JSON (admin-suite AS-4).

    The service navigation registry moved out of framework code into a
    deploy-config env-JSON (12-factor, read by both Python and the non-Django
    agent service). stapel-create-project seeds ``STAPEL_SERVICES=[]`` in the
    project ``.env`` / ``.env.example``; each new service appends a row — the
    same discipline as ``STAPEL_BUS_ROUTES``. Idempotent: a service already
    present (by prefix) is left untouched.
    """
    svc_name = title or "".join(
        p.capitalize() for p in slug.replace("-", "_").split("_")
    )
    for filename in (".env", ".env.example"):
        path = root / filename
        if not path.exists():
            continue
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if not line.lstrip().startswith("STAPEL_SERVICES="):
                continue
            _, _, raw = line.partition("=")
            try:
                services = json.loads(raw) if raw.strip() else []
            except json.JSONDecodeError:
                break  # malformed — leave for the operator, don't clobber
            if not isinstance(services, list):
                break
            if any(
                isinstance(s, dict) and s.get("prefix") == slug for s in services
            ):
                break  # already registered — idempotent
            services.append({"name": svc_name, "prefix": slug})
            lines[i] = f"STAPEL_SERVICES={json.dumps(services)}"
            path.write_text("\n".join(lines) + "\n")
            print(f"  updated {filename} (STAPEL_SERVICES)")
            break


def _add_db_to_compose(path: Path, db_name: str) -> bool:
    """Append *db_name* to POSTGRES_MULTIPLE_DATABASES in one compose file
    (keeping the value one quoted scalar). Returns True when changed."""
    if not path.exists():
        return False
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        if "POSTGRES_MULTIPLE_DATABASES" in line and db_name not in line:
            prefix, value = line.split(":", 1)
            value = value.strip().strip('"').strip("'")
            value = f"{value},{db_name}" if value else db_name
            lines[i] = f'{prefix}: "{value}"'
            path.write_text("\n".join(lines) + "\n")
            return True
    return False


def _update_compose_base(root: Path, slug: str, dir_name: str):
    db_name = f"stapel_{slug.replace('-', '_')}"
    # The local stack has its OWN db service (self-contained file) — keep its
    # database list in step with the base's.
    if _add_db_to_compose(root / "docker-compose.local.yml", db_name):
        print("  updated docker-compose.local.yml (databases)")

    path = root / "docker-compose.base.yml"
    if not path.exists():
        return
    lines = path.read_text().splitlines()

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


def _update_compose_file(
    root: Path, filename: str, slug: str, dir_name: str, debug_port: int = 0,
    dev_mode: bool = False,
):
    path = root / filename
    if not path.exists():
        return
    text = path.read_text()
    # A real service key line, not a substring match — the monolith/micro
    # compose templates ship a COMMENTED example ("  # svc-app:") that a bare
    # `f"{dir_name}:" in text` check false-positives against for a project's
    # first/default service (e.g. "app" -> "svc-app"), silently leaving the
    # backend never actually wired into docker-compose.yml/docker-compose.local.yml
    # (found live, §57 dev-compose audit: a fresh "app" monolith's dev/prod
    # compose never started the backend at all).
    if re.search(rf"^  {re.escape(dir_name)}:\s*$", text, re.MULTILINE):
        return
    if dev_mode:
        # Dev canon (§57 items 1/7): the backend's baked image defaults to
        # config.settings.prod (see _templates.DOCKERFILE) — that boots with
        # DEBUG=False and SECURE_SSL_REDIRECT on, which breaks plain-HTTP
        # local dev outright. Override to config.settings.dev (DEBUG=True,
        # file-mailtrap, debug toolbar, dev-only mock providers), bind-mount
        # the source for hot reload, and point env_file at .env.local (real
        # generated secrets + DJANGO_SUPERUSER_*/VITE_* dev defaults) instead
        # of the base service's own env_file: ".env" (prod).
        # depends_on is repeated here because compose `extends` deliberately
        # does NOT inherit it — and docker-compose.local.yml is self-contained
        # (its own db/redis), so the refs resolve inside the same file.
        block = (
            f"\n  {dir_name}:\n"
            f"    extends:\n"
            f"      file: {dir_name}.yml\n"
            f"      service: {dir_name}\n"
            f"    env_file: \".env.local\"\n"
            f"    environment:\n"
            f"      DJANGO_SETTINGS_MODULE: config.settings.dev\n"
            f"    volumes:\n"
            f"      - ./{dir_name}:/app\n"
            f"      - ./stapel_core:/app/stapel_core:ro\n"
            f"    depends_on:\n"
            f"      db:\n"
            f"        condition: service_healthy\n"
            f"      redis:\n"
            f"        condition: service_started\n"
        )
    elif debug_port:
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
        f"    proxy_set_header Host $http_host;\n"
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
      sh -c "celery -A config worker --loglevel=info ${{CELERY_WORKER_OPTS:-}}"
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
      sh -c "celery -A config beat --loglevel=info"
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
    module_config: Optional[dict[str, dict]] = None,
):
    """Scaffold a service. stapel_apps — Django app names of Stapel feature
    modules (e.g. ["stapel_auth"]) to wire into INSTALLED_APPS and urls;
    their packages are expected inside the service dir (git submodule).
    module_config — {module: {SETTING_KEY: value}} rendered as STAPEL_<MOD>
    blocks into settings/base.py (non-default capability axes only; validated
    against the module's docs/capabilities.json when the sibling checkout has
    one). Transports default to whatever broker the project's .env declares."""
    from ._module_config import validate_module_config

    validate_module_config(
        module_config,
        selected=[app.removeprefix("stapel_") for app in (stapel_apps or [])],
    )
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
        module_config=module_config,
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
    _update_compose_file(root, "docker-compose.local.yml", slug, dir_name, dev_mode=True)
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
    parser.add_argument(
        "--module-config", type=Path, metavar="PATH",
        help="JSON file {module: {SETTING_KEY: value}} rendered as "
             "STAPEL_<MOD> = {...} blocks in settings/base.py (non-default "
             "capability axes only; keys validated against the module's "
             "docs/capabilities.json when a sibling checkout has one)",
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

    module_config = None
    if args.module_config:
        from ._module_config import load_module_config_file

        module_config = load_module_config_file(args.module_config)

    scaffold_service(
        slug=slug,
        title=title,
        prefix=args.prefix,
        project_root=args.project_root,
        celery=args.celery,
        dry_run=args.dry_run,
        stapel_apps=args.stapel_apps,
        module_config=module_config,
    )


if __name__ == "__main__":
    main()
