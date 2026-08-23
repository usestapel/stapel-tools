"""Per-module STAPEL_<MOD> config rendering + validation for the scaffolders
(capability-config.md §4 p.1, closes §5-A5).

``create_project`` / ``scaffold_service`` accept ``module_config`` — the
CTO-chain output ``{module: {SETTING_KEY: value}}`` (ProjectBrief.modules.config)
— and render ``STAPEL_<MOD> = {…}`` blocks into the generated settings.
Only the PROVIDED (non-default) keys are rendered: defaults stay in each
module's ``conf.py``, the generated settings never duplicate them. No config →
no block, byte-for-byte identical to the previous scaffold output.

Validation seam: when the module's repo is checked out as a workspace sibling
and carries ``docs/capabilities.json`` (the §2 contract artifact), provided
keys are validated against its axes + extension surface — an unknown key is a
hard error carrying the known-key list. A module not yet swept (no
capabilities.json) warns and passes through.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _default_workspace_root() -> Path:
    """The directory module repos are siblings of — the stapel-tools checkout's
    parent in the workspace layout (stapel-tools/, stapel-auth/, … side by side).
    When stapel-tools is pip-installed there are no siblings and every module
    falls back to the warn-and-pass-through path."""
    return Path(__file__).resolve().parent.parent.parent


def capabilities_path(module: str, workspace_root: Path | None = None) -> Path:
    root = workspace_root or _default_workspace_root()
    return root / f"stapel-{module}" / "docs" / "capabilities.json"


def _installed_capabilities_path(module: str) -> Path | None:
    """``docs/capabilities.json`` inside the INSTALLED distribution, if any.

    Every swept module ships the artifact as package data, so a scaffold run
    from a plain ``pip install stapel-tools stapel-gdpr`` — no workspace
    checkout in sight — can still read a module's declarations. Without this
    the required-settings gate below would be silently skipped in exactly the
    environment a real project generates in, which is the shape of defect the
    gate exists to end.
    """
    spec = importlib.util.find_spec(f"stapel_{module.replace('-', '_')}")
    origin = getattr(spec, "origin", None) if spec is not None else None
    if not origin:
        return None
    return Path(origin).resolve().parent / "docs" / "capabilities.json"


def capabilities_doc(module: str, workspace_root: Path | None = None) -> dict | None:
    """The module's ``docs/capabilities.json``, or ``None`` when unreadable.

    Workspace sibling first (a checkout is the newest truth when one exists),
    installed distribution second.
    """
    candidates = [capabilities_path(module, workspace_root)]
    if workspace_root is None:
        installed = _installed_capabilities_path(module)
        if installed is not None:
            candidates.append(installed)
    for path in candidates:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def required_settings(module: str, workspace_root: Path | None = None) -> list[dict]:
    """What installing this module makes MANDATORY (capabilities.json §required_settings).

    A library that raises a boot-fatal system check when a setting is missing
    has a requirement, and a requirement nobody can read is a project that is
    dead on arrival: ``stapel_gdpr`` raises ``gdpr.E001`` when
    ``STAPEL_GDPR["DATA_OWNERS"]`` is empty, and both example apps in this
    workspace were installed with the app and no such setting anywhere.

    Each entry carries enough SHAPE for a generator to emit a correct
    placeholder (``key``, ``kind``, ``example``) and enough PROSE for a human
    to know what to put there (``why``, ``unset_check``).
    """
    doc = capabilities_doc(module, workspace_root)
    if not doc:
        return []
    return [
        entry
        for entry in doc.get("required_settings", [])
        if isinstance(entry, dict) and "key" in entry
    ]


def known_config_keys(module: str, workspace_root: Path | None = None) -> set[str] | None:
    """The module's configurable keys from its capabilities.json, or ``None``
    when the artifact is absent/unreadable (module not yet swept).

    Axes + extension surface + REQUIRED settings. The last one is not a
    refinement: without it ``validate_module_config`` hard-rejected the very
    keys a module declares mandatory — a caller supplying ``DATA_OWNERS`` was
    refused because it is not an axis, so the scaffold could not have fixed
    the dead-on-arrival project even if it tried.
    """
    doc = capabilities_doc(module, workspace_root)
    if doc is None:
        return None
    axes = {a["key"] for a in doc.get("axes", []) if isinstance(a, dict) and "key" in a}
    extensions = {
        e["name"]
        for e in doc.get("extension_points", [])
        if isinstance(e, dict) and "name" in e
    }
    required = {entry["key"] for entry in required_settings(module, workspace_root)}
    return axes | extensions | required


def validate_module_config(
    module_config: dict[str, dict] | None,
    *,
    selected: list[str] | None = None,
    workspace_root: Path | None = None,
) -> None:
    """Validate a ``{module: {SETTING_KEY: value}}`` mapping.

    - a module that is not among the ``selected`` project modules is a hard
      error (config for an unmounted module is dead weight);
    - a key unknown to the module's capabilities.json axes+extension surface
      is a hard error listing the known keys;
    - a module without a sibling capabilities.json warns and passes through.
    """
    if not module_config:
        return
    for module, config in module_config.items():
        if not isinstance(config, dict):
            raise SystemExit(
                f"Error: module_config[{module!r}] must be a mapping of "
                f"SETTING_KEY -> value, got {type(config).__name__}"
            )
        if selected is not None and module not in selected:
            raise SystemExit(
                f"Error: module_config names '{module}', which is not among the "
                f"project's modules ({', '.join(sorted(selected)) or 'none'}) — "
                "config for an unmounted module is dead weight"
            )
        known = known_config_keys(module, workspace_root)
        if known is None:
            print(
                f"  Warning: {capabilities_path(module, workspace_root)} not found "
                f"— cannot validate STAPEL_{module.replace('-', '_').upper()} keys "
                "(module not swept yet?); passing them through as given.",
                file=sys.stderr,
            )
            continue
        unknown = [key for key in config if key not in known]
        if unknown:
            raise SystemExit(
                f"Error: unknown STAPEL_{module.replace('-', '_').upper()} key(s) "
                f"{', '.join(sorted(unknown))} — not in stapel-{module}'s "
                "capabilities.json axes/extension surface. Known keys: "
                + ", ".join(sorted(known))
            )


#: Placeholder value per declared ``kind``, for a module that declares a
#: required setting without an ``example``. The generator must be able to emit
#: something of the right SHAPE even then — a placeholder of the wrong type is
#: a second defect layered on the first.
_PLACEHOLDER_BY_KIND = {
    "list": [],
    "dict": {},
    "str": "",
    "string": "",
    "bool": False,
    "int": 0,
    "secret": "",
}


def _placeholder_for(entry: dict):
    if "example" in entry:
        return entry["example"]
    return _PLACEHOLDER_BY_KIND.get(entry.get("kind", "str"), "")


def render_required_placeholder_block(module: str, entries: list[dict]) -> str:
    """A paste-ready ``STAPEL_<MOD> = {...}`` block for unsatisfied requirements.

    The refusal below is only actionable if it hands back the thing that fixes
    it. Shape comes from ``kind``/``example``, the ``# why`` comment from the
    declaration's prose.
    """
    mod_u = module.replace("-", "_")
    lines = [f"STAPEL_{mod_u.upper()} = {{"]
    for entry in entries:
        why = entry.get("why")
        if why:
            lines.append(f"    # {why}")
        lines.append(f'    "{entry["key"]}": {_placeholder_for(entry)!r},')
    lines.append("}")
    return "\n".join(lines)


def check_required_settings(
    selected: list[str],
    module_config: dict[str, dict] | None,
    *,
    workspace_root: Path | None = None,
) -> None:
    """Refuse to generate a project whose libraries are dead on arrival.

    ``stapel_gdpr`` raises the boot-fatal ``gdpr.E001`` when
    ``STAPEL_GDPR["DATA_OWNERS"]`` is empty; the scaffold installed the app
    and never emitted the setting, so BOTH example apps in this workspace
    could not run ``manage.py check``. The failure belongs at generation time,
    where the person who chose the library is still in the room — not at first
    boot in production, where the only signal is a service that will not start.

    A module that declares nothing is passed through: silence in
    capabilities.json means "this library has no mandatory settings", which is
    true of most of them.
    """
    provided = module_config or {}
    problems: list[str] = []
    for module in selected:
        entries = required_settings(module, workspace_root)
        if not entries:
            continue
        supplied = provided.get(module) or {}
        missing = [
            entry for entry in entries
            if entry["key"] not in supplied
            or supplied[entry["key"]] in (None, "", [], {}, ())
        ]
        if not missing:
            continue
        names = ", ".join(entry["key"] for entry in missing)
        detail = "\n".join(
            f"    - {entry['key']}: {entry.get('why', 'declared required by the module')}"
            + (f" (unset → {entry['unset_check']})" if entry.get("unset_check") else "")
            for entry in missing
        )
        problems.append(
            f"stapel-{module} declares {names} required, and this project supplies "
            f"no value:\n{detail}\n"
            f"  Installing the app without it produces a project that fails "
            f"`manage.py check` on first boot. Supply it via module_config "
            f"(--module-config), e.g.:\n\n"
            + "\n".join(
                "    " + line
                for line in render_required_placeholder_block(module, missing).splitlines()
            )
        )
    if problems:
        raise SystemExit(
            "Error: required module settings are missing.\n\n" + "\n\n".join(problems)
        )


#: Prose the GENERATED settings must carry for keys whose consequence is not
#: visible from the value. A reader who opens settings.py has to be able to
#: tell what happens if the value is wrong — and for the gdpr inventory, "wrong"
#: means an erasure that certifies itself while the data is still on disk.
_SETTING_COMMENTS: dict[tuple[str, str], tuple[str, ...]] = {
    ("gdpr", "DATA_OWNERS"): (
        "Every store that holds personal data, and the subject types it can",
        "erase. Derived at generation time from the libraries this project",
        "selected: each one declares its own owner name and subjects (see",
        "stapel-<lib>/erasure.py or gdpr.py) — this map is a fact about the",
        "selection, not a preference.",
        "The law: an erasure is only proven complete when EVERY store listed",
        "here returns a receipt. A store that holds personal data and is",
        "missing from this map is never asked and never waited for, so the",
        "closure reports DELETED while the data is still there — silent",
        "retention, with a receipt saying otherwise.",
        "So add an entry for every store stapel-tools cannot see (search",
        "indexes, warehouses, third-party processors, your own apps), and",
        "bump DATA_OWNERS_VERSION when you do.",
    ),
    ("gdpr", "DATA_OWNERS_VERSION"): (
        "Stamped onto every closure, so an audit can tell which inventory",
        "certified a given erasure. Bump it whenever DATA_OWNERS changes.",
    ),
}


def _setting_comment(module: str, key: str) -> tuple[str, ...]:
    return _SETTING_COMMENTS.get((module, key), ())


def _render_value(value, indent: int = 4) -> str:
    """``repr`` for a short value; one entry per line for a mapping that would
    run off the page (the derived DATA_OWNERS map is a dozen entries wide, and
    a settings file nobody can read is a settings file nobody edits)."""
    text = repr(value)
    if len(text) + indent + 8 <= 88 or not isinstance(value, dict) or not value:
        return text
    pad = " " * (indent + 4)
    body = "".join(f"{pad}{k!r}: {v!r},\n" for k, v in value.items())
    return "{\n" + body + " " * indent + "}"


def render_settings_block(module_config: dict[str, dict] | None) -> str:
    """Render the ``{{STAPEL_MODULE_CONFIG}}`` settings fragment.

    Empty/None → ``""`` (the templates render byte-identically to the
    pre-module_config scaffold). Otherwise a leading blank line plus one
    ``STAPEL_<MOD> = {…}`` block per module (sorted by module; keys in the
    provided order), each with a comment pointing at the module's
    docs/capabilities.json for the full axis list.
    """
    if not module_config:
        return ""
    parts: list[str] = []
    for module in sorted(module_config):
        config = module_config[module]
        if not config:
            continue
        mod_u = module.replace("-", "_")
        lines = [
            f"# {module}: non-default capability axes only — defaults live in "
            f"stapel_{mod_u}/conf.py;",
            f"# the full axis list is stapel-{module}/docs/capabilities.json "
            "(emitted by `make contract`).",
            f"STAPEL_{mod_u.upper()} = {{",
        ]
        for key, value in config.items():
            lines.extend(f"    # {line}" for line in _setting_comment(module, key))
            lines.append(f'    "{key}": {_render_value(value)},')
        lines.append("}")
        parts.append("\n".join(lines))
    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts)


def load_module_config_file(path: Path) -> dict[str, dict]:
    """Load a ``--module-config`` JSON file ({module: {SETTING_KEY: value}})."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Error: cannot read module config {path}: {exc}") from exc
    except ValueError as exc:
        raise SystemExit(f"Error: module config {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not all(
        isinstance(v, dict) for v in data.values()
    ):
        raise SystemExit(
            f"Error: module config {path} must be a JSON object of the shape "
            '{"<module>": {"SETTING_KEY": value, ...}, ...}'
        )
    return data
