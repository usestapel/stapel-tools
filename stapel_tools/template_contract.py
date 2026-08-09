"""
stapel-template-contract — emit a module's ``docs/templates.json``, the SIXTH
per-module contract artifact next to ``docs/{schema,flows,errors,capabilities}
.json`` and ``docs/llms.txt``, under exactly the same pipeline discipline:
emitted by ``make contract``, drift-gated by ``make contract-check`` /
``tests/test_contract.py``, committed to the repo, shipped in the wheel.

Why this artifact exists
------------------------
Django templates shipped inside a library are an **extension surface**: a host
project overrides one by dropping a file of the same name into a template dir
that resolves first. Nothing else about that surface was ever declared. The
other five artifacts describe HTTP, errors, flows, config axes and the usage
surface; none of them names a single template path or a single context
variable. So a host that overrides a template obtains its contract by reading
the library's ``services.py``, and the library can break that host twice over
without a single failing test on either side:

1. **A renamed context variable.** ``{{ code }}`` becomes ``{{ otp }}`` in a
   patch release. Django renders an unknown variable as the empty string
   (``string_if_invalid = ''``), so the mail ships with a blank code: 200 OK,
   no exception, nobody can log in.
2. **A renamed template file.** ``otp_code.html`` becomes ``otp.html``. The
   host's override stops shadowing anything and the LIBRARY's letter goes out
   under the host's brand — and the host's own guard test ("this template
   resolves from our folder, not from site-packages") stays GREEN, because it
   asserts the name the host itself chose and that file still exists. The
   guard matches by name while the override is dead.

``templates.json`` is what a host's gate reads instead: every routing key, the
template path it resolves to, and the context variables the library actually
passes — with each variable's provenance, so the host can tell "the library
guarantees this" from "you are expected to supply this".

Derivation, and its edges
-------------------------
Nothing here is retyped by hand. Two static sources:

* **The render call site** (:func:`scan_call_site`) — Python AST over the
  module that calls ``render_to_string``. Every literal-key write into the
  context dict (``ctx["x"] = ...``, ``ctx.setdefault("x", ...)``) is a
  declared variable; a write guarded by an ``if`` is declared *conditional*,
  and the guard expression is recorded. A write under a **non-constant** key
  (``ctx[whatever] = ...``) cannot be resolved statically — the emitter does
  not guess, it reports ``dynamic_keys`` on the call site and the caller of
  this module must declare those names from wherever they really come from
  (for stapel-notifications: the translation-key registry).
* **The templates themselves** (:func:`scan_template`, :func:`resolve_chain`)
  — Django's own lexer and filter-expression parser, not a regex, following
  ``{% extends %}`` / ``{% include %}``. This yields what each letter reads,
  which is how "the host must pass this" is derived rather than asserted.

What it CANNOT derive, and says so in ``limits``:

* variables a caller passes that neither the shipped template renders nor a
  translation string interpolates are invisible here — nothing in the library
  ever mentions them;
* ``{% extends %}`` block overrides are not modelled: a chain's variables are
  the UNION over the chain, so a parent's default block content is counted
  even when the child replaces it. The union over-declares, which is the safe
  direction for a subset gate;
* ``{% include %} ... only`` isolates context; the union ignores that, same
  direction;
* a custom template tag is recorded in ``unknown_tags`` rather than silently
  parsed, and an ``as <name>`` result is treated as a local, not a context
  variable.

Loud, never partial: a missing template file, a render call site that no
longer matches the declared wiring, or a template that consumes a variable no
provenance declares — each aborts emission with a message naming the thing.
The alternative is an artifact that is wrong in exactly the way the artifact
exists to prevent.

Usage — the mechanism is shared, the wiring is a per-module shim, same split
as ``stapel_tools.capabilities``::

    # <module>/_template_contract.py
    from stapel_tools.template_contract import run_template_contract_cli
    def main(argv=None):
        return run_template_contract_cli(argv, repo=..., routes=..., ...)
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: Artifact filename, and the schema version of its shape.
ARTIFACT = "templates.json"
SCHEMA_VERSION = 1

#: Tags the scanner models exactly. Anything else is recorded in
#: ``unknown_tags`` (and, under ``strict``, aborts emission) rather than being
#: parsed on a guess — see the module docstring.
_STRUCTURAL_TAGS = {
    "extends", "include", "block", "endblock", "comment", "endcomment",
    "if", "elif", "else", "endif", "for", "endfor", "empty",
    "with", "endwith", "load", "spaceless", "endspaceless",
    "autoescape", "endautoescape", "csrf_token", "now",
    "trans", "translate", "blocktrans", "blocktranslate",
    "endblocktrans", "endblocktranslate", "plural", "filter", "endfilter",
}

#: Operators of the ``{% if %}`` mini-language; everything else in a condition
#: is a filter expression.
_IF_OPERATORS = {
    "and", "or", "not", "in", "is", "==", "!=", "<", ">", "<=", ">=",
}

#: Filters that make a missing variable harmless — a variable whose every
#: occurrence carries one is reported ``optional``.
_DEFAULTING_FILTERS = {"default", "default_if_none"}

REQUIRED = "required"
OPTIONAL = "optional"

ALWAYS = "always"
CONDITIONAL = "conditional"


class EmitError(Exception):
    """A loud, actionable emission failure. Never write a partial artifact."""


# ── template scanning ────────────────────────────────────────────────────────

@dataclass
class TemplateScan:
    """What one template file declares and reads, on its own."""

    name: str
    extends: str | None = None
    includes: tuple[str, ...] = ()
    #: includes that sit inside an ``{% if %}`` — everything the included
    #: partial reads is reachable only when that condition holds, so the chain
    #: union counts those reads as ``optional``. Without this an auth letter,
    #: which can never have an ``unsubscribe_url`` and therefore never pulls
    #: in the unsubscribe footer, would appear to require the footer's
    #: variables.
    guarded_includes: tuple[str, ...] = ()
    blocks: tuple[str, ...] = ()
    #: variable root name -> REQUIRED | OPTIONAL
    variables: dict[str, str] = field(default_factory=dict)
    #: custom/unmodelled tags met while scanning, sorted, deduplicated
    unknown_tags: tuple[str, ...] = ()


def _parser():
    """A Django template ``Parser`` carrying the stock filter library.

    Built from a bare :class:`django.template.Engine` rather than the
    project's configured one: the scanner must work in a plain process (an
    emitter, a lint run) with no ``DJANGO_SETTINGS_MODULE``, and the stock
    builtins are what every DjangoTemplates backend has anyway.
    """
    from django.template import Engine
    from django.template.base import Parser

    engine = Engine(dirs=[], app_dirs=False, libraries={}, builtins=None)
    return Parser([], libraries=engine.template_libraries, builtins=engine.template_builtins)


def _filter_names(fexpr) -> set[str]:
    names = set()
    for func, _args in fexpr.filters:
        inner = getattr(func, "_decorated_function", func)
        names.add(getattr(inner, "__name__", ""))
    return names


def _roots(fexpr) -> list[str]:
    """Context-variable roots read by one filter expression.

    Both the head (``{{ a.b|f }}`` → ``a``) and any variable filter ARGUMENT
    (``{{ x|default:fallback }}`` → ``fallback``) — the argument is a real
    context read and was worth getting right the first time.
    """
    from django.template.base import Variable

    out = []
    head = fexpr.var
    if isinstance(head, Variable) and head.literal is None:
        out.append(head.var.split(".")[0])
    for _func, args in fexpr.filters:
        for is_var, value in args:
            if is_var and isinstance(value, Variable) and value.literal is None:
                out.append(value.var.split(".")[0])
    return out


class _Scanner:
    def __init__(self, name: str):
        self.name = name
        self.parser = _parser()
        self.scan = TemplateScan(name=name)
        self.includes: list[str] = []
        self.guarded_includes: set[str] = set()
        self.blocks: list[str] = []
        self.unknown: set[str] = set()
        self.if_depth = 0
        #: stack of locally-bound names ({% for %}, {% with %}, ``as`` results)
        self.scopes: list[set[str]] = [set()]

    # -- recording -------------------------------------------------------
    def _bound(self, name: str) -> bool:
        return any(name in scope for scope in self.scopes)

    def record(self, expr_src: str, *, optional: bool = False) -> None:
        try:
            fexpr = self.parser.compile_filter(expr_src)
        except Exception:
            self.unknown.add(f"?{expr_src}")
            return
        is_optional = optional or bool(_filter_names(fexpr) & _DEFAULTING_FILTERS)
        for root in _roots(fexpr):
            if self._bound(root) or root == "forloop":
                continue
            status = OPTIONAL if is_optional else REQUIRED
            # required wins: one unguarded read makes the variable required
            if self.scan.variables.get(root) != REQUIRED:
                self.scan.variables[root] = status

    def record_condition(self, bits: list[str]) -> None:
        """An ``{% if %}`` / ``{% elif %}`` condition.

        Reads here are ``optional`` by construction: the template is testing
        for the variable, which is exactly how a template says "this one may
        be absent". Django never routes a failed lookup in a condition
        through ``string_if_invalid`` either.
        """
        for bit in bits:
            if bit in _IF_OPERATORS:
                continue
            self.record(bit, optional=True)

    # -- tags ------------------------------------------------------------
    def tag(self, contents: str) -> None:
        bits = contents.split()
        if not bits:
            return
        tag, args = bits[0], bits[1:]

        if tag == "extends":
            if args and args[0][:1] in "\"'":
                self.scan.extends = args[0].strip("\"'")
            else:
                self.unknown.add("extends(dynamic)")
        elif tag == "include":
            if args and args[0][:1] in "\"'":
                included = args[0].strip("\"'")
                self.includes.append(included)
                if self.if_depth:
                    self.guarded_includes.add(included)
            else:
                self.unknown.add("include(dynamic)")
            self._kwargs(args[1:])
        elif tag == "block":
            if args:
                self.blocks.append(args[0])
        elif tag == "if":
            self.if_depth += 1
            self.record_condition(args)
        elif tag == "elif":
            self.record_condition(args)
        elif tag == "endif":
            self.if_depth = max(0, self.if_depth - 1)
        elif tag == "for":
            # {% for a, b in seq reversed %}
            if "in" in args:
                idx = args.index("in")
                names = " ".join(args[:idx]).replace(",", " ").split()
                rest = [a for a in args[idx + 1:] if a != "reversed"]
                if rest:
                    self.record(rest[0])
                self.scopes.append(set(names) | {"forloop"})
            else:
                self.scopes.append(set())
        elif tag == "endfor":
            self._pop()
        elif tag in {"with", "blocktrans", "blocktranslate"}:
            names = self._kwargs(args)
            self.scopes.append(names)
        elif tag in {"endwith", "endblocktrans", "endblocktranslate"}:
            self._pop()
        elif tag in {"trans", "translate"}:
            if args and args[0][:1] not in "\"'":
                self.record(args[0])
        elif tag in _STRUCTURAL_TAGS:
            pass
        else:
            self.unknown.add(tag)
            # An assignment tag ({% mytag ... as name %}) binds a local; the
            # name is NOT a context variable and must not be reported as one.
            if len(args) >= 2 and args[-2] == "as":
                self.scopes[-1].add(args[-1])

    def _kwargs(self, args: list[str]) -> set[str]:
        """``key=expr`` pairs (and the legacy ``expr as name``); returns the
        local names bound, having recorded the right-hand sides."""
        names: set[str] = set()
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "only":
                i += 1
                continue
            if arg == "as" and i + 1 < len(args):
                names.add(args[i + 1])
                i += 2
                continue
            if "=" in arg:
                key, _, value = arg.partition("=")
                names.add(key)
                self.record(value)
            else:
                self.record(arg)
            i += 1
        return names

    def _pop(self) -> None:
        if len(self.scopes) > 1:
            self.scopes.pop()

    def finish(self) -> TemplateScan:
        self.scan.includes = tuple(sorted(set(self.includes)))
        self.scan.guarded_includes = tuple(sorted(self.guarded_includes))
        self.scan.blocks = tuple(sorted(set(self.blocks)))
        self.scan.unknown_tags = tuple(sorted(self.unknown))
        return self.scan


def scan_source(source: str, *, name: str, strict: bool = False) -> TemplateScan:
    """Scan one template's SOURCE with Django's own lexer.

    ``strict`` turns an unmodelled construct into an :class:`EmitError` — what
    a library's own emitter wants, so that a template gaining a custom tag is
    a build failure and not a quietly incomplete contract. A consumer scanning
    somebody else's templates (a host project's overrides) leaves it off and
    reads :attr:`TemplateScan.unknown_tags` instead.
    """
    from django.template.base import Lexer, TokenType

    scanner = _Scanner(name)
    in_comment = False
    for token in Lexer(source).tokenize():
        if token.token_type == TokenType.BLOCK:
            head = token.contents.split()[:1]
            if head == ["comment"]:
                in_comment = True
                continue
            if head == ["endcomment"]:
                in_comment = False
                continue
            if in_comment:
                continue
            scanner.tag(token.contents)
        elif token.token_type == TokenType.VAR and not in_comment:
            scanner.record(token.contents)

    scan = scanner.finish()
    if strict and scan.unknown_tags:
        raise EmitError(
            f"{name}: unmodelled template construct(s) {list(scan.unknown_tags)}. "
            "The scanner refuses to guess what they read from the context — "
            "teach stapel_tools.template_contract about them, or the emitted "
            "contract would silently under-declare this template."
        )
    return scan


def scan_template(path: Path, *, name: str | None = None, strict: bool = False) -> TemplateScan:
    """Scan a template FILE. ``name`` defaults to the file's own name."""
    try:
        source = path.read_text()
    except OSError as exc:
        raise EmitError(f"cannot read template {path}: {exc}") from exc
    return scan_source(source, name=name or path.name, strict=strict)


