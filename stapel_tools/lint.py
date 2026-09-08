"""
Stapel static linter.

Checks project-specific coding rules that standard linters don't cover.
Exit code: 0 = clean, 1 = violations found.

Rules
-----
R001  return Response(…) — bare Response() in views; use StapelResponse or StapelErrorResponse
R002  raise serializers.ValidationError — use StapelValidationError in client serializers
R003  @action without @extend_schema / @extend_schema_view entry — document all actions
R004  @dataclass in dto.py without docstring — OpenAPI docs are driven by the docstring
R005  StapelErrorResponse(status, 'literal') — use an ERR_* constant, not a raw string
R006  StapelResponse({…}) — passing a dict literal skips serializer; use StapelResponse(MySerializer(dto))
R007  @extend_schema view method without @flow_step — every endpoint must belong to a documented flow
R008  get_or_create/update_or_create with a lifecycle or security flag in defaults= — WARNING
R009  call('llm.transcribe'|...) — synchronous call to a long operation; make it a task (comm.start)
R010  Cyrillic in a comment, a docstring or an identifier — source is English-only
R011  One word carrying both Latin and Cyrillic letters — a homoglyph
R012  handle_exception() branching on a rest_framework.exceptions type — that
      refusal belongs to the fleet's EXCEPTION_HANDLER, not to one view
R100  README must link both language docs when i18n artifacts exist (i18n-shipping.md §4) — WARNING

Levels
------
Most rules are errors (exit 1). R008 and R100 are warnings (printed,
non-blocking): R100's i18n doc-link convention is rolling out (W→E after the
sweep, i18n-shipping.md §4); R008 stays a warning permanently, because seeding
a flag only at creation is sometimes exactly right and an error-level rule on a
legitimate idiom gets silenced wholesale.

Suppression
-----------
Add "# noqa: R001" (or the relevant rule ID) at the end of the offending line to silence it.
Add "# noqa" to silence all rules on that line.

R012 carries a second, method-scoped escape on top of that one:
"# stapel: owns-refusal" anywhere in the handle_exception body declares that
this view really does own the refusal it converts — write the reason next to
it.
"""

import argparse
import ast
import io
import os
import re
import sys
import tokenize
from collections import Counter
from dataclasses import dataclass
from typing import Iterator

from . import escape

SKIP_DIRS = {
    "migrations",
    "__pycache__",
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "htmlcov",
    "build",
    "dist",
    ".claude",
    "worktrees",
}
SKIP_SUFFIXES = {".pyc", ".pyo"}


@dataclass
class Violation:
    path: str
    line: int
    rule: str
    message: str
    level: str = "error"  # "error" (exit 1) | "warning" (printed, non-blocking)

    def __str__(self) -> str:
        tag = self.rule if self.level == "error" else f"{self.rule} warning"
        return f"{self.path}:{self.line}: [{tag}] {self.message}"


def _decorator_names(decorator_list: list) -> list[str]:
    names = []
    for d in decorator_list:
        if isinstance(d, ast.Name):
            names.append(d.id)
        elif isinstance(d, ast.Attribute):
            names.append(d.attr)
        elif isinstance(d, ast.Call):
            if isinstance(d.func, ast.Name):
                names.append(d.func.id)
            elif isinstance(d.func, ast.Attribute):
                names.append(d.func.attr)
    return names


def _extend_schema_view_keys(class_node: ast.ClassDef) -> set[str]:
    keys: set[str] = set()
    for d in class_node.decorator_list:
        if not isinstance(d, ast.Call):
            continue
        func = d.func
        name = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute)
            else ""
        )
        if name != "extend_schema_view":
            continue
        for kw in d.keywords:
            if kw.arg:
                keys.add(kw.arg)
    return keys


def _noqa(lines: list[str], lineno: int, rule: str) -> bool:
    """The shared ``# noqa: RULE`` grammar (``stapel_tools.escape``), applied
    to one already-split line list. See that module for the grammar itself.
    """
    if lineno < 1 or lineno > len(lines):
        return False
    return escape.line_suppressed(lines[lineno - 1], rule)


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------


