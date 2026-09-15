"""
stapel-bounds-lint — the "untrusted external text assigned to a bounded column
without a boundary" gate, in the ``stapel-image-lint`` / ``stapel-schema-lint``
idiom (rule codes, ``--json``, ``--strict``, exit 1 on any error).

Why this exists
----------------
2026-09-15, a client's alert store: every report answered 500. A payload
string longer than 255 characters reached ``title =
models.CharField(max_length=255)``, Postgres raised
``StringDataRightTruncation`` out of ``Issue.objects.create(...)``, the ingest
transaction rolled back, and the endpoint 500'd. Eight in twenty minutes. The
worst possible failure for that library in particular, because the report that
was lost was a report ABOUT a defect, and the 500 made the alert store the
loudest error on the host.

Postgres does not truncate. Django's ``max_length`` is a form-layer and
``full_clean()`` concern; a ``.objects.create()`` never validates it, so a
column bound declared in a model is not a bound enforced on the path that
writes it. Every string that arrives from another process — an exception
message, a stack frame, a ``request_path``, a header, a field of a JSON body —
has a length nobody promised, and all of them land in ``CharField``s.

The canonical repair is ``stapel-alerts/bounds.py``, and it states the rule
this linter checks, in its own words:

    So the rule is structural rather than per-field: **no value from a payload
    reaches a bounded column unbounded**, and the bound is read off the field
    itself. Typing the numbers here a second time would make a future
    ``max_length`` change a silent data-loss bug — the model would grow and
    the truncation would not, or worse, shrink and the truncation would not.

and the distinction between its three functions is the whole of the remedy
this linter points at:

* ``fit(model, field, value)`` — descriptive text. Cut, with the cut MARKED,
  so a reader can tell "this title is the whole message" from "this title is
  the front of a longer one". A silent truncation is a lie about the data.
* ``choice_or_default(model, field, value)`` — a ``choices`` field. ``level``
  and ``kind`` are vocabularies, not prose: a value that is not in the
  vocabulary is not made to fit by cutting it, because ``"warninggggg"[:16]``
  is not a level. It falls back to the field's own default.
* ``max_length_of(model, field)`` — the one place the limit is read.

Which is why this linter reads the limit off the model too (see "The field
table" below) rather than carrying a table of its own: a rule that hardcoded
255 would keep reporting a field that grew to 512 and go quiet on one that
shrank to 64.

Rules
-----
BND001  (error) **The splat.** ``Model(**d)``, ``Model.objects.create(**d)``,
        or a ``defaults=d`` handed to ``update_or_create`` / ``get_or_create``,
        where ``d`` is payload-derived and the model declares at least one
        ``max_length`` field.

        No per-field analysis is needed and none is done: EVERY key in that
        dict lands in a column, so every bounded column on that model is
        reachable by an over-long string from outside. This is the shape that
        breaks first and the shape that is hardest to see in review, because
        the field names are not in the source at all.

BND002  (error) **The direct subscript.** A keyword argument of a model write
        (``Model(...)``, ``.create``, ``.update``, ``.update_or_create``,
        ``.get_or_create``), an entry of a ``defaults={...}`` literal, or an
        attribute assignment ``obj.field = ...``, naming a ``max_length``
        field, whose value expression's ROOT is a payload source and which
        carries no bound in between. ``title=payload["title"]``,
        ``service=data.get("service", "")``, ``obj.culprit =
        request.data["culprit"]``.

        A bound is a fitter call (``fit``, ``bounds.fit``, ``truncate*``,
        ``choice_or_default``, ``Truncator``, anything named with
        ``--fitter``), a slice ``x[:N]``, a ``max_length_of(...)``-derived
        expression, or a cast that cannot produce text (``int``, ``float``,
        ``bool``, ``len``).

        A **membership test against a vocabulary** is a bound too — ``if lang
        not in SUPPORTED_LANGUAGES: return 400``, ``if state not in {s for s,
        _ in STATE_CHOICES}`` — because after it the value is one of a fixed
        set and its length is that set's. That is what a careful endpoint
        writes instead of ``choice_or_default``, and it is not the same thing
        as a null check (``if doc_key in (None, "")``) or a lookup (``if key
        not in by_key``, usually followed by creating a row with that very
        key); see :data:`VOCABULARY_HINTS` for where the line is drawn.

BND003  (warning) **The inverse burden.** A ``max_length`` field assigned a
        value that is neither provably bounded nor obviously constant —
        wherever that write is, with no requirement that a payload still be
        visible in the same frame.

        That last clause is the whole point of the rule, and it was measured
        before it was chosen. The 2026-09-15 write lived in a service function
        the ingest view called with the payload already unpacked into ordinary
        ``str`` parameters: ``title=norm.title_for(trace, message=message)``,
        inside ``record()``, where nothing is left for a taint analysis to
        see. Conditioning BND003 on "a payload source is in scope" makes it
        quiet (5 findings instead of 39, over the 36 libraries of the
        fleet sweep) and makes it
        silent on the defect it was commissioned for, which is the family of
        green gate this fleet already has too many of. So the burden sits
        where ``bounds.py`` puts it: on the WRITE. A column with a declared
        limit is given a value; either the source says the value fits, or
        nobody does.

        It is still the NARROWEST possible reading of "not provably bounded":
        only a CALL result, or a string built out of one. A bare name is never
        reported, however unknown its provenance, because
        ``Model.objects.create(name=name)`` is most of the fleet and a warning
        tier that fires on it is a tier people learn to skim past — strictly
        worse than no tier.

        Provably safe and never reported: a string literal, or any expression
        that folds to one that fits (``"a" * 64``); a pure dotted chain
        (``Level.ERROR``, ``X.Status.NEW``, ``settings.SERVICE``); a
        digest-shaped call (anything named ``*uuid*``, ``*hash*``,
        ``*digest*``, ``*sha*``, ``*fingerprint*``, ``token_hex``,
        ``get_random_string``); a value read off another instance's
        SAME-NAMED field (``title=series.title`` — a column cannot overflow
        the column it came out of); an f-string or concatenation all of whose
        pieces are themselves safe; a fitter, a slice or a cast applied to the
        WHOLE value. Test modules are out of scope entirely: a suite's
        fabricated ``file_hash="a" * 64`` is not untrusted text, and those
        were 140 of the rule's first 196 findings.

        The bound has to be on the whole value, not somewhere inside it. The
        pre-fix ``record()`` opens with ``trace = (trace or "")[:MAX_TRACE]``
        — a 60000-character slice — and then passes ``trace`` to the helper
        whose result goes into a 255 column; a rule that read that slice as
        the title's bound would be green on the write that answered 500.

What these rules provably CANNOT catch
---------------------------------------
Be blunt about it, because the gap is where the next one will come from:

* **Taint does not cross a function boundary, so BND001 and BND002 stop at
  one.** The analysis is per-function. A view that reads ``request.data`` and
  calls ``record(service=payload["service"], ...)``, where ``record()`` writes
  the row, is invisible to both: at the call site there is no model, and
  inside ``record()`` the values arrive as ordinary ``str`` parameters with no
  mark on them. That is exactly the arrangement the 2026-09-15 incident had.
  Following values across functions (and across modules) turns a decidable
  check into a guess, and a linter that guesses a defect into existence is
  discredited on the first line a reader checks — so instead of guessing, the
  frame BND001/BND002 cannot reach is covered by BND003, which asks the write
  itself to prove the value fits and does not care where it came from. The
  positive control for that is in the suite: the pre-fix ``services.py``
  reports, the repaired one is silent.
* **A DRF serializer that DOES enforce the limit is invisible.** A
  ``ModelSerializer`` field built off a bounded column — or an explicit
  ``CharField(max_length=N)`` — validates length on input and answers 400, so
  a value that reached the view through one is already bounded, and nothing at
  the write says so. This is the largest family under ``serializer.
  validated_data``, and the fleet sweep shows it splits BOTH ways, which is
  why the rule does not assume either: ``stapel-recordings``' create endpoint
  validates ``title`` with ``CharField(max_length=500)`` into a 500 column (a
  finding that is false today, and fragile for exactly the reason
  ``bounds.py`` gives — the limit is typed twice, so the day the column
  shrinks the serializer still says 500), while ``stapel-categories``'
  ``CategoryCommandSerializer`` is a plain ``Serializer`` whose ``name`` and
  ``slug`` are ``CharField()`` with NO ``max_length`` at all, over 255-char
  columns — an over-long name passes validation and 500s the write. An
  automatic "it came through a serializer, it is fine" would have hidden the
  second family to quieten the first. The answer to a false one is a
  ``# noqa: BND002`` naming the serializer that bounds it.
* **A payload that arrives under a name this module does not know.** The
  sources are enumerated (:data:`REQUEST_ATTRS`, ``json.loads``,
  ``serializer.validated_data``, a parameter named ``payload`` / ``data`` /
  ``report`` / ``event`` / ``body``). A dict read off a Kafka message, a
  Celery task argument called ``msg``, a file, or an LLM response is not a
  source here, and nothing it feeds is reported.
* **``**kwargs`` is not a payload source unless it is named like one.**
  ``def make(**kwargs): return Model(**kwargs)`` is the most common factory in
  the estate and almost never a defect; ``def ingest(**payload)`` is.
* **Whether a bound is the RIGHT bound.** ``value[:64]`` into a
  ``max_length=32`` column passes BND002 and fails in production. The bound
  has to be read off the field to be correct, and only ``max_length_of`` /
  ``fit`` do that — this linter grades the PRESENCE of a boundary, never its
  arithmetic.
* **A model whose fields are not declared in a ``models.py``.** The table is
  read from ``models.py`` files and ``models/`` packages only. Fields added by
  a mixin from another distribution, by ``add_to_class``, or generated at
  import time are not in it, so a write into them is not graded. Migrations
  are skipped on purpose: they describe history, not the current model.
* **``TextField`` and unbounded columns.** Out of scope by construction —
  there is nothing to overflow. So is every non-text column: an over-long
  integer is a different defect with a different fix.

The field table
----------------
Every ``class X(models.Model)`` in the library's ``models.py`` files (and any
``models/`` package) is parsed by AST; for every field whose call carries a
``max_length=<int>`` keyword the limit is recorded, along with whether the
call carries ``choices=`` — which changes the REMEDY the message names
(``choice_or_default``, not ``fit``: a vocabulary value that does not fit is
not a long value, it is a wrong one). Base classes declared in the same module
are merged in. Because the limit is read from the model, a ``max_length``
change can never leave a truncation behind.

Scope and exclusions
---------------------
* A **library** is a directory carrying a ``pyproject.toml``/``setup.py`` and
  at least one ``models.py``; ``lint_project`` accepts either such a directory
  or a parent holding many, so the same call works in a library's pre-commit
  and over a whole workspace. A tree with no models is silent, with a note.
* ``.venv``, ``node_modules``, ``.git``, ``dist``, ``build``, ``__pycache__``,
  ``site-packages`` and ``migrations`` are skipped.
* Suppress a deliberate exception with ``# noqa: BND001`` (etc.) on the call
  or assignment line — the shared ``stapel_tools.escape`` grammar.

Exit codes: 0 clean (warnings allowed), 1 errors present, 2 usage errors.
``--strict`` promotes warnings to errors.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from . import escape

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

#: Attributes of a request object that carry what a client sent.
REQUEST_ATTRS = frozenset({"data", "POST", "GET", "query_params", "headers", "body"})

#: What makes a receiver a request. Substring, lowercased — ``request``,
#: ``self.request``, ``drf_request`` are the same thing.
REQUEST_HINT = "request"

#: A parameter with one of these names IS the payload, in every ingest
#: signature in the estate. ``**kwargs`` counts only when named like this:
#: ``def make(**kwargs)`` is a factory, ``def ingest(**payload)`` is a door.
PAYLOAD_PARAM_NAMES = frozenset({"payload", "data", "report", "event", "body"})

#: Manager/queryset methods that WRITE. ``.filter(title=...)`` is a query and
#: can no more truncate than it can insert.
WRITE_METHODS = frozenset({
    "create", "acreate", "update", "aupdate", "update_or_create",
    "aupdate_or_create", "get_or_create", "aget_or_create", "bulk_create",
})

#: The methods whose ``defaults=`` keyword is a row, not a filter.
DEFAULTS_METHODS = frozenset({
    "update_or_create", "aupdate_or_create", "get_or_create", "aget_or_create",
})

#: Calls that hand back ONE ROW, so the name they are assigned to holds an
#: instance whose model is known. A queryset is deliberately not one of them:
#: ``qs.title = …`` is not a field write, and inferring a model for it would
#: grade an attribute set on a manager against a column.
INSTANCE_METHODS = frozenset({
    "create", "acreate", "get", "aget", "first", "last", "earliest", "latest",
    "get_or_create", "aget_or_create", "update_or_create", "aupdate_or_create",
    "get_object_or_404",
})

#: Calls that put a bound on a value. ``truncate*`` by prefix, because every
#: fleet and every template library spells it differently.
FITTERS = frozenset({
    "fit", "choice_or_default", "max_length_of", "Truncator", "clamp",
    "ellipsize", "shorten", "chars",
})

#: Casts that cannot produce a long string, so a value passed through one is
#: bounded whatever went in.
CAST_CALLS = frozenset({"int", "float", "bool", "len", "abs", "round"})

#: Calls whose result is fixed-length BY CONSTRUCTION: a digest, a uuid, a
#: token of a named size. ``fingerprint`` is in here for the reason
#: ``stapel-alerts/bounds.py`` states — a sha256 hex digest is exactly 64 into
#: a 64 column, fitting it could only ever be a no-op, and cutting one would
#: merge two bugs that share a prefix. Matched as a substring of the called
#: name, lowercased.
DIGEST_HINTS = (
    "uuid", "hash", "digest", "sha1", "sha224", "sha256", "sha384", "sha512",
    "md5", "blake2", "fingerprint", "token_hex", "token_urlsafe",
    "token_bytes", "get_random_string",
)

#: String methods that cannot make their receiver longer.
STRING_METHODS = frozenset({
    "lower", "upper", "strip", "lstrip", "rstrip", "casefold", "title",
    "capitalize", "swapcase",
})

#: Calls that hand back their argument essentially unchanged — the taint and
#: the boundlessness pass straight through them.
PASSTHROUGH_CALLS = frozenset({
    "str", "dict", "copy", "deepcopy", "force_str", "smart_str", "force_text",
})

MODEL_FILE = "models.py"
MODEL_PACKAGE = "models"

#: Does BND003 need a payload source visible in the SAME frame to report?
#:
#: No — and the name says the value, so no comment is load-bearing. The rule
#: as ``bounds.py`` states it is structural rather than per-field: a bounded
#: column is given a value nothing proves fits, and the burden is on the write.
#: That is the only reading that reaches the frame the 2026-09-15 incident
#: actually lived in, where a service function receives the payload already
#: unpacked into ordinary ``str`` parameters and there is no payload source
#: left to see — the positive control is ``tests/test_bounds_lint.py::
#: TestBND003::test_the_write_that_answered_500_is_reported_in_its_own_frame``.
#:
#: Setting this True is the conditioned reading: 5 findings instead of 39
#: over the fleet's 36 model-bearing libraries, and blind to the incident this
#: gate was commissioned for.
#: Both numbers were measured before the default was chosen.
BND003_REQUIRES_PAYLOAD_IN_SCOPE = False


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


@dataclass(frozen=True)
class FieldSpec:
    """One bounded column, as the model declares it."""

    model: str
    name: str
    max_length: int
    #: a ``choices=`` vocabulary — the remedy is choice_or_default, not fit
    has_choices: bool = False


#: model name -> field name -> FieldSpec
FieldTable = dict


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def _skipped_dir(name: str) -> bool:
    # Dot directories cover .git, .venv, .tox, .worktrees, .vendor and the
    # cache directories in one rule; a library root is never a dot directory.
    return name.startswith(".") or name in SKIP_DIRS or name.endswith(".egg-info")


def _walk(root: Path) -> Iterable[tuple]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _skipped_dir(d))
        yield Path(dirpath), dirnames, sorted(filenames)


def model_files(library: Path) -> list:
    """Every ``models.py`` and every module of a ``models/`` package."""
    out: list = []
    for dirpath, _dirnames, filenames in _walk(library):
        in_package = dirpath.name == MODEL_PACKAGE
        for name in filenames:
            if not name.endswith(".py"):
                continue
            if name == MODEL_FILE or in_package:
                out.append(dirpath / name)
    return sorted(out)


def python_files(library: Path) -> list:
    out: list = []
    for dirpath, _dirnames, filenames in _walk(library):
        for name in filenames:
            if name.endswith(".py"):
                out.append(dirpath / name)
    return sorted(out)


def _is_distribution(path: Path) -> bool:
    return (path / "pyproject.toml").is_file() or (path / "setup.py").is_file()


def find_libraries(root: Path) -> list:
    """Every library under ``root`` (``root`` itself included) that declares
    models.

    A library is a distribution root, because the field table is per-library:
    two libraries may each declare an ``Issue`` and merging their tables would
    grade one's call sites against the other's columns. A tree that declares
    models but is not a distribution (a fixture, a single app checked out on
    its own) is taken as one library, so the gate is usable before a
    ``pyproject.toml`` exists.
    """
    root = Path(root).resolve()
    if _is_distribution(root):
        return [root] if model_files(root) else []

    found: list = []
    for dirpath, dirnames, _filenames in _walk(root):
        if dirpath != root and _is_distribution(dirpath):
            dirnames[:] = []  # a distribution is one library, not many
            if model_files(dirpath):
                found.append(dirpath)
    if not found and model_files(root):
        return [root]

    # A model-bearing child that is NOT a distribution is still a library, and
    # must be found from a workspace exactly as it is when it is named on its
    # own — the paragraph above already says so, and the walk alone does not
    # do it: the walk only ever appends a distribution, so a tree with models
    # and no pyproject.toml is appended by nobody, and the `not found`
    # fallback cannot rescue it once any sibling has matched.
    #
    # It silently cost five findings on this gate's first sweep, three of them
    # the best BND003 had (an LLM's JSON going into a `severity(16)` and a
    # `fingerprint(255)`), and it cost them in the mode the README advertises
    # for exactly this job. A gate that covers fewer libraries from a
    # workspace than from naming each child is a gate that quietly proves less
    # than it claims, which is the failure this whole file exists to argue
    # against.
    claimed = list(found)
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        if _skipped_dir(child.name):
            continue
        if any(library == child or library.is_relative_to(child) for library in claimed):
            continue
        if model_files(child):
            found.extend(find_libraries(child))
    return sorted(set(found))


def _parse(path: Path) -> Optional[ast.Module]:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        return None


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _called_name(node: ast.AST) -> str:
    """The trailing name of a call target: ``bounds.fit`` -> ``fit``."""
    if isinstance(node, ast.Call):
        return _called_name(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _root_name(node: ast.AST) -> str:
    """The base name an expression hangs off: ``Issue.objects.create`` ->
    ``Issue``, ``request.data["x"]`` -> ``request``."""
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


def _attribute_chain(node: ast.AST) -> list:
    """``Issue.objects.create`` -> ``["Issue", "objects", "create"]``; a chain
    interrupted by a call or a subscript returns ``[]``."""
    parts: list = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return []
    parts.append(node.id)
    return list(reversed(parts))


def _assigned_names(node: ast.AST) -> list:
    targets = getattr(node, "targets", None)
    if targets is None:
        single = getattr(node, "target", None)
        targets = [single] if single is not None else []
    out: list = []
    for target in targets:
        if isinstance(target, ast.Name):
            out.append(target.id)
        elif isinstance(target, ast.Tuple):
            out.extend(e.id for e in target.elts if isinstance(e, ast.Name))
    return out


def _source(node: ast.AST, limit: int = 90) -> str:
    try:
        text = ast.unparse(node)
    except Exception:  # pragma: no cover - unparse covers every node we pass
        return "<unparseable>"
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _lines_of(node: ast.AST) -> list:
    start = getattr(node, "lineno", 0)
    end = getattr(node, "end_lineno", None) or start
    return list(range(start, end + 1))


# ---------------------------------------------------------------------------
# the field table
# ---------------------------------------------------------------------------


def _max_length_of_call(call: ast.Call) -> Optional[int]:
    for keyword in call.keywords:
        if keyword.arg != "max_length":
            continue
        value = keyword.value
        if isinstance(value, ast.Constant) and isinstance(value.value, int) \
                and not isinstance(value.value, bool):
            return value.value
    return None


def _has_choices(call: ast.Call) -> bool:
    return any(keyword.arg == "choices" for keyword in call.keywords)


def _looks_like_model_base(base: ast.AST) -> bool:
    name = base.attr if isinstance(base, ast.Attribute) else (
        base.id if isinstance(base, ast.Name) else ""
    )
    return name == "Model" or name.endswith("Model")


def collect_model_fields(tree: ast.Module) -> FieldTable:
    """``{model name: {field name: FieldSpec}}`` for one module.

    A class counts as a model when a base's trailing name is ``Model`` or ends
    in it (``models.Model``, ``BaseModel``, ``SafeDeleteModel``) or when it
    inherits from a model already collected in this module — which is also how
    an abstract base's fields reach the concrete class that uses it.
    """
    table: FieldTable = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        inherited: dict = {}
        is_model = False
        for base in node.bases:
            name = base.attr if isinstance(base, ast.Attribute) else (
                base.id if isinstance(base, ast.Name) else ""
            )
            if name in table:
                is_model = True
                inherited.update(table[name])
            elif _looks_like_model_base(base):
                is_model = True
        if not is_model:
            continue
        fields: dict = {
            name: FieldSpec(node.name, spec.name, spec.max_length, spec.has_choices)
            for name, spec in inherited.items()
        }
        for statement in node.body:
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            value = statement.value
            if not isinstance(value, ast.Call):
                continue
            limit = _max_length_of_call(value)
            if limit is None:
                continue
            for name in _assigned_names(statement):
                fields[name] = FieldSpec(node.name, name, limit, _has_choices(value))
        table[node.name] = fields
    return table


def build_field_table(library: Path) -> FieldTable:
    """The library's bounded columns, read off its models."""
    table: FieldTable = {}
    for path in model_files(library):
        tree = _parse(path)
        if tree is None:
            continue
        for model, fields in collect_model_fields(tree).items():
            if fields or model not in table:
                table.setdefault(model, {}).update(fields)
    return table


