"""
stapel-swap-lint — the anti-lock-in indirection gate (§55,
``docs/pending/extensibility-presenters.md``). Keeps consumers of a
swappable DAO model / :class:`~stapel_core.django.api.presenters.Presenter`
going through ``get_model()`` / ``get_presenter()`` (``stapel_core.django.
swappable``) instead of quietly reaching around them — the exact bug the
extensibility research flagged in django-oscar's ``get_class()`` (issue
#3232): a stray direct import silently defeats a host's config-swap for
that one call site, with no error, just a host override that never takes
effect.

Rules
-----
SWAP001  (error) A direct ``import``/``from ... import`` of a class that is
         itself registered as the *default* of a ``get_model(key, default=...)``
         / ``get_presenter(key, default=...)`` call somewhere in the scanned
         tree, OR a direct call/instantiation of that class via such an
         import — anywhere outside the module that defines the class itself,
         and outside ``tests/``. The registry is built in one pass over the
         whole project (every ``get_model``/``get_presenter`` call's second
         positional argument or ``default=`` keyword, when it is a string
         literal); the second pass flags any other file that names one of
         those dotted paths directly instead of resolving it dynamically.
         Suppress a deliberate exception with ``# noqa: SWAP001``.

SWAP002  (error) A ``views.py`` instantiates a ``@dataclass`` DTO imported
         from a sibling ``dto.py`` directly (``SomeDTO(...)``) instead of
         going through a presenter (``get_presenter(KEY, default=...)()``
         /``.present(...)``). A view that fills in a DTO literal owns
         DAO->DTO mapping logic no host can intercept via config — the same
         lock-in shape as SWAP001, one layer up the stack. Only DTOs
         *imported from* a ``dto.py`` module are in scope: a dataclass
         defined and used locally within the same ``views.py`` is not the
         cross-module presenter contract this rule protects. ``tests/`` is
         excluded (fixtures/factories legitimately build DTOs by hand).
         Suppress with ``# noqa: SWAP002``.

Scope and false-positive posture
---------------------------------
Both rules are deliberately conservative about *what* counts as a
swappable/DTO name — they only ever flag names this scan can trace back to a
``get_model``/``get_presenter`` default or a ``dto.py`` import, never a
bare heuristic on class naming. An unresolvable import (plain ``import pkg.mod``
with attribute-qualified access, rather than ``from pkg.mod import Name``) is
not flagged — the ambiguity is resolved towards *not* flagging, the opposite
default from ``url_lint``'s URL001, because a false SWAP001/SWAP002 here
blocks a legitimate presenter/model definition file, not just a width choice.

Exit codes: 0 clean, 1 errors present, 2 usage/environment errors.
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

#: function names that declare a swappable-class default (arg 1 or kw "default")
_SWAP_ACCESSORS = {"get_model", "get_presenter"}


@dataclass
class Violation:
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
# file discovery
# ---------------------------------------------------------------------------


def _walk_py(root: Path) -> Iterable[Path]:
    """All ``.py`` files under ``root``, skipping ``tests/`` directories and
    ``test_*.py`` / ``tests.py`` files everywhere else — both rules exempt
    tests (fixtures/factories legitimately construct concrete classes)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")
        )
        for fname in sorted(filenames):
            if not fname.endswith(".py"):
                continue
            if fname.startswith("test_") or fname == "tests.py":
                continue
            yield Path(dirpath) / fname


def _noqa_rules(line: str) -> Optional[set]:
    if "# noqa" not in line:
        return None
    if "# noqa:" not in line:
        return set()
    tail = line.split("# noqa:", 1)[1]
    return {r.strip() for r in tail.replace(";", ",").split(",") if r.strip()}


def _read(path: Path) -> tuple[Optional[ast.Module], list[str]]:
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None, []
    return tree, src.splitlines()


# ---------------------------------------------------------------------------
# import-alias resolution (shared shape with url_lint._name_origins)
# ---------------------------------------------------------------------------