def find_template(name: str, dirs: list[Path]) -> Path | None:
    """First match for ``name`` across ``dirs``, in order — the filesystem
    loader's own precedence rule, which is what makes an override work."""
    for directory in dirs:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def resolve_chain(
    name: str, dirs: list[Path], *, strict: bool = False
) -> tuple[list[str], dict[str, str], dict[str, TemplateScan]]:
    """Follow ``{% extends %}`` / ``{% include %}`` from ``name``.

    Returns ``(chain, variables, scans)`` — the chain in discovery order
    starting at ``name``, the UNION of the variables read across it (required
    beats optional), and every scan by template name.

    The union deliberately ignores block overriding and ``include ... only``:
    it over-declares rather than under-declares, and over-declaring is the
    safe side of a subset gate.
    """
    chain: list[str] = []
    scans: dict[str, TemplateScan] = {}
    variables: dict[str, str] = {}
    # (template name, reachable unconditionally?)
    queue: list[tuple[str, bool]] = [(name, True)]
    while queue:
        current, certain = queue.pop(0)
        if current in scans:
            continue
        path = find_template(current, dirs)
        if path is None:
            raise EmitError(
                f"template {current!r} is referenced but not found in "
                f"{[str(d) for d in dirs]} — a contract cannot declare a "
                "template that is not shipped"
            )
        scan = scan_template(path, name=current, strict=strict)
        scans[current] = scan
        chain.append(current)
        for var, status in scan.variables.items():
            effective = status if certain else OPTIONAL
            if variables.get(var) != REQUIRED:
                variables[var] = effective
        if scan.extends:
            queue.append((scan.extends, certain))
        for included in scan.includes:
            queue.append((included, certain and included not in scan.guarded_includes))
    return chain, variables, scans


