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

The three seams, and why they are read from stapel-gdpr's own list
------------------------------------------------------------------
A generated project's map is graded by the very checks that read the
libraries at boot — ``gdpr.E009`` (the host names an owner nothing declares),
``gdpr.E010`` (a library declares an owner the host never lists), ``gdpr.W012``
(a subject the owner erases and the host never names). So this module reads
exactly what :mod:`stapel_gdpr.declarations` reads, in the same order of
authority, and takes the *vocabulary* of the seams — which submodules carry a
declaration, under which constant names — from that module when stapel-gdpr is
installed (see :func:`_seams`). A reader with a list of its own is a reader
that drifts, and the 2026-09-07 fleet incident is what drift costs: a
deployment listing ``profiles`` and ``cdn`` (no library has ever declared
either; the names are ``profile`` and ``media``) and omitting ``video`` and
``agent`` outright, every erasure wrong the same way for months.

1. ``stapel_core.gdpr.register_gdpr_owner(OWNER, SUBJECT_TYPES, erase)`` in the
   library's ``AppConfig.ready()`` — the canonical seam, the only one that
   carries the name AND the subject types AND proof the erasure path is
   subscribed.
2. ``GDPRProvider.section`` registered into ``gdpr_registry`` from ``apps.py``
   — a name, and subject types only when the class spells them out.
   stapel-gdpr's ``gdpr.E002`` fires on a registered provider absent from
   ``DATA_OWNERS``, so omitting these produces a project that fails
   ``manage.py check`` just as surely as an empty map. A bare provider erases
   the account by construction, so its subject list is ``["account"]``.
3. Module constants ``OWNER``/``GDPR_OWNER`` and
   ``SUBJECT_TYPES``/``GDPR_SUBJECT_TYPES`` in ``erasure.py``/``gdpr.py`` — a
   static fact of the package, true whether or not ``ready()`` ran. Read
   UNGATED, exactly as stapel-gdpr reads it: before 0.64.1 this reader first
   demanded ``schemas/consumes/gdpr.erasure.requested.json`` (ADO005's
   detection), so a library carrying the constants without that contract fell
   through to seam 2 and lost its real subject types — ``gdpr.W012``, from
   the generator.

Seams are merged the way stapel-gdpr merges them (:func:`_merge_declaration`):
the first sighting names the owner and its seam, and subject types are filled
in from whichever sighting actually carries them. Three sightings of one owner
is the normal case for a modern library, not a conflict — and a library whose
seams name two DIFFERENT owners really does declare two, which is what the
boot checks see, so both are listed.
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
    _resolve_str_seq,
    _string_env,
    locate_module_dir,
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

#: The canonical registration call, read out of a library's ``apps.py``.
REGISTER_OWNER_FUNC = "register_gdpr_owner"


@dataclass(frozen=True)
class _Seams:
    """The seam vocabulary — stapel-gdpr's when it is installed, else a copy.

    Every field mirrors a module-level constant of
    :mod:`stapel_gdpr.declarations`. Two readers with two hand-maintained
    copies of this list is exactly how a host inventory and the libraries it
    certifies drift apart, so the copy below exists only for the case
    stapel-gdpr is NOT installed — a project that selected no gdpr host, where
    no ``DATA_OWNERS`` is emitted at all — and is pinned to what
    ``stapel-gdpr>=0.5.4`` declares.
    """

    #: ``via`` labels, and the strings ``derivation_table`` prints.
    registration: str = "register_gdpr_owner"
    provider: str = "GDPRProvider.section"
    constant: str = "module constant"
    #: Owner-name constants, most specific first.
    owner_attrs: tuple[str, ...] = ("GDPR_OWNER", "OWNER")
    #: Subject-type constants, most specific first.
    subject_attrs: tuple[str, ...] = ("GDPR_SUBJECT_TYPES", "SUBJECT_TYPES")
    #: Submodules that may carry the static declaration, as file names.
    decl_files: tuple[str, ...] = GDPR_DECL_FILES
    #: True when the vocabulary came from the installed stapel-gdpr.
    from_library: bool = False


