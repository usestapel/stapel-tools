"""
stapel-remove-service — remove a Stapel/Django microservice.

Inverse of stapel-new-service. Deletes the service directory and
removes its entries from compose files, nginx, prometheus, and VSCode.

Usage:
    stapel-remove-service auth
    stapel-remove-service auth --prefix iron-
    stapel-remove-service auth --dry-run
    stapel-remove-service auth --yes
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

from .new_service import find_project_root

DRY_RUN = False


def _log(msg: str):
    prefix = "[dry-run] " if DRY_RUN else ""
    print(f"{prefix}{msg}")


def _write(path: Path, text: str):
    if not DRY_RUN:
        path.write_text(text)


def _remove(path: Path, root: Path) -> bool:
    if not path.exists():
        return False
    _log(f"removing {path.relative_to(root)}")
    if not DRY_RUN:
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    return True


def _remove_lines(text: str, predicate) -> str:
    return "\n".join(line for line in text.splitlines() if not predicate(line)) + (
        "\n" if text.endswith("\n") else ""
    )


def _remove_compose_block(text: str, service_name: str) -> str:
    """Remove a top-level service block from a compose YAML."""
    lines = text.splitlines(keepends=True)
    result = []
    skip = False
    for line in lines:
        if re.match(rf"^  {re.escape(service_name)}:\s*$", line):
            skip = True
            continue
        if skip:
            if re.match(r"^  \S", line):
                skip = False
            else:
                continue
        result.append(line)
    return "".join(result)


# ---------------------------------------------------------------------------
# Individual config updates
# ---------------------------------------------------------------------------


def _remove_from_services_conf(root: Path, slug: str):
    conf = root / "services.conf"
    if not conf.exists():
        return
    text = conf.read_text()
    new_text = _remove_lines(text, lambda line: line.strip() == slug)
    if new_text != text:
        _log("updated services.conf")
        _write(conf, new_text)


def _remove_from_stapel_services(root: Path, slug: str):
    for cfg in root.rglob("config.py"):
        # Skip vendored library checkouts (stapel_* submodules)
        if any(part.startswith("stapel_") for part in cfg.relative_to(root).parts[:-1]):
            continue
        text = cfg.read_text()
        if "STAPEL_SERVICES" not in text or f"'{slug}'" not in text:
            continue
        pattern = rf"\{{'name':\s*'[^']*',\s*'prefix':\s*'{re.escape(slug)}'\}},?\n?"
        new_text = re.sub(pattern, "", text)
        if new_text != text:
            _log(f"updated {cfg.relative_to(root)}")
            _write(cfg, new_text)


def _remove_from_compose_base(root: Path, slug: str, dir_name: str):
    path = root / "docker-compose.base.yml"
    if not path.exists():
        return
    text = path.read_text()
    db_entry = f"stapel_{slug.replace('-', '_')}"
    # Remove from POSTGRES_MULTIPLE_DATABASES
    text = re.sub(rf",?\s*{re.escape(db_entry)}", "", text)
    # Remove from nginx depends_on
    text = _remove_lines(text, lambda line: line.strip() == f"- {dir_name}")
    _log("updated docker-compose.base.yml")
    _write(path, text)


def _remove_from_compose_file(root: Path, filename: str, dir_name: str):
    path = root / filename
    if not path.exists():
        return
    text = path.read_text()
    if dir_name not in text:
        return
    new_text = _remove_compose_block(text, dir_name)
    # Also remove celery blocks
    new_text = _remove_compose_block(new_text, f"{dir_name}-celery")
    new_text = _remove_compose_block(new_text, f"{dir_name}-celery-beat")
    if new_text != text:
        _log(f"updated {filename}")
        _write(path, new_text)


def _remove_from_nginx(root: Path, slug: str):
    nginx_dir = root / "service-configs" / "nginx"
    if not nginx_dir.exists():
        return
    pattern = rf"\s*location /{re.escape(slug)} \{{[^}}]*\}}"
    for conf_name in ("nginx.conf", "nginx.ssl.conf"):
        conf = nginx_dir / conf_name
        if not conf.exists():
            continue
        text = conf.read_text()
        new_text = re.sub(pattern, "", text, flags=re.DOTALL)
        if new_text != text:
            _log(f"updated service-configs/nginx/{conf_name}")
            _write(conf, new_text)


def _remove_from_prometheus(root: Path, dir_name: str):
    path = root / "service-configs" / "prometheus" / "prometheus.yml"
    if not path.exists():
        return
    text = path.read_text()
    # Job bodies contain '[' (targets lists), so match lazily up to the next
    # job, a top-level comment marker, or end of file.
    pattern = rf"\n?\s*- job_name: '{re.escape(dir_name)}'.*?(?=\n\s*- job_name:|\n\s*#|\Z)"
    new_text = re.sub(pattern, "", text, flags=re.DOTALL)
    if new_text != text:
        _log("updated prometheus.yml")
        _write(path, new_text)


def _remove_from_vscode(root: Path, dir_name: str, title: str):
    service_path = f"${{workspaceFolder}}/{dir_name}"

    settings_path = root / ".vscode" / "settings.json"
    if settings_path.exists():
        data = json.loads(settings_path.read_text())
        changed = False
        for key in ("cursorpyright.analysis.extraPaths", "python.analysis.extraPaths"):
            paths = data.get(key, [])
            if service_path in paths:
                paths.remove(service_path)
                data[key] = paths
                changed = True
        if changed:
            _log("updated .vscode/settings.json")
            if not DRY_RUN:
                settings_path.write_text(json.dumps(data, indent=2) + "\n")

    launch_path = root / ".vscode" / "launch.json"
    if launch_path.exists():
        data = json.loads(launch_path.read_text())
        configs = data.get("configurations", [])
        name = f"{title} - Debug"
        new_configs = [c for c in configs if c.get("name") != name]
        if len(new_configs) != len(configs):
            data["configurations"] = new_configs
            _log("updated .vscode/launch.json")
            if not DRY_RUN:
                launch_path.write_text(json.dumps(data, indent=4))


def _remove_from_pyrightconfig(root: Path, dir_name: str):
    path = root / "pyrightconfig.json"
    if not path.exists():
        return
    data = json.loads(path.read_text())
    extra = data.get("extraPaths", [])
    if dir_name in extra:
        extra.remove(dir_name)
        data["extraPaths"] = extra
        _log("updated pyrightconfig.json")
        _write(path, json.dumps(data, indent=2) + "\n")


def _remove_from_run_tests(root: Path, slug: str):
    path = root / "run_tests.sh"
    if not path.exists():
        return
    text = path.read_text()
    new_text = re.sub(rf"\s*\b{re.escape(slug)}\b", "", text)
    if new_text != text:
        _log("updated run_tests.sh")
        _write(path, new_text)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def remove_service(
    slug: str,
    prefix: str = "",
    project_root: Optional[Path] = None,
    yes: bool = False,
    dry_run: bool = False,
):
    global DRY_RUN
    DRY_RUN = dry_run

    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug):
        print("Error: name must be lowercase alphanumeric with optional dashes", file=sys.stderr)
        sys.exit(1)
    if prefix and not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*-", prefix):
        print("Error: prefix must be lowercase alphanumeric ending with a dash, e.g. 'svc-'", file=sys.stderr)
        sys.exit(1)

    cwd = Path.cwd()
    root = (project_root or find_project_root(cwd) or cwd).resolve()
    dir_name = f"{prefix}{slug}" if prefix else slug
    title = " ".join(p.capitalize() for p in slug.replace("-", " ").split())

    service_dir = (root / dir_name).resolve()
    service_yml = (root / f"{dir_name}.yml").resolve()
    if service_dir == root or not service_dir.is_relative_to(root):
        print(f"Error: refusing to remove {service_dir}: outside project root {root}", file=sys.stderr)
        sys.exit(1)

    if not service_dir.exists() and not service_yml.exists():
        print(f"Error: neither {service_dir} nor {service_yml} found", file=sys.stderr)
        sys.exit(1)

    if not yes and not dry_run:
        confirm = input(f"Remove {dir_name} and all its config entries? [y/N] ")
        if confirm.lower() not in ("y", "yes"):
            print("Aborted.")
            sys.exit(0)

    _remove(service_dir, root)
    _remove(service_yml, root)

    print("\nCleaning project configs...")
    _remove_from_services_conf(root, slug)
    _remove_from_stapel_services(root, slug)
    _remove_from_compose_base(root, slug, dir_name)
    for compose in ("docker-compose.yml", "docker-compose.dev.yml", "docker-compose.stg.yml"):
        _remove_from_compose_file(root, compose, dir_name)
    _remove_from_nginx(root, slug)
    _remove_from_prometheus(root, dir_name)
    _remove_from_vscode(root, dir_name, title)
    _remove_from_pyrightconfig(root, dir_name)
    _remove_from_run_tests(root, slug)

    print(f"\n{'[dry-run] Would remove' if dry_run else 'Removed'} service {dir_name}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="Service slug, e.g. 'auth'")
    parser.add_argument("--prefix", default="", help="Directory prefix, e.g. 'iron-' (default: none)")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would be removed")
    parser.add_argument("--project-root", type=Path, help="Explicit project root (default: auto-detect)")
    args = parser.parse_args()

    remove_service(
        slug=args.name,
        prefix=args.prefix,
        project_root=args.project_root,
        yes=args.yes,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
