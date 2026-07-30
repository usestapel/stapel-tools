"""stapel-surface — the THIRD surface of ``docs/capabilities.json``.

``axes`` describe the **configuration** surface ("what can be switched on");
``extension_points`` describe the **substitution** surface ("what can be
replaced"). Neither answers the question a product author actually asks —
*"is there already a mechanism for X, and what do I call?"* — so six mechanisms
in one night were built, released and never picked up: a permission class, a
safety gate, a published capability field, a nav template, a loader factory, a
set of error predicates. Every one of them was invisible to a machine reading
the fleet's contracts.

``surface`` is that third section: the **usage** surface — the symbols a
product is meant to call, subclass, mount or read.

Discipline (the same one that has kept ``axes`` alive since it shipped):

* **the entry set is DERIVED, never hand-listed.** A module declares
  ``surface_roots`` in its ``docs/capabilities.meta.json`` — *scopes*, not
  symbols — and every symbol a root selects becomes an entry. A new export in a
  declared root appears by itself; a deleted one disappears and takes its stale
  prose with it. Hand-maintained lists rot, so there is no way to write one.
* **``name``/``kind``/``path`` are machine facts.** The selector fixes the
  ``kind`` family; only the three function roles are curated (and validated
  against the AST shape), because "is this a gate, a predicate or a factory" is
  not readable off a ``def``.
* **``intent`` is curated, and its absence is LOUD.** A selected symbol without
  an intent line fails ``make contract`` naming the symbol. A warning would be
  read zero times; and a library that exports a symbol it cannot explain has
  just built the next unadopted mechanism.

Selectors (a CLOSED set — a new one is added when a seventh genre of mechanism
is actually missed, not in advance):

``permission_classes``
    ``path``: a ``.py`` file. Every class whose bases name ``BasePermission``.
    kind: ``permission_class`` (derived).
``functions``
    ``path``: a ``.py`` file. Every public module-level ``def``/``async def``.
    kind: curated, one of ``gate_function`` / ``predicate`` / ``factory``.
``capability_fields``
    ``path``: a ``.py`` file + ``class``: a class name. Every annotated field
    of that class. kind: ``capability_field`` (derived); ``name`` is
    ``Class.field`` so two DTOs may carry the same field name.
``templates``
    ``path``: a directory. Every file under it, recursively.
    kind: ``template`` (derived); ``name``/``path`` are repo-relative.

Curated per-entry fields (``surface`` map in the meta file, keyed by entry
name):

``intent`` (required)
    One line: what this exists for and when to reach for it.
``instead_of`` (optional)
    Outside symbols this one displaces (``rest_framework.permissions.
    IsAuthenticated``). The fuel for a duplicate-of-surface check: it turns
    "the product wrote its own" into something a machine can name.
``consumer`` (optional)
    Who is OBLIGED to consume this (``frontend`` for a capability field
    published so a host UI can render a dev-mode badge). The fuel for a
    publisher-without-consumer check.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
import tomllib
from pathlib import Path

#: The closed ``kind`` vocabulary — exactly the six genres of mechanism that
#: were observed going unadopted. Closed on purpose: an open vocabulary becomes
#: a heap of synonyms (``helper``/``util``/``function``) and a search over it
#: stops working. It grows on the next incident, not in advance.
KINDS = (
    "permission_class",
    "gate_function",
    "template",
    "predicate",
    "capability_field",
    "factory",
)

#: Selector name -> the kind it derives, or ``None`` when the kind is curated.
SELECTORS: dict[str, str | None] = {
    "permission_classes": "permission_class",
    "functions": None,
    "capability_fields": "capability_field",
    "templates": "template",
}

#: Kinds a ``functions`` root may curate. A ``def`` cannot be a permission
#: class or a template, so the curated choice is bounded to the three roles a
#: callable can play — and each one drives a different pre-merge check
#: (a gate that is imported and never called; a predicate that was reinvented;
#: a factory the host never wired).
FUNCTION_KINDS = ("gate_function", "predicate", "factory")

_PERMISSION_BASE = "BasePermission"


def _stable_json(data) -> str:
    """Byte-stable JSON for drift gates — same pinning as the sibling emitters."""
    return json.dumps(data, indent=2, ensure_ascii=False, separators=(",", ": ")) + "\n"


# ---------------------------------------------------------------------------
# AST scanners — one per selector, each returning (name, dotted_suffix) pairs
# ---------------------------------------------------------------------------


def _parse(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise SystemExit(f"surface: cannot parse {path}: {exc}") from exc


def _base_names(node: ast.ClassDef) -> list[str]:
    """Trailing names of a class's bases (``permissions.BasePermission`` ->
    ``BasePermission``) — enough to recognise a genre without importing the
    module (the emitter must not need the module's Django harness)."""
    out = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            out.append(base.id)
        elif isinstance(base, ast.Attribute):
            out.append(base.attr)
    return out


def scan_permission_classes(path: Path) -> list[str]:
    """Names of DRF permission classes defined in *path*."""
    return [
        node.name
        for node in _parse(path).body
        if isinstance(node, ast.ClassDef) and _PERMISSION_BASE in _base_names(node)
    ]


def scan_functions(path: Path) -> list[str]:
    """Names of public module-level functions in *path*.

    Module-level only, and ``_``-prefixed names are skipped: a private helper
    is not a surface, and demanding an intent line for one would train authors
    to write filler."""
    return [
        node.name
        for node in _parse(path).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    ]


def scan_capability_fields(path: Path, class_name: str) -> list[str]:
    """``Class.field`` for every annotated field of *class_name* in *path*."""
    for node in _parse(path).body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return [
                f"{class_name}.{stmt.target.id}"
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            ]
    raise SystemExit(f"surface: {path} defines no class {class_name!r}")


def scan_templates(directory: Path) -> list[str]:
    """Every template file under *directory*, as a directory-relative path."""
    return sorted(
        str(p.relative_to(directory))
        for p in directory.rglob("*")
        if p.is_file() and not p.name.startswith(".")
    )


# ---------------------------------------------------------------------------
# root expansion
# ---------------------------------------------------------------------------


def package_name(repo: Path) -> str:
    """The importable package a repo root IS (``stapel-core`` -> ``stapel_core``)."""
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text())
    return pyproject["project"]["name"].replace("-", "_")


