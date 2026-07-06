"""
Stapel analytics report generator (frontend-guardrails.md §3.3).

Builds a summary report of the typed-analytics surface across a pnpm workspace
of `@stapel/*-react` pairs and/or a customer application. Sources are STATIC and
already generated (§3.3: "Источники — только статика и generated-артефакты"):

    events.json / manifest.events   defineEvent catalog (name, description, typed
                                     props, optional flow link, source file:line)
                                     + auto-instrumented flow funnels
    flows.json (backend + pair)      canonical backend flows: title/description,
                                     actors, steps, endpoints
    manifest.json (machines/flows)   which machines a pair ships
    TS/TSX source                    call sites: tracked()/trackedSubmit()/track()
                                     emit points, data-analytics="flow"/"none"
                                     markers (+reason), eslint-disable descriptions

The report is emitted machine-readable (report.json — for the Studio project
"passport", user decision Q13) and human-readable (Markdown + HTML — presentable,
not merely technical). Two slices are always reported and kept separate: **app**
(customer code) and **library** (`@stapel/*` pairs).

Design decisions / deviations (for the format review):

* Pure Python, no Node runtime dependency. stapel-tools is a dependency-free
  Python package (pyproject `dependencies = []`); every existing tool parses with
  Python's own facilities and `pytest` must stay green without a JS toolchain.
  The heavy TS-AST work (defineEvent meta + flow funnels + each event's source
  file:line) is ALREADY done by the workspace's `gen:events` (scripts/
  events-lib.mjs, TypeScript AST) and committed to the drift-gated events.json —
  this report CONSUMES that authoritative artifact instead of re-parsing TS for
  it. The only surface not in any generated artifact is call sites, for which a
  focused syntactic scanner is adequate and matches how the
  `stapel/clickable-needs-event` lint itself reasons (§3.2 "syntactic
  checkability > semantic undecidability").

* Cross-file call-site → event resolution reuses the events.json TS-AST catalog
  (produced by events-lib.mjs) as the authoritative event set, and derives the
  binding→name map from `defineEvent` assignments (anchored, where available, on
  the authoritative source coordinates in events.json) — NOT the intentionally
  conservative in-file resolver of the lint. Import aliasing (`X as Y`) is
  followed. Absence of events.json degrades to source-derived bindings, never a
  crash.

* env-gating badge is a placeholder for task G6: the `gated_by` field is not yet
  emitted in flows.json. The format carries the badge and renders it when the
  field appears; its absence means the flow is treated as always-on.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPORT_SCHEMA = "stapel-analytics-report/v1"

# Vendor/build dirs never scanned. Test + fixture dirs are excluded too: they
# deliberately contain anti-pattern call sites (frontend-guardrails G2 note —
# fixtures legitimately train anti-patterns), and the authoritative gen:events
# only ever scans a package's `src/`. See `_source_roots`.
SKIP_DIRS = {
    "node_modules",
    ".git",
    "dist",
    "build",
    ".turbo",
    "coverage",
    "generated",
    "test",
    "tests",
    "__tests__",
    "__mocks__",
    "fixtures",
}
SKIP_FILE_RE = re.compile(r"\.(test|spec|d)\.tsx?$")
SOURCE_RE = re.compile(r"\.tsx?$")

# Wrappers that mark a click as tracked (frontend-guardrails §3.2 outcome a).
EMIT_WRAPPERS = ("tracked", "trackedSubmit", "track")

# defineEvent binding: `[export] const NAME = defineEvent({ ... name: "..." ... })`
DEFINE_EVENT_RE = re.compile(
    r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*defineEvent\s*\(\s*\{",
)
# name property inside a defineEvent object literal.
NAME_PROP_RE = re.compile(r"""name\s*:\s*["'`]([^"'`]+)["'`]""")
# tracked/trackedSubmit/track(<identifier>, ...) — the emit call sites.
EMIT_CALL_RE = re.compile(
    r"\b(tracked|trackedSubmit|track)\s*\(\s*([A-Za-z_$][\w$.]*)",
)
# data-analytics markers.
DA_FLOW_RE = re.compile(r"""data-analytics\s*=\s*["']flow["']""")
DA_NONE_RE = re.compile(r"""data-analytics\s*=\s*["']none["']""")
DA_REASON_RE = re.compile(r"""data-analytics-reason\s*=\s*["']([^"']*)["']""")
# eslint-disable comments carrying a `-- description` (require-description, §2.4).
ESLINT_DISABLE_RE = re.compile(
    r"eslint-disable(?:-next-line|-line)?\s+([^\n]*?)\s*--\s*(.+?)(?:\*/|$)",
)
# import specifiers, to follow `X as Y` aliasing of event bindings.
IMPORT_BLOCK_RE = re.compile(r"import\s*(?:type\s*)?\{([^}]*)\}\s*from", re.DOTALL)
# a line that is (starts as) a comment — JSDoc/`//`/`/* */`/JSX comment. Used to
# skip markers/call sites that live in prose, not code (a real JSX attribute or
# call is never on such a line).
COMMENT_LINE_RE = re.compile(r"^\s*(//|\*|/\*|\*/|<!--)")
# nearest enclosing component/function name above a call site.
COMPONENT_DEF_RE = re.compile(
    r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"
    r"|(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=",
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Emit:
    """A single call site that emits an event."""

    file: str
    line: int
    wrapper: str  # tracked | trackedSubmit | track
    component: str | None = None
    resolved: bool = True

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"file": self.file, "line": self.line, "wrapper": self.wrapper}
        if self.component:
            d["component"] = self.component
        if not self.resolved:
            d["resolved"] = False
        return d


@dataclass
class EventEntry:
    """A typed app/library event (defineEvent) with its emit sites."""

    name: str
    slice: str  # app | library
    package: str
    description: str = ""
    props: dict[str, Any] = field(default_factory=dict)
    flow: str | None = None
    source: dict[str, Any] | None = None
    emits: list[Emit] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "slice": self.slice,
            "package": self.package,
            "description": self.description,
            "props": self.props,
        }
        if self.flow:
            d["flow"] = self.flow
        if self.source:
            d["source"] = self.source
        d["emits"] = [e.to_dict() for e in self.emits]
        return d


