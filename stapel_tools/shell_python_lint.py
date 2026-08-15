"""Python that a shell script feeds to an interpreter, checked like source.

Motivating incident: ``iron-auth/bootstrap.sh`` ran a heredoc that began
``from common.django.openid import ...`` — a module deleted at the stapel
migration. It failed on every single boot for months and nobody knew, because
the script did not stop on error and the payload's output went nowhere anyone
looked. The import has been fixed; nothing prevented the next one.

Is this statically decidable?
-----------------------------
"Are this payload's imports resolvable" is NOT decidable in general.
``sys.path`` is assembled at runtime, the interpreter may be a different one
from the linter's, and third-party distributions live in an environment no
static tool can see. A rule that tried would either miss most cases or drown a
repo in false positives on every ``import requests``.

What IS decidable is the shape the incident actually had, and it is the common
one: **a FIRST-PARTY import.** If the payload's import starts with a top-level
name that exists as a package or module in this repository, then this
repository is the authority on whether the rest of the dotted path exists —
and ``common`` was such a name right up to the day the directory was deleted.
So:

* SH001 — an embedded payload imports ``a.b.c`` where ``a`` is a package in
  this repo and ``a.b.c`` is not. Third-party and stdlib imports are not
  examined at all, which is why this rule has no false-positive story.
* SH002 — an embedded payload runs in a script that neither stops on error nor
  handles the failure. This is the half that made the first one invisible for
  months: a payload can fail every boot and the script goes on to start the
  server.

SH002 accepts EITHER discipline: a ``set -e``-family flag, or the per-step
classification a generated project uses (``require``/``optional`` verbs — a
blanket ``set -e`` makes cosmetic steps fatal, which is its own defect), or an
explicit ``|| ...`` / ``if``-guard on the invocation itself. What it refuses is
a bare invocation whose exit status nothing reads.

Exit code: 0 = clean, 1 = violations found.

Suppression: ``# noqa: SH001`` on the offending line, or ``# stapel-lint:
ignore`` anywhere in the shell script.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SHELL_SUFFIXES = {".sh", ".bash", ".zsh"}

SKIP_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
    "build",
    "dist",
    "htmlcov",
    "migrations",
    ".worktrees",
}

#: ``python``/``python3``/``py`` with any path prefix, plus the two shapes a
#: Django project uses to run a payload (``manage.py shell``, ``django-admin
#: shell``). ``-`` and ``-c`` are the two ways a payload arrives.
_PY_INVOCATION = re.compile(
    r"(?:^|[\s;&|(`]|\$\()"
    r"(?:(?:[\w./${}\-]*/)?(?:python[\d.]*|py)"
    r"|(?:[\w./${}\-]*/)?manage\.py\s+shell\w*"
    r"|django-admin\s+shell\w*)"
)

#: ``<<`` or ``<<-`` followed by a delimiter, quoted or not.
_HEREDOC = re.compile(r"<<-?\s*(?P<quote>['\"]?)(?P<delim>[A-Za-z_]\w*)(?P=quote)")

#: ``-c 'payload'`` / ``-c "payload"``.
_DASH_C = re.compile(r"-c\s+(?P<quote>['\"])(?P<body>.*?)(?<!\\)(?P=quote)", re.DOTALL)

_SET_E = re.compile(r"^\s*set\s+-[a-zA-Z]*e", re.MULTILINE)
#: The per-step classification a generated project uses instead of ``set -e``.
_STEP_VERB = re.compile(r"^\s*(?:require|optional)\s")


@dataclass
class Violation:
    path: str
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.message}"


@dataclass
class Payload:
    """A block of Python text a shell script hands to an interpreter."""

    body: str
    line: int  # 1-based line of the invocation in the shell script
    body_line: int  # 1-based line the payload's first line sits on
    guarded: bool  # the invocation's exit status is read


# ---------------------------------------------------------------------------
# first-party module index
# ---------------------------------------------------------------------------


def _candidate_dirs(root: Path):
    yield root
    for path in root.rglob("*"):
        if path.is_dir() and not any(
            part in SKIP_DIRS for part in path.relative_to(root).parts
        ):
            yield path


def first_party_index(root: Path) -> dict[str, list[Path]]:
    """``{top-level name: [directories it is importable from]}``.

    A directory is an import root if it holds a package (``__init__.py``) or a
    bare module. Both layouts a repo uses are covered without configuration:
    the repo root itself, ``src/``, and each service directory — a service's
    ``bootstrap.sh`` runs with its own directory as the working directory, so
    ``common`` next to it is importable exactly as the payload writes it.
    """
    index: dict[str, list[Path]] = {}
    for path in _candidate_dirs(root):
        try:
            children = list(path.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and (child / "__init__.py").is_file():
                name = child.name
            elif child.is_file() and child.suffix == ".py" and child.stem.isidentifier():
                name = child.stem
            else:
                continue
            bases = index.setdefault(name, [])
            if path not in bases:
                bases.append(path)
    return index


def _module_exists(base: Path, dotted: str) -> bool:
    target = base.joinpath(*dotted.split("."))
    return (target / "__init__.py").is_file() or target.with_suffix(".py").is_file()


def resolves(dotted: str, index: dict[str, list[Path]]) -> bool | None:
    """True / False / ``None`` = not first-party, so not this rule's business."""
    bases = index.get(dotted.split(".", 1)[0])
    if not bases:
        return None
    return any(_module_exists(base, dotted) for base in bases)