def check_r001(tree: ast.Module, lines: list[str], path: str) -> Iterator[Violation]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return):
            continue
        val = node.value
        if not isinstance(val, ast.Call):
            continue
        func = val.func
        if isinstance(func, ast.Name):
            func_name = func.id
        elif isinstance(func, ast.Attribute):
            func_name = func.attr
        else:
            continue
        if func_name != "Response":
            continue
        if not _noqa(lines, node.lineno, "R001"):
            yield Violation(
                path, node.lineno, "R001",
                "return Response(…) — use StapelResponse for success or "
                "StapelErrorResponse for errors; never bare Response",
            )


def check_r002(tree: ast.Module, lines: list[str], path: str) -> Iterator[Violation]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise):
            continue
        exc = node.exc
        if exc is None:
            continue
        call_or_attr = exc if isinstance(exc, (ast.Attribute, ast.Call)) else None
        if call_or_attr is None:
            continue
        attr_node = exc.func if isinstance(exc, ast.Call) else exc
        if not isinstance(attr_node, ast.Attribute):
            continue
        if attr_node.attr != "ValidationError":
            continue
        if not isinstance(attr_node.value, ast.Name):
            continue
        if attr_node.value.id != "serializers":
            continue
        if not _noqa(lines, node.lineno, "R002"):
            yield Violation(
                path, node.lineno, "R002",
                "raise serializers.ValidationError — use StapelValidationError "
                "with a registered ERR_* key in client-facing serializers",
            )


def check_r003(tree: ast.Module, lines: list[str], path: str) -> Iterator[Violation]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        esv_keys = _extend_schema_view_keys(node)
        for item in node.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            deco_names = _decorator_names(item.decorator_list)
            if "action" not in deco_names:
                continue
            has_schema = "extend_schema" in deco_names or item.name in esv_keys
            if not has_schema:
                if not _noqa(lines, item.lineno, "R003"):
                    yield Violation(
                        path, item.lineno, "R003",
                        f"{node.name}.{item.name}: @action without @extend_schema "
                        f"or @extend_schema_view entry",
                    )


def check_r004(tree: ast.Module, lines: list[str], path: str) -> Iterator[Violation]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        deco_names = _decorator_names(node.decorator_list)
        if "dataclass" not in deco_names:
            continue
        first = node.body[0] if node.body else None
        has_doc = (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        )
        if not has_doc:
            if not _noqa(lines, node.lineno, "R004"):
                yield Violation(
                    path, node.lineno, "R004",
                    f"@dataclass {node.name} has no docstring "
                    f"(docstring drives OpenAPI schema descriptions)",
                )


def check_r005(tree: ast.Module, lines: list[str], path: str) -> Iterator[Violation]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute)
            else ""
        )
        if name != "StapelErrorResponse":
            continue
        if len(node.args) < 2:
            continue
        second_arg = node.args[1]
        if isinstance(second_arg, ast.Constant) and isinstance(second_arg.value, str):
            if not _noqa(lines, node.lineno, "R005"):
                yield Violation(
                    path, node.lineno, "R005",
                    f'StapelErrorResponse with hardcoded string "{second_arg.value}" '
                    f"— define an ERR_* constant",
                )


def check_r006(tree: ast.Module, lines: list[str], path: str) -> Iterator[Violation]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute)
            else ""
        )
        if name != "StapelResponse":
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if isinstance(first_arg, ast.Dict):
            if not _noqa(lines, node.lineno, "R006"):
                yield Violation(
                    path, node.lineno, "R006",
                    "StapelResponse({…}) passes a raw dict — "
                    "use StapelResponse(MySerializer(dto)) for documented schemas",
                )


# ---------------------------------------------------------------------------
# File routing: which rules apply to which files
# ---------------------------------------------------------------------------


