"""
stapel-api-lint — the HTTP surface versioning gate (docs/pending/api-versioning.md
§3), in the ``stapel-migration-lint`` / ``stapel-url-lint`` idiom (rule codes,
``--json``, ``--strict``, exit 1 on any error).

Why this exists
----------------
Everything around this gate already existed and none of it closed the hole.
The contract pipeline (§17) emits ``docs/schema.json`` and checks it is
byte-identical to the committed copy — but a drift gate only says *the file
changed*, never *the change breaks a client*. Semver discipline
(library-standard §1.4) says "minor = breaking" — but nothing decides whether
an HTTP diff is breaking; the author does, from memory, in a hurry. The
result is the defect api-versioning.md was written against: a response field
quietly changes type in a patch release and the failure surfaces in someone
else's runtime.

This linter is the classifier that was missing. It diffs the OpenAPI document
committed at a baseline git ref against the one in the working tree, decides
additive-vs-breaking by the five rules of §3, and then holds the release to
the discipline that a breaking change is supposed to carry: a version bump, a
machine-readable ``UPGRADE.json`` record, and a new ``vN+1`` mounted *beside*
the old one rather than on top of it.

Deliberately no ``oasdiff`` dependency: the rule set is five bullets wide and
the whole point is that the classifier agrees with a written policy, not with
another tool's idea of compatibility.

Rules
-----
API001  (error) The schema diff against the baseline contains at least one
        BREAKING change (see below) and the release does not carry it: either
        the package version was not bumped far enough (pre-1.0 a breaking
        change needs a MINOR bump, post-1.0 a MAJOR one — library-standard
        §1.4), or ``docs/UPGRADE.json`` has no ``kind: "api_change"`` record
        for the new version. Both halves are required; a bump with no record
        leaves the consuming project's ``stapel-upgrade plan`` blind, and a
        record with no bump leaves the pin range unable to exclude it.

        Breaking, per api-versioning.md §3:
          1. an endpoint (path + method) is removed or renamed;
          2. a field is removed, renamed, or changes type; and a
             required-status flip in the direction that breaks the caller
             — optional -> required in a REQUEST body (callers omit it
             today), required -> optional in a RESPONSE body (callers read
             it unconditionally). The opposite flip on each side is a
             strengthening and stays additive;
          3. a response status code disappears from an existing operation
             (the error semantics of a case that used to be reachable);
          4. an operation's auth contract changes (security requirements);
          5. an enum value is removed — always breaking, regardless of the
             open/closed policy — or added to an enum explicitly marked
             ``x-stapel-closed-enum``.

        Enum policy (api-versioning.md "owner decision" 1, recommended
        option): enums are OPEN by default, so ADDING a value is additive and
        clients are expected to ignore what they do not know. A field whose
        schema carries ``x-stapel-closed-enum: true`` is closed and a new
        value there is breaking — the case that motivates the flag is a role
        / permission / billing-status vocabulary, where an old client
        interpreting an unknown value as "not that one" is a security answer,
        not a display glitch.

API002  (error) A breaking change landed IN PLACE: the paths of an existing
        ``vN`` changed shape without a ``vN+1`` appearing next to it. The old
        version must keep serving its frozen shape while the new one is added
        beside it (the HTTP twin of the expand/contract rule migration-lint
        enforces for columns). Also fires when ``vN+1`` was added but the
        module's ``urls.py`` no longer mounts ``urls_vN.py`` — a version that
        is in the schema but not in the URLconf is not actually served.

API003  (error) A version ``vN`` present at the baseline is gone from the
        current schema before its sunset came due: either the operations
        never declared an ``x-stapel-sunset`` date (§2.4 — the deprecation
        window was never opened, so it cannot have closed), or the declared
        date is still in the future. Symmetric to migration-lint's
        reversible-floor check: you may not drop the old shape in the same
        release that stops needing it.

SCHEMA001 (warning) ``docs/schema.json``'s ``info`` block diverges from the
        fleet convention.

        The convention (owner decision, and the state of all 24 libs):
        ``info.version`` is NOT the contract's version. Every per-lib emitter
        leaves ``SPECTACULAR_SETTINGS`` unset in its ``_codegen_settings.py``
        so its triad stays byte-identical to the monolith aggregate's slice —
        and the aggregate runs on drf defaults, which emit
        ``info.version: "0.0.0"`` with an empty ``info.title``. That pair is
        therefore the CORRECT state and is silent here. The version of the
        contract lives where a consumer can actually pin it: ``version`` in
        ``pyproject.toml`` and ``backend.contract`` in the pair's
        ``manifest.json``.

        So the rule fires on divergence FROM the convention, not on the
        convention itself:
          * ``info.version == "0.0.0"`` and ``info.title == ""`` — clean;
          * a lib that writes its package version into ``info.version``
            (``package=``/``version=`` passed to ``get_spectacular_settings``
            in the codegen settings) — flagged: the emitted slice no longer
            matches the aggregate byte-for-byte, which is what the whole
            per-lib triad exists to guarantee;
          * anything else (a stale hand-set version, a non-empty title beside
            the placeholder) — flagged as before.

        Warning rather than error: a divergence is a contract-pipeline defect
        to fix, not a release blocker, and ``--strict`` promotes it. Named
        next to REL001/REL002 in the release manifest, per §3.4.

Baseline
--------
``--base-ref`` names the git ref whose ``docs/schema.json`` is the "before".
Default: the newest ``v<semver>`` tag reachable from HEAD — the last release,
which is the thing a consumer actually pinned. No git repo, no tag, or no
schema at that ref means there is no "before": the linter says so in a note
and reports nothing. A gate that invents a baseline is worse than a gate that
admits it has none.

Exit codes: 0 clean (warnings allowed), 1 errors present (or warnings under
``--strict``), 2 usage/environment errors.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

SCHEMA_REL = "docs/schema.json"
UPGRADE_REL = "docs/UPGRADE.json"

HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)

#: The canonical version segment (api-versioning.md §2.1): right after ``api/``
#: and nowhere else, so a resource literally named ``/v2/`` cannot be mistaken
#: for a surface version.
API_VERSION_RE = re.compile(r"/api/v(\d+)(?=/|$)")

#: Marks an enum whose vocabulary is closed, making an ADDED value breaking.
CLOSED_ENUM_KEY = "x-stapel-closed-enum"
SUNSET_KEY = "x-stapel-sunset"

#: Which half of the call a body belongs to. See :func:`_diff_fields`.
REQUEST = "request"
RESPONSE = "response"

_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)")
_MAX_LISTED = 12


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    message: str
    level: str = "error"  # "error" | "warning"

    def __str__(self) -> str:
        tag = self.rule if self.level == "error" else f"{self.rule} warning"
        return f"{self.path}:{self.line}: [{tag}] {self.message}"

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "line": self.line,
            "rule": self.rule,
            "message": self.message,
            "level": self.level,
        }


@dataclass(frozen=True)
class Change:
    """One classified difference between two OpenAPI documents."""

    kind: str      # endpoint | field | status | security | enum
    where: str     # human-addressable location ("POST /x/api/v1/y: field a.b")
    detail: str

    def __str__(self) -> str:
        return f"{self.where} — {self.detail}"


@dataclass(frozen=True)
class Shape:
    """What the classifier can see of one field, flattened out of the schema."""

    type: str = ""
    fmt: str = ""
    required: bool = False
    enum: tuple = ()
    closed_enum: bool = False
    nullable: bool = False


# ---------------------------------------------------------------------------
# OpenAPI walking — $ref resolution, allOf merge, property flattening
# ---------------------------------------------------------------------------


def _deref(node, doc: dict, _budget: int = 8):
    """Resolve a local ``$ref`` against *doc*. Foreign or unresolvable refs
    come back untouched — an unknown ref is not a diff, and pretending to
    resolve it would invent fields on both sides."""
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < _budget:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return node
        target = doc
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                return node
            target = target[part]
        node = target
        seen += 1
    return node


def _ref_name(node) -> str:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            return ref
    return ""


def _merge_allof(node: dict, doc: dict) -> dict:
    """Flatten ``allOf`` into one node. drf-spectacular emits a lone
    ``allOf: [$ref]`` wrapper whenever a property carries a description
    alongside a component reference, which is most enum fields — without this
    every such field would look typeless to the classifier."""
    parts = node.get("allOf")
    if not isinstance(parts, list):
        return node
    merged: dict = {k: v for k, v in node.items() if k != "allOf"}
    required: list = list(merged.get("required") or [])
    props: dict = dict(merged.get("properties") or {})
    for part in parts:
        part = _deref(part, doc)
        if not isinstance(part, dict):
            continue
        part = _merge_allof(part, doc)
        for key, value in part.items():
            if key == "properties" and isinstance(value, dict):
                props.update(value)
            elif key == "required" and isinstance(value, list):
                required.extend(value)
            else:
                merged.setdefault(key, value)
    if props:
        merged["properties"] = props
    if required:
        merged["required"] = sorted(set(required))
    return merged


def _shape(node: dict, required: bool) -> Shape:
    enum = node.get("enum")
    values: tuple = ()
    if isinstance(enum, list):
        values = tuple(sorted(repr(v) for v in enum))
    return Shape(
        type=str(node.get("type") or ""),
        fmt=str(node.get("format") or ""),
        required=required,
        enum=values,
        closed_enum=bool(node.get(CLOSED_ENUM_KEY)),
        nullable=bool(node.get("nullable")),
    )


def flatten_schema(node, doc: dict, prefix: str = "", *, _seen=frozenset(), _depth=0) -> dict:
    """``{dotted.field.path: Shape}`` for one schema node.

    Recursion stops on a ``$ref`` already open on this path (self-referential
    components — a comment tree, a category parent — are common and would
    otherwise never terminate) and at a depth cap; a field nested nine levels
    deep is not what this gate is for.
    """
    out: dict = {}
    name = _ref_name(node)
    if name and name in _seen:
        return out
    if _depth > 8:
        return out
    seen = _seen | {name} if name else _seen
    node = _deref(node, doc)
    if not isinstance(node, dict):
        return out
    node = _merge_allof(node, doc)

    props = node.get("properties")
    if isinstance(props, dict):
        required = set(node.get("required") or [])
        for key, sub in props.items():
            resolved = _deref(sub, doc)
            if not isinstance(resolved, dict):
                continue
            resolved = _merge_allof(resolved, doc)
            dotted = f"{prefix}{key}"
            out[dotted] = _shape(resolved, key in required)
            out.update(
                flatten_schema(
                    sub, doc, f"{dotted}.", _seen=seen, _depth=_depth + 1
                )
            )
    items = node.get("items")
    if isinstance(items, dict):
        out.update(
            flatten_schema(items, doc, f"{prefix}[].", _seen=seen, _depth=_depth + 1)
        )
    return out


def _iter_operations(doc: dict) -> Iterator[tuple]:
    paths = doc.get("paths")
    if not isinstance(paths, dict):
        return
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() in HTTP_METHODS and isinstance(op, dict):
                yield path, method.lower(), op


def _security_of(op: dict, doc: dict):
    """Normalised auth contract of one operation: the sorted set of scheme
    names of each alternative. Only the scheme identity matters here — a
    scope list changing is a scope change the schema cannot judge, the
    *scheme* changing (cookie -> header) is the §3 case."""
    sec = op.get("security")
    if sec is None:
        sec = doc.get("security")
    if not isinstance(sec, list):
        return None
    alternatives = []
    for entry in sec:
        if isinstance(entry, dict):
            alternatives.append(tuple(sorted(entry)))
    return tuple(sorted(alternatives))


def _bodies(op: dict, doc: dict) -> dict:
    """``{label: schema}`` for every request/response body of an operation."""
    out: dict = {}
    body = _deref(op.get("requestBody"), doc)
    if isinstance(body, dict):
        for media, spec in (body.get("content") or {}).items():
            if isinstance(spec, dict) and "schema" in spec:
                out[f"request({media})"] = spec["schema"]
    responses = op.get("responses")
    if isinstance(responses, dict):
        for code, spec in responses.items():
            spec = _deref(spec, doc)
            if not isinstance(spec, dict):
                continue
            for media, mspec in (spec.get("content") or {}).items():
                if isinstance(mspec, dict) and "schema" in mspec:
                    out[f"response {code}({media})"] = mspec["schema"]
    return out


def _status_codes(op: dict) -> set:
    responses = op.get("responses")
    if not isinstance(responses, dict):
        return set()
    return {str(code) for code in responses}


# ---------------------------------------------------------------------------
# the classifier — api-versioning.md §3
# ---------------------------------------------------------------------------


def classify_schema_diff(old: dict, new: dict) -> list[Change]:
    """Every BREAKING change between two OpenAPI documents, per §3.

    Additive differences (a new endpoint, a new optional field, a new
    response code, a value added to an open enum) are deliberately NOT
    returned: this function answers "must this release carry a version
    story?", and the answer for an additive release is no.
    """
    changes: list[Change] = []
    old_ops = {(p, m): op for p, m, op in _iter_operations(old)}
    new_ops = {(p, m): op for p, m, op in _iter_operations(new)}

    # 1. endpoint removed or renamed (a rename is a removal plus an addition;
    #    the removal half is what breaks the caller)
    for key in sorted(old_ops.keys() - new_ops.keys()):
        path, method = key
        changes.append(Change(
            "endpoint", f"{method.upper()} {path}",
            "endpoint removed or renamed — callers of this path get a 404",
        ))

    for key in sorted(old_ops.keys() & new_ops.keys()):
        path, method = key
        old_op, new_op = old_ops[key], new_ops[key]
        where = f"{method.upper()} {path}"

        # 3. a response status code that used to be reachable is gone
        for code in sorted(_status_codes(old_op) - _status_codes(new_op)):
            changes.append(Change(
                "status", where,
                f"response {code} removed — a case the client handles "
                f"explicitly no longer exists",
            ))

        # 4. auth contract change
        old_sec, new_sec = _security_of(old_op, old), _security_of(new_op, new)
        if old_sec is not None and new_sec is not None and old_sec != new_sec:
            changes.append(Change(
                "security", where,
                f"auth contract changed: {_fmt_sec(old_sec)} -> {_fmt_sec(new_sec)}",
            ))

        # 2 + 5. field and enum shape, per body
        old_bodies, new_bodies = _bodies(old_op, old), _bodies(new_op, new)
        for label in sorted(old_bodies.keys() & new_bodies.keys()):
            changes.extend(_diff_fields(
                flatten_schema(old_bodies[label], old),
                flatten_schema(new_bodies[label], new),
                f"{where} {label}",
                direction=REQUEST if label.startswith("request") else RESPONSE,
            ))
        for label in sorted(old_bodies.keys() - new_bodies.keys()):
            changes.append(Change(
                "field", f"{where} {label}",
                "request/response body removed",
            ))
    return changes


def _fmt_sec(sec) -> str:
    if not sec:
        return "none"
    return " | ".join("+".join(alt) or "anonymous" for alt in sec) or "anonymous"


def _diff_fields(old: dict, new: dict, where: str, *, direction: str) -> list[Change]:
    """Field-shape diff for one body, read from the CLIENT's side.

    ``direction`` is load-bearing and not a detail: a required-status flip
    breaks in opposite directions for the two halves of a call, and a
    classifier that ignores that either misses the real breakage or cries
    wolf on a strengthening — and a gate that cries wolf gets routed around
    within a week, which costs more than not having it.

      request   optional -> required  breaks (callers omit the field today)
                required -> optional  is additive
      response  required -> optional  breaks (callers read it unconditionally)
                optional -> required  is additive (the server promises more)

    Nullability reads the same way: narrowing what a caller may SEND breaks a
    request, widening what a caller may RECEIVE breaks a response.
    """
    changes: list[Change] = []
    for name in sorted(old.keys() - new.keys()):
        changes.append(Change(
            "field", f"{where}: {name}",
            "field removed or renamed",
        ))
    for name in sorted(old.keys() & new.keys()):
        before, after = old[name], new[name]
        at = f"{where}: {name}"
        if before.type != after.type:
            changes.append(Change(
                "field", at, f"type changed {before.type or '?'} -> {after.type or '?'}",
            ))
        elif before.fmt != after.fmt:
            changes.append(Change(
                "field", at, f"format changed {before.fmt or '-'} -> {after.fmt or '-'}",
            ))
        if direction == REQUEST:
            if not before.required and after.required:
                changes.append(Change(
                    "field", at,
                    "optional -> required — existing callers omit it and now fail",
                ))
            if before.nullable and not after.nullable:
                changes.append(Change(
                    "field", at,
                    "nullable -> non-nullable — callers that send null now fail",
                ))
        else:
            if before.required and not after.required:
                changes.append(Change(
                    "field", at,
                    "required -> optional — callers read it unconditionally and "
                    "now get nothing",
                ))
            if not before.nullable and after.nullable:
                changes.append(Change(
                    "field", at,
                    "non-nullable -> nullable — callers did not model a null here",
                ))
        changes.extend(_diff_enum(before, after, at))
    return changes


def _diff_enum(before: Shape, after: Shape, at: str) -> list[Change]:
    """§3 rule 5 + owner decision 1. Removal always breaks. Addition breaks
    only for an enum that declared itself closed."""
    if not before.enum or not after.enum:
        return []
    changes: list[Change] = []
    removed = sorted(set(before.enum) - set(after.enum))
    if removed:
        changes.append(Change(
            "enum", at,
            f"enum value(s) removed: {', '.join(removed)} — always breaking, "
            f"open or closed",
        ))
    added = sorted(set(after.enum) - set(before.enum))
    if added and (before.closed_enum or after.closed_enum):
        changes.append(Change(
            "enum", at,
            f"value(s) {', '.join(added)} added to an enum marked "
            f"{CLOSED_ENUM_KEY} — a client that maps the closed vocabulary "
            f"exhaustively cannot interpret them",
        ))
    return changes


# ---------------------------------------------------------------------------
# surface versions
# ---------------------------------------------------------------------------


def schema_versions(doc: dict) -> dict:
    """``{version int: [paths]}`` — the surface versions this document serves."""
    out: dict = {}
    paths = doc.get("paths")
    if not isinstance(paths, dict):
        return out
    for path in paths:
        match = API_VERSION_RE.search(path)
        if match:
            out.setdefault(int(match.group(1)), []).append(path)
    return out


def sunset_dates(doc: dict, version: int) -> list:
    """Every ``x-stapel-sunset`` declared on an operation of ``vN`` (§2.4)."""
    dates = []
    for path, _method, op in _iter_operations(doc):
        match = API_VERSION_RE.search(path)
        if not match or int(match.group(1)) != version:
            continue
        raw = op.get(SUNSET_KEY)
        if isinstance(raw, str):
            try:
                dates.append(date.fromisoformat(raw[:10]))
            except ValueError:
                continue
    return dates


# ---------------------------------------------------------------------------
# git plumbing — the baseline document
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True, check=False,
    )


def git_root(path: Path) -> Optional[Path]:
    proc = _git(path, "rev-parse", "--show-toplevel")
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return Path(out) if out else None


def latest_release_ref(root: Path) -> Optional[str]:
    """Newest ``v<semver>`` tag reachable from HEAD — the release a consumer
    could actually have pinned. ``--merged HEAD`` matters: a tag on a branch
    nobody merged is not this repo's "before"."""
    proc = _git(root, "tag", "--list", "v*", "--merged", "HEAD", "--sort=-v:refname")
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        tag = line.strip()
        if _TAG_RE.match(tag):
            return tag
    return None


