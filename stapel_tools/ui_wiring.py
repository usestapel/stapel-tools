"""Static UI wiring check: the generated ``scripts/ui-wiring.mjs`` and its make target.

A UI manifest names, per acceptance criterion, the SPA pages a feature must
render: route, h1, ``data-testid`` values and actions (``{testid, handler,
request: {method, path}}``). ``make frontend-wiring MANIFEST=<file|dir>`` runs
the script over ``<frontend>/src`` with the TypeScript compiler API installed
in ``<frontend>/node_modules``: red when a testid is not rendered, an action's
element carries no handler, its request (full path, base included, evaluated
from constant parts) is issued nowhere in reach, or an API path in a page's
scope sits outside the project's canon base (``API_BASE``). What cannot be
resolved statically is UNVERIFIABLE: never green, exit 3. One run takes about
a second; nothing is built or served.

The script lives at the repo root (``scripts/``), not under ``frontend/``: the
frontend's own lint, build and test sets stay as generated.

Versions. A generated project carries no stapel-tools dependency, so the only
statement of which checker it runs is the script itself: line 2 stamps the
stapel-tools version, the output protocol and the sha256 of the shipped text.
:func:`backfill_decision` is the policy a caller that writes the script into
an existing project applies (Studio's ui leg): keep a pristine script of the
current protocol (the project's pin), upgrade a pristine one of an older
protocol (an older checker answers green on what this one is red on), refuse
an edited script or one newer than the caller's stapel-tools.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from importlib import resources

#: Where the scaffold writes the script, relative to the project root.
SCRIPT_PATH = "scripts/ui-wiring.mjs"
#: The root Makefile target that runs it.
MAKE_TARGET = "frontend-wiring"
#: Default manifest location (a directory of ``<task>.json``).
MANIFEST_DIR = "contracts/ui"
#: The last line of every run: ``UI_WIRING_RESULT <json>``.
RESULT_TAG = "UI_WIRING_RESULT"
#: Printed before the result when the check could not run (exit 2).
ERROR_TAG = "UI_WIRING_ERROR"
#: Output protocol. 1: 0.74.0 (path after ``/api`` only, PASS/FAIL).
#: 2: full request path with its base, ``base`` check, UNVERIFIABLE (exit 3).
PROTOCOL = 2

#: The 0.74.0 script carried no stamp; its text is known.
_UNSTAMPED = {
    "55ddc6c10e2a1083f88f03d8e5681c860b827bddf22a654fcb5d493949cfe322": ("0.74.0", 1),
}
_STAMP_RE = re.compile(
    r"^// stapel-tools (?P<version>\S+) ui-wiring protocol (?P<protocol>\d+) "
    r"sha256 (?P<sha>[0-9a-f]{64})$", re.M)


def makefile_block(*, api_base: str = "", frontend: str = "frontend") -> str:
    """The make block that runs the script. *api_base* is the project's
    canon (``/<slug>/api``); empty, the canon comes from the manifest.
    ``frontend-install`` is not a prerequisite here (the scaffold leaves the
    install to the caller); a caller that owns that target adds
    ``frontend-wiring: frontend-install``."""
    return f"""
# --- Static UI wiring (stapel-tools ui_wiring; needs {frontend}/node_modules) ---
# MANIFEST is one JSON file or a directory of them: pages, testids and actions
# per acceptance criterion. Red when a testid is not rendered, an action's
# element has no handler or its request (full path) is issued nowhere, or an
# API path sits outside API_BASE. Last line: {RESULT_TAG} <json>.
MANIFEST ?= {MANIFEST_DIR}
API_BASE ?={(' ' + api_base) if api_base else ''}
.PHONY: {MAKE_TARGET}
{MAKE_TARGET}:
\tnode {SCRIPT_PATH} --frontend {frontend} --api-base "$(API_BASE)" $(MANIFEST)
"""


#: The block with no canon and the frontend under ``frontend/``.
MAKEFILE_BLOCK = makefile_block()


def _raw() -> str:
    return resources.files("stapel_tools").joinpath("data/ui-wiring.mjs").read_text(
        encoding="utf-8")


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("stapel-tools")
    except Exception:  # pragma: no cover - not installed as a distribution
        return "unknown"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def script_source() -> str:
    """The script the scaffold writes to :data:`SCRIPT_PATH`: the shipped text
    with the stamp after its first line."""
    raw = _raw()
    first, rest = raw.split("\n", 1)
    stamp = f"// stapel-tools {_version()} ui-wiring protocol {PROTOCOL} sha256 {_sha(raw)}"
    return f"{first}\n{stamp}\n{rest}"


@dataclass(frozen=True)
class ScriptStamp:
    """What a project's script is: ``pristine`` False means edited or unknown."""

    version: str
    protocol: int
    pristine: bool