@dataclass
class Funnel:
    """An auto-instrumented flow funnel a pair emits (flow.<id>.<step>)."""

    flow: str
    event: str
    slice: str
    package: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    props: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "flow": self.flow,
            "event": self.event,
            "slice": self.slice,
            "package": self.package,
            "steps": self.steps,
            "props": self.props,
        }


@dataclass
class Marker:
    """A data-analytics="none" opt-out or an eslint-disable escape hatch."""

    file: str
    line: int
    slice: str
    package: str
    reason: str = ""  # for untracked
    rule: str = ""  # for disabled
    description: str = ""  # for disabled

    def untracked_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "slice": self.slice,
            "package": self.package,
            "reason": self.reason,
        }

    def disabled_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "slice": self.slice,
            "package": self.package,
            "rule": self.rule,
            "description": self.description,
        }


@dataclass
class CanonicalFlow:
    """A canonical backend flow joined with its frontend coverage."""

    id: str
    title: str = ""
    description: str = ""
    actors: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    gated_by: list[str] | None = None  # G6 placeholder
    sources: list[str] = field(default_factory=list)
    frontend: dict[str, Any] = field(default_factory=dict)
    app_events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "actors": self.actors,
            "steps": self.steps,
        }
        if self.gated_by is not None:
            d["gated_by"] = self.gated_by
        d["sources"] = self.sources
        d["frontend"] = self.frontend
        d["app_events"] = self.app_events
        return d


@dataclass
class PackageScan:
    """Everything read/derived from a single package."""

    name: str
    slice: str  # app | library
    dir: str
    events: list[EventEntry] = field(default_factory=list)
    funnels: list[Funnel] = field(default_factory=list)
    untracked: list[Marker] = field(default_factory=list)
    disabled: list[Marker] = field(default_factory=list)
    flows_json: list[dict[str, Any]] = field(default_factory=list)
    machines: list[str] = field(default_factory=list)
    has_events_json: bool = False
    flow_markers: int = 0


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def _read_json(path: str) -> Any | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _source_root(pkg_dir: str) -> str:
    """A package's `src/` if present (matches gen:events), else the package dir."""
    src = os.path.join(pkg_dir, "src")
    return src if os.path.isdir(src) else pkg_dir


def _iter_source_files(root: str) -> Iterable[str]:
    """Yield .ts/.tsx source files under root, skipping generated/tests/vendor."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if SOURCE_RE.search(name) and not SKIP_FILE_RE.search(name):
                yield os.path.join(dirpath, name)


def _find_first(root: str, target: str) -> str | None:
    """First file named `target` under root (skipping vendor dirs)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d in ("generated",) or d not in SKIP_DIRS]
        if target in filenames:
            return os.path.join(dirpath, target)
    return None


def _rel(path: str, base: str) -> str:
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return path


# ---------------------------------------------------------------------------
# Source scanning (call sites + markers)
# ---------------------------------------------------------------------------


def _is_noise_match(line: str, start: int) -> bool:
    """True if a match at column `start` is in a comment line or a template/
    string literal — i.e. prose, not code (avoids false positives from JSDoc
    examples and doc strings that mention `data-analytics="flow"`/`tracked(`)."""
    if COMMENT_LINE_RE.match(line):
        return True
    # odd number of backticks before the match → inside a template literal
    return line.count("`", 0, start) % 2 == 1


def _component_before(lines: list[str], idx: int) -> str | None:
    """Nearest enclosing function/component name at or above line index `idx`."""
    for i in range(idx, -1, -1):
        m = COMPONENT_DEF_RE.search(lines[i])
        if m:
            name = m.group(1) or m.group(2)
            if name and name[0].isupper():
                return name
    return None


def collect_bindings(sources: dict[str, str]) -> dict[str, str]:
    """Map every `defineEvent` binding identifier → event name, across files.

    Follows `import { X as Y }` aliases so a call site that uses the alias still
    resolves. Reuses the authoritative event names; see module docstring on why
    this is anchored to the events.json TS-AST catalog rather than a conservative
    in-file resolver.
    """
    bindings: dict[str, str] = {}
    for text in sources.values():
        for m in DEFINE_EVENT_RE.finditer(text):
            ident = m.group(1)
            # name property within the following object literal (bounded window)
            window = text[m.end() : m.end() + 400]
            nm = NAME_PROP_RE.search(window)
            if nm:
                bindings[ident] = nm.group(1)
    # second pass: import aliases
    for text in sources.values():
        for block in IMPORT_BLOCK_RE.finditer(text):
            for spec in block.group(1).split(","):
                parts = spec.strip().split(" as ")
                if len(parts) == 2:
                    orig, alias = parts[0].strip(), parts[1].strip()
                    if orig in bindings and alias not in bindings:
                        bindings[alias] = bindings[orig]
    return bindings


