"""
stapel-config-lint — the config-in-one-place gate (static-scaffold-and-config.md
§2). Keeps a project's configuration reads and its CONFIG.MD registry in sync,
in the ``stapel-migration-lint`` / ``stapel-adoption-lint`` idiom (rule codes,
``--json``, ``--strict``, exit 1 on any error).

The law is: configuration is read in ONE place (the settings module), and every
key read there is described in the project-root CONFIG.MD. The rules enforce
the two halves of that sentence, its converse, the *existence* of the registry
itself (CFG000), and — in a library checkout — that the rows a library owns
name knobs it actually has (CFG005).

Rules
-----
CFG000  (warning) The project has no CONFIG.MD at all. Everything below that
        reads against the registry (CFG002, CFG003) is then unrunnable, so the
        gate goes green by having no registry — the one failure mode a
        registry law must not have. Warning-level, not error: it names the
        hole in every ``stapel-verify`` run without failing a build that
        simply has not done the CONFIG.MD sweep yet.

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

CFG004  (warning) A CONFIG.MD row's Purpose column is empty — "documented" in
        name only, useless to whoever regenerates the aggregate or fills the
        client-facing config questionnaire (§57 owner directive item 8: the
        Purpose column is the whole point of the registry, not decoration).
        Warning-level while the legacy per-lib CONFIG.MD sweep is in progress
        (same posture as DOC001) — promote to error once every onboarded
        lib's CONFIG.MD carries a purpose for each row.

CFG005  (error) **In a library checkout**, a CONFIG.MD row whose owner section
        is this library names a key that exists nowhere in the library: not in
        any ``AppSettings(defaults={...})`` namespace, not in a
        ``declare_config`` call, and not read through
        ``get_config``/``get_secret``/``os.environ``. This is CFG003's mirror
        image and closes CFG003's hole: in a *consuming* project, rows owned by
        a stapel lib are exempt "because the lib reads them itself" — an
        assumption nothing checked. It came from a real switch documented as
        "turn it off without a deploy" that had no settings key at all, so it
        could not be turned off; CFG001 was the wrong class for it (there was
        no read anywhere, which is precisely the defect). Both halves are
        machine-readable, so the rule is an error, not a warning.

``os.environ.setdefault(...)`` is a write, not a read (manage.py / wsgi set
DJANGO_SETTINGS_MODULE that way) and is never flagged.

Exit codes: 0 clean, 1 errors present (``--strict`` also fails on any note is
not applicable here — all findings are errors; CFG004 warnings never fail the
build on their own — see ``stapel-verify`` for how warnings are surfaced),
2 usage/environment errors.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
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


def _callee_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _dict_string_keys(node: ast.AST) -> list[str]:
    if not isinstance(node, ast.Dict):
        return []
    return [
        k.value for k in node.keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    ]


def _module_dicts(tree: ast.Module) -> dict[str, ast.Dict]:
    """Module-level ``NAME = {...}`` assignments (the ``DEFAULTS = {...}``
    idiom every lib's conf.py uses so the capabilities emitter can read the
    axes without re-parsing the ``AppSettings()`` call)."""
    out: dict[str, ast.Dict] = {}
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.target is not None:
            targets = [node.target]
        value = getattr(node, "value", None)
        if not isinstance(value, ast.Dict):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                out[target.id] = value
    return out


def collect_settings_defaults(project: Path) -> dict[str, tuple[str, int]]:
    """Keys of every ``AppSettings(..., defaults=...)`` in *project*.

    The library half of the registry law is machine-readable: an
    ``AppSettings`` namespace's ``defaults`` dict IS the set of knobs the
    library actually offers. Keys are namespace-relative (``AppSettings._raw``
    resolves a bare key name against the namespace dict / a flat setting / the
    environment), which is exactly how a lib's CONFIG.MD names them.

    ``defaults=`` is usually a module-level ``DEFAULTS = {...}`` name rather
    than an inline literal, so a named dict in the same module is resolved.
    Nested one level: a block key whose value is itself a dict literal (the
    ``VECTOR`` / ``RERANK`` idiom) contributes its inner keys too, because
    CONFIG.MD documents those inner knobs as rows of their own.

    Also picks up ``declare_config("KEY", ...)`` (``stapel_core.config``), the
    in-code alternative to a hand-written row.
    """
    found: dict[str, tuple[str, int]] = {}

    def _record(dict_node: ast.Dict, py: Path, depth: int = 0) -> None:
        for key_node, value_node in zip(dict_node.keys, dict_node.values):
            if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
                continue
            found.setdefault(key_node.value, (str(py), key_node.lineno))
            if depth < 1 and isinstance(value_node, ast.Dict):
                _record(value_node, py, depth + 1)

    for py in _walk_py(project):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        module_dicts = _module_dicts(tree)
        # The DEFAULTS/-suffixed module dicts are the declared surface even
        # when the AppSettings() call lives in another module.
        for name, node in module_dicts.items():
            if name == "DEFAULTS" or name.endswith("_DEFAULTS") or name.startswith("DEFAULT_"):
                _record(node, py)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _callee_name(node.func)
            if name == "AppSettings":
                defaults: Optional[ast.AST] = None
                for kw in node.keywords:
                    if kw.arg == "defaults":
                        defaults = kw.value
                if defaults is None and len(node.args) >= 2:
                    defaults = node.args[1]
                if isinstance(defaults, ast.Name):
                    defaults = module_dicts.get(defaults.id)
                if isinstance(defaults, ast.Dict):
                    _record(defaults, py)
            elif name == "declare_config":
                key = _str_first_arg(node)
                if key:
                    found.setdefault(key, (str(py), node.lineno))
    return found


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """ids of the string Constants that are docstrings / bare string
    expressions — prose, not code that names a key."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            out.add(id(node.value))
    return out


