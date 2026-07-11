"""
CONFIG.MD manifest — parse + aggregate (static-scaffold-and-config.md §2).

Two consumers share this module:

* the **advisor** (§1.2) asks ``aggregate_config_md(libs)`` for the union of
  the CONFIG.MD registries the selected stapel libs ship — that is the
  project's config surface, what it must ask the client for and what
  boot-smoke will check;
* ``assemble_scaffold`` (§1.3) writes that aggregate into the generated
  project's root ``CONFIG.MD`` and runs ``config_lint`` (CFG001–CFG003) over
  the result in its verify pass.

The format is the one ``stapel_core.config.get_config`` routes on — a GFM table
per ``## <owner>`` section:

    ## stapel-core
    | Key | Source | Purpose | Required | Default |
    |-----|--------|---------|----------|---------|
    | SECRET_KEY | vault | Django secret | yes | |

This module keeps its own parser (stapel-tools carries no runtime deps and must
install without stapel-core); the two parsers agree on the format by contract.

Per-module CONFIG.MD sweep of all ~19 libs is the NEXT wave — today only
stapel-core ships one. A lib without a CONFIG.MD is a reported gap
(``missing``), never a crash: the mechanism is here, the content follows.
"""
from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

CONFIG_MD = "CONFIG.MD"
SOURCE_ENV = "env"
SOURCE_VAULT = "vault"
_SOURCES = (SOURCE_ENV, SOURCE_VAULT)

_TRUTHY = {"yes", "y", "true", "1", "да", "required", "req"}
_NO_DEFAULT = {"", "-", "—", "–", "none", "n/a", "нет"}


class ConfigManifestError(Exception):
    """A CONFIG.MD row is malformed (unknown source, …)."""


@dataclass
class ConfigEntry:
    key: str
    source: str
    purpose: str = ""
    required: bool = False
    default: Optional[str] = None
    owner: Optional[str] = None
    line: int = 0

    @property
    def library_owned(self) -> bool:
        """A key owned by a stapel lib (read inside the lib) — exempt from the
        CFG003 'declared but never read in the project' rule."""
        return bool(self.owner) and self.owner.lower().startswith("stapel-")


# --- parsing ----------------------------------------------------------------


_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


def _cell(value: str) -> str:
    return value.replace("\\|", "|").strip().strip("`").strip()


def _split_row(line: str) -> list[str]:
    parts = _UNESCAPED_PIPE.split(line.strip())
    # Border pipes yield a leading/trailing length-0 element; drop only those
    # (a genuinely empty cell between pipes carries at least a space).
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def _is_separator_row(cells: list[str]) -> bool:
    stripped = [c.strip() for c in cells]
    return bool(stripped) and all(
        set(c) <= {"-", ":"} and "-" in c for c in stripped if c
    ) and any(c for c in stripped)


def _parse_bool(value: str) -> bool:
    return _cell(value).lower() in _TRUTHY


def _parse_default(value: str) -> Optional[str]:
    token = _cell(value)
    return None if token.lower() in _NO_DEFAULT else token


def parse_config_md(source: str | Path, *, path_label: str | None = None) -> list[ConfigEntry]:
    """Parse a CONFIG.MD (path or text) into an ordered list of entries."""
    if isinstance(source, Path):
        text = source.read_text(encoding="utf-8")
        label = path_label or str(source)
    elif isinstance(source, str) and "\n" not in source and source.upper().endswith(".MD"):
        p = Path(source)
        text = p.read_text(encoding="utf-8")
        label = path_label or str(p)
    else:
        text = str(source)
        label = path_label or "<config.md>"

    entries: list[ConfigEntry] = []
    owner: Optional[str] = None
    header: Optional[list[str]] = None
    col: dict[str, int] = {}

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            heading = line.lstrip("#").strip()
            if level == 2:
                owner = heading or None
            elif level == 1:
                owner = None
            header = None
            continue
        if not line.startswith("|"):
            header = None
            continue

        cells = _split_row(line)
        if _is_separator_row(cells):
            continue
        lowered = [c.strip().lower() for c in cells]
        if header is None:
            if "key" in lowered and "source" in lowered:
                header = lowered
                col = {name: i for i, name in enumerate(header)}
            continue

        def _get(name: str, _cells=cells, _col=col) -> str:
            i = _col.get(name)
            return _cells[i] if i is not None and i < len(_cells) else ""

        key = _cell(_get("key"))
        if not key:
            continue
        source_val = _cell(_get("source")).lower()
        if source_val not in _SOURCES:
            raise ConfigManifestError(
                f"{label}:{lineno}: config key {key!r} has source {source_val!r}; "
                f"expected one of {', '.join(_SOURCES)}."
            )
        entries.append(ConfigEntry(
            key=key,
            source=source_val,
            purpose=_cell(_get("purpose")),
            required=_parse_bool(_get("required")),
            default=_parse_default(_get("default")),
            owner=owner,
            line=lineno,
        ))
    return entries


