"""
stapel-migration-lint — expand/contract gate for Django migrations
(docs/release-management.md §3.2, R-1).

Analyzes Django migration FILES statically (AST) across a project tree, a
single app, or a stapel-* module repo. Two consumers, one mechanism:

* **customer-project gate** — runs at cut-release on the project checkout
  (and feeds the `migration_lint` gate of ``release.json``, see
  ``stapel_tools.release``);
* **stapel-* module CI** — the same discipline on our own migrations, which
  reach customers through module bumps.

Why AST and not import-and-inspect
----------------------------------
Importing migrations sees the *real* operations list, but requires configured
Django settings, every INSTALLED_APP importable, and — fatally for the
``--base-sha`` check and for module CI — a runnable project at an arbitrary
git sha. A stapel-* module repo is not a runnable project, and a customer
checkout at cut time must be lintable without booting Django. Migration files
are declarative by convention (a static ``operations = [...]`` list written
by ``makemigrations``), so AST sees everything that matters; the rare
dynamically-built operations list is reported as a WARNING (MIG101) instead
of being silently half-analyzed.

Rules
-----
MIG001  destructive operation (RemoveField, DeleteModel, RenameField,
        RenameModel, AlterField that narrows: null→NOT NULL or max_length
        shrink) in a migration without the ``# stapel: contract-phase``
        marker → ERROR. Destructive schema changes ship one release AFTER
        the code stopped using the target (expand rN → contract rN+1).
MIG002  with ``--base-sha <sha of the previous release>``: a destructive
        operation in a migration file that did NOT exist at the base sha,
        whose target (removed field / deleted or renamed model / renamed
        field's old name) is still referenced by the app's non-migration
        sources at that sha → ERROR, marker or not. Grep-level word-boundary
        check via ``git show`` — a common-word field name ("name") can
        false-positive against sibling models in the same app; that is the
        deliberate price of a dependency-free check. Narrowing AlterField
        is exempt (the field legitimately still exists at N-1).
MIG003  RunPython/RunSQL without a reverse (``reverse_code``/``reverse_sql``)
        in a migration without the ``# stapel: irreversible`` marker → ERROR.
        The marker lowers the app's ``reversible_floor`` to that migration
        (recorded in release.json; rollback tooling refuses to cross it).
MIG004  AddField that is NOT NULL (no ``null=True``) without ``default`` /
        ``db_default`` on a model that already exists (not created in the
        same migration file) → ERROR. Breaks N-1 compatibility: during the
        migrate→swap window the old code INSERTs rows without the column.
        ManyToManyField is exempt (no column on the model's table).
        Suppress with ``# noqa: MIG004`` on the AddField line (e.g. a table
        provably empty in every deployment).
MIG101  ``operations`` is not a static list — cannot analyze → WARNING.
MIG102  ``# stapel: irreversible`` marker on a migration whose operations
        are all reversible — stale marker needlessly lowers the app's
        reversible_floor → WARNING.

Markers are file-level comment lines: ``# stapel: contract-phase`` and
``# stapel: irreversible`` anywhere in the migration file.

reversible_floor semantics (shared with the manifest builder)
-------------------------------------------------------------
Per app, migrations are ordered by filename (Django's zero-padded numeric
prefix). A migration is irreversible when it factually contains a
RunPython/RunSQL without reverse OR carries the ``# stapel: irreversible``
marker (the declaration is trusted — conservative for rollback). The floor is
the NAME OF THE LATEST irreversible migration: ``manage.py migrate <app>
<floor>`` is the earliest safe rollback target, because any earlier target
would have to reverse the irreversible migration. No irreversible migrations
→ floor is ``"zero"`` (the whole app history rolls back).

Exit codes: 0 clean (warnings allowed), 1 errors (``--strict`` promotes
warnings to errors), 2 usage/environment errors.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from dataclasses import field as _dc_field
from pathlib import Path
from typing import Iterable, Optional

MARKER_CONTRACT_PHASE = "contract-phase"
MARKER_IRREVERSIBLE = "irreversible"
_MARKER_RE = re.compile(r"#\s*stapel:\s*([a-z][a-z\-]*)")

FLOOR_ZERO = "zero"

SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".hg",
    ".tox",
    ".venv",
    "venv",
    "node_modules",
    "htmlcov",
    "build",
    "dist",
    ".claude",
    "worktrees",
    "site-packages",
}

#: sentinel — a value the AST could not resolve to a constant
UNKNOWN = object()


@dataclass
class Violation:
    path: str
    line: int
    rule: str
    message: str
    level: str = "error"  # "error" | "warning"

    def __str__(self) -> str:
        tag = self.rule if self.level == "error" else f"{self.rule} warning"
        return f"{self.path}:{self.line}: [{tag}] {self.message}"

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "line": self.line,
            "rule": self.rule,
            "message": self.message,
            "level": self.level,
        }


@dataclass
class FieldInfo:
    """What the linter can see of a field definition from the AST."""

    type: str = ""
    null: object = False        # True | False | UNKNOWN
    max_length: object = None   # int | None | UNKNOWN
    has_default: bool = False
    has_db_default: bool = False


@dataclass
class Op:
    """One parsed entry of a migration's ``operations`` list."""

    kind: str
    line: int
    model: Optional[str] = None      # lowercase model key (state tracking)
    name: Optional[str] = None       # field name / model name / old_name
    new_name: Optional[str] = None
    field: Optional[FieldInfo] = None
    fields: list = _dc_field(default_factory=list)  # CreateModel: [(name, FieldInfo)]
    has_reverse: Optional[bool] = None           # RunPython / RunSQL