def scan_source(
    path: str,
    rel: str,
    text: str,
    bindings: dict[str, str],
    slice_: str,
    package: str,
) -> tuple[list[tuple[str, Emit]], list[Marker], list[Marker]]:
    """Scan one source file for emit call sites and analytics markers.

    Returns (emits_by_event, untracked_markers, disabled_markers) where
    emits_by_event is a list of (event_name, Emit); event_name is "" when the
    first argument does not resolve to a known event binding.
    """
    lines = text.splitlines()
    emits: list[tuple[str, Emit]] = []
    untracked: list[Marker] = []
    disabled: list[Marker] = []

    for lineno, line in enumerate(lines, start=1):
        # emit call sites
        for m in EMIT_CALL_RE.finditer(line):
            if _is_noise_match(line, m.start()):
                continue
            wrapper, ident = m.group(1), m.group(2)
            name = bindings.get(ident)
            emits.append(
                (
                    name or "",
                    Emit(
                        file=rel,
                        line=lineno,
                        wrapper=wrapper,
                        component=_component_before(lines, lineno - 1),
                        resolved=name is not None,
                    ),
                )
            )
        # data-analytics="none" opt-out (+ reason in a small window)
        none_m = DA_NONE_RE.search(line)
        if none_m and not _is_noise_match(line, none_m.start()):
            reason = ""
            lo, hi = max(0, lineno - 4), min(len(lines), lineno + 3)
            for j in range(lo, hi):
                rm = DA_REASON_RE.search(lines[j])
                if rm:
                    reason = rm.group(1)
                    break
            untracked.append(
                Marker(file=rel, line=lineno, slice=slice_, package=package, reason=reason)
            )
        # eslint-disable escape hatches with a description
        dm = ESLINT_DISABLE_RE.search(line)
        if dm:
            disabled.append(
                Marker(
                    file=rel,
                    line=lineno,
                    slice=slice_,
                    package=package,
                    rule=dm.group(1).strip(),
                    description=dm.group(2).strip(),
                )
            )

    return emits, untracked, disabled


def count_flow_markers(text: str) -> int:
    n = 0
    for line in text.splitlines():
        for m in DA_FLOW_RE.finditer(line):
            if not _is_noise_match(line, m.start()):
                n += 1
    return n


# ---------------------------------------------------------------------------
# Flow normalization
# ---------------------------------------------------------------------------


def _normalize_step(step: dict[str, Any]) -> dict[str, Any]:
    endpoints = []
    for ep in step.get("endpoints", []) or []:
        if isinstance(ep, dict):
            endpoints.append(f"{ep.get('method', '')} {ep.get('path', '')}".strip())
        elif isinstance(ep, str):
            endpoints.append(ep)
    return {
        "order": step.get("order"),
        "kind": step.get("kind", ""),
        "note": step.get("note", ""),
        "noteKey": step.get("note_key") or step.get("noteKey", ""),
        "ref": step.get("ref", ""),
        "endpoints": endpoints,
    }


def normalize_flow(raw: dict[str, Any], source: str) -> CanonicalFlow:
    steps = [_normalize_step(s) for s in raw.get("steps", []) or []]
    gated = raw.get("gated_by")  # G6 placeholder; None when absent
    return CanonicalFlow(
        id=raw.get("id", ""),
        title=raw.get("title") or raw.get("title_key") or raw.get("titleKey", ""),
        description=raw.get("description")
        or raw.get("description_key")
        or raw.get("descriptionKey", ""),
        actors=list(raw.get("actors", []) or []),
        steps=steps,
        gated_by=list(gated) if isinstance(gated, list) else None,
        sources=[source],
    )


def _is_prose(text: str) -> bool:
    """True for human-readable text, False for an i18n-key placeholder."""
    return bool(text) and not text.startswith("flow.")


def _merge_flow(into: CanonicalFlow, other: CanonicalFlow) -> None:
    """Prefer prose-bearing fields; union sources; keep richest steps."""
    if not _is_prose(into.title) and _is_prose(other.title):
        into.title = other.title
    if not _is_prose(into.description) and _is_prose(other.description):
        into.description = other.description
    if not into.actors and other.actors:
        into.actors = other.actors
    other_notes = any(s["note"] for s in other.steps)
    into_notes = any(s["note"] for s in into.steps)
    other_eps = sum(len(s["endpoints"]) for s in other.steps)
    into_eps = sum(len(s["endpoints"]) for s in into.steps)
    if other_eps > into_eps or (other_notes and not into_notes):
        into.steps = other.steps
    if into.gated_by is None and other.gated_by is not None:
        into.gated_by = other.gated_by
    for s in other.sources:
        if s not in into.sources:
            into.sources.append(s)


# ---------------------------------------------------------------------------
# Package discovery + scan
# ---------------------------------------------------------------------------