# ── render call site (Python AST) ────────────────────────────────────────────

@dataclass
class CallSite:
    """What a render call site puts into its context dict, statically."""

    file: str
    context_var: str
    #: variable name -> {"presence": always|conditional, "when": <guard src>}
    variables: dict[str, dict] = field(default_factory=dict)
    #: True when the context dict is also written under a non-literal key —
    #: names that cannot be resolved here and must be declared from elsewhere.
    dynamic_keys: bool = False
    #: every ``render_to_string`` found: template expression + context expression
    renders: list[dict] = field(default_factory=list)


def _guaranteed(body: list[ast.stmt], var: str) -> set[str]:
    """Names written into ``var`` on EVERY path through ``body``.

    ``if`` contributes only what both branches write, which is why
    ``company_host`` (set in both arms of an if/else) is honestly ``always``
    and ``unsubscribe_url`` (one arm, no else) is honestly ``conditional``.
    Loops and try bodies guarantee nothing.
    """
    out: set[str] = set()
    for stmt in body:
        if isinstance(stmt, ast.If):
            if stmt.orelse:
                out |= _guaranteed(stmt.body, var) & _guaranteed(stmt.orelse, var)
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While, ast.Try)):
            continue
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out |= _guaranteed(stmt.body, var)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            out |= _guaranteed(stmt.body, var)
        else:
            for name, _dynamic in _writes(stmt, var):
                if name is not None:
                    out.add(name)
    return out


