"""
stapel-schema-lint — the "``docs/schema.json`` is a CLAIM, and the drift gate
compares the claim with itself" gate, in the ``stapel-image-lint`` /
``stapel-env-address-lint`` idiom (rule codes, ``--json``, ``--strict``, exit
1 on any error).

Why this exists
----------------
``docs/schema.json`` is emitted from the views' ``@extend_schema``
annotations. An annotation is a hand-written statement about what a method
returns, and drf-spectacular has no way to check it against the method body —
it copies the claim into the document.

Every library in this fleet ships a per-module contract gate
(``tests/test_contract.py``) that compares the committed ``docs/schema.json``
against a FRESH EMISSION of the same annotations. That gate proves exactly one
thing: the committed file is not stale. It cannot notice that the claim is
false, because both sides of its comparison come from the same claim.

``stapel-alerts`` 0.2.0 is the worked example. ``GET /alerts/api/v1/issues``
carried ``responses=IssueSerializer(many=True)`` — declared ``Issue[]`` — while
the method body built ``{"count": …, "offset": …, "limit": …, "results": …}``
and returned it. The drift gate was green, on every run, for the whole life of
0.2.0. The frontend pair generated a typed client from the document, read
``response[0]``, and rendered ``undefined``. The defect was found by a person
looking at a screen, which is the most expensive place in the estate to find
one.

The fix (0.2.1, ``tests/test_contract_wire.py``) is the gate the generator
cannot be: it reads the COMMITTED schema, enumerates every operation the
document declares with a JSON response body, performs each of them against a
real test client, and validates the body it gets back against the schema it
was promised. That test is the proof. This linter is what checks the proof
exists, and what catches the one contradiction that needs no proof at all
because it is visible in the source.

What this linter is, and is not
--------------------------------
It does **not** re-run the check. stapel-tools is dependency-free and runs in
pre-commit; it cannot boot twenty-seven Django applications, migrate twenty-
seven databases and issue requests. Two designs were available:

1. **Re-implement the wire check here**, emitting each library's schema and
   driving its views from inside the linter. That needs Django, DRF,
   drf-spectacular, each library's settings and each library's database — it
   is the library's own test suite, relocated to a place where it has none of
   its dependencies.
2. **Grade the PRESENCE AND SHAPE of the proof**, plus the contradictions that
   are decidable from source alone. That is what this module does.

So the rules below are about evidence, not about wire bodies: a library that
publishes response bodies must own a test that proves them (SCH001), a test
that looks like that proof must actually derive its work list from the
committed document rather than from a hand-picked list (SCH002), and a
declaration that contradicts its own method body in a way no serializer
knowledge is needed to see is reported outright (SCH003).

Rules
-----
SCH001  (warning, during the sweep) The library commits a ``docs/schema.json``
        that declares at least one 2xx response with an ``application/json``
        body, and the repository contains NO WIRE TEST — no module under
        ``tests/`` that reads the committed document, enumerates its
        ``paths``, issues real client requests and validates the bodies it
        receives against the declared schemas.

        WARNING AND NOT ERROR, deliberately. On the day this rule shipped,
        26 of the 27 libraries that commit a schema had no such test — only
        ``stapel-alerts``, which wrote one the day the defect was found. A
        rule that turns the whole fleet red on the day it lands is a rule
        people learn to skim past, which is strictly worse than no rule; the
        same reasoning that keeps IMG001 and DOC001 at warning level while
        their sweeps run. ``--strict`` promotes it, which is how a library
        that HAS written its wire test keeps the gate closed before the
        default level flips.

SCH002  (error) A module under ``tests/`` looks like a wire test — it issues
        client requests AND validates response bodies with ``jsonschema`` —
        but does not derive its operation list from the committed document.
        It validates a hand-picked list of endpoints against hand-picked
        schemas.

        This is the "green gate that covers three of four rows" family: the
        operation somebody forgot to add is exactly the operation nobody
        checks, and the report says "wire contract: passed". An error from
        the first day, and safe to be: it can only fire on a library that has
        already written such a test, so it cannot turn a library red for work
        it has not started.

SCH003  (error) A method decorated with ``@extend_schema`` declares an ARRAY
        as its 2xx response (``responses=X(many=True)``, ``responses={200:
        X(many=True)}``, or a ``ListSerializer``) while its own body returns
        an OBJECT built by hand — a dict literal, passed positionally to the
        response it returns, or assigned to a name and returned through a
        helper.

        Declared-array-versus-built-object is a contradiction at the level of
        TYPE. It needs no knowledge of the serializer's fields, no emission,
        and no running application: an array is not an object, whatever is
        inside either of them. That is why it is an error from the first day.
        This is the exact shape ``stapel-alerts`` 0.2.0 had.

        A view declaring a ``pagination_class`` is exempt, and has to be:
        drf-spectacular's pagination hook wraps a ``many=True`` response in
        that paginator's envelope before it reaches the document, so the
        annotation declares ``PaginatedXList`` — an object — and a
        hand-assembled envelope in the body is the matching body, not a
        contradiction (``stapel-workspaces``' audit endpoint is exactly
        this, and is correct).

What these rules provably CANNOT catch
---------------------------------------
* **A declared object whose FIELDS are wrong.** ``responses=IssueSerializer``
  on a method that builds ``{"issue": …}`` by hand is a lie this linter reads
  as object-versus-object and says nothing about. Field-level truth requires
  knowing what the serializer emits, which requires importing it, which
  requires the application. That is SCH001's job to demand a test for, not
  SCH003's job to decide.
* **A body assembled anywhere but a dict literal in the same function.** A
  payload built by a helper, a ``dict(...)`` call, a comprehension, a module
  constant, or a serializer subclass is invisible to SCH003 — on purpose.
  Following values across functions turns a decidable check into a guess, and
  a linter that guesses a contradiction into existence on a published
  contract is discredited on the first line a reader checks.
* **Status codes, headers, error envelopes, content negotiation.** Only 2xx
  ``application/json`` bodies are in scope.
* **Whether an EXISTING wire test is complete.** SCH001 and SCH002 grade the
  shape of the proof, never its coverage. A wire test that enumerates the
  committed ``paths`` and then ``continue``s past half of them satisfies both
  rules. The defence against that lives inside the test itself — see
  ``stapel-alerts/tests/test_contract_wire.py``, which fails loudly on an
  operation it has no request recipe for rather than skipping it.
* **A schema that is stale relative to the annotations.** That is
  ``stapel-api-lint``/``test_contract.py``'s job, and it does it correctly.
  The two gates are complementary: one proves the document matches the
  annotations, this one is about the annotations matching the wire.

How a wire test is recognised
------------------------------
By BEHAVIOUR, never by filename. A file called ``test_contract_wire.py`` that
asserts ``2 + 2 == 4`` is not a wire test, and a proof written in
``tests/test_api.py`` is one. Four signals, read off the AST, all four
required:

(a) it READS THE COMMITTED DOCUMENT at run time — a read call (``open``,
    ``read_text``, ``read_bytes``, ``json.load``/``loads``) whose expression
    names a path whose basename is ``schema.json``, directly or through a
    name assigned such a path;
(b) it ENUMERATES THE DECLARED OPERATIONS — it subscripts the loaded document
    by ``"paths"`` (or ``.get("paths")``);
(c) it PERFORMS REAL REQUESTS — a method call on a test client
    (``client.get``, ``api_client.post``, ``staff_client.patch``, a
    ``getattr(client, method)(...)`` dispatch, or an ``APIClient()``);
(d) it VALIDATES RECEIVED BODIES against the declared schema — ``jsonschema``
    (a ``Draft*Validator``, ``validate``, ``iter_errors``) applied to a
    RESPONSE BODY (``response.json()``, ``response.data``, or a name assigned
    one), or an explicit comparison of a ``response.json()`` against a
    sub-object of the loaded document. The instance under validation has to be
    a received body: half the fleet validates event-bus payloads against
    ``docs/events/*.json`` in a module that also drives a client, and reading
    that as "the wire is proven" would report a gate nobody wrote.

(a)+(b) is "the work list comes from the contract". (c)+(d) is "the bodies are
real and are checked". SCH001 fires when no module in the tree has all four;
SCH002 fires on a module that has (c)+(d) but not (a)+(b).

Scope and exclusions
---------------------
* A **library** is a directory containing ``docs/schema.json``. ``lint_project``
  accepts either such a directory or a parent holding many of them, so the
  same call works in a library's pre-commit and over a whole workspace.
* SCH003 rides the same discovery: it grades ``@extend_schema`` in a
  schema-bearing library, where the claim becomes a published contract a
  generated client is built from. A tree with no ``docs/schema.json`` is
  silent, with a note — the same way the image rules are silent in a tree
  with no Dockerfile.
* Suppress a deliberate exception with ``# noqa: SCH003`` on the decorator
  (the shared ``stapel_tools.escape`` grammar). SCH001 and SCH002 report
  against a whole artefact rather than a line, and carry no per-line escape;
  a library that means to stay unproven says so in its lint profile.

Exit codes: 0 clean (warnings allowed), 1 errors present, 2 usage errors.
``--strict`` promotes warnings to errors.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from . import escape

SCHEMA_RELATIVE = ("docs", "schema.json")
SCHEMA_BASENAME = "schema.json"

SKIP_DIRS = {
    "__pycache__",
    "node_modules",
    "venv",
    "build",
    "dist",
    "htmlcov",
    "site-packages",
    "migrations",
}

#: The methods an OpenAPI path item may carry. Anything else under a path
#: ("parameters", "summary", vendor extensions) is not an operation.
HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)

#: Calls that READ a file. The point of the list is that every one of them
#: takes the path as part of its own expression, so the string constant naming
#: the document is inside the call node's subtree.
READ_CALLS = frozenset({"open", "read_text", "read_bytes", "load", "loads"})

#: HTTP verbs a test client is driven by.
CLIENT_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"}
)

#: What makes a receiver a test client. Substring, lowercased: `client`,
#: `api_client`, `staff_client`, `self.client`, `anon_client` are all the same
#: thing, and a fleet that invents `owner_client` tomorrow needs no edit here.
CLIENT_HINT = "client"

#: Validator entry points that mean "something was checked against a schema".
JSONSCHEMA_CALLS = frozenset({"validate", "iter_errors", "is_valid"})

#: Attributes that hold a RECEIVED body when read off a response object.
BODY_ATTRIBUTES = frozenset({"data", "content", "json"})

#: What makes a receiver a response. Substring, lowercased.
RESPONSE_HINTS = ("response", "resp")

_STATUS_CONST_RE = re.compile(r"HTTP_(\d{3})")


@dataclass
class Violation:
    path: str
    line: int
    rule: str
    message: str
    level: str = "error"

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


@dataclass
class WireSignals:
    """The four behaviours that, together, make a module a wire test."""

    #: (a) reads the committed docs/schema.json at run time
    reads_committed_schema: bool = False
    #: (b) enumerates the declared operations out of it
    enumerates_paths: bool = False
    #: (c) performs real client requests
    performs_requests: bool = False
    #: (d) validates received bodies against a schema
    validates_bodies: bool = False
    #: names bound to the loaded document, for the report
    schema_names: list = field(default_factory=list)

    @property
    def derives_operations(self) -> bool:
        return self.reads_committed_schema and self.enumerates_paths

    @property
    def exercises_the_wire(self) -> bool:
        return self.performs_requests and self.validates_bodies

    @property
    def is_wire_test(self) -> bool:
        return self.derives_operations and self.exercises_the_wire

    def missing(self) -> list:
        """Which of (a)/(b) a wire-shaped module fails — the SCH002 message."""
        gaps = []
        if not self.reads_committed_schema:
            gaps.append("it never reads the committed docs/schema.json")
        if not self.enumerates_paths:
            gaps.append("it never enumerates the document's `paths`")
        return gaps


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def _skipped_dir(name: str) -> bool:
    # Dot directories cover .git, .venv, .tox, .worktrees, .vendor and the
    # cache directories in one rule; a library root is never a dot directory.
    return name.startswith(".") or name in SKIP_DIRS or name.endswith(".egg-info")


def _walk(root: Path) -> Iterable[tuple[Path, list, list]]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _skipped_dir(d))
        yield Path(dirpath), dirnames, sorted(filenames)


def schema_path(library: Path) -> Path:
    return library.joinpath(*SCHEMA_RELATIVE)


def find_libraries(root: Path) -> list:
    """Every directory under ``root`` (``root`` itself included) that commits
    a ``docs/schema.json``."""
    root = Path(root).resolve()
    found: list = []
    if schema_path(root).is_file():
        return [root]
    for dirpath, _dirnames, _filenames in _walk(root):
        if schema_path(dirpath).is_file():
            found.append(dirpath)
    return sorted(found)


def _python_files(library: Path) -> list:
    out: list = []
    for dirpath, _dirnames, filenames in _walk(library):
        for name in filenames:
            if name.endswith(".py"):
                out.append(dirpath / name)
    return sorted(out)


def _test_modules(library: Path) -> list:
    """Every ``.py`` under a directory named ``tests`` in this library."""
    out: list = []
    for dirpath, _dirnames, filenames in _walk(library):
        if "tests" not in dirpath.parts:
            continue
        for name in filenames:
            if name.endswith(".py"):
                out.append(dirpath / name)
    return sorted(out)


def _parse(path: Path) -> Optional[ast.Module]:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        return None


# ---------------------------------------------------------------------------
# the committed document
# ---------------------------------------------------------------------------


def declared_json_operations(document: dict) -> list:
    """``(method, path)`` for every operation declaring a 2xx
    ``application/json`` body with a schema — the rows a wire test has to
    cover, and the reason SCH001 has anything to say."""
    out: list = []
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return out
    for path, item in sorted(paths.items()):
        if not isinstance(item, dict):
            continue
        for method, operation in sorted(item.items()):
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            responses = operation.get("responses")
            if not isinstance(responses, dict):
                continue
            for code, response in sorted(responses.items()):
                if not str(code).startswith("2") or not isinstance(response, dict):
                    continue
                body = (response.get("content") or {}).get("application/json")
                if isinstance(body, dict) and "schema" in body:
                    out.append((method.upper(), path))
                    break
    return out


def _read_document(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _called_name(node: ast.AST) -> str:
    """The trailing name of a call target: ``json.loads`` -> ``loads``,
    ``jsonschema.Draft202012Validator`` -> ``Draft202012Validator``."""
    if isinstance(node, ast.Call):
        return _called_name(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _root_name(node: ast.AST) -> str:
    """The base name an expression hangs off: ``SCHEMA["paths"].items`` ->
    ``SCHEMA``."""
    while True:
        if isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, ast.Subscript):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            break
    return node.id if isinstance(node, ast.Name) else ""


def _names_in(node: ast.AST) -> set:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _mentions_schema_file(node: ast.AST) -> bool:
    """Does this expression name a path whose basename is ``schema.json``?"""
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            text = child.value
            if text == SCHEMA_BASENAME or text.endswith("/" + SCHEMA_BASENAME):
                return True
    return False


def _reads_response_body(node: ast.AST) -> bool:
    """Is this expression a RECEIVED body — ``response.json()``,
    ``response.data``, ``resp.content`` — anywhere inside it?"""
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and _called_name(child) == "json":
            if not child.args and not child.keywords:
                return True
        if isinstance(child, ast.Attribute) and child.attr in BODY_ATTRIBUTES:
            root = _root_name(child.value).lower()
            if any(hint in root for hint in RESPONSE_HINTS):
                return True
    return False


def _assigned_names(node: ast.AST) -> list:
    targets = getattr(node, "targets", None)
    if targets is None:
        single = getattr(node, "target", None)
        targets = [single] if single is not None else []
    return [t.id for t in targets if isinstance(t, ast.Name)]


# ---------------------------------------------------------------------------
# wire-test detection
# ---------------------------------------------------------------------------


def analyse_wire_signals(tree: ast.Module) -> WireSignals:
    """Read the four behaviours (a)-(d) off a test module's AST."""
    signals = WireSignals()

    # Names holding a PATH to the committed document, so a two-step
    # `SCHEMA_PATH = ROOT / "docs" / "schema.json"` / `json.loads(
    # SCHEMA_PATH.read_text())` reads the same as the one-liner.
    path_names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            if _mentions_schema_file(node.value):
                path_names.update(_assigned_names(node))

    def _reads_document(call: ast.Call) -> bool:
        if _called_name(call) not in READ_CALLS:
            return False
        return _mentions_schema_file(call) or bool(_names_in(call) & path_names)

    # (a) and the names the loaded document is bound to
    schema_names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _reads_document(node):
            signals.reads_committed_schema = True
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        if any(
            isinstance(child, ast.Call) and _reads_document(child)
            for child in ast.walk(node.value)
        ):
            schema_names.update(_assigned_names(node))
    signals.schema_names = sorted(schema_names)

    # (b) — the operation list comes OUT of that document
    for node in ast.walk(tree):
        base: Optional[ast.AST] = None
        if isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant) and key.value == "paths":
                base = node.value
        elif isinstance(node, ast.Call) and _called_name(node) == "get" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and first.value == "paths":
                base = node.func.value if isinstance(node.func, ast.Attribute) else None
        if base is None:
            continue
        # Bound to the loaded document when we know its name; when the read is
        # inline (`json.loads(...)["paths"]`) there is no name to match and the
        # read itself is the binding.
        if _root_name(base) in schema_names or not schema_names:
            if signals.reads_committed_schema:
                signals.enumerates_paths = True

    # (c) — real requests
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in CLIENT_METHODS:
            if CLIENT_HINT in _root_name(func.value).lower():
                signals.performs_requests = True
        if _called_name(node) == "APIClient":
            signals.performs_requests = True
        if _called_name(node) == "getattr" and node.args:
            if CLIENT_HINT in _root_name(node.args[0]).lower():
                signals.performs_requests = True

    # (d) — RECEIVED BODIES validated against a schema. The instance under
    # validation has to be a response body, not merely something: half the
    # fleet validates event-bus payloads against docs/events/*.json in the
    # same test module that also drives a client, and a rule that reads that
    # as "the wire is proven" reports a gate nobody wrote.
    body_names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            if _reads_response_body(node.value):
                body_names.update(_assigned_names(node))

    def _validates_received_body(call: ast.Call) -> bool:
        for child in ast.walk(call):
            if isinstance(child, ast.Name) and child.id in body_names:
                return True
            if _reads_response_body(child):
                return True
        return False

    imports_jsonschema = any(
        (isinstance(n, ast.Import) and any(a.name.split(".")[0] == "jsonschema" for a in n.names))
        or (isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "jsonschema")
        for n in ast.walk(tree)
    )
    if imports_jsonschema:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _called_name(node)
            if (name.startswith("Draft") or name in JSONSCHEMA_CALLS) and \
                    _validates_received_body(node):
                signals.validates_bodies = True
    if not signals.validates_bodies and schema_names:
        # The equivalent without the library: an explicit comparison of a
        # received body against a sub-object of the loaded document.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            sides = [node.left] + list(node.comparators)
            received = any(
                isinstance(c, ast.Call) and _called_name(c) == "json"
                for side in sides
                for c in ast.walk(side)
            )
            declared = any(
                _names_in(side) & schema_names for side in sides
            )
            if received and declared:
                signals.validates_bodies = True

    return signals