def check_r007(tree: ast.Module, lines: list[str], path: str) -> Iterator[Violation]:
    """Every documented endpoint participates in at least one flow.

    A method counts as an endpoint when it is an http verb handler or an
    @action, and is schema-documented (extend_schema on the method or an
    extend_schema_view entry). The flow attachment may live on the method
    or on the class (class-level @flow_step covers all its methods).
    """
    http_verbs = {"get", "post", "put", "patch", "delete"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        class_has_flow = "flow_step" in _decorator_names(node.decorator_list)
        esv_keys = _extend_schema_view_keys(node)
        for item in node.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            deco_names = _decorator_names(item.decorator_list)
            is_endpoint = item.name in http_verbs or "action" in deco_names
            if not is_endpoint:
                continue
            documented = "extend_schema" in deco_names or item.name in esv_keys
            if not documented:
                continue  # R003 handles undocumented actions
            has_flow = class_has_flow or "flow_step" in deco_names
            if not has_flow:
                if not _noqa(lines, item.lineno, "R007"):
                    yield Violation(
                        path, item.lineno, "R007",
                        f"{node.name}.{item.name}: documented endpoint without "
                        f"@flow_step — attach it to a flow "
                        f"(see stapel_core.flows) or add '# noqa: R007'",
                    )


# ---------------------------------------------------------------------------
# R008 — lifecycle/security flag in get_or_create(defaults=…)
# ---------------------------------------------------------------------------

#: Flags whose value decides whether an account/resource may be used at all.
_R008_FLAGS = frozenset({"is_active", "is_verified", "is_staff"})
#: …plus the "<something>_required" family (admit_required, mfa_required, …).
_R008_SUFFIX = "_required"


def _r008_keys(dict_node: ast.Dict) -> list[str]:
    hits = []
    for key in dict_node.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            if key.value in _R008_FLAGS or key.value.endswith(_R008_SUFFIX):
                hits.append(key.value)
    return sorted(hits)


def check_r008(tree: ast.Module, lines: list[str], path: str) -> Iterator[Violation]:
    """A lifecycle/security flag passed in ``defaults=`` decides nothing for a
    row that already exists.

    ``get_or_create(..., defaults={"is_active": True})`` reads as "this object
    is active" and is not: on the *get* branch the dict is never touched, so
    the caller silently accepts whatever the stored row says — an object
    deactivated by an admin, a revoked verification, a flag another service
    flipped. ``update_or_create`` has the mirror problem: the dict IS applied
    to the found row, so the same line silently re-activates it.

    **Warning, never an error.** The pattern is often exactly right — a flag
    that genuinely only seeds the initial state — and a rule that fails builds
    on a legitimate idiom gets silenced wholesale, which costs more than it
    saves.

    The canon this points at is where the invariant belongs: on the **point of
    use**, once, not on every creation site. stapel-auth gates issuing a
    session on the account's live state, so a deactivated user cannot get in
    no matter which of the dozen ``get_or_create`` calls first created the
    row. Gate at the door; ``defaults`` is a seed, not a guarantee.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr if isinstance(func, ast.Attribute)
            else func.id if isinstance(func, ast.Name)
            else ""
        )
        if name not in ("get_or_create", "update_or_create"):
            continue
        for kw in node.keywords:
            if kw.arg != "defaults" or not isinstance(kw.value, ast.Dict):
                continue
            hits = _r008_keys(kw.value)
            if not hits or _noqa(lines, node.lineno, "R008"):
                continue
            flags = ", ".join(repr(h) for h in hits)
            if name == "get_or_create":
                detail = (
                    "defaults is applied ONLY when the object is created — on "
                    "the get branch the caller silently accepts whatever the "
                    "stored row says (deactivated, unverified, revoked)"
                )
            else:
                detail = (
                    "update_or_create applies defaults to the row it FOUND too "
                    "— this silently rewrites the flag on an existing object"
                )
            yield Violation(
                path, node.lineno, "R008",
                f"{name}(defaults=…) carries the lifecycle/security flag(s) "
                f"{flags} — {detail}. Check the flag on "
                f"the returned object, or gate the invariant at the point of "
                f"use (canon: stapel-auth gates session issuance on the live "
                f"account state, once, instead of in every creation site). "
                f"Legitimate seeding: '# noqa: R008'",
                level="warning",
            )


# ---------------------------------------------------------------------------
# R100 — repo-level: README links both language docs when i18n artifacts exist
# ---------------------------------------------------------------------------


#: Operations that run for MINUTES, or longer still if queued. A synchronous
#: ``call()`` can't work for them: the caller must name a wait deadline up
#: front, and the queue doesn't know one.
#:
#: Listed explicitly rather than matched by name heuristic: "long-running" is
#: a property of the operation known to its author, and guessing from a
#: substring would false-positive on llm.embed or llm.rerank, which finish in
#: seconds.
LONG_RUNNING_OPERATIONS = frozenset({
    "llm.transcribe",
    "llm.summarize",
    "llm.diarize",
    "llm.generate_image",
})


def check_r009(tree: ast.Module, lines: list[str], path: str) -> Iterator[Violation]:
    """A long-running operation must not be called through synchronous ``call()``.

    Caught live (2026-08-08): ``call("llm.transcribe", payload)`` with no
    ``timeout`` took the 5s ``FUNCTION_TIMEOUT`` default, but a real
    transcription takes 14s — every real call timed out, retried three
    times, and errored two and a half hours later.

    A ``timeout=`` argument only postpones the same problem: under a busy
    queue, the wait is unbounded and no number is a safe guess. The fix is a
    different primitive — ``stapel_core.comm.start()`` returns a task_id
    immediately, state is observable via ``status()``, completion arrives as
    a ``task.completed`` Action.

    A deliberate exception (script, one-off utility, test) is suppressed with
    ``# noqa: R009``.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "call" or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        if first.value not in LONG_RUNNING_OPERATIONS:
            continue
        if _noqa(lines, node.lineno, "R009"):
            continue
        yield Violation(
            path, node.lineno, "R009",
            f"call({first.value!r}) — synchronous call to a long-running "
            f"operation. Its response can't be waited on: the deadline is "
            f"unguessable behind a queue. Make it a task instead — "
            f"stapel_core.comm.start({first.value!r}, "
            f"payload, correlation_id=...) — and follow up via task.completed",
        )


def _error_doc_langs(docs_dir: str) -> list[str]:
    langs = []
    prefix, suffix = "errors.", ".md"
    try:
        for name in os.listdir(docs_dir):
            if name.startswith(prefix) and name.endswith(suffix):
                lang = name[len(prefix):-len(suffix)]
                if lang:
                    langs.append(lang)
    except OSError:
        pass
    return sorted(langs)


def _flow_doc_langs(flows_dir: str) -> list[str]:
    langs = []
    try:
        for name in os.listdir(flows_dir):
            sub = os.path.join(flows_dir, name)
            if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, "README.md")):
                langs.append(name)
    except OSError:
        pass
    return sorted(langs)