def blob_at(root: Path, ref: str, relpath: str) -> Optional[str]:
    proc = _git(root, "show", f"{ref}:{relpath}")
    if proc.returncode != 0:
        return None
    return proc.stdout


def _json_or_none(text: Optional[str]):
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# versions and upgrade records
# ---------------------------------------------------------------------------


def _parse_version(raw) -> Optional[tuple]:
    if not isinstance(raw, str):
        return None
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", raw.strip())
    if not match:
        return None
    return tuple(int(g) for g in match.groups())


def read_project_version(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    try:
        data = tomllib.loads(text)
    except (ValueError, tomllib.TOMLDecodeError):
        return None
    version = data.get("project", {}).get("version")
    return version if isinstance(version, str) else None


def bump_is_sufficient(before: str, after: str) -> bool:
    """library-standard §1.4: pre-1.0 a breaking change needs a MINOR bump,
    from 1.0 on it needs a MAJOR one. Anything less (a patch, or no bump at
    all) cannot carry a breaking HTTP change — which is precisely the
    "forbidden" bullet of §3."""
    old, new = _parse_version(before), _parse_version(after)
    if old is None or new is None:
        return True  # unparseable version — not this linter's finding to make
    if old[0] == 0 and new[0] == 0:
        return new[1] > old[1]
    return new[0] > old[0]


def upgrade_records(project: Path) -> list:
    """``kind: api_change`` records from ``docs/UPGRADE.json`` (upgrade-
    pipeline.md §2). Accepts both the bare-list and the ``{"entries": [...]}``
    envelope so the gate does not fight the file's final shape."""
    path = project / UPGRADE_REL
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        data = data.get("entries") or data.get("records") or []
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict) and e.get("kind") == "api_change"]


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------