# ---------------------------------------------------------------------------
# SCH003 — declared array, built object
# ---------------------------------------------------------------------------


def _extend_schema_calls(node: ast.AST) -> list:
    out: list = []
    for decorator in getattr(node, "decorator_list", []):
        if isinstance(decorator, ast.Call) and _called_name(decorator) == "extend_schema":
            out.append(decorator)
    return out


def _is_array_declaration(node: ast.AST) -> bool:
    """``X(many=True)`` or a ``ListSerializer`` — the two ways an annotation
    says "this response is an array"."""
    if not isinstance(node, ast.Call):
        return False
    if _called_name(node).endswith("ListSerializer"):
        return True
    for keyword in node.keywords:
        if keyword.arg == "many" and isinstance(keyword.value, ast.Constant):
            if keyword.value.value is True:
                return True
    return False


def declared_2xx_array(call: ast.Call) -> Optional[ast.AST]:
    """The node declaring a 2xx ARRAY response, or None.

    A ``responses`` dict is read per status code so a ``{200: X(many=True),
    404: ErrorSerializer}`` says what it means; a bare value is the 200.
    """
    for keyword in call.keywords:
        if keyword.arg != "responses":
            continue
        value = keyword.value
        if isinstance(value, ast.Dict):
            for key, item in zip(value.keys, value.values):
                if key is None or not isinstance(key, ast.Constant):
                    continue
                if not str(key.value).startswith("2"):
                    continue
                if _is_array_declaration(item):
                    return item
            return None
        if _is_array_declaration(value):
            return value
    return None


