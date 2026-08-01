"""
stapel-surface-lint — pre-merge gate against REINVENTING what the fleet ships.

Why this exists, and why it is a different linter from ``adoption_lint``
------------------------------------------------------------------------
``stapel-adoption-lint`` catches non-adoption **after the fact**: the module is
pinned, the module is installed, and its endpoints are simply not mounted. By
then the product has already lived without the mechanism — the finding arrives
as archaeology.

These rules move the moment. They read the ``surface`` section of the fleet's
``capabilities.json`` documents (``stapel_tools.surface`` — the *usage* surface:
what a product is meant to call, subclass, mount or read) and fail on the first
CI run of the branch that reinvents one. The cost of not looking drops from a
production incident to a single iteration.

Every rule here has a real incident behind it, and each was verified against
that incident rather than against an invented example:

SUR001 (error) **duplicate-of-surface.** The project defines a DRF permission
    class under a name an installed module already publishes as a
    ``permission_class``. Prototype: ``marketplace-common-python`` carries its
    own ``IsNotAnonymousUser``/``IsStaffUser``/``IsSuperUser``/``IsServiceRequest``/
    ``ReadOnlyOrStaff``/``ReadOnlyOrSuperUser`` — six classes, name for name,
    beside the six ``stapel-core`` already shipped.

SUR002 (error) **instead_of.** An installed module publishes a symbol that
    explicitly displaces an outside one (``IsNotAnonymousUser.instead_of =
    [rest_framework.permissions.IsAuthenticated]``); the project puts the
    displaced symbol in ``permission_classes`` and uses the replacement
    **nowhere at all**. One finding per displaced symbol, not per call site.

SUR003 (error) **imported-but-never-called.** A ``gate_function`` is imported
    into a module and the bound name never appears again — no call, no
    reference. Prototype: ``redaction_gate`` during the lab port, where the
    import survived and the call did not, so the protection became its own
    appearance.

SUR004 (error) **publisher-without-consumer.** A ``capability_field`` declares
    ``consumer: frontend``, and the consuming ``-react`` package never reads it
    outside its generated OpenAPI types. Prototype: ``email_mock`` — published
    so a host UI can render a dev-mode badge when OTP delivery is mocked,
    present in ``schema.json``, present in the generated ``schema.ts``, and read
    by exactly nothing, so the screen says "code sent" when nothing was sent.

Where the surface index comes from
----------------------------------
The same two sources ``stapel-catalog`` uses, unioned, installed first:
every INSTALLED ``stapel-*`` distribution that ships ``docs/capabilities.json``
in its wheel, plus every ``stapel-*/docs/capabilities.json`` under the search
roots (``--workspace``). Entries owned by the project **itself** are dropped —
a library defining its own surface is not duplicating it.

What these rules deliberately do NOT catch (see also the design's §4)
---------------------------------------------------------------------
* **Prevention before the code is written.** Outside the studio pipeline, code
  is written first and checked second. These rules buy one iteration, not
  clairvoyance.
* **A semantic duplicate with no structural signature.** SUR001 matches on the
  published *name*. A product that reinvents ``IsNotAnonymousUser`` under the
  name ``IsRealAccount`` is invisible to it, and an AST-similarity heuristic
  over permission bodies was measured against the fleet's real reimplementations
  (``IsInternalService``/``IsServiceAPIKey`` beside ``IsServiceRequest``) and
  matched none of them while promising noise — the honest answer for renamed
  reinventions is the index plus review, not a regex.
* **Predicates.** The genre the design calls out by name: a product feeling out
  a shape the core already had leaves no structural trace at all.
* **A gate imported and passed around instead of called.** SUR003 clears any
  reference to the bound name — a callback, a registry value, a
  ``functools.partial`` — because those are legitimate and indistinguishable
  from a deferred call. The failure it catches is the total one: bound and
  never mentioned again.
* **A consumer that reads the field and ignores it.** SUR004 proves the field
  name occurs in hand-written consumer code. It cannot prove the branch is
  right; only that the promise was not dropped on the floor.

Exit codes: 0 clean (warnings allowed), 1 errors present, 2 usage errors.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .adoption_lint import SKIP_DIRS, canon_dist, module_short

#: Directories inside a ``-react`` package whose content is machine-generated
#: from the backend's OpenAPI document. A capability field appearing ONLY here
#: is the exact shape of the ``email_mock`` incident: the type was generated,
#: the promise was typed, and nothing ever read it.
GENERATED_MARKERS = ("/generated/", "/dist/", "/node_modules/")
GENERATED_SUFFIXES = (".gen.ts", ".gen.tsx", ".gen.js", ".gen.json")
CONSUMER_SOURCE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx")

_PERMISSION_BASE = "BasePermission"


@dataclass
class Finding:
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


# ---------------------------------------------------------------------------
# the surface index
# ---------------------------------------------------------------------------


def _project_dist_name(project: Path) -> Optional[str]:
    """The distribution the project IS, from its own ``pyproject.toml``.

    Used to drop the project's own entries from the index: ``stapel-core``
    defining ``IsNotAnonymousUser`` is the publication, not a duplicate of it.
    """
    pyproject = project / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    name = (data.get("project") or {}).get("name")
    return canon_dist(name) if isinstance(name, str) else None


def load_surface_index(
    project: Path,
    *,
    search_roots: Optional[list[Path]] = None,
    use_installed: bool = True,
    warn=lambda msg: None,
) -> list[dict]:
    """Every ``surface`` entry visible to *project*, each tagged with its
    owning ``module``/``version`` and with ``own`` — whether the project IS the
    publishing module.

    Sources are unioned exactly as ``stapel-catalog`` unions them — the
    environment first (a document that ships in the wheel describes what will
    actually run), then the workspace checkouts — and deduped on module name so
    an editable install cannot double a module. The project's own document is
    kept, flagged: ``stapel-core`` defining ``IsNotAnonymousUser`` is the
    publication and must never be read as a duplicate of itself, but
    ``stapel-auth`` declaring ``consumer: frontend`` is a promise the project
    itself owes.
    """
    from . import catalog

    sources: list[Path] = []
    if use_installed:
        sources.extend(catalog.discover_installed(warn=warn))
    for root in search_roots or []:
        sources.extend(catalog.discover_workspace(Path(root)))

    own = _project_dist_name(project)
    by_module: dict[str, dict] = {}
    for source in sources:
        try:
            doc = json.loads(Path(source).read_text())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        module = doc.get("module")
        if not isinstance(module, str) or not module:
            continue
        by_module.setdefault(canon_dist(module), doc)

    own_doc = project / "docs" / "capabilities.json"
    if own and own_doc.is_file() and own not in by_module:
        try:
            by_module[own] = json.loads(own_doc.read_text())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass

    entries: list[dict] = []
    for key, doc in sorted(by_module.items()):
        for entry in doc.get("surface") or []:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            item = dict(entry)
            item["module"] = doc.get("module")
            item["module_version"] = doc.get("version")
            item["own"] = bool(own) and key == own
            entries.append(item)
    return entries


def external(index: list[dict]) -> list[dict]:
    """The entries the project did not publish itself."""
    return [entry for entry in index if not entry.get("own")]


def entry_package(entry: dict) -> str:
    """The importable root package an entry lives in (``stapel_core``)."""
    return canon_dist(entry.get("module") or "").replace("-", "_")


# ---------------------------------------------------------------------------
# python file walking / parsing
# ---------------------------------------------------------------------------


def walk_py(root: Path, *, foreign: frozenset[str] = frozenset()) -> Iterable[Path]:
    """Every ``.py`` file the project actually AUTHORS.

    ``foreign`` names packages that belong to somebody else — every module in
    the surface index, in both its distribution and its import spelling. A path
    segment equal to one of them means the file is a *checkout of that library*,
    not the project's code: ``stapel-studio`` keeps `.vendor/stapel-core/` and
    the fleet gate keeps similar working copies, and reporting ``stapel-core``'s
    own ``IsNotAnonymousUser`` as a duplicate of itself is six findings of pure
    noise per vendored checkout — measured, before this filter existed.
    """
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in SKIP_DIRS
            and not d.endswith(".egg-info")
            and canon_dist(d) not in foreign
        )
        for fname in sorted(filenames):
            if fname.endswith(".py"):
                yield Path(dirpath) / fname


def foreign_packages(index: list[dict]) -> frozenset[str]:
    """Canonical keys of every module in the index (``stapel_core`` covers both
    ``stapel-core`` the checkout directory and ``stapel_core`` the package)."""
    return frozenset(
        canon_dist(entry.get("module") or "") for entry in index if entry.get("module")
    )


def _parse(path: Path) -> Optional[ast.Module]:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def _base_names(node: ast.ClassDef) -> list[str]:
    out = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            out.append(base.id)
        elif isinstance(base, ast.Attribute):
            out.append(base.attr)
    return out


def _dunder_all(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                for element in ast.walk(node.value) if node.value else []:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        names.add(element.value)
    return names


def _type_checking_nodes(tree: ast.Module) -> set[int]:
    """``id()`` of every node under an ``if TYPE_CHECKING:`` branch.

    A typing-only import binds nothing at runtime, so demanding a call from it
    would be demanding a call that cannot exist.
    """
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        name = None
        if isinstance(test, ast.Name):
            name = test.id
        elif isinstance(test, ast.Attribute):
            name = test.attr
        if name != "TYPE_CHECKING":
            continue
        for child in node.body:
            for sub in ast.walk(child):
                guarded.add(id(sub))
    return guarded


# ---------------------------------------------------------------------------
# SUR001 — duplicate-of-surface
# ---------------------------------------------------------------------------


def check_duplicate_of_surface(project: Path, index: list[dict]) -> list[Finding]:
    """A project-owned permission class under a name the fleet already publishes.

    Matching is on the published NAME, deliberately. The design's broader form
    ("any ``BasePermission`` subclass while the index holds a
    ``permission_class``") was measured against the fleet and flags every
    legitimate domain permission a product must own — ``IsWorkspaceAdmin``,
    ``IsAdOwner``, ``IsReportModerator`` — none of which duplicate anything. A
    rule that reds on a product's own domain gets muted wholesale, and a muted
    rule protects nothing.
    """
    published = {
        entry["name"]: entry
        for entry in index
        if entry.get("kind") == "permission_class"
    }
    if not published:
        return []

    findings: list[Finding] = []
    for path in walk_py(project, foreign=foreign_packages(index)):
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if _PERMISSION_BASE not in _base_names(node):
                continue
            entry = published.get(node.name)
            if entry is None:
                continue
            findings.append(Finding(
                str(path), node.lineno, "SUR001",
                f"{node.name} is already published by {entry['module']} "
                f"{entry.get('module_version') or ''}".rstrip() + " as "
                f"{entry['path']} — import it instead of keeping a local copy. "
                f"Intent: {entry.get('intent', '')}",
            ))
    return findings


# ---------------------------------------------------------------------------
# SUR002 — instead_of
# ---------------------------------------------------------------------------


def _permission_class_names(tree: ast.Module) -> list[tuple[str, int]]:
    """``(expression, lineno)`` for every entry of a ``permission_classes``
    assignment or keyword argument, spelled exactly as the file spells it
    (``IsAuthenticated`` and ``permissions.IsAuthenticated`` are different
    strings, and which one a file uses decides how the import is resolved)."""
    out: list[tuple[str, int]] = []

    def collect(value: ast.AST, lineno: int) -> None:
        for element in ast.walk(value):
            if isinstance(element, (ast.Name, ast.Attribute)):
                try:
                    text = ast.unparse(element)
                except Exception:  # pragma: no cover - defensive
                    continue
                out.append((text, getattr(element, "lineno", lineno)))

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(
                isinstance(t, ast.Name) and t.id == "permission_classes"
                for t in node.targets
            ):
                collect(node.value, node.lineno)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "permission_classes":
                if node.value is not None:
                    collect(node.value, node.lineno)
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "permission_classes":
                    collect(kw.value, node.lineno)
    return out


def spellings_of(tree: ast.Module, dotted: str) -> set[str]:
    """Every way *this file* can legally spell the dotted symbol *dotted*.

    ``rest_framework.permissions.IsAuthenticated`` is written three ways in the
    fleet and all three are in live code: ``IsAuthenticated`` after a
    ``from ... import``, ``permissions.IsAuthenticated`` after
    ``from rest_framework import permissions`` (the majority idiom — 35 view
    classes in one product), and the full dotted path. Resolving through the
    file's own imports is what keeps a same-named local class from matching.
    """
    parent, _, leaf = dotted.rpartition(".")
    spellings: set[str] = set()
    if not parent:
        return {dotted}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bound = alias.asname or alias.name
                if node.module == parent and alias.name == leaf:
                    spellings.add(bound)
                elif f"{node.module}.{alias.name}" == parent:
                    spellings.add(f"{bound}.{leaf}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == parent:
                    spellings.add(f"{alias.asname or alias.name}.{leaf}")
                elif alias.asname is None and parent.startswith(alias.name + "."):
                    spellings.add(dotted)
    return spellings


def check_instead_of(project: Path, index: list[dict]) -> list[Finding]:
    """A displaced symbol is in use and its published replacement is not — anywhere.

    Narrowed to the WHOLE PROJECT on purpose, and measured: the per-call-site
    form the design sketches produces one finding per view (13 in one product,
    67 occurrences fleet-wide), and almost all of them are a deliberate
    decision that this particular endpoint is open to guests. What is never
    deliberate is a project that puts the displaced symbol in
    ``permission_classes`` and has never once heard of the replacement — that is
    the state the incident happened in, and it is one finding, not sixty-seven.

    NOT caught: a project that uses the replacement somewhere and forgets it on
    one view. That is a per-view judgement, and the fleet data says a machine
    cannot make it — a product with guest sessions legitimately runs both.
    """
    displacing = [
        entry for entry in index
        if entry.get("instead_of") and entry.get("kind") == "permission_class"
    ]
    if not displacing:
        return []

    # one pass over the project: which displaced symbols sit in
    # permission_classes, and whether the replacement occurs anywhere at all
    used_here: dict[str, tuple[str, int]] = {}
    seen_names: set[str] = set()
    for path in walk_py(project, foreign=foreign_packages(index)):
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                seen_names.add(node.id)
            elif isinstance(node, ast.Attribute):
                seen_names.add(node.attr)
            elif isinstance(node, ast.alias):
                seen_names.add((node.asname or node.name).split(".")[0])

        listed = _permission_class_names(tree)
        if not listed:
            continue
        for entry in displacing:
            for displaced in entry["instead_of"]:
                if displaced in used_here:
                    continue
                spellings = spellings_of(tree, displaced)
                for text, lineno in listed:
                    if text in spellings:
                        used_here[displaced] = (str(path), lineno)
                        break

    findings: list[Finding] = []
    for entry in displacing:
        if entry["name"] in seen_names:
            continue  # the project knows the replacement — per-view calls are its own
        for displaced in entry["instead_of"]:
            site = used_here.get(displaced)
            if site is None:
                continue
            path, lineno = site
            findings.append(Finding(
                path, lineno, "SUR002",
                f"permission_classes uses {displaced}, which {entry['module']} "
                f"publishes a replacement for, and {entry['name']} is used "
                f"nowhere in this project — {entry['path']}. "
                f"Intent: {entry.get('intent', '')}",
            ))
    return findings


# ---------------------------------------------------------------------------
# SUR003 — imported-but-never-called
# ---------------------------------------------------------------------------


def check_imported_but_never_called(project: Path, index: list[dict]) -> list[Finding]:
    """A ``gate_function`` bound by an import and never mentioned again.

    A gate leaves nothing behind to inspect: unlike a filter, whose absence
    shows up in the output, a gate that is not called is indistinguishable from
    a gate that passed. So the import IS the whole evidence, and it lies.

    Four legitimate no-call shapes are cleared before anything is reported, all
    of them observed in the fleet:

    * ``__init__.py`` — a re-export hub binds names it never calls, by design;
    * a name in ``__all__`` — the same re-export intent, stated explicitly;
    * an import under ``if TYPE_CHECKING:`` — it binds nothing at runtime;
    * any other reference to the bound name — passed as a callback, stored in a
      registry, wrapped in ``functools.partial``. A deferred call is a call, and
      no AST can tell it from a decorative reference.

    A module ``import``ed for its registration side effect never binds the gate
    name at all, so it is out of scope by construction.
    """
    gates = [entry for entry in index if entry.get("kind") == "gate_function"]
    if not gates:
        return []
    by_name: dict[str, list[dict]] = {}
    for entry in gates:
        by_name.setdefault(entry["name"], []).append(entry)

    findings: list[Finding] = []
    for path in walk_py(project, foreign=foreign_packages(index)):
        if path.name == "__init__.py":
            continue
        tree = _parse(path)
        if tree is None:
            continue
        guarded = _type_checking_nodes(tree)
        exported = _dunder_all(tree)

        # collect candidate bindings: from <owning package>... import <gate>
        bindings: list[tuple[str, dict, int]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if id(node) in guarded:
                continue
            for alias in node.names:
                for entry in by_name.get(alias.name, []):
                    pkg = entry_package(entry)
                    if node.module == pkg or node.module.startswith(pkg + "."):
                        bindings.append((alias.asname or alias.name, entry, node.lineno))
        if not bindings:
            continue

        # every mention of a name in the file, minus the import aliases
        mentions: dict[str, int] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                mentions[node.id] = mentions.get(node.id, 0) + 1
            elif isinstance(node, ast.Attribute):
                mentions[node.attr] = mentions.get(node.attr, 0) + 1

        for bound, entry, lineno in bindings:
            if bound in exported or mentions.get(bound, 0):
                continue
            findings.append(Finding(
                str(path), lineno, "SUR003",
                f"{bound} is imported from {entry['module']} and never called "
                f"or referenced in this module — a gate that is not called is "
                f"indistinguishable from a gate that passed. "
                f"Intent: {entry.get('intent', '')}",
            ))
    return findings


# ---------------------------------------------------------------------------
# SUR004 — publisher-without-consumer
# ---------------------------------------------------------------------------


def locate_consumer_package(
    module: str, *, project: Path, search_roots: list[Path]
) -> Optional[Path]:
    """The ``-react`` source checkout obliged to consume *module*'s fields.

    Only a SOURCE checkout counts. A published tarball in ``node_modules`` would
    answer the question too, but nobody linting a product can fix it there —
    the finding belongs to the repository that can act on it, and pointing a
    product's pre-commit at a defect in a dependency is how a rule gets muted.
    """
    short = module_short(module)
    candidates = [project / "packages" / f"{short}-react"]
    for root in search_roots:
        candidates.append(Path(root) / "stapel-react" / "packages" / f"{short}-react")
    for candidate in candidates:
        if (candidate / "package.json").is_file():
            return candidate
    return None


def _consumer_reads(package: Path, field: str) -> bool:
    """Does hand-written consumer code mention *field*?

    Generated OpenAPI types are excluded, and that exclusion is the whole check:
    ``email_mock`` and ``phone_mock`` are present in ``src/api/generated/
    schema.ts`` and in nothing else, while every other published field of the
    same two DTOs occurs 14-179 times in hand-written components. "Typed but
    unread" is exactly the state that let a screen claim a code had been sent.
    """
    pattern = re.compile(rf"\b{re.escape(field)}\b")
    for name in ("manifest.json", "llms.txt"):
        candidate = package / name
        if candidate.is_file():
            try:
                if pattern.search(candidate.read_text(encoding="utf-8")):
                    return True
            except (OSError, UnicodeDecodeError):
                pass
    src = package / "src"
    if not src.is_dir():
        return False
    for path in sorted(src.rglob("*")):
        if not path.is_file() or path.suffix not in CONSUMER_SOURCE_SUFFIXES:
            continue
        posix = path.as_posix()
        if any(marker in posix for marker in GENERATED_MARKERS):
            continue
        if any(path.name.endswith(suffix) for suffix in GENERATED_SUFFIXES):
            continue
        try:
            if pattern.search(path.read_text(encoding="utf-8")):
                return True
        except (OSError, UnicodeDecodeError):
            continue
    return False


def check_publisher_without_consumer(
    project: Path,
    index: list[dict],
    *,
    search_roots: Optional[list[Path]] = None,
    notes: Optional[list[str]] = None,
) -> list[Finding]:
    """A field published FOR a consumer that the consumer never reads.

    ``consumer`` is not documentation — it is the reason the field exists. A
    capability flag with no reader is worse than an absent one: the backend has
    discharged its duty and the screen still lies.

    Reported only to a repository that can act on it: the module that made the
    promise, or the ``-react`` package that owes it. Without that narrowing the
    rule is worth exactly 2 findings and costs 78 — every one of the 39 other
    repositories in the workspace re-reported ``auth-react``'s two gaps, which is
    how a rule gets switched off before it has ever caught anything.
    """
    notes = notes if notes is not None else []
    search_roots = list(search_roots or [])
    published = [
        entry for entry in index
        if entry.get("consumer") == "frontend" and entry.get("kind") == "capability_field"
    ]
    if not published:
        return []

    findings: list[Finding] = []
    packages: dict[str, Optional[Path]] = {}
    reported: set[tuple[str, str]] = set()
    for entry in published:
        module = entry.get("module") or ""
        if module not in packages:
            packages[module] = locate_consumer_package(
                module, project=project, search_roots=search_roots
            )
            if packages[module] is None and entry.get("own"):
                notes.append(
                    f"stapel-surface-lint: no source checkout of the "
                    f"{module_short(module)}-react package (looked in the project "
                    f"and every --workspace root) — SUR004 skipped for {module}"
                )
        package = packages[module]
        if package is None:
            continue
        if not entry.get("own") and project not in package.parents:
            continue  # neither the publisher nor the consumer — not this repo's finding
        field = entry["name"].rsplit(".", 1)[-1]
        key = (module, field)
        if key in reported or _consumer_reads(package, field):
            continue
        reported.add(key)
        findings.append(Finding(
            str(package / "package.json"), 1, "SUR004",
            f"{module} publishes '{field}' with consumer: frontend and "
            f"{package.name} reads it nowhere outside its generated types "
            f"({entry['name']} — {entry['path']}). "
            f"Intent: {entry.get('intent', '')}",
        ))
    return findings


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------


def lint_project(
    project: Path,
    *,
    search_roots: Optional[list[Path]] = None,
    use_installed: bool = True,
    notes: Optional[list[str]] = None,
) -> list[Finding]:
    project = Path(project).resolve()
    notes = notes if notes is not None else []
    search_roots = list(search_roots or [project.parent])
    index = load_surface_index(
        project,
        search_roots=search_roots,
        use_installed=use_installed,
        warn=lambda msg: None,
    )
    if not index:
        notes.append(
            "stapel-surface-lint: no capabilities.json with a 'surface' section "
            "is visible (no installed stapel-* distribution ships one, and no "
            "--workspace root holds one) — all SUR rules skipped"
        )
        return []
    others = external(index)
    findings = check_duplicate_of_surface(project, others)
    findings += check_instead_of(project, others)
    findings += check_imported_but_never_called(project, others)
    findings += check_publisher_without_consumer(
        project, index, search_roots=search_roots, notes=notes
    )
    return findings


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-surface-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_dir", nargs="?", default=".", help="Project root.")
    parser.add_argument(
        "--workspace", action="append", default=[],
        help="Extra root holding sibling stapel-<mod>/stapel-react checkouts "
             "(repeatable).",
    )
    parser.add_argument(
        "--no-installed", action="store_true",
        help="Do not read the surface index from the installed distributions "
             "of the current environment (workspace roots only).",
    )
    parser.add_argument("--json", action="store_true", help="Machine output.")
    args = parser.parse_args(argv)

    project = Path(args.project_dir)
    if not project.is_dir():
        print(f"Error: not a directory: {project}", file=sys.stderr)
        return 2

    notes: list[str] = []
    findings = lint_project(
        project,
        search_roots=[Path(w).resolve() for w in args.workspace] or None,
        use_installed=not args.no_installed,
        notes=notes,
    )
    errors = sum(1 for f in findings if f.level == "error")

    if args.json:
        print(json.dumps(
            {
                "ok": errors == 0,
                "errors": errors,
                "warnings": len(findings) - errors,
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
        print(
            f"stapel-surface-lint: {errors} error(s) in {project}"
            if errors else f"stapel-surface-lint: clean ({project})"
        )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