def _dotted(package: str, rel: Path, symbol: str) -> str:
    module = ".".join([package, *rel.with_suffix("").parts])
    return f"{module}.{symbol}"


def expand_root(root: dict, *, repo: Path, package: str) -> list[tuple[str, str, str]]:
    """One ``surface_roots`` entry -> ``[(name, kind_or_empty, path), ...]``.

    ``kind_or_empty`` is ``""`` when the selector leaves the kind to curation.
    """
    select = root.get("select")
    if select not in SELECTORS:
        raise SystemExit(
            f"surface: unknown selector {select!r} in surface_roots — the "
            f"selector set is closed: {', '.join(sorted(SELECTORS))}"
        )
    rel_raw = root.get("path")
    if not rel_raw:
        raise SystemExit(f"surface: surface_roots entry {root!r} has no 'path'")
    rel = Path(rel_raw)
    target = repo / rel
    if not target.exists():
        raise SystemExit(f"surface: surface root {target} does not exist")

    derived = SELECTORS[select] or ""

    if select == "templates":
        return [
            (name, derived, str(rel / name)) for name in scan_templates(target)
        ]
    if select == "permission_classes":
        names = scan_permission_classes(target)
    elif select == "functions":
        names = scan_functions(target)
    else:  # capability_fields
        class_name = root.get("class")
        if not class_name:
            raise SystemExit(
                f"surface: capability_fields root {rel_raw!r} needs a 'class'"
            )
        names = scan_capability_fields(target, class_name)
    return [(name, derived, _dotted(package, rel, name)) for name in names]


# ---------------------------------------------------------------------------
# the LOUD assembly
# ---------------------------------------------------------------------------