def _declared_status_codes(call: ast.Call) -> list:
    """Every status code this response call states, from a leading positional
    integer (``StapelErrorResponse(404, …)``) or a ``status=`` keyword."""
    codes: list = []

    def _code(node: ast.AST) -> Optional[int]:
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        name = node.attr if isinstance(node, ast.Attribute) else (
            node.id if isinstance(node, ast.Name) else ""
        )
        match = _STATUS_CONST_RE.search(name)
        return int(match.group(1)) if match else None

    if call.args:
        first = _code(call.args[0])
        if first is not None and 100 <= first <= 599:
            codes.append(first)
    for keyword in call.keywords:
        if keyword.arg in ("status", "status_code"):
            found = _code(keyword.value)
            if found is not None:
                codes.append(found)
    return codes


def _is_error_response(call: ast.Call) -> bool:
    return any(code >= 300 for code in _declared_status_codes(call))


def _own_statements(func: ast.AST) -> Iterable[ast.AST]:
    """Every node in this function's body, in source order, EXCEPT anything
    inside a function nested in it — a closure's ``return`` is not this
    method's return, and a helper defined inline does not describe the
    response the decorator declares."""

    def _descend(node: ast.AST) -> Iterable[ast.AST]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield child
            yield from _descend(child)

    for statement in func.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield statement
        yield from _descend(statement)


