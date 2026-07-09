"""
stapel-capabilities — emit a module's ``docs/capabilities.json``
(capability-config.md §1-§2), the FOURTH per-module contract artifact
alongside the ``docs/{schema,flows,errors}.json`` triad and under the same
pipeline discipline: emitted by ``make contract``, drift-gated by
``make contract-check`` / ``tests/test_contract.py``, committed to the repo.

This module is the generic MECHANISM, lifted from the stapel-auth etalon
(``stapel_auth/_capabilities.py``). A module keeps only its thin config:

- its gate registry lives in its own ``urls.py`` (declared where the gating
  executes — no second truth to drift), duck-typed entries with
  ``.name / .flags / .patterns`` (see :class:`GateEntry` for the reference
  shape; modules define their own NamedTuple so their runtime never imports
  stapel-tools);
- its curated layer is its own hand-written ``docs/capabilities.meta.json``
  (summary/business_label per axis, ``provides``, ``requires``,
  ``extension_points``) — a missing/extra/empty entry is a LOUD emission
  error, never a silent skip;
- its axis rule (which ``conf.py`` DEFAULTS keys are CTO-facing axes) and
  axis grouping come in as callables (``is_axis`` / ``axis_group`` — see
  :func:`axis_group_rules` for the common exact+suffix form).

Derivable facts are derived; semantics are curated:

- ``key/kind/default`` — introspected from the module's
  ``stapel_core.conf.AppSettings``-shaped ``DEFAULTS`` literal dict;
- ``gates.operations`` — the registry's patterns cross-referenced against
  schema.json operationIds (URL resolution handles both route and regex
  patterns, including router-composed URLs);
- ``gates.co_gates`` — derived for OR-composed gates: a registry entry's
  flags compose with OR (its operations disappear only when ALL flags are
  off), so each axis lists the sibling flags that keep its operations
  mounted — otherwise an aggregate index would falsely read "one flag off =
  endpoints gone".

Typical per-module shim (the whole module-side surface)::

    from pathlib import Path
    from stapel_tools.capabilities import axis_group_rules, run_capabilities_cli

    def main(argv=None):
        from stapel_mod._codegen import _configure
        _configure()                                # module's Django harness
        from stapel_mod.conf import DEFAULTS
        from stapel_mod.urls import GATE_REGISTRY
        return run_capabilities_cli(
            argv,
            repo=Path(__file__).resolve().parent,
            canonical_prefix="/mod/api",
            defaults=DEFAULTS,
            registry=GATE_REGISTRY,
            is_axis=lambda key: key.startswith("MOD_"),
            axis_group=axis_group_rules(suffix={"_LOGIN": "mod.login"}),
        )
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Callable, NamedTuple

#: Dummy values substituted for ``{param}`` path parameters when probing
#: which gate-registry entry serves a schema path. Tried in order; a path
#: matches an entry if any candidate substitution resolves.
_PROBE_CANDIDATES = ("x", "1", "3fa85f64-5717-4562-b3fc-2c963f66afa6")

_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


class GateEntry(NamedTuple):
    """Reference shape of a gate-registry entry (capability-config.md §2 p.2).

    ``flags`` compose with OR — the block is mounted while ANY flag is on and
    disappears only when ALL of them are off. Empty flags = always on.

    Modules declare their OWN NamedTuple of this shape in their ``urls.py``
    (runtime code must not import stapel-tools); the mechanism only duck-types
    ``.name / .flags / .patterns``. This class exists for tests and for
    documentation of the protocol.
    """

    name: str
    flags: tuple
    patterns: tuple


def _stable_json(data) -> str:
    """Byte-stable JSON for drift gates — same pinning as stapel_tools.codegen."""
    return json.dumps(data, indent=2, ensure_ascii=False, separators=(",", ": ")) + "\n"


def axis_kind(default) -> str:
    """Axis ``kind`` (capability-config.md §1) derived from the default's shape."""
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, (list, tuple)):
        return "list"
    return "enum"