# The drf-spectacular defaults the whole fleet emits under: a per-lib triad is
# byte-identical to the monolith aggregate's slice only if its emitter runs on
# the same (unset) SPECTACULAR_SETTINGS the aggregate does.
CONVENTION_INFO_VERSION = "0.0.0"
CONVENTION_INFO_TITLE = ""

CONVENTION_NOTE = (
    'the convention is that info.version is NOT the contract version: every lib '
    'leaves SPECTACULAR_SETTINGS unset in _codegen_settings.py, so the emitted '
    'info block is the drf default (version "0.0.0", empty title) and the triad '
    'stays byte-identical to the monolith aggregate. The version lives in '
    "pyproject.toml and in the pair's manifest.json (backend.contract)"
)


def check_schema001(project: Path, doc: Optional[dict], pyproject_version: Optional[str]) -> list:
    """SCHEMA001 — the emitted ``info`` block must follow the fleet convention."""
    if doc is None or not pyproject_version:
        return []
    info = doc.get("info")
    if not isinstance(info, dict):
        return []
    declared = info.get("version")
    title = info.get("title")
    if declared == CONVENTION_INFO_VERSION and title == CONVENTION_INFO_TITLE:
        return []
    if declared == CONVENTION_INFO_VERSION:
        message = (
            f"info.title is {title!r}, not '' — info.version follows the convention but "
            f"the title does not, so the emitted slice diverges from the aggregate: "
            f"{CONVENTION_NOTE}"
        )
    elif declared == pyproject_version:
        message = (
            f"info.version is {declared!r} — this lib writes its package version into "
            f"the emitted contract, so its slice no longer matches the aggregate: "
            f"{CONVENTION_NOTE}. Drop package=/version= from get_spectacular_settings "
            f"and re-emit the contract"
        )
    else:
        message = (
            f"info.version is {declared!r} but the package is {pyproject_version!r} — "
            f"the emitted contract matches neither the release nor the convention: "
            f"{CONVENTION_NOTE}"
        )
    return [Finding(SCHEMA_REL, 1, "SCHEMA001", message, level="warning")]


