"""
stapel-assemble — static scaffold assembler (static-scaffold-and-config.md
§1.3/§1.4/§6.1). Fixes the root failure mode of the day: "wire lib X into the
project" was being run through the full architect/coder/controls LLM cycle
(T-001/003/005/007 each burned hours on it) for work that is entirely
DETERMINISTIC — a script's job, done in seconds. This module is that script.

``assemble_scaffold(slug, libs=[...], config={...})`` does, in ONE
deterministic pass, with NO LLM involved at any step:

  1. Calls the existing :func:`stapel_tools.create_project.create_project`
     (canonical ``config/`` + ``apps/`` minimal layout) — that function
     already wires every requested lib into requirements.txt (pip pin
     comment), ``INSTALLED_APPS`` (full dotted app path), ``config/urls.py``
     (``/<mod>/api/...`` canon mount) and ``STAPEL_<MOD>`` settings blocks
     (:mod:`stapel_tools._module_config`) — this module does not re-implement
     any of that wiring, it is the one-call wrapper + verification gate around
     it, run idempotently against ``STAPEL_LIBS``.
  2. Runs the static verification gates, no LLM: ``manage.py check`` under the
     project's own settings, and the boot-smoke gate (dummy-DB overlay,
     R3/§44, mechanism shipped in this repo by commit efcb552 — reused as-is
     here, not reimplemented) under ``config.settings_boot_smoke``. A red gate
     is a plain subprocess failure with captured stdout/stderr, never a retry
     loop.
  3. A lib name that is not in ``STAPEL_LIBS`` is a structured gap in the
     result (``AssembleResult.libs_unknown``), not a crash — assembly proceeds
     with the known subset. This is a scaffolding-registry gap (the module
     hasn't been onboarded to stapel-tools yet), not a caller bug.

CLI:
    stapel-assemble proof --libs auth notifications gdpr profiles

Python API:
    from stapel_tools.assemble_scaffold import assemble_scaffold
    result = assemble_scaffold("proof", libs=["auth", "notifications"])
    assert result.ok

Studio interface point (NOT built here — studio owns it): a
``scaffold_assembly`` FSM task type calls this function (or the CLI) as its
sole executor, with no architect/coder/controls role in the loop, per
static-scaffold-and-config.md §1.3/§6.1. A future "upgrade libs" act (re-stamp
an existing generated project onto newer ``STAPEL_LIBS`` pins) is §25/§52
scope — the natural seam is a second entry point,
``upgrade_scaffold(project_dir, libs=[...])``, that re-renders the same
wiring against a project that already exists; not implemented here, this
module only pins the registry version data it would read.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config_lint import collect_reads, lint_project
from .config_manifest import ConfigEntry, aggregate_config_md, collect_lib_entries
from .create_project import STAPEL_LIBS, _expand_with_requires, create_project


@dataclass
class GateResult:
    """One static verification gate's outcome."""

    name: str
    passed: bool
    output: str = ""
    skipped: bool = False