def hand_built_object(func: ast.AST) -> Optional[tuple]:
    """``(line, source)`` of a dict literal this function returns as its body,
    or None.

    Only POSITIONAL arguments of a returned call are considered — a
    ``headers={…}`` keyword is metadata, not the body — and only a dict
    LITERAL, directly or through a name assigned one in the same function. A
    call that names a non-2xx status is skipped: an error envelope built by
    hand on the 400 branch says nothing about the 200 the annotation
    describes.
    """
    literals: dict = {}
    for node in _own_statements(func):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Dict):
            for name in _assigned_names(node):
                literals.setdefault(name, node.value)

    for node in _own_statements(func):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if _is_error_response(call):
            continue
        for argument in call.args:
            if isinstance(argument, ast.Dict):
                return argument.lineno, _source(argument)
            if isinstance(argument, ast.Name) and argument.id in literals:
                literal = literals[argument.id]
                return literal.lineno, f"{argument.id} = {_source(literal)}"
    return None


def _source(node: ast.AST, limit: int = 120) -> str:
    try:
        text = ast.unparse(node)
    except Exception:  # pragma: no cover - unparse covers every node we pass
        return "<unparseable>"
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _paginated(classdef: Optional[ast.ClassDef], classes: dict, seen=None) -> bool:
    """Does the view class declare a ``pagination_class``?

    drf-spectacular's pagination hook WRAPS a ``many=True`` response in that
    paginator's envelope before it reaches the document, so on a paginated
    view ``responses=X(many=True)`` does not declare an array at all — it
    declares ``PaginatedXList``, an object. A hand-assembled envelope in the
    method body is then the correct body, not a contradiction. Checked before
    anything else, because this is the one shape that looks exactly like the
    defect and is not it (``stapel-workspaces``' audit endpoint).
    """
    if classdef is None:
        return False
    seen = seen or set()
    if classdef.name in seen:
        return False
    seen.add(classdef.name)
    for statement in classdef.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        if "pagination_class" not in _assigned_names(statement):
            continue
        value = statement.value
        if value is not None and not (
            isinstance(value, ast.Constant) and value.value is None
        ):
            return True
    for base in classdef.bases:
        name = base.attr if isinstance(base, ast.Attribute) else (
            base.id if isinstance(base, ast.Name) else ""
        )
        if name in classes and _paginated(classes[name], classes, seen):
            return True
    return False


