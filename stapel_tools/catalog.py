"""
stapel-catalog — aggregate the module catalog from every module's
``docs/capabilities.json`` (BACKLOG §33 p.1, capability-config.md §1-§2).

Each Stapel module already emits, as its FOURTH per-module contract artifact,
a drift-gated ``docs/capabilities.json`` (see :mod:`stapel_tools.capabilities`):
a ``provides`` one-liner, the CTO-facing ``axes`` with their per-operation
gates, ``extension_points`` and ``requires``. This tool is the read side: it
gathers those documents across a workspace (or an explicit list of repos) and
projects them into two catalog artifacts:

* ``catalog.json`` — the full machine aggregate: every source document verbatim
  plus roll-up totals (and the curated recipes, if any);
* ``catalog.md``   — a COMPACT, prompt-ready projection meant to drop into a
  system prompt: a header with the roll-up, then one section per module
  (name, version, ``provides`` one-liner, an axis table `key | default |
  ops gated`, extension-point names, requires) and, if supplied, a curated
  ``recipes`` section.

Both outputs are DETERMINISTIC — modules are sorted by name, axes by key, and
no timestamps or environment-dependent values are emitted — so two runs over
the same inputs are byte-for-byte identical (catalog.md is an artifact that
gets committed into other repos' prompts, so stability matters).

Curated recipes
---------------
A "recipe" is a composite projection — a product shape that is really N modules
stacked (a marketplace, a booking app, ...). Recipes are curated, not derived,
so they live in a separate ``recipes.yaml`` passed via ``--recipes`` and render
as their own catalog section. The minimal schema (a restricted YAML subset — a
top-level ``recipes:`` list of mappings, so the tool stays dependency-free)::

    recipes:
      - name: marketplace                       # required, short slug/title
        summary: Two-sided marketplace ...       # required, one line
        modules: [stapel-auth, stapel-profiles]  # required, list of module names
        notes: reviews via a separate module     # optional, one line

``modules`` also accepts a block list (``- stapel-auth`` on its own lines).
``summary`` and ``notes`` are single-line scalars (optionally quoted). Unknown
keys are ignored. A malformed recipes file is a loud error (unlike a malformed
capabilities.json, which is skipped with a warning) — recipes are hand-curated
input, not discovered artifacts.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CATALOG_SCHEMA_VERSION = 1

#: Markdown table separator row (``|---|---|`` etc.) — only dashes/colons per
#: cell once split on ``|``. Used to filter CONFIG.MD table rows down to real
#: data rows (agent-knowledge-base.md §64 "Волна 1").
_TABLE_SEP_CHARS = set("-: ")


# ---------------------------------------------------------------------------
# stable serialization (same pinning as stapel_tools.codegen / .capabilities)
# ---------------------------------------------------------------------------


def _stable_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, separators=(",", ": ")) + "\n"


# ---------------------------------------------------------------------------
# source discovery + loading
# ---------------------------------------------------------------------------


def capabilities_path(source: Path) -> Path:
    """Resolve a positional source to its ``capabilities.json``.

    A source is either the repo directory of a module (its
    ``docs/capabilities.json`` is used) or a direct path to a
    ``capabilities.json`` file.
    """
    if source.is_file():
        return source
    return source / "docs" / "capabilities.json"


def discover_workspace(workspace: Path) -> list[Path]:
    """Every ``stapel-*/docs/capabilities.json`` under a workspace, sorted."""
    return sorted(
        p
        for p in workspace.glob("stapel-*/docs/capabilities.json")
        if p.is_file()
    )


def load_documents_with_roots(
    sources: list[Path],
    *,
    warn=lambda msg: print(msg, file=sys.stderr),
) -> tuple[list[tuple[dict, Path]], list[str]]:
    """Like :func:`load_documents`, but also pairs each loaded document with
    its module repo root (``<repo>/docs/capabilities.json``'s grandparent) —
    needed by :func:`build_index` to find that module's sibling
    ``flows.json``/``errors.json``/``CONFIG.MD``. Returns
    ``([(doc, repo_root), ...], skipped_labels)``; skip semantics are
    identical to :func:`load_documents` (never a crash).
    """
    pairs: list[tuple[dict, Path]] = []
    skipped: list[str] = []
    for source in sources:
        path = capabilities_path(source)
        label = str(source)
        if not path.is_file():
            warn(f"stapel-catalog: warning: no capabilities.json for {label} "
                 f"(looked at {path}) — skipped")
            skipped.append(label)
            continue
        try:
            doc = json.loads(path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            warn(f"stapel-catalog: warning: {path} is not valid JSON ({exc}) "
                 "— skipped")
            skipped.append(label)
            continue
        if not isinstance(doc, dict) or not doc.get("module"):
            warn(f"stapel-catalog: warning: {path} lacks a 'module' field "
                 "— not a capabilities document, skipped")
            skipped.append(label)
            continue
        # path is always "<repo>/docs/capabilities.json", whether `source` was
        # the repo dir or the file itself (capabilities_path returns a file
        # source unchanged) — so the grandparent is always the repo root.
        pairs.append((doc, path.parent.parent))
    return pairs, skipped


def load_documents(
    sources: list[Path],
    *,
    warn=lambda msg: print(msg, file=sys.stderr),
) -> tuple[list[dict], list[str]]:
    """Load capabilities documents from sources; return (docs, skipped labels).

    A source without the artifact, or with malformed JSON, is skipped with a
    warning (never a crash) — a partial catalog is more useful than none.
    """
    pairs, skipped = load_documents_with_roots(sources, warn=warn)
    return [doc for doc, _root in pairs], skipped


# ---------------------------------------------------------------------------
# minimal recipes YAML parser (restricted subset — keeps the package dep-free)
# ---------------------------------------------------------------------------


def _strip_comment(line: str) -> str:
    """Drop an unquoted trailing/whole-line ``#`` comment."""
    out = []
    quote = None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out)