# ---------------------------------------------------------------------------
# payload sources
# ---------------------------------------------------------------------------


def _payload_parameters(func: ast.AST) -> set:
    """Parameters that ARE a payload, by name or by annotation."""
    args = getattr(func, "args", None)
    if args is None:
        return set()
    out: set = set()
    everything = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
    if args.vararg is not None:
        everything.append(args.vararg)
    if args.kwarg is not None:
        everything.append(args.kwarg)
    for arg in everything:
        # The NAME is the signal, never the annotation. A parameter annotated
        # `Event` is a model or a bus envelope — `_persist_occurrence(series:
        # Event, ...)` in stapel-calendar reads its OWN row's columns — and a
        # rule that read the annotation reported every one of them.
        if arg.arg in PAYLOAD_PARAM_NAMES:
            out.add(arg.arg)
    return out


def _body_statements(func: ast.AST) -> Iterable[ast.AST]:
    """Every node in this function's body except anything inside a function
    nested in it — a closure is its own scope and is analysed as one."""

    def _descend(node: ast.AST) -> Iterable[ast.AST]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield child
            yield from _descend(child)

    for statement in getattr(func, "body", []):
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield statement
        yield from _descend(statement)


#: What a membership test has to be tested AGAINST for the value to count as
#: bounded by it. A vocabulary, not a lookup: ``if key not in by_key`` asks
#: whether a row exists and is usually followed by creating it with that very
#: key, and ``if doc_key in (None, "")`` is a null check. Reading either as a
#: bound removed a true finding when this rule first shipped.
VOCABULARY_HINTS = (
    "choice", "valid", "allowed", "supported", "types", "kinds", "levels",
    "codes", "vocab", "languages", "statuses", "states", "plans", "platforms",
    "roles", "options",
)


