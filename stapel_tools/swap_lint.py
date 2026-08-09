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

SWAP003  (error) A **hardcoded dotted-path literal naming a symbol in another
         top-level package, resolved at runtime**: ``import_string("stapel_
         profiles.validators.validate_display_name")``, ``importlib.
         import_module("other_pkg.thing")``, ``apps.get_model("other_app",
         "Model")``, ``apps.is_installed("other_app")``, ``find_spec
         ("other_pkg")``, or ``getattr(other_pkg_module, "symbol")``.
         Prototype: ``stapel-workspaces 0.19.0``, which asked Django's app
         registry whether ``stapel_profiles`` ran in this process and then
         resolved ``stapel_profiles.validators.validate_display_name`` by
         string. It worked in a monolith and answered a permanent 503 in a
         split deployment, because **there is no remote form of a symbol
         resolution**. An audit found it was the only one in the fleet: the
         anomaly, not the pattern — everything else goes through comm
         Functions, which are topology-independent by construction.
         Suppress with ``# noqa: SWAP003``.

SWAP004  (error) A direct import of a **vendor SDK that a fleet library owns
         the integration for**, from anywhere outside that library. The
         table is tiny and explicit (``_VENDOR_SDK_OWNERS``): today
         ``livekit`` belongs to ``stapel_video``.

         Prototype: a product that carried its own copy of the LiveKit
         provider next to the library's. It was not a bad copy — it was
         AHEAD of the library on two capabilities. That is the whole
         mechanism: a fork of a provider layer never starts as a fork, it
         starts as one call the library did not have yet, added where the
         engineer was standing. Every capability added there is a capability
         no other consumer ever gets, and the day the library fixes
         something (a rename that reaches a live call), the product with the
         fork cannot receive the fix at all.

         The rule is what makes "upstream it" a mechanism instead of a
         request. With it, the next engineer who needs a new vendor call
         physically cannot add it in the product, so it lands in the library
         and every consumer gets it. Without it the whole arrangement is a
         prose obligation, which is the defect class this file exists for.

         Not a dependency ban: a product may still depend on the SDK
         (transitively it does), run it in a worker, or vendor an unrelated
         piece. What it may not do is *import* it — the one act that puts a
         provider call in product code. Suppress a deliberate exception with
         ``# noqa: SWAP004``.

The line SWAP003 draws (this is the whole design)
--------------------------------------------------
A dotted path is the fleet's *extension mechanism*, so the rule must not
outlaw it — it outlaws exactly one shape of it. The discriminator is where
the value comes from, and it is checkable at the call site with no index and
no configuration:

**Legitimate — the value is not a literal here.** It arrived from
configuration and points at an extension point the module itself declares;
the host chose the target. ``import_string(settings.STAPEL_RECORDINGS
["STORAGE"])``, ``import_string(getattr(settings, "NORMALIZER", DEFAULT))``,
``import_string(self._resolver_path)``, a GDPR provider registry, a
merge-registry keyed by kind, and every ``get_model(KEY, default="...")``
swap seam SWAP001 already guards. None of these hand a *string constant* to
the resolver, so none of them are ever seen by this rule. A dotted-path
literal sitting in a settings default, a ``DEFAULTS`` dict or a
``get_model`` default is data waiting for a host to override, not a
resolution, and is likewise invisible.

**Forbidden — the value is a literal, and its top-level package is not
ours.** Nobody chose it but the author, so it is a hidden import across a
module boundary with none of an import's honesty: no dependency declaration,
no version constraint, no failure until runtime, and no remote form at all.
The fix is a comm Function (topology-independent), a plain ``import`` (if
the dependency is real, declare it), or a declared extension point whose
value the host supplies.

"Ours" is derived, never configured, and it is the union of two answers to
"could this reasonably be absent from my process?":

* every top-level package in the scanned tree (a directory chain of
  ``__init__.py``), every Django ``AppConfig`` label/name declared in it, and
  the distribution name from ``pyproject.toml``. A same-package literal — a
  module naming its own symbol — is silent, which is what "only to your own
  overridable entities" means;
* every package this distribution DECLARES it depends on
  (``[project.dependencies]``, any extra, any ``requirements*.txt`` in the
  tree), plus the standard library and ``django`` itself. The entire charge
  SWAP003 makes is *hidden* import — no dependency declaration, no version
  constraint, no failure until runtime. A pinned dependency is none of those;
  it is installed by the same resolver that installed you. What stays
  forbidden is the UNDECLARED reach, which is exactly what a fleet peer is:
  ``stapel-workspaces``' manifest declares ``stapel-core`` and nothing else,
  so ``stapel_profiles`` is foreign to it by its own reckoning.

There is no allowlist and no configuration key. Both halves are read off
files that already have to be right for the code to install at all.

Scope and false-positive posture
---------------------------------
The first three rules are deliberately conservative about *what* counts as a
swappable/DTO name — they only ever flag names this scan can trace back to a
``get_model``/``get_presenter`` default or a ``dto.py`` import, never a
bare heuristic on class naming. An unresolvable import (plain ``import pkg.mod``
with attribute-qualified access, rather than ``from pkg.mod import Name``) is
not flagged — the ambiguity is resolved towards *not* flagging, the opposite
default from ``url_lint``'s URL001, because a false SWAP001/SWAP002 here
blocks a legitimate presenter/model definition file, not just a width choice.

SWAP003 inherits the same posture, and its exclusions are the ones a run
across the whole fleet actually turned up — 34 raw hits triaged down to 3,
each exclusion paid for by a class of hit that was not a defect:

* ``tests/`` and ``test_*.py`` — fixtures deliberately resolve fake and
  foreign paths; ``migrations/`` — a migration names other apps by design;
  ``.vendor/``/``vendored/`` — whole sibling repos checked in (4 hits,
  ``stapel-studio``), where "ours" is computed for the wrong distribution.
* ``getattr(mod, "__dunder__")`` — a version probe on a statically imported
  optional dependency (11 hits, ``ironmemo-backend``). A dunder is the module
  protocol, present on every module, never a private symbol of another
  package.
* ``django`` and ``django.contrib.*`` (5 hits, ``stapel-core`` and
  ``stapel-recordings``): ``apps.is_installed("django.contrib.admin")`` asks
  whether the HOST turned admin on. Configuration, not topology.
* Anything the manifest pins (``pyvips`` behind ``stapel-cdn``'s ``images``
  extra, ``stapel_core.django.taskstore`` from ``stapel-recordings``,
  ``meeteval==0.4.3`` from ``ironmemo-backend``) — see above.

Two classes need no exclusion because the design already makes them
invisible, and both are asserted in the tests so they stay that way: a dotted
path inside a **code template** (stapel-tools generates these strings for
scaffolded projects) is a string constant nobody hands to a resolver, and
Django's own settings strings (``AUTH_USER_MODEL``, ``MIDDLEWARE``,
``DEFAULT_AUTO_FIELD``, ``AUTHENTICATION_BACKENDS``, ``STORAGES``) are
assignments, not calls.