def _writes(node: ast.AST, var: str):
    """``(literal_key | None, is_dynamic)`` for every write into ``var``."""
    for child in ast.walk(node):
        if isinstance(child, ast.Assign):
            for target in child.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == var
                ):
                    key = target.slice
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        yield key.value, False
                    else:
                        yield None, True
        elif isinstance(child, ast.Call):
            func = child.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "setdefault"
                and isinstance(func.value, ast.Name)
                and func.value.id == var
                and child.args
            ):
                key = child.args[0]
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    yield key.value, False
                else:
                    yield None, True


def scan_call_site(
    path: Path,
    *,
    context_var: str,
    render_func: str = "render_to_string",
    require_render: bool = True,
) -> CallSite:
    """Read the context contract out of the module that renders.

    Every literal-key write into ``context_var`` becomes a declared variable.
    A write reachable only under an ``if`` is ``conditional`` and carries the
    guard's source text, because "you get ``unsubscribe_url`` only for a
    non-auth group with a known user" is precisely the sort of thing a host
    otherwise learns from an empty space in a delivered email.
    """
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise EmitError(f"cannot parse render call site {path}: {exc}") from exc

    site = CallSite(file=path.name, context_var=context_var)
    always = _guaranteed(tree.body, context_var)

    guards: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            for name, _dyn in _writes(node, context_var):
                if name is not None and name not in always and name not in guards:
                    guards[name] = ast.unparse(node.test)

    for name, dynamic in _writes(tree, context_var):
        if dynamic:
            site.dynamic_keys = True
            continue
        entry = {"presence": ALWAYS if name in always else CONDITIONAL}
        if entry["presence"] == CONDITIONAL and name in guards:
            entry["when"] = guards[name]
        site.variables[name] = entry

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == render_func:
            if len(node.args) < 2:
                continue
            template_expr = ast.unparse(node.args[0])
            context_expr = ast.unparse(node.args[1])
            extra: list[str] = []
            if isinstance(node.args[1], ast.Dict):
                for key in node.args[1].keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        extra.append(key.value)
            site.renders.append(
                {
                    "template": template_expr,
                    "context": context_expr,
                    "extra_keys": sorted(extra),
                    "line": node.lineno,
                }
            )

    if require_render and not any(
        r["context"] == context_var or f"**{context_var}" in r["context"]
        for r in site.renders
    ):
        raise EmitError(
            f"{path.name}: no {render_func}(...) call renders with {context_var!r}. "
            "The declared wiring no longer matches the code, so every context "
            "variable this emitter derives would be derived from the wrong "
            "dict. Fix the shim's `context_var`/`render_func`, or the call site."
        )
    return site


