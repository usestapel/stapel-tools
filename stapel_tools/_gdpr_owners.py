"""Derive ``STAPEL_GDPR["DATA_OWNERS"]`` from the libraries a project selected.

The defect this closes: ``create_project`` installed ``stapel_gdpr`` and every
owner library beside it, and emitted no ``DATA_OWNERS`` at all. stapel-gdpr
raises the boot-fatal ``gdpr.E001`` on an empty inventory, so the generated
project was dead on arrival — and this repo's own ``check_required_settings``
refused to generate it ("required module settings are missing"), which is
where stapel-studio's scaffold-assembly task hit the wall: it calls
``assemble_scaffold(..., config=None)`` and has no map to hand over.

Asking the caller for the map was never the answer. The map is not an opinion:
it is a FACT about the selection, and every participating library already
publishes it in machine-readable form. So the generator derives it — zero
tiers, no LLM, no placeholder — from the selected libraries themselves.

Why the map has to be right
---------------------------
``DATA_OWNERS`` is the inventory an erasure is certified against. stapel-gdpr
waits for a receipt from every store listed here before a closure may reach
DELETED. A store that holds personal data and is MISSING from the map is never
asked and never waited for: the erasure reports success while the data is
still on disk. Silent retention, with a receipt saying otherwise. That is why
an unreadable declaration is a hard generation failure below, and never a
guessed name or an example placeholder.

Two ways a library participates, both read from the library itself
------------------------------------------------------------------
1. The erasure-request protocol (0.5.0+): the library ships
   ``schemas/consumes/gdpr.erasure.requested.json`` and declares
   ``OWNER``/``SUBJECT_TYPES`` (or ``GDPR_OWNER``/``GDPR_SUBJECT_TYPES``) in
   its ``erasure.py``/``gdpr.py``. This is exactly ADO005's detection, and it
   is IMPORTED from :mod:`stapel_tools.adoption_lint` rather than reimplemented
   — one reader, so the linter and the generator can never disagree about who
   an owner is.
2. The in-process ``GDPRProvider`` (the older half, still live in most libs):
   the library's ``apps.py`` calls ``gdpr_registry.register(XGDPRProvider())``
   and the class carries a ``section``. stapel-gdpr's own ``gdpr.E002`` fires
   on a registered provider that is absent from ``DATA_OWNERS``, so leaving
   these out would produce a project that fails ``manage.py check`` just as
   surely as an empty map — the second half of "dead on arrival", in a
   different check id. A provider registers ``account`` data by construction,
   so its subject list is ``["account"]`` unless (1) says otherwise.

Where both apply, (1) wins: it carries the real subject types, and the two
declarations agree on the name by design (``stapel-cdn`` answers to ``media``
in both, ``stapel-profiles`` to ``profile``).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .adoption_lint import (
    GDPR_CONSUMES_ERASURE,
    GDPR_DECL_FILES,
    _resolve_str,
    _string_env,
    locate_module_dir,
    read_gdpr_owner,
)

#: The module that HOSTS the registry — it declares no store of its own, and
#: its ``apps.py`` registers whatever ``settings.GDPR_PROVIDERS`` names rather
#: than a class of its own (see :func:`_registered_provider_classes`).
GDPR_HOST = "gdpr"

#: The in-process registry a legacy provider is registered into.
GDPR_REGISTRY_NAME = "gdpr_registry"

#: The account subject every in-process ``GDPRProvider`` erases — the only
#: subject that existed before the erasure protocol grew entity types.
SUBJECT_ACCOUNT = "account"


@dataclass(frozen=True)
class OwnerDeclaration:
    """One selected library's answer to "what do you own, and under what name".

    ``via`` records WHICH declaration was read, so the derivation table the
    generator prints can be checked against the library by hand.
    """

    module: str
    owner: str
    subject_types: tuple[str, ...]
    via: str
    decl: str


def _package_name(module: str) -> str:
    return "stapel_" + module.replace("-", "_")


def _module_dir(module: str, workspace_root: Path | None) -> Path | None:
    """Where this library's source is — the installed distribution, or the
    sibling checkout under *workspace_root*.

    An explicit *workspace_root* is authoritative: a caller (a test, a
    generator pinned at a specific checkout) that names a root means that root,
    not whatever happens to be importable in the ambient environment.
    """
    if workspace_root is not None:
        cand = Path(workspace_root) / f"stapel-{module}"
        return cand if cand.is_dir() else None
    from . import _module_config

    return locate_module_dir(
        _package_name(module), [_module_config._default_workspace_root()]
    )


def _decl_trees(mod_dir: Path) -> dict[str, ast.AST]:
    """Parsed ``erasure.py``/``gdpr.py`` — the two files a declaration lives in."""
    trees: dict[str, ast.AST] = {}
    for fname in GDPR_DECL_FILES:
        path = mod_dir / fname
        if not path.is_file():
            continue
        try:
            trees[fname] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
    return trees


def _registered_provider_classes(mod_dir: Path) -> set[str]:
    """Class names this library registers into the in-process gdpr registry.

    Only a name the library itself defines counts. ``stapel_gdpr`` registers
    ``provider_cls()`` — a variable holding whatever ``GDPR_PROVIDERS`` named —
    which resolves to no class here, which is correct: the host owns no store.
    """
    path = mod_dir / "apps.py"
    if not path.is_file():
        return set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "register"
            and isinstance(func.value, ast.Name)
            and func.value.id == GDPR_REGISTRY_NAME
        ):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
            names.add(arg.func.id)
        elif isinstance(arg, ast.Name):
            names.add(arg.id)
    return names


def _provider_section(
    mod_dir: Path, class_names: set[str]
) -> tuple[str | None, str | None, bool]:
    """``(section, file, class_found)`` for a registered provider class.

    ``class_found`` separates "this library registers no class of its own"
    (not a participant) from "it does, and its ``section`` is unreadable"
    (a hard failure — an owner nobody can name cannot be listed).
    """
    trees = _decl_trees(mod_dir)
    consts: dict[str, str] = {}
    attrs: dict[str, str] = {}
    for tree in trees.values():
        tree_consts, tree_attrs = _string_env(tree)
        consts.update(tree_consts)
        attrs.update(tree_attrs)
    found = False
    for fname, tree in trees.items():
        for node in getattr(tree, "body", []):
            if not isinstance(node, ast.ClassDef) or node.name not in class_names:
                continue
            found = True
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign):
                    continue
                if not any(
                    isinstance(t, ast.Name) and t.id == "section" for t in stmt.targets
                ):
                    continue
                section = _resolve_str(stmt.value, consts, attrs)
                if section:
                    return section, fname, True
    return None, None, found


def read_owner_declaration(
    module: str, workspace_root: Path | None = None
) -> OwnerDeclaration | None:
    """This library's owner declaration, or ``None`` when it owns no store.

    Raises ``SystemExit`` when the library plainly PARTICIPATES (it ships the
    erasure consume-contract, or registers a provider class of its own) but
    the constant that names it cannot be read. There is no safe fallback: a
    guessed name is a store the orchestrator never asks and never waits for.
    """
    if module == GDPR_HOST:
        return None
    mod_dir = _module_dir(module, workspace_root)
    if mod_dir is None:
        return None

    search_roots = [mod_dir.parent]
    erasure = read_gdpr_owner(_package_name(module), search_roots)
    if erasure is not None:
        if not erasure.owner or not erasure.subject_types:
            raise SystemExit(
                f"Error: stapel-{module} is a gdpr data owner (it ships "
                f"{'/'.join(GDPR_CONSUMES_ERASURE)}) "
                f"but its OWNER/SUBJECT_TYPES constants in {mod_dir} could not be "
                f"read, so this generator cannot name it in "
                f'STAPEL_GDPR["DATA_OWNERS"]. An owner missing from that map is '
                f"never asked to erase and never waited for — the closure reports "
                f"DELETED while the data is still there. Fix the declaration in "
                f"stapel-{module}, or pass the map explicitly via --module-config."
            )
        return OwnerDeclaration(
            module=module,
            owner=erasure.owner,
            subject_types=tuple(erasure.subject_types),
            via="erasure protocol",
            decl=erasure.decl or "",
        )

    classes = _registered_provider_classes(mod_dir)
    if not classes:
        return None
    section, fname, class_found = _provider_section(mod_dir, classes)
    if not class_found:
        # Registers something it does not define (the host's GDPR_PROVIDERS
        # indirection) — not a store of its own.
        return None
    if not section:
        raise SystemExit(
            f"Error: stapel-{module} registers an in-process GDPRProvider "
            f"({', '.join(sorted(classes))}) but its `section` in {mod_dir} could "
            f"not be read, so this generator cannot name it in "
            f'STAPEL_GDPR["DATA_OWNERS"]. A registered provider absent from that '
            f"map fails stapel-gdpr's gdpr.E002 at boot, and a store missing from "
            f"the inventory is silent retention. Fix the declaration in "
            f"stapel-{module}, or pass the map explicitly via --module-config."
        )
    return OwnerDeclaration(
        module=module,
        owner=section,
        subject_types=(SUBJECT_ACCOUNT,),
        via="in-process provider",
        decl=fname or "",
    )


def owner_declarations(
    selected: list[str], workspace_root: Path | None = None
) -> list[OwnerDeclaration]:
    """Every selected library that owns personal data, in owner-name order."""
    found: dict[str, OwnerDeclaration] = {}
    for module in selected:
        decl = read_owner_declaration(module, workspace_root)
        if decl is None:
            continue
        # One name is one owner (stapel_core.gdpr.register_gdpr_owner enforces
        # the same rule at runtime); the richer declaration wins.
        current = found.get(decl.owner)
        if current is None or len(decl.subject_types) > len(current.subject_types):
            found[decl.owner] = decl
    return [found[name] for name in sorted(found)]


def derive_data_owners(
    selected: list[str], workspace_root: Path | None = None
) -> dict[str, list[str]]:
    """``{owner: [subject types]}`` for the selected libraries."""
    return {
        decl.owner: list(decl.subject_types)
        for decl in owner_declarations(selected, workspace_root)
    }


def data_owners_version(on: date | None = None) -> str:
    """The stamp every closure records the inventory by.

    Dated, because the question it answers is "which inventory certified this
    erasure" — and the inventory changes when the selection does. The ``.1``
    is the revision within the day; a human bumps it when they edit the map.
    """
    return f"{(on or date.today()).isoformat()}.1"


def derivation_table(declarations: list[OwnerDeclaration]) -> str:
    """Human-readable receipt of what was derived and where each name came from."""
    if not declarations:
        return "  (no selected library owns personal data)"
    width = max(len(d.owner) for d in declarations)
    lines = []
    for decl in declarations:
        subjects = ", ".join(decl.subject_types)
        source = f"stapel-{decl.module}/{decl.decl}" if decl.decl else f"stapel-{decl.module}"
        lines.append(
            f"  {decl.owner.ljust(width)}  {subjects}"
            f"  <- {source} ({decl.via})"
        )
    return "\n".join(lines)


def inject_derived_data_owners(
    module_config: dict[str, dict] | None,
    selected: list[str],
    *,
    workspace_root: Path | None = None,
    verbose: bool = True,
) -> dict[str, dict] | None:
    """Fill in ``STAPEL_GDPR["DATA_OWNERS"]``/``DATA_OWNERS_VERSION`` for a
    project that selected stapel-gdpr and did not supply them.

    A caller-supplied value is never overwritten: an operator who wrote the
    inventory by hand knows about stores this generator cannot see (search
    indexes, warehouses, third-party processors).
    """
    if GDPR_HOST not in selected:
        return module_config
    supplied = (module_config or {}).get(GDPR_HOST) or {}
    if supplied.get("DATA_OWNERS"):
        return module_config

    declarations = owner_declarations(selected, workspace_root)
    if not declarations:
        # Nothing selected owns a store. Emitting an empty map would satisfy
        # nothing — stapel-gdpr's gdpr.E001 fires on exactly that — so leave it
        # unset and let check_required_settings say so, with the fix attached.
        return module_config

    gdpr_config = dict(supplied)
    gdpr_config["DATA_OWNERS"] = derive_data_owners(selected, workspace_root)
    gdpr_config.setdefault("DATA_OWNERS_VERSION", data_owners_version())
    merged = {**(module_config or {}), GDPR_HOST: gdpr_config}
    if verbose:
        print('  STAPEL_GDPR["DATA_OWNERS"] derived from the selected libraries:')
        print(derivation_table(declarations))
    return merged