# --- lib CONFIG.MD location -------------------------------------------------


def _default_workspace_root() -> Path:
    """Directory the stapel-<lib> repos are siblings of (the stapel-tools
    checkout's parent in the workspace layout)."""
    return Path(__file__).resolve().parent.parent.parent


def locate_lib_config_md(lib: str, workspace_root: Path | None = None) -> Optional[Path]:
    """CONFIG.MD of ``stapel-<lib>``: a workspace sibling repo first, then the
    installed ``stapel_<lib>`` package directory. None when neither has one."""
    root = workspace_root or _default_workspace_root()
    sibling = root / f"stapel-{lib}" / CONFIG_MD
    if sibling.is_file():
        return sibling
    try:
        spec = importlib.util.find_spec(f"stapel_{lib.replace('-', '_')}")
    except (ImportError, ValueError, ModuleNotFoundError):
        spec = None
    if spec is not None and spec.submodule_search_locations:
        cand = Path(list(spec.submodule_search_locations)[0]) / CONFIG_MD
        if cand.is_file():
            return cand
    return None


# --- aggregation + rendering ------------------------------------------------


def collect_lib_entries(
    libs: Iterable[str], workspace_root: Path | None = None
) -> tuple[list[ConfigEntry], list[str]]:
    """Union of the selected libs' CONFIG.MD entries (owner defaulted to
    ``stapel-<lib>``) + the list of libs that ship no CONFIG.MD yet."""
    entries: list[ConfigEntry] = []
    missing: list[str] = []
    seen: set[str] = set()
    for lib in libs:
        path = locate_lib_config_md(lib, workspace_root)
        if path is None:
            missing.append(lib)
            continue
        for entry in parse_config_md(path):
            if entry.owner is None:
                entry.owner = f"stapel-{lib}"
            if entry.key in seen:
                continue  # first declaring lib wins (core before features)
            seen.add(entry.key)
            entries.append(entry)
    return entries, missing


def render_config_md(entries: Iterable[ConfigEntry], *, title: str = "CONFIG.MD") -> str:
    """Render entries back to a CONFIG.MD, grouped by owner (declaration order
    preserved within and across owners)."""
    entries = list(entries)
    lines = [
        f"# {title}",
        "",
        "Generated config registry (static-scaffold-and-config.md §2): one row",
        "per key, its source (`env` = process environment; `vault` = the",
        "`stapel_core.secrets` provider seam), purpose, whether it is required,",
        "and its default. `get_config(key)` routes reads here; the `config-lint`",
        "gate (CFG001-CFG003) keeps this file and the settings in sync.",
        "",
    ]
    order: list[str] = []
    by_owner: dict[str, list[ConfigEntry]] = {}
    for entry in entries:
        owner = entry.owner or "project"
        if owner not in by_owner:
            by_owner[owner] = []
            order.append(owner)
        by_owner[owner].append(entry)

    for owner in order:
        lines.append(f"## {owner}")
        lines.append("")
        lines.append("| Key | Source | Purpose | Required | Default |")
        lines.append("|-----|--------|---------|----------|---------|")
        for e in by_owner[owner]:
            req = "yes" if e.required else "no"
            default = (e.default or "").replace("|", "\\|")
            purpose = (e.purpose or "").replace("|", "\\|")
            lines.append(f"| {e.key} | {e.source} | {purpose} | {req} | {default} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def aggregate_config_md(
    libs: Iterable[str],
    workspace_root: Path | None = None,
    *,
    title: str = "CONFIG.MD",
    extra_entries: Iterable[ConfigEntry] = (),
) -> tuple[str, list[str]]:
    """Aggregate the CONFIG.MD of the selected libs (+ optional project-owned
    ``extra_entries``) into one rendered CONFIG.MD. Returns (text, missing_libs)."""
    entries, missing = collect_lib_entries(libs, workspace_root)
    covered = {e.key for e in entries}
    for extra in extra_entries:
        if extra.key not in covered:
            entries.append(extra)
            covered.add(extra.key)
    return render_config_md(entries, title=title), missing
