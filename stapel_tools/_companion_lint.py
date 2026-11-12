"""CFG011 — companion registrations in a project's settings (static).

A companion brick (``STAPEL_LIBS[key]["companion_of"]``) plugs into its target
only through the settings fragment it ships, ``<package>.preset.
SETTINGS_DEFAULTS``. The generated settings merge that fragment
(``_wiring.preset_modules``); a hand-wired host pastes it. Two defects are
silent at runtime until the first checkout:

* the companion and its target are both installed and nothing registers the
  companion: no ``"<package>.preset"`` merge, no import of the preset, and
  not every Function its fragment registers is spelled in the settings;
* a registration names a Function no installed app provides (a pasted
  ``delivery.quote`` with stapel-delivery not installed, a typo).

A registration is a string under one of :data:`FUNCTION_KEYS` inside a
``STAPEL_<MOD>`` dict, in the settings files or in a preset fragment the
settings merge or import. Providers are the ``function("…")`` /
``register_function("…", …)`` calls in the installed apps' sources.

What it cannot see: a Function registered under another key, a settings value
built at runtime (a call, a comprehension), a provider whose source is not
locatable (reported as a note, never guessed), and a provider app installed
through ``COMMON_INSTALLED_APPS`` (stapel-core's own apps are scanned
always).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

#: Keys whose string value names a comm Function in a registration: the orders
#: checkout contract (ADJUSTERS ``quote``, CHECKOUT_HOOKS ``reserve`` /
#: ``commit`` / ``release``, PURCHASABLE_TYPES ``resolve``) and the bookable
#: seam (BOOKABLE_TYPES ``resolve``).
FUNCTION_KEYS = frozenset({"quote", "reserve", "commit", "release", "resolve"})

_PROVIDER_CALLS = frozenset({"function", "register_function"})
_SKIP_SCAN = frozenset({"tests", "migrations", "__pycache__", "node_modules", "build", "dist"})


@dataclass(frozen=True)
class Registration:
    function: str
    path: str
    line: int
    origin: str  # "settings" or "<package>.preset"


@dataclass(frozen=True)
class Problem:
    path: str
    line: int
    message: str


# ── settings ────────────────────────────────────────────────────────────────


def _parse(path: Path) -> Optional[ast.Module]:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def _assigned_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    if isinstance(node, (ast.AugAssign, ast.AnnAssign)) and isinstance(node.target, ast.Name):
        return node.target.id
    return None


def installed_apps(trees: dict[Path, ast.Module]) -> dict[str, tuple[str, int]]:
    """``app -> (path, line)`` of every string in an INSTALLED_APPS assignment."""
    found: dict[str, tuple[str, int]] = {}
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if _assigned_name(node) != "INSTALLED_APPS" or node.value is None:
                continue
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    found.setdefault(sub.value, (str(path), sub.lineno))
    return found


def preset_references(trees: dict[Path, ast.Module]) -> dict[str, tuple[str, int]]:
    """``package -> (path, line)`` for every preset the settings merge or import."""
    found: dict[str, tuple[str, int]] = {}
    for path, tree in trees.items():
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value.endswith(".preset"):
                names.append(node.value)
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.endswith(".preset"):
                    names.append(node.module)
                elif any(a.name == "preset" for a in node.names):
                    names.append(f"{node.module}.preset")
            elif isinstance(node, ast.Import):
                names.extend(a.name for a in node.names if a.name.endswith(".preset"))
            for name in names:
                found.setdefault(name.removesuffix(".preset"), (str(path), node.lineno))
    return found


def _registrations_in(node: ast.AST, path: str, origin: str) -> Iterable[Registration]:
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Dict):
            continue
        for key, value in zip(sub.keys, sub.values):
            if (isinstance(key, ast.Constant) and key.value in FUNCTION_KEYS
                    and isinstance(value, ast.Constant) and isinstance(value.value, str)):
                yield Registration(value.value, path, value.lineno, origin)


def settings_registrations(trees: dict[Path, ast.Module]) -> list[Registration]:
    out: list[Registration] = []
    for path, tree in trees.items():
        for node in ast.walk(tree):
            name = _assigned_name(node)
            if name and name.startswith("STAPEL_") and getattr(node, "value", None) is not None:
                out.extend(_registrations_in(node.value, str(path), "settings"))
    return out


# ── packages ────────────────────────────────────────────────────────────────


def _literal(node: ast.AST, env: dict) -> object:
    """A preset value: literals, names bound above, and ``**`` dict unpacking."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise ValueError(node.id)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_literal(e, env) for e in node.elts]
    if isinstance(node, ast.Dict):
        out: dict = {}
        for key, value in zip(node.keys, node.values):
            if key is None:
                out.update(_literal(value, env))
            else:
                out[_literal(key, env)] = _literal(value, env)
        return out
    raise ValueError(ast.dump(node))


def read_fragment(package_dir: Path) -> Optional[dict]:
    """``SETTINGS_DEFAULTS`` of ``<package_dir>/preset.py``, read without importing."""
    tree = _parse(package_dir / "preset.py")
    if tree is None:
        return None
    env: dict = {}
    for node in tree.body:
        name = _assigned_name(node)
        if name and getattr(node, "value", None) is not None:
            try:
                env[name] = _literal(node.value, env)
            except (ValueError, TypeError):
                continue
    value = env.get("SETTINGS_DEFAULTS")
    return value if isinstance(value, dict) else None