def _name_origins(tree: ast.Module) -> dict[str, tuple[str, int]]:
    """local bound name -> (dotted "module.attr" origin, import lineno)."""
    origins: dict[str, tuple[str, int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bound = alias.asname or alias.name
                origins[bound] = (f"{node.module}.{alias.name}", node.lineno)
    return origins


def _func_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _str_arg(call: ast.Call, index: int, kw: str) -> Optional[str]:
    if len(call.args) > index:
        arg = call.args[index]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        return None
    for keyword in call.keywords:
        if keyword.arg == kw and isinstance(keyword.value, ast.Constant):
            if isinstance(keyword.value.value, str):
                return keyword.value.value
    return None


# ---------------------------------------------------------------------------
# SWAP001 — registry build + violation scan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SwapEntry:
    key: str
    default: str  # dotted "module.ClassName"
    path: str
    line: int


def collect_swap_registry(project: Path) -> list[SwapEntry]:
    """Every ``get_model(key, default)`` / ``get_presenter(key, default)``
    call in the project, as a swap-key -> dotted-default declaration."""
    entries: list[SwapEntry] = []
    for py in _walk_py(project):
        tree, _ = _read(py)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _func_name(node) not in _SWAP_ACCESSORS:
                continue
            # The registry key itself is usually a module-level constant
            # (``get_presenter(PRESENTER_KEY, default=...)``), not a literal
            # at the call site — only the dotted *default* class path needs
            # to be a string literal for this scan to trace it.
            default = _str_arg(node, 1, "default")
            if default:
                key = _str_arg(node, 0, "key") or "<dynamic>"
                entries.append(SwapEntry(key, default, str(py), node.lineno))
    return entries


def _registry_by_dotted(entries: list[SwapEntry]) -> dict[str, str]:
    """dotted default path -> the module part (everything before the last
    ``.``), so a later import can be matched against ``module.ClassName``."""
    return {e.default: e.default.rsplit(".", 1)[0] for e in entries}


def find_swap001(project: Path, entries: Optional[list[SwapEntry]] = None) -> list[Violation]:
    if entries is None:
        entries = collect_swap_registry(project)
    if not entries:
        return []
    dotted_defaults = {e.default for e in entries}
    # module dotted path -> True, for "defined here, no import statement expected"
    owning_modules = {d.rsplit(".", 1)[0] for d in dotted_defaults}

    violations: list[Violation] = []
    for py in _walk_py(project):
        tree, lines = _read(py)
        if tree is None:
            continue
        origins = _name_origins(tree)
        # a name bound via "from X import Y" whose "X.Y" is a registered default
        flagged_names: set[str] = set()
        for bound, (origin, lineno) in origins.items():
            if origin not in dotted_defaults:
                continue
            raw = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
            suppressed = _noqa_rules(raw)
            if suppressed is not None and (not suppressed or "SWAP001" in suppressed):
                flagged_names.discard(bound)
                continue
            violations.append(Violation(
                str(py), lineno, "SWAP001",
                f"direct import of swappable class '{origin}' — bypasses the "
                f"get_model()/get_presenter() indirection (STAPEL_SWAP registry); "
                f"resolve it through the get_*() accessor instead of importing "
                f"the default class directly",
            ))
            flagged_names.add(bound)

        # direct instantiation via one of those imported names: SomeClass(...)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            name = node.func.id
            if name not in flagged_names:
                continue
            raw = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
            suppressed = _noqa_rules(raw)
            if suppressed is not None and (not suppressed or "SWAP001" in suppressed):
                continue
            violations.append(Violation(
                str(py), node.lineno, "SWAP001",
                f"direct instantiation of swappable class '{name}' — bypasses "
                f"the get_model()/get_presenter() indirection; use the "
                f"get_*() accessor and call the class it returns instead",
            ))
    _ = owning_modules  # kept for readability of the registry shape
    violations.sort(key=lambda v: (v.path, v.line))
    return violations


# ---------------------------------------------------------------------------
# SWAP002 — views.py building a dto.py dataclass directly
# ---------------------------------------------------------------------------


def _dataclass_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for d in node.decorator_list:
            deco_name = (
                d.id if isinstance(d, ast.Name)
                else d.attr if isinstance(d, ast.Attribute)
                else d.func.id if isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                else d.func.attr if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                else None
            )
            if deco_name == "dataclass":
                names.add(node.name)
    return names


def _is_dto_module(module: Optional[str]) -> bool:
    if not module:
        return False
    return module == "dto" or module.endswith(".dto")


def find_swap002(project: Path) -> list[Violation]:
    violations: list[Violation] = []
    for py in _walk_py(project):
        if "views" not in py.name:
            continue
        tree, lines = _read(py)
        if tree is None:
            continue

        # DTO dataclass names imported (not locally defined) from a dto.py
        dto_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and _is_dto_module(node.module):
                for alias in node.names:
                    dto_names.add(alias.asname or alias.name)
        if not dto_names:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in dto_names:
                continue
            raw = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
            suppressed = _noqa_rules(raw)
            if suppressed is not None and (not suppressed or "SWAP002" in suppressed):
                continue
            violations.append(Violation(
                str(py), node.lineno, "SWAP002",
                f"{node.func.id}(...) instantiated directly in a view — build "
                f"it through a presenter (get_presenter(KEY, default=...)"
                f".present(...)) instead of filling in the DTO by hand, or a "
                f"host swap of the presenter never runs for this response",
            ))
    violations.sort(key=lambda v: (v.path, v.line))
    return violations


# ---------------------------------------------------------------------------
# combined driver
# ---------------------------------------------------------------------------


def lint_project(project: Path) -> list[Violation]:
    project = project.resolve()
    entries = collect_swap_registry(project)
    violations = find_swap001(project, entries)
    violations.extend(find_swap002(project))
    violations.sort(key=lambda v: (v.path, v.line, v.rule))
    return violations


def lint_paths(paths: Iterable) -> list[Violation]:
    violations: list[Violation] = []
    for raw in paths:
        root = Path(raw)
        if not root.exists():
            raise SystemExit(f"Error: path does not exist: {root}")
        violations.extend(lint_project(root))
    violations.sort(key=lambda v: (v.path, v.line, v.rule))
    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-swap-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths", nargs="*", default=["."],
        help="Project roots or module repos to lint (default: .)",
    )
    parser.add_argument("--json", action="store_true", help="Machine output")
    args = parser.parse_args(argv)

    violations = lint_paths(args.paths)
    errors = [v for v in violations if v.level == "error"]

    if args.json:
        print(json.dumps(
            {
                "ok": not errors,
                "errors": len(errors),
                "violations": [v.to_dict() for v in violations],
            },
            indent=2, sort_keys=True, ensure_ascii=False,
        ))
    else:
        for violation in violations:
            print(violation)
        if violations:
            print(f"\n{len(errors)} error(s) found.")
        else:
            print("No violations found.")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