@dataclass
class MigrationScan:
    name: str                 # filename without .py
    path: Path
    markers: set
    ops: list                 # list[Op]
    analyzable: bool
    lines: list               # source lines (noqa lookup)

    @property
    def irreversible_ops(self) -> list:
        return [
            op for op in self.ops
            if op.kind in ("RunPython", "RunSQL") and op.has_reverse is False
        ]

    @property
    def is_irreversible(self) -> bool:
        return bool(self.irreversible_ops) or MARKER_IRREVERSIBLE in self.markers


@dataclass
class AppScan:
    label: str
    app_dir: Path
    migrations: list  # list[MigrationScan], ordered by name

    @property
    def watermark(self) -> str:
        """Max migration FILE present in the codebase — the artifact's
        watermark, not any database's applied state."""
        return self.migrations[-1].name


# ---------------------------------------------------------------------------
# AST extraction
# ---------------------------------------------------------------------------


def _call_name(func) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _kwargs(call: ast.Call) -> dict:
    return {kw.arg: kw.value for kw in call.keywords if kw.arg}


def _const(node):
    if isinstance(node, ast.Constant):
        return node.value
    return UNKNOWN


def _arg(call: ast.Call, index: int, kwname: str):
    """Positional-or-keyword argument node, or None."""
    if len(call.args) > index:
        return call.args[index]
    return _kwargs(call).get(kwname)


def _str_arg(call: ast.Call, index: int, kwname: str) -> Optional[str]:
    value = _const(_arg(call, index, kwname) or ast.Constant(value=None))
    return value if isinstance(value, str) else None


def _field_info(node) -> FieldInfo:
    if not isinstance(node, ast.Call):
        return FieldInfo(type="", null=UNKNOWN, max_length=UNKNOWN)
    kw = _kwargs(node)
    info = FieldInfo(type=_call_name(node.func))
    if "null" in kw:
        value = _const(kw["null"])
        info.null = value if isinstance(value, bool) else UNKNOWN
    if "max_length" in kw:
        value = _const(kw["max_length"])
        info.max_length = value if isinstance(value, int) else UNKNOWN
    if "default" in kw:
        # default=None is not a usable backfill value for a NOT NULL column
        info.has_default = _const(kw["default"]) is not None
    info.has_db_default = "db_default" in kw
    return info


def _has_reverse(call: ast.Call, kwname: str) -> bool:
    """RunPython(code, reverse_code) / RunSQL(sql, reverse_sql)."""
    reverse = call.args[1] if len(call.args) > 1 else _kwargs(call).get(kwname)
    if reverse is None:
        return False
    return not (isinstance(reverse, ast.Constant) and reverse.value is None)