def check_api001(project: Path, breaking: list, before_version: Optional[str],
                 after_version: Optional[str], base_ref: str) -> list:
    """API001 — a breaking diff must be carried by a bump AND a record."""
    if not breaking:
        return []
    missing: list[str] = []
    if before_version and after_version and not bump_is_sufficient(
        before_version, after_version
    ):
        floor = "minor" if (_parse_version(after_version) or (0,))[0] == 0 else "major"
        missing.append(
            f"the package went {before_version} -> {after_version}, which is not a "
            f"{floor} bump (library-standard §1.4)"
        )
    records = upgrade_records(project)
    if not records:
        missing.append(
            f"{UPGRADE_REL} carries no 'kind: api_change' record — "
            f"stapel-upgrade plan cannot build the client-side codemod without it"
        )
    elif after_version and not any(
        r.get("version") in (None, after_version) for r in records
    ):
        missing.append(
            f"{UPGRADE_REL} has api_change record(s), but none for {after_version}"
        )
    if not missing:
        return []
    listed = "\n    ".join(str(c) for c in breaking[:_MAX_LISTED])
    more = "" if len(breaking) <= _MAX_LISTED else (
        f"\n    ... and {len(breaking) - _MAX_LISTED} more"
    )
    return [Finding(
        SCHEMA_REL, 1, "API001",
        f"{len(breaking)} breaking HTTP change(s) since {base_ref}, uncarried: "
        + "; ".join(missing)
        + f".\n    {listed}{more}",
    )]