def inspect_script(text: str) -> ScriptStamp:
    """Read the stamp of a project's script and check the text against it."""
    match = _STAMP_RE.search(text or "")
    if match:
        body = text[:match.start()] + text[match.end() + 1:]
        return ScriptStamp(match["version"], int(match["protocol"]), _sha(body) == match["sha"])
    known = _UNSTAMPED.get(_sha(text or ""))
    if known:
        return ScriptStamp(known[0], known[1], True)
    return ScriptStamp("unknown", 0, False)


@dataclass(frozen=True)
class Backfill:
    """``action``: ``write`` (absent), ``keep``, ``upgrade`` or ``refuse``."""

    action: str
    reason: str
    current: ScriptStamp | None = None


def backfill_decision(existing: str | None) -> Backfill:
    """What a caller does with the project's ``scripts/ui-wiring.mjs``
    (``None``: the project has none) before it runs the check."""
    mine = f"{_version()} (protocol {PROTOCOL})"
    if existing is None:
        return Backfill("write", f"the project has no checker; writing stapel-tools {mine}")
    stamp = inspect_script(existing)
    theirs = f"{stamp.version} (protocol {stamp.protocol})"
    if not stamp.pristine:
        return Backfill("refuse", (
            f"{SCRIPT_PATH} is not a stapel-tools release (stamp {theirs}, text edited): "
            "an edited checker proves nothing; restore it from stapel-tools or delete it "
            "so the caller writes a fresh one"), stamp)
    if stamp.protocol > PROTOCOL:
        return Backfill("refuse", (
            f"the project's checker is stapel-tools {theirs}, newer than the caller's "
            f"{mine}: upgrade the caller's stapel-tools, its parser predates that output"), stamp)
    if stamp.protocol < PROTOCOL:
        return Backfill("upgrade", (
            f"the project's checker {theirs} is blind to what protocol {PROTOCOL} checks "
            f"(request base, unverifiable values) and would answer green there; "
            f"replacing it with stapel-tools {mine}"), stamp)
    return Backfill("keep", f"the project's checker {theirs} is pristine; kept as pinned", stamp)


def parse_result(output: str) -> dict | None:
    """The ``UI_WIRING_RESULT`` payload of a run, or ``None`` when the output
    carries none (the script never ran: make, node or the install failed).

    The payload: ``{"ok": bool, "checks": int, "failed": int, "unverifiable":
    int, "protocol": int, "criteria": [{"task", "id", "ok", "failed": [str],
    "unverifiable": [str]}]}`` (protocol 1 has no ``unverifiable``); a run that
    could not check anything has ``"error"`` and ``checks == 0``."""
    payload = None
    for line in (output or "").splitlines():
        if line.startswith(RESULT_TAG + " "):
            try:
                payload = json.loads(line[len(RESULT_TAG) + 1:])
            except ValueError:
                payload = None
    return payload if isinstance(payload, dict) else None


def manifest(task: str, criteria: dict[str, dict], *, api_base: str = "") -> dict:
    """A manifest document from ``{criterion id: {"pages": [...]}}``.
    *api_base* (``/<slug>/api``) names the canon for the ``base`` check when
    the make target passes none."""
    doc = {
        "version": 1,
        "task": task,
        "criteria": [{"id": cid, "pages": list((spec or {}).get("pages") or [])}
                     for cid, spec in criteria.items()],
    }
    if api_base:
        doc["api_base"] = api_base
    return doc


__all__ = [
    "ERROR_TAG", "MAKEFILE_BLOCK", "MAKE_TARGET", "MANIFEST_DIR", "PROTOCOL", "RESULT_TAG",
    "SCRIPT_PATH", "Backfill", "ScriptStamp", "backfill_decision", "inspect_script",
    "makefile_block", "manifest", "parse_result", "script_source",
]