def check_readme_i18n_links(root: str) -> list[Violation]:
    """Every present i18n artifact block must be linked in README, in each language.

    i18n-shipping.md §4: if ``docs/flows/`` exists the README links each flow-doc
    language (en + ru at minimum); if ``docs/errors.json`` or any
    ``docs/errors.<lang>.md`` exists the README links each error-reference
    language. Emitted at WARNING level (the convention is rolling out).
    """
    readme_path = os.path.join(root, "README.md")
    if not os.path.isfile(readme_path):
        return []
    try:
        text = open(readme_path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError):
        return []

    docs = os.path.join(root, "docs")
    violations: list[Violation] = []

    flows_dir = os.path.join(docs, "flows")
    if os.path.isdir(flows_dir):
        langs = sorted(set(_flow_doc_langs(flows_dir)) | {"en", "ru"})
        for lang in langs:
            needle = f"docs/flows/{lang}/"
            if needle not in text:
                violations.append(Violation(
                    readme_path, 1, "R100",
                    f"README does not link the {lang} flow docs "
                    f"({needle}README.md) — link every language (i18n-shipping.md §4)",
                    level="warning",
                ))

    errors_json = os.path.isfile(os.path.join(docs, "errors.json"))
    error_langs = _error_doc_langs(docs)
    if errors_json or error_langs:
        for lang in sorted(set(error_langs) | {"en", "ru"}):
            needle = f"docs/errors.{lang}.md"
            if needle not in text:
                violations.append(Violation(
                    readme_path, 1, "R100",
                    f"README does not link the {lang} error reference "
                    f"({needle}) — link both languages (i18n-shipping.md §4)",
                    level="warning",
                ))
    return violations