Two things SWAP003 deliberately does NOT do. It is not a dataflow engine: it
folds string constants and exactly ONE level of local helper (the shape the
incident had — ``profiles_in_process(dotted_path)`` wrapping the probe and
the ``import_string`` behind one honest name, with every literal at the call
sites). Two levels of indirection walk past it, and the answer to that is
review, not a solver. And it never reads a *value* that is not a literal
here: a variable assigned from settings, a dict lookup, a parameter — all
silent, always, because that is the legitimate half of the design and not a
gap in it.

Exit codes: 0 clean, 1 errors present, 2 usage/environment errors.
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
    ".vendor",
    "vendored",
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
# SWAP003 — a hardcoded dotted path into somebody else's package
# ---------------------------------------------------------------------------

#: Callables that turn a string into a symbol. ``import_string`` is Django's
#: (``django.utils.module_loading``) and the shape stapel-core's own swap
#: accessors use internally; ``import_module`` is importlib's. Matched by the
#: trailing name, so ``module_loading.import_string`` and a bare
#: ``import_string`` both count.
_SYMBOL_RESOLVERS = {"import_string", "import_module"}

#: Callables that ask whether another package is in THIS process. Not symbol
#: resolution, but the same defect: the answer is a property of the topology,
#: the caller hardcodes whose presence it is asking about, and there is no
#: remote form of the question either. This is the literal first half of the
#: stapel-workspaces incident.
_PRESENCE_PROBES = {"is_installed", "find_spec"}