def check_api002(project: Path, breaking: list, before_versions: dict,
                 after_versions: dict, base_ref: str) -> list:
    """API002 — a breaking change may not land in place; the new version has
    to be mounted beside the old one."""
    if not breaking:
        return []
    findings: list[Finding] = []
    added = sorted(set(after_versions) - set(before_versions))
    if not added:
        served = ", ".join(f"v{n}" for n in sorted(after_versions)) or "none"
        findings.append(Finding(
            SCHEMA_REL, 1, "API002",
            f"{len(breaking)} breaking change(s) since {base_ref} landed IN PLACE "
            f"on the existing surface (versions served: {served}) — a breaking "
            f"change must add /api/v{max(after_versions, default=1) + 1}/ beside "
            f"the frozen one, not reshape it (api-versioning.md §2.3, §3)",
        ))
        return findings
    # A new version appeared: every older one must still be both in the schema
    # and mounted in the URLconf. Schema presence is checked by API003 (which
    # knows about sunsets); here we check the mount, because a version that is
    # in the emitted document but absent from urls.py is not actually served.
    for version in sorted(before_versions):
        if version not in after_versions:
            continue  # gone entirely — API003's case, with the sunset question
        module_file = project / f"urls_v{version}.py"
        root_urls = project / "urls.py"
        if not module_file.is_file():
            continue  # module does not use the urls_vN.py layout — nothing to assert
        mounted = False
        if root_urls.is_file():
            try:
                mounted = f"urls_v{version}" in root_urls.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                mounted = True  # unreadable file is not evidence of a defect
        if not mounted:
            findings.append(Finding(
                "urls.py", 1, "API002",
                f"v{version} still ships {module_file.name} and still appears in "
                f"the schema, but urls.py does not mount it — the deprecated "
                f"version is documented and not served",
            ))
    return findings