# ---------------------------------------------------------------------------
# R010 / R011 — the source is English
# ---------------------------------------------------------------------------
#
# Owner ruling, 2026-08-09: identifiers, comments, docstrings, log messages and
# commit messages are English, in the OSS libraries and the private products
# alike. Russian that is CONTENT stays: i18n catalogues, e-mail templates, UI
# copy, prompts, and fixtures whose Cyrillic is the thing under test.
#
# That distinction is why R010 ignores plain string literals, and why it needs
# no per-path allowlist: prose and names have no legitimate reason to be
# Russian, data does. A rule without an allowlist is one nobody learns to
# silence wholesale.

CYRILLIC = re.compile(r"[\u0400-\u04FF]")

#: A word carrying BOTH scripts. ``\w`` is unicode-aware, so this matches
#: ``miттudei`` but never a Cyrillic word standing next to a Latin one.  # noqa: R010,R011
MIXED_SCRIPT_WORD = re.compile(r"\b(?=\w*[a-zA-Z])(?=\w*[\u0400-\u04FF])\w+\b")

#: Escape sequences, stripped before the homoglyph scan. On raw source text the
#: ``n`` of ``"\nУточняющий"`` and the ``b`` of ``r"\bготово"`` attach to the  # noqa: R010
#: Cyrillic that follows and read as one mixed word. Three of the first four
#: hits across the fleet were exactly this, and none of them were defects.
ESCAPE_SEQ = re.compile(r"\\.")

#: Below this length a mixed-script hit is a regex character class, not a word:
#: ``[a-zА-Я]`` puts ``z`` directly against ``А``.  # noqa: R010
MIN_HOMOGLYPH_WORD = 4

_DOC_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
_NAME_FIELDS = ("name", "id", "arg", "attr", "asname")


def _comment_tokens(lines: list[str]) -> Iterator[tuple[int, str]]:
    """Comment tokens only — a ``#`` inside a string literal is not a comment."""
    src = "".join(ln if ln.endswith("\n") else ln + "\n" for ln in lines)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                yield tok.start[0], tok.string
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return


def _docstring_node(node):
    body = getattr(node, "body", None)
    if not body:
        return None
    first = body[0]
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        return first.value
    return None


def _first_cyrillic_line(doc_node) -> int:
    """The line the Cyrillic actually sits on, not the line the block opens on.

    Reporting a module docstring at line 1 produces a violation that can never
    be suppressed: line 1 is inside the string, so a trailing ``# noqa`` there
    would be text rather than a directive.
    """
    start = getattr(doc_node, "lineno", 1)
    for offset, line in enumerate(doc_node.value.splitlines()):
        if CYRILLIC.search(line):
            return start + offset
    return start


def check_r010(tree: ast.Module, lines: list[str], path: str) -> Iterator[Violation]:
    """E: Cyrillic in a comment, a docstring or an identifier."""
    for lineno, text in _comment_tokens(lines):
        if CYRILLIC.search(text) and not _noqa(lines, lineno, "R010"):
            yield Violation(path, lineno, "R010", "comment is not in English")

    for node in ast.walk(tree):
        if isinstance(node, _DOC_OWNERS):
            doc = _docstring_node(node)
            if doc is not None and CYRILLIC.search(doc.value):
                first = _first_cyrillic_line(doc)
                end = getattr(doc, "end_lineno", first)
                if not (_noqa(lines, first, "R010") or _noqa(lines, end, "R010")):
                    yield Violation(path, first, "R010", "docstring is not in English")
        for field in _NAME_FIELDS:
            value = getattr(node, field, None)
            if isinstance(value, str) and CYRILLIC.search(value):
                line = getattr(node, "lineno", 1)
                if not _noqa(lines, line, "R010"):
                    yield Violation(
                        path, line, "R010",
                        f"identifier {value!r} is not in English",
                    )


