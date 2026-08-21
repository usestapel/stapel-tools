"""
stapel-index-lint — the gate against "indexed silently, read by nothing".

Why this exists
---------------
The legacy marketplace's search died in one specific, boring way: fields
were written into the index and read by no query. ``features_search`` was
built on every publish and never queried. ``description_en`` was populated
and never searched. ``geohash`` was stored and never used for proximity.
None of it failed. None of it was noticed for years, because writing a
field and reading a field are different files, and nothing connected them.

``stapel-search`` answers that with a contract as DATA — ``docs/index.json``,
one row per indexed field carrying its source, the named query capabilities
that READ it, and the pytest node id that proves the round trip. These
rules are the static half of enforcing that contract, and they run wherever
an ``docs/index.json`` exists: in the library that owns the index, and in a
product that ships its own backend.

The rules
---------
IDX001 (error) **indexed-but-undeclared.** A concrete field on an index model,
    or a field of the document dataclass a backend's ``upsert`` receives, that
    ``docs/index.json`` does not account for. The map is explicit
    (``model_columns``), so adding a column forces a decision: it is an
    indexed value with a read path and a test, or it is bookkeeping and says
    so. "I'll wire the query later" stops being expressible.

IDX002 (error) **declared-but-unreachable.** A query read path that some
    shipped backend does not answer. Each backend module declares
    ``READ_PATH_IMPL = {read_path: symbol}``; the rule checks the promise is
    registered AND that the named symbol exists in that module's AST. A
    backend may answer with ``capability:<name>`` instead — Meilisearch has
    no geohash column and resolves proximity natively — which is a declared,
    reviewable difference rather than a silent one. Modules setting
    ``IS_STUB = True`` are skipped: a rule that forces a stub to grow a fake
    implementation is a rule that manufactures the defect it audits.

IDX003 (error) **test-does-not-resolve.** A declared ``test`` node id whose
    file does not exist under ``tests/``, or whose function is not defined in
    it. A field whose proof went missing is a field back on the honour system.

IDX004 (warning) **dead pull.** A field the source mapper extracts that lands
    in no index field — the mirror image of IDX001, and the reason it is a
    warning: a mapper legitimately reads more than it indexes (a status it
    only tests, a key it only echoes), so this is a prompt to look, not a
    verdict.

IDX005 (warning) **kind outside the closed vocabulary.** Widening the field
    taxonomy should be a deliberate act, visible in review.

What these rules deliberately do NOT catch
------------------------------------------
The boundary is SUR004's, restated because it is exactly the same boundary
(``surface_lint.py:64-75``): these rules prove **the promise was not dropped
on the floor** — that a declared field is reachable by a named query
capability and that a test with that name exists. They cannot prove the
query branch is correct, and they cannot prove the test asserts anything
useful. Only the round-trip suite does that, which is why
``stapel_search.testing.backend_conformance`` exists and why every field's
test carries a mandatory negative half. A static gate that claimed more
would be the third kind of false confidence in a story that already has two.

Waivers are explicit and named, never silence (the
``stapel-core/django/adoption_checks.py:26-45`` canon)::

    # stapel: index-waived <field> — <reason>

Level follows the reader's power to act (``adoption_checks.py:53-66``): in a
library that owns its index, IDX findings are errors. In a consuming project
that merely installed a third-party backend, they are warnings — the reader
there cannot fix somebody else's contract, and an error they cannot clear
teaches them to ignore the tool.

Exit codes: 0 clean (warnings allowed), 1 errors present, 2 usage errors.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

#: ``# stapel: index-waived <field> — <reason>``
WAIVER_RE = re.compile(
    r"#\s*stapel:\s*index-waived\s+(?P<field>[\w.]+)\s*[—\-:]\s*(?P<reason>.+)$"
)

#: Value prefix meaning "this engine answers the read path natively".
NATIVE_IMPL_PREFIX = "capability:"

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "migrations",
}


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    message: str
    level: str = "error"

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


# ---------------------------------------------------------------------------
# reading the contract
# ---------------------------------------------------------------------------


def load_index_schema(project: Path) -> Optional[dict]:
    """``docs/index.json``, or ``None`` when the project declares no index.

    Silence is the correct behaviour for a project that has no search index:
    a linter that complains about a missing artifact nobody promised is a
    linter people disable.
    """
    path = project / "docs" / "index.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"index-lint: {path} is not valid JSON: {exc}")


def waived_fields(project: Path) -> dict[str, str]:
    """``{field: reason}`` from explicit waiver comments anywhere in the tree."""
    waivers: dict[str, str] = {}
    for path in _python_files(project):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "index-waived" not in text:
            continue
        for line in text.splitlines():
            match = WAIVER_RE.search(line)
            if match:
                waivers[match.group("field")] = match.group("reason").strip()
    return waivers


def _python_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def _rel(project: Path, path: Path) -> str:
    try:
        return str(path.relative_to(project))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# IDX001 — indexed but undeclared
# ---------------------------------------------------------------------------

_FIELD_CALL_RE = re.compile(r"^(?:models\.)?\w*Field$")


def _model_fields(tree: ast.AST) -> dict[str, list[tuple[str, int]]]:
    """``{ModelName: [(field_name, lineno), ...]}`` for Django model classes."""
    found: dict[str, list[tuple[str, int]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        fields: list[tuple[str, int]] = []
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
                continue
            target = stmt.targets[0]
            if not isinstance(target, ast.Name):
                continue
            call = stmt.value
            if not isinstance(call, ast.Call):
                continue
            name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(
                call.func, "id", ""
            )
            if _FIELD_CALL_RE.match(name or ""):
                fields.append((target.id, stmt.lineno))
        if fields:
            found[node.name] = fields
    return found


def check_indexed_but_undeclared(project: Path, schema: dict) -> list[Finding]:
    """IDX001: a model column the contract does not account for."""
    declared = {f["field"] for f in schema.get("fields", [])}
    columns = schema.get("model_columns") or {}
    waivers = waived_fields(project)
    findings: list[Finding] = []

    models_py = project / "models.py"
    if not models_py.is_file():
        return findings
    try:
        tree = ast.parse(models_py.read_text(encoding="utf-8"))
    except SyntaxError:
        return findings

    for model_name, fields in _model_fields(tree).items():
        mapping = columns.get(model_name)
        if mapping is None:
            continue  # not an index model; the contract says which are
        for field_name, lineno in fields:
            if field_name in waivers:
                continue
            if field_name not in mapping:
                findings.append(
                    Finding(
                        _rel(project, models_py),
                        lineno,
                        "IDX001",
                        f"{model_name}.{field_name} is stored on an index model but "
                        f"docs/index.json does not account for it. Add it to "
                        f"model_columns — mapped to the index field it realizes, or "
                        f"to null if it is table bookkeeping. Indexing a value "
                        f"nothing reads is the defect this contract exists to stop.",
                    )
                )
                continue
            target = mapping[field_name]
            if target is not None and target not in declared:
                findings.append(
                    Finding(
                        _rel(project, models_py),
                        lineno,
                        "IDX001",
                        f"{model_name}.{field_name} maps to index field {target!r}, "
                        f"which is not declared in docs/index.json.",
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# IDX002 — declared but unreachable
# ---------------------------------------------------------------------------


def _module_constant_dict(tree: ast.AST, name: str) -> Optional[dict[str, str]]:
    for node in tree.body if isinstance(tree, ast.Module) else []:
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                value = node.value
                if isinstance(value, ast.Dict):
                    out: dict[str, str] = {}
                    for key, val in zip(value.keys, value.values):
                        if isinstance(key, ast.Constant) and isinstance(val, ast.Constant):
                            out[str(key.value)] = str(val.value)
                    return out
    return None


def _module_flag(tree: ast.AST, name: str) -> bool:
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == name
                    and isinstance(node.value, ast.Constant)
                ):
                    return bool(node.value.value)
    return False


def _defined_symbols(tree: ast.AST) -> set[str]:
    """Every function, method and class name defined anywhere in the module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def backend_modules(project: Path) -> list[Path]:
    """Shipped backend modules: ``backends/*.py``, excluding privates."""
    directory = project / "backends"
    if not directory.is_dir():
        return []
    return sorted(
        path for path in directory.glob("*.py")
        if not path.name.startswith("_") and path.name != "base.py"
    )