#: Django app-registry accessors, only ever matched as ``<registry>.<name>``
#: so stapel-core's own ``get_model(KEY, default=...)`` swap accessor — a
#: bare name, and SWAP001's subject — is never mistaken for one.
_APP_REGISTRY_ACCESSORS = {"get_model", "get_app_config", "is_installed"}

#: Names an app registry is bound to by convention across the fleet.
_APP_REGISTRY_NAMES = {"apps", "django_apps", "_django_apps", "global_apps"}

#: Never foreign, whatever the tree declares. The standard library ships with
#: the interpreter and ``django`` is the framework the code executes inside:
#: both are in the process by construction (the file importing them says so at
#: module level), neither can be in "another process", and neither has a remote
#: form to miss. ``apps.is_installed("django.contrib.admin")`` in particular is
#: a question about the HOST'S CONFIGURATION — did the host turn admin on —
#: which is the legitimate side of this rule's line, not a topology probe.
#: A scope statement, not an escape hatch.
_ALWAYS_LOCAL = frozenset(sys.stdlib_module_names) | {"django"}

#: Attribute names exempt from the ``getattr``-on-a-module check: the module
#: protocol, guaranteed present on every module, never a private symbol of
#: another package. ``getattr(torch, "__version__", "unknown")`` is a version
#: probe on a statically imported optional dependency — eleven of the first
#: thirty-four fleet hits were exactly this, and none of them were defects.
_MODULE_DUNDERS_ARE_EXEMPT = True

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Directories that are not packages of this distribution but whose names a
#: dotted literal may legitimately carry (a test helper module, a conftest).
_ALWAYS_OURS = {"tests", "test", "conftest"}

_PKG_SCAN_SKIP = SKIP_DIRS - {"tests", "migrations"}


def _canon_token(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name.strip().lower())


def own_package_names(project: Path) -> set[str]:
    """Top-level names this distribution may resolve into without lying.

    Derived, never configured, from three sources that all describe the same
    thing from different angles: every top-level Python package in the tree
    (a directory chain of ``__init__.py`` whose parent is not itself a
    package), every Django ``AppConfig``'s ``name``/``label``, and the
    ``pyproject.toml`` distribution name. A repo that ships several packages
    (a service with a handful of apps) owns all of them, and a monorepo of
    modules owns each — the union is deliberately generous, because a false
    SWAP003 lands on a legitimate in-package resolution.
    """
    names: set[str] = set(_ALWAYS_OURS)

    for dirpath, dirnames, filenames in os.walk(project):
        dirnames[:] = sorted(
            d for d in dirnames if d not in _PKG_SCAN_SKIP and not d.endswith(".egg-info")
        )
        if "__init__.py" not in filenames:
            continue
        top = Path(dirpath)
        while (
            top != project
            and top.parent != top
            and (top.parent / "__init__.py").is_file()
        ):
            top = top.parent
        names.add(_canon_token(top.name))

    for py in _walk_py(project):
        if py.name not in ("apps.py", "settings.py") and "settings" not in py.name:
            continue
        tree, _ = _read(py)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(b == "AppConfig" for b in _class_base_names(node)):
                continue
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign):
                    continue
                if not isinstance(stmt.value, ast.Constant):
                    continue
                if not isinstance(stmt.value.value, str):
                    continue
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id in ("name", "label"):
                        names.add(_canon_token(stmt.value.value.split(".")[0]))

    pyproject = project / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        text = ""
    match = re.search(r'(?m)^\s*name\s*=\s*["\']([^"\']+)["\']', text)
    if match:
        names.add(_canon_token(match.group(1)))

    return {n for n in names if n}


def _class_base_names(node: ast.ClassDef) -> list[str]:
    out = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            out.append(base.id)
        elif isinstance(base, ast.Attribute):
            out.append(base.attr)
    return out