def _match_machines(flow_id: str, machines: list[str]) -> list[str]:
    """Best-effort machine→flow match by name (manifest carries no explicit map).

    `auth.password_login` → `createPasswordLoginFlow`. Matches when the machine
    name, stripped of the `create`/`Flow` affixes and lowercased, equals the
    flow's tail (segments after the module) with separators removed. Returns the
    matches (often one), or [] when nothing lines up — honest under-claim rather
    than dumping the pair's whole machine list on every flow.
    """
    tail = "".join(flow_id.split(".")[1:]).replace("_", "").lower()
    if not tail:
        return []
    out = []
    for m in machines:
        key = m
        if key.startswith("create"):
            key = key[len("create") :]
        if key.endswith("Flow"):
            key = key[: -len("Flow")]
        if key.lower() == tail:
            out.append(m)
    return out


def discover_packages(workspace: str) -> list[str]:
    """Directories that look like packages (have a package.json) under workspace.

    Honors a `packages/*` layout (pnpm workspace) but also treats the workspace
    root itself as a package if it carries a package.json (single-app mode).
    """
    dirs: list[str] = []
    root_pkg = os.path.join(workspace, "package.json")
    pkgs_dir = os.path.join(workspace, "packages")
    if os.path.isdir(pkgs_dir):
        for name in sorted(os.listdir(pkgs_dir)):
            d = os.path.join(pkgs_dir, name)
            if os.path.isfile(os.path.join(d, "package.json")):
                dirs.append(d)
    if not dirs and os.path.isfile(root_pkg):
        dirs.append(workspace)
    return dirs


def scan_package(pkg_dir: str, workspace: str) -> PackageScan:
    pkg_json = _read_json(os.path.join(pkg_dir, "package.json")) or {}
    name = pkg_json.get("name") or os.path.basename(pkg_dir)
    slice_ = "library" if str(name).startswith("@stapel/") else "app"

    scan = PackageScan(name=name, slice=slice_, dir=pkg_dir)

    # generated events.json (authoritative defineEvent + funnel catalog)
    events_json_path = _find_first(pkg_dir, "events.json")
    events_json = _read_json(events_json_path) if events_json_path else None
    scan.has_events_json = bool(events_json)

    # manifest.json (machines list + events fallback)
    manifest = _read_json(os.path.join(pkg_dir, "manifest.json")) or {}
    scan.machines = list(manifest.get("machines", []) or [])
    if events_json is None and manifest.get("events"):
        events_json = manifest["events"]
        scan.has_events_json = True

    # flows.json (pair projection; may also be backend-shaped)
    flows_path = _find_first(pkg_dir, "flows.json")
    flows_data = _read_json(flows_path) if flows_path else None
    if isinstance(flows_data, list):
        scan.flows_json = flows_data

    # read all source once (src/ only, mirroring gen:events)
    sources: dict[str, str] = {}
    for path in _iter_source_files(_source_root(pkg_dir)):
        try:
            with open(path, encoding="utf-8") as fh:
                sources[path] = fh.read()
        except (OSError, UnicodeDecodeError):
            continue

    bindings = collect_bindings(sources)

    # index defined events from the authoritative catalog
    defined_index: dict[str, EventEntry] = {}
    for d in (events_json or {}).get("defined", []) if isinstance(events_json, dict) else []:
        ev = EventEntry(
            name=d.get("name", ""),
            slice=slice_,
            package=name,
            description=d.get("description", ""),
            props=d.get("props", {}) or {},
            flow=d.get("flow"),
            source=d.get("source"),
        )
        defined_index[ev.name] = ev

    # funnels from the catalog
    for f in (events_json or {}).get("flows", []) if isinstance(events_json, dict) else []:
        scan.funnels.append(
            Funnel(
                flow=f.get("flow", ""),
                event=f.get("event", ""),
                slice=slice_,
                package=name,
                steps=f.get("steps", []) or [],
                props=f.get("props", {}) or {},
            )
        )

    # scan sources for emit sites + markers
    for path, text in sources.items():
        rel = _rel(path, workspace)
        scan.flow_markers += count_flow_markers(text)
        emits, untracked, disabled = scan_source(
            path, rel, text, bindings, slice_, name
        )
        for ev_name, emit in emits:
            if ev_name and ev_name in defined_index:
                defined_index[ev_name].emits.append(emit)
            elif ev_name:
                # resolved to a binding not in the catalog (e.g. no events.json)
                defined_index.setdefault(
                    ev_name,
                    EventEntry(name=ev_name, slice=slice_, package=name),
                ).emits.append(emit)
            # unresolved (string/dynamic first arg) → not attributed to an event
        scan.untracked.extend(untracked)
        scan.disabled.extend(disabled)

    scan.events = sorted(defined_index.values(), key=lambda e: e.name)
    return scan


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def build_report(
    workspace: str,
    pkg_dirs: list[str],
    backend_flows: list[dict[str, Any]] | None = None,
    tool_version: str = "0",
) -> dict[str, Any]:
    scans = [scan_package(d, workspace) for d in pkg_dirs]

    events: list[EventEntry] = []
    funnels: list[Funnel] = []
    untracked: list[Marker] = []
    disabled: list[Marker] = []
    for s in scans:
        events.extend(s.events)
        funnels.extend(s.funnels)
        untracked.extend(s.untracked)
        disabled.extend(s.disabled)

    # canonical flows: backend source (prose) + every pair's flows.json, merged
    flows_by_id: dict[str, CanonicalFlow] = {}
    for raw in backend_flows or []:
        cf = normalize_flow(raw, "backend")
        if cf.id:
            flows_by_id[cf.id] = cf
    for s in scans:
        for raw in s.flows_json:
            cf = normalize_flow(raw, s.name)
            if not cf.id:
                continue
            if cf.id in flows_by_id:
                _merge_flow(flows_by_id[cf.id], cf)
            else:
                flows_by_id[cf.id] = cf

    # frontend coverage per flow
    funnels_by_flow: dict[str, list[Funnel]] = {}
    for fu in funnels:
        funnels_by_flow.setdefault(fu.flow, []).append(fu)
    machines_by_pkg = {s.name: s.machines for s in scans}
    app_events_by_flow: dict[str, list[str]] = {}
    for ev in events:
        if ev.slice == "app" and ev.flow:
            app_events_by_flow.setdefault(ev.flow, []).append(ev.name)

    for fid, cf in flows_by_id.items():
        covering = funnels_by_flow.get(fid, [])
        pkgs = sorted({fu.package for fu in covering})
        screens = sorted(
            {e.file for ev in events if ev.flow == fid for e in ev.emits}
        )
        pair_machines = sorted({m for p in pkgs for m in machines_by_pkg.get(p, [])})
        matched = _match_machines(fid, pair_machines)
        cf.frontend = {
            "packages": pkgs,
            "funnel": covering[0].event if covering else None,
            "machines": matched,
            "screens": screens,
        }
        cf.app_events = sorted(app_events_by_flow.get(fid, []))

    canonical_flows = [flows_by_id[k] for k in sorted(flows_by_id)]

    # coverage: clickable outcomes by marker type (§3.3)
    flow_marker_count = sum(s.flow_markers for s in scans)
    tracked_count = sum(len(e.emits) for e in events)

    def slice_summary(slc: str) -> dict[str, Any]:
        sl_scans = [s for s in scans if s.slice == slc]
        return {
            "packages": sorted(s.name for s in sl_scans),
            "defined_events": sum(1 for e in events if e.slice == slc),
            "emit_sites": sum(len(e.emits) for e in events if e.slice == slc),
            "flow_funnels": sum(1 for f in funnels if f.slice == slc),
            "untracked": sum(1 for m in untracked if m.slice == slc),
            "disabled": sum(1 for m in disabled if m.slice == slc),
        }

    report = {
        "$schema": REPORT_SCHEMA,
        "generated_by": f"stapel-analytics-report {tool_version}",
        "workspace": os.path.abspath(workspace),
        "summary": {
            "packages": len(scans),
            "packages_missing_events_json": sorted(
                s.name for s in scans if not s.has_events_json
            ),
            "app": slice_summary("app"),
            "library": slice_summary("library"),
            "coverage": {
                "tracked": tracked_count,
                "flow": flow_marker_count,
                "untracked": len(untracked),
                "disabled": len(disabled),
            },
        },
        "events": [e.to_dict() for e in sorted(events, key=lambda e: (e.slice, e.name))],
        "flow_funnels": [f.to_dict() for f in sorted(funnels, key=lambda f: f.flow)],
        "flows": [cf.to_dict() for cf in canonical_flows],
        "untracked": [m.untracked_dict() for m in untracked],
        "disabled": [m.disabled_dict() for m in disabled],
    }
    return report