def _is_vocabulary(node: ast.AST) -> bool:
    """Is this expression a fixed set of admissible values?"""
    if isinstance(node, (ast.SetComp, ast.ListComp, ast.GeneratorExp, ast.DictComp)):
        return True
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return bool(node.elts) and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) and e.value
            for e in node.elts
        )
    name = _called_name(node).lower()
    return bool(name) and any(hint in name for hint in VOCABULARY_HINTS)


def _vocabulary_guarded(func: ast.AST) -> list:
    """Names this frame tests for MEMBERSHIP of a fixed set.

    ``if state not in {s for s, _ in STATE_CHOICES}: return 400`` /
    ``if image_type not in valid_types: raise`` — after that test the value is
    one of a known vocabulary, so its length is the vocabulary's, not the
    caller's. This is the shape a careful endpoint uses instead of
    ``choice_or_default``, and reading it as unbounded reported
    ``stapel-gdpr``'s DSAR state, ``stapel-profiles``' contact policy and
    ``stapel-cdn``'s image type, each of which validates one line above the
    write.

    The right-hand side must not itself be payload-derived — ``if kind not in
    payload`` tests the PAYLOAD for a key and says nothing about ``kind``.
    That check is done by the caller, which knows the payload names.
    """
    out: list = []
    for node in _body_statements(func):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], (ast.In, ast.NotIn)):
            continue
        if not isinstance(node.left, ast.Name):
            continue
        if not _is_vocabulary(node.comparators[0]):
            continue
        out.append((node.left.id, node.comparators[0]))
    return out