def check_r011(tree: ast.Module, lines: list[str], path: str) -> Iterator[Violation]:
    """E: one word, two alphabets — a homoglyph.

    ``miттudei`` reads as Latin, greps as neither, and survives review because  # noqa: R010,R011
    the eye cannot tell the two т apart. Unlike R010 this also looks inside
    string literals: no legitimate text mixes scripts mid-word.
    """
    for lineno, raw in enumerate(lines, start=1):
        line = ESCAPE_SEQ.sub(" ", raw)
        for word in MIXED_SCRIPT_WORD.findall(line):
            if len(word) < MIN_HOMOGLYPH_WORD:
                continue
            if _noqa(lines, lineno, "R011"):
                continue
            yield Violation(
                path, lineno, "R011",
                f"{word!r} mixes Latin and Cyrillic letters in one word",
            )


# ---------------------------------------------------------------------------
# R012 — a view intercepting a refusal the fleet's exception handler owns
# ---------------------------------------------------------------------------
#
# stapel-core 0.61.0 made twelve DRF refusal types answer the fleet envelope
# through one seam, REST_FRAMEWORK["EXCEPTION_HANDLER"]. A view that overrides
# handle_exception and converts one of those types itself takes the refusal
# away from that seam: the envelope, the localizable key and the params never
# happen for it, and the two answers drift. stapel-cdn 0.20.0 was exactly
# that — DescribeMediaView converted Throttled in the view and answered
# `wait + 1`, one second more than the Retry-After header on the same
# response, because DRF had already rounded the wait up once.
#
# The legitimate case is the module's OWN exception type: stapel-workspaces'
# BillingSeamMixin turns its `entitlements.BillingUnavailable` into a 503 for
# every method of the view. Nothing else in the process knows that type, so
# nothing else can answer it — the rule never fires there, because the rule
# only ever looks at names that resolve to `rest_framework.exceptions`.

#: The method whose override takes a refusal out of the fleet handler's hands.
R012_METHOD = "handle_exception"

#: Method-scoped declaration for the view that genuinely owns a DRF refusal
#: (AUTHZ007's `# stapel: strict-authenticator` grammar). Write the reason.
R012_MARKER = "stapel: owns-refusal"

#: Modules whose members ARE the fleet handler's business. `serializers` is
#: here because `rest_framework.serializers.ValidationError` IS
#: `rest_framework.exceptions.ValidationError` — the same class under the
#: name most view code reaches it by.
_R012_EXC_MODULES = {
    "rest_framework.exceptions": None,
    "rest_framework.serializers": frozenset({"ValidationError"}),
}


def _r012_drf_names(tree: ast.Module) -> tuple[dict, dict]:
    """Module-level bindings that name a ``rest_framework`` exception.

    Returns ``(symbols, modules)`` — local name → dotted exception, and local
    name → the module it stands for (``rest_framework`` itself included, so a
    fully qualified ``rest_framework.exceptions.Throttled`` resolves too).
    """
    symbols: dict[str, str] = {}
    modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local = alias.asname or alias.name
                if module in _R012_EXC_MODULES:
                    allowed = _R012_EXC_MODULES[module]
                    if allowed is None or alias.name in allowed:
                        symbols[local] = f"{module}.{alias.name}"
                elif module == "rest_framework":
                    full = f"rest_framework.{alias.name}"
                    if full in _R012_EXC_MODULES:
                        modules[local] = full
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name != "rest_framework" and not alias.name.startswith(
                    "rest_framework."
                ):
                    continue
                if alias.asname:
                    modules[alias.asname] = alias.name
                else:
                    # `import a.b` binds `a`, not `a.b`.
                    root = alias.name.split(".", 1)[0]
                    modules[root] = root
    return symbols, modules


def _r012_resolve(node: ast.AST, symbols: dict, modules: dict):
    """The dotted DRF exception *node* names, or ``None``."""
    if isinstance(node, ast.Name):
        return symbols.get(node.id)
    if not isinstance(node, ast.Attribute):
        return None
    owner = _r012_dotted(node.value)
    if owner is None:
        return None
    root, _, rest = owner.partition(".")
    base = modules.get(root)
    if base is None:
        return None
    module = f"{base}.{rest}" if rest else base
    if module not in _R012_EXC_MODULES:
        return None
    allowed = _R012_EXC_MODULES[module]
    if allowed is not None and node.attr not in allowed:
        return None
    return f"{module}.{node.attr}"


