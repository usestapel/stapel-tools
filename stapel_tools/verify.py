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
* ``stapel_tools.authz_lint``      — AUTHZ001-005 (the "credentials verified,
  authorization never asked" class that stapel-core 0.38.0-0.43.0 closed in
  five releases: a ``LoginView.form_valid`` that minted fleet-wide JWTs for
  any active account because no staff gate sat on the minting path, a
  ``refresh_access_token(x, None)`` re-minting from up-to-7-day-old claims,
  two ``get_user()`` overrides that dropped Django's own
  ``user_can_authenticate``, and a revocation blacklist written through
  ``django.core.cache.cache``, whose per-service ``KEY_PREFIX`` made "log out
  everywhere" invisible to peers. Every one of those was found by a human
  reading code — each component's own suite was green, because the defect
  lived in what was NOT written and nothing in the fleet read for absence.)
* ``stapel_tools.api_lint``        — API001-003 + SCHEMA001 (HTTP surface
                                     versioning: a breaking OpenAPI diff must
                                     carry a bump, an UPGRADE.json record and a
                                     vN+1 mounted beside the frozen vN)
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

Per-project profile (legacy projects)
-------------------------------------
Every linter above encodes a *stapel* contract, which is a fair gate only
against a project stapel generated. An imported legacy tree trips hundreds of
them on its first commit — none describing a defect — and the gate becomes a
red wall the operator learns to skim past, which is strictly worse than no
gate. ``stapel_tools.lint_profile`` is the switch: a project-root
``stapel-lint.toml`` declaring, per surface, ``stapel`` (the arsenal, the
default), ``native`` (the project's OWN ruff/eslint IS the gate) or ``off``
(with a mandatory written reason). Absent file ⇒ every surface is ``stapel``,
so a generated project is unaffected.

A ``native`` command is a **shell command out of the repo under inspection**,
so it does not run unless the caller asks for it: ``--run-native`` (Studio's
sandbox passes it; it already runs the project's own ``make controls`` there).
Without the flag the surface is reported as declared-but-not-run, never
silently green-by-omission.

Usage
-----
    stapel-verify <project_root> [--workspace ROOT ...] [--base-sha SHA]
                  [--run-native] [--json]

``--workspace`` and ``--base-sha`` are forwarded to the sub-linters that
accept them (adoption-lint and surface-lint; migration-lint and api-lint
respectively — for api-lint the ref is the baseline whose committed
``docs/schema.json`` the current one is diffed against, defaulting to the
newest reachable ``v<semver>`` tag when not given); the other linters
ignore what does not apply to them.

Exit codes: 0 all clean (warnings allowed), 1 any linter reported at least one
error (or a native gate came back non-zero), 2 usage/environment error (bad
project_root, unreadable profile).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from . import (
    adoption_lint,
    api_lint,
    authz_lint,
    config_lint,
    doc_lint,
    env_address_lint,
    exposure_lint,
    frontend_delivery_lint,
    index_lint,
    lint,
    lint_profile,
    migration_lint,
    nginx_cache_lint,
    po_lint,
    surface_lint,
    swap_lint,
    url_lint,
)
from .lint_profile import (
    MODE_NATIVE,
    MODE_OFF,
    MODE_STAPEL,
    LintProfile,
    LintProfileError,
)


@dataclasses.dataclass
class LinterReport:
    name: str
    errors: int
    warnings: int
    findings: list[dict]
    notes: list[str] = dataclasses.field(default_factory=list)
    #: which profile surface this report belongs to ("" for the whole-project
    #: profile note line itself)
    surface: str = ""
    #: the surface's profile mode — "stapel" | "native" | "off"
    mode: str = MODE_STAPEL
    #: True when the linter did not run at all (surface off, or replaced by a
    #: native gate, or a native gate declared with --run-native withheld).
    #: A skipped report never carries findings and never fails the run — but
    #: it is always PRINTED, so "not checked" is visible next to "checked".
    skipped: bool = False


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


def run_authz_lint(project: Path) -> LinterReport:
    notes: list[str] = []
    findings = authz_lint.lint_project(project, notes=notes)
    errors, warnings = _count(findings)
    return LinterReport("stapel-authz-lint", errors, warnings, _to_dicts(findings), notes)


def run_api_lint(project: Path, base_ref: Optional[str]) -> LinterReport:
    notes: list[str] = []
    findings = api_lint.lint_project(project, base_ref=base_ref, notes=notes)
    errors, warnings = _count(findings)
    return LinterReport("stapel-api-lint", errors, warnings, _to_dicts(findings), notes)


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


# ---------------------------------------------------------------------------
# the native gate — the project's OWN linter, when the profile says so
# ---------------------------------------------------------------------------

#: (command, cwd) -> (returncode, combined output). Injectable so the suite
#: proves the wiring without shelling out, and so an embedder (Studio) can
#: route the call through its own sandbox instead of this process.
NativeRunner = Callable[[str, Path], "tuple[int, str]"]

#: The composition order, names only — the introspectable handle a linter's
#: own "am I actually wired into the gate?" test asserts against. Bytecode
#: introspection used to serve that purpose and broke the moment the calls
#: moved behind the profile switch; a declared tuple cannot drift silently,
#: because ``verify_project`` compares its own list against it.
COMPOSED_LINTERS: tuple[str, ...] = (
    "stapel-lint",
    "stapel-adoption-lint",
    "stapel-url-lint",
    "stapel-authz-lint",
    "stapel-api-lint",
    "stapel-config-lint",
    "stapel-migration-lint",
    "stapel-swap-lint",
    "stapel-doc-lint",
    "stapel-surface-lint",
    "stapel-index-lint",
    "stapel-nginx-cache-lint",
    "stapel-env-address-lint",
    "stapel-frontend-delivery-lint",
    "stapel-po-lint",
    "stapel-exposure-lint",
)


def _subprocess_runner(command: str, cwd: Path) -> "tuple[int, str]":
    proc = subprocess.run(
        command, shell=True, cwd=str(cwd), capture_output=True, text=True,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def run_native_gate(
    surface: lint_profile.SurfaceProfile, project: Path, runner: NativeRunner,
) -> LinterReport:
    """Run the project's own linter for one surface and turn its exit code
    into the same verdict shape every other report carries.

    One error, not a parsed finding list: the whole point of a native gate is
    that stapel does not know that tool's output format. The command's exit
    code is the verdict and its tail is the evidence — which is exactly what
    the coder loop needs, since it already reads ``make controls`` tails the
    same way.
    """
    code, output = runner(surface.command, project)
    tail = output.strip()[-4000:]
    notes = [f"native gate: {surface.command}"]
    if code == 0:
        notes.append("exit 0")
    return LinterReport(
        name=f"native:{surface.surface}",
        errors=0 if code == 0 else 1,
        warnings=0,
        findings=[] if code == 0 else [{
            "path": surface.command, "line": 0, "rule": "NATIVE",
            "message": tail or f"exited {code} with no output",
            "level": "error",
        }],
        notes=notes,
        surface=surface.surface,
        mode=MODE_NATIVE,
    )


def _skipped(name: str, surface: lint_profile.SurfaceProfile, note: str) -> LinterReport:
    return LinterReport(
        name=name, errors=0, warnings=0, findings=[], notes=[note],
        surface=surface.surface, mode=surface.mode, skipped=True,
    )


def _apply_waivers(report: LinterReport, waivers: dict[str, str]) -> LinterReport:
    """Drop findings whose rule id is waived, and say so in the notes.

    Same canon as ``STAPEL_SECURITY_CHECK_WAIVERS``: per-id, with a written
    reason, echoed on every run. A waiver that matched nothing is reported by
    :func:`profile_notes` instead, so the dict cannot rot into a blanket list.
    """
    if not waivers:
        return report
    kept, dropped = [], []
    for f in report.findings:
        if f.get("rule") in waivers:
            dropped.append(f["rule"])
        else:
            kept.append(f)
    if not dropped:
        return report
    report.findings = kept
    report.errors = sum(1 for f in kept if f["level"] == "error")
    report.warnings = len(kept) - report.errors
    for rule in sorted(set(dropped)):
        n = sum(1 for r in dropped if r == rule)
        report.notes.append(f"waived {rule} x{n}: {waivers[rule]}")
    return report


def verify_project(
    project: Path,
    *,
    workspace: Optional[list[Path]] = None,
    base_sha: Optional[str] = None,
    profile: Optional[LintProfile] = None,
    run_native: bool = False,
    native_runner: Optional[NativeRunner] = None,
) -> list[LinterReport]:
    """Run the stapel lint arsenal against ``project``, filtered by its
    per-project profile. Returns one :class:`LinterReport` per linter, in a
    fixed order, plus one per ``native`` surface.

    ``profile`` defaults to ``lint_profile.load_profile(project)`` — the
    project's own ``stapel-lint.toml``, or the full arsenal when it has none.
    """
    project = project.resolve()
    search_roots = [project.parent] + list(workspace or [])
    prof = profile if profile is not None else lint_profile.load_profile(project)

    # name -> zero-arg call. Order is the fixed composition order; the profile
    # only ever replaces an entry with a skipped report, never reorders.
    composed: list[tuple[str, Callable[[], LinterReport]]] = [
        ("stapel-lint", lambda: run_lint(project)),
        ("stapel-adoption-lint", lambda: run_adoption_lint(project, search_roots)),
        ("stapel-url-lint", lambda: run_url_lint(project)),
        ("stapel-authz-lint", lambda: run_authz_lint(project)),
        ("stapel-api-lint", lambda: run_api_lint(project, base_sha)),
        ("stapel-config-lint", lambda: run_config_lint(project)),
        ("stapel-migration-lint", lambda: run_migration_lint(project, base_sha)),
        ("stapel-swap-lint", lambda: run_swap_lint(project)),
        ("stapel-doc-lint", lambda: run_doc_lint(project)),
        ("stapel-surface-lint", lambda: run_surface_lint(project, search_roots)),
        ("stapel-index-lint", lambda: run_index_lint(project)),
        ("stapel-nginx-cache-lint", lambda: run_nginx_cache_lint(project)),
        ("stapel-env-address-lint", lambda: run_env_address_lint(project)),
        ("stapel-frontend-delivery-lint", lambda: run_frontend_delivery_lint(project)),
        ("stapel-po-lint", lambda: run_po_lint(project)),
        ("stapel-exposure-lint", lambda: run_exposure_lint(project)),
    ]
    if [n for n, _ in composed] != list(COMPOSED_LINTERS):  # pragma: no cover
        raise LintProfileError(
            "verify_project's composition drifted from COMPOSED_LINTERS — the "
            "declared order is what every consumer introspects"
        )
    # A linter missing from LINTER_SURFACES would escape every profile
    # silently — the one failure mode a switch like this must not have.
    missing = [n for n, _ in composed if n not in lint_profile.LINTER_SURFACES]
    if missing:  # pragma: no cover - guarded by test_lint_profile
        raise LintProfileError(
            f"linter(s) {', '.join(missing)} are composed by stapel-verify but "
            f"carry no surface in lint_profile.LINTER_SURFACES"
        )

    reports: list[LinterReport] = []
    for name, call in composed:
        surface = prof.for_linter(name)
        if surface.mode == MODE_OFF:
            reports.append(_skipped(
                name, surface,
                f"skipped: surface '{surface.surface}' is off — {surface.reason}",
            ))
            continue
        if surface.mode == MODE_NATIVE:
            reports.append(_skipped(
                name, surface,
                f"skipped: surface '{surface.surface}' is gated by the "
                f"project's own linter ({surface.command})",
            ))
            continue
        report = call()
        report.surface = surface.surface
        report.mode = MODE_STAPEL
        reports.append(_apply_waivers(report, prof.waivers))

    runner = native_runner or _subprocess_runner
    for surface in prof.native_surfaces():
        if run_native:
            reports.append(run_native_gate(surface, project, runner))
        else:
            reports.append(_skipped(
                f"native:{surface.surface}", surface,
                f"declared but NOT RUN (pass --run-native): {surface.command}",
            ))
    return reports


def profile_notes(profile: LintProfile, reports: list[LinterReport]) -> list[str]:
    """Header lines describing what this run did *not* check, and why.

    Printed above the table and carried in the JSON, because the one thing a
    profile must never do is make a surface disappear from the report.
    """
    if not profile.present:
        return []
    lines = [f"lint profile: {profile.path}"] + [f"  {s}" for s in profile.summary()]
    fired = {
        n.split(" x")[0].removeprefix("waived ")
        for r in reports for n in r.notes if n.startswith("waived ")
    }
    for rule in sorted(set(profile.waivers) - fired):
        lines.append(f"  waiver {rule} matched nothing this run")
    return lines


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def _print_table(
    reports: list[LinterReport], project: Path, notes: Optional[list[str]] = None,
) -> None:
    name_w = max(len(r.name) for r in reports)
    print(f"stapel-verify: {project}\n")
    for line in notes or []:
        print(line)
    if notes:
        print()
    header = f"{'linter':<{name_w}}  errors  warnings"
    print(header)
    print("-" * len(header))
    for r in reports:
        if r.skipped:
            print(f"{r.name:<{name_w}}  {'—':>6}  {'—':>8}  ({r.mode})")
            continue
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
    skipped = sum(1 for r in reports if r.skipped)
    ran = len(reports) - skipped
    print()
    if skipped:
        print(f"{skipped} of {len(reports)} linters did not run "
              f"(see the lint profile above).")
    if total_errors or total_warnings:
        parts = []
        if total_errors:
            parts.append(f"{total_errors} error{'s' if total_errors != 1 else ''}")
        if total_warnings:
            parts.append(f"{total_warnings} warning{'s' if total_warnings != 1 else ''}")
        print(f"{', '.join(parts)} found across {ran} linter{'s' if ran != 1 else ''}"
              f"{' that ran' if skipped else ''}.")
    else:
        # "All clean across 15 linters" when 9 of them never ran is the exact
        # false reassurance the profile mechanism exists to prevent — count
        # what actually ran.
        print(f"All clean across {ran} linter{'s' if ran != 1 else ''}"
              f"{' that ran' if skipped else ''}.")


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
             "stapel-migration-lint's MIG002 base-sha check and as "
             "stapel-api-lint's schema baseline (default: newest "
             "reachable v<semver> tag).",
    )
    parser.add_argument(
        "--run-native", action="store_true",
        help="Execute the native gate command(s) a stapel-lint.toml profile "
             "declares. Off by default: a native command is a shell command "
             "out of the tree under inspection, so running it is the "
             "caller's decision (Studio's sandbox passes this; a bare local "
             "run of an untrusted checkout should not).",
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

    try:
        profile = lint_profile.load_profile(project)
    except LintProfileError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    workspace = [Path(w).resolve() for w in args.workspace]
    reports = verify_project(
        project, workspace=workspace, base_sha=args.base_sha,
        profile=profile, run_native=args.run_native,
    )
    notes = profile_notes(profile, reports)

    total_errors = sum(r.errors for r in reports)
    total_warnings = sum(r.warnings for r in reports)

    if args.json:
        print(json.dumps(
            {
                "ok": total_errors == 0,
                "errors": total_errors,
                "warnings": total_warnings,
                "profile": {
                    "path": profile.path,
                    "present": profile.present,
                    "surfaces": {
                        s: dataclasses.asdict(profile.for_surface(s))
                        for s in lint_profile.SURFACES
                    },
                    "waivers": profile.waivers,
                    "notes": notes,
                },
                "linters": [dataclasses.asdict(r) for r in reports],
            },
            indent=2, sort_keys=True, ensure_ascii=False,
        ))
    else:
        _print_table(reports, project.resolve(), notes)

    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