def lint_source_file(path: Path) -> list:
    """SCH003 over one Python file."""
    tree = _parse(path)
    if tree is None:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):  # pragma: no cover - _parse read it
        return []

    classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    owner: dict = {}
    for classdef in classes.values():
        for statement in classdef.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner[id(statement)] = classdef

    violations: list = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _paginated(owner.get(id(node)), classes):
            continue
        for decorator in _extend_schema_calls(node):
            declared = declared_2xx_array(decorator)
            if declared is None:
                continue
            built = hand_built_object(node)
            if built is None:
                continue
            escape_lines = list(
                range(decorator.lineno, (decorator.end_lineno or decorator.lineno) + 1)
            ) + [node.lineno]
            if escape.any_line_suppressed(lines, "SCH003", *escape_lines):
                continue
            line, source = built
            violations.append(Violation(
                str(path), declared.lineno, "SCH003",
                f"{node.name}() declares an ARRAY response "
                f"(responses={_source(declared)}) and returns an OBJECT it "
                f"builds by hand at line {line}: {source}. An array is not an "
                f"object, so the committed docs/schema.json describes a body "
                f"this method never sends, and a client generated from it "
                f"reads the wrong shape at run time — stapel-alerts 0.2.0 "
                f"shipped exactly this and the drift gate stayed green, "
                f"because it compared the claim with itself. Declare the "
                f"envelope you actually send (a serializer that renders it, "
                f"so the contract and the wire are one object) and prove it "
                f"with a wire test (SCH001)",
            ))
    return violations