def _bindings_of(func: ast.AST) -> list:
    """``(names, value, is_iteration)`` for every name this frame binds.

    Assignments, ``for`` targets and ``with ... as`` names, because a payload
    is as often walked (``for item in request.data["events"]``) as it is
    assigned, and a rule that only read ``=`` would be silent on the loop that
    writes a row per element.
    """
    out: list = []
    for node in _body_statements(func):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            if node.value is not None:
                names = _assigned_names(node)
                if names:
                    out.append((names, node.value, False))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            names = _assigned_names(node)
            if names:
                out.append((names, node.iter, True))
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            names = [node.optional_vars.id] if isinstance(node.optional_vars, ast.Name) else []
            if names:
                out.append((names, node.context_expr, False))
    return out


class Scope:
    """One function's local knowledge: which names hold payload, which names
    hold something provably bounded, and which names hold a model instance.

    Per-function and non-recursive, which is the honest limit of the whole
    gate — see the module docstring.
    """

    def __init__(self, func: ast.AST, table: FieldTable, fitters: frozenset,
                 owner_model: Optional[str] = None):
        self.table = table
        self.fitters = fitters
        self.payload_names: set = _payload_parameters(func)
        #: name -> where the payload in it came INTO this frame, so a finding
        #: names `request.data`, not the local it was parked in.
        self.origins: dict = {}
        self.bounded_names: set = set()
        self.instances: dict = {}
        if owner_model is not None:
            self.instances["self"] = owner_model
        self._bindings = _bindings_of(func)
        self._guarded = _vocabulary_guarded(func)
        self._settle()

    # -- payload -------------------------------------------------------

    def _settle(self) -> None:
        """Propagate payload-ness and bounded-ness over the function's
        assignments to a fixed point. Order-insensitive on purpose: a linter
        that depended on statement order would read a loop wrongly, and being
        generous here can only ever make a finding MORE likely to be real
        (the name really does hold payload somewhere in this frame)."""
        for _pass in range(4):
            before = (len(self.payload_names), len(self.bounded_names),
                      len(self.instances))
            for name, vocabulary in self._guarded:
                if self.payload_source(vocabulary) is None:
                    self.bounded_names.add(name)
            for names, value, iteration in self._bindings:
                source = self.payload_source(value)
                if source:
                    self.payload_names.update(names)
                    root = source.split(" via ")[0]
                    for name in names:
                        self.origins.setdefault(name, root)
                if not iteration and self.bound_in(value) is not None:
                    self.bounded_names.update(names)
                # `for issue in Issue.objects.filter(...)` binds a ROW; an
                # assignment binds one only from a call that returns one.
                model = (self.model_of(value) if iteration
                         else self.instance_model(value))
                if model is not None:
                    for name in names:
                        self.instances[name] = model
            after = (len(self.payload_names), len(self.bounded_names),
                     len(self.instances))
            if before == after:
                break

    def payload_source(self, node: ast.AST) -> Optional[str]:
        """The payload source at the ROOT of this expression, or None.

        The chain is descended, never the whole subtree: ``fit(Issue, "title",
        payload["title"])`` has a payload deep inside it and is exactly the
        REPAIRED form, so a rule that read any payload anywhere in the
        expression would report the fix.
        """
        current = node
        seen = 0
        while current is not None and seen < 64:
            seen += 1
            if isinstance(current, ast.Attribute):
                if current.attr in REQUEST_ATTRS and \
                        REQUEST_HINT in _root_name(current.value).lower():
                    return f"request.{current.attr}"
                if current.attr == "validated_data":
                    return "serializer.validated_data"
                current = current.value
            elif isinstance(current, ast.Subscript):
                current = current.value
            elif isinstance(current, ast.Call):
                name = _called_name(current)
                if name in ("loads", "load") and _root_name(current.func) == "json":
                    return "json.loads(...)"
                if name in PASSTHROUGH_CALLS and current.args:
                    current = current.args[0]
                else:
                    current = current.func
            elif isinstance(current, ast.Name):
                if current.id not in self.payload_names:
                    return None
                origin = self.origins.get(current.id)
                return f"{origin} via `{current.id}`" if origin else f"`{current.id}`"
            elif isinstance(current, ast.Dict):
                for value in current.values:
                    found = self.payload_source(value)
                    if found is not None:
                        return found
                return None
            elif isinstance(current, ast.BoolOp):
                for value in current.values:
                    found = self.payload_source(value)
                    if found is not None:
                        return found
                return None
            elif isinstance(current, ast.IfExp):
                return (self.payload_source(current.body)
                        or self.payload_source(current.orelse))
            else:
                return None
        return None

    def has_payload_in_scope(self) -> bool:
        return bool(self.payload_names)

    # -- bounds --------------------------------------------------------

    def bound_in(self, node: ast.AST) -> Optional[str]:
        """The boundary this expression carries, or None.

        Read over the WHOLE subtree, which is the mirror image of
        :meth:`payload_source`: a bound anywhere between the payload and the
        column is a bound.
        """
        for child in ast.walk(node):
            if isinstance(child, ast.Subscript) and isinstance(child.slice, ast.Slice):
                if child.slice.upper is not None:
                    return f"a slice ({_source(child.slice)})"
            if isinstance(child, ast.Call):
                name = _called_name(child)
                if name in self.fitters or name.startswith("truncate"):
                    return f"a fitter ({name})"
                if name in CAST_CALLS:
                    return f"a cast ({name})"
            if isinstance(child, ast.Name) and child.id in self.bounded_names:
                return f"`{child.id}`, bounded where it is assigned"
        return None

    def top_bound(self, node: ast.AST) -> Optional[str]:
        """The boundary applied to the WHOLE value, or None.

        BND003's reading of "bounded", and deliberately not
        :meth:`bound_in`'s. A bound somewhere INSIDE an expression says
        nothing about the length of what the expression returns: the write
        that answered 500 was ``title=norm.title_for(trace, message=message)``
        in a function whose first line is ``trace = (trace or "")[:MAX_TRACE]``
        — a 60000-character slice, on the argument, of a helper whose result
        goes into a 255 column. Reading that slice as the title's bound is how
        a rule comes to be green on the defect it was written for.
        """
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            if node.slice.upper is not None:
                return f"a slice ({_source(node.slice)})"
            return None
        if isinstance(node, ast.Name):
            return (f"`{node.id}`, bounded where it is assigned"
                    if node.id in self.bounded_names else None)
        if isinstance(node, ast.BoolOp):
            found = [self.top_bound(v) for v in node.values]
            return found[0] if all(found) else None
        if isinstance(node, ast.IfExp):
            left, right = self.top_bound(node.body), self.top_bound(node.orelse)
            return left if left and right else None
        if isinstance(node, ast.Call):
            name = _called_name(node)
            if name in self.fitters or name.startswith("truncate"):
                return f"a fitter ({name})"
            if name in CAST_CALLS:
                return f"a cast ({name})"
            if name in PASSTHROUGH_CALLS and node.args:
                return self.top_bound(node.args[0])
            if name in STRING_METHODS and isinstance(node.func, ast.Attribute):
                # `.lower()`, `.strip()` — the length is the receiver's.
                return self.top_bound(node.func.value)
        return None

    def constant_safe(self, node: ast.AST, spec: FieldSpec) -> bool:
        """BND003's "obviously constant, or bounded by construction"."""
        folded = _folded_string(node)
        if folded is not None:
            return len(folded) <= spec.max_length
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.Call) and _called_name(node) in PASSTHROUGH_CALLS \
                and node.args:
            # `str(x)` is exactly as knowable as `x`, and a bare name is never
            # reported — so neither is a cast of one.
            return self.constant_safe(node.args[0], spec)
        if isinstance(node, ast.Name):
            # A bare name is never reported: see the module docstring.
            return True
        if _attribute_chain(node):
            # A pure dotted chain: an enum member, a module constant, or a
            # value read off another instance's field.
            return True
        if isinstance(node, ast.Call):
            name = _called_name(node).lower()
            if any(hint in name for hint in DIGEST_HINTS):
                return True
            if name in ("slugify", "get_random_string") and (node.args or node.keywords):
                return True
        if isinstance(node, (ast.BoolOp,)):
            return all(self.constant_safe(v, spec) for v in node.values)
        if isinstance(node, ast.IfExp):
            return (self.constant_safe(node.body, spec)
                    and self.constant_safe(node.orelse, spec))
        if isinstance(node, ast.JoinedStr):
            # A string built out of pieces is exactly as knowable as its
            # pieces: `f"totp:{uuid4().hex}"` and `f"{signal}-{index}"` are
            # not findings, `f"{exc}: {describe(trace)}"` still is.
            return all(
                self.constant_safe(part.value, spec)
                for part in node.values if isinstance(part, ast.FormattedValue)
            )
        if isinstance(node, ast.BinOp):
            return (self.constant_safe(node.left, spec)
                    and self.constant_safe(node.right, spec))
        if isinstance(node, ast.Call):
            return False
        # Everything else — a subscript of a local mapping, a comprehension, a
        # starred name — is left alone. BND003 reports only the three shapes
        # it can describe.
        return True

    # -- models --------------------------------------------------------

    def model_of(self, node: ast.AST) -> Optional[str]:
        """The model a WRITE call writes, or the model an expression
        instantiates — ``Issue(...)``, ``Issue.objects.create(...)``,
        ``Issue.objects.filter(...).first()``."""
        if not isinstance(node, ast.Call):
            return None
        chain = _attribute_chain(node.func)
        if chain:
            if len(chain) == 1 and chain[0] in self.table:
                return chain[0]  # Issue(...)
            if chain[0] in self.table and "objects" in chain:
                return chain[0]  # Issue.objects.<anything>(...)
            return None
        # Issue.objects.filter(...).first() — a call on a call.
        root = _root_name(node.func)
        return root if root in self.table else None

    def instance_model(self, node: ast.AST) -> Optional[str]:
        """The model of the ROW this expression hands back, or None."""
        model = self.model_of(node)
        if model is None:
            return None
        chain = _attribute_chain(node.func)
        method = chain[-1] if chain else _called_name(node)
        if method == model or method in INSTANCE_METHODS:
            return model
        return None

    def write_target(self, node: ast.Call) -> Optional[tuple]:
        """``(model, method)`` when this call WRITES a row, else None."""
        model = self.model_of(node)
        if model is None:
            return None
        chain = _attribute_chain(node.func)
        method = chain[-1] if chain else _called_name(node)
        if method == model:
            return model, "()"  # the constructor
        if method in WRITE_METHODS:
            return model, method
        return None


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------