def fragment_functions(fragment: dict) -> list[str]:
    out: list[str] = []

    def walk(value):
        if isinstance(value, dict):
            for key, sub in value.items():
                if key in FUNCTION_KEYS and isinstance(sub, str):
                    out.append(sub)
                else:
                    walk(sub)

    walk(fragment)
    return out


def provided_functions(package_dir: Path) -> set[str]:
    names: set[str] = set()
    for path in sorted(package_dir.rglob("*.py")):
        if _SKIP_SCAN & set(path.relative_to(package_dir).parts):
            continue
        tree = _parse(path)
        if tree is None:
            continue
        consts = {
            n.targets[0].id: n.value.value for n in tree.body
            if isinstance(n, ast.Assign) and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            callee = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if callee not in _PROVIDER_CALLS:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                names.add(first.value)
            elif isinstance(first, ast.Name) and first.id in consts:
                names.add(consts[first.id])
    return names


# ── the rule ────────────────────────────────────────────────────────────────


def _service_root(path: Path) -> Path:
    """``<root>/config/settings/base.py`` and ``<root>/config/settings.py`` -> root."""
    parent = path.parent
    if parent.name == "settings":
        parent = parent.parent
    return parent.parent if parent.name == "config" else parent


def lint(project: Path, settings_files: list[Path], notes: list[str]) -> list[Problem]:
    """CFG011 per settings unit (one service of a fleet is one unit)."""
    groups: dict[Path, list[Path]] = {}
    for path in settings_files:
        groups.setdefault(_service_root(path), []).append(path)
    problems: list[Problem] = []
    for root, files in sorted(groups.items()):
        problems.extend(_lint_unit(project, root, files, notes))
    return problems


def _lint_unit(project: Path, unit: Path, settings_files: list[Path], notes: list[str]) -> list[Problem]:
    from .adoption_lint import locate_module_dir
    from .create_project import STAPEL_LIBS

    trees = {p: t for p in settings_files if (t := _parse(p)) is not None}
    apps = installed_apps(trees)
    if not apps:
        return []
    refs = preset_references(trees)
    roots = [project.parent, project]
    dirs: dict[str, Optional[Path]] = {}

    def local_dir(app: str) -> Optional[Path]:
        for base in (unit, project):
            cand = base / Path(*app.split("."))
            if cand.is_dir():
                return cand
        return None

    def package_dir(app: str) -> Optional[Path]:
        if app not in dirs:
            local = local_dir(app)
            if local is not None:
                dirs[app] = local
            elif app.startswith("stapel_") and "." not in app:
                dirs[app] = locate_module_dir(app, roots)
            else:
                dirs[app] = None
        return dirs[app]

    problems: list[Problem] = []
    strings = {s.function for s in settings_registrations(trees)}
    for key, info in STAPEL_LIBS.items():
        targets = [t for t in info.get("companion_of", ()) if STAPEL_LIBS[t]["dir"] in apps]
        package = info["dir"]
        if package not in apps or not targets or package in refs:
            continue
        where = package_dir(package)
        fragment = read_fragment(where) if where else None
        functions = fragment_functions(fragment) if fragment else []
        if functions and all(f in strings for f in functions):
            continue
        path, line = apps[package]
        target_names = ", ".join(STAPEL_LIBS[t]["dir"] for t in targets)
        problems.append(Problem(
            path, line,
            f"{package} is installed next to {target_names} but nothing registers "
            f"it: the settings neither merge nor import {package}.preset, so its "
            f"SETTINGS_DEFAULTS fragment never reaches "
            f"{', '.join('STAPEL_' + t.upper() for t in targets)} and checkout runs "
            f"without it. Regenerate the settings wiring (stapel-create-project "
            f"merges it) or merge {package}.preset.SETTINGS_DEFAULTS under your "
            f"own values"))

    registrations = settings_registrations(trees)
    for package, (path, line) in sorted(refs.items()):
        where = package_dir(package)
        fragment = read_fragment(where) if where else None
        if fragment is None:
            if package in apps or package.startswith("stapel_"):
                notes.append(f"stapel-config-lint: CFG011 could not read {package}.preset "
                             "(not locatable); its registrations are unverified")
            continue
        registrations.extend(
            Registration(f, path, line, f"{package}.preset") for f in fragment_functions(fragment))
    if not registrations:
        return problems

    provided: set[str] = set()
    unscanned: list[str] = []
    candidates = ["stapel_core", *apps]
    scan = list(dict.fromkeys(
        a.split(".")[0] if a.startswith("stapel_") else a
        for a in candidates if a.startswith("stapel_") or local_dir(a) is not None))
    for app in scan:
        where = package_dir(app)
        if where is None:
            if app != "stapel_core":
                unscanned.append(app)
            continue
        provided |= provided_functions(where)
    seen: set[tuple[str, str]] = set()
    for reg in registrations:
        if reg.function in provided or (reg.function, reg.origin) in seen:
            continue
        seen.add((reg.function, reg.origin))
        if unscanned:
            notes.append(
                f"stapel-config-lint: CFG011 cannot verify {reg.function!r} "
                f"({reg.origin}): sources of {', '.join(sorted(unscanned))} not locatable")
            continue
        problems.append(Problem(
            reg.path, reg.line,
            f"registration ({reg.origin}) names the comm Function {reg.function!r}, "
            f"which no installed app provides: the call fails at checkout. Install "
            f"the module that provides it, or correct the registration"))
    return problems