# ---------------------------------------------------------------------------
# lint driver
# ---------------------------------------------------------------------------


def lint_library(library: Path) -> list:
    violations: list = []
    document_path = schema_path(library)
    document = _read_document(document_path)
    operations = declared_json_operations(document) if document else []

    wire_tests: list = []
    for module in _test_modules(library):
        tree = _parse(module)
        if tree is None:
            continue
        signals = analyse_wire_signals(tree)
        if signals.is_wire_test:
            wire_tests.append(module)
            continue
        if signals.exercises_the_wire:
            violations.append(Violation(
                str(module), 1, "SCH002",
                f"issues client requests and validates bodies with jsonschema, "
                f"but {' and '.join(signals.missing())} — so the operations it "
                f"checks are a hand-picked list, and the operation nobody "
                f"remembered to add is exactly the one nobody checks, under a "
                f"report that reads 'passed'. Load "
                f"{Path(*SCHEMA_RELATIVE)} and iterate its `paths`, so a new "
                f"operation is covered the moment it is declared; see "
                f"stapel-alerts/tests/test_contract_wire.py",
            ))

    if operations and not wire_tests:
        first = ", ".join(f"{method} {path}" for method, path in operations[:3])
        more = f" (+{len(operations) - 3} more)" if len(operations) > 3 else ""
        violations.append(Violation(
            str(document_path), 1, "SCH001",
            f"declares {len(operations)} operation(s) with a 2xx "
            f"application/json body — {first}{more} — and no test in this "
            f"tree proves any of them. tests/test_contract.py compares this "
            f"document against a fresh emission of the same @extend_schema "
            f"annotations, so it cannot see an annotation that is simply "
            f"false: it compares the claim with itself. Add a wire test that "
            f"reads THIS file, enumerates its `paths`, performs each "
            f"operation against a test client and validates the body it gets "
            f"back against the declared schema — the shape of "
            f"stapel-alerts/tests/test_contract_wire.py",
            level="warning",
        ))

    for source_file in _python_files(library):
        violations.extend(lint_source_file(source_file))
    return violations