def is_test_module(path: Path) -> bool:
    """A module of the suite, where a fabricated string is not untrusted text.

    BND003 is silent here (BND001/BND002 are not: a test that splats a real
    request payload is reporting the same defect as production code). The
    fleet's suites fabricate columns by the thousand — ``file_hash="a" * 64``,
    ``name=f"Root {index}"`` — and each one was written to fit. On the day
    this rule shipped they were 140 of its 196 findings, which is the whole
    difference between a tier people read and a tier people skim past.
    """
    name = path.name
    return (
        "tests" in path.parts
        or name.startswith("test_")
        or name == "conftest.py"
        or name.endswith("_test.py")
    )


def _folded_string(node: ast.AST) -> Optional[str]:
    """The string this expression evaluates to, when it is built only out of
    literals — ``"a" * 64``, ``"product/" + "f" * 64 + "/x.jpg"``.

    Test suites are full of these and every one of them was written to fit;
    reading them as "not provably bounded" is reporting the fixture.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = [_folded_string(v) for v in node.values]
        return "".join(parts) if all(p is not None for p in parts) else None
    if isinstance(node, ast.BinOp):
        left, right = node.left, node.right
        if isinstance(node.op, ast.Add):
            a, b = _folded_string(left), _folded_string(right)
            return a + b if a is not None and b is not None else None
        if isinstance(node.op, ast.Mult):
            for text_node, count_node in ((left, right), (right, left)):
                text = _folded_string(text_node)
                if text is None or not isinstance(count_node, ast.Constant):
                    continue
                count = count_node.value
                # A bounded repeat only: `"x" * n` with a huge literal is not
                # worth building a string for to measure it.
                if isinstance(count, int) and not isinstance(count, bool) \
                        and 0 <= count <= 100000:
                    return text * count
    return None


def _reads_same_named_field(node: ast.AST, field: str) -> bool:
    """``title=series.title`` — a value read off another instance's field of
    the SAME name.

    A column cannot overflow the column it was read out of, and the fleet
    copies rows constantly (an occurrence materialised from its series, a
    revision from its document). Read as "unbounded payload", every one of
    those is a finding nobody can act on.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr == field:
            return True
    return False


