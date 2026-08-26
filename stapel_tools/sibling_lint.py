"""
stapel-sibling-lint — "the suite imports what nothing declares", in the
``stapel-authz-lint`` / ``stapel-api-lint`` idiom (rule codes, ``--json``,
``--strict``, exit 1 on any error).

Why this exists
----------------
On 2026-08-24 three releases died of one defect within a few hours of each
other — stapel-chat 0.5.0, stapel-core 0.44.0, stapel-notifications 0.17.0.
All three were green on a laptop and red on the runner, at test *setup*, with
``ModuleNotFoundError``. All three were re-released the next morning
(0.5.1 / 0.44.1 / 0.17.1) with no product change at all.

The mechanism is the same every time: this fleet develops in ONE shared
virtualenv that has every sibling installed, so a test may import
``stapel_moderation`` while ``pyproject.toml`` never says the suite needs it.
Nothing in the repo disagrees — pytest is happy, ruff is happy, review is
happy, because a reader cannot see an absence. The runner, which installs only
what is declared, is the first machine to find out.

The three costumes, all of them real:

* **A plain import** (chat 0.5.0). ``tests/test_moderation_seam.py`` did
  ``from stapel_moderation.registry import reset_registries`` inside a fixture,
  and ``tests/test_attachments.py`` reached for ``stapel_cdn.kinds`` inside a
  helper. Neither package was named anywhere in ``pyproject.toml``. Depth is
  the whole trick: the imports that bit were inside functions and ``try``
  blocks, where a module-header grep does not look.
* **A settings string** (core 0.44.0). ``override_settings(INSTALLED_APPS=
  ["stapel_realtime"])``. There is no ``import`` token on that line, and it
  still imports: Django reloads the app registry on ``setting_changed`` and
  loads every label in the list *for real*. The fix (0.44.1) was to set the
  list without firing the signal, because the code under test only reads the
  LIST — but the lesson is that a sibling's name in a settings list is an
  import, whatever it looks like.
* **A skip that never ran** (notifications 0.17.0, and chat's CDN agreement
  tests). ``pytest.importorskip("stapel_cdn")`` never fails — and on CI it
  never *ran* either, so the cross-module agreement those tests claim to
  enforce was enforced nowhere for months. This is the quiet half of the same
  defect, and it is the worse half: the first kind reddens a release, this
  kind stays green while asserting nothing.

Rules
-----
SIB001  (error) A suite file imports a top-level ``stapel_*`` package that
        this repo's ``pyproject.toml`` declares neither in
        ``[project].dependencies`` nor in the ``test`` extra. Imports are
        collected at any depth — module scope, function bodies, fixtures,
        ``try`` blocks — because that is where the ones that shipped were.

SIB002  (error) A suite file names an undeclared ``stapel_*`` package in an
        ``INSTALLED_APPS`` list (an ``override_settings``/``modify_settings``
        keyword, a ``settings.INSTALLED_APPS = [...]`` assignment, a module
        constant). Verbatim core 0.44.0.

SIB003  (error) ``pytest.importorskip("stapel_*")`` — or the ``requires``/
        ``installed`` seam from ``tests/siblings.py`` — naming an undeclared
        sibling. A skip is not a declaration; it is a decision to run nothing,
        taken silently, on every machine that lacks the package, which on CI is
        every machine. The seam is how a suite reaches a DECLARED sibling, not
        a way around declaring one.

SIB004  (warning) A **declared** sibling reached behind a skip guard
        (``importorskip``, or ``try: import … except ImportError: skip``) in a
        repo whose workflows never set ``STAPEL_TEST_STRICT_SIBLINGS=1``. The
        guard is right for a contributor's laptop and wrong for CI: there the
        extra IS installed, so a skip means the install step did not do what
        the workflow says it does, and the run stays green while the tests
        that matter did not execute.

SIB005  (warning) The ``test`` extra declares a ``stapel-*`` sibling that no
        suite file imports or names (a ``requires("stapel_x")`` counts as
        naming it — the seam speaks in strings). The other direction, so the extra cannot
        rot into a wish list: an extra that stops describing the suite stops
        being read.

SIB006  (error) A committed ``docs/errors.json`` entry whose ``owner`` is
        neither this module nor ``stapel_core``. The same class, one step
        downstream: the codegen builds that artifact from the error registry of
        the interpreter it runs in, so a workspace virtualenv holding every
        sibling emits a catalogue containing OTHER modules' error keys, while
        CI — which installs only what is declared — emits a smaller one. The
        committed file then describes the machine that generated it instead of
        the module that ships it, and its own drift gate flips depending on who
        ran it last. ``stapel_core`` is the one foreign owner that belongs
        there: every module stands on it and re-publishes its shared vocabulary
        on purpose.

The ``STAPEL_TEST_STRICT_SIBLINGS`` contract (what SIB004 is asking for)
-----------------------------------------------------------------------
Every stapel library's suite honours one environment variable:

    ``STAPEL_TEST_STRICT_SIBLINGS=1`` — a DECLARED sibling that is not
    installed is a FAILURE, never a skip.

Unset (a laptop, a fork, a contributor who ran ``pip install -e .``), a
missing sibling skips with a named reason. Set (CI, always), it fails, because
CI installed ``.[test]`` two steps earlier and a skip there is the install
step lying. The seam is three lines in ``tests/siblings.py`` — ``STRICT``,
``installed(module)``, ``requires(module, dist)`` — and it is what
``stapel-new-library`` now scaffolds; the workflow sets the variable on the
pytest step. The linter never checks the *runtime* behaviour (it cannot: it
does not run the suite). It checks that the declaration exists and that a
skip-guarded suite is not shipped with CI silent about strictness.

Suppression
-----------
``# noqa: SIB00N`` on the reported line, same escape as every other stapel
linter, and write the reason next to it. A bare ``# noqa`` suppresses all of
them.

What this rule deliberately does NOT try to be
-----------------------------------------------
A dependency resolver. It maps a module to a distribution by the fleet's own
convention (``stapel_foo`` -> ``stapel-foo``) and looks that name up in the
two declared lists; a distribution that ships a top-level module under a
different name reads as undeclared, and the answer there is a ``# noqa`` with
the reason. It does not resolve transitive dependencies either — a sibling
that arrives because *another* sibling depends on it is still undeclared here,
on purpose: transitivity is not a contract, and the whole class shipped
because something worked "by accident of what was installed". The one
expansion it does perform is a self-referential extra
(``stapel-notifications[realtime]`` inside the ``test`` extra pulls in what
this same pyproject declares under ``realtime``), because that is a
declaration in this file, about this file.

Exit codes: 0 clean, 1 errors present (``--strict`` promotes SIB004/SIB005 to
errors), 2 usage/environment errors.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import tomllib
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
}

#: Directories inside a suite tree that hold DATA, not code that runs: a
#: fixture project's ``settings.py`` names apps it will never import here.
FIXTURE_DIRS = {"fixtures", "fixture", "data", "snapshots", "golden", "_fixtures"}

#: Directory names that make every ``.py`` under them a suite file.
SUITE_DIRS = {"tests", "test"}

#: The environment variable that IS the contract (see the module docstring).
STRICT_ENV = "STAPEL_TEST_STRICT_SIBLINGS"

#: The `tests/siblings.py` seam this fleet scaffolds: it names a sibling as a
#: string, and it is already strict-aware (it FAILS rather than skips under
#: STAPEL_TEST_STRICT_SIBLINGS), so a declared sibling reached this way never
#: earns SIB004 — that is the whole point of the seam.
SIBLING_SEAM_CALLS = frozenset({"requires", "installed"})

#: Exception names that mean "the import did not happen".
IMPORT_ERRORS = frozenset({"ImportError", "ModuleNotFoundError"})


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
# names
# ---------------------------------------------------------------------------


def normalize_dist(name: str) -> str:
    """PEP 503-ish normalization: ``Stapel_Core`` -> ``stapel-core``."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def dist_of(module: str) -> str:
    """``stapel_moderation`` -> ``stapel-moderation`` (the fleet convention)."""
    return normalize_dist(module.replace("_", "-"))