def check_declared_but_unreachable(
    project: Path, schema: dict, *, level: str = "error"
) -> list[Finding]:
    """IDX002: a query read path some shipped backend does not answer."""
    prefixes = tuple(schema.get("query_read_path_prefixes") or ())
    if not prefixes:
        return []
    wanted: list[str] = []
    for field in schema.get("fields", []):
        for path in field.get("read_paths", []):
            if path.startswith(prefixes) and path not in wanted:
                wanted.append(path)
    if not wanted:
        return []

    findings: list[Finding] = []
    for module_path in backend_modules(project):
        try:
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        if _module_flag(tree, "IS_STUB"):
            continue
        declared = _module_constant_dict(tree, "READ_PATH_IMPL")
        if declared is None:
            findings.append(
                Finding(
                    _rel(project, module_path),
                    1,
                    "IDX002",
                    "a shipped backend declares no READ_PATH_IMPL, so there is no "
                    "way to tell which of the index contract's read paths it "
                    "answers. Declare the map (or set IS_STUB = True if it is a "
                    "pointer, not a promise).",
                    level,
                )
            )
            continue
        symbols = _defined_symbols(tree)
        missing = [path for path in wanted if path not in declared]
        if missing:
            findings.append(
                Finding(
                    _rel(project, module_path),
                    1,
                    "IDX002",
                    "backend does not answer declared read path(s): "
                    + ", ".join(sorted(missing))
                    + ". Implement them, or declare 'capability:<name>' when the "
                    "engine answers the same question natively.",
                    level,
                )
            )
        for path, impl in sorted(declared.items()):
            if impl.startswith(NATIVE_IMPL_PREFIX):
                continue
            if impl not in symbols:
                findings.append(
                    Finding(
                        _rel(project, module_path),
                        1,
                        "IDX002",
                        f"read path {path!r} names implementation {impl!r}, which is "
                        f"not defined in this module.",
                        level,
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# IDX003 — the declared test does not resolve
# ---------------------------------------------------------------------------


def check_test_resolves(project: Path, schema: dict, *, level: str = "error") -> list[Finding]:
    """IDX003: a declared pytest node id that is not there."""
    findings: list[Finding] = []
    cache: dict[Path, set[str]] = {}
    for field in schema.get("fields", []):
        node_id = field.get("test") or ""
        rel, _, function = node_id.partition("::")
        target = project / rel
        if not rel or not target.is_file():
            findings.append(
                Finding(
                    "docs/index.json", 1, "IDX003",
                    f"index field {field.get('field')!r} declares test {node_id!r}, "
                    f"but {rel or '(nothing)'} does not exist.",
                    level,
                )
            )
            continue
        if target not in cache:
            try:
                cache[target] = _defined_symbols(ast.parse(target.read_text(encoding="utf-8")))
            except (OSError, SyntaxError):
                cache[target] = set()
        base = function.split("[", 1)[0]
        if base and base not in cache[target]:
            findings.append(
                Finding(
                    rel, 1, "IDX003",
                    f"index field {field.get('field')!r} declares test {node_id!r}, "
                    f"but {base!r} is not defined there. A field whose proof went "
                    f"missing is a field back on the honour system.",
                    level,
                )
            )
    return findings


# ---------------------------------------------------------------------------
# IDX004 — dead pull
# ---------------------------------------------------------------------------


def check_dead_pull(project: Path, schema: dict) -> list[Finding]:
    """IDX004 (warning): a document-input field that reaches no index field.

    Read off the source-document dataclass rather than off a host's mapper:
    the mapper lives in a composite this linter may never see, but the shape
    it fills is right here.
    """
    declared = {f["field"] for f in schema.get("fields", [])}
    columns = schema.get("model_columns") or {}
    for mapping in columns.values():
        declared.update(name for name in mapping if name)
    waivers = waived_fields(project)

    dto = project / "dto.py"
    if not dto.is_file():
        return []
    try:
        tree = ast.parse(dto.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []

    # Fields that exist to carry the document, not to be indexed themselves.
    plumbing = {"doc_type", "doc_key", "status", "seq", "source_event_id",
                "category_id", "features", "features_search", "source_updated_at"}
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "SearchDocumentInput":
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                continue
            name = stmt.target.id
            if name in declared or name in plumbing or name in waivers:
                continue
            findings.append(
                Finding(
                    _rel(project, dto), stmt.lineno, "IDX004",
                    f"SearchDocumentInput.{name} is pulled from the source but lands "
                    f"in no index field — dead haulage. Index it, drop it, or waive "
                    f"it: '# stapel: index-waived {name} — <reason>'.",
                    "warning",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# IDX005 — kind outside the closed vocabulary
# ---------------------------------------------------------------------------


def check_kind_vocabulary(schema: dict) -> list[Finding]:
    """IDX005 (warning): widening the taxonomy should be deliberate."""
    allowed = set(schema.get("kinds") or ())
    if not allowed:
        return []
    findings = []
    for field in schema.get("fields", []):
        kind = field.get("kind")
        if kind not in allowed:
            findings.append(
                Finding(
                    "docs/index.json", 1, "IDX005",
                    f"index field {field.get('field')!r} has kind {kind!r}, outside "
                    f"the closed vocabulary ({', '.join(sorted(allowed))}).",
                    "warning",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def owns_the_index(project: Path) -> bool:
    """Whether this tree OWNS the index (library) or merely consumes it.

    Level follows the reader's power to act: the owner can fix a contract
    violation, a consumer that installed somebody else's backend cannot, and
    an error a reader cannot clear teaches them to ignore the tool.
    """
    return (project / "index_schema.py").is_file()


def lint_project(project: Path, *, notes: Optional[list] = None) -> list[Finding]:
    """Every IDX rule against *project*. Silent when it declares no index."""
    schema = load_index_schema(project)
    if schema is None:
        if notes is not None:
            notes.append(
                f"index-lint: no docs/index.json in {project} — nothing to check."
            )
        return []
    level = "error" if owns_the_index(project) else "warning"
    findings = check_indexed_but_undeclared(project, schema)
    if level == "warning":
        for finding in findings:
            finding.level = "warning"
    findings += check_declared_but_unreachable(project, schema, level=level)
    findings += check_test_resolves(project, schema, level=level)
    findings += check_dead_pull(project, schema)
    findings += check_kind_vocabulary(schema)
    return findings


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-index-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_dir", nargs="?", default=".", help="Project root.")
    parser.add_argument("--json", action="store_true", help="Machine output.")
    parser.add_argument(
        "--strict", action="store_true", help="Fail on warnings as well as errors."
    )
    args = parser.parse_args(argv)

    project = Path(args.project_dir)
    if not project.is_dir():
        print(f"Error: not a directory: {project}", file=sys.stderr)
        return 2

    notes: list[str] = []
    findings = lint_project(project, notes=notes)
    errors = sum(1 for f in findings if f.level == "error")
    warnings = len(findings) - errors

    if args.json:
        print(json.dumps(
            {
                "ok": errors == 0 and not (args.strict and warnings),
                "errors": errors,
                "warnings": warnings,
                "findings": [f.to_dict() for f in findings],
                "notes": notes,
            },
            indent=2, sort_keys=True, ensure_ascii=False,
        ))
    else:
        for note in notes:
            print(note, file=sys.stderr)
        for finding in findings:
            print(finding)
        if errors:
            print(f"stapel-index-lint: {errors} error(s) in {project}")
        elif warnings:
            print(f"stapel-index-lint: {warnings} warning(s) in {project}")
        else:
            print(f"stapel-index-lint: clean ({project})")
    if errors:
        return 1
    return 1 if (args.strict and warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
