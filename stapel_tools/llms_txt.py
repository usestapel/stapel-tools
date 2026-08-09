"""
stapel-llms-txt — emit a module's ``docs/llms.txt``, the FIFTH per-module
contract artifact next to ``docs/{schema,flows,errors,capabilities}.json``
(badge-canon §3), under exactly the same pipeline discipline: emitted by
``make contract``, drift-gated by ``make contract-check`` /
``tests/test_contract.py``, committed to the repo, shipped in the wheel.

Why a generator and not a hand-written file
-------------------------------------------
A hand-written ``llms.txt`` rots on the first release that changes a symbol,
and it rots SILENTLY: nothing reads it except a model, and a model cannot tell
a stale surface from a current one. The observed cost of exactly this failure
is the studio index that listed 25 of 26 modules with an auth version eleven
releases behind. The frontend half of the fleet has been immune since
``scripts/gen-manifest.mjs`` shipped, because there the file is *generated from
the same artifacts as the code* and gated with ``git diff --exit-code``. This
module is that mechanism for the Python half — the same three properties, not
a re-invention:

1. **Deterministic.** Every list is sorted by an explicit key, nothing carries
   a timestamp, a hostname or an absolute path. Two runs over the same inputs
   are byte-identical, so a drift gate can compare bytes without false reds.
2. **Hard budget.** The rendered file must fit :data:`DEFAULT_TOKEN_BUDGET`
   tokens. Over budget is a *failure with a per-section cost breakdown*, never
   a quiet truncation — a truncated context file reads exactly like a complete
   one, which is how a truncation survives review twice.
3. **Loud on nothing to say.** A module with no ``docs/capabilities.json`` is
   an error, not an empty ``llms.txt``. An empty context file is worse than a
   missing one: it answers "does the fleet have a mechanism for X?" with a
   confident no.

What goes in, and in what order (badge-canon §3, "order = order in the file")
---------------------------------------------------------------------------
====================================  =======================================
Source                                Section
====================================  =======================================
``capabilities.json`` module/version  header
``capabilities.json`` provides        header paragraph
``capabilities.json`` axes            "Configuration axes"
``capabilities.json`` surface         "Usage surface"  ← the main section
``capabilities.json`` extension_points"Extension points"
``capabilities.json`` requires        "Fits with"
``docs/schema.json``                  "HTTP operations", by tag
``docs/errors.json``                  "Error codes", one line each
``docs/flows.json``                   "Documented flows", name + title
====================================  =======================================

``surface`` is the section this artifact exists for. ``axes`` answer "what can
I switch on" and ``schema`` answers "what can I call over HTTP", but the
question that cost six mechanisms their adoption is *"is there already
something for X, and what is it called?"* — and only ``surface`` answers it,
with the ``instead_of`` line naming the outside symbol a product would
otherwise reach for.

Usage (per-module shim is a one-liner in the module's Makefile)::

    contract:       python3 -m stapel_tools.llms_txt .
    contract-check: python3 -m stapel_tools.llms_txt . --check

Generating for a repo you must not write to (a foreign checkout under a drift
gate) is ``--out <tmpdir>``.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from stapel_tools.surface import KINDS

#: The frontend's ``LLMS_TOKEN_BUDGET`` (scripts/gen-manifest.mjs), adopted
#: verbatim so both halves of the fleet describe themselves at the same cost.
#: A slice a harness drops into a coder's context has to leave room for the
#: code; 4k is the observed ceiling at which it still does.
DEFAULT_TOKEN_BUDGET = 4000

#: Order in which sections are to be TRIMMED BY THE AUTHOR when a module does
#: not fit — reported in the failure message. The tool never trims by itself.
TRIM_ORDER = ("surface", "axes", "extension_points")

#: Contract inputs, all optional except ``capabilities.json``.
CAPABILITIES = "capabilities.json"
SCHEMA = "schema.json"
ERRORS = "errors.json"
FLOWS = "flows.json"

_HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")


class EmitError(Exception):
    """A loud, actionable emission failure (missing input, budget overrun)."""


def approx_tokens(text: str) -> int:
    """Token estimate, same 4-chars-per-token heuristic as ``gen-manifest.mjs``.

    Deliberately the crude one: the budget is a *guard rail against a file
    nobody can afford to read*, not an accounting of a particular tokenizer,
    and a shared heuristic keeps the two halves of the fleet comparable.
    """
    return math.ceil(len(text) / 4)


# ── inputs ───────────────────────────────────────────────────────────────────

def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:  # pragma: no cover - malformed input
        raise EmitError(f"{path}: not valid JSON — {exc}") from exc


def load_inputs(repo: Path) -> dict:
    """Load the contract documents of ``repo``.

    ``capabilities.json`` is REQUIRED — its absence is an :class:`EmitError`
    naming the module, never an empty render. The other three are optional:
    ``stapel-core`` has no OpenAPI surface at all, and a module without flows
    simply has no flows section.
    """
    docs = repo / "docs"
    capabilities_path = docs / CAPABILITIES
    if not capabilities_path.is_file():
        raise EmitError(
            f"{capabilities_path} does not exist — {repo.name} has no contract "
            "document, so there is nothing truthful to put in an llms.txt.\n"
            "An empty context file is worse than a missing one: it answers "
            "\"does the fleet have a mechanism for X?\" with a confident no.\n"
            "Emit the contract first (`make contract` in that module, i.e. "
            "stapel_tools.capabilities or stapel_tools.surface), or pass "
            "--skip-missing to make this module a deliberate no-op in a fleet loop."
        )
    data = {"capabilities": _read_json(capabilities_path)}
    for key, name in (("schema", SCHEMA), ("errors", ERRORS), ("flows", FLOWS)):
        path = docs / name
        data[key] = _read_json(path) if path.is_file() else None
    return data


# ── projections (deterministic; every list gets an explicit sort key) ─────────

def _kind_rank(kind: str) -> tuple[int, str]:
    """Sort surface entries by the closed kind vocabulary, then by name.

    Grouping by kind is what makes the section skimmable ("show me the
    permission classes"); an unknown kind sorts after the closed set rather
    than crashing, because a newer module may ship a kind this release of the
    tool has not learned yet.
    """
    return (KINDS.index(kind), kind) if kind in KINDS else (len(KINDS), kind)


def operations(schema) -> list[dict]:
    """``schema.json`` → a flat, sorted operation catalog.

    Only ``operationId``/``tag``/``method``/``path`` survive: request and
    response schema names are a click away in ``schema.json`` itself and would
    double this section's cost for a reader who, at this point, only needs to
    learn that the operation EXISTS and what to call it.
    """
    if not schema:
        return []
    out = []
    for path, item in (schema.get("paths") or {}).items():
        for method in _HTTP_METHODS:
            op = item.get(method)
            if not op:
                continue
            out.append(
                {
                    "id": op.get("operationId") or f"{method}_{path}",
                    "method": method.upper(),
                    "path": path,
                    "tag": (op.get("tags") or ["untagged"])[0],
                }
            )
    return sorted(out, key=lambda o: (o["tag"], o["id"]))


def common_prefix(paths: list[str]) -> str:
    """Longest shared path prefix ending in ``/`` — factored out of the listing.

    Every operation of a module lives under one mount (``/auth/api/v1/``), so
    the prefix is stated once and each line carries only what distinguishes it.
    Pure token economy, and it is also how the module's own client spells them.
    """
    if len(paths) < 2:
        return ""
    prefix = paths[0]
    for path in paths[1:]:
        while not path.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    cut = prefix.rfind("/")
    return prefix[: cut + 1] if cut > 0 else ""


def error_codes(errors) -> list[dict]:
    """``errors.json`` → one compact record per code, localized text dropped.

    The English and Russian strings live in ``errors.<lang>.md``; carrying them
    here would spend most of the budget on prose a model does not act on. What
    it acts on is the code, the status, the remediation and the interpolation
    slots.
    """
    if not errors:
        return []
    out = [
        {
            "code": e["code"],
            "status": e.get("status"),
            "remediation": e.get("remediation") or "",
            "params": list(e.get("params") or []),
        }
        for e in errors
    ]
    return sorted(out, key=lambda e: e["code"])


def flow_index(flows) -> list[dict]:
    """``flows.json`` → id + one-line title (the ``demos-lib.mjs`` treatment)."""
    if not flows:
        return []
    out = [{"id": f["id"], "title": (f.get("title") or "").strip()} for f in flows]
    return sorted(out, key=lambda f: f["id"])


# ── rendering ────────────────────────────────────────────────────────────────

def _paragraph(text: str) -> list[str]:
    return [" ".join((text or "").split())]


def render_header(cap: dict, counts: dict) -> list[str]:
    module = cap.get("module") or "(unnamed module)"
    version = cap.get("version") or "0.0.0"
    lines = [f"# {module} {version}", ""]
    lines += _paragraph(cap.get("provides") or "")
    lines.append("")
    facts = " · ".join(
        f"{label} {counts[key]}"
        for key, label in (
            ("axes", "axes"),
            ("surface", "surface"),
            ("extension_points", "extension points"),
            ("operations", "operations"),
            ("errors", "error codes"),
            ("flows", "flows"),
        )
        if counts.get(key)
    )
    lines.append(f"Contract: {facts}." if facts else "Contract: (no catalogued surface).")
    lines.append(
        "Generated from docs/capabilities.json by `stapel-llms-txt` — do not edit; "
        "drift-gated by `make contract-check`."
    )
    return lines


def render_axes(axes: list[dict]) -> list[str]:
    if not axes:
        return []
    lines = [
        "",
        "## Configuration axes — what a product switches on",
        "Settings keys; `default` is what you get by saying nothing. "
        "Turning an axis off unmounts the operations it gates.",
    ]
    for axis in sorted(axes, key=lambda a: a["key"]):
        curated = axis.get("curated") or {}
        label = (curated.get("business_label") or "").strip()
        summary = " ".join((curated.get("summary") or "").split())
        head = f"- {axis['key']} [{axis.get('kind', 'enum')}, default {json.dumps(axis.get('default'))}]"
        if label:
            head += f" — {label}"
        lines.append(head)
        gates = (axis.get("gates") or {}).get("operations") or []
        co_gates = (axis.get("gates") or {}).get("co_gates") or []
        if summary:
            lines.append(f"  {summary}")
        if gates:
            note = f"  gates {len(gates)} operation(s)"
            if co_gates:
                # An OR-composed gate: the operations survive while ANY sibling
                # flag is on. Without this line an index reads "one flag off =
                # endpoints gone", which is false and expensive to discover.
                note += f"; kept mounted by {', '.join(sorted(co_gates))}"
            lines.append(note)
    return lines


def render_surface(surface: list[dict]) -> list[str]:
    if not surface:
        return []
    lines = [
        "",
        "## Usage surface — call these before writing your own",
        "This is the answer to \"does Stapel already have something for X?\". "
        "`instead of` names the outside symbol this one displaces.",
    ]
    last_kind = None
    for entry in sorted(surface, key=lambda e: (_kind_rank(e.get("kind", "")), e["name"])):
        kind = entry.get("kind", "")
        if kind != last_kind:
            lines.append(f"### {kind}")
            last_kind = kind
        lines.append(f"- {entry['name']} — {entry.get('path', '')}")
        instead_of = entry.get("instead_of") or []
        if instead_of:
            lines.append(f"  instead of: {', '.join(sorted(instead_of))}")
        consumer = entry.get("consumer")
        if consumer:
            lines.append(f"  consumer: {consumer}")
        intent = " ".join((entry.get("intent") or "").split())
        if intent:
            lines.append(f"  {intent}")
    return lines


def render_extension_points(points: list[dict]) -> list[str]:
    if not points:
        return []
    lines = [
        "",
        "## Extension points — what a product replaces, fork-free",
    ]
    for point in sorted(points, key=lambda p: p["name"]):
        summary = " ".join((point.get("summary") or "").split())
        lines.append(f"- {point['name']} [{point.get('kind', '')}]")
        if summary:
            lines.append(f"  {summary}")
    return lines


def render_requires(requires: list[dict]) -> list[str]:
    if not requires:
        return []
    lines = ["", "## Fits with — fleet dependencies"]
    for req in sorted(requires, key=lambda r: r["module"]):
        kind = "optional" if req.get("optional") else "required"
        reason = " ".join((req.get("reason") or "").split())
        lines.append(f"- {req['module']} ({kind})" + (f" — {reason}" if reason else ""))
    return lines


def render_operations(ops: list[dict]) -> list[str]:
    if not ops:
        return []
    prefix = common_prefix([o["path"] for o in ops])
    lines = [
        "",
        f"## HTTP operations ({len(ops)}) — call by operationId, never by a typed path",
    ]
    if prefix:
        lines.append(f"Paths are relative to `{prefix}`.")
    last_tag = None
    for op in ops:
        if op["tag"] != last_tag:
            lines.append(f"### {op['tag']}")
            last_tag = op["tag"]
        shown = op["path"][len(prefix) - 1:] if prefix else op["path"]
        lines.append(f"- {op['method']} {shown} — {op['id']}")
    return lines


def render_errors(errors: list[dict]) -> list[str]:
    if not errors:
        return []
    lines = [
        "",
        f"## Error codes ({len(errors)}) — the StapelError envelope",
        "Render `t(code, params)`; branch UX on the remediation. "
        "Localized text lives in docs/errors.<lang>.md, not here.",
    ]
    for err in errors:
        slots = f" {{{','.join(err['params'])}}}" if err["params"] else ""
        lines.append(f"- {err['code']} [{err['status']}] {err['remediation']}{slots}")
    return lines


def render_flows(flows: list[dict]) -> list[str]:
    if not flows:
        return []
    lines = [
        "",
        f"## Documented flows ({len(flows)}) — full steps in docs/flows.json",
    ]
    for flow in flows:
        lines.append(f"- {flow['id']}" + (f" — {flow['title']}" if flow["title"] else ""))
    return lines


def build_sections(inputs: dict) -> dict[str, list[str]]:
    """Render every section, keyed by name — so the budget report can price them.

    Section order here IS the file order (badge-canon §3): what it is, what you
    switch on, what you call, what you replace, what it needs, then the HTTP /
    error / flow catalogs.
    """
    cap = inputs["capabilities"]
    ops = operations(inputs.get("schema"))
    errs = error_codes(inputs.get("errors"))
    flows = flow_index(inputs.get("flows"))
    counts = {
        "axes": len(cap.get("axes") or []),
        "surface": len(cap.get("surface") or []),
        "extension_points": len(cap.get("extension_points") or []),
        "operations": len(ops),
        "errors": len(errs),
        "flows": len(flows),
    }
    return {
        "header": render_header(cap, counts),
        "axes": render_axes(cap.get("axes") or []),
        "surface": render_surface(cap.get("surface") or []),
        "extension_points": render_extension_points(cap.get("extension_points") or []),
        "requires": render_requires(cap.get("requires") or []),
        "operations": render_operations(ops),
        "errors": render_errors(errs),
        "flows": render_flows(flows),
    }


def render(inputs: dict, budget: int = DEFAULT_TOKEN_BUDGET) -> str:
    """Render ``llms.txt`` or raise :class:`EmitError` if it does not fit.

    Over budget FAILS. It does not drop a section, does not elide intents and
    does not cut the tail: a shortened context file is indistinguishable from a
    complete one at the point of use, so the only safe place to discover the
    overrun is here, with the per-section costs in hand.
    """
    sections = build_sections(inputs)
    text = "\n".join(line for name in sections for line in sections[name]).rstrip("\n") + "\n"
    total = approx_tokens(text)
    if total > budget:
        costs = {name: approx_tokens("\n".join(lines)) for name, lines in sections.items()}
        breakdown = " · ".join(
            f"{name} {cost}" for name, cost in sorted(costs.items(), key=lambda kv: -kv[1]) if cost
        )
        module = inputs["capabilities"].get("module", "(module)")
        raise EmitError(
            f"{module}: llms.txt is ~{total} tokens, over the {budget}-token budget "
            f"by {total - budget}.\n"
            f"  per-section cost: {breakdown}\n"
            f"  Nothing was written. Trim the source in this order: "
            f"{' -> '.join(TRIM_ORDER)} (shorten `intent`/`summary` lines in "
            f"docs/capabilities.meta.json), or raise the ceiling DELIBERATELY with "
            f"--budget N in the module's Makefile.\n"
            f"  It is not truncated on purpose: a cut context file reads exactly "
            f"like a complete one."
        )
    return text


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-llms-txt",
        description="Emit docs/llms.txt — the module's own surface slice for an "
        "agent's context — from docs/capabilities.json (+ schema/errors/flows "
        "when present). Deterministic, hard token budget, drift-gated.",
    )
    parser.add_argument("repo", nargs="?", default=".", help="Module repo root.")
    parser.add_argument(
        "--out",
        help="Output directory (default: <repo>/docs). Use a temp dir to render "
        "a checkout you must not write to.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Drift gate: render in memory and compare against the committed "
        "docs/llms.txt byte for byte; nonzero exit and no write on any drift.",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=DEFAULT_TOKEN_BUDGET,
        help=f"Token ceiling (default {DEFAULT_TOKEN_BUDGET}, the frontend's "
        "LLMS_TOKEN_BUDGET). Exceeding it FAILS; the file is never truncated.",
    )
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="A repo with no docs/capabilities.json is a loud no-op (exit 0) "
        "instead of an error — for fleet loops over mixed repos. It still "
        "never writes an empty llms.txt.",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write the render to stdout instead of a file (no drift gate).",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    try:
        inputs = load_inputs(repo)
    except EmitError as exc:
        if args.skip_missing and not (repo / "docs" / CAPABILITIES).is_file():
            print(
                f"stapel-llms-txt: skipping {repo.name}: no docs/{CAPABILITIES} "
                "(no contract document — nothing to describe)",
                file=sys.stderr,
            )
            return 0
        print(f"stapel-llms-txt: {exc}", file=sys.stderr)
        return 1

    try:
        rendered = render(inputs, budget=args.budget)
    except EmitError as exc:
        print(f"stapel-llms-txt: {exc}", file=sys.stderr)
        return 1

    if args.stdout:
        sys.stdout.write(rendered)
        return 0

    committed = repo / "docs" / "llms.txt"
    if args.check:
        if not committed.is_file():
            print(
                f"stapel-llms-txt: --check: {committed} does not exist — run the "
                "module's `make contract` and commit it",
                file=sys.stderr,
            )
            return 1
        if committed.read_text() != rendered:
            print(
                f"DRIFT: {committed} is stale — run the module's `make contract` "
                "and commit it",
                file=sys.stderr,
            )
            return 1
        print(f"stapel-llms-txt: --check: {committed} is up to date", file=sys.stderr)
        return 0

    out_dir = Path(args.out) if args.out else repo / "docs"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "llms.txt").write_text(rendered)
    module = inputs["capabilities"].get("module", repo.name)
    print(
        f"{module} llms.txt: ~{approx_tokens(rendered)}/{args.budget} tokens "
        f"→ {out_dir}/llms.txt",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