def _requirement_name(spec: str) -> tuple[str, list[str]]:
    """``"stapel-x[a,b]>=1,<2"`` -> ``("stapel-x", ["a", "b"])``."""
    text = spec.strip()
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[([^\]]*)\])?", text)
    if not match:
        return "", []
    name = normalize_dist(match.group(1))
    extras = [normalize_dist(e) for e in (match.group(2) or "").split(",") if e.strip()]
    return name, extras


# ---------------------------------------------------------------------------
# the declaration side: pyproject.toml
# ---------------------------------------------------------------------------


@dataclass
class Declaration:
    """What ``pyproject.toml`` says the package and its suite need."""

    self_dist: str
    runtime: set
    test_extra: set
    #: dist -> the raw spec string it came from, for SIB005's message
    test_specs: dict
    path: Optional[Path] = None

    @property
    def declared(self) -> set:
        return self.runtime | self.test_extra

    def self_modules(self) -> set:
        return {self.self_dist.replace("-", "_")}


def read_declaration(project: Path) -> Optional[Declaration]:
    """Parse ``<project>/pyproject.toml``; ``None`` when there is none.

    A repo without a ``[project]`` table declares nothing and is not held to a
    declaration — the linter says so in a note rather than inventing one.
    """
    path = project / "pyproject.toml"
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None
    project_table = data.get("project")
    if not isinstance(project_table, dict):
        return None

    self_dist = normalize_dist(str(project_table.get("name") or ""))
    extras = project_table.get("optional-dependencies") or {}
    if not isinstance(extras, dict):
        extras = {}
    extras = {normalize_dist(k): v for k, v in extras.items()}

    def names(specs, seen_extras=None) -> tuple[set, dict]:
        """Distribution names in ``specs``, expanding self-referential extras."""
        seen_extras = set() if seen_extras is None else seen_extras
        found: set = set()
        origin: dict = {}
        for spec in specs or []:
            if not isinstance(spec, str):
                continue
            name, spec_extras = _requirement_name(spec)
            if not name:
                continue
            found.add(name)
            origin.setdefault(name, spec)
            # `stapel-notifications[realtime]` in this package's own `test`
            # extra IS a declaration of what `realtime` holds — it is this
            # file talking about this file, not transitivity.
            if name == self_dist:
                for extra in spec_extras:
                    if extra in seen_extras or extra not in extras:
                        continue
                    sub, sub_origin = names(extras[extra], seen_extras | {extra})
                    found |= sub
                    for key, value in sub_origin.items():
                        origin.setdefault(key, value)
        return found, origin

    runtime, _ = names(project_table.get("dependencies"))
    test_extra, test_specs = names(extras.get("test"))
    test_extra.discard(self_dist)
    return Declaration(
        self_dist=self_dist,
        runtime=runtime,
        test_extra=test_extra,
        test_specs=test_specs,
        path=path,
    )