def _manifest_files(project: Path):
    """One ``([pyproject.toml...], [requirements*.txt...])`` pair for the tree.

    Walked, not globbed at the root: a monorepo backend
    (``<repo>/backend/pyproject.toml``) and a repo whose requirements live one
    level down (``ironmemo-backend/iron-benchmark/requirements.txt``, which is
    where its pinned ``meeteval==0.4.3`` is declared) are both normal, and a
    root-only glob reads them as declaring nothing at all — i.e. as reaching
    into a package they in fact pin.
    """
    pyprojects: list[Path] = []
    requirements: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(project):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")
        )
        for fname in filenames:
            if fname == "pyproject.toml":
                pyprojects.append(Path(dirpath) / fname)
            elif fname.startswith("requirements") and fname.endswith(".txt"):
                requirements.append(Path(dirpath) / fname)
    yield sorted(pyprojects), sorted(requirements)


def declared_dependencies(project: Path) -> set[str]:
    """Top-level names this distribution DECLARES it depends on.

    The whole indictment SWAP003 makes is "hidden import": no dependency
    declaration, no version constraint, no failure until runtime. A package
    named in ``[project.dependencies]``, in an extra, or in a
    ``requirements*.txt`` is none of those — it is pinned, it is installed by
    the same resolver that installed this distribution, and it is therefore in
    this process whenever this code runs. What stays forbidden is the
    UNDECLARED reach, which is exactly what a fleet peer is and exactly what
    the ``stapel-workspaces`` incident was: its ``pyproject.toml`` declares
    ``stapel-core`` and nothing else, so ``stapel_profiles`` is foreign to it
    by its own manifest.

    Distribution names, not import names — plus whatever
    ``importlib.metadata`` can tell us about the two differing for packages
    installed in the linting environment (``djangorestframework`` ->
    ``rest_framework``). Derived from files in the tree; there is no list to
    maintain here, and no configuration key to widen.
    """
    names: set[str] = set()
    raw: set[str] = set()

    for pyproject, requirements in _manifest_files(project):
        for path in pyproject:
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
                continue
            proj = data.get("project", {}) if isinstance(data, dict) else {}
            specs = list(proj.get("dependencies") or [])
            for extra in (proj.get("optional-dependencies") or {}).values():
                specs.extend(extra or [])
            raw.update(str(spec) for spec in specs)
        for path in requirements:
            try:
                raw.update(path.read_text(encoding="utf-8").splitlines())
            except (OSError, UnicodeDecodeError):
                continue

    for spec in raw:
        spec = spec.split("#", 1)[0].strip()
        if not spec or spec.startswith("-"):
            continue
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
        if match:
            names.add(_canon_token(match.group(1)))

    try:
        from importlib.metadata import packages_distributions

        for import_name, dists in packages_distributions().items():
            if any(_canon_token(d) in names for d in dists):
                names.add(_canon_token(import_name))
    except Exception:  # pragma: no cover - environment-dependent, never fatal
        pass

    return names


def _is_foreign(token: str, ours: set[str]) -> bool:
    token = _canon_token(token)
    if not token or not _IDENT_RE.match(token):
        return False
    if token in ours:
        return False
    if token in _ALWAYS_LOCAL:
        return False
    return True


def _module_objects(tree: ast.Module) -> dict[str, str]:
    """Local name -> top-level package, for names bound to a MODULE object.

    Only plain ``import`` statements: those always bind a module. ``from x
    import y`` is skipped on purpose — ``y`` may be a module or a symbol, and
    the ambiguity resolves towards not flagging.
    """
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            top = alias.name.split(".")[0]
            bound[alias.asname or top] = top
    return bound


