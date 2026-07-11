"""
stapel-config-lint — the config-in-one-place gate (static-scaffold-and-config.md
§2). Keeps a project's configuration reads and its CONFIG.MD registry in sync,
in the ``stapel-migration-lint`` / ``stapel-adoption-lint`` idiom (rule codes,
``--json``, ``--strict``, exit 1 on any error).

The law is: configuration is read in ONE place (the settings module), and every
key read there is described in the project-root CONFIG.MD. Three rules enforce
the two halves of that sentence plus its converse.

Rules
-----
CFG001  (error) A configuration/secret read happens OUTSIDE the settings module
        — ``get_config(...)`` / ``get_secret(...)`` / ``os.environ[...]`` /
        ``os.environ.get(...)`` / ``os.getenv(...)`` in any file that is not
        ``settings.py`` (or a module of a ``settings/`` package). Config must
        funnel through settings so there is a single audited surface; a stray
        read elsewhere hides a knob from CONFIG.MD and from ops. Suppress a
        deliberate exception with ``# noqa: CFG001`` on the read's line.

CFG002  (error) A key IS read in the settings module but has no row in
        CONFIG.MD — an undeclared knob. Add a ``| KEY | env|vault | … |`` row
        (or, if the read is genuinely not configuration, drop it). Skipped with
        a note when the project has no CONFIG.MD at all.

CFG003  (error) A key HAS a CONFIG.MD row but is read nowhere in the project —
        a stale registry entry. Remove the row, or wire the key. Keys owned by
        a stapel lib (``## stapel-<mod>`` section) are exempt: the lib reads
        them internally, the project need not.

``os.environ.setdefault(...)`` is a write, not a read (manage.py / wsgi set
DJANGO_SETTINGS_MODULE that way) and is never flagged.

Exit codes: 0 clean, 1 errors present (``--strict`` also fails on any note is
not applicable here — all findings are errors), 2 usage/environment errors.
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

from .config_manifest import CONFIG_MD, ConfigEntry, parse_config_md

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
    # test scaffolding legitimately touches os.environ (fixtures, harnesses);
    # the config-in-settings law is about the runtime service surface.
    "tests",
}

#: call targets that read one config key from their first string argument
_READ_CALLS = {"get_config", "get_secret", "getenv"}
#: attribute-call read: os.environ.get(...) / environ.get(...)
_ENVIRON_GET = "get"


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


@dataclass
class ConfigRead:
    key: str
    path: str
    line: int
    in_settings: bool


# ---------------------------------------------------------------------------
# file discovery
# ---------------------------------------------------------------------------


def _walk_py(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")
        )
        for fname in sorted(filenames):
            if fname.endswith(".py"):
                yield Path(dirpath) / fname


def _is_settings_file(path: Path) -> bool:
    return path.parent.name == "settings" or path.name.startswith("settings")


# ---------------------------------------------------------------------------
# AST read extraction
# ---------------------------------------------------------------------------


def _str_first_arg(call: ast.Call) -> Optional[str]:
    if call.args:
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _is_environ(node: ast.AST) -> bool:
    """``os.environ`` or a bare ``environ`` (from os import environ)."""
    if isinstance(node, ast.Attribute):
        return node.attr == "environ" and isinstance(node.value, ast.Name) and node.value.id == "os"
    return isinstance(node, ast.Name) and node.id == "environ"


def _reads_in_call(call: ast.Call) -> Optional[str]:
    func = call.func
    # get_config(...) / get_secret(...) / getenv(...)  — Name or Attribute
    if isinstance(func, ast.Name) and func.id in _READ_CALLS:
        return _str_first_arg(call)
    if isinstance(func, ast.Attribute):
        if func.attr in ("get_config", "get_secret", "getenv"):
            return _str_first_arg(call)
        # os.environ.get("X") / environ.get("X")
        if func.attr == _ENVIRON_GET and _is_environ(func.value):
            return _str_first_arg(call)
    return None


def _reads_in_subscript(node: ast.Subscript) -> Optional[str]:
    # os.environ["X"] / environ["X"] in a load context
    if not _is_environ(node.value):
        return None
    key_node = node.slice
    if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
        return key_node.value
    return None


def _noqa_rules(line: str) -> Optional[set[str]]:
    """None when no noqa; empty set = blanket noqa; else the listed rules."""
    if "# noqa" not in line:
        return None
    if "# noqa:" not in line:
        return set()
    tail = line.split("# noqa:", 1)[1]
    return {r.strip() for r in tail.replace(";", ",").split(",") if r.strip()}


def collect_reads(project: Path) -> list[ConfigRead]:
    reads: list[ConfigRead] = []
    for py in _walk_py(project):
        try:
            src = py.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(py))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        in_settings = _is_settings_file(py)
        for node in ast.walk(tree):
            key: Optional[str] = None
            if isinstance(node, ast.Call):
                key = _reads_in_call(node)
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
                key = _reads_in_subscript(node)
            if key:
                reads.append(ConfigRead(key, str(py), node.lineno, in_settings))
    return reads


# ---------------------------------------------------------------------------
# lint driver
# ---------------------------------------------------------------------------


def find_config_md(project: Path) -> Optional[Path]:
    cand = project / CONFIG_MD
    return cand if cand.is_file() else None


def lint_project(project: Path, *, notes: Optional[list[str]] = None) -> list[Finding]:
    project = project.resolve()
    if notes is None:
        notes = []
    findings: list[Finding] = []

    reads = collect_reads(project)

    # ------------------------------------------------------------------ CFG001
    line_cache: dict[str, list[str]] = {}
    for read in reads:
        if read.in_settings:
            continue
        lines = line_cache.get(read.path)
        if lines is None:
            try:
                lines = Path(read.path).read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                lines = []
            line_cache[read.path] = lines
        raw = lines[read.line - 1] if 0 < read.line <= len(lines) else ""
        suppressed = _noqa_rules(raw)
        if suppressed is not None and (not suppressed or "CFG001" in suppressed):
            continue
        findings.append(Finding(
            read.path, read.line, "CFG001",
            f"config/secret read of '{read.key}' outside the settings module — "
            f"route it through config/settings (get_config) so every knob is in "
            f"one audited place and in CONFIG.MD; suppress a deliberate exception "
            f"with '# noqa: CFG001'",
        ))

    config_md = find_config_md(project)
    if config_md is None:
        notes.append(
            f"stapel-config-lint: no {CONFIG_MD} at project root — CFG002/CFG003 "
            f"skipped (nothing to check reads against). CFG001 still enforced."
        )
        findings.sort(key=lambda f: (f.path, f.line, f.rule))
        return findings

    entries: list[ConfigEntry] = parse_config_md(config_md)
    declared = {e.key: e for e in entries}
    read_keys = {r.key for r in reads}
    settings_read_keys = {r.key for r in reads if r.in_settings}

    # ------------------------------------------------------------------ CFG002
    reported_002: set[str] = set()
    for read in reads:
        if not read.in_settings or read.key in declared or read.key in reported_002:
            continue
        reported_002.add(read.key)
        findings.append(Finding(
            read.path, read.line, "CFG002",
            f"'{read.key}' is read in settings but has no {CONFIG_MD} row — "
            f"declare it (| {read.key} | env|vault | purpose | required | default |) "
            f"or drop the read if it is not configuration",
        ))

    # ------------------------------------------------------------------ CFG003
    for entry in entries:
        if entry.library_owned:
            continue  # read inside the owning lib, not in the project
        if entry.key in read_keys:
            continue
        findings.append(Finding(
            str(config_md), entry.line, "CFG003",
            f"'{entry.key}' is declared in {CONFIG_MD} but read nowhere in the "
            f"project — remove the stale row or wire the key through get_config "
            f"in settings",
        ))

    _ = settings_read_keys  # (kept for readability of the two key sets)
    findings.sort(key=lambda f: (f.path, f.line, f.rule))
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-config-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "project_dir", nargs="?", default=".",
        help="Project directory to lint (default: .)",
    )
    parser.add_argument("--json", action="store_true", help="Machine output")
    parser.add_argument(
        "--strict", action="store_true",
        help="(All config-lint findings are already errors; flag kept for "
             "idiom parity with the other stapel linters.)",
    )
    args = parser.parse_args(argv)

    project = Path(args.project_dir)
    if not project.is_dir():
        print(f"Error: not a directory: {project}", file=sys.stderr)
        return 2

    notes: list[str] = []
    try:
        findings = lint_project(project, notes=notes)
    except Exception as exc:  # malformed CONFIG.MD, etc.
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    errors = [f for f in findings if f.level == "error"]

    if args.json:
        print(json.dumps(
            {
                "ok": not errors,
                "errors": len(errors),
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
        if findings:
            print(f"\n{len(errors)} error(s) found in {project}.")
        else:
            print(f"No config issues found in {project}.")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