# ---------------------------------------------------------------------------
# file discovery
# ---------------------------------------------------------------------------


def suite_files(root: Path) -> Iterable[Path]:
    """Every file that IS the test suite: what pytest collects, plus the
    helper modules a collected file imports from its own ``tests/`` package.

    Fixture trees are excluded: a generated project's ``settings.py`` under
    ``tests/fixtures/`` names apps that are data here, never imports.
    """
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and d not in FIXTURE_DIRS and not d.endswith(".egg-info")
        )
        parts = set(here.relative_to(root).parts) if here != root else set()
        in_suite_dir = bool(parts & SUITE_DIRS)
        for fname in sorted(filenames):
            if not fname.endswith(".py"):
                continue
            collected = (
                fname == "conftest.py"
                or fname.startswith("test_")
                or fname.endswith("_test.py")
            )
            if collected or in_suite_dir:
                yield here / fname


def workflow_files(project: Path) -> list:
    wf = project / ".github" / "workflows"
    if not wf.is_dir():
        return []
    return sorted(p for p in wf.iterdir() if p.suffix in (".yml", ".yaml"))


def strict_siblings_declared(project: Path) -> bool:
    """Does any workflow in this repo set ``STAPEL_TEST_STRICT_SIBLINGS=1``?

    Read as text, not as YAML: the question is whether the runner gets the
    variable, and every way of writing that (``env:`` block, inline ``export``,
    a ``$GITHUB_ENV`` append) reaches the same runner.
    """
    for path in workflow_files(project):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        for line in text.splitlines():
            if STRICT_ENV not in line:
                continue
            value = line.split(STRICT_ENV, 1)[1]
            if "1" in value:
                return True
    return False