def _fields_note(fields: dict) -> str:
    listed = sorted(fields.values(), key=lambda s: (s.max_length, s.name))
    shown = ", ".join(f"{s.name}({s.max_length})" for s in listed[:5])
    more = f" (+{len(listed) - 5} more)" if len(listed) > 5 else ""
    return shown + more


def _remedy(spec: FieldSpec) -> str:
    if spec.has_choices:
        return (
            f"`{spec.name}` is a choices vocabulary, so the remedy is NOT a cut: "
            f"bounds.choice_or_default(<Model>, \"{spec.name}\", value) falls back "
            f"to the column's own default, because a value that is not in the "
            f"vocabulary is not a long value, it is a wrong one"
        )
    return (
        f"pass it through bounds.fit(<Model>, \"{spec.name}\", value), which reads "
        f"the limit off the field and MARKS the cut — a hardcoded [:{spec.max_length}] "
        f"here would go stale the day max_length changes, silently"
    )


def _check_splat(call: ast.Call, scope: Scope, path: Path, lines: list) -> list:
    """BND001 over one call."""
    target = scope.write_target(call)
    if target is None:
        return []
    model, method = target
    fields = scope.table.get(model) or {}
    if not fields:
        return []

    candidates: list = []
    for keyword in call.keywords:
        if keyword.arg is None:  # Model(**d)
            candidates.append((keyword.value, "**"))
        elif keyword.arg == "defaults" and method in DEFAULTS_METHODS \
                and not isinstance(keyword.value, ast.Dict):
            candidates.append((keyword.value, "defaults="))

    out: list = []
    for value, shape in candidates:
        source = scope.payload_source(value)
        if source is None:
            continue
        if scope.bound_in(value) is not None:
            continue
        if escape.any_line_suppressed(lines, "BND001", *_lines_of(call)):
            continue
        written = f"{model}(**…)" if method == "()" else f"{model}.objects.{method}(…)"
        out.append(Violation(
            str(path), call.lineno, "BND001",
            f"{written} splats {shape}{_source(value)} — payload-derived "
            f"({source}) — into a model with bounded columns: "
            f"{_fields_note(fields)}. Every key in that dict lands in a column "
            f"unchecked, and Django does not enforce max_length on a write: "
            f"Postgres raises StringDataRightTruncation, the transaction rolls "
            f"back and the endpoint answers 500. Name the fields you mean and "
            f"fit each bounded one (stapel-alerts/bounds.py: `no value from a "
            f"payload reaches a bounded column unbounded`, with the bound read "
            f"off the field itself)",
        ))
    return out


