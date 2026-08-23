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
  shadow routes, migration shipped on main, no dead requirement pins, an
  installed gdpr data owner reachable by an erasure)
* ``stapel_tools.url_lint``        — URL001 (bare ``URLField()`` truncation
  trap)
* ``stapel_tools.config_lint``     — CFG-codes (config-in-one-place law)
* ``stapel_tools.migration_lint``  — MIG-codes (expand/contract discipline)
* ``stapel_tools.swap_lint``       — SWAP001/SWAP002/SWAP003/SWAP004 (§55
  anti-lock-in: swappable model/presenter indirection, DTOs built only
  through a presenter, and no hardcoded dotted path into another top-level
  package — the ``stapel-workspaces 0.19.0`` shape that worked in a monolith
  and answered a permanent 503 in a split deployment, because a symbol
  resolution has no remote form. A path that comes from configuration is the
  extension mechanism and is never flagged; only a string literal naming
  somebody else's package is. SWAP004 bans importing a vendor SDK a fleet
  library owns the integration for — ``livekit`` outside ``stapel_video`` —
  because a provider call added in product code is a capability no other
  consumer gets and a fix the product can no longer receive)
* ``stapel_tools.doc_lint``        — DOC001 (§55 DOC-FIELD: model field docs,
  warning-level while the legacy sweep is in progress)
* ``stapel_tools.index_lint``      — IDX-codes (the search-index contract:
  a field stored on an index model that ``docs/index.json`` does not account
  for, a declared query read path some shipped backend does not answer, a
  declared proving test that does not resolve, and a document field pulled
  from the source that lands nowhere. Composed HERE for the same reason as
  the nginx rules: a project picks the gate up on its next stapel-tools
  upgrade with nothing to regenerate — and it is silent by design in a
  project that ships no ``docs/index.json`` at all. The class it closes:
  ``features_search``, ``description_en`` and ``geohash`` were written into
  the legacy index and read by no query, for years, because writing a field
  and reading a field are different files and nothing connected them.)
* ``stapel_tools.surface_lint``    — SUR-codes (pre-merge gate against
  reinventing what the fleet already publishes: a permission class the project
  re-declares under a published name, a displaced symbol used where its
  replacement is never used at all, a ``gate_function`` imported and never
  called, a capability field published for a consumer that never reads it).
  Composed HERE for the same reason as the nginx rules: it reads the ``surface``
  section of whatever ``capabilities.json`` the project's environment and
  workspace expose, so a project picks the gate up on its next stapel-tools
  upgrade with nothing to regenerate — and stays silent, by design, in an
  environment whose installed modules ship no contract document yet.
* ``stapel_tools.nginx_cache_lint`` — NGX-codes (SPA cache canon: the unhashed
  entry document must revalidate, hashed assets must be immutable, and no
  location may emit both ``expires`` and ``add_header Cache-Control``).
  Composed HERE on purpose: every generated project's pre-commit already runs
  ``stapel-verify .``, so a project that already exists — including one whose
  nginx conf was hand-maintained and never came from the scaffold — picks the
  gate up on its next stapel-tools upgrade, with nothing to regenerate.