def collect_key_mentions(project: Path) -> set[str]:
    """Every configuration-key-shaped name the library's *code* names.

    Backing evidence of last resort for CFG005. A key is "wired" in ways an
    AST read-matcher cannot follow — ``_resolve("KV_MOUNT", "VAULT_KV_MOUNT",
    ...)`` behind a helper, ``BOOTSTRAP_PROVIDER_ENV = "STAPEL_SECRETS_
    PROVIDER"`` then read through the constant, a flat ``DEBUG = ...`` Django
    setting. All of those are real wiring; none of them is a
    ``get_config("KEY")`` call. So CFG005 asks the weaker, exactly checkable
    question instead: does the key exist in the code **at all**?

    Counted: string constants that are not docstrings, module-level
    UPPER_CASE assignment targets, and keyword-argument names. Deliberately
    NOT counted: comments and docstrings — a key that appears only in prose is
    documented, which is the thing being questioned.
    """
    mentions: set[str] = set()
    for py in _walk_py(project):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) not in docstrings and node.value:
                    mentions.add(node.value)
            elif isinstance(node, ast.Name) and node.id.isupper():
                mentions.add(node.id)
            elif isinstance(node, ast.Attribute) and node.attr.isupper():
                mentions.add(node.attr)
    return mentions


def _pyproject_name(project: Path) -> Optional[str]:
    pyproject = project / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'(?m)^\s*name\s*=\s*["\']([^"\']+)["\']', text)
    return match.group(1) if match else None


def library_distribution(project: Path) -> Optional[str]:
    """``stapel-<lib>`` when *project* is a **library checkout**, else None.

    A library repo ships a ``pyproject.toml`` naming a ``stapel-*``
    distribution and has no ``manage.py`` — it is the unit that OWNS its
    CONFIG.MD rows. A consuming service (which has ``manage.py``) is the
    CFG002/CFG003 world instead: there, library-owned rows are deliberately
    exempt because the lib reads them internally. CFG005 is the check that
    the assumption behind that exemption is true.
    """
    if (project / "manage.py").is_file():
        return None
    name = _pyproject_name(project)
    if not name:
        return None
    normalized = name.strip().lower().replace("_", "-")
    return normalized if normalized.startswith("stapel-") else None


def _owner_matches(owner: Optional[str], distribution: str) -> bool:
    if not owner:
        return False
    return owner.strip().lower().replace("_", "-") == distribution


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
        # ------------------------------------------------------------- CFG000
        # The registry law must not be opt-out by omission. This used to be a
        # note only — i.e. a project with no CONFIG.MD had CFG002/CFG003
        # silently skipped and a green gate, and its pre-commit config said so
        # in a comment ("CFG002/CFG003 у stapel-verify скипаются и так"). A
        # warning is the honest level: it shows up in every stapel-verify run
        # without failing a build that has not done the sweep yet.
        notes.append(
            f"stapel-config-lint: no {CONFIG_MD} at project root — CFG002/CFG003 "
            f"skipped (nothing to check reads against). CFG001 still enforced."
        )
        # Only for a unit that HAS configuration to register: a Django service
        # (manage.py), a stapel library distribution, or anything that reads a
        # config key at all. A repo that reads nothing (a TS package, a spec
        # repo) has no registry to be missing.
        has_config_surface = bool(reads) or (project / "manage.py").is_file() \
            or library_distribution(project) is not None
        if not has_config_surface:
            findings.sort(key=lambda f: (f.path, f.line, f.rule))
            return findings
        findings.append(Finding(
            str(project / CONFIG_MD), 0, "CFG000",
            f"no {CONFIG_MD} at the root — the config registry does not exist, "
            f"so CFG002 (undeclared knob) and CFG003 (stale row) cannot run at "
            f"all and every configuration key in this project is undocumented "
            f"by construction. Create {CONFIG_MD} (stapel-config-manifest "
            f"regenerates the library-owned sections)",
            level="warning",
        ))
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

    # ------------------------------------------------------------------ CFG004
    for entry in entries:
        if entry.purpose.strip():
            continue
        findings.append(Finding(
            str(config_md), entry.line, "CFG004",
            f"'{entry.key}' has an empty Purpose column in {CONFIG_MD} — "
            f"fill in what the key is for (regenerated by "
            f"stapel-config-manifest from the owning lib's own CONFIG.MD; "
            f"a project-owned row is edited directly)",
            level="warning",
        ))

    # ------------------------------------------------------------------ CFG005
    distribution = library_distribution(project)
    if distribution:
        defaults = collect_settings_defaults(project)
        backed = set(defaults) | read_keys | collect_key_mentions(project)
        for entry in entries:
            if not _owner_matches(entry.owner, distribution):
                continue  # a row this lib re-documents from another owner
            if entry.key in backed:
                continue
            findings.append(Finding(
                str(config_md), entry.line, "CFG005",
                f"'{entry.key}' is documented in {distribution}'s {CONFIG_MD} but "
                f"exists nowhere in the library's code: it is in no "
                f"AppSettings(defaults=...) namespace, no declare_config, no "
                f"config read, and it is not even named as a literal or a "
                f"setting anywhere — only in this table. A knob documented as "
                f"switchable that was never introduced cannot be switched. Add "
                f"it to the owning namespace's defaults, or drop the row",
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