def _attr_root(node: ast.expr) -> Optional[str]:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _literal(node: Optional[ast.expr]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """Names this file binds to a string constant and never to anything else.

    ``_PROFILES_APP_LABEL = "stapel_profiles"`` then ``apps.is_installed
    (_PROFILES_APP_LABEL)`` is the same hardcoding as the inline literal — the
    incident wrote it exactly that way, with a comment explaining that a label
    is not an import. A name that is *ever* assigned a non-constant (a
    ``getattr(settings, ...)``, a parameter, a computed path) is dropped
    outright: that is precisely the settings-sourced case the rule must not
    see, and dropping the whole name keeps the two from ever being confused.
    """
    constants: dict[str, str] = {}
    poisoned: set[str] = set()
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        value: Optional[ast.expr] = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign) and node.target is not None:
            targets, value = [node.target], node.value
        elif isinstance(node, (ast.AugAssign, ast.NamedExpr)):
            targets, value = [node.target], node.value
        elif isinstance(node, ast.For):
            targets, value = [node.target], None
        else:
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            literal = _literal(value)
            if literal is None or (target.id in constants and constants[target.id] != literal):
                poisoned.add(target.id)
            else:
                constants[target.id] = literal
    for func in ast.walk(tree):
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in [*func.args.args, *func.args.posonlyargs, *func.args.kwonlyargs]:
                poisoned.add(arg.arg)
    return {k: v for k, v in constants.items() if k not in poisoned}


def _is_resolver_call(node: ast.Call) -> bool:
    name = _func_name(node)
    if name in _SYMBOL_RESOLVERS or name in _PRESENCE_PROBES:
        return True
    return (
        isinstance(node.func, ast.Attribute)
        and name in _APP_REGISTRY_ACCESSORS
        and _attr_root(node.func.value) in _APP_REGISTRY_NAMES
    )


def _forwarders(tree: ast.Module) -> dict[str, int]:
    """Module-level ``def``s that hand a parameter straight to a resolver.

    The incident's own shape: ``profiles_in_process(dotted_path)`` wraps
    ``apps.is_installed(...)`` plus ``import_string(dotted_path)`` behind one
    honest, well-documented name, and every hardcoded path then sits at a
    *call* to that helper. A rule that only reads resolver arguments reads the
    parameter, finds a variable, and clears the file that contains the defect —
    which is how this exact rule died in draft.

    Exactly ONE level, module-level ``def``s only, same file. This is not a
    dataflow engine; it is the observation that a seam this deliberate gets a
    name, and the name is where the literals gather.
    """
    forwarders: dict[str, int] = {}
    for func in tree.body:
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = [a.arg for a in [*func.args.posonlyargs, *func.args.args]]
        if not params:
            continue
        for node in ast.walk(func):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if not _is_resolver_call(node):
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Name) and arg.id in params:
                forwarders[func.name] = params.index(arg.id)
                break
    return forwarders


def _swap003_hits(tree: ast.Module, ours: set[str]) -> list[tuple[int, str, str]]:
    """(lineno, foreign dotted/label literal, what-kind-of-call) triples."""
    module_objects = _module_objects(tree)
    constants = _string_constants(tree)
    forwarders = _forwarders(tree)
    hits: list[tuple[int, str, str]] = []

    def arg_value(node: Optional[ast.expr]) -> Optional[str]:
        if node is None:
            return None
        literal = _literal(node)
        if literal is not None:
            return literal
        if isinstance(node, ast.Name):
            return constants.get(node.id)
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _func_name(node)
        first = arg_value(node.args[0]) if node.args else None

        # a local helper that forwards its argument to one of the resolvers
        if isinstance(node.func, ast.Name) and node.func.id in forwarders:
            index = forwarders[node.func.id]
            forwarded = arg_value(node.args[index]) if len(node.args) > index else None
            if forwarded and not forwarded.startswith("."):
                if _is_foreign(forwarded.split(".")[0], ours):
                    hits.append((node.lineno, forwarded, f"{node.func.id}()"))
            continue

        # apps.get_model("other_app", "Model") / apps.is_installed("other_app")
        if (
            isinstance(node.func, ast.Attribute)
            and name in _APP_REGISTRY_ACCESSORS
            and _attr_root(node.func.value) in _APP_REGISTRY_NAMES
            and first
        ):
            label = first.split(".")[0]
            if _is_foreign(label, ours):
                hits.append((node.lineno, first, "the Django app registry"))
            continue

        # import_string("other_pkg.mod.symbol") / import_module("other_pkg.mod")
        if name in _SYMBOL_RESOLVERS and first and not first.startswith("."):
            if _is_foreign(first.split(".")[0], ours):
                hits.append((node.lineno, first, f"{name}()"))
            continue

        # find_spec("other_pkg") / <registry>.is_installed handled above
        if name in _PRESENCE_PROBES and first and not first.startswith("."):
            if _is_foreign(first.split(".")[0], ours):
                hits.append((node.lineno, first, f"{name}()"))
            continue

        # getattr(other_pkg_module, "symbol")
        if name == "getattr" and isinstance(node.func, ast.Name) and len(node.args) >= 2:
            attr = _literal(node.args[1])
            root = _attr_root(node.args[0])
            top = module_objects.get(root or "")
            if attr and attr.startswith("__") and _MODULE_DUNDERS_ARE_EXEMPT:
                continue
            if attr and top and _is_foreign(top, ours):
                hits.append((node.lineno, f"{top}.{attr}", "getattr() on a module object"))
            continue

    return hits