def _check_keyword_writes(call: ast.Call, scope: Scope, path: Path, lines: list) -> list:
    """BND002 over the keyword arguments of one model write, and over any
    ``defaults={...}`` literal it carries."""
    target = scope.write_target(call)
    if target is None:
        return []
    model, method = target
    fields = scope.table.get(model) or {}
    if not fields:
        return []

    pairs: list = []
    for keyword in call.keywords:
        if keyword.arg is None:
            continue
        if keyword.arg == "defaults" and method in DEFAULTS_METHODS \
                and isinstance(keyword.value, ast.Dict):
            for key, value in zip(keyword.value.keys, keyword.value.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    pairs.append((key.value, value, "defaults="))
            continue
        pairs.append((keyword.arg, keyword.value, ""))

    out: list = []
    for name, value, where in pairs:
        spec = fields.get(name)
        if spec is None:
            continue
        source = scope.payload_source(value)
        if source is None:
            continue
        if scope.bound_in(value) is not None:
            continue
        if _reads_same_named_field(value, name):
            continue
        if escape.any_line_suppressed(lines, "BND002", *(_lines_of(value) + [call.lineno])):
            continue
        out.append(Violation(
            str(path), value.lineno, "BND002",
            f"{model}.{name} is CharField(max_length={spec.max_length}) and "
            f"{where}{name}={_source(value)} puts payload in it unbounded "
            f"(root: {source}). Django validates max_length in forms, never on "
            f"a write, so a longer string reaches Postgres as "
            f"StringDataRightTruncation: the transaction rolls back and the "
            f"caller gets a 500 — {_remedy(spec)}",
        ))
    return out


def _check_attribute_assignment(node: ast.AST, scope: Scope, path: Path,
                                lines: list) -> list:
    """BND002 over ``obj.field = <payload>``."""
    targets = getattr(node, "targets", None)
    if targets is None:
        single = getattr(node, "target", None)
        targets = [single] if single is not None else []
    value = node.value
    if value is None:
        return []

    out: list = []
    for target in targets:
        if not isinstance(target, ast.Attribute):
            continue
        model = scope.instances.get(_root_name(target.value)) if \
            isinstance(target.value, (ast.Name, ast.Attribute)) else None
        if model is None:
            # The receiver's model is not inferable in this frame. Reporting
            # on the field NAME alone would grade every `self.title = ...` in
            # the estate against somebody else's column.
            continue
        spec = (scope.table.get(model) or {}).get(target.attr)
        if spec is None:
            continue
        source = scope.payload_source(value)
        if source is None or scope.bound_in(value) is not None:
            continue
        if _reads_same_named_field(value, target.attr):
            continue
        if escape.any_line_suppressed(lines, "BND002", *_lines_of(node)):
            continue
        out.append(Violation(
            str(path), node.lineno, "BND002",
            f"{model}.{target.attr} is CharField(max_length={spec.max_length}) "
            f"and `{_source(target)} = {_source(value)}` puts payload in it "
            f"unbounded (root: {source}). The row is written by the next "
            f"save(), where Postgres raises StringDataRightTruncation rather "
            f"than truncating — {_remedy(spec)}",
        ))
    return out


def _check_inverse_burden(call: ast.Call, scope: Scope, path: Path, lines: list,
                          anywhere: bool = not BND003_REQUIRES_PAYLOAD_IN_SCOPE) -> list:
    """BND003 over the keyword arguments of one model write."""
    if not anywhere and not scope.has_payload_in_scope():
        return []
    target = scope.write_target(call)
    if target is None:
        return []
    model, _method = target
    fields = scope.table.get(model) or {}

    out: list = []
    for keyword in call.keywords:
        if keyword.arg is None:
            continue
        spec = fields.get(keyword.arg)
        if spec is None:
            continue
        value = keyword.value
        if scope.payload_source(value) is not None:
            continue  # BND002 owns this one
        if scope.top_bound(value) is not None:
            continue
        if scope.constant_safe(value, spec):
            continue
        if _reads_same_named_field(value, keyword.arg):
            continue
        if escape.any_line_suppressed(lines, "BND003", *(_lines_of(value) + [call.lineno])):
            continue
        out.append(Violation(
            str(path), value.lineno, "BND003",
            f"{model}.{keyword.arg} is CharField(max_length={spec.max_length}) "
            f"and {keyword.arg}={_source(value)} is neither a bounded "
            f"expression nor a constant. Nothing here proves the string fits "
            f"and the column does not enforce it, so what that value's length "
            f"turns out to be is decided somewhere else — and a longer one is "
            f"a 500 out of the write, not a truncation. This is the shape that "
            f"answered 500 on 2026-09-15: a helper's result, in a frame where "
            f"the payload it came from is no longer visible — {_remedy(spec)}",
            level="warning",
        ))
    return out


# ---------------------------------------------------------------------------
# lint driver
# ---------------------------------------------------------------------------


def lint_source_file(path: Path, table: FieldTable,
                     fitters: Optional[frozenset] = None,
                     anywhere: bool = not BND003_REQUIRES_PAYLOAD_IN_SCOPE) -> list:
    """Every rule over one module, against an already-built field table."""
    tree = _parse(path)
    if tree is None or not table:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):  # pragma: no cover - _parse read it
        return []
    fitters = fitters or FITTERS
    suite = is_test_module(path)

    #: method -> the model class it is defined on, so `self.title = ...` in a
    #: model's own method resolves.
    owners: dict = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name not in table:
            continue
        for statement in ast.walk(node):
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owners[id(statement)] = node.name

    scopes: list = [(tree, Scope(tree, table, fitters))]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append((node, Scope(node, table, fitters, owners.get(id(node)))))

    violations: list = []
    for func, scope in scopes:
        for node in _body_statements(func):
            if isinstance(node, ast.Call):
                violations.extend(_check_splat(node, scope, path, lines))
                violations.extend(_check_keyword_writes(node, scope, path, lines))
                if not suite:
                    violations.extend(
                        _check_inverse_burden(node, scope, path, lines, anywhere))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                violations.extend(_check_attribute_assignment(node, scope, path, lines))
    return violations


