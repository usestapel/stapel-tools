"""Generate every composite as a real project and run its own gates.

For each composite in the registry (shop, classified, booking, social) and
each project type that carries a backend (minimal, monolith), this:

1. generates the project with ``create_project`` (the scaffold Studio calls);
2. installs the UNION of every generated requirements file in ONE pip call,
   so the registry pins have to resolve jointly (a sibling's ceiling is the
   usual reason they do not);
3. runs the project's gates: ``manage.py check`` (minimal settings; monolith
   base and dev tiers, with the project's own env files), the boot-smoke
   tier, the generated pytest suite, and ruff over the service.

Any red gate fails the run and is printed with its output tail. The frontend
of a monolith is generated but not installed or built here (that is the e2e
job's Tier 0 step).

    python scripts/composite_gates.py [--out DIR] [--no-install] [composite ...]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PY = sys.executable


def _generate(out: Path, key: str, project_type: str) -> Path:
    from stapel_tools.create_project import create_project

    slug = f"{'m' if project_type == 'monolith' else 'n'}{key}"
    # gdpr rides along on the monolith so the closure guard is gated too.
    modules = ["core", key, *(["gdpr"] if project_type == "monolith" else [])]
    create_project(
        name=slug, project_type=project_type, title=slug, url=f"https://{slug}.example",
        company_name="Acme", company_email=f"hi@{slug}.example", modules=modules,
        output_dir=out, use_submodules=False, init_git=False,
    )
    return out / slug


def _requirements(proj: Path, project_type: str) -> list[Path]:
    if project_type == "monolith":
        return [proj / f"svc-{proj.name}" / "requirements.txt"]
    return [proj / "requirements.txt", proj / "requirements-dev.txt"]


def _run(cmd: list[str], cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def _gates(proj: Path, project_type: str, key: str, migrate: bool) -> list[tuple[str, subprocess.CompletedProcess]]:
    from stapel_tools._wiring import seed_commands
    from stapel_tools.assemble_scaffold import _load_dotenv
    from stapel_tools.create_project import _expand_with_requires

    # The project's env files. The runner's own POSTGRES_*/REDIS_URL apply to
    # the boot steps only (a CI service is reached at 127.0.0.1, not at the
    # compose name `db`); the check tiers keep the project's own values, which
    # is what prodguard grades.
    dotenv = _load_dotenv(proj)
    runner = {k: v for k, v in os.environ.items() if k.startswith(("POSTGRES_", "REDIS_URL"))}
    env = {
        **{k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"},
        **dotenv, "PYTHONDONTWRITEBYTECODE": "1",
    }
    results = []
    if project_type == "monolith":
        svc = proj / f"svc-{proj.name}"
        tiers = (("check", "config.settings.base"), ("check-dev", "config.settings.dev"),
                 ("boot-smoke", "config.settings.boot_smoke"))
    else:
        svc = proj
        tiers = (("check", "config.settings"), ("boot-smoke", "config.settings_boot_smoke"))
    for name, module in tiers:
        results.append((name, _run([PY, "manage.py", "check", "--fail-level", "ERROR"], svc,
                                   {**env, "DJANGO_SETTINGS_MODULE": module})))
    if migrate:
        # Boot: the schema migrates (system checks run first, as in
        # bootstrap.sh), then the seeds bootstrap.sh runs load reference data.
        boot_env = {**env, **runner, "DJANGO_SETTINGS_MODULE": tiers[0][1]}
        if project_type == "monolith":
            boot_env["DJANGO_SETTINGS_MODULE"] = "config.settings.dev"
        results.append(("migrate", _run([PY, "manage.py", "migrate", "--noinput"], svc, boot_env)))
        for cmd in seed_commands(_expand_with_requires([key])):
            args = cmd.split()[2:]
            results.append((f"seed:{args[0]}", _run([PY, "manage.py", *args], svc, boot_env)))
    results.append(("pytest", _run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"], svc, env)))
    results.append(("ruff", _run([PY, "-m", "ruff", "check", "."], svc, env)))
    return results


def main(argv: list[str] | None = None) -> int:
    from stapel_tools.preset_gate import composite_keys

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("composites", nargs="*")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--no-install", action="store_true", help="the environment already has the requirements")
    parser.add_argument(
        "--types", default="minimal,monolith",
        help="comma-separated project types (default: minimal,monolith)",
    )
    parser.add_argument(
        "--migrate", choices=("none", "minimal", "all"), default="minimal",
        help="run migrate and the bootstrap seeds: minimal (SQLite) only, or "
             "monolith too (needs the POSTGRES_* env of a reachable server)",
    )
    args = parser.parse_args(argv)
    keys = args.composites or composite_keys()
    out = args.out or Path(tempfile.mkdtemp(prefix="composite-gates-"))
    out.mkdir(parents=True, exist_ok=True)

    types = [t for t in args.types.split(",") if t]
    projects = [(key, t, _generate(out, key, t)) for key in keys for t in types]
    def regenerate() -> None:
        # Generation reads installed libraries (gdpr's DATA_OWNERS is derived
        # from the owners they declare), so once the requirements are in, the
        # projects are generated again against what is installed — the way a
        # host with its libraries installed generates them.
        import shutil

        for i, (key, t, proj) in enumerate(projects):
            shutil.rmtree(proj)
            projects[i] = (key, t, _generate(out, key, t))

    if not args.no_install:
        req_args = []
        for _key, t, proj in projects:
            for req in _requirements(proj, t):
                req_args += ["-r", str(req)]
        print(f"installing the union of {len(req_args) // 2} requirements files (one resolution)")
        proc = subprocess.run([PY, "-m", "pip", "install", "--quiet", *req_args])
        if proc.returncode:
            print("FAIL: the generated requirements do not resolve jointly")
            return 1
        regenerate()

    failed = 0
    for key, t, proj in projects:
        migrate = args.migrate == "all" or (args.migrate == "minimal" and t == "minimal")
        for name, proc in _gates(proj, t, key, migrate):
            ids = sorted(set(re.findall(r"\(([a-z_.]+\.[EW]\d+)\)", proc.stdout + proc.stderr)))
            status = "ok" if proc.returncode == 0 else "FAIL"
            print(f"{status:4} {key:10} {t:8} {name:10} {' '.join(i for i in ids if '.E' in i)}")
            if proc.returncode:
                failed += 1
                print((proc.stdout + proc.stderr)[-4000:])
    print(f"{len(projects)} projects, {failed} red gate(s); generated under {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