def find_swap003(project: Path, ours: Optional[set[str]] = None) -> list[Violation]:
    if ours is None:
        ours = own_package_names(project) | declared_dependencies(project)
    if not ours - _ALWAYS_OURS:
        # Nothing in this tree is a package we can call "ours" — every literal
        # would read as foreign. Stay silent rather than flag a whole repo.
        return []

    violations: list[Violation] = []
    for py in _walk_py(project):
        tree, lines = _read(py)
        if tree is None:
            continue
        for lineno, dotted, how in _swap003_hits(tree, ours):
            raw = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
            suppressed = _noqa_rules(raw)
            if suppressed is not None and (not suppressed or "SWAP003" in suppressed):
                continue
            violations.append(Violation(
                str(py), lineno, "SWAP003",
                f"hardcoded dotted path '{dotted}' resolved through {how} — "
                f"'{dotted.split('.')[0]}' is another top-level package, so this "
                f"is a hidden import across a module boundary: no dependency "
                f"declaration, no version constraint, no failure until runtime, "
                f"and no remote form at all (it answers 503 the moment the two "
                f"run in different processes). Call a comm Function, import the "
                f"package properly if the dependency is real, or take the path "
                f"from configuration (a settings-sourced value is not a literal "
                f"here and is never flagged)",
            ))
    violations.sort(key=lambda v: (v.path, v.line))
    return violations


# ---------------------------------------------------------------------------
# SWAP004 — a vendor SDK imported outside the library that owns it
# ---------------------------------------------------------------------------

#: Top-level import name of a vendor SDK -> the fleet package that owns the
#: integration with it. Deliberately a table rather than a heuristic: each row
#: is a decision that a library now carries this vendor for the whole fleet,
#: and is added the day that library ships the capability, not before. Keys are
#: matched on the TOP-LEVEL package only (``livekit.api`` is ``livekit``).
_VENDOR_SDK_OWNERS = {
    # stapel-video owns the video-provider seam: token minting, the live
    # rename/kick pair, room metadata, the health probe and recording egress.
    # A product reaching for livekit.api directly is re-growing the fork the
    # seam replaced.
    "livekit": "stapel_video",
}


def find_swap004(project: Path, ours: Optional[set] = None) -> list:
    """Vendor-SDK imports outside the fleet library that owns the vendor."""
    if ours is None:
        ours = own_package_names(project)
    violations: list = []
    for py in _walk_py(project):
        tree, lines = _read(py)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # A relative import can never name a foreign top-level package.
                if node.level or not node.module:
                    continue
                imported = [node.module]
            else:
                continue
            for dotted in imported:
                top = _canon_token(dotted.split(".")[0])
                owner = _VENDOR_SDK_OWNERS.get(top)
                if owner is None:
                    continue
                if _canon_token(owner) in ours:
                    continue  # this IS the library that owns the vendor
                line = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
                suppressed = _noqa_rules(line)
                if suppressed is not None and (
                    not suppressed or "SWAP004" in suppressed
                ):
                    continue
                violations.append(Violation(
                    str(py), node.lineno, "SWAP004",
                    f"`{dotted}` is a vendor SDK owned by `{owner}`: import it "
                    f"there, not here. A provider call added where you are "
                    f"standing is a capability no other consumer of `{owner}` "
                    f"ever gets — and it is how this product stops being able "
                    f"to receive fixes to that provider at all. Add the "
                    f"capability to `{owner}`'s provider contract and call it "
                    f"through the seam",
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
    violations.extend(find_swap003(project))
    violations.extend(find_swap004(project))
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