# ---------------------------------------------------------------------------
# the usage side: what a suite file reaches for
# ---------------------------------------------------------------------------


@dataclass
class Reach:
    """One place a suite file reaches for a sibling module."""

    module: str
    line: int
    kind: str  # "import" | "installed_apps" | "importorskip" | "guarded_import"


def _top(name: str) -> str:
    return name.split(".", 1)[0]


def _final_name(node: ast.AST) -> str:
    """Last identifier of a Name/Attribute chain (``a.b.c`` -> ``"c"``)."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_sibling(name: str, self_modules: set) -> bool:
    return name.startswith("stapel_") and name not in self_modules


def _string_constants(node: ast.AST) -> Iterable[tuple[str, int]]:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub.value, getattr(sub, "lineno", getattr(node, "lineno", 1))


def _importorskip_arg(call: ast.Call) -> Optional[tuple[str, int]]:
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else (
        func.id if isinstance(func, ast.Name) else ""
    )
    if name != "importorskip" or not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value, call.lineno
    return None


def _skips(node: ast.AST) -> bool:
    """Does this handler body skip (rather than fail or re-raise)?"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            name = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else ""
            )
            if name in ("skip", "skipif", "xfail"):
                return True
        if isinstance(sub, ast.Attribute) and sub.attr in ("skip", "skipif"):
            return True
    return False


def _handles_import_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for item in names:
        label = item.attr if isinstance(item, ast.Attribute) else (
            item.id if isinstance(item, ast.Name) else ""
        )
        if label in IMPORT_ERRORS:
            return True
    return False


def _guarded_lines(tree: ast.Module) -> set:
    """Line numbers of imports sitting inside a ``try/except ImportError:
    skip`` — the quiet guard SIB004 is about."""
    guarded: set = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if not any(_handles_import_error(h) and _skips(h) for h in node.handlers):
            continue
        for stmt in node.body:
            for sub in ast.walk(stmt):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    guarded.add(sub.lineno)
    return guarded


def reaches(tree: ast.Module, self_modules: set) -> list:
    """Every sibling reach in one parsed suite file."""
    out: list = []
    guarded = _guarded_lines(tree)

    for node in ast.walk(tree):
        # --- plain imports, at any depth -----------------------------------
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = _top(alias.name)
                if _is_sibling(top, self_modules):
                    kind = "guarded_import" if node.lineno in guarded else "import"
                    out.append(Reach(top, node.lineno, kind))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                top = _top(node.module)
                if _is_sibling(top, self_modules):
                    kind = "guarded_import" if node.lineno in guarded else "import"
                    out.append(Reach(top, node.lineno, kind))
        elif isinstance(node, ast.Call):
            # --- pytest.importorskip("stapel_x") ---------------------------
            found = _importorskip_arg(node)
            if found:
                top = _top(found[0])
                if _is_sibling(top, self_modules):
                    out.append(Reach(top, found[1], "importorskip"))
            # --- requires("stapel_x") / installed("stapel_x") ---------------
            # The sanctioned seam (`tests/siblings.py`): it names the module as
            # a STRING, so an AST that only reads imports would call a suite
            # that uses it correctly an unused extra.
            called = _final_name(node.func)
            if called in SIBLING_SEAM_CALLS:
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        top = _top(arg.value)
                        if _is_sibling(top, self_modules):
                            out.append(Reach(top, node.lineno, "requires"))
            # --- override_settings(INSTALLED_APPS=[...]) -------------------
            for keyword in node.keywords:
                if keyword.arg != "INSTALLED_APPS":
                    continue
                for value, line in _string_constants(keyword.value):
                    top = _top(value)
                    if _is_sibling(top, self_modules):
                        out.append(Reach(top, line, "installed_apps"))
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            # --- INSTALLED_APPS = [...] / settings.INSTALLED_APPS = [...] --
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            labels = {
                t.attr if isinstance(t, ast.Attribute) else (t.id if isinstance(t, ast.Name) else "")
                for t in targets
            }
            if "INSTALLED_APPS" in labels and node.value is not None:
                for value, line in _string_constants(node.value):
                    top = _top(value)
                    if _is_sibling(top, self_modules):
                        out.append(Reach(top, line, "installed_apps"))

    out.sort(key=lambda r: (r.line, r.module, r.kind))
    return out


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------