def _parse_operation(node) -> tuple[list, bool]:
    """Parse one element of the operations list → (ops, analyzable)."""
    if not isinstance(node, ast.Call):
        return [], False
    kind = _call_name(node.func)
    line = node.lineno
    kw = _kwargs(node)

    if kind == "CreateModel":
        name = _str_arg(node, 0, "name")
        fields_node = _arg(node, 1, "fields")
        fields = []
        if isinstance(fields_node, (ast.List, ast.Tuple)):
            for element in fields_node.elts:
                if isinstance(element, (ast.Tuple, ast.List)) and len(element.elts) >= 2:
                    fname = _const(element.elts[0])
                    if isinstance(fname, str):
                        fields.append((fname, _field_info(element.elts[1])))
        return [Op(kind, line, model=(name or "").lower(), name=name, fields=fields)], True

    if kind == "DeleteModel":
        name = _str_arg(node, 0, "name")
        return [Op(kind, line, model=(name or "").lower(), name=name)], True

    if kind in ("AddField", "RemoveField", "AlterField"):
        model = (_str_arg(node, 0, "model_name") or "").lower()
        fname = _str_arg(node, 1, "name")
        info = _field_info(kw.get("field") or (node.args[2] if len(node.args) > 2 else None))
        return [Op(kind, line, model=model, name=fname, field=info)], True

    if kind == "RenameField":
        model = (_str_arg(node, 0, "model_name") or "").lower()
        return [Op(kind, line, model=model,
                   name=_str_arg(node, 1, "old_name"),
                   new_name=_str_arg(node, 2, "new_name"))], True

    if kind == "RenameModel":
        return [Op(kind, line,
                   name=_str_arg(node, 0, "old_name"),
                   new_name=_str_arg(node, 1, "new_name"))], True

    if kind == "RunPython":
        return [Op(kind, line, has_reverse=_has_reverse(node, "reverse_code"))], True

    if kind == "RunSQL":
        return [Op(kind, line, has_reverse=_has_reverse(node, "reverse_sql"))], True

    if kind == "SeparateDatabaseAndState":
        # Only database_operations touch the DB; state_operations are no-ops
        # for schema/rollback purposes.
        db_ops = kw.get("database_operations")
        ops: list = []
        analyzable = True
        if isinstance(db_ops, (ast.List, ast.Tuple)):
            for element in db_ops.elts:
                sub_ops, sub_ok = _parse_operation(element)
                ops.extend(sub_ops)
                analyzable = analyzable and sub_ok
        elif db_ops is not None:
            analyzable = False
        return ops, analyzable

    # AddIndex, AddConstraint, AlterModelOptions, … — not in the destructive
    # set (§3.2) and reversible by construction.
    return [Op(kind, line)], True


def scan_migration_file(path: Path) -> MigrationScan:
    name = path.stem
    try:
        src = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return MigrationScan(name, path, set(), [], analyzable=False, lines=[])

    lines = src.splitlines()
    markers = set()
    for line in lines:
        match = _MARKER_RE.search(line)
        if match:
            markers.add(match.group(1))

    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return MigrationScan(name, path, markers, [], analyzable=False, lines=lines)

    ops: list = []
    analyzable = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            targets = [t.id for t in item.targets if isinstance(t, ast.Name)]
            if "operations" not in targets:
                continue
            if isinstance(item.value, (ast.List, ast.Tuple)):
                analyzable = True
                for element in item.value.elts:
                    parsed, ok = _parse_operation(element)
                    ops.extend(parsed)
                    analyzable = analyzable and ok
            else:
                analyzable = False
    return MigrationScan(name, path, markers, ops, analyzable=analyzable, lines=lines)


# ---------------------------------------------------------------------------
# App discovery
# ---------------------------------------------------------------------------