# ---------------------------------------------------------------------------
# Rendering: Markdown
# ---------------------------------------------------------------------------


def _prop_summary(props: dict[str, Any]) -> str:
    parts = []
    for pname, spec in props.items():
        if isinstance(spec, dict):
            typ = spec.get("type", "")
            opts = spec.get("options")
            desc = spec.get("description", "")
            label = f"{typ}({'|'.join(opts)})" if opts else typ
            parts.append(f"{pname} ({label}{' — ' + desc if desc else ''})")
        else:
            parts.append(f"{pname} ({spec})")
    return ", ".join(parts)


def render_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    cov = s["coverage"]
    L: list[str] = []
    L.append("# Analytics report")
    L.append("")
    L.append(f"Workspace: `{report['workspace']}`  ")
    L.append(f"Generated by `{report['generated_by']}`")
    L.append("")
    L.append("## Summary")
    L.append("")
    L.append("| Slice | Packages | Events | Emit sites | Flow funnels | Untracked | Disabled |")
    L.append("|---|---|---|---|---|---|---|")
    for slc in ("app", "library"):
        d = s[slc]
        L.append(
            f"| **{slc}** | {len(d['packages'])} | {d['defined_events']} | "
            f"{d['emit_sites']} | {d['flow_funnels']} | {d['untracked']} | {d['disabled']} |"
        )
    L.append("")
    L.append(
        f"**Clickable outcomes** (by static marker): "
        f"tracked() {cov['tracked']} · flow {cov['flow']} · "
        f"explicitly untracked {cov['untracked']} · eslint-disabled {cov['disabled']}"
    )
    if s["packages_missing_events_json"]:
        L.append("")
        L.append(
            "> Note: no generated events.json for: "
            + ", ".join(f"`{p}`" for p in s["packages_missing_events_json"])
            + " — funnels/defined events for these degrade to available sources."
        )
    L.append("")

    for slc in ("app", "library"):
        slc_events = [e for e in report["events"] if e["slice"] == slc]
        slc_funnels = [f for f in report["flow_funnels"] if f["slice"] == slc]
        if not slc_events and not slc_funnels:
            continue
        L.append(f"## Events — {slc}")
        L.append("")
        for e in slc_events:
            L.append(f"### `{e['name']}` — {e['description'] or '—'}  [{slc}]")
            if e["props"]:
                L.append(f"props: {_prop_summary(e['props'])}")
            if e.get("flow"):
                L.append(f"flow: `{e['flow']}` (linked backend flow)")
            if e["emits"]:
                L.append("emitted at:")
                for em in e["emits"]:
                    comp = f" <{em['component']}>" if em.get("component") else ""
                    unres = " (unresolved)" if em.get("resolved") is False else ""
                    L.append(f"  - `{em['file']}:{em['line']}` via {em['wrapper']}(){comp}{unres}")
            else:
                L.append("emitted at: — (no static call site found)")
            L.append("")
        for f in slc_funnels:
            L.append(f"### `{f['event']}` (auto-instrumented) [{slc}]")
            L.append(f"package: `{f['package']}` · {len(f['steps'])} documented step(s)")
            kinds = " → ".join(str(st.get("kind", "")) for st in f["steps"])
            if kinds:
                L.append(f"steps: {kinds}")
            if f["props"]:
                L.append(f"props: {_prop_summary(f['props'])}")
            L.append("")

    # canonical flows
    if report["flows"]:
        L.append("## Flows (canonical backend + frontend coverage)")
        L.append("")
        for cf in report["flows"]:
            badge = ""
            if cf.get("gated_by"):
                badge = f"  [gated: {', '.join(cf['gated_by'])}]"
            L.append(f"### {cf['title'] or cf['id']} — `{cf['id']}`{badge}")
            if cf["description"] and not cf["description"].startswith("flow."):
                L.append(f"{cf['description']}")
            n_http = sum(1 for st in cf["steps"] if st["kind"] == "http")
            n_human = sum(1 for st in cf["steps"] if st["kind"] == "human")
            L.append(
                f"steps: {len(cf['steps'])} ({n_human} human, {n_http} http) · "
                f"actors: {', '.join(cf['actors']) or '—'}"
            )
            fe = cf["frontend"]
            if fe.get("funnel"):
                L.append(
                    f"frontend funnel: `{fe['funnel']}` · pairs: "
                    + (", ".join(f"`{p}`" for p in fe["packages"]) or "—")
                )
            if fe.get("machines"):
                L.append("machines: " + ", ".join(f"`{m}`" for m in fe["machines"]))
            if cf["app_events"]:
                L.append("linked app events: " + ", ".join(f"`{a}`" for a in cf["app_events"]))
            else:
                L.append("linked app events: — (none declared)")
            L.append("")

    # explicitly untracked
    L.append('## Explicitly untracked (data-analytics="none")')
    L.append("")
    if report["untracked"]:
        for m in report["untracked"]:
            L.append(f"- `{m['file']}:{m['line']}` [{m['slice']}] — {m['reason'] or '(no reason)'}")
    else:
        L.append("- (none)")
    L.append("")

    # explicitly disabled
    L.append("## Explicitly disabled (eslint-disable with description)")
    L.append("")
    if report["disabled"]:
        for m in report["disabled"]:
            L.append(
                f"- `{m['file']}:{m['line']}` [{m['slice']}] "
                f"`{m['rule']}` — {m['description']}"
            )
    else:
        L.append("- (none)")
    L.append("")

    return "\n".join(L)