@dataclass
class AssembleResult:
    slug: str
    project_dir: Path
    libs_applied: list[str]
    libs_unknown: list[str]
    gates: list[GateResult] = field(default_factory=list)
    #: libs whose CONFIG.MD registry is not shipped yet (per-module sweep is
    #: the next wave — static-scaffold-and-config.md §2). A reported gap, not a
    #: failure: the aggregate still carries every lib that does ship one.
    config_libs_missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True iff every gate that actually ran passed. Unknown libs are a
        reported gap, not a failure — assembly still succeeded for the known
        subset."""
        return all(g.passed for g in self.gates)


def _write_config_md(project_dir: Path, slug: str, libs: list[str]) -> list[str]:
    """Aggregate the selected libs' CONFIG.MD into the project root CONFIG.MD
    (static-scaffold-and-config.md §1.2/§2), plus a project section auto-derived
    from the keys the generated settings actually read that no lib already
    covers — so the baseline is self-consistent (the config-lint gate below is
    green by construction, and stays a live gate for the author's later edits).

    Returns the list of selected libs that ship no CONFIG.MD yet (a gap)."""
    lib_entries, missing = collect_lib_entries(libs)
    covered = {e.key for e in lib_entries}
    extra: list[ConfigEntry] = []
    seen: set[str] = set()
    for read in collect_reads(project_dir):
        if not read.in_settings or read.key in covered or read.key in seen:
            continue
        seen.add(read.key)
        extra.append(ConfigEntry(
            key=read.key,
            source="env",
            purpose="project setting (generated — classify env or vault as needed)",
            owner="project",
        ))
    text, missing = aggregate_config_md(
        libs, title=f"CONFIG.MD — {slug}", extra_entries=extra
    )
    (project_dir / "CONFIG.MD").write_text(text, encoding="utf-8")
    return missing


def _run_config_lint(project_dir: Path) -> GateResult:
    notes: list[str] = []
    findings = lint_project(project_dir, notes=notes)
    output = "\n".join([*notes, *(str(f) for f in findings)])
    return GateResult("config-lint", passed=not findings, output=output)


def _run_manage_check(project_dir: Path, python: str, *, settings_module: str, gate_name: str) -> GateResult:
    proc = subprocess.run(
        [python, "manage.py", "check"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": settings_module},
    )
    return GateResult(gate_name, proc.returncode == 0, proc.stdout + proc.stderr)


def assemble_scaffold(
    slug: str,
    libs: list[str] | None = None,
    config: dict[str, dict] | None = None,
    *,
    output_dir: Path | None = None,
    project_type: str = "minimal",
    title: str | None = None,
    url: str | None = None,
    company_name: str | None = None,
    company_email: str | None = None,
    python: str | None = None,
    verify: bool = True,
) -> AssembleResult:
    """Assemble a project from a slug + a flat list of Stapel lib keys, then
    statically verify it. See module docstring for the full contract.

    ``project_type`` defaults to ``"minimal"`` — the only preset with a
    boot-smoke gate today (R3/§44 shipped there first); ``monolith``/
    ``microservices`` still run ``manage.py check`` but the boot-smoke gate is
    reported as skipped for them (a follow-up gap, not silently green).
    """
    libs = list(libs or [])
    output_dir = Path(output_dir) if output_dir is not None else Path.cwd()
    python = python or sys.executable

    # Filter against the registry BEFORE calling create_project: an unknown
    # key would otherwise KeyError inside create_project's STAPEL_LIBS[key]
    # lookups — a crash, which is exactly what a mis-typed/not-yet-onboarded
    # lib name must NOT cause.
    #
    # known_libs is normalized to the STAPEL_LIBS registry's own order (not
    # the caller's --libs order): the assembly must be deterministic given the
    # same SET of libs, independent of how the CTO/advisor chain happened to
    # list them — otherwise two runs over the same brief could diverge only in
    # requirements.txt/INSTALLED_APPS ordering, a spurious diff with no
    # behavioural meaning.
    requested = set()
    unknown_libs: list[str] = []
    for lib in libs:
        if lib == "core":
            continue
        if lib in STAPEL_LIBS:
            requested.add(lib)
        elif lib not in unknown_libs:
            unknown_libs.append(lib)
    # Close over hard "requires" (categories -> attributes, etc.) so a caller
    # who only asked for "categories" still gets a project whose pip install
    # actually resolves (create_project does the same closure — this mirrors
    # it here so libs_applied/CONFIG.MD reporting matches what was wired).
    known_libs = _expand_with_requires(list(requested))

    if unknown_libs:
        print(
            "stapel-assemble: gap — lib(s) not in the STAPEL_LIBS registry, "
            "skipped (not a crash, not a bug in the request — the module "
            f"just isn't onboarded to stapel-tools yet): {', '.join(unknown_libs)}",
            file=sys.stderr,
        )

    title = title or " ".join(
        w.capitalize() for w in slug.replace("-", " ").replace("_", " ").split()
    )
    url = url or f"https://{slug}.example.com"
    company_name = company_name or title
    company_email = company_email or f"hello@{slug}.example.com"

    project_dir = output_dir / slug

    create_project(
        name=slug,
        project_type=project_type,
        title=title,
        url=url,
        company_name=company_name,
        company_email=company_email,
        modules=["core", *known_libs],
        output_dir=output_dir,
        use_submodules=False,
        init_git=False,
        module_config=config,
    )

    # Aggregate the connected libs' CONFIG.MD registries into the project's own
    # (§1.2/§2) — the config surface the advisor asks the client about and the
    # config-lint gate checks. Written for every project type; the lint gate
    # itself runs where the layout is fully known (minimal, like boot-smoke).
    config_libs_missing = _write_config_md(project_dir, slug, ["core", *known_libs])

    result = AssembleResult(
        slug=slug,
        project_dir=project_dir,
        libs_applied=known_libs,
        libs_unknown=unknown_libs,
        config_libs_missing=config_libs_missing,
    )

    if verify:
        result.gates.append(
            _run_manage_check(project_dir, python, settings_module="config.settings", gate_name="check")
        )
        if project_type == "minimal":
            result.gates.append(_run_config_lint(project_dir))
            result.gates.append(
                _run_manage_check(
                    project_dir, python,
                    settings_module="config.settings_boot_smoke",
                    gate_name="boot-smoke",
                )
            )
        else:
            result.gates.append(
                GateResult(
                    "config-lint",
                    passed=True,
                    skipped=True,
                    output=(
                        "skipped: the config-lint gate runs on --type minimal today "
                        f"(CONFIG.MD written for {project_type} too, but its "
                        "settings layout is not swept to get_config yet — §2 "
                        "follow-up)."
                    ),
                )
            )
            result.gates.append(
                GateResult(
                    "boot-smoke",
                    passed=True,
                    skipped=True,
                    output=(
                        f"skipped: config/settings_boot_smoke.py is only generated for "
                        f"--type minimal today (R3/§44); {project_type} boot-smoke "
                        "coverage is a follow-up gap, not verified here."
                    ),
                )
            )

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-assemble",
        description=(
            "Static scaffold assembler: create-project + wire every requested "
            "lib (requirements/INSTALLED_APPS/urls/config) + static "
            "verification (manage.py check + boot-smoke), one deterministic "
            "pass, NO LLM."
        ),
    )
    parser.add_argument("slug", help="Project name / slug")
    parser.add_argument(
        "--libs", nargs="*", default=[], metavar="LIB",
        help="Stapel libs to wire in besides core. Registry: "
        + ", ".join(k for k in STAPEL_LIBS if k != "core"),
    )
    parser.add_argument(
        "--config", type=Path, metavar="PATH",
        help="JSON file {module: {SETTING_KEY: value}} — same shape as "
        "stapel-create-project --module-config, rendered as STAPEL_<MOD> "
        "settings blocks.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--type", dest="project_type", default="minimal",
        choices=["minimal", "monolith", "microservices"],
        help="Project type (default: minimal — no Docker, SQLite, the only "
        "preset with a boot-smoke gate today).",
    )
    parser.add_argument("--title")
    parser.add_argument("--url")
    parser.add_argument("--company-name")
    parser.add_argument("--company-email")
    parser.add_argument(
        "--python",
        help="Interpreter to run manage.py check/boot-smoke with (default: "
        "this interpreter — must have Django + the requested libs importable).",
    )
    parser.add_argument(
        "--no-verify", action="store_true",
        help="Skip the check/boot-smoke gates (assembly only).",
    )
    args = parser.parse_args(argv)

    config = None
    if args.config:
        from ._module_config import load_module_config_file

        config = load_module_config_file(args.config)

    result = assemble_scaffold(
        args.slug,
        libs=args.libs,
        config=config,
        output_dir=args.output_dir,
        project_type=args.project_type,
        title=args.title,
        url=args.url,
        company_name=args.company_name,
        company_email=args.company_email,
        python=args.python,
        verify=not args.no_verify,
    )

    print(
        f"stapel-assemble: '{result.slug}' — {len(result.libs_applied)} lib(s) wired "
        f"({', '.join(result.libs_applied) or 'none'}) -> {result.project_dir}"
    )
    if result.libs_unknown:
        print(f"  gap: unknown libs skipped: {', '.join(result.libs_unknown)}", file=sys.stderr)
    if result.config_libs_missing:
        print(
            f"  gap: no CONFIG.MD yet for: {', '.join(result.config_libs_missing)} "
            "(per-module sweep is the next wave — CONFIG.MD still written for the "
            "libs that ship one)",
            file=sys.stderr,
        )
    for gate in result.gates:
        status = "SKIP" if gate.skipped else ("PASS" if gate.passed else "FAIL")
        print(f"  [{status}] {gate.name}")
        if gate.skipped or not gate.passed:
            for line in gate.output.splitlines():
                print(f"    {line}", file=sys.stderr)

    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