def check_api003(before_doc: Optional[dict], before_versions: dict,
                 after_versions: dict, base_ref: str, today: date) -> list:
    """API003 — no version disappears before its declared sunset."""
    findings: list[Finding] = []
    for version in sorted(set(before_versions) - set(after_versions)):
        dates = sunset_dates(before_doc or {}, version)
        if not dates:
            findings.append(Finding(
                SCHEMA_REL, 1, "API003",
                f"/api/v{version}/ served {len(before_versions[version])} path(s) at "
                f"{base_ref} and is gone, but it never declared "
                f"{SUNSET_KEY} — a deprecation window that was never opened "
                f"cannot have closed (api-versioning.md §2.4, §3)",
            ))
            continue
        due = max(dates)
        if due > today:
            findings.append(Finding(
                SCHEMA_REL, 1, "API003",
                f"/api/v{version}/ removed before its sunset {due.isoformat()} "
                f"(today {today.isoformat()}) — clients were promised that date",
            ))
    return findings


# ---------------------------------------------------------------------------
# project entry point
# ---------------------------------------------------------------------------


def lint_project(
    project: Path,
    *,
    base_ref: Optional[str] = None,
    notes: Optional[list] = None,
    today: Optional[date] = None,
) -> list[Finding]:
    """Every API rule against *project*. Silent when the repo has no emitted
    HTTP contract — a module without ``docs/schema.json`` has no HTTP surface
    this gate can speak about."""
    project = Path(project)
    today = today or datetime.now().date()
    note = notes.append if notes is not None else (lambda _msg: None)

    schema_path = project / SCHEMA_REL
    current = _json_or_none(
        schema_path.read_text(encoding="utf-8") if schema_path.is_file() else None
    )
    if current is None:
        note(f"api-lint: no {SCHEMA_REL} in {project} — no HTTP contract to check.")
        return []

    pyproject_path = project / "pyproject.toml"
    current_version = read_project_version(
        pyproject_path.read_text(encoding="utf-8") if pyproject_path.is_file() else None
    )

    findings: list[Finding] = check_schema001(project, current, current_version)

    root = git_root(project)
    if root is None:
        note(f"api-lint: {project} is not inside a git repo — no baseline, "
             f"API001-003 not checked.")
        return findings
    ref = base_ref or latest_release_ref(root)
    if not ref:
        note(f"api-lint: no v<semver> tag reachable from HEAD in {root} — "
             f"no baseline, API001-003 not checked.")
        return findings

    try:
        rel = str(schema_path.resolve().relative_to(root.resolve()))
    except ValueError:  # pragma: no cover - schema outside its own git root
        note(f"api-lint: {schema_path} is outside {root} — baseline not resolvable.")
        return findings

    before = _json_or_none(blob_at(root, ref, rel))
    if before is None:
        note(f"api-lint: {rel} does not exist at {ref} — first emitted contract, "
             f"nothing to diff against.")
        return findings

    note(f"api-lint: baseline {ref}")
    try:
        pyproject_rel = str(
            (project / "pyproject.toml").resolve().relative_to(root.resolve())
        )
    except ValueError:  # pragma: no cover
        pyproject_rel = "pyproject.toml"
    before_version = read_project_version(blob_at(root, ref, pyproject_rel))

    breaking = classify_schema_diff(before, current)
    before_versions = schema_versions(before)
    after_versions = schema_versions(current)

    findings += check_api001(project, breaking, before_version, current_version, ref)
    findings += check_api002(project, breaking, before_versions, after_versions, ref)
    findings += check_api003(before, before_versions, after_versions, ref, today)
    return findings