# ── document ─────────────────────────────────────────────────────────────────

@dataclass
class Route:
    """One routing key of the module: a key, the template it resolves to, and
    the context the library passes when rendering it, grouped by provenance.

    ``context`` is ``{provenance: [names]}``. The provenances are the module's
    own vocabulary (stapel-notifications uses ``translation`` / ``branding`` /
    ``caller``); the only rule the mechanism enforces is that everything the
    template reads is declared by SOME provenance.
    """

    key: str
    template: str
    context: dict[str, list[str]]
    meta: dict = field(default_factory=dict)
    note: str | None = None


def _stable_json(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, separators=(",", ": ")) + "\n"


def build_document(
    *,
    module: str,
    version: str,
    routing_key: str,
    template_root: str,
    template_dirs: list[Path],
    routes: list[Route],
    call_sites: list[CallSite],
    limits: list[str],
    strict: bool = True,
) -> dict:
    """Assemble the artifact, verifying it as it goes.

    Verification, all of it loud:

    * every route's template must exist under ``template_dirs`` (a route
      pointing at a template nobody ships is the #2 failure mode itself);
    * every variable the shipped template chain READS must be declared by some
      provenance — an undeclared read means the library renders a variable it
      never documents, and a host copying the template inherits a hole;
    * a required (non-defaulted, non-guarded) read that no provenance declares
      is fatal even when the emitter could have shrugged it off.
    """
    by_template: dict[str, dict] = {}
    route_docs: list[dict] = []

    for route in sorted(routes, key=lambda r: r.key):
        chain, consumed, _scans = resolve_chain(route.template, template_dirs, strict=strict)
        declared: set[str] = set()
        for names in route.context.values():
            declared |= set(names)
        undeclared = sorted(
            name for name, status in consumed.items()
            if name not in declared and status == REQUIRED
        )
        if undeclared:
            raise EmitError(
                f"{module}: route {route.key!r} renders {route.template} which "
                f"reads {undeclared} — no provenance declares them. Either the "
                "library passes them and the shim must say from where, or the "
                "template reads a variable that is never set and the letter has "
                "been shipping a blank space."
            )
        doc = {
            "key": route.key,
            "template": route.template,
            "chain": chain,
            "context": {k: sorted(v) for k, v in sorted(route.context.items()) if v},
            "consumed": dict(sorted(consumed.items())),
        }
        if route.meta:
            doc.update({k: route.meta[k] for k in sorted(route.meta)})
        if route.note:
            doc["note"] = route.note
        route_docs.append(doc)

        for name in chain:
            entry = by_template.setdefault(
                name,
                {
                    "path": name,
                    "role": "letter" if name == route.template else "partial",
                    "used_by": set(),
                    "declared": set(),
                },
            )
            if name == route.template:
                entry["role"] = "letter"
            entry["used_by"].add(route.key)
            entry["declared"] |= declared

    template_docs = []
    for name in sorted(by_template):
        entry = by_template[name]
        path = find_template(name, template_dirs)
        scan = scan_template(path, name=name, strict=strict)
        template_docs.append(
            {
                "path": name,
                "role": entry["role"],
                "used_by": sorted(entry["used_by"]),
                "extends": scan.extends,
                "includes": list(scan.includes),
                "blocks": list(scan.blocks),
                "consumed": dict(sorted(scan.variables.items())),
                "declared": sorted(entry["declared"]),
                "unknown_tags": list(scan.unknown_tags),
            }
        )

    return {
        "artifact": "templates",
        "schema_version": SCHEMA_VERSION,
        "module": module,
        "module_version": version,
        "routing_key": routing_key,
        "template_root": template_root,
        "call_sites": [
            {
                "file": site.file,
                "context_var": site.context_var,
                "dynamic_keys": site.dynamic_keys,
                "variables": {k: site.variables[k] for k in sorted(site.variables)},
                "renders": sorted(site.renders, key=lambda r: (r["template"], r["line"])),
            }
            for site in sorted(call_sites, key=lambda s: s.file)
        ],
        "routes": route_docs,
        "templates": template_docs,
        "limits": list(limits),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def run_template_contract_cli(argv, *, repo: Path, build) -> int:
    """The shared ``--out`` / ``--check`` CLI a module shim exposes as ``main``.

    ``build`` is a zero-argument callable returning the document — the shim
    boots whatever Django harness it needs and applies its own provenance
    rules before calling :func:`build_document`.
    """
    parser = argparse.ArgumentParser(
        prog="stapel-template-contract",
        description="Emit docs/templates.json (sixth contract artifact): the "
        "routing key -> template path -> context variables the library passes.",
    )
    parser.add_argument("--out", default="docs", help="Output directory (default: docs).")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Drift gate: render in memory and compare byte for byte against "
        "the committed docs/templates.json; nonzero exit and no write on drift.",
    )
    parser.add_argument("--stdout", action="store_true", help="Write to stdout, no file.")
    args = parser.parse_args(argv)

    try:
        doc = build()
    except EmitError as exc:
        print(f"stapel-template-contract: {exc}", file=sys.stderr)
        return 1

    rendered = _stable_json(doc)

    if args.stdout:
        sys.stdout.write(rendered)
        return 0

    if args.check:
        committed = repo / "docs" / ARTIFACT
        if not committed.is_file():
            print(
                f"stapel-template-contract: --check: {committed} does not exist "
                "— run `make contract` and commit it",
                file=sys.stderr,
            )
            return 1
        if committed.read_text() != rendered:
            print(
                f"DRIFT: {committed} is stale — run `make contract` and commit it",
                file=sys.stderr,
            )
            return 1
        print(f"stapel-template-contract: --check: {committed} is up to date", file=sys.stderr)
        return 0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ARTIFACT).write_text(rendered)
    print(
        f"{doc['module']} templates: {len(doc['routes'])} routes, "
        f"{len(doc['templates'])} templates → {out_dir}/{ARTIFACT}",
        file=sys.stderr,
    )
    return 0


