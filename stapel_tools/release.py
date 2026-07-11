"""
stapel-release-manifest — build the open ``release.json`` manifest
(docs/release-management.md §1, R-1).

``release.json`` is the OSS artifact of a Release: an immutable description
of ONE gated build — baked into the image at ``/app/release.json`` and stored
in the release registry. The schema is open (any tooling may read it); the
private platform models (Release/Environment, promote/rollback UI, registry
provisioning) are R-2 and NOT here. This module only *describes* a checkout —
it never bakes images, never promotes, never touches a database.

Watermark semantics (deliberate, per §1/§3)
-------------------------------------------
``migrations`` records, per Django app present in the project tree, the MAX
migration FILE in the codebase at the release sha — NOT any database's
applied state. The manifest describes the release *artifact* (the highest
schema this build can migrate a DB up to); deployment state is runtime data
that belongs to the environment, not to the immutable release. Rollback
tooling compares a DB's applied state against these watermarks at promote/
rollback time (§2). Apps not shipped in the tree (pip-installed stapel
modules, django.contrib) are covered transitively: their migrations are
pinned by ``contracts`` / the base image, not enumerated here.

Schema (schema_version 1)
-------------------------
{
  "schema_version": 1,
  "release":          "r<N>"                     — monotonic per project,
  "project":          "<name>",
  "git_sha":          "<gated sha>",
  "images":           {service: registry-tag},
  "migrations":       {django_app: watermark},
  "reversible_floor": {django_app: earliest_safe_target}  # "zero" = full
                       rollback; otherwise the latest irreversible
                       migration's name (see stapel_tools.migration_lint),
  "contracts":        {stapel_module: version}   — exact version when
                       determinable (vendored checkout's pyproject, ==pin,
                       git tag); otherwise the requirement spec verbatim
                       (resolving a range pin to the exact installed version
                       is the bake step's job — it has the built image),
  "config_digest":    "sha256:…" over the STAPEL_<MOD> settings blocks
                       (the config *fingerprint*; actual values live in the
                       secret store, never in the manifest),
  "gates":            {prodguard, handover_scan, migration_lint} — recorded
                       RESULTS ("pass"/"fail"), null = not run. migration_lint
                       is computed here (shared analyzer); prodguard/
                       handover_scan results are supplied by the pipeline
                       that ran them (--gate). The builder records honestly
                       and still emits on failure — it is a describer, not a
                       gate; pipelines must run stapel-migration-lint (and
                       friends) as the actual gate,
  "created_at":       ISO-8601 UTC (override: --created-at or
                       SOURCE_DATE_EPOCH for reproducible builds),
  "notes":            free text or null
}

Output is deterministic: sorted keys, fixed indent, trailing newline — the
same drift-gate discipline as the codegen artifacts. Same inputs (including
created_at) → byte-identical output.

Contract artifact freshness (REL001/REL002, process-gap §26)
--------------------------------------------------------------
A version bump in ``pyproject.toml`` that forgets to regenerate the contract
triad (``make contract``) leaves a stale ``version`` baked into
``docs/capabilities.json`` — the release tags clean, but the contract tests
go red on the very next check. Unlike migration_lint (recorded into
``gates``, not fatal here — the pipeline is the actual gate), this check is
fatal to the manifest build itself: it must be caught BEFORE the tag, not
after.

REL001  a ``docs/*.json`` contract artifact's own embedded TOP-LEVEL
        ``version`` is behind the repo's ``pyproject.toml`` → ERROR,
        ``build_manifest``/the CLI aborts (no manifest is emitted). Only
        ``capabilities.json`` carries a version that actually tracks the
        module (``stapel_tools.capabilities`` writes it verbatim from
        pyproject); ``schema.json``'s OpenAPI version lives nested under
        ``info`` (a drf-spectacular placeholder, never wired to the module
        version — see ``_codegen_settings.py``) and ``flows.json`` /
        ``errors.json`` are bare lists with no envelope, so looking only at
        the TOP level naturally skips both without special-casing filenames.
REL002  ``docs/capabilities.json`` is missing while the repo has a
        ``make contract`` target (a ``contract:`` rule in its ``Makefile``)
        → WARNING, printed but non-fatal — the artifact was presumably never
        emitted.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .migration_lint import (
    SKIP_DIRS,
    Violation,
    app_report,
    lint_paths,
)

SCHEMA_VERSION = 1
_RELEASE_RE = re.compile(r"^r[1-9][0-9]*$|^r0$")
_STAPEL_REQ_RE = re.compile(r"^(stapel[-_][A-Za-z0-9._\-]+)\s*(.*)$")
_STAPEL_DIR_RE = re.compile(r"^stapel[-_][a-z0-9_\-]+$")
_STAPEL_SETTING_RE = re.compile(r"^STAPEL_[A-Z0-9_]+$")
# stapel-assemble/stapel-create-project render ahead-of-PyPI modules as an
# editable sibling-checkout install (`-e ../stapel-core`), not a `stapel-core
# @ ...` spec — _STAPEL_REQ_RE alone would silently drop these from the
# contracts map. The version rides in the comment line immediately above
# (`# stapel_core — vX.Y.Z ...`, written by _setup_pip_deps).
_STAPEL_EDITABLE_RE = re.compile(r"^-e\s+\.\./(stapel[-_][A-Za-z0-9._\-]+)\s*$")
_STAPEL_COMMENT_PIN_RE = re.compile(r"\bv([0-9][\w.\-]*)\b")
_GATE_KEYS_OVERRIDABLE = ("prodguard", "handover_scan")


def _norm_module(name: str) -> str:
    """PEP 503-style module key: lowercase, underscores → dashes."""
    return name.strip().lower().replace("_", "-")


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and not d.endswith(".egg-info")
        )
        yield Path(dirpath), dirnames, sorted(filenames)


# ---------------------------------------------------------------------------
# contracts{} — stapel-* pins (§17/§7 discipline lifted to the app level)
# ---------------------------------------------------------------------------


def _version_from_spec(spec: str) -> str:
    """Best-effort exact version from a requirement spec; the raw spec when
    only a range is pinned; 'unpinned' when nothing is."""
    spec = spec.strip()
    if not spec:
        return "unpinned"
    if spec.startswith("==") and "," not in spec:
        return spec[2:].strip()
    match = re.search(r"@\s*git\+\S+@([A-Za-z0-9._\-]+)$", spec)
    if match:
        ref = match.group(1)
        return ref[1:] if re.fullmatch(r"v\d[\w.\-]*", ref) else ref
    return spec


def _parse_requirement_line(line: str) -> Optional[tuple]:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    # strip trailing comments ("pkg==1  # why"); '#egg=' fragments have no
    # preceding space and survive
    line = line.split(" #")[0].strip()
    match = _STAPEL_REQ_RE.match(line)
    if not match:
        return None
    return _norm_module(match.group(1)), _version_from_spec(match.group(2))


def collect_contracts(project_dir: Path) -> dict:
    """{stapel_module: version} from requirement files, pyproject dependency
    lists, and vendored module checkouts (checkout's own pyproject wins —
    it is exact)."""
    contracts: dict = {}
    vendored: dict = {}

    for dirpath, dirnames, filenames in _walk(project_dir):
        # vendored checkout (git submodule / copied module repo)
        if (
            _STAPEL_DIR_RE.match(dirpath.name)
            and "pyproject.toml" in filenames
            and dirpath != project_dir
        ):
            try:
                doc = tomllib.loads((dirpath / "pyproject.toml").read_text(encoding="utf-8"))
                proj = doc.get("project", {})
                name, version = proj.get("name"), proj.get("version")
                if name and version:
                    vendored[_norm_module(name)] = str(version)
            except (OSError, ValueError):
                pass
            dirnames[:] = []  # a module's own deps are module pins, not project pins
            continue

        for fname in filenames:
            if fname.startswith("requirements") and fname.endswith(".txt"):
                try:
                    lines = (dirpath / fname).read_text(encoding="utf-8").splitlines()
                except (OSError, UnicodeDecodeError):
                    continue
                prev_line = ""
                for line in lines:
                    parsed = _parse_requirement_line(line)
                    if parsed:
                        contracts.setdefault(*parsed)
                    else:
                        editable = _STAPEL_EDITABLE_RE.match(line.strip())
                        if editable:
                            pin_match = _STAPEL_COMMENT_PIN_RE.search(prev_line)
                            version = f"v{pin_match.group(1)}" if pin_match else "unpinned"
                            contracts.setdefault(_norm_module(editable.group(1)), version)
                    prev_line = line
            elif fname == "pyproject.toml":
                try:
                    doc = tomllib.loads((dirpath / fname).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for dep in doc.get("project", {}).get("dependencies", []) or []:
                    parsed = _parse_requirement_line(str(dep))
                    if parsed:
                        contracts.setdefault(*parsed)

    contracts.update(vendored)
    return contracts


# ---------------------------------------------------------------------------
# contract artifact freshness — REL001/REL002 (process-gap §26, see module
# docstring): a version bump must not outrun `make contract`.
# ---------------------------------------------------------------------------

_CONTRACT_ARTIFACTS = ("capabilities.json", "schema.json", "flows.json", "errors.json")
_CONTRACT_TARGET_RE = re.compile(r"(?m)^contract:")


def check_contract_freshness(project_dir: Path) -> list:
    """Compare each ``docs/*.json`` contract artifact's own embedded
    TOP-LEVEL ``version`` (when it carries one) against this repo's
    ``pyproject.toml`` ``project.version``. See the module docstring for why
    only ``capabilities.json`` is ever actually caught by REL001, and why
    that is correct (not a gap): ``schema.json``'s OpenAPI version is nested
    under ``info``, not top-level, and ``flows.json``/``errors.json`` are
    bare lists — neither carries a real envelope version to compare.

    No ``pyproject.toml`` at ``project_dir`` (e.g. a scaffolded customer
    project — contract artifacts live in its vendored stapel-* module
    checkouts, not at the project root) → nothing to check, silently."""
    violations: list = []
    pyproject_path = project_dir / "pyproject.toml"
    if not pyproject_path.is_file():
        return violations
    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return violations
    project_version = pyproject.get("project", {}).get("version")
    if not project_version:
        return violations

    docs_dir = project_dir / "docs"
    capabilities_path = docs_dir / "capabilities.json"

    if not capabilities_path.is_file():
        makefile = project_dir / "Makefile"
        has_contract_target = False
        if makefile.is_file():
            try:
                has_contract_target = bool(
                    _CONTRACT_TARGET_RE.search(makefile.read_text(encoding="utf-8"))
                )
            except (OSError, UnicodeDecodeError):
                pass
        if has_contract_target:
            violations.append(Violation(
                "docs/capabilities.json", 1, "REL002",
                "docs/capabilities.json is missing though this repo has a "
                "'make contract' target — run make contract and commit it",
                level="warning",
            ))
        return violations

    for name in _CONTRACT_ARTIFACTS:
        path = docs_dir / name
        if not path.is_file():
            continue
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(artifact, dict):
            continue  # flows.json / errors.json — bare lists, no envelope
        version = artifact.get("version")
        if not version or version == project_version:
            continue  # no top-level version (e.g. schema.json) — not checked
        violations.append(Violation(
            f"docs/{name}", 1, "REL001",
            f"артефакт {name}: version {version} отстаёт от pyproject "
            f"{project_version} — прогони make contract и закоммить",
            level="error",
        ))
    return violations


# ---------------------------------------------------------------------------
# config_digest — fingerprint of the STAPEL_<MOD> settings blocks
# ---------------------------------------------------------------------------


def _iter_settings_files(project_dir: Path):
    for dirpath, _dirnames, filenames in _walk(project_dir):
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            if fname == "settings.py" or dirpath.name == "settings":
                yield dirpath / fname


def compute_config_digest(project_dir: Path) -> str:
    """sha256 over every top-level ``STAPEL_*`` assignment in the project's
    settings files (source text of the value expression, keyed by relative
    path + name, sorted). Opaque — only equality between releases matters.
    The config VALUES stay in settings/secret store; the manifest carries
    the fingerprint only."""
    entries = []
    for path in _iter_settings_files(project_dir):
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(project_dir).as_posix()
        for node in tree.body:
            targets = []
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            value = getattr(node, "value", None)
            if value is None:
                continue
            for name in targets:
                if _STAPEL_SETTING_RE.match(name):
                    segment = ast.get_source_segment(src, value) or ast.dump(value)
                    entries.append(f"{rel}:{name}={segment}")
    entries.sort()
    digest = hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# ---------------------------------------------------------------------------
# git sha verification
# ---------------------------------------------------------------------------


def _verify_git_sha(project_dir: Path, git_sha: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(project_dir), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return  # not a git checkout — accept the sha as given
    head = result.stdout.strip()
    resolved = subprocess.run(
        ["git", "-C", str(project_dir), "rev-parse", f"{git_sha}^{{commit}}"],
        capture_output=True, text=True,
    )
    given = resolved.stdout.strip() if resolved.returncode == 0 else git_sha
    if given != head:
        raise SystemExit(
            f"Error: --git-sha {git_sha} does not match the checked-out HEAD "
            f"{head} — the manifest must describe THIS checkout (the bake "
            f"runs on the gated sha). Pass --no-verify-sha only if you know "
            f"why this is safe."
        )


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def _resolve_created_at(created_at: Optional[str]) -> str:
    if created_at:
        return created_at
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        moment = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    else:
        moment = datetime.now(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_manifest(
    project_dir: Path,
    *,
    release: str,
    git_sha: str,
    images: dict,
    project: Optional[str] = None,
    notes: Optional[str] = None,
    gate_overrides: Optional[dict] = None,
    base_sha: Optional[str] = None,
    created_at: Optional[str] = None,
    verify_sha: bool = True,
) -> dict:
    project_dir = Path(project_dir).resolve()
    if not project_dir.is_dir():
        raise SystemExit(f"Error: project dir does not exist: {project_dir}")
    if not _RELEASE_RE.match(release):
        raise SystemExit(
            f"Error: release must be 'r<N>' (monotonic per project, §1 V2), "
            f"got {release!r}"
        )
    if not (project_dir / "manage.py").exists() and not list(
        project_dir.glob("*/manage.py")
    ):
        print(
            f"Warning: no manage.py under {project_dir} — is this a scaffolded "
            f"stapel project?", file=sys.stderr,
        )
    if verify_sha:
        _verify_git_sha(project_dir, git_sha)

    # contract artifact freshness (REL001/REL002) — fatal, unlike the gates
    # below: this must be caught BEFORE the tag, not merely recorded for a
    # pipeline to notice later.
    contract_findings = check_contract_freshness(project_dir)
    for finding in contract_findings:
        print(finding, file=sys.stderr)
    contract_errors = [f for f in contract_findings if f.level == "error"]
    if contract_errors:
        raise SystemExit(
            f"Error: {len(contract_errors)} contract artifact(s) behind "
            f"pyproject.toml (REL001 above) — run make contract and commit "
            f"before cutting this release"
        )

    # migration analysis — shared with stapel-migration-lint (same scan, same
    # floor semantics), so the gate and the manifest can never disagree
    violations, apps = lint_paths([str(project_dir)], base_sha=base_sha)
    lint_errors = [v for v in violations if v.level == "error"]
    for violation in violations:
        print(violation, file=sys.stderr)
    if lint_errors:
        print(
            f"Warning: migration-lint found {len(lint_errors)} error(s) — "
            f"recording gates.migration_lint=\"fail\" (a release pipeline "
            f"must treat this as a red gate)", file=sys.stderr,
        )

    reports = {app.label: app_report(app) for app in apps}

    gates = {
        "prodguard": None,
        "handover_scan": None,
        "migration_lint": "fail" if lint_errors else "pass",
    }
    for key, value in (gate_overrides or {}).items():
        if key not in _GATE_KEYS_OVERRIDABLE:
            raise SystemExit(
                f"Error: gate {key!r} is not overridable "
                f"(allowed: {', '.join(_GATE_KEYS_OVERRIDABLE)}; "
                f"migration_lint is computed, not declared)"
            )
        gates[key] = value

    return {
        "schema_version": SCHEMA_VERSION,
        "release": release,
        "project": project or project_dir.name,
        "git_sha": git_sha,
        "images": dict(images),
        "migrations": {label: r["watermark"] for label, r in reports.items()},
        "reversible_floor": {
            label: r["reversible_floor"] for label, r in reports.items()
        },
        "contracts": collect_contracts(project_dir),
        "config_digest": compute_config_digest(project_dir),
        "gates": gates,
        "created_at": _resolve_created_at(created_at),
        "notes": notes,
    }