def _r012_dotted(node: ast.AST):
    """``a.b.c`` as a string, for a pure Name/Attribute chain."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _r012_dotted(node.value)
        return None if owner is None else f"{owner}.{node.attr}"
    return None


def _r012_branch_subtrees(method: ast.AST):
    """The parts of a method body that TEST a value, with the line to report.

    Converting an exception is not the finding; *deciding on its type* is.
    Raising a DRF exception, or naming one in a type annotation, leaves the
    refusal with the fleet handler and is none of this rule's business.
    """
    for node in ast.walk(method):
        if isinstance(node, ast.Call):
            name = (
                node.func.id if isinstance(node.func, ast.Name)
                else getattr(node.func, "attr", "")
            )
            if name in ("isinstance", "issubclass") and len(node.args) > 1:
                yield node.args[1], node.lineno
        elif isinstance(node, ast.ExceptHandler) and node.type is not None:
            yield node.type, node.lineno
        elif isinstance(node, (ast.If, ast.IfExp)):
            yield node.test, node.lineno
        elif isinstance(node, ast.Compare):
            yield node, node.lineno
        elif isinstance(node, ast.match_case):
            yield node.pattern, node.pattern.lineno


def check_r012(tree: ast.Module, lines: list[str], path: str) -> Iterator[Violation]:
    """A ``handle_exception`` override that branches on a DRF exception type.

    The refusal types stapel-core's ``stapel_exception_handler`` answers — 401,
    403, 404, 405, 406, 415, 429, the 400 of an unparseable body — are the ones
    no view raises: they come from authenticators, permission classes,
    dispatch, throttles. A view that intercepts one of them converts it with
    none of the handler's registry behind it, so that endpoint answers a shape
    (and, in the live case, a number) the rest of the fleet does not.

    A module converting its OWN exception type is untouched — that type is
    unknown to every other layer, so nothing but this view can answer it.
    """
    symbols, modules = _r012_drf_names(tree)
    if not symbols and not modules:
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name != R012_METHOD:
                continue
            if _r012_declared(lines, item):
                continue
            reported: set = set()
            for subtree, lineno in _r012_branch_subtrees(item):
                for inner in ast.walk(subtree):
                    exception = _r012_resolve(inner, symbols, modules)
                    if exception is None:
                        continue
                    short = exception.rsplit(".", 1)[1]
                    if (lineno, short) in reported:
                        continue
                    reported.add((lineno, short))
                    if _noqa(lines, lineno, "R012"):
                        continue
                    yield Violation(
                        path, lineno, "R012",
                        f"{node.name}.{R012_METHOD} branches on {short} "
                        f"({exception}) — that refusal belongs to the fleet's "
                        f"EXCEPTION_HANDLER (stapel_core.django.api.errors."
                        f"stapel_exception_handler), which answers it with the "
                        f"localizable envelope and the headers DRF set. "
                        f"Converting it here gives this one endpoint a "
                        f"different body, and a second computation of the same "
                        f"numbers. Let it raise through. Converting the "
                        f"module's OWN exception type here is fine and is not "
                        f"reported; if this view genuinely owns the DRF "
                        f"refusal, say so with '# {R012_MARKER}' in the method "
                        f"(and the reason next to it)",
                    )


def _r012_declared(lines: list[str], method: ast.AST) -> bool:
    """Is ``# stapel: owns-refusal`` written inside the method?"""
    start = getattr(method, "lineno", 1)
    end = getattr(method, "end_lineno", start) or start
    for lineno in range(start, min(end, len(lines)) + 1):
        if R012_MARKER in lines[lineno - 1]:
            return True
    return False


