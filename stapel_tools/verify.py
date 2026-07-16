"""
stapel-verify — one gate that runs the entire stapel lint arsenal on a client
project.

Why this exists
----------------
Each linter in this package is independently useful, and independently
*optional*: nothing forces a client project's CI to run all of them. A
"migration" has twice landed with a green generic linter while R006 (raw
``StapelResponse({...})``, skipping the serializer) and ADO002 (a hand-rolled
route shadowing an operation the installed module already ships) sat
unexercised — not because the checks don't exist, but because nobody wired
them into the pipeline that ran. stapel-verify is the mechanical answer: one
command that runs every existing linter's real implementation — no
reimplemented rules, pure composition — against a project and fails if any of
them reported an error.

Linters composed (in this order)
---------------------------------
* ``stapel_tools.lint``            — R-codes (StapelResponse/ErrorResponse
  discipline, flow attachment, i18n doc links)
* ``stapel_tools.adoption_lint``   — ADO-codes (module actually mounted, no
  shadow routes, migration shipped on main, no dead requirement pins)
* ``stapel_tools.url_lint``        — URL001 (bare ``URLField()`` truncation
  trap)
* ``stapel_tools.config_lint``     — CFG-codes (config-in-one-place law)
* ``stapel_tools.migration_lint``  — MIG-codes (expand/contract discipline)

Usage
-----
    stapel-verify <project_root> [--workspace ROOT ...] [--base-sha SHA] [--json]

``--workspace`` and ``--base-sha`` are forwarded to the sub-linters that
accept them (adoption-lint, migration-lint respectively); the other linters
ignore what does not apply to them.

Exit codes: 0 all clean (warnings allowed), 1 any linter reported at least one
error, 2 usage/environment error (bad project_root).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Optional

from . import adoption_lint, config_lint, lint, migration_lint, url_lint


@dataclasses.dataclass
class LinterReport:
    name: str
    errors: int
    warnings: int
    findings: list[dict]
    notes: list[str] = dataclasses.field(default_factory=list)


def _to_dicts(items) -> list[dict]:
    """Every linter's per-violation dataclass shares the same shape (path,
    line, rule, message, level) — ``dataclasses.asdict`` works uniformly
    across all five without depending on each module's own ``to_dict``."""
    return [dataclasses.asdict(v) for v in items]


def _count(items) -> tuple[int, int]:
    errors = sum(1 for v in items if v.level == "error")
    warnings = sum(1 for v in items if v.level != "error")
    return errors, warnings


# ---------------------------------------------------------------------------
# one wrapper per linter — reuses its public API, adds no checking logic
# ---------------------------------------------------------------------------


def run_lint(project: Path) -> LinterReport:
    violations = lint.scan_paths([str(project)])
    errors, warnings = _count(violations)
    return LinterReport("stapel-lint", errors, warnings, _to_dicts(violations))


def run_adoption_lint(project: Path, search_roots: list[Path]) -> LinterReport:
    notes: list[str] = []
    findings = adoption_lint.lint_project(project, search_roots=search_roots, notes=notes)
    errors, warnings = _count(findings)
    return LinterReport("stapel-adoption-lint", errors, warnings, _to_dicts(findings), notes)


def run_url_lint(project: Path) -> LinterReport:
    violations = url_lint.lint_paths([str(project)])
    errors, warnings = _count(violations)
    return LinterReport("stapel-url-lint", errors, warnings, _to_dicts(violations))


def run_config_lint(project: Path) -> LinterReport:
    notes: list[str] = []
    findings = config_lint.lint_project(project, notes=notes)
    errors, warnings = _count(findings)
    return LinterReport("stapel-config-lint", errors, warnings, _to_dicts(findings), notes)


def run_migration_lint(project: Path, base_sha: Optional[str]) -> LinterReport:
    violations, _apps = migration_lint.lint_paths([str(project)], base_sha=base_sha)
    errors, warnings = _count(violations)
    return LinterReport("stapel-migration-lint", errors, warnings, _to_dicts(violations))


def verify_project(
    project: Path,
    *,
    workspace: Optional[list[Path]] = None,
    base_sha: Optional[str] = None,
) -> list[LinterReport]:
    """Run every stapel linter against ``project``. Returns one
    :class:`LinterReport` per linter, in a fixed order."""
    project = project.resolve()
    search_roots = [project.parent] + list(workspace or [])
    return [
        run_lint(project),
        run_adoption_lint(project, search_roots),
        run_url_lint(project),
        run_config_lint(project),
        run_migration_lint(project, base_sha),
    ]


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def _print_table(reports: list[LinterReport], project: Path) -> None:
    name_w = max(len(r.name) for r in reports)
    print(f"stapel-verify: {project}\n")
    header = f"{'linter':<{name_w}}  errors  warnings"
    print(header)
    print("-" * len(header))
    for r in reports:
        print(f"{r.name:<{name_w}}  {r.errors:>6}  {r.warnings:>8}")

    for r in reports:
        for note in r.notes:
            print(f"  [{r.name}] {note}", file=sys.stderr)

    findings_present = any(r.findings for r in reports)
    if findings_present:
        print()
        for r in reports:
            for f in r.findings:
                tag = f["rule"] if f["level"] == "error" else f"{f['rule']} warning"
                print(f"[{r.name}] {f['path']}:{f['line']}: [{tag}] {f['message']}")

    total_errors = sum(r.errors for r in reports)
    total_warnings = sum(r.warnings for r in reports)
    print()
    if total_errors or total_warnings:
        parts = []
        if total_errors:
            parts.append(f"{total_errors} error{'s' if total_errors != 1 else ''}")
        if total_warnings:
            parts.append(f"{total_warnings} warning{'s' if total_warnings != 1 else ''}")
        print(f"{', '.join(parts)} found across {len(reports)} linters.")
    else:
        print(f"All clean across {len(reports)} linters.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-verify",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "project_dir", nargs="?", default=".",
        help="Project directory to verify (default: .)",
    )
    parser.add_argument(
        "--workspace", action="append", default=[],
        help="Extra root to search for sibling stapel-<mod> repos (repeatable) "
             "— forwarded to stapel-adoption-lint.",
    )
    parser.add_argument(
        "--base-sha", metavar="SHA",
        help="Git sha of the previous release — forwarded to "
             "stapel-migration-lint's MIG002 base-sha check.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Machine output: per-linter errors/warnings/findings (for agents/CI).",
    )
    args = parser.parse_args(argv)

    project = Path(args.project_dir)
    if not project.is_dir():
        print(f"Error: not a directory: {project}", file=sys.stderr)
        return 2

    workspace = [Path(w).resolve() for w in args.workspace]
    reports = verify_project(project, workspace=workspace, base_sha=args.base_sha)

    total_errors = sum(r.errors for r in reports)
    total_warnings = sum(r.warnings for r in reports)

    if args.json:
        print(json.dumps(
            {
                "ok": total_errors == 0,
                "errors": total_errors,
                "warnings": total_warnings,
                "linters": [dataclasses.asdict(r) for r in reports],
            },
            indent=2, sort_keys=True, ensure_ascii=False,
        ))
    else:
        _print_table(reports, project.resolve())

    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
