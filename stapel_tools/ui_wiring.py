"""Static UI wiring check: the generated ``scripts/ui-wiring.mjs`` and its make target.

A UI manifest names, per acceptance criterion, the SPA pages a feature must
render: route, h1, ``data-testid`` values and actions (``{testid, handler,
request: {method, path}}``). ``make frontend-wiring MANIFEST=<file|dir>`` runs
the script over ``frontend/src`` with the TypeScript compiler API installed in
``frontend/node_modules``: red when a testid is not rendered, an action's
element carries no handler, or its module (or one it imports, two hops) holds
no literal of the request path with the method. One run takes about a second;
nothing is built or served.

The script lives at the repo root (``scripts/``), not under ``frontend/``: the
frontend's own lint, build and test sets stay as generated.

What it cannot see is printed in the script header: paths assembled from
concatenated literals, handlers handed down through props, requests a
``@stapel/*`` pair issues from ``node_modules``.
"""
from __future__ import annotations

import json
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

#: Appended to the generated root Makefile. ``frontend-install`` is not a
#: prerequisite here (the scaffold leaves the frontend install to the caller);
#: a caller that owns that target adds ``frontend-wiring: frontend-install``.
MAKEFILE_BLOCK = f"""
# --- Static UI wiring (stapel-tools ui_wiring; needs frontend/node_modules) ---
# MANIFEST is one JSON file or a directory of them: pages, testids and actions
# per acceptance criterion. Red when a testid is not rendered or an action's
# element has no handler or issues no request. Last line: {RESULT_TAG} <json>.
MANIFEST ?= {MANIFEST_DIR}
.PHONY: {MAKE_TARGET}
{MAKE_TARGET}:
\tnode {SCRIPT_PATH} --frontend frontend $(MANIFEST)
"""


def script_source() -> str:
    """The script the scaffold writes to :data:`SCRIPT_PATH`."""
    return resources.files("stapel_tools").joinpath("data/ui-wiring.mjs").read_text(
        encoding="utf-8")


def parse_result(output: str) -> dict | None:
    """The ``UI_WIRING_RESULT`` payload of a run, or ``None`` when the output
    carries none (the script never ran: make, node or the install failed).

    The payload: ``{"ok": bool, "checks": int, "failed": int, "criteria":
    [{"task", "id", "ok", "failed": [str]}]}``; a run that could not check
    anything has ``"error"`` and ``checks == 0``."""
    payload = None
    for line in (output or "").splitlines():
        if line.startswith(RESULT_TAG + " "):
            try:
                payload = json.loads(line[len(RESULT_TAG) + 1:])
            except ValueError:
                payload = None
    return payload if isinstance(payload, dict) else None


def manifest(task: str, criteria: dict[str, dict]) -> dict:
    """A manifest document from ``{criterion id: {"pages": [...]}}``."""
    return {
        "version": 1,
        "task": task,
        "criteria": [{"id": cid, "pages": list((spec or {}).get("pages") or [])}
                     for cid, spec in criteria.items()],
    }


__all__ = [
    "ERROR_TAG", "MAKEFILE_BLOCK", "MAKE_TARGET", "MANIFEST_DIR", "RESULT_TAG",
    "SCRIPT_PATH", "manifest", "parse_result", "script_source",
]