def axis_group_rules(
    *,
    exact: dict[str, str] | None = None,
    suffix: dict[str, str] | None = None,
) -> Callable[[str], str]:
    """Build an ``axis_group`` callable from exact-key + key-suffix rules.

    Covers the common module shape (the auth etalon: two exact keys, three
    suffixes). Exact rules win; suffixes are tried in dict order; an unmatched
    key is a loud emission error — a new axis must be classified, not guessed.
    """
    exact = exact or {}
    suffix = suffix or {}

    def axis_group(key: str) -> str:
        if key in exact:
            return exact[key]
        for suf, group in suffix.items():
            if key.endswith(suf):
                return group
        raise SystemExit(f"capabilities: no axis group rule for key {key!r}")

    return axis_group


def operations_by_entry(
    schema: dict, registry: dict, *, canonical_prefix: str
) -> dict[str, list[str]]:
    """Attribute every schema operation to the registry entry that serves it.

    For each schema path, strip the canonical prefix and resolve the resulting
    URL (with ``{param}`` placeholders substituted) against each entry's own
    patterns. Handles route patterns, regex patterns and router-composed URL
    resolvers alike (anything Django's ``URLResolver`` can resolve). Paths
    served by no entry (e.g. co-mounted sibling-module endpoints) are simply
    not attributed — they belong to no gate of this module.
    """
    from django.urls.exceptions import Resolver404
    from django.urls.resolvers import RegexPattern, URLResolver

    resolvers = {
        name: URLResolver(RegexPattern(r"^"), list(entry.patterns))
        for name, entry in registry.items()
    }

    def _resolves(resolver, rel_path: str) -> bool:
        for candidate in _PROBE_CANDIDATES:
            probe = re.sub(r"\{[^}]+\}", candidate, rel_path)
            try:
                resolver.resolve(probe)
                return True
            except Resolver404:
                continue
        return False

    ops: dict[str, list[str]] = {name: [] for name in registry}
    for path_key, path_item in schema.get("paths", {}).items():
        if not path_key.startswith(canonical_prefix):
            raise SystemExit(
                f"capabilities: schema path {path_key!r} lacks the canonical "
                f"prefix {canonical_prefix!r} — wrong schema for this harness?"
            )
        rel = path_key[len(canonical_prefix):].lstrip("/")
        for name, resolver in resolvers.items():
            if _resolves(resolver, rel):
                ops[name].extend(
                    op["operationId"]
                    for method, op in path_item.items()
                    if method in _HTTP_METHODS
                )
                break
    return {name: sorted(op_ids) for name, op_ids in ops.items()}


def build_capabilities(
    *,
    module: str,
    version: str,
    defaults: dict,
    registry: dict,
    schema: dict,
    meta: dict,
    is_axis: Callable[[str], bool],
    axis_group: Callable[[str], str],
    canonical_prefix: str,
) -> dict:
    """Assemble the capabilities.json document (pure core — fully argument-driven).

    ``defaults`` is the module's ``conf.py`` DEFAULTS literal dict (the
    ``stapel_core.conf.AppSettings`` shape); axis order follows DEFAULTS
    insertion order. The curated ``meta`` must name exactly the axis key set
    ``is_axis`` selects — both directions of drift fail loudly.
    """
    axis_keys = [key for key in defaults if is_axis(key)]

    meta_axes = meta.get("axes", {})
    missing = [k for k in axis_keys if k not in meta_axes]
    extra = [k for k in meta_axes if k not in axis_keys]
    if missing or extra:
        raise SystemExit(
            "capabilities: curated meta out of sync with conf.py DEFAULTS — "
            f"axes missing from capabilities.meta.json: {missing or 'none'}; "
            f"stale axes in capabilities.meta.json: {extra or 'none'}. "
            "Update docs/capabilities.meta.json to match the module's axis rule."
        )
    for field in ("provides",):
        if not meta.get(field):
            raise SystemExit(f"capabilities: meta field {field!r} is missing/empty")
    for field in ("extension_points", "requires"):
        if not isinstance(meta.get(field), list):
            raise SystemExit(f"capabilities: meta field {field!r} must be a list")

    entry_ops = operations_by_entry(
        schema, registry, canonical_prefix=canonical_prefix
    )

    axes = []
    for key in axis_keys:
        curated = meta_axes[key]
        for field in ("summary", "business_label"):
            if not curated.get(field):
                raise SystemExit(
                    f"capabilities: axis {key!r} lacks a non-empty {field!r} "
                    "in docs/capabilities.meta.json"
                )
        gating_entries = [e for e in registry.values() if key in e.flags]
        operations: set[str] = set()
        co_gates: set[str] = set()
        for entry in gating_entries:
            operations.update(entry_ops[entry.name])
            co_gates.update(entry.flags)
        co_gates.discard(key)
        gates = {"operations": sorted(operations), "co_gates": sorted(co_gates)}
        if curated.get("behavior"):
            gates["behavior"] = curated["behavior"]
        axes.append(
            {
                "key": key,
                "kind": axis_kind(defaults[key]),
                "default": defaults[key],
                "group": axis_group(key),
                "gates": gates,
                "curated": {
                    "summary": curated["summary"],
                    "business_label": curated["business_label"],
                },
            }
        )

    operations_total = sum(
        1
        for path_item in schema.get("paths", {}).values()
        for method in path_item
        if method in _HTTP_METHODS
    )

    return {
        "module": module,
        "version": version,
        "provides": meta["provides"],
        "axes": axes,
        "extension_points": meta["extension_points"],
        "operations_total": operations_total,
        "requires": meta["requires"],
    }


