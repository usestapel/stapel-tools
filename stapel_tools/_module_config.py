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


def known_config_keys(module: str, workspace_root: Path | None = None) -> set[str] | None:
    """The module's axes + extension surface from its capabilities.json, or
    ``None`` when the artifact is absent/unreadable (module not yet swept)."""
    path = capabilities_path(module, workspace_root)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    axes = {a["key"] for a in doc.get("axes", []) if isinstance(a, dict) and "key" in a}
    extensions = {
        e["name"]
        for e in doc.get("extension_points", [])
        if isinstance(e, dict) and "name" in e
    }
    return axes | extensions


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
            lines.append(f'    "{key}": {value!r},')
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