def resolve_app_label(app_dir: Path) -> str:
    """Django app label: explicit ``label = "…"`` in apps.py wins, then the
    last component of the AppConfig ``name``, then the directory name."""
    apps_py = app_dir / "apps.py"
    label = name = None
    if apps_py.is_file():
        try:
            tree = ast.parse(apps_py.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for item in node.body:
                    if not isinstance(item, ast.Assign):
                        continue
                    targets = [t.id for t in item.targets if isinstance(t, ast.Name)]
                    value = _const(item.value)
                    if not isinstance(value, str):
                        continue
                    if "label" in targets and label is None:
                        label = value
                    if "name" in targets and name is None:
                        name = value
    if label:
        return label
    if name:
        return name.rsplit(".", 1)[-1]
    return app_dir.name.replace("-", "_")


def discover_apps(root: Path) -> list:
    """Every app in the tree owning a ``migrations/`` dir with at least one
    migration file. Returns AppScans sorted by (label, path)."""
    root = Path(root).resolve()
    apps = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and not d.endswith(".egg-info")
        )
        if Path(dirpath).name != "migrations":
            continue
        migration_files = sorted(
            f for f in filenames if f.endswith(".py") and not f.startswith("__")
        )
        if not migration_files:
            continue
        app_dir = Path(dirpath).parent
        migrations = [scan_migration_file(Path(dirpath) / f) for f in migration_files]
        apps.append(AppScan(resolve_app_label(app_dir), app_dir, migrations))
    apps.sort(key=lambda a: (a.label, str(a.app_dir)))
    return apps


def compute_reversible_floor(app: AppScan) -> str:
    """Earliest safe rollback target (see module docstring): the latest
    irreversible migration's name, or ``"zero"`` when fully reversible."""
    floor = FLOOR_ZERO
    for migration in app.migrations:
        if migration.is_irreversible:
            floor = migration.name
    return floor


def app_report(app: AppScan) -> dict:
    """Machine summary consumed by --json and the release manifest builder."""
    return {
        "watermark": app.watermark,
        "reversible_floor": compute_reversible_floor(app),
        "migrations": [m.name for m in app.migrations],
        "irreversible": [m.name for m in app.migrations if m.is_irreversible],
    }


# ---------------------------------------------------------------------------
# git plumbing for --base-sha
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )


