"""
stapel-doc-lint — Django model field documentation gate (DOC-FIELD,
``docs/pending/extensibility-presenters.md`` §3). Field-level docs feed the
presenter auto-catalog (§4) and OpenAPI schema generation (§2, "as-is"
fields there source their description straight from the model field's
``help_text``) — an undocumented field is a silent gap in both.

Rule
----
DOC001  (warning) A Django model field assignment (``name = models.XField(...)``
        / any ``...Field(...)`` / ``ForeignKey``/``OneToOneField``/
        ``ManyToManyField``/``GenericForeignKey`` call) has neither an
        explicit ``help_text=`` keyword NOR a ``#`` comment on the line
        immediately above it. Warning, not error: the legacy model surface
        across the workspace is large and this is a documentation gate, not
        a correctness one (unlike SWAP001/SWAP002) — see the rollout note in
        ``lint.py``'s R100 for the same W-before-E posture. Suppress a
        deliberate exception with ``# noqa: DOC001`` on the field's line.

Not this rule's job
--------------------
``@dataclass`` DTOs (``dto.py``) already have a docstring gate, ``R004`` in
``lint.py`` — that check is class-level (the dataclass itself must have a
docstring) and applies to a different file kind entirely. DOC001 never scans
``dto.py`` and never duplicates R004.

Scope and false-positive posture
---------------------------------
Only fields declared directly in a model's class body are in scope — ``Meta``
inner classes, methods, and non-field class attributes (managers, plain
constants) are not calls whose callee name ends in "Field" or is one of the
known relation names, so they never match. A field whose call spans multiple
lines is still matched by the ``help_text=`` keyword scan (AST-based, not
line-based) — only the "preceding comment" fallback is line-based, checked
against the assignment's own start line.

Exit codes: 0 clean (only warnings possible; DOC001 never fails the build on
its own — see ``stapel-verify`` for how warnings are surfaced), 2 usage/
environment errors.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

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
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "worktrees",
    "site-packages",
    "migrations",
    "tests",
}

#: relation field constructors that don't end in "Field" but are still fields
_RELATION_NAMES = {"ForeignKey", "GenericForeignKey"}


@dataclass
class Violation:
    path: str
    line: int
    rule: str
    message: str
    level: str = "warning"

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
# file discovery
# ---------------------------------------------------------------------------


def _walk_py(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")
        )
        for fname in sorted(filenames):
            if not fname.endswith(".py"):
                continue
            if fname.startswith("test_") or fname == "tests.py" or fname == "dto.py":
                continue
            yield Path(dirpath) / fname


def _noqa_rules(line: str) -> Optional[set]:
    if "# noqa" not in line:
        return None
    if "# noqa:" not in line:
        return set()
    tail = line.split("# noqa:", 1)[1]
    return {r.strip() for r in tail.replace(";", ",").split(",") if r.strip()}


# ---------------------------------------------------------------------------
# model / field detection
# ---------------------------------------------------------------------------


def _base_names(class_node: ast.ClassDef) -> set[str]:
    names = set()
    for base in class_node.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def _looks_like_django_model(class_node: ast.ClassDef) -> bool:
    """Best-effort: a class whose bases include something spelled ``Model``
    (``models.Model`` / a local abstract base ending in ``Model``/``Base``/
    ``Mixin``). Not full MRO resolution (no Django app registry here, this is
    a pure-AST linter) — errs toward scanning a few non-model classes over
    silently skipping real models behind a project's abstract base."""
    bases = _base_names(class_node)
    if "Model" in bases:
        return True
    return any(b.endswith("Model") for b in bases)


def _call_is_field(call: ast.Call) -> bool:
    func = call.func
    name = (
        func.id if isinstance(func, ast.Name)
        else func.attr if isinstance(func, ast.Attribute)
        else None
    )
    if name is None:
        return False
    return name.endswith("Field") or name in _RELATION_NAMES


def _has_help_text(call: ast.Call) -> bool:
    return any(kw.arg == "help_text" for kw in call.keywords)


def _preceding_comment(lines: list[str], lineno: int) -> bool:
    """A ``#`` comment on the non-blank line immediately above ``lineno``
    (1-indexed) counts as the field's docstring-comment."""
    idx = lineno - 2  # zero-indexed line right above
    while idx >= 0:
        stripped = lines[idx].strip()
        if stripped == "":
            idx -= 1
            continue
        return stripped.startswith("#")
    return False


# ---------------------------------------------------------------------------
# lint driver
# ---------------------------------------------------------------------------


def lint_file(path: Path) -> list[Violation]:
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    lines = src.splitlines()
    violations: list[Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not _looks_like_django_model(node):
            continue
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            if not isinstance(item.value, ast.Call) or not _call_is_field(item.value):
                continue
            if len(item.targets) != 1 or not isinstance(item.targets[0], ast.Name):
                continue
            field_name = item.targets[0].id
            if _has_help_text(item.value):
                continue
            if _preceding_comment(lines, item.lineno):
                continue

            raw = lines[item.lineno - 1] if 0 < item.lineno <= len(lines) else ""
            suppressed = _noqa_rules(raw)
            if suppressed is not None and (not suppressed or "DOC001" in suppressed):
                continue

            violations.append(Violation(
                str(path), item.lineno, "DOC001",
                f"{node.name}.{field_name} has neither help_text= nor a "
                f"preceding '#' comment — undocumented model fields are a "
                f"silent gap in the presenter auto-catalog and generated "
                f"OpenAPI schema (extensibility-presenters.md §3/§4)",
            ))
    return violations


def lint_project(project: Path) -> list[Violation]:
    violations: list[Violation] = []
    for py in _walk_py(project.resolve()):
        violations.extend(lint_file(py))
    violations.sort(key=lambda v: (v.path, v.line))
    return violations


def lint_paths(paths: Iterable) -> list[Violation]:
    violations: list[Violation] = []
    for raw in paths:
        root = Path(raw)
        if not root.exists():
            raise SystemExit(f"Error: path does not exist: {root}")
        violations.extend(lint_project(root))
    violations.sort(key=lambda v: (v.path, v.line))
    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-doc-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths", nargs="*", default=["."],
        help="Project roots or module repos to lint (default: .)",
    )
    parser.add_argument("--json", action="store_true", help="Machine output")
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit 1 if any DOC001 warning is found (default: warnings never fail the build).",
    )
    args = parser.parse_args(argv)

    violations = lint_paths(args.paths)

    if args.json:
        print(json.dumps(
            {
                "ok": True,
                "warnings": len(violations),
                "violations": [v.to_dict() for v in violations],
            },
            indent=2, sort_keys=True, ensure_ascii=False,
        ))
    else:
        for violation in violations:
            print(violation)
        if violations:
            print(f"\n{len(violations)} warning(s) found.")
        else:
            print("No violations found.")

    return 1 if (args.strict and violations) else 0


if __name__ == "__main__":
    sys.exit(main())
