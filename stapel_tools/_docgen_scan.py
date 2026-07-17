"""Shared per-module doc-artifact discovery for `stapel-gen-client` /
`stapel-docs` (owner directive, `docs/pending/profile-fields.md` "Дополнение
владельца" §17.07 — "раз мы оверрайдим профайл, его фронтенд-пара должна
уметь это грамотно обрабатывать").

Both commands need the same thing: which backend module "slices" does this
PROJECT carry a committed OpenAPI ``schema.json`` for, and where do its
sibling ``flows.json``/``errors.json``/translations live. One scanner, so
the two CLIs agree on module names and paths — a project's client-override
folders (`stapel-gen-client`) and its API docs (`stapel-docs`) are keyed by
the SAME module name.

Three real, already-observed layouts, all supported (first match per
resolved path wins; a project may mix layouts):

1. **Monolith aggregate** — ``codegen/generated/schema.json`` (+
   ``flows.json``/``errors.json`` siblings) — the artifact
   ``stapel_tools.codegen`` / ``codegen/generate.sh`` produce for an
   all-modules Django instance (flow-system.md §0.1). One module named
   after the project directory.
2. **Per-module/per-service docs/** — ``<service>/docs/schema.json``
   (a vendored library checkout, or a microservices project's own
   per-service backend) — the SAME path shape individual stapel-* library
   repos already commit at their own root (e.g. ``stapel-auth/docs/
   schema.json``, consumed today by stapel-react's ``scripts/gen-api.mjs``).
   Module name = the service directory name (``svc-`` prefix stripped).
3. **Literal per-module app slice** — ``<mod>/api/v1/schema.json`` (the
   path shape named verbatim in the owner directive) — a module app inside
   a monolith service directory that runs its own scoped spectacular
   export instead of using the project-wide aggregate.

Translations (when present) live in a sibling ``translations/`` directory
next to the ``docs/`` (or ``codegen/generated/``) directory's parent — the
layout stapel-translate already writes into individual library repos
(``translations/errors.ru.json``, ``translations/flows.ru.json``). A
project that only ever sees the monolith aggregate typically has none yet
— that is an honest gap, not a bug; both consumers fall back to English
with an "(en)" marker rather than fabricate a translation.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SKIP_DIRS = {
    "__pycache__", ".git", ".hg", ".tox", ".venv", "venv", "node_modules",
    "htmlcov", "build", "dist", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "migrations", "frontend", "worktrees", "site-packages",
}


@dataclass(frozen=True)
class ModuleDocs:
    """One module's committed doc bundle, as found in the project.

    ``is_aggregate`` marks layout 1 (the monolith's ``codegen/generated/``)
    — a single schema/flows/errors triple spanning EVERY installed module,
    tagged internally by path/flow-id prefix rather than split into one
    file per module. `stapel-docs` uses the flag to further group that one
    bundle's flows/endpoints by first path segment for its "per module"
    doc structure; errors stay one flat table (error codes carry no module
    prefix — see module docstring)."""

    name: str
    schema_json: Path
    flows_json: Optional[Path] = None
    errors_json: Optional[Path] = None
    flows_ru_json: Optional[Path] = None
    errors_ru_json: Optional[Path] = None
    is_aggregate: bool = False


def _sibling(directory: Path, filename: str) -> Optional[Path]:
    p = directory / filename
    return p if p.is_file() else None


def _translations_for(docs_dir: Path) -> tuple[Optional[Path], Optional[Path]]:
    translations_dir = docs_dir.parent / "translations"
    if not translations_dir.is_dir():
        return None, None
    return (
        _sibling(translations_dir, "flows.ru.json"),
        _sibling(translations_dir, "errors.ru.json"),
    )


def _is_aggregate(docs_dir: Path) -> bool:
    return docs_dir.name == "generated" and docs_dir.parent.name == "codegen"


def _module_name(docs_dir: Path, project_dir: Path) -> str:
    if _is_aggregate(docs_dir):
        return project_dir.name or "project"
    # literal `<mod>/api/v1/schema.json` (owner directive's own wording) —
    # the module is the app dir two levels up, not `api`.
    if docs_dir.name == "v1" and docs_dir.parent.name == "api":
        mod_dir = docs_dir.parent.parent
        return mod_dir.name or "project"
    parent = docs_dir.parent
    if parent == project_dir:
        return project_dir.name or "project"
    name = parent.name
    return name[4:] if name.startswith("svc-") else name


def _entry(docs_dir: Path, project_dir: Path) -> Optional[ModuleDocs]:
    schema = _sibling(docs_dir, "schema.json")
    if schema is None:
        return None
    flows_ru, errors_ru = _translations_for(docs_dir)
    return ModuleDocs(
        name=_module_name(docs_dir, project_dir),
        schema_json=schema,
        flows_json=_sibling(docs_dir, "flows.json"),
        errors_json=_sibling(docs_dir, "errors.json"),
        flows_ru_json=flows_ru,
        errors_ru_json=errors_ru,
        is_aggregate=_is_aggregate(docs_dir),
    )


def discover_modules(project_dir: str | Path) -> list[ModuleDocs]:
    """Every module in *project_dir* with a committed ``schema.json``,
    de-duplicated by resolved path (first-seen order: the monolith
    aggregate first, then a breadth-first walk of the rest of the tree)."""
    project_dir = Path(project_dir)
    seen: dict[Path, ModuleDocs] = {}
    candidate_dirs: list[Path] = []

    aggregate = project_dir / "codegen" / "generated"
    if aggregate.is_dir():
        candidate_dirs.append(aggregate)

    root_docs = project_dir / "docs"
    if root_docs.is_dir():
        candidate_dirs.append(root_docs)

    # Breadth-first walk for `<service>/docs/schema.json` and
    # `<mod>/api/v1/schema.json` — bounded depth (project layouts are
    # shallow by convention), skipping vendored/build/test noise.
    frontier = [project_dir]
    for _ in range(4):
        next_frontier: list[Path] = []
        for d in frontier:
            try:
                children = sorted(p for p in d.iterdir() if p.is_dir())
            except OSError:
                continue
            for child in children:
                if child.name in SKIP_DIRS or child.name.startswith("."):
                    continue
                docs_dir = child / "docs"
                if docs_dir.is_dir():
                    candidate_dirs.append(docs_dir)
                v1_dir = child / "api" / "v1"
                if v1_dir.is_dir() and (v1_dir / "schema.json").is_file():
                    candidate_dirs.append(v1_dir)
                next_frontier.append(child)
        frontier = next_frontier

    for docs_dir in candidate_dirs:
        entry = _entry(docs_dir, project_dir)
        if entry is None:
            continue
        resolved = entry.schema_json.resolve()
        if resolved not in seen:
            seen[resolved] = entry
    return list(seen.values())