def rules_for_file(path: str):
    basename = os.path.basename(path)
    is_view = "views" in basename
    is_serializer = "serializer" in basename
    is_dto = basename == "dto.py"

    checkers = []
    if is_view:
        checkers += [check_r001, check_r003, check_r005, check_r006, check_r007]
    if is_serializer and "admin" not in path:
        checkers += [check_r002]
    if is_dto:
        checkers += [check_r004]
    if not is_view:
        checkers += [check_r005]
    # R008 is not about a layer: get_or_create lives in services, consumers,
    # actions, management commands and views alike.
    checkers += [check_r008]
    # R009 too: a long-running operation can be called from anywhere, and
    # it was a pipeline stage that called it when this broke in production.
    checkers += [check_r009]
    # R010/R011 are about the language of the source, so they apply to
    # every file regardless of layer.
    checkers += [check_r010, check_r011]
    # R012 is not routed by layer either: handle_exception is overridden on a
    # view, but just as often on a mixin that lives in mixins.py/base.py — the
    # stapel-workspaces case is `BillingSeamMixin` in views.py and the
    # stapel-cdn one was a view class in the same file, and a rule that only
    # read views.py would miss the first module that extracts its mixin.
    checkers += [check_r012]
    return checkers


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def scan_file(path: str, checkers=None) -> list[Violation]:
    try:
        src = open(path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError:
        return []

    lines = src.splitlines()
    violations: list[Violation] = []
    for checker in (checkers if checkers is not None else rules_for_file(path)):
        violations.extend(checker(tree, lines, path))
    violations.sort(key=lambda v: v.line)
    return violations


def scan_paths(roots: list[str]) -> list[Violation]:
    all_violations: list[Violation] = []
    for root in roots:
        if os.path.isfile(root):
            if root.endswith(".py"):
                all_violations.extend(scan_file(root))
            continue
        # Repo-level rules (README ↔ i18n artifacts) run once per directory root.
        all_violations.extend(check_readme_i18n_links(root))
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRS and not d.endswith(".egg-info")
            ]
            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                if fname.endswith(tuple(SKIP_SUFFIXES)):
                    continue
                fpath = os.path.join(dirpath, fname)
                if fname.startswith("test_") or fname == "tests.py":
                    # Layer rules (views/serializers/DTO) do not apply to tests,
                    # but the language rules do — and tests were where Russian
                    # names were thickest: whole classes and test methods were
                    # spelled in Cyrillic, and pytest prints those names. A
                    # language rule blind to tests would miss its main target.
                    all_violations.extend(
                        scan_file(fpath, checkers=[check_r010, check_r011])
                    )
                    continue
                all_violations.extend(scan_file(fpath))
    all_violations.sort(key=lambda v: (v.path, v.line))
    return all_violations


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "paths", nargs="*", default=["."],
        help="Files or directories to scan (default: current directory)",
    )
    parser.add_argument(
        "--rules", metavar="R001,R002",
        help="Comma-separated list of rules to enable (default: all)",
    )
    parser.add_argument(
        "--ignore", metavar="R001,R002",
        help="Comma-separated list of rules to skip",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Print a summary count per rule at the end",
    )
    args = parser.parse_args()

    enabled = set(args.rules.split(",")) if args.rules else None
    ignored = set(args.ignore.split(",")) if args.ignore else set()

    violations = scan_paths(args.paths)
    if enabled:
        violations = [v for v in violations if v.rule in enabled]
    if ignored:
        violations = [v for v in violations if v.rule not in ignored]

    for v in violations:
        print(v)

    if args.stats and violations:
        counts = Counter(v.rule for v in violations)
        print()
        for rule, count in sorted(counts.items()):
            print(f"  {rule}: {count} violation{'s' if count > 1 else ''}")

    errors = [v for v in violations if v.level == "error"]
    warnings = [v for v in violations if v.level != "error"]
    if violations:
        parts = []
        if errors:
            parts.append(f"{len(errors)} error{'s' if len(errors) > 1 else ''}")
        if warnings:
            parts.append(f"{len(warnings)} warning{'s' if len(warnings) > 1 else ''}")
        print(f"\n{', '.join(parts)} found.")
    else:
        print("No violations found.")
    # Warnings are printed but never fail the build (R100 rolls out W→E).
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