# ---------------------------------------------------------------------------
# payload extraction
# ---------------------------------------------------------------------------


def _is_guarded(line: str, has_set_e: bool) -> bool:
    stripped = line.strip()
    if has_set_e or _STEP_VERB.match(line):
        return True
    if "||" in stripped or "&&" in stripped:
        return True
    if stripped.startswith(("if ", "if(", "while ", "until ", "!", "exec ")):
        # ``exec`` replaces the shell, so the payload's status IS the script's.
        return True
    if stripped.endswith("&"):
        # A backgrounded long-running process (a dev server). Its status is not
        # readable at this point by construction, so demanding one would push
        # authors toward a worse script rather than a better one — and this
        # rule is about PREPARATION steps that fail silently, not about
        # servers.
        return True
    return bool(re.search(r"\bexit\b", stripped) and "$?" in stripped)


def extract_payloads(text: str) -> list[Payload]:
    lines = text.splitlines()
    has_set_e = bool(_SET_E.search(text))
    payloads: list[Payload] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("#") or not _PY_INVOCATION.search(line):
            i += 1
            continue
        guarded = _is_guarded(line, has_set_e)
        here = _HEREDOC.search(line)
        if here:
            delim = here.group("delim")
            body: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j].strip() != delim:
                body.append(lines[j])
                j += 1
            payloads.append(Payload("\n".join(body), i + 1, i + 2, guarded))
            i = j + 1
            continue
        dash_c = _DASH_C.search(line)
        # An invocation with no payload still carries the SH002 question.
        body_text = dash_c.group("body") if dash_c else ""
        payloads.append(Payload(body_text, i + 1, i + 1, guarded))
        i += 1
    return payloads


def _imported_modules(body: str) -> list[tuple[str, int]]:
    """``(dotted module path, line offset)`` for every absolute import.

    Parsed, not grepped: a string containing the word "import" is not one, and
    a payload that does not parse has a different problem than this rule's.
    Relative imports are skipped — a heredoc has no package context, so their
    resolution is not a question this rule can answer.
    """
    try:
        tree = ast.parse(body)
    except SyntaxError:
        return []
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.module, node.lineno))
    return found


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------


def lint_script(path: Path, root: Path, index: dict[str, list[Path]]) -> list[Violation]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if "# stapel-lint: ignore" in text:
        return []
    lines = text.splitlines()
    rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    violations: list[Violation] = []
    for payload in extract_payloads(text):
        for dotted, offset in _imported_modules(payload.body):
            line_no = payload.body_line + offset - 1
            source = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
            if "noqa" in source:
                continue
            if resolves(dotted, index) is False:
                violations.append(Violation(
                    rel, line_no, "SH001",
                    f"embedded Python imports '{dotted}', and "
                    f"'{dotted.split('.')[0]}' is a package in this repository "
                    "that has no such module — the payload raises ImportError "
                    "on every run",
                ))
        if not payload.guarded:
            source = lines[payload.line - 1] if 0 < payload.line <= len(lines) else ""
            if "noqa" in source:
                continue
            violations.append(Violation(
                rel, payload.line, "SH002",
                "the exit status of this Python invocation is read by nothing: "
                "no set -e, no require/optional step verb, no '|| ...' — a "
                "payload that fails every run leaves no trace and the script "
                "continues",
            ))
    return violations


def lint_tree(root: Path) -> list[Violation]:
    root = Path(root).resolve()
    index = first_party_index(root)
    violations: list[Violation] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SHELL_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        violations.extend(lint_script(path, root, index))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-shell-python-lint",
        description="Python payloads embedded in shell scripts, linted like source.",
    )
    parser.add_argument("path", nargs="?", default=".", help="repository root")
    parser.add_argument("--rules", metavar="SH001,SH002", help="only report these rules")
    args = parser.parse_args(argv)

    wanted = set(args.rules.split(",")) if args.rules else None
    violations = [
        v for v in lint_tree(Path(args.path))
        if wanted is None or v.rule in wanted
    ]
    for violation in violations:
        print(violation, file=sys.stderr)
    if violations:
        print(f"\n{len(violations)} violation(s) found.", file=sys.stderr)
        return 1
    print("shell-python-lint: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