_MSG_001 = (
    "the suite imports sibling `{module}` ({dist}), declared neither in "
    "[project].dependencies nor in the `test` extra of pyproject.toml — a "
    "runner installs only what is declared and errors at test setup. Add "
    "{dist} to [project.optional-dependencies].test, or stop importing it"
)
_MSG_002 = (
    "INSTALLED_APPS names `{module}` ({dist}), declared nowhere in "
    "pyproject.toml — Django reloads the app registry on setting_changed and "
    "IMPORTS the label for real, so a settings string is an import with no "
    "import token in it (stapel-core 0.44.0, verbatim)"
)
_MSG_003 = (
    "{guard}(\"{module}\") on undeclared sibling {dist} — this never "
    "fails, and on a runner without the package it never runs either, so the "
    "agreement it asserts is asserted nowhere. Declare {dist} in the `test` "
    "extra and let the skip mean \"a laptop without the extra\""
)
_MSG_004 = (
    "declared sibling {dist} is reached behind a skip guard, and no workflow "
    "in this repo sets {env}=1 — on CI the `test` extra IS installed, so a "
    "skip there means the install step did not do what the workflow says, and "
    "the run stays green having asserted nothing"
)
_MSG_005 = (
    "the `test` extra declares {dist}, which no suite file imports or names — "
    "an extra that stops describing the suite stops being read. Remove it, or "
    "write the test that needs it"
)
_MSG_006 = (
    "docs/errors.json publishes `{code}`, owned by `{owner}` — a foreign "
    "module's key. The codegen reads the error registry of the interpreter it "
    "runs in, so a workspace virtualenv holding every sibling emits keys this "
    "package does not own while CI emits fewer, and the committed artifact "
    "describes the machine instead of the module. Regenerate it in an "
    "environment holding only this package's declared dependencies "
    "(`stapel_core` is the one foreign owner that belongs here)"
)


# ---------------------------------------------------------------------------
# noqa
# ---------------------------------------------------------------------------


def _noqa_rules(line: str) -> Optional[set]:
    if "# noqa" not in line:
        return None
    if "# noqa:" not in line:
        return set()
    tail = line.split("# noqa:", 1)[1]
    rules = set()
    for chunk in tail.replace(";", ",").split(","):
        token = chunk.strip().split()[:1]
        if token:
            rules.add(token[0])
    return rules


def _suppress(violations: list, source_of: dict) -> list:
    kept = []
    for violation in violations:
        lines = source_of.get(violation.path) or []
        text = lines[violation.line - 1] if 0 < violation.line <= len(lines) else ""
        suppressed = _noqa_rules(text)
        if suppressed is not None and (not suppressed or violation.rule in suppressed):
            continue
        kept.append(violation)
    return kept


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


#: The one foreign owner a module's error catalogue may carry: everything in
#: the fleet stands on core and re-publishes its shared vocabulary on purpose.
SHARED_ERROR_OWNER = "stapel_core"


def _errors_json_violations(project: Path, self_modules: set) -> list:
    """SIB006 — foreign-owned keys in the committed ``docs/errors.json``."""
    path = project / "docs" / "errors.json"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
        entries = json.loads(text)
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    if isinstance(entries, dict):
        entries = list(entries.values())
    if not isinstance(entries, list):
        return []

    allowed = set(self_modules) | {SHARED_ERROR_OWNER}
    lines = text.splitlines()
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        owner = entry.get("owner")
        code = entry.get("code") or ""
        if not owner or owner in allowed:
            continue
        line = 1
        for index, raw in enumerate(lines, start=1):
            if code and f'"{code}"' in raw:
                line = index
                break
        out.append(Violation(
            str(path), line, "SIB006", _MSG_006.format(code=code or "?", owner=owner),
        ))
    return out