def to_json(manifest: dict) -> str:
    """Deterministic encoding: sorted keys at every level, 2-space indent,
    readable unicode, single trailing newline."""
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_kv(pairs: list, what: str) -> dict:
    result = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key or not value:
            raise SystemExit(f"Error: --{what} expects name=value, got {pair!r}")
        result[key] = value
    return result


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-release-manifest",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_dir", help="Scaffolded stapel project checkout")
    parser.add_argument("--release", required=True, metavar="rN",
                        help="Release id: r1, r2, … (monotonic per project)")
    parser.add_argument("--git-sha", required=True, metavar="SHA",
                        help="The gated sha this manifest describes")
    parser.add_argument("--image", action="append", metavar="SERVICE=TAG",
                        help="Baked image tag per service (repeatable)")
    parser.add_argument("--images-json", type=Path, metavar="FILE",
                        help="JSON file {service: tag}; --image entries override")
    parser.add_argument("--project", help="Project name (default: dir name)")
    parser.add_argument("--notes", help="Free-text release notes")
    parser.add_argument("--gate", action="append", metavar="NAME=pass|fail",
                        help="Recorded result of an externally-run gate "
                             f"({'/'.join(_GATE_KEYS_OVERRIDABLE)})")
    parser.add_argument("--base-sha", metavar="SHA",
                        help="Previous release sha for the migration-lint "
                             "expand/contract reference check (MIG002)")
    parser.add_argument("--created-at", metavar="ISO8601",
                        help="Timestamp override (also honors SOURCE_DATE_EPOCH)")
    parser.add_argument("--no-verify-sha", action="store_true",
                        help="Skip the HEAD == --git-sha consistency check")
    parser.add_argument("--out", default="-", metavar="FILE",
                        help="Output path (default: stdout)")
    args = parser.parse_args(argv)

    images = {}
    if args.images_json:
        try:
            loaded = json.loads(args.images_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"Error: cannot read --images-json: {exc}") from exc
        if not isinstance(loaded, dict):
            raise SystemExit("Error: --images-json must be a {service: tag} object")
        images.update(loaded)
    images.update(_parse_kv(args.image, "image"))

    gate_overrides = _parse_kv(args.gate, "gate")
    for key, value in gate_overrides.items():
        if value not in ("pass", "fail"):
            raise SystemExit(f"Error: --gate {key} must be 'pass' or 'fail'")

    manifest = build_manifest(
        Path(args.project_dir),
        release=args.release,
        git_sha=args.git_sha,
        images=images,
        project=args.project,
        notes=args.notes,
        gate_overrides=gate_overrides,
        base_sha=args.base_sha,
        created_at=args.created_at,
        verify_sha=not args.no_verify_sha,
    )
    encoded = to_json(manifest)

    if args.out == "-":
        sys.stdout.write(encoded)
    else:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(encoded, encoding="utf-8")
        apps = len(manifest["migrations"])
        contracts = len(manifest["contracts"])
        print(
            f"{out_path}: {manifest['release']} @ {manifest['git_sha'][:12]} — "
            f"{apps} app(s), {contracts} contract pin(s), "
            f"migration_lint={manifest['gates']['migration_lint']}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