# ── consumer helper ──────────────────────────────────────────────────────────

def load_contract(module_dir: Path) -> dict:
    """Read a dependency's committed ``docs/templates.json``.

    This is the half a HOST project uses: point it at the installed package
    directory (``Path(stapel_notifications.__file__).parent``) and gate your
    overrides against what the library declares TODAY, not against what it
    declared when the override was written.
    """
    path = module_dir / "docs" / ARTIFACT
    if not path.is_file():
        raise EmitError(
            f"{path} is missing — this dependency does not publish a template "
            "contract, so an override of its templates cannot be gated. Pin a "
            "version that ships docs/templates.json."
        )
    return json.loads(path.read_text())


def declared_for(contract: dict, template_path: str) -> set[str]:
    """Variables the library declares for one template path.

    Raises :class:`EmitError` when the path is unknown — which is the whole
    point: a host override whose path has vanished upstream is a dead override,
    and a name-matching guard cannot see it.
    """
    for entry in contract["templates"]:
        if entry["path"] == template_path:
            return set(entry["declared"])
    known = ", ".join(sorted(e["path"] for e in contract["templates"]))
    raise EmitError(
        f"{contract['module']} {contract['module_version']} declares no template "
        f"at {template_path!r} — an override of that path shadows nothing and is "
        f"dead code. Known templates: {known}"
    )