def lint_project(project: Path, notes: Optional[list] = None) -> list:
    """Lint one repository. Returns violations sorted by (path, line, rule)."""
    project = Path(project).resolve()
    declaration = read_declaration(project)
    if declaration is None:
        if notes is not None:
            notes.append(
                "stapel-sibling-lint: no pyproject.toml [project] table — nothing "
                "declares dependencies here, so SIB001-005 do not run"
            )
        return []

    self_modules = declaration.self_modules()
    declared = declaration.declared
    strict = strict_siblings_declared(project)

    violations: list = []
    source_of: dict = {}
    used_dists: set = set()
    scanned = 0

    for path in suite_files(project):
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            continue
        scanned += 1
        source_of[str(path)] = src.splitlines()

        for reach in reaches(tree, self_modules):
            dist = dist_of(reach.module)
            used_dists.add(dist)
            known = dist in declared
            if not known:
                if reach.kind == "installed_apps":
                    rule, message = "SIB002", _MSG_002.format(module=reach.module, dist=dist)
                elif reach.kind in ("importorskip", "requires"):
                    guard = "importorskip" if reach.kind == "importorskip" else "requires"
                    rule, message = "SIB003", _MSG_003.format(
                        guard=guard, module=reach.module, dist=dist,
                    )
                else:
                    rule, message = "SIB001", _MSG_001.format(module=reach.module, dist=dist)
                violations.append(Violation(str(path), reach.line, rule, message))
            elif reach.kind in ("importorskip", "guarded_import") and not strict:
                violations.append(Violation(
                    str(path), reach.line, "SIB004",
                    _MSG_004.format(dist=dist, env=STRICT_ENV),
                    level="warning",
                ))

    # SIB005 — the extra as a description of the suite, checked backwards.
    if declaration.path is not None:
        pyproject_lines = declaration.path.read_text(encoding="utf-8").splitlines()
        source_of[str(declaration.path)] = pyproject_lines
        for dist in sorted(declaration.test_extra):
            if not dist.startswith("stapel-"):
                continue  # pytest & friends are the harness, not a sibling
            if dist in used_dists or dist in declaration.runtime:
                continue
            spec = declaration.test_specs.get(dist, dist)
            line = 1
            for index, text in enumerate(pyproject_lines, start=1):
                if spec in text or dist in text:
                    line = index
                    break
            violations.append(Violation(
                str(declaration.path), line, "SIB005",
                _MSG_005.format(dist=dist), level="warning",
            ))

    # SIB006 — the committed error catalogue describes the module, not the venv.
    for violation in _errors_json_violations(project, self_modules):
        source_of.setdefault(
            violation.path, Path(violation.path).read_text(encoding="utf-8").splitlines()
        )
        violations.append(violation)

    violations = _suppress(violations, source_of)
    violations.sort(key=lambda v: (v.path, v.line, v.rule))

    if notes is not None:
        notes.append(f"stapel-sibling-lint: {scanned} suite file(s) scanned")
        notes.append(
            f"stapel-sibling-lint: {STRICT_ENV}=1 "
            + ("is set by a workflow in this repo" if strict else "is set by no workflow here")
        )
    return violations


def lint_paths(paths: Iterable) -> list:
    violations: list = []
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
        prog="stapel-sibling-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths", nargs="*", default=["."],
        help="Repository roots to lint (each needs its own pyproject.toml; default: .)",
    )
    parser.add_argument("--json", action="store_true", help="Machine output")
    parser.add_argument(
        "--strict", action="store_true",
        help="Promote SIB004/SIB005 (warnings) to errors",
    )
    args = parser.parse_args(argv)

    notes: list = []
    violations: list = []
    for raw in args.paths:
        root = Path(raw)
        if not root.exists():
            print(f"Error: path does not exist: {root}", file=sys.stderr)
            return 2
        violations.extend(lint_project(root, notes=notes))
    violations.sort(key=lambda v: (v.path, v.line, v.rule))

    errors = [
        v for v in violations
        if v.level == "error" or (args.strict and v.level == "warning")
    ]
    warnings = [v for v in violations if v not in errors]

    if args.json:
        print(json.dumps(
            {
                "ok": not errors,
                "errors": len(errors),
                "warnings": len(warnings),
                "notes": notes,
                "violations": [v.to_dict() for v in violations],
            },
            indent=2, sort_keys=True, ensure_ascii=False,
        ))
    else:
        for violation in violations:
            print(violation)
        for note in notes:
            print(note)
        if violations:
            print(f"\n{len(errors)} error(s), {len(warnings)} warning(s) found.")
        else:
            print("No violations found.")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