def _git_root(path: Path) -> Optional[Path]:
    result = _git(path, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def _base_tree(git_root: Path, sha: str) -> set:
    result = _git(git_root, "ls-tree", "-r", "--name-only", sha)
    if result.returncode != 0:
        raise SystemExit(
            f"Error: cannot read tree at base sha {sha!r} in {git_root}: "
            f"{result.stderr.strip()}"
        )
    return set(result.stdout.splitlines())


def _blob_at(git_root: Path, sha: str, relpath: str) -> str:
    result = _git(git_root, "show", f"{sha}:{relpath}")
    return result.stdout if result.returncode == 0 else ""


def _search_base_references(
    git_root: Path, sha: str, tree: set, app_rel: str, target: str
) -> list:
    """``path:line`` hits of ``target`` (word-boundary) in the app's
    non-migration .py sources at the base sha."""
    if app_rel in (".", ""):
        candidates = tree
    else:
        prefix = app_rel.rstrip("/") + "/"
        candidates = {p for p in tree if p.startswith(prefix)}
    pattern = re.compile(rf"(?<![\w]){re.escape(target)}(?![\w])")
    hits = []
    for relpath in sorted(candidates):
        if not relpath.endswith(".py"):
            continue
        parts = relpath.split("/")
        if "migrations" in parts:
            continue
        for lineno, line in enumerate(_blob_at(git_root, sha, relpath).splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{relpath}:{lineno}")
                break  # first hit per file is enough evidence
    return hits


# ---------------------------------------------------------------------------
# Lint pass
# ---------------------------------------------------------------------------


def _noqa(lines: list, lineno: int, rule: str) -> bool:
    if lineno < 1 or lineno > len(lines):
        return False
    comment = lines[lineno - 1]
    if "# noqa" not in comment:
        return False
    if "# noqa:" not in comment:
        return True
    listed = [r.strip() for r in comment.split("# noqa:")[1].split(",")]
    return rule in listed


def _destructive_ops(migration: MigrationScan, state: dict) -> list:
    """(op, base_ref_target, description) triples; mutates ``state`` — the
    per-app {(model, field): FieldInfo} map built across migrations in order."""
    found = []
    created_here = set()
    for op in migration.ops:
        kind = op.kind
        if kind == "CreateModel":
            created_here.add(op.model)
            for fname, info in op.fields:
                state[(op.model, fname)] = info
        elif kind == "AddField":
            state[(op.model, op.name)] = op.field or FieldInfo()
        elif kind == "AlterField":
            previous = state.get((op.model, op.name))
            info = op.field or FieldInfo()
            if previous is not None:
                narrows = []
                if previous.null is True and info.null is False:
                    narrows.append("null → NOT NULL")
                if (
                    isinstance(previous.max_length, int)
                    and isinstance(info.max_length, int)
                    and info.max_length < previous.max_length
                ):
                    narrows.append(
                        f"max_length {previous.max_length} → {info.max_length}"
                    )
                if narrows:
                    found.append((
                        op,
                        None,  # field still exists at N-1 — no base-sha check
                        f"AlterField '{op.model}.{op.name}' narrows "
                        f"({', '.join(narrows)})",
                    ))
            state[(op.model, op.name)] = info
        elif kind == "RemoveField":
            found.append((op, op.name, f"RemoveField '{op.model}.{op.name}'"))
            state.pop((op.model, op.name), None)
        elif kind == "DeleteModel":
            found.append((op, op.name, f"DeleteModel '{op.name}'"))
            for key in [k for k in state if k[0] == op.model]:
                del state[key]
        elif kind == "RenameField":
            found.append((
                op, op.name,
                f"RenameField '{op.model}.{op.name}' → '{op.new_name}'",
            ))
            if (op.model, op.name) in state:
                state[(op.model, op.new_name)] = state.pop((op.model, op.name))
        elif kind == "RenameModel":
            found.append((op, op.name, f"RenameModel '{op.name}' → '{op.new_name}'"))
            old = (op.name or "").lower()
            new = (op.new_name or "").lower()
            for model, fname in [k for k in state if k[0] == old]:
                state[(new, fname)] = state.pop((model, fname))
    return found, created_here


def lint_app(
    app: AppScan,
    *,
    base_sha: Optional[str] = None,
    git_root: Optional[Path] = None,
    base_tree: Optional[set] = None,
) -> list:
    violations = []
    state: dict = {}

    app_rel = ""
    if git_root is not None:
        try:
            app_rel = app.app_dir.resolve().relative_to(git_root.resolve()).as_posix()
        except ValueError:
            git_root = None  # app outside the repo — base check not possible

    for migration in app.migrations:
        path = str(migration.path)

        if not migration.analyzable:
            violations.append(Violation(
                path, 1, "MIG101",
                "operations is not a static list — cannot analyze; "
                "MIG001-MIG004 are skipped for this migration",
                level="warning",
            ))

        destructive, created_here = _destructive_ops(migration, state)

        # MIG004 — NOT NULL AddField on an existing model
        for op in migration.ops:
            if op.kind != "AddField":
                continue
            info = op.field or FieldInfo()
            if op.model in created_here or info.type == "ManyToManyField":
                continue
            if info.null is True or info.null is UNKNOWN:
                continue
            if info.has_default or info.has_db_default:
                continue
            if _noqa(migration.lines, op.line, "MIG004"):
                continue
            violations.append(Violation(
                path, op.line, "MIG004",
                f"AddField '{op.model}.{op.name}' is NOT NULL without "
                f"default/db_default on an existing model — old (N-1) code "
                f"INSERTs rows without this column during the migrate→swap "
                f"window; add null=True now (expand) and tighten in a later "
                f"release, or provide default/db_default",
            ))

        # MIG001 — destructive without contract-phase marker
        if destructive and MARKER_CONTRACT_PHASE not in migration.markers:
            for op, _target, description in destructive:
                violations.append(Violation(
                    path, op.line, "MIG001",
                    f"{description} is destructive — expand/contract: it may "
                    f"only ship one release AFTER the code stopped using the "
                    f"target; when that holds, mark the file "
                    f"'# stapel: {MARKER_CONTRACT_PHASE}'",
                ))

        # MIG003 — irreversible data op without marker
        if migration.irreversible_ops and MARKER_IRREVERSIBLE not in migration.markers:
            for op in migration.irreversible_ops:
                reverse_kw = "reverse_code" if op.kind == "RunPython" else "reverse_sql"
                violations.append(Violation(
                    path, op.line, "MIG003",
                    f"{op.kind} without {reverse_kw} makes this migration "
                    f"irreversible — add a reverse (noop is fine when the "
                    f"forward op is additive) or mark the file "
                    f"'# stapel: {MARKER_IRREVERSIBLE}' to accept lowering "
                    f"this app's reversible_floor to {migration.name}",
                ))

        # MIG102 — stale irreversible marker
        if MARKER_IRREVERSIBLE in migration.markers and not migration.irreversible_ops:
            violations.append(Violation(
                path, 1, "MIG102",
                "marked '# stapel: irreversible' but every operation is "
                "reversible — the stale marker needlessly lowers the app's "
                "reversible_floor",
                level="warning",
            ))

        # MIG002 — base-sha reference check (new migrations only)
        if base_sha and destructive and git_root is not None and base_tree is not None:
            try:
                mig_rel = (
                    migration.path.resolve().relative_to(git_root.resolve()).as_posix()
                )
            except ValueError:
                mig_rel = None
            if mig_rel is not None and mig_rel not in base_tree:
                for op, target, description in destructive:
                    if not target:
                        continue
                    hits = _search_base_references(
                        git_root, base_sha, base_tree, app_rel, target
                    )
                    if hits:
                        violations.append(Violation(
                            path, op.line, "MIG002",
                            f"{description}: '{target}' is still referenced at "
                            f"base {base_sha[:12]} ({hits[0]}) — expand/contract "
                            f"violated: this release both stops using and "
                            f"destroys it; ship the destructive migration in "
                            f"the NEXT release",
                        ))
    return violations


def lint_paths(paths: Iterable, base_sha: Optional[str] = None) -> tuple[list, list]:
    """Lint every app under each path. Returns (violations, apps)."""
    violations: list = []
    apps: list = []
    tree_cache: dict = {}
    for raw in paths:
        root = Path(raw)
        if not root.exists():
            raise SystemExit(f"Error: path does not exist: {root}")
        for app in discover_apps(root):
            git_root = base_tree = None
            if base_sha:
                git_root = _git_root(app.app_dir)
                if git_root is None:
                    print(
                        f"Warning: {app.app_dir} is not inside a git repo — "
                        f"--base-sha check skipped for app '{app.label}'",
                        file=sys.stderr,
                    )
                else:
                    key = str(git_root)
                    if key not in tree_cache:
                        tree_cache[key] = _base_tree(git_root, base_sha)
                    base_tree = tree_cache[key]
            violations.extend(
                lint_app(app, base_sha=base_sha, git_root=git_root, base_tree=base_tree)
            )
            apps.append(app)
    violations.sort(key=lambda v: (v.path, v.line, v.rule))
    return violations, apps


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-migration-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths", nargs="*", default=["."],
        help="Project roots, app dirs or module repos to lint (default: .)",
    )
    parser.add_argument(
        "--base-sha", metavar="SHA",
        help="Git sha of the previous release: destructive ops in migrations "
             "added since then must not target anything the code at that sha "
             "still references (MIG002)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Machine output: violations + per-app watermark/reversible_floor",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Promote warnings to errors (exit 1 on any finding)",
    )
    args = parser.parse_args(argv)

    violations, apps = lint_paths(args.paths, base_sha=args.base_sha)

    errors = [v for v in violations if v.level == "error"]
    warnings = [v for v in violations if v.level != "error"]
    failing = violations if args.strict else errors

    if args.json:
        print(json.dumps(
            {
                "ok": not failing,
                "errors": len(errors),
                "warnings": len(warnings),
                "violations": [v.to_dict() for v in violations],
                "apps": {app.label: app_report(app) for app in apps},
            },
            indent=2, sort_keys=True, ensure_ascii=False,
        ))
    else:
        for violation in violations:
            print(violation)
        if violations:
            parts = []
            if errors:
                parts.append(f"{len(errors)} error{'s' if len(errors) > 1 else ''}")
            if warnings:
                parts.append(
                    f"{len(warnings)} warning{'s' if len(warnings) > 1 else ''}"
                )
            print(f"\n{', '.join(parts)} found across {len(apps)} app(s).")
        else:
            print(f"No violations found across {len(apps)} app(s).")

    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