def lint_library(library: Path, fitters: Optional[frozenset] = None,
                 anywhere: bool = not BND003_REQUIRES_PAYLOAD_IN_SCOPE) -> list:
    table = build_field_table(library)
    if not table:
        return []
    violations: list = []
    for path in python_files(library):
        violations.extend(lint_source_file(path, table, fitters, anywhere))
    violations.sort(key=lambda v: (v.path, v.line, v.rule))
    return violations


def lint_project(project: Path, notes: Optional[list] = None,
                 fitters: Optional[frozenset] = None,
                 anywhere: bool = not BND003_REQUIRES_PAYLOAD_IN_SCOPE) -> list:
    violations: list = []
    libraries = find_libraries(Path(project))
    for library in libraries:
        violations.extend(lint_library(library, fitters, anywhere))
    if notes is not None and not libraries:
        notes.append("no models.py in this tree — bounds rules are silent")
    violations.sort(key=lambda v: (v.path, v.line, v.rule))
    return violations


def lint_paths(paths: Iterable, fitters: Optional[frozenset] = None,
               anywhere: bool = not BND003_REQUIRES_PAYLOAD_IN_SCOPE) -> list:
    violations: list = []
    for raw in paths:
        root = Path(raw)
        if not root.exists():
            raise SystemExit(f"Error: path does not exist: {root}")
        violations.extend(lint_project(root, fitters=fitters, anywhere=anywhere))
    violations.sort(key=lambda v: (v.path, v.line, v.rule))
    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-bounds-lint",
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
        help="Promote warnings to errors — BND003's inverse burden included.",
    )
    parser.add_argument(
        "--payload-in-frame", action="store_true",
        help="BND003 only where a payload source is visible in the same "
             "function. Quieter, and blind to a write whose caller unpacked "
             "the payload into ordinary parameters — which is the shape the "
             "incident this gate was built for actually had.",
    )
    parser.add_argument(
        "--fitter", action="append", default=[], metavar="NAME",
        help="An additional function name that BOUNDS a value (repeatable). "
             "A project with its own truncation helper names it here rather "
             "than annotating every call site.",
    )
    args = parser.parse_args(argv)

    fitters = FITTERS | frozenset(args.fitter)
    violations = lint_paths(
        args.paths, fitters=fitters, anywhere=not args.payload_in_frame
    )
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
