"""
stapel-url-lint — bare Django ``URLField()`` gate (docs/reference/library-standard.md
§3.8), in the ``stapel-migration-lint`` / ``stapel-config-lint`` idiom (rule codes,
``--json``, ``--strict``, exit 1 on any error).

Why this exists
----------------
``django.db.models.URLField`` defaults ``max_length`` to 200 when not given
explicitly. Real-world external URLs routinely exceed that: OAuth provider
avatar URLs (Google/GitHub), SAML IdP SSO/SLO endpoints, OIDC discovery
documents — all observed over 200 chars in production. A bare ``URLField()``
backing a column that receives such a value degrades from "wrong length" to
"``StringDataRightTruncation`` on INSERT" — a 500, not a validation error,
because Postgres enforces the column width the ORM's implicit default chose
for you. (Incident: OAuth signup 500s on a Google avatar URL > 200 chars;
fixed in stapel-core users_user.avatar 200→500 + stapel-auth SSOConfig
saml_sso_url/saml_slo_url/oidc_discovery_url 200→500.)

Rule
----
URL001  (error) ``models.URLField(...)`` (or a bare ``URLField(...)`` bound to
        ``django.db.models``/``django.db.models.fields`` via
        ``from django.db.models import URLField``) with no explicit
        ``max_length`` keyword → the implicit 200-char default is almost
        certainly wrong. Fields carrying external/untrusted URLs (avatar,
        provider/webhook/media/callback, anything from a third party) need
        ``max_length=500`` or wider; internal/short config URLs still need an
        *explicit* max_length (even 200) — a bare call is never acceptable so
        the width was a deliberate choice, not an accident. Suppress a
        deliberate exception with ``# noqa: URL001`` on the field's line.

Scope and exclusions
---------------------
Only the Django ORM field is in scope. ``rest_framework.serializers.URLField``
(and other DRF field classes) are a different class entirely — ``CharField``
with no implicit ``max_length`` at all, no backing DB column, so the
truncation bug this rule guards against cannot occur there. Call sites
qualified by a name resolved (via this file's imports) to
``rest_framework.serializers`` / ``rest_framework.fields`` are never flagged.
Detection is import-alias based (like config-lint's settings-module check),
not full type inference — a ``URLField(`` bound to an unrecognized qualifier
defaults to being treated as the Django ORM field (the overwhelmingly common
case in this codebase), erring toward flagging rather than silently passing.

Migrations are skipped: a migration's ``AlterField``/``CreateModel`` field
literal is a copy of the model source `makemigrations` already saw, so
flagging the model file once is enough; flagging the migration too would be a
duplicate finding for the same fix.

Exit codes: 0 clean, 1 errors present (``--strict`` kept for idiom parity —
all findings here are already errors), 2 usage/environment errors.
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

SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".hg",
    ".tox",
    ".venv",
    "venv",
    "node_modules",
    "htmlcov",
    "build",
    "dist",
    ".claude",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "worktrees",
    "site-packages",
    "migrations",
}

#: dotted origins that make it a non-ORM field this rule must NOT flag
_EXCLUDED_ORIGINS = {
    "rest_framework.serializers",
    "rest_framework.fields",
    "rest_framework",
}


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


# ---------------------------------------------------------------------------
# file discovery
# ---------------------------------------------------------------------------


def _walk_py(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")
        )
        for fname in sorted(filenames):
            if fname.endswith(".py"):
                yield Path(dirpath) / fname


# ---------------------------------------------------------------------------
# import-alias resolution
# ---------------------------------------------------------------------------


def _name_origins(tree: ast.Module) -> dict:
    """local name -> dotted "module.attr" origin, for both
    ``from a.b import c [as d]`` (name -> "a.b.c") and ``from a.b import c``
    where c is itself a module attribute used as a qualifier
    (name -> "a.b" so ``name.URLField`` resolves against "a.b.URLField")."""
    origins: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bound = alias.asname or alias.name
                origins[bound] = f"{node.module}.{alias.name}"
    return origins


def _qualifier_origin(name: str, name_origins: dict) -> Optional[str]:
    """Dotted origin of a bare qualifier name, e.g. "models" -> "django.db.models"
    from ``from django.db import models``."""
    return name_origins.get(name)


def _resolve_call_origin(node: ast.Call, name_origins: dict) -> Optional[str]:
    """Best-effort dotted origin of a ``...URLField(...)`` call's target, or
    None if unresolvable."""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "URLField":
        if isinstance(func.value, ast.Name):
            qualifier = _qualifier_origin(func.value.id, name_origins)
            if qualifier:
                return f"{qualifier}.URLField"
            # Unrecognized qualifier (e.g. a local var, or "models" bound via
            # plain ``import django.db.models as models``, which _name_origins
            # doesn't cover) — treat as unknown, not excluded.
            return None
        return None
    if isinstance(func, ast.Name) and func.id == "URLField":
        origin = name_origins.get("URLField")
        return origin
    return None


def _is_excluded_origin(origin: Optional[str]) -> bool:
    if origin is None:
        return False
    return any(
        origin == f"{excluded}.URLField" or origin.startswith(f"{excluded}.")
        for excluded in _EXCLUDED_ORIGINS
    )


def _is_urlfield_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr == "URLField"
    if isinstance(func, ast.Name):
        return func.id == "URLField"
    return False


def _has_max_length(node: ast.Call) -> bool:
    return any(kw.arg == "max_length" for kw in node.keywords)


def _noqa_rules(line: str) -> Optional[set]:
    if "# noqa" not in line:
        return None
    if "# noqa:" not in line:
        return set()
    tail = line.split("# noqa:", 1)[1]
    return {r.strip() for r in tail.replace(";", ",").split(",") if r.strip()}


# ---------------------------------------------------------------------------
# lint driver
# ---------------------------------------------------------------------------


def lint_file(path: Path) -> list:
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    name_origins = _name_origins(tree)
    lines = src.splitlines()
    violations: list = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_urlfield_call(node):
            continue
        origin = _resolve_call_origin(node, name_origins)
        if _is_excluded_origin(origin):
            continue
        if _has_max_length(node):
            continue

        raw = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
        suppressed = _noqa_rules(raw)
        if suppressed is not None and (not suppressed or "URL001" in suppressed):
            continue

        violations.append(Violation(
            str(path), node.lineno, "URL001",
            "URLField() with no explicit max_length — Django's implicit "
            "default is varchar(200), which real external URLs (OAuth "
            "avatar, IdP SSO/OIDC discovery, webhooks) routinely exceed; "
            "set max_length=500+ for external/untrusted URLs, or an "
            "explicit width for internal/short ones. Suppress a deliberate "
            "exception with '# noqa: URL001'",
        ))
    return violations


def lint_project(project: Path) -> list:
    violations: list = []
    for py in _walk_py(project.resolve()):
        violations.extend(lint_file(py))
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
        prog="stapel-url-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths", nargs="*", default=["."],
        help="Project roots or module repos to lint (default: .)",
    )
    parser.add_argument("--json", action="store_true", help="Machine output")
    parser.add_argument(
        "--strict", action="store_true",
        help="(All url-lint findings are already errors; flag kept for idiom "
             "parity with the other stapel linters.)",
    )
    args = parser.parse_args(argv)

    violations = lint_paths(args.paths)
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
            print(f"\n{len(errors)} error(s) found.")
        else:
            print("No violations found.")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