def lint_paths(
    paths: Iterable, *, base_ref: Optional[str] = None, notes: Optional[list] = None
) -> list[Finding]:
    findings: list[Finding] = []
    for raw in paths:
        findings += lint_project(Path(raw), base_ref=base_ref, notes=notes)
    return findings


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-api-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_dir", nargs="?", default=".", help="Project root.")
    parser.add_argument(
        "--base-ref",
        default=None,
        help="Git ref whose docs/schema.json is the baseline "
             "(default: newest v<semver> tag reachable from HEAD).",
    )
    parser.add_argument("--json", action="store_true", help="Machine output.")
    parser.add_argument(
        "--strict", action="store_true", help="Fail on warnings as well as errors."
    )
    args = parser.parse_args(argv)

    project = Path(args.project_dir)
    if not project.is_dir():
        print(f"Error: not a directory: {project}", file=sys.stderr)
        return 2

    notes: list[str] = []
    findings = lint_project(project, base_ref=args.base_ref, notes=notes)
    errors = sum(1 for f in findings if f.level == "error")
    warnings = len(findings) - errors

    if args.json:
        print(json.dumps(
            {
                "ok": errors == 0 and not (args.strict and warnings),
                "errors": errors,
                "warnings": warnings,
                "findings": [f.to_dict() for f in findings],
                "notes": notes,
            },
            indent=2, sort_keys=True, ensure_ascii=False,
        ))
    else:
        for note in notes:
            print(note, file=sys.stderr)
        for finding in findings:
            print(finding)
        if errors:
            print(f"stapel-api-lint: {errors} error(s) in {project}")
        elif warnings:
            print(f"stapel-api-lint: {warnings} warning(s) in {project}")
        else:
            print(f"stapel-api-lint: clean ({project})")
    if errors:
        return 1
    return 1 if (args.strict and warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