def build_surface(meta: dict, *, repo: Path, package: str | None = None) -> list[dict]:
    """Assemble the ``surface`` section from the meta's roots + intents.

    Both directions of drift are loud, exactly as they are for ``axes``: a
    selected symbol with no curated intent fails naming the symbol, and a
    curated intent for a symbol no root selects fails as stale prose. Returns
    ``[]`` when the module declares no roots — ``surface`` is opt-in per module
    at the section's introduction (see the emitters), so a library that has not
    declared a root yet emits exactly what it emitted before.
    """
    roots = meta.get("surface_roots") or []
    if not roots:
        if meta.get("surface"):
            raise SystemExit(
                "surface: capabilities.meta.json carries a 'surface' intent map "
                "but no 'surface_roots' — nothing selects those symbols, so "
                "every entry is unreachable prose. Declare the roots."
            )
        return []
    if package is None:
        package = package_name(repo)

    curated = meta.get("surface")
    if not isinstance(curated, dict):
        raise SystemExit(
            "surface: capabilities.meta.json declares surface_roots but its "
            "'surface' key is missing or is not an object (name -> {intent, ...})"
        )

    selected: list[tuple[str, str, str]] = []
    for root in roots:
        selected.extend(expand_root(root, repo=repo, package=package))

    seen: dict[str, str] = {}
    for name, _kind, path in selected:
        if name in seen:
            raise SystemExit(
                f"surface: two roots both select {name!r} ({seen[name]} and "
                f"{path}) — entry names must be unique within a module"
            )
        seen[name] = path

    missing = [name for name, _k, _p in selected if not (curated.get(name) or {}).get("intent")]
    if missing:
        raise SystemExit(
            "surface: exported without an intent — "
            + ", ".join(sorted(missing))
            + ". Every symbol a surface root selects must say, in one line, "
            "what it exists for and when to reach for it, in "
            "docs/capabilities.meta.json's 'surface' map. A mechanism nobody "
            "can explain is the next mechanism nobody adopts."
        )
    stale = [name for name in curated if name not in seen]
    if stale:
        raise SystemExit(
            "surface: curated intent for a symbol no surface root selects — "
            + ", ".join(sorted(stale))
            + ". Remove the stale entry or widen the root."
        )

    entries: list[dict] = []
    for name, derived_kind, path in selected:
        item = curated[name]
        kind = derived_kind or item.get("kind")
        if derived_kind and item.get("kind") not in (None, derived_kind):
            raise SystemExit(
                f"surface: {name!r} is selected as {derived_kind!r} but the "
                f"curated layer claims kind {item.get('kind')!r} — the kind of "
                "a derived genre is a machine fact, drop it from the meta"
            )
        if not kind:
            raise SystemExit(
                f"surface: {name!r} needs an explicit 'kind' in the curated "
                f"layer, one of {', '.join(FUNCTION_KINDS)}"
            )
        if kind not in KINDS:
            raise SystemExit(
                f"surface: {name!r} has kind {kind!r}, which is not in the "
                f"closed vocabulary ({', '.join(KINDS)})"
            )
        if not derived_kind and kind not in FUNCTION_KINDS:
            raise SystemExit(
                f"surface: {name!r} is a function, so its kind must be one of "
                f"{', '.join(FUNCTION_KINDS)}, not {kind!r}"
            )
        entry = {
            "name": name.split(".")[-1] if kind == "capability_field" else name,
            "kind": kind,
            "path": path,
            "intent": item["intent"],
        }
        instead_of = item.get("instead_of")
        if instead_of:
            if not isinstance(instead_of, list):
                raise SystemExit(
                    f"surface: {name!r} 'instead_of' must be a list of symbols"
                )
            entry["instead_of"] = list(instead_of)
        consumer = item.get("consumer")
        if consumer:
            entry["consumer"] = consumer
        entries.append(entry)

    return sorted(entries, key=lambda e: (e["path"], e["name"]))


# ---------------------------------------------------------------------------
# standalone emitter — for modules with no derivable axes/operations pipeline
# ---------------------------------------------------------------------------


def load_meta(repo: Path) -> dict:
    path = repo / "docs" / "capabilities.meta.json"
    if not path.is_file():
        raise SystemExit(
            f"surface: curated layer {path} is missing — it is hand-written "
            "(surface_roots + one intent line per selected symbol) and must be "
            "committed."
        )
    return json.loads(path.read_text())