* ``stapel_tools.po_lint``         — PO-codes (gettext catalogue gate: no
  fuzzy, no obsolete, and no entry parked for a string this tree does not own.
  Composed HERE for the same reason as the nginx rules — every generated
  project's pre-commit already runs ``stapel-verify .``, so a project picks the
  gate up on its next stapel-tools upgrade with nothing to regenerate — and it
  is silent by design in a project that ships no ``locale/`` at all. The class
  it closes: a bare ``makemessages`` demotes every entry whose source it cannot
  find, gettext skips fuzzy and obsolete alike, and a suite that asserts almost
  no strings stays green while the product reverts to its source language.
* ``stapel_tools.exposure_lint``   — EXP-codes (a private client name must
  not reach a public stapel-*/@stapel/* tree; list lives OUTSIDE the repo)
* ``stapel_tools.env_address_lint`` — EADDR-codes (the "address that belongs
  to the environment, frozen into a file that outlives it" class:
  ``docs/pending/env-address-class-v2.md``). EADDR001 catches a literal
  private IP endpoint in a deploy-class file; EADDR002 is itself the delivery
  channel for the upstream-reachability gate into a project that has not
  mounted it yet (same "rule as transport" idiom as the nginx-cache rules
  above); EADDR003 catches an env-boundary proxy location with no fast
  ``proxy_connect_timeout``, which is half of why the original incident read
  as "server load" for a full day instead of "wrong address".

Usage
-----
    stapel-verify <project_root> [--workspace ROOT ...] [--base-sha SHA] [--json]

``--workspace`` and ``--base-sha`` are forwarded to the sub-linters that
accept them (adoption-lint and surface-lint, migration-lint respectively);
the other linters ignore what does not apply to them.

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

from . import (
    adoption_lint,
    config_lint,
    doc_lint,
    env_address_lint,
    exposure_lint,
    frontend_delivery_lint,
    index_lint,
    lint,
    migration_lint,
    nginx_cache_lint,
    po_lint,
    surface_lint,
    swap_lint,
    url_lint,
)


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


def run_swap_lint(project: Path) -> LinterReport:
    violations = swap_lint.lint_paths([str(project)])
    errors, warnings = _count(violations)
    return LinterReport("stapel-swap-lint", errors, warnings, _to_dicts(violations))


def run_doc_lint(project: Path) -> LinterReport:
    violations = doc_lint.lint_paths([str(project)])
    errors, warnings = _count(violations)
    return LinterReport("stapel-doc-lint", errors, warnings, _to_dicts(violations))


def run_index_lint(project: Path) -> LinterReport:
    notes: list[str] = []
    findings = index_lint.lint_project(project, notes=notes)
    errors, warnings = _count(findings)
    return LinterReport("stapel-index-lint", errors, warnings, _to_dicts(findings), notes)


def run_surface_lint(project: Path, search_roots: list[Path]) -> LinterReport:
    notes: list[str] = []
    findings = surface_lint.lint_project(
        project, search_roots=search_roots, notes=notes
    )
    errors, warnings = _count(findings)
    return LinterReport("stapel-surface-lint", errors, warnings, _to_dicts(findings), notes)


def run_nginx_cache_lint(project: Path) -> LinterReport:
    notes: list[str] = []
    findings = nginx_cache_lint.lint_project(project, notes=notes)
    errors, warnings = _count(findings)
    return LinterReport("stapel-nginx-cache-lint", errors, warnings, _to_dicts(findings), notes)


def run_env_address_lint(project: Path) -> LinterReport:
    notes: list[str] = []
    findings = env_address_lint.lint_project(project, notes=notes)
    errors, warnings = _count(findings)
    return LinterReport("stapel-env-address-lint", errors, warnings, _to_dicts(findings), notes)


def run_exposure_lint(project: Path) -> LinterReport:
    notes: list[str] = []
    findings = exposure_lint.lint_project(project, notes=notes)
    errors, warnings = _count(findings)
    return LinterReport("stapel-exposure-lint", errors, warnings, _to_dicts(findings), notes)


def run_po_lint(project: Path) -> LinterReport:
    notes: list[str] = []
    findings = po_lint.lint_project(project, notes=notes)
    errors, warnings = _count(findings)
    return LinterReport("stapel-po-lint", errors, warnings, _to_dicts(findings), notes)


def run_frontend_delivery_lint(project: Path) -> LinterReport:
    notes: list[str] = []
    findings = frontend_delivery_lint.lint_project(project, notes=notes)
    errors, warnings = _count(findings)
    return LinterReport(
        "stapel-frontend-delivery-lint", errors, warnings, _to_dicts(findings), notes
    )


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
        run_swap_lint(project),
        run_doc_lint(project),
        run_surface_lint(project, search_roots),
        run_index_lint(project),
        run_nginx_cache_lint(project),
        run_env_address_lint(project),
        run_frontend_delivery_lint(project),
        run_po_lint(project),
        run_exposure_lint(project),
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
        help="Extra root to search for sibling stapel-<mod> / stapel-react "
             "repos (repeatable) — forwarded to stapel-adoption-lint and "
             "stapel-surface-lint.",
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