# ---------------------------------------------------------------------------
# Rendering: HTML (self-contained, presentable — Studio passport, Q13)
# ---------------------------------------------------------------------------

_HTML_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  margin: 0; padding: 2.5rem clamp(1rem, 4vw, 4rem); max-width: 1100px;
  margin-inline: auto; color: #1a1c22; background: #fafafb; }
h1 { font-size: 1.9rem; margin: 0 0 .25rem; }
h2 { font-size: 1.3rem; margin: 2.2rem 0 .8rem; padding-bottom: .3rem;
  border-bottom: 2px solid #e3e5ea; }
h3 { font-size: 1.02rem; margin: 1.4rem 0 .4rem; }
.sub { color: #6b7280; font-size: .85rem; margin-bottom: 1.5rem; }
code, .mono { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: .86em; }
code { background: #eceef2; padding: .1em .35em; border-radius: 4px; }
.cards { display: flex; flex-wrap: wrap; gap: .8rem; margin: 1rem 0; }
.card { flex: 1 1 130px; background: #fff; border: 1px solid #e3e5ea; border-radius: 10px;
  padding: .9rem 1rem; box-shadow: 0 1px 2px rgba(0,0,0,.03); }
.card .n { font-size: 1.7rem; font-weight: 700; }
.card .l { color: #6b7280; font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: .9rem; }
th, td { text-align: left; padding: .5rem .7rem; border-bottom: 1px solid #e3e5ea; }
th { color: #6b7280; font-weight: 600; font-size: .8rem; text-transform: uppercase; }
.badge { display: inline-block; font-size: .72rem; font-weight: 600; padding: .1em .5em;
  border-radius: 20px; vertical-align: middle; margin-left: .4rem; }
.badge.app { background: #dbeafe; color: #1e40af; }
.badge.library { background: #dcfce7; color: #166534; }
.badge.gated { background: #fef3c7; color: #92400e; }
.ev { background: #fff; border: 1px solid #e3e5ea; border-radius: 10px; padding: 1rem 1.2rem;
  margin: .8rem 0; }
.ev .desc { color: #374151; }
.ev ul { margin: .4rem 0 0; padding-left: 1.2rem; }
.muted { color: #9096a1; }
.pill { font-size: .78rem; color: #4b5563; }
@media (prefers-color-scheme: dark) {
  body { background: #14151a; color: #e4e6eb; }
  h2 { border-color: #2a2d36; } h3 {}
  .card, .ev { background: #1c1e26; border-color: #2a2d36; }
  code { background: #2a2d36; } th, td { border-color: #2a2d36; }
  .sub, .card .l, .muted { color: #9096a1; }
}
"""


def _esc(text: Any) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_html(report: dict[str, Any]) -> str:
    s = report["summary"]
    cov = s["coverage"]
    H: list[str] = []
    H.append("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">")
    H.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    H.append("<title>Analytics report</title>")
    H.append(f"<style>{_HTML_CSS}</style></head><body>")
    H.append("<h1>Analytics report</h1>")
    H.append(
        f'<div class="sub">Workspace <code>{_esc(report["workspace"])}</code> · '
        f'{_esc(report["generated_by"])}</div>'
    )

    # summary cards
    H.append('<div class="cards">')
    for label, n in (
        ("tracked()", cov["tracked"]),
        ("flow actions", cov["flow"]),
        ("untracked", cov["untracked"]),
        ("disabled", cov["disabled"]),
    ):
        H.append(f'<div class="card"><div class="n">{n}</div><div class="l">{label}</div></div>')
    H.append("</div>")

    # slice table
    H.append("<h2>Summary by slice</h2><table><thead><tr>"
             "<th>Slice</th><th>Packages</th><th>Events</th><th>Emit sites</th>"
             "<th>Funnels</th><th>Untracked</th><th>Disabled</th></tr></thead><tbody>")
    for slc in ("app", "library"):
        d = s[slc]
        H.append(
            f"<tr><td><span class='badge {slc}'>{slc}</span></td>"
            f"<td>{len(d['packages'])}</td><td>{d['defined_events']}</td>"
            f"<td>{d['emit_sites']}</td><td>{d['flow_funnels']}</td>"
            f"<td>{d['untracked']}</td><td>{d['disabled']}</td></tr>"
        )
    H.append("</tbody></table>")

    # events
    for slc in ("app", "library"):
        slc_events = [e for e in report["events"] if e["slice"] == slc]
        slc_funnels = [f for f in report["flow_funnels"] if f["slice"] == slc]
        if not slc_events and not slc_funnels:
            continue
        H.append(f"<h2>Events — {slc}</h2>")
        for e in slc_events:
            H.append('<div class="ev">')
            H.append(
                f'<h3><code>{_esc(e["name"])}</code>'
                f'<span class="badge {slc}">{slc}</span></h3>'
            )
            H.append(f'<div class="desc">{_esc(e["description"] or "—")}</div>')
            if e["props"]:
                H.append(f'<div class="pill">props: {_esc(_prop_summary(e["props"]))}</div>')
            if e.get("flow"):
                H.append(f'<div class="pill">flow: <code>{_esc(e["flow"])}</code></div>')
            if e["emits"]:
                H.append("<ul>")
                for em in e["emits"]:
                    comp = f" &lt;{_esc(em['component'])}&gt;" if em.get("component") else ""
                    H.append(
                        f'<li><code>{_esc(em["file"])}:{em["line"]}</code> '
                        f'via {_esc(em["wrapper"])}(){comp}</li>'
                    )
                H.append("</ul>")
            else:
                H.append('<div class="muted">no static call site found</div>')
            H.append("</div>")
        for f in slc_funnels:
            H.append('<div class="ev">')
            H.append(
                f'<h3><code>{_esc(f["event"])}</code> '
                f'<span class="pill">auto-instrumented</span>'
                f'<span class="badge {slc}">{slc}</span></h3>'
            )
            kinds = " → ".join(_esc(st.get("kind", "")) for st in f["steps"])
            H.append(
                f'<div class="pill">package <code>{_esc(f["package"])}</code> · '
                f'{len(f["steps"])} step(s): {kinds}</div>'
            )
            H.append("</div>")

    # flows
    if report["flows"]:
        H.append("<h2>Flows (canonical + frontend coverage)</h2>")
        for cf in report["flows"]:
            badge = ""
            if cf.get("gated_by"):
                badge = f'<span class="badge gated">gated: {_esc(", ".join(cf["gated_by"]))}</span>'
            H.append('<div class="ev">')
            H.append(
                f'<h3>{_esc(cf["title"] or cf["id"])} '
                f'<code>{_esc(cf["id"])}</code>{badge}</h3>'
            )
            if cf["description"] and not cf["description"].startswith("flow."):
                H.append(f'<div class="desc">{_esc(cf["description"])}</div>')
            n_http = sum(1 for st in cf["steps"] if st["kind"] == "http")
            n_human = sum(1 for st in cf["steps"] if st["kind"] == "human")
            H.append(
                f'<div class="pill">steps: {len(cf["steps"])} '
                f'({n_human} human, {n_http} http) · actors: '
                f'{_esc(", ".join(cf["actors"]) or "—")}</div>'
            )
            fe = cf["frontend"]
            if fe.get("funnel"):
                H.append(
                    f'<div class="pill">frontend funnel <code>{_esc(fe["funnel"])}</code> · '
                    f'pairs {_esc(", ".join(fe["packages"]) or "—")}</div>'
                )
            if fe.get("machines"):
                H.append(
                    '<div class="pill">machines '
                    + ", ".join(f"<code>{_esc(m)}</code>" for m in fe["machines"])
                    + "</div>"
                )
            app_ev = ", ".join(cf["app_events"]) if cf["app_events"] else "— none declared"
            H.append(f'<div class="pill">linked app events: {_esc(app_ev)}</div>')
            H.append("</div>")

    # untracked / disabled
    H.append('<h2>Explicitly untracked (data-analytics="none")</h2>')
    if report["untracked"]:
        H.append("<table><thead><tr><th>Location</th><th>Slice</th><th>Reason</th></tr></thead><tbody>")
        for m in report["untracked"]:
            H.append(
                f'<tr><td><code>{_esc(m["file"])}:{m["line"]}</code></td>'
                f'<td>{m["slice"]}</td><td>{_esc(m["reason"] or "—")}</td></tr>'
            )
        H.append("</tbody></table>")
    else:
        H.append('<p class="muted">none</p>')

    H.append("<h2>Explicitly disabled (eslint-disable with description)</h2>")
    if report["disabled"]:
        H.append("<table><thead><tr><th>Location</th><th>Slice</th><th>Rule</th><th>Description</th></tr></thead><tbody>")
        for m in report["disabled"]:
            H.append(
                f'<tr><td><code>{_esc(m["file"])}:{m["line"]}</code></td>'
                f'<td>{m["slice"]}</td><td><code>{_esc(m["rule"])}</code></td>'
                f'<td>{_esc(m["description"])}</td></tr>'
            )
        H.append("</tbody></table>")
    else:
        H.append('<p class="muted">none</p>')

    H.append("</body></html>")
    return "".join(H)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _tool_version() -> str:
    try:
        from importlib.metadata import version

        return version("stapel-tools")
    except Exception:
        return "0"


def _load_backend_flows(path: str | None) -> list[dict[str, Any]] | None:
    if not path:
        return None
    if os.path.isdir(path):
        found = _find_first(path, "flows.json")
        path = found or os.path.join(path, "flows.json")
    data = _read_json(path)
    return data if isinstance(data, list) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-analytics-report",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "workspace",
        help="pnpm workspace dir (packages/*) or a single package/app dir",
    )
    parser.add_argument(
        "--package",
        metavar="DIR",
        action="append",
        help="Restrict to one package dir (repeatable); default = discover all",
    )
    parser.add_argument(
        "--backend-flows",
        metavar="PATH",
        help="Canonical backend flows.json (or a dir to search) for prose flow docs",
    )
    parser.add_argument(
        "--out",
        metavar="DIR",
        help="Write report.json + report.md + report.html into DIR",
    )
    parser.add_argument(
        "--format",
        choices=["json", "md", "html", "all"],
        default="json",
        help="What to print to stdout when --out is not given (default: json)",
    )
    parser.add_argument(
        "--capabilities",
        metavar="URL|FILE",
        help="(reserved, §3.4 env-aware mode — not yet implemented; ignored)",
    )
    args = parser.parse_args(argv)

    workspace = os.path.abspath(args.workspace)
    if not os.path.isdir(workspace):
        parser.error(f"not a directory: {workspace}")

    if args.package:
        pkg_dirs = [os.path.abspath(p) for p in args.package]
    else:
        pkg_dirs = discover_packages(workspace)
    if not pkg_dirs:
        parser.error(f"no packages found under {workspace} (need a package.json)")

    if args.capabilities:
        print(
            "note: --capabilities env-aware mode is reserved (§3.4) and ignored",
            file=sys.stderr,
        )

    backend_flows = _load_backend_flows(args.backend_flows)
    report = build_report(
        workspace, pkg_dirs, backend_flows=backend_flows, tool_version=_tool_version()
    )

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "report.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
            fh.write("\n")
        with open(os.path.join(args.out, "report.md"), "w", encoding="utf-8") as fh:
            fh.write(render_markdown(report))
        with open(os.path.join(args.out, "report.html"), "w", encoding="utf-8") as fh:
            fh.write(render_html(report))
        print(f"wrote report.json + report.md + report.html → {args.out}", file=sys.stderr)
    else:
        if args.format == "json":
            print(json.dumps(report, indent=2))
        elif args.format == "md":
            print(render_markdown(report))
        elif args.format == "html":
            print(render_html(report))
        else:
            print(json.dumps(report, indent=2))
            print(render_markdown(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