def _seams() -> _Seams:
    """stapel-gdpr's own seam vocabulary, or the pinned fallback.

    Imported, never re-derived: ``_OWNER_ATTRS``/``_SUBJECT_ATTRS`` are ordered
    by specificity (``GDPR_OWNER`` beats ``OWNER``) and a reader that reversed
    that order would name a library differently from the check that grades it.
    """
    try:
        from stapel_gdpr import declarations as gdpr_declarations
    except Exception:  # stapel-gdpr absent: no host, so no map to emit anyway
        return _Seams()
    try:
        return _Seams(
            registration=gdpr_declarations.SEAM_REGISTRATION,
            provider=gdpr_declarations.SEAM_PROVIDER,
            constant=gdpr_declarations.SEAM_CONSTANT,
            owner_attrs=tuple(gdpr_declarations._OWNER_ATTRS),
            subject_attrs=tuple(gdpr_declarations._SUBJECT_ATTRS),
            decl_files=tuple(f"{name}.py" for name in gdpr_declarations._ERASURE_MODULES),
            from_library=True,
        )
    except AttributeError:  # an older stapel-gdpr than the declared floor
        return _Seams()


@dataclass(frozen=True)
class OwnerDeclaration:
    """One selected library's answer to "what do you own, and under what name".

    ``via`` records WHICH seam was read — the same label
    :mod:`stapel_gdpr.declarations` uses — so the derivation table the
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


def _parse(path: Path) -> ast.AST | None:
    """Parsed source, or None when the file is absent or unreadable."""
    if not path.is_file():
        return None
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def _decl_trees(mod_dir: Path, seams: _Seams) -> dict[str, ast.AST]:
    """Parsed ``erasure.py``/``gdpr.py`` — the submodules stapel-gdpr imports
    when it looks for the static declaration, in the same order."""
    trees: dict[str, ast.AST] = {}
    for fname in seams.decl_files:
        tree = _parse(mod_dir / fname)
        if tree is not None:
            trees[fname] = tree
    return trees


def _string_environment(trees: list[ast.AST]) -> tuple[dict[str, str], dict[str, str]]:
    """Module constants and class attributes across *trees*, first binding wins.

    Enough to resolve the indirections real owner libraries use:
    ``OWNER = AgentGDPRProvider.section`` (agent, billing) and
    ``register_gdpr_owner(OWNER, SUBJECT_TYPES, ...)`` in ``apps.py``, whose
    two names are imported from ``erasure.py`` next door.
    """
    consts: dict[str, str] = {}
    attrs: dict[str, str] = {}
    for tree in trees:
        tree_consts, tree_attrs = _string_env(tree)
        for key, value in tree_consts.items():
            consts.setdefault(key, value)
        for key, value in tree_attrs.items():
            attrs.setdefault(key, value)
    return consts, attrs


def _merge_declaration(found: dict[str, OwnerDeclaration], decl: OwnerDeclaration) -> None:
    """stapel-gdpr's ``declarations._merge``, on the static reading.

    The first sighting names the owner and the seam it is on; subject types
    and the source file are filled in from whichever sighting carries them. A
    modern library is seen three times over — canonical registration, provider,
    constants — and that is agreement, not conflict.
    """
    existing = found.get(decl.owner)
    if existing is None:
        found[decl.owner] = decl
        return
    found[decl.owner] = OwnerDeclaration(
        module=existing.module,
        owner=existing.owner,
        subject_types=existing.subject_types or decl.subject_types,
        via=existing.via,
        decl=existing.decl or decl.decl,
    )


def _from_registration(
    module: str,
    apps_tree: ast.AST | None,
    seams: _Seams,
    consts: dict[str, str],
    attrs: dict[str, str],
    found: dict[str, OwnerDeclaration],
) -> bool:
    """Seam 1 — ``register_gdpr_owner(OWNER, SUBJECT_TYPES, erase)``.

    Returns whether the call is there AT ALL, which is the difference between
    "this library does not use the canonical seam" and "it does, and the name
    it passes cannot be read here" — the second is a refusal, never a guess.
    """
    if apps_tree is None:
        return False
    called = False
    for node in ast.walk(apps_tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        else:
            continue
        if name != REGISTER_OWNER_FUNC:
            continue
        called = True
        owner = _resolve_str(node.args[0], consts, attrs)
        if not owner:
            continue
        subjects = (
            _resolve_str_seq(node.args[1], consts, attrs) if len(node.args) > 1 else ()
        )
        _merge_declaration(found, OwnerDeclaration(
            module=module, owner=owner, subject_types=subjects,
            via=seams.registration, decl="apps.py",
        ))
    return called


def _registered_provider_classes(mod_dir: Path, apps_tree: ast.AST | None) -> set[str]:
    """Class names this library registers into the in-process gdpr registry.

    Only a name the library itself defines counts. ``stapel_gdpr`` registers
    ``provider_cls()`` — a variable holding whatever ``GDPR_PROVIDERS`` named —
    which resolves to no class here, which is correct: the host owns no store.
    """
    if apps_tree is None:
        return set()
    names: set[str] = set()
    for node in ast.walk(apps_tree):
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


def _from_providers(
    module: str,
    trees: dict[str, ast.AST],
    class_names: set[str],
    seams: _Seams,
    consts: dict[str, str],
    attrs: dict[str, str],
    found: dict[str, OwnerDeclaration],
) -> bool:
    """Seam 2 — ``GDPRProvider.section``, plus ``subject_types`` when spelled out.

    Returns whether a registered name is a class this library DEFINES, which
    separates "registers something it does not own" (not a participant) from
    "owns a store whose name is unreadable" (a refusal).
    """
    class_found = False
    for fname, tree in trees.items():
        for node in getattr(tree, "body", []):
            if not isinstance(node, ast.ClassDef) or node.name not in class_names:
                continue
            class_found = True
            assigned: dict[str, ast.AST] = {}
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign):
                    continue
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        assigned.setdefault(target.id, stmt.value)
            section = _resolve_str(assigned.get("section"), consts, attrs)
            if not section:
                continue
            # stapel_gdpr.declarations reads `provider.subject_types` off the
            # live object; here it is only visible when the class states it.
            subjects = _resolve_str_seq(assigned.get("subject_types"), consts, attrs)
            _merge_declaration(found, OwnerDeclaration(
                module=module, owner=section, subject_types=subjects,
                via=seams.provider, decl=fname,
            ))
    return class_found


def _from_constants(
    module: str,
    trees: dict[str, ast.AST],
    seams: _Seams,
    consts: dict[str, str],
    attrs: dict[str, str],
    found: dict[str, OwnerDeclaration],
) -> None:
    """Seam 3 — ``OWNER``/``GDPR_OWNER`` + ``SUBJECT_TYPES``/``GDPR_SUBJECT_TYPES``.

    Ungated: stapel-gdpr reads these constants off the installed package with
    no regard for whether it also ships the erasure consume-contract, so a
    reader that demanded the contract first (as this one did before 0.64.1)
    reports fewer subject types than the check that grades the result.
    """
    for fname, tree in trees.items():
        assigned: dict[str, ast.AST] = {}
        for node in getattr(tree, "body", []):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned.setdefault(target.id, node.value)
        owner = None
        for attr in seams.owner_attrs:
            if attr in assigned:
                owner = _resolve_str(assigned[attr], consts, attrs)
                if owner:
                    break
        if not owner:
            continue
        subjects: tuple[str, ...] = ()
        for attr in seams.subject_attrs:
            if attr in assigned:
                subjects = _resolve_str_seq(assigned[attr], consts, attrs)
                if subjects:
                    break
        _merge_declaration(found, OwnerDeclaration(
            module=module, owner=owner, subject_types=subjects,
            via=seams.constant, decl=fname,
        ))


def read_owner_declarations(
    module: str, workspace_root: Path | None = None
) -> list[OwnerDeclaration]:
    """Every owner this library declares, by name, across all three seams.

    A list rather than one entry because that is what the boot checks see: a
    library whose provider ``section`` and ``OWNER`` constant disagree really
    does declare two owners, and listing only one of them is ``gdpr.E010`` in
    the generated project.

    Raises ``SystemExit`` when the library plainly PARTICIPATES (it ships the
    erasure consume-contract, calls ``register_gdpr_owner``, or registers a
    provider class of its own) but no seam yields a readable name. There is no
    safe fallback: a guessed name is a store the orchestrator never asks and
    never waits for.
    """
    if module == GDPR_HOST:
        return []
    mod_dir = _module_dir(module, workspace_root)
    if mod_dir is None:
        return []

    seams = _seams()
    trees = _decl_trees(mod_dir, seams)
    apps_tree = _parse(mod_dir / "apps.py")
    consts, attrs = _string_environment(
        list(trees.values()) + ([apps_tree] if apps_tree is not None else [])
    )

    found: dict[str, OwnerDeclaration] = {}
    registers = _from_registration(module, apps_tree, seams, consts, attrs, found)
    classes = _registered_provider_classes(mod_dir, apps_tree)
    class_found = _from_providers(module, trees, classes, seams, consts, attrs, found)
    _from_constants(module, trees, seams, consts, attrs, found)

    if found:
        # An owner whose seam carried no subject types still needs one: a
        # DATA_OWNERS entry with an empty list is a store nothing is ever
        # asked to erase for, and stapel-gdpr reads a bare name as ["account"].
        return [
            decl if decl.subject_types
            else OwnerDeclaration(
                module=decl.module, owner=decl.owner,
                subject_types=(SUBJECT_ACCOUNT,), via=decl.via, decl=decl.decl,
            )
            for _, decl in sorted(found.items())
        ]

    if mod_dir.joinpath(*GDPR_CONSUMES_ERASURE).is_file():
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
    if registers:
        raise SystemExit(
            f"Error: stapel-{module} calls {REGISTER_OWNER_FUNC}() in its "
            f"apps.py but the owner name it passes in {mod_dir} could not be "
            f"read, so this generator cannot name it in "
            f'STAPEL_GDPR["DATA_OWNERS"]. An owner missing from that map is '
            f"never asked to erase and never waited for — the closure reports "
            f"DELETED while the data is still there. Fix the declaration in "
            f"stapel-{module}, or pass the map explicitly via --module-config."
        )
    if class_found:
        raise SystemExit(
            f"Error: stapel-{module} registers an in-process GDPRProvider "
            f"({', '.join(sorted(classes))}) but its `section` in {mod_dir} could "
            f"not be read, so this generator cannot name it in "
            f'STAPEL_GDPR["DATA_OWNERS"]. A registered provider absent from that '
            f"map fails stapel-gdpr's gdpr.E002 at boot, and a store missing from "
            f"the inventory is silent retention. Fix the declaration in "
            f"stapel-{module}, or pass the map explicitly via --module-config."
        )
    return []


def read_owner_declaration(
    module: str, workspace_root: Path | None = None
) -> OwnerDeclaration | None:
    """The library's declaration when it names exactly one owner (the normal
    case), the first by name when it names several, ``None`` when it owns no
    store. See :func:`read_owner_declarations` for the refusals."""
    declarations = read_owner_declarations(module, workspace_root)
    return declarations[0] if declarations else None


def owner_declarations(
    selected: list[str], workspace_root: Path | None = None
) -> list[OwnerDeclaration]:
    """Every selected library that owns personal data, in owner-name order."""
    found: dict[str, OwnerDeclaration] = {}
    for module in selected:
        for decl in read_owner_declarations(module, workspace_root):
            # One name is one owner (stapel_core.gdpr.register_gdpr_owner
            # enforces the same rule at runtime); the richer declaration wins.
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
