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
R009  call('llm.transcribe'|...) — синхронный вызов долгой операции; ставьте задачей (comm.start)
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
"""

import argparse
import ast
import os
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Iterator

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
    if lineno < 1 or lineno > len(lines):
        return False
    comment = lines[lineno - 1]
    if "# noqa" not in comment:
        return False
    if "# noqa:" not in comment:
        return True
    after = comment.split("# noqa:")[1]
    listed = [r.strip() for r in after.split(",")]
    return rule in listed


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


#: Операции, которые идут МИНУТАМИ, а при очереди — сколько угодно.
#: Синхронный ``call()`` для них негоден по построению: вызывающий обязан
#: назвать срок ожидания заранее, а очередь его не знает.
#:
#: Список именно перечислением, а не эвристикой по имени: «долгая» — это
#: свойство операции, известное её автору, и угадывать его по подстроке
#: значило бы ловить ложные срабатывания на llm.embed или llm.rerank,
#: которые как раз укладываются в секунды.
LONG_RUNNING_OPERATIONS = frozenset({
    "llm.transcribe",
    "llm.summarize",
    "llm.diarize",
    "llm.generate_image",
})


def check_r009(tree: ast.Module, lines: list[str], path: str) -> Iterator[Violation]:
    """Долгую операцию нельзя звать синхронным ``call()``.

    ЖИВОЙ ДЕФЕКТ (08.08.2026, стенд айронмемо). ``call("llm.transcribe",
    payload)`` без аргумента ``timeout`` брал ``FUNCTION_TIMEOUT`` — пять
    секунд по умолчанию. Настоящая расшифровка укладывается в 14 секунд,
    сводка в 36. То есть КАЖДАЯ настоящая запись падала по таймауту,
    ретраилась трижды и уходила в error спустя два с половиной часа, а
    человек всё это время смотрел на «обрабатывается».

    Явный ``timeout=`` дефект не закрывает, а отодвигает: если исполнители
    заняты, ждать придётся столько, сколько длится очередь, и никакое
    число здесь не угадать. Поэтому правило требует не «поставьте срок», а
    смены примитива — ``stapel_core.comm.start()``: задача возвращает
    task_id сразу, состояние наблюдаемо через ``status()``, завершение
    приходит Action'ом ``task.completed``.

    Осознанное исключение (скрипт, разовая утилита, тест) глушится
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
            f"call({first.value!r}) — синхронный вызов долгой операции. "
            f"Ждать её ответа нельзя: срок неугадываем, если перед вами "
            f"очередь. Ставьте задачей — stapel_core.comm.start({first.value!r}, "
            f"payload, correlation_id=...) — и досчитывайте по task.completed",
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
    # R009 — тем более: долгую операцию можно позвать откуда угодно, и
    # именно из стадии конвейера её и звали, когда всё падало.
    checkers += [check_r009]
    return checkers


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def scan_file(path: str) -> list[Violation]:
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
    for checker in rules_for_file(path):
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
                if fname.startswith("test_") or fname == "tests.py":
                    continue
                all_violations.extend(scan_file(os.path.join(dirpath, fname)))
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