def _scalar(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    return raw


def _inline_list(raw: str) -> list[str]:
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return []
    return [_scalar(item) for item in inner.split(",") if item.strip()]


def parse_recipes(text: str) -> list[dict]:
    """Parse the restricted-subset recipes YAML into a list of recipe dicts.

    Supports the documented shape only: a top-level ``recipes:`` list whose
    items are mappings with single-line scalars and inline (``[a, b]``) or
    block (``- a``) lists. Raises ``SystemExit`` on a structurally broken file.
    """
    lines = text.splitlines()
    # find the `recipes:` anchor
    idx = 0
    while idx < len(lines):
        stripped = _strip_comment(lines[idx]).rstrip()
        if not stripped.strip():
            idx += 1
            continue
        if stripped.strip() == "recipes:" or stripped.strip().startswith("recipes:"):
            # allow inline empty; the list follows on subsequent lines
            idx += 1
            break
        raise SystemExit(
            f"stapel-catalog: recipes file must start with a top-level "
            f"'recipes:' key, got {stripped.strip()!r}"
        )
    else:
        return []

    recipes: list[dict] = []
    current: dict | None = None
    pending_list_key: str | None = None

    for raw_line in lines[idx:]:
        line = _strip_comment(raw_line).rstrip()
        if not line.strip():
            continue
        body = line.strip()

        # block-list item feeding the most recent `key:` with an empty value
        if body.startswith("- ") or body == "-":
            item = body[1:].strip()
            if pending_list_key is not None and current is not None and ":" not in item:
                if item:
                    current.setdefault(pending_list_key, []).append(_scalar(item))
                continue
            # otherwise: start a new recipe mapping
            current = {}
            recipes.append(current)
            pending_list_key = None
            if not item:
                continue
            key, _, value = item.partition(":")
            _assign(current, key.strip(), value)
            pending_list_key = key.strip() if value.strip() == "" else None
            continue

        if current is None:
            raise SystemExit(
                f"stapel-catalog: recipes entry expected a '- ' list item, "
                f"got {body!r}"
            )

        # a key: value pair on the current recipe
        if ":" not in body:
            raise SystemExit(
                f"stapel-catalog: malformed recipe line {body!r} "
                "(expected 'key: value')"
            )
        key, _, value = body.partition(":")
        _assign(current, key.strip(), value)
        pending_list_key = key.strip() if value.strip() == "" else None

    return recipes


def _assign(recipe: dict, key: str, value: str) -> None:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        recipe[key] = _inline_list(value)
    elif value == "":
        # block list or empty scalar — leave for block-list feeding / default
        recipe.setdefault(key, [])
    else:
        recipe[key] = _scalar(value)


def load_recipes(path: Path) -> list[dict]:
    """Load + validate recipes from a file; normalize to the canonical shape."""
    recipes = parse_recipes(path.read_text())
    normalized = []
    for i, r in enumerate(recipes):
        name = r.get("name")
        if not name:
            raise SystemExit(
                f"stapel-catalog: recipe #{i + 1} in {path} has no 'name'"
            )
        modules = r.get("modules") or []
        if isinstance(modules, str):
            modules = [modules]
        normalized.append(
            {
                "name": str(name),
                "summary": str(r.get("summary", "")),
                "modules": [str(m) for m in modules],
                "notes": str(r.get("notes", "")),
            }
        )
    return sorted(normalized, key=lambda r: r["name"])


# ---------------------------------------------------------------------------
# catalog assembly
# ---------------------------------------------------------------------------


def _http_axis_gate_count(axis: dict) -> int:
    gates = axis.get("gates") or {}
    return len(gates.get("operations") or [])


def build_catalog(docs: list[dict], recipes: list[dict] | None = None) -> dict:
    """Assemble the full aggregate document from loaded capabilities docs."""
    modules = sorted(docs, key=lambda d: d.get("module", ""))
    totals = {
        "modules": len(modules),
        "operations": sum(int(d.get("operations_total") or 0) for d in modules),
        "axes": sum(len(d.get("axes") or []) for d in modules),
        "extension_points": sum(len(d.get("extension_points") or []) for d in modules),
    }
    catalog: dict = {
        "generated_by": "stapel-catalog",
        "schema_version": CATALOG_SCHEMA_VERSION,
        "totals": totals,
        "modules": modules,
    }
    if recipes is not None:
        catalog["recipes"] = recipes
    return catalog


# ---------------------------------------------------------------------------
# full machine index (agent-knowledge-base.md §64 "Волна 1") — extends the
# capabilities aggregate above with the other per-module artifacts an ADVISOR
# exact-layer query needs, per module: flows.json (verbatim), errors.json
# (verbatim), CONFIG.MD table rows, the STAPEL_LIBS registry projection
# (url_prefix/requires/pin) and, for modules with a published `-react`
# sibling, a components projection (operations/hooks/demos) of its
# manifest.json. This is the CONSUMER-FACING shape `studio_cto.advisor_index`
# already documents and reads (see that module's docstring + build_advisor_
# fixture.py in stapel-studio) — every field name/shape here is load-bearing,
# not cosmetic.
# ---------------------------------------------------------------------------


def _load_json_list(
    path: Path, *, warn=lambda msg: print(msg, file=sys.stderr)
) -> list:
    """A sibling JSON-list artifact (flows.json/errors.json), or ``[]`` for an
    absent/malformed one — an honest gap, never a crash (matches
    :func:`load_documents`'s degrade discipline)."""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        warn(f"stapel-catalog: warning: {path} is not valid JSON ({exc}) "
             "— treated as empty")
        return []
    if not isinstance(data, list):
        warn(f"stapel-catalog: warning: {path} does not contain a JSON list "
             "— treated as empty")
        return []
    return data


def _is_table_separator_row(cells: list[str]) -> bool:
    return all(not cell or set(cell) <= _TABLE_SEP_CHARS for cell in cells)


def load_config_lines(repo_root: Path) -> list[str]:
    """Raw ``| ... |`` table-row lines from a module's ``CONFIG.MD`` (its
    config-key registry, static-scaffold-and-config.md §2) — the header row
    and the ``|---|---|`` separator are dropped, every real data row is kept
    verbatim (source/purpose/required/default all live in the row text; no
    need to re-parse columns for a prompt-ready projection). ``[]`` for a
    module with no CONFIG.MD yet (a real, common gap on today's disk)."""
    path = repo_root / "CONFIG.MD"
    if not path.is_file():
        return []
    lines: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        if cells[0].lower() == "key":  # header row
            continue
        if _is_table_separator_row(cells):
            continue
        lines.append(line)
    return lines


def stapel_libs_entry(module_name: str) -> dict | None:
    """Project a ``STAPEL_LIBS`` registry entry (``create_project.py``) for a
    capabilities-doc module name (``stapel-auth`` -> registry key ``auth``).
    ``None`` for a module the registry doesn't (yet) carry — an honest gap,
    never a ``KeyError``; the caller omits the key entirely in that case."""
    from stapel_tools.create_project import STAPEL_LIBS

    entry = STAPEL_LIBS.get(module_name.removeprefix("stapel-"))
    if entry is None:
        return None
    return {
        "url_prefix": entry.get("url_prefix"),
        "requires": list(entry.get("requires") or []),
        "pin": entry.get("pin"),
    }


def load_components(module_name: str, react_root: Path) -> dict | None:
    """Slim projection of the matching ``-react`` sibling package's
    ``manifest.json`` (operations/hooks/demos) — ``None`` when the workspace
    has no such package (most modules today; only 7/19 have shipped a
    ``-react`` pair as of §64's audit) so the caller omits ``components``
    entirely rather than emit a fabricated empty shell."""
    bare = module_name.removeprefix("stapel-")
    manifest_path = react_root / "packages" / f"{bare}-react" / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None

    operations = manifest.get("operations")
    hooks = manifest.get("hooks")
    demos = [d for d in (manifest.get("demos") or []) if isinstance(d, dict)]
    return {
        "package": manifest.get("package"),
        "version": manifest.get("version"),
        "operations": sorted(operations.keys()) if isinstance(operations, dict)
        else sorted(operations or []),
        "hooks": sorted(hooks.keys()) if isinstance(hooks, dict) else sorted(hooks or []),
        "demos": sorted(
            (
                {
                    "id": d.get("id"),
                    "title": d.get("title"),
                    "component": d.get("component"),
                    "flow": d.get("flow"),
                    "source": d.get("source"),
                }
                for d in demos
            ),
            key=lambda d: d["id"] or "",
        ),
    }


def build_index(
    sources: list[Path],
    *,
    recipes: list[dict] | None = None,
    react_root: Path | None = None,
    warn=lambda msg: print(msg, file=sys.stderr),
) -> tuple[dict, list[str]]:
    """Assemble the FULL machine index (agent-knowledge-base.md §64 "Волна
    1"): :func:`build_catalog`'s aggregate, with every module extended by
    ``flows`` (verbatim ``docs/flows.json``, ``[]`` if undocumented — an
    honest gap, e.g. billing/recordings ship an empty file today), ``errors``
    (verbatim ``docs/errors.json``), ``config_md`` (CONFIG.MD table rows,
    key omitted if the module has none yet), ``stapel_libs`` (the
    url_prefix/requires/pin registry projection, key omitted for a module the
    registry doesn't carry) and, when ``react_root`` locates a matching
    ``-react`` sibling, ``components`` (operations/hooks/demos).

    Returns ``(index, skipped_labels)`` — same skip semantics as
    :func:`load_documents`.
    """
    pairs, skipped = load_documents_with_roots(sources, warn=warn)
    docs = [doc for doc, _root in pairs]
    root_by_module = {doc.get("module"): root for doc, root in pairs}

    index = build_catalog(docs, recipes=recipes)
    for doc in index["modules"]:
        name = doc.get("module")
        root = root_by_module.get(name)

        doc["flows"] = _load_json_list(root / "docs" / "flows.json", warn=warn) if root else []
        doc["errors"] = _load_json_list(root / "docs" / "errors.json", warn=warn) if root else []

        config_lines = load_config_lines(root) if root else []
        if config_lines:
            doc["config_md"] = config_lines

        libs_entry = stapel_libs_entry(name) if name else None
        if libs_entry is not None:
            doc["stapel_libs"] = libs_entry

        components = load_components(name, react_root) if name and react_root else None
        if components is not None:
            doc["components"] = components

    return index, skipped


# ---------------------------------------------------------------------------
# markdown rendering (compact, prompt-ready, deterministic)
# ---------------------------------------------------------------------------


def _md_default(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(str(v) for v in value) + "]"
    return str(value)


def _md_requires(requires: list[dict]) -> str:
    parts = []
    for req in requires:
        name = req.get("module", "?")
        parts.append(f"{name} (optional)" if req.get("optional") else name)
    return ", ".join(parts) if parts else "—"


def render_module_section(doc: dict) -> list[str]:
    module = doc.get("module", "?")
    version = doc.get("version", "?")
    lines = [f"### {module} {version}", ""]
    provides = (doc.get("provides") or "").strip()
    if provides:
        lines += [provides, ""]

    axes = sorted(doc.get("axes") or [], key=lambda a: a.get("key", ""))
    if axes:
        lines += ["| axis | default | ops gated |", "|---|---|---|"]
        for axis in axes:
            count = _http_axis_gate_count(axis)
            if count:
                gated = str(count)
            elif (axis.get("gates") or {}).get("behavior"):
                gated = "behavior"
            else:
                gated = "0"
            lines.append(
                f"| {axis.get('key', '?')} | {_md_default(axis.get('default'))} "
                f"| {gated} |"
            )
        lines.append("")

    eps = doc.get("extension_points") or []
    ep_names = [ep.get("name", "?") for ep in eps]
    lines.append(f"**Extension points:** {', '.join(ep_names) if ep_names else '—'}")
    lines.append("")
    lines.append(f"**Requires:** {_md_requires(doc.get('requires') or [])}")
    lines.append("")

    # §64 index-mode extensions (absent entirely in plain-catalog mode, so
    # these lines only appear when build_index populated the keys — plain
    # catalog.md stays byte-identical to before this projection existed).
    if "flows" in doc:
        flows = doc.get("flows") or []
        if flows:
            titles = [f.get("title") or f.get("id", "?") for f in flows if isinstance(f, dict)]
            lines.append(f"**Flows:** {', '.join(titles)}")
        else:
            lines.append("**Flows:** — (not documented)")
        lines.append("")

    components = doc.get("components")
    if components:
        pkg = components.get("package", "?")
        cver = components.get("version", "?")
        hooks = components.get("hooks") or []
        demos = components.get("demos") or []
        lines.append(
            f"**React components:** {pkg}@{cver} — {len(hooks)} hooks, "
            f"{len(demos)} demos"
        )
        lines.append("")

    return lines


def render_recipe_section(recipe: dict) -> list[str]:
    lines = [f"### {recipe['name']}", ""]
    if recipe.get("summary"):
        lines += [recipe["summary"], ""]
    modules = recipe.get("modules") or []
    lines.append(f"**Modules:** {', '.join(modules) if modules else '—'}")
    if recipe.get("notes"):
        lines += ["", f"**Notes:** {recipe['notes']}"]
    lines.append("")
    return lines


def render_markdown(catalog: dict) -> str:
    totals = catalog["totals"]
    lines = [
        "# Stapel module catalog",
        "",
        f"{totals['modules']} modules · {totals['operations']} operations · "
        f"{totals['axes']} axes · {totals['extension_points']} extension points.",
        "",
        "Generated by `stapel-catalog` from each module's "
        "`docs/capabilities.json`. Do not edit by hand.",
        "",
        "## Modules",
        "",
    ]
    for doc in catalog["modules"]:
        lines += render_module_section(doc)

    recipes = catalog.get("recipes")
    if recipes:
        lines += ["## Recipes", "",
                  "Curated composite projections (a product shape = N modules).",
                  ""]
        for recipe in recipes:
            lines += render_recipe_section(recipe)

    # single trailing newline, no doubled blank line at EOF
    text = "\n".join(lines).rstrip("\n") + "\n"
    return text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-catalog",
        description="Aggregate the module catalog from every module's "
        "docs/capabilities.json into catalog.json + a compact, prompt-ready "
        "catalog.md. --index emits the FULL machine index (agent-knowledge-"
        "base.md §64) instead: capabilities + flows.json + errors.json + "
        "CONFIG.MD + STAPEL_LIBS + react manifest.json projections, per "
        "module, into one JSON file.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Module repo directories (their docs/capabilities.json is read) "
        "or direct paths to capabilities.json files.",
    )
    parser.add_argument(
        "--workspace",
        help="Scan <workspace>/stapel-*/docs/capabilities.json; repos without "
        "the artifact are skipped with a warning. Also the default source "
        "for --react-root (<workspace>/stapel-react) in --index mode.",
    )
    parser.add_argument(
        "--recipes",
        help="Curated recipes YAML (composite projections) — rendered as its "
        "own catalog.md section / index 'recipes' key.",
    )
    parser.add_argument(
        "--out-dir",
        default=".",
        help="Directory to write catalog.json + catalog.md (default: cwd). "
        "Ignored by --index when -o/--output is given.",
    )
    parser.add_argument(
        "--index",
        action="store_true",
        help="Emit the full machine index (see above) to a single JSON file "
        "instead of the compact capabilities-only catalog.json + catalog.md.",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output file path for --index mode (default: <out-dir>/catalog.json).",
    )
    parser.add_argument(
        "--react-root",
        help="Root containing packages/<mod>-react/manifest.json for the "
        "--index 'components' projection (default: <workspace>/stapel-react "
        "when --workspace is given and that directory exists).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Drift-check: build fresh in memory and compare against the "
        "already-committed artifact(s) byte-for-byte; nonzero exit and no "
        "write on any mismatch or missing file.",
    )
    args = parser.parse_args(argv)

    sources: list[Path] = [Path(p) for p in args.paths]
    if args.workspace:
        ws_sources = discover_workspace(Path(args.workspace))
        if not ws_sources:
            print(
                f"stapel-catalog: warning: no stapel-*/docs/capabilities.json "
                f"under {args.workspace}",
                file=sys.stderr,
            )
        sources += ws_sources
    if not sources:
        parser.error("no inputs: pass module repo paths and/or --workspace")

    recipes = load_recipes(Path(args.recipes)) if args.recipes else None

    if args.index:
        react_root = Path(args.react_root) if args.react_root else None
        if react_root is None and args.workspace:
            candidate = Path(args.workspace) / "stapel-react"
            if candidate.is_dir():
                react_root = candidate

        index, skipped = build_index(sources, recipes=recipes, react_root=react_root)
        out_path = Path(args.output) if args.output else Path(args.out_dir) / "catalog.json"
        rendered = _stable_json(index)

        if args.check:
            if not out_path.is_file():
                print(
                    f"stapel-catalog: --check: {out_path} does not exist — "
                    "run without --check to materialize it first",
                    file=sys.stderr,
                )
                return 1
            if out_path.read_text() != rendered:
                print(
                    f"stapel-catalog: --check: {out_path} is stale (drift "
                    "detected) — re-run `stapel-catalog --index` to refresh",
                    file=sys.stderr,
                )
                return 1
            print(f"stapel-catalog: --check: {out_path} is up to date", file=sys.stderr)
            return 0

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered)
        print(
            f"stapel-catalog: --index: {index['totals']['modules']} modules "
            f"covered ({len(skipped)} skipped) → {out_path}",
            file=sys.stderr,
        )
        return 0

    docs, skipped = load_documents(sources)
    catalog = build_catalog(docs, recipes=recipes)
    catalog_json = _stable_json(catalog)
    catalog_md = render_markdown(catalog)
    out_dir = Path(args.out_dir)

    if args.check:
        stale = [
            p for p, rendered in (
                (out_dir / "catalog.json", catalog_json),
                (out_dir / "catalog.md", catalog_md),
            )
            if not p.is_file() or p.read_text() != rendered
        ]
        if stale:
            for p in stale:
                print(f"stapel-catalog: --check: {p} is missing or stale (drift detected)",
                      file=sys.stderr)
            return 1
        print(f"stapel-catalog: --check: catalog.json/catalog.md up to date in {out_dir}",
              file=sys.stderr)
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "catalog.json").write_text(catalog_json)
    (out_dir / "catalog.md").write_text(catalog_md)

    print(
        f"stapel-catalog: {catalog['totals']['modules']} modules covered "
        f"({len(skipped)} skipped), {catalog['totals']['operations']} operations "
        f"→ {out_dir}/catalog.json, {out_dir}/catalog.md",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