def lint_project(project: Path, notes: Optional[list] = None) -> list:
    violations: list = []
    libraries = find_libraries(Path(project))
    for library in libraries:
        violations.extend(lint_library(library))
    if notes is not None and not libraries:
        notes.append(
            f"no {Path(*SCHEMA_RELATIVE)} in this tree — schema-wire rules are silent"
        )
    violations.sort(key=lambda v: (v.path, v.line, v.rule))
    return violations


def lint_paths(paths: Iterable) -> list:
    violations: list = []
    for raw in paths:
        root = Path(raw)
        if not root.exists():
            raise SystemExit(f"Error: path does not exist: {root}")
        violations.extend(lint_project(root))
    violations.sort(key=lambda v: (v.path, v.line, v.rule))
    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-schema-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths", nargs="*", default=["."],
        help="Library roots, or a workspace holding many of them (default: .)",
    )
    parser.add_argument("--json", action="store_true", help="Machine output")
    parser.add_argument(
        "--strict", action="store_true",
        help="Promote warnings to errors — for a library that HAS written its "
             "wire test and wants SCH001 to stay closed.",
    )
    args = parser.parse_args(argv)

    violations = lint_paths(args.paths)
    if args.strict:
        for violation in violations:
            violation.level = "error"
    errors = [v for v in violations if v.level == "error"]

    if args.json:
        print(json.dumps(
            {
                "ok": not errors,
                "errors": len(errors),
                "violations": [v.to_dict() for v in violations],
            },
            indent=2, sort_keys=True, ensure_ascii=False,
        ))
    else:
        for violation in violations:
            print(violation)
        if violations:
            print(f"\n{len(errors)} error(s), "
                  f"{len(violations) - len(errors)} warning(s) found.")
        else:
            print("No violations found.")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