def emit_capabilities(
    out_dir: Path,
    *,
    repo: Path,
    canonical_prefix: str,
    defaults: dict,
    registry: dict,
    is_axis: Callable[[str], bool],
    axis_group: Callable[[str], str],
) -> dict:
    """Emit ``<out_dir>/capabilities.json``; returns the document.

    Reads module/version from ``<repo>/pyproject.toml``, the curated layer
    from ``<repo>/docs/capabilities.meta.json`` and the already-emitted
    ``<out_dir>/schema.json`` (emit the contract triad first). Fails closed on
    a missing meta/schema or an empty gate registry.
    """
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text())

    meta_path = repo / "docs" / "capabilities.meta.json"
    if not meta_path.is_file():
        raise SystemExit(
            f"capabilities: curated layer {meta_path} is missing — it is "
            "hand-written (summary/business_label per axis, provides, "
            "requires, extension_points) and must be committed."
        )
    meta = json.loads(meta_path.read_text())

    schema_path = out_dir / "schema.json"
    if not schema_path.is_file():
        raise SystemExit(
            f"capabilities: {schema_path} not found — emit the contract triad "
            "first (the module's `python -m <module>._codegen --out` run)."
        )
    schema = json.loads(schema_path.read_text())

    # Fail closed if the urls module somehow didn't populate the registry.
    if not registry:
        raise SystemExit("capabilities: gate registry is empty after urls import")

    doc = build_capabilities(
        module=pyproject["project"]["name"],
        version=pyproject["project"]["version"],
        defaults=defaults,
        registry=registry,
        schema=schema,
        meta=meta,
        is_axis=is_axis,
        axis_group=axis_group,
        canonical_prefix=canonical_prefix,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "capabilities.json").write_text(_stable_json(doc))
    return doc


def run_capabilities_cli(
    argv: list[str] | None = None,
    *,
    repo: Path,
    canonical_prefix: str,
    defaults: dict,
    registry: dict,
    is_axis: Callable[[str], bool],
    axis_group: Callable[[str], str],
    prog: str | None = None,
) -> int:
    """The shared ``--out`` CLI a module shim exposes as its ``main()``.

    The shim configures its own Django harness (the same single-module
    instance its triad emitter uses) and imports DEFAULTS + the gate registry
    BEFORE calling this — everything module-specific enters as arguments.
    """
    parser = argparse.ArgumentParser(
        prog=prog or "stapel-capabilities",
        description="Emit docs/capabilities.json (fourth contract artifact) "
        "from conf.py DEFAULTS + the urls.py gate registry + schema.json + "
        "the curated docs/capabilities.meta.json.",
    )
    parser.add_argument(
        "--out",
        default="docs",
        help="Output directory; must already contain schema.json (default: docs).",
    )
    args = parser.parse_args(argv)

    doc = emit_capabilities(
        Path(args.out),
        repo=repo,
        canonical_prefix=canonical_prefix,
        defaults=defaults,
        registry=registry,
        is_axis=is_axis,
        axis_group=axis_group,
    )
    gated = sum(1 for a in doc["axes"] if a["gates"]["operations"])
    print(
        f"{doc['module']} capabilities: {len(doc['axes'])} axes ({gated} gating "
        f"operations), {doc['operations_total']} operations total → "
        f"{args.out}/capabilities.json",
        file=sys.stderr,
    )
    return 0