def build_static_capabilities(repo: Path, meta: dict) -> dict:
    """A whole ``capabilities.json`` for a module with no OpenAPI surface.

    ``stapel-core`` is the fleet's most-reused repository and was its only
    undescribed one — not out of neglect: the document could describe config
    axes and OpenAPI operations, and the core has neither, so it had literally
    nothing to say in the format. ``operations_total`` is therefore emitted
    only when the module actually ships a ``docs/schema.json``: its absence is
    a limit of the format, not a defect of the module, and a mandatory zero
    would have read as "this module serves no HTTP" — a claim about the module
    rather than about the document.
    """
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text())
    if not meta.get("provides"):
        raise SystemExit("surface: meta field 'provides' is missing/empty")
    for field in ("extension_points", "requires"):
        if not isinstance(meta.get(field), list):
            raise SystemExit(f"surface: meta field {field!r} must be a list")

    doc: dict = {
        "module": pyproject["project"]["name"],
        "version": pyproject["project"]["version"],
        "provides": meta["provides"],
        "axes": list(meta.get("axes") or []),
        "extension_points": meta["extension_points"],
    }
    surface = build_surface(meta, repo=repo)
    if surface:
        doc["surface"] = surface

    schema_path = repo / "docs" / "schema.json"
    if schema_path.is_file():
        schema = json.loads(schema_path.read_text())
        doc["operations_total"] = sum(
            1
            for path_item in schema.get("paths", {}).values()
            for method in path_item
            if method in ("get", "put", "post", "delete", "options", "head", "patch", "trace")
        )
    doc["requires"] = meta["requires"]
    return doc


def patch_capabilities(repo: Path, meta: dict) -> dict:
    """Inject ``surface`` into a module's EXISTING ``capabilities.json``.

    For a module whose document is not (yet) emitted end-to-end from code —
    ``stapel-agent`` keeps a hand-written one — so the usage surface can still
    be derived and drift-gated without first rebuilding that module's whole
    contract pipeline. ``module``/``version`` are refreshed from
    ``pyproject.toml`` while we are here: they are derivable facts, and a
    hand-written copy of a version number is exactly the drift this section
    exists to stop.
    """
    path = repo / "docs" / "capabilities.json"
    if not path.is_file():
        raise SystemExit(
            f"surface: --patch needs an existing {path}; emit the document "
            "first (or drop --patch to build it from the meta layer)."
        )
    doc = json.loads(path.read_text())
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text())
    doc["module"] = pyproject["project"]["name"]
    doc["version"] = pyproject["project"]["version"]

    surface = build_surface(meta, repo=repo)
    doc.pop("surface", None)
    if surface:
        # keep the section next to its two siblings, before the counters
        rebuilt: dict = {}
        for key, value in doc.items():
            rebuilt[key] = value
            if key == "extension_points":
                rebuilt["surface"] = surface
        if "surface" not in rebuilt:
            rebuilt["surface"] = surface
        doc = rebuilt
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-surface",
        description="Emit docs/capabilities.json for a module with no OpenAPI "
        "pipeline (stapel-core), or inject the derived 'surface' section into "
        "an existing one (--patch). Symbols come from the meta layer's "
        "surface_roots; an export with no curated intent is a loud failure.",
    )
    parser.add_argument("repo", nargs="?", default=".", help="Module repo root.")
    parser.add_argument(
        "--patch",
        action="store_true",
        help="Only refresh module/version + the 'surface' section of an "
        "already-emitted docs/capabilities.json, leaving the rest verbatim.",
    )
    parser.add_argument(
        "--out",
        help="Output directory (default: <repo>/docs) — a temp dir under a "
        "drift gate.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare against the committed docs/capabilities.json byte for "
        "byte; nonzero exit and no write on drift.",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    meta = load_meta(repo)
    doc = patch_capabilities(repo, meta) if args.patch else build_static_capabilities(repo, meta)
    rendered = _stable_json(doc)

    committed = repo / "docs" / "capabilities.json"
    if args.check:
        if not committed.is_file():
            print(f"surface: --check: {committed} does not exist", file=sys.stderr)
            return 1
        if committed.read_text() != rendered:
            print(
                f"DRIFT: {committed} is stale — run the module's `make contract` "
                "and commit it",
                file=sys.stderr,
            )
            return 1
        print(f"surface: --check: {committed} is up to date", file=sys.stderr)
        return 0

    out_dir = Path(args.out) if args.out else repo / "docs"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "capabilities.json").write_text(rendered)
    print(
        f"{doc['module']} capabilities: {len(doc.get('surface') or [])} surface "
        f"entries, {len(doc.get('extension_points') or [])} extension points "
        f"→ {out_dir}/capabilities.json",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
