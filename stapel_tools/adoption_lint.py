"""
stapel-adoption-lint — mechanical honesty gate for stapel-module adoption
in a customer project (BACKLOG §26/§30/§32, §35).

A module can be "adopted" on paper without being adopted in fact: pinned in
``requirements`` and listed in ``INSTALLED_APPS`` but never mounted (so none of
its endpoints exist), or re-implemented locally as a hand-written route that
duplicates an operation the module already ships, or worse — the migration to
it is done in a branch that never reached ``main``. Those gaps pass every
per-module test yet leave the project a half-migration. This linter is the
mechanical gate that fails them, in the ``stapel-migration-lint`` idiom (rule
codes, ``--json``, ``--strict``, exit 1 on any error).

Rules
-----
ADO001  (error) A stapel module is *installed* (named in ``requirements*.txt``
        or in ``INSTALLED_APPS``) and *exposes a urlconf* (ships a ``urls``
        module), but its urls are NOT mounted in the project's ROOT_URLCONF —
        ``include("stapel_<mod>.urls")`` is absent. The module's endpoints
        simply do not exist in the running service. Deliberate headless use
        (the module is wanted only for its models/services/tasks, not its HTTP
        surface) is declared with a file-level marker comment
        ``# stapel: headless <mod>`` in the urlconf or a settings file; ``<mod>``
        may be the short name (``auth``) or the full package (``stapel_auth``).

ADO002  (error) A hand-written urlpattern the project owns duplicates an
        operation of an installed module: its route, normalized, equals a path
        the module publishes in its ``docs/schema.json`` (OpenAPI). Route
        parameters are normalized so ``<int:pk>`` and ``{id}`` compare equal.
        The finding names the shadowed module operation(s). Fix by deleting the
        local route and using the module's (or, if the behaviour must differ,
        an extension point) — do not fork the wire contract.

ADO003  (warning) The project has a ``STAPEL-MIGRATION.md`` that records
        *done* work, but the current git branch is neither ``main``/``master``
        nor merged into it. A finished migration living on an unmerged branch
        is the §32 failure mode (the work is "done" everywhere except where it
        ships). Git-only signal; no network.

ADO004  (warning) A ``requirements`` pin is never imported anywhere in the
        project — a dead pin (the canonical case: ``PyJWT``, correctly resolved
        to its ``jwt`` import name, left pinned after the code that used it was
        replaced). Import names are resolved through installed-distribution
        metadata (``top_level.txt`` / ``packages_distributions``) with a small
        alias table, so ``PyJWT``→``jwt``, ``PyYAML``→``yaml`` etc. are not
        false-flagged. stapel modules are exempt (they are referenced by
        dotted string in ``INSTALLED_APPS``/urlconf, never ``import``ed) and so
        is a small set of entry-point-only runtime/tooling packages (servers,
        DB drivers, test/lint tools) that are legitimately never imported.

ADO005  (error) A **gdpr data owner is installed but cannot answer**. A library
        that ships ``schemas/consumes/gdpr.erasure.requested.json`` has an
        erasure subscriber, and a subscriber is worth exactly what its
        delivery is worth. So for every such library named in
        ``INSTALLED_APPS``, two things must hold, and each is its own finding:

        1. **when the service's actions ride a broker** (``STAPEL_COMM
           ["ACTION_TRANSPORT"]`` is anything but ``inprocess``), its deploy
           runs a ``consume_actions`` process — the compose fragment for this
           service (``docker-compose*.yml`` for a monolith, ``<svc>.yml``
           next to ``services.conf`` in the fleet shape
           ``scripts/verify_boot_contract.sh`` walks). Without it the library
           is installed, migrated, healthy — and never delivered to, so every
           erasure request against it runs out its clock as a TIMEOUT on a
           silent owner. In-process delivery calls the handler in the
           emitting process and needs no consumer, so it is not asked for;
        2. the gdpr host (the service that installs ``stapel_gdpr``, this one
           or a sibling in the same fleet) lists the library's owner name in
           ``STAPEL_GDPR["DATA_OWNERS"]``, *with the subject types the library
           claims*. The owner name and subject types are read from the
           library's own ``erasure.py``/``gdpr.py``
           (``OWNER``/``SUBJECT_TYPES`` or ``GDPR_OWNER``/
           ``GDPR_SUBJECT_TYPES``; they differ per library —
           ``stapel-cdn`` answers to ``media``, ``stapel-profiles`` to
           ``profile``). An owner absent from the map gets no
           ``ErasurePart``, which means the orchestrator marks the request
           DELETED while that owner's rows are untouched — a receipt for work
           nobody did.

        Each half is skipped, with a stderr note, when its input is not
        discoverable (no compose file at all; no service installs
        ``stapel_gdpr``; a computed ``STAPEL_GDPR``) — unreadable is not the
        same as missing, and a gate must not invent findings.

Deliberate parsing limitations (what we do NOT try to catch)
------------------------------------------------------------
* **Mounts** are recognized only from ``include("<pkg>.urls")`` *string*
  literals and from ``include([... inline list ...])`` (recursed with the
  accumulated prefix). A module mounted through a *variable* or a
  computed/dynamic include is opaque to the AST and would false-positive
  ADO001 — the ``# stapel: headless`` marker is the escape hatch, or mount it
  with a literal string.
* **Custom routes** are gathered from the ROOT_URLCONF file(s) (the
  ``config/urls.py`` convention, resolved from each settings module's
  ``ROOT_URLCONF``) and any inline-list includes reachable from there; routes
  buried inside an app-level ``urls.py`` reached via a *string* include are not
  reconstructed for ADO002. ``re_path`` regexes are normalized best-effort
  (anchors stripped, groups → ``{}``).
* **Module schemas** for ADO002 are read from ``docs/schema.json`` located next
  to the installed package (``importlib`` spec — works for editable/dev-mode
  installs and the neighbour-repo workspace layout) or a sibling
  ``stapel-<mod>/docs/schema.json``. ``docs/`` is not shipped in a wheel, so
  when a module is a plain pip install with no discoverable schema, ADO002 is
  skipped for it (a stderr note, never a false error).
* **ADO004** only decides "dead" for a dist whose import name(s) we could
  resolve; an unresolvable, non-stapel, non-allowlisted dist is left alone.
* **ADO005** reads the deploy as text: any mention of ``consume_actions`` in
  a compose file that belongs to this service satisfies the consumer half.
  Which compose *service* carries it, and whether that service is actually
  started by the stand, is out of scope here (the delivery gates own that).
  Run against a fleet ROOT (the directory holding ``services.conf``) the
  check degrades to "somewhere in this fleet", because at that granularity
  the INSTALLED_APPS of every service are one union — lint the service
  directories to keep the finding per-service.

Exit codes: 0 clean (warnings allowed), 1 errors present (``--strict`` promotes
warnings to errors), 2 usage/environment errors.
"""

from __future__ import annotations

import argparse
import ast
import importlib.metadata as importlib_metadata
import importlib.util
import json
import re
import subprocess
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
}

#: dist canonical name -> import name, for pins whose import name is not the
#: dist name and that we may need to resolve even when they are NOT installed.
IMPORT_ALIASES = {
    "pyjwt": "jwt",
    "pyyaml": "yaml",
    "pillow": "PIL",
    "python_dateutil": "dateutil",
    "beautifulsoup4": "bs4",
    "psycopg2_binary": "psycopg2",
    "python_dotenv": "dotenv",
    "msgpack_python": "msgpack",
    "django_environ": "environ",
}

#: canonical dist names that are legitimately never ``import``ed — invoked
#: through a console entry point (servers, tooling) or referenced only by a
#: settings string (DB drivers). Exempt from ADO004 to keep the signal on the
#: PyJWT-style dead pin rather than on infrastructure.
RUNTIME_ONLY_DISTS = {
    "gunicorn",
    "uvicorn",
    "uwsgi",
    "gevent",
    "daphne",
    "hypercorn",
    "psycopg",
    "psycopg2",
    "psycopg2_binary",
    "mysqlclient",
    "pytest",
    "pytest_django",
    "pytest_cov",
    "pytest_asyncio",
    "ruff",
    "mypy",
    "black",
    "isort",
    "flake8",
    "coverage",
    "tox",
    "pip",
    "setuptools",
    "wheel",
    "build",
    "twine",
    "pre_commit",
}

_HEADLESS_RE = re.compile(r"#\s*stapel:\s*headless\s+([\w.-]+)")
_ROUTE_PARAM_DJANGO = re.compile(r"<[^>]+>")
_ROUTE_PARAM_OPENAPI = re.compile(r"\{[^}]+\}")
_DONE_RE = re.compile(r"\[[xX]\]|✅|\bdone\b|\bготово\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    message: str
    level: str = "error"  # "error" | "warning"

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
# name canonicalisation
# ---------------------------------------------------------------------------


def canon_dist(name: str) -> str:
    """PEP 503-ish canonical dist key: lowercase, runs of -/_/. → single _."""
    return re.sub(r"[-_.]+", "_", name.strip().lower())


def module_short(pkg: str) -> str:
    """``stapel_auth`` / ``stapel-auth`` → ``auth``."""
    c = canon_dist(pkg)
    return c[len("stapel_"):] if c.startswith("stapel_") else c


def is_stapel_module(name: str) -> bool:
    c = canon_dist(name)
    return c.startswith("stapel_") and c not in ("stapel_tools",)


# ---------------------------------------------------------------------------
# file discovery
# ---------------------------------------------------------------------------


def _walk_py(root: Path) -> Iterable[Path]:
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")
        )
        for fname in sorted(filenames):
            if fname.endswith(".py"):
                yield Path(dirpath) / fname


def find_requirements(project: Path) -> list[Path]:
    found: list[Path] = []
    for cand in sorted(project.glob("requirements*.txt")):
        found.append(cand)
    req_dir = project / "requirements"
    if req_dir.is_dir():
        found.extend(sorted(req_dir.glob("*.txt")))
    return found


def find_settings_files(project: Path) -> list[Path]:
    out: list[Path] = []
    for py in _walk_py(project):
        if py.parent.name == "settings" or py.name.startswith("settings"):
            out.append(py)
    # de-dup, stable
    seen: set = set()
    uniq = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


# ---------------------------------------------------------------------------
# requirements parsing
# ---------------------------------------------------------------------------


@dataclass
class Pin:
    dist: str          # raw dist name as written
    canon: str         # canonical key
    path: str
    line: int


def parse_requirements(paths: Iterable[Path]) -> list[Pin]:
    pins: list[Pin] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # drop an inline comment
            line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
            match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", line)
            if not match:
                continue
            dist = match.group(1)
            pins.append(Pin(dist, canon_dist(dist), str(path), lineno))
    return pins


# ---------------------------------------------------------------------------
# settings parsing — INSTALLED_APPS + ROOT_URLCONF
# ---------------------------------------------------------------------------


def _string_constants(node: ast.AST) -> list[str]:
    return [
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def collect_settings_strings(paths: Iterable[Path]) -> set[str]:
    """First dotted segment of every string constant in the settings files —
    the set of packages a Django project *configures by string* (INSTALLED_APPS
    entries, ``*_BACKEND``/``ENGINE`` targets, celery app paths, …). Such a
    package is used even though nothing ``import``s it in app code."""
    segments: set[str] = set()
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for value in _string_constants(tree):
            token = value.strip()
            if token:
                segments.add(token.split(".")[0])
    return segments


def parse_settings(paths: Iterable[Path]) -> tuple[set[str], set[str]]:
    """Return (installed_apps, root_urlconf_module_dotted_paths)."""
    installed: set[str] = set()
    root_urlconfs: set[str] = set()
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            names = {t.id for t in targets if isinstance(t, ast.Name)}
            value = node.value
            if value is None:
                continue
            if "INSTALLED_APPS" in names:
                installed.update(_string_constants(value))
            if "ROOT_URLCONF" in names and isinstance(value, ast.Constant):
                if isinstance(value.value, str):
                    root_urlconfs.add(value.value)
    return installed, root_urlconfs


# ---------------------------------------------------------------------------
# urlconf discovery + parsing
# ---------------------------------------------------------------------------


def discover_urlconfs(project: Path, root_urlconfs: set[str]) -> list[Path]:
    """Files that make up the project's ROOT_URLCONF(s)."""
    files: list[Path] = []
    for dotted in root_urlconfs:
        rel = dotted.replace(".", "/") + ".py"
        for py in _walk_py(project):
            if py.as_posix().endswith(rel):
                files.append(py)
    if not files:
        files = [p for p in project.glob("**/config/urls.py")]
    if not files:
        # last resort: a top-level urls.py next to a settings package
        files = [
            p
            for p in _walk_py(project)
            if p.name == "urls.py" and "site-packages" not in p.parts
        ]
    seen: set = set()
    uniq = []
    for p in sorted(files):
        if p not in seen and p.exists():
            seen.add(p)
            uniq.append(p)
    return uniq


@dataclass
class Route:
    route: str   # full path template (leading/trailing slashes preserved as written)
    line: int


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _fstring_to_route(node: ast.JoinedStr) -> str:
    """Best-effort literal rendering of an f-string route, e.g.
    ``f"{url_prefix}api/"`` -> ``"{}api/"``: each dynamic ``FormattedValue``
    becomes a ``"{}"`` placeholder (the same normalization ``re_path`` regex
    groups already get below), each literal string segment is kept verbatim.

    Without this, a route written as an f-string parses as neither a Constant
    nor anything ``_route_literal`` recognized, so ``_walk_patterns`` bailed
    out via its ``raw_route is None`` guard BEFORE ever looking at the
    ``include(...)`` target — silently dropping the mount from ADO001's
    ``mounts`` set even though the include is a plain string literal one
    argument over. This is exactly the shape stapel-tools' OWN generated
    ``config/urls.py`` uses for every stapel-module mount
    (``path(f"{url_prefix}api/", include("stapel_auth.urls"))`` — see
    ``_templates.URLS_PY`` / ``new_service.make_context``), so every
    freshly-generated monolith with an HTTP-capable lib false-positived
    ADO001 on itself (found via the e2e-generated-project CI gate)."""
    parts = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        else:
            parts.append("{}")
    return "".join(parts)


def _route_literal(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return _fstring_to_route(node)
    return None


def _join_route(prefix: str, seg: str) -> str:
    if not prefix:
        return seg
    return prefix.rstrip("/") + "/" + seg.lstrip("/")


def _mount_module(dotted: str) -> str:
    """``"stapel_auth.urls"`` → ``"stapel_auth"``."""
    dotted = dotted.strip()
    if dotted.endswith(".urls"):
        dotted = dotted[: -len(".urls")]
    return dotted


def _walk_patterns(
    elts: list[ast.expr], prefix: str, mounts: set[str], routes: list[Route]
) -> None:
    for el in elts:
        if not isinstance(el, ast.Call):
            continue
        fn = _call_name(el.func)
        if fn not in ("path", "re_path", "url"):
            continue
        if not el.args:
            continue
        raw_route = _route_literal(el.args[0])
        if raw_route is None:
            continue
        seg = _regex_to_route(raw_route) if fn in ("re_path", "url") else raw_route
        full = _join_route(prefix, seg)
        target = el.args[1] if len(el.args) > 1 else None
        if isinstance(target, ast.Call) and _call_name(target.func) == "include":
            inc = target.args[0] if target.args else None
            if isinstance(inc, ast.Constant) and isinstance(inc.value, str):
                mounts.add(_mount_module(inc.value))
            elif isinstance(inc, (ast.List, ast.Tuple)):
                _walk_patterns(list(inc.elts), full, mounts, routes)
            # else: opaque include (variable / computed) — cannot resolve
        else:
            routes.append(Route(full, el.lineno))


def _regex_to_route(regex: str) -> str:
    """Best-effort re_path regex → route template."""
    r = regex.strip()
    r = r.lstrip("^").rstrip("$")
    r = re.sub(r"\(\?P<[^>]+>[^)]*\)", "{}", r)  # named groups
    r = re.sub(r"\([^)]*\)", "{}", r)            # anonymous groups
    return r


def parse_urlconf(path: Path) -> tuple[set[str], list[Route]]:
    mounts: set[str] = set()
    routes: list[Route] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return mounts, routes
    for node in ast.walk(tree):
        target_names: set[str] = set()
        value: Optional[ast.expr] = None
        if isinstance(node, ast.Assign):
            target_names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            value = node.value
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            target_names = {node.target.id}
            value = node.value
        if "urlpatterns" not in target_names or value is None:
            continue
        if isinstance(value, (ast.List, ast.Tuple)):
            _walk_patterns(list(value.elts), "", mounts, routes)
    return mounts, routes


def normalize_route(route: str) -> str:
    r = route.strip().strip("/")
    r = _ROUTE_PARAM_DJANGO.sub("{}", r)
    r = _ROUTE_PARAM_OPENAPI.sub("{}", r)
    return r


def scan_headless_markers(files: Iterable[Path]) -> set[str]:
    """Short names declared headless via ``# stapel: headless <mod>``."""
    marks: set[str] = set()
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _HEADLESS_RE.finditer(text):
            marks.add(module_short(match.group(1)))
    return marks


# ---------------------------------------------------------------------------
# module location + schema loading
# ---------------------------------------------------------------------------


def locate_module_dir(pkg: str, search_roots: list[Path]) -> Optional[Path]:
    """Directory of an installed ``stapel_<mod>`` package, or a sibling
    ``stapel-<mod>`` repo under a search root."""
    try:
        spec = importlib.util.find_spec(canon_dist(pkg))
    except (ImportError, ValueError, ModuleNotFoundError):
        spec = None
    if spec is not None:
        if spec.submodule_search_locations:
            return Path(list(spec.submodule_search_locations)[0])
        if spec.origin and spec.origin not in ("built-in", "namespace"):
            return Path(spec.origin).parent
    short = module_short(pkg)
    for root in search_roots:
        cand = root / f"stapel-{short}"
        if cand.is_dir():
            return cand
    return None


def module_has_urls(pkg: str, search_roots: list[Path]) -> bool:
    mod_dir = locate_module_dir(pkg, search_roots)
    if mod_dir is None:
        return False
    return (mod_dir / "urls.py").is_file() or (mod_dir / "urls").is_dir()


def load_module_schema_paths(
    pkg: str, search_roots: list[Path]
) -> Optional[dict[str, list[str]]]:
    """Map normalized module path -> list of ``"METHOD operationId"`` labels,
    or None when no schema is discoverable."""
    mod_dir = locate_module_dir(pkg, search_roots)
    schema_path = None
    if mod_dir is not None and (mod_dir / "docs" / "schema.json").is_file():
        schema_path = mod_dir / "docs" / "schema.json"
    else:
        short = module_short(pkg)
        for root in search_roots:
            cand = root / f"stapel-{short}" / "docs" / "schema.json"
            if cand.is_file():
                schema_path = cand
                break
    if schema_path is None:
        return None
    try:
        doc = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    paths = doc.get("paths")
    if not isinstance(paths, dict):
        return {}
    out: dict[str, list[str]] = {}
    for raw_path, methods in paths.items():
        norm = normalize_route(str(raw_path))
        labels = out.setdefault(norm, [])
        if isinstance(methods, dict):
            for method, op in methods.items():
                op_id = ""
                if isinstance(op, dict):
                    op_id = op.get("operationId") or op.get("summary") or ""
                labels.append(f"{method.upper()} {raw_path}"
                              + (f" ({op_id})" if op_id else ""))
    return out


# ---------------------------------------------------------------------------
# ADO005 — a gdpr owner library needs a consumer process and a part
# ---------------------------------------------------------------------------

#: The file whose presence in a library's package IS its declaration "I am a
#: gdpr data owner": the consumed-action contract for the erasure request.
#: A library that ships it has a subscriber that only ever runs inside an
#: action-consuming process.
GDPR_CONSUMES_ERASURE = ("schemas", "consumes", "gdpr.erasure.requested.json")

#: Where an owner library declares its identity, and under which names. Both
#: spellings are live in the fleet (``OWNER``/``SUBJECT_TYPES`` in recordings,
#: cdn, docs, agent; ``GDPR_OWNER``/``GDPR_SUBJECT_TYPES`` in workspaces,
#: profiles, notifications), in either file.
GDPR_DECL_FILES = ("erasure.py", "gdpr.py")
GDPR_OWNER_CONSTANTS = ("OWNER", "GDPR_OWNER")
GDPR_SUBJECT_CONSTANTS = ("SUBJECT_TYPES", "GDPR_SUBJECT_TYPES")

#: The management command that drains the action transport. A deploy without
#: one delivers no action to any subscriber, however correctly installed.
CONSUME_ACTIONS_CMD = "consume_actions"

#: The service list at the root of the fleet shape stapel-tools emits
#: (``services.conf`` + ``<prefix><svc>/`` directories + ``<prefix><svc>.yml``
#: compose fragments) — the same file ``scripts/verify_boot_contract.sh``
#: iterates.
FLEET_SERVICES_CONF = "services.conf"

#: Compose files a project owns directly (monolith shape, and the root
#: stack of a fleet).
DEPLOY_GLOBS = (
    "docker-compose*.yml",
    "docker-compose*.yaml",
    "compose*.yml",
    "compose*.yaml",
    "deploy/docker-compose*.yml",
    "deploy/docker-compose*.yaml",
)


@dataclass
class GdprOwner:
    """What an owner library claims: the name it answers to in
    ``STAPEL_GDPR["DATA_OWNERS"]`` and the subject types it can erase."""

    pkg: str
    owner: Optional[str]
    subject_types: tuple[str, ...]
    decl: Optional[str]  # file the constants were read from


def _string_env(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    """Module-level ``NAME = "literal"`` bindings, and ``Class.attr = "literal"``
    class attributes — enough to resolve the two indirections real owner
    libraries use (``SUBJECT_TYPES = (SUBJECT_ACCOUNT, ...)`` in recordings,
    ``OWNER = AgentGDPRProvider.section`` in agent)."""
    consts: dict[str, str] = {}
    attrs: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign):
            if (
                isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        consts[target.id] = node.value.value
        elif isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign):
                    continue
                if not (
                    isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    continue
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        attrs[f"{node.name}.{target.id}"] = stmt.value.value
    return consts, attrs


def _resolve_str(
    node: Optional[ast.AST], consts: dict[str, str], attrs: dict[str, str]
) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return attrs.get(f"{node.value.id}.{node.attr}")
    return None


def _resolve_str_seq(
    node: Optional[ast.AST], consts: dict[str, str], attrs: dict[str, str]
) -> tuple[str, ...]:
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return ()
    out = []
    for el in node.elts:
        value = _resolve_str(el, consts, attrs)
        if value is not None:
            out.append(value)
    return tuple(out)


def _gdpr_decl_dir(pkg: str, search_roots: list[Path]) -> Optional[Path]:
    """Directory that ships the erasure consume-contract for ``pkg`` — the
    installed package, or the sibling ``stapel-<mod>`` checkout."""
    mod_dir = locate_module_dir(pkg, search_roots)
    if mod_dir is not None and mod_dir.joinpath(*GDPR_CONSUMES_ERASURE).is_file():
        return mod_dir
    short = module_short(pkg)
    for root in search_roots:
        cand = root / f"stapel-{short}"
        if cand.joinpath(*GDPR_CONSUMES_ERASURE).is_file():
            return cand
    return None


def read_gdpr_owner(pkg: str, search_roots: list[Path]) -> Optional[GdprOwner]:
    """The owner declaration of an installed library, or None when the library
    does not participate in the gdpr protocol at all."""
    decl_dir = _gdpr_decl_dir(pkg, search_roots)
    if decl_dir is None:
        return None
    owner: Optional[str] = None
    subjects: tuple[str, ...] = ()
    source: Optional[str] = None
    for fname in GDPR_DECL_FILES:
        path = decl_dir / fname
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        consts, attrs = _string_env(tree)
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if owner is None and names & set(GDPR_OWNER_CONSTANTS):
                owner = _resolve_str(node.value, consts, attrs)
                if owner is not None:
                    source = fname
            if not subjects and names & set(GDPR_SUBJECT_CONSTANTS):
                subjects = _resolve_str_seq(node.value, consts, attrs)
        if owner is not None and subjects:
            break
    return GdprOwner(pkg=canon_dist(pkg), owner=owner, subject_types=subjects,
                     decl=source)


def find_deploy_files(project: Path) -> list[Path]:
    """Compose/deploy files that describe how THIS service runs.

    Two shapes, both emitted by stapel-tools: a monolith owns its
    ``docker-compose*.yml``; a fleet service is a ``<prefix><svc>/`` directory
    whose compose fragment is the sibling ``<prefix><svc>.yml`` next to
    ``services.conf`` (the layout ``scripts/verify_boot_contract.sh`` walks).
    A fleet ROOT (the directory that holds ``services.conf``) resolves to the
    root stack plus every service fragment it lists.
    """
    found: set[Path] = set()
    for pattern in DEPLOY_GLOBS:
        found.update(p for p in project.glob(pattern) if p.is_file())

    parent = project.parent
    if (parent / FLEET_SERVICES_CONF).is_file():
        fragments = [
            parent / f"{project.name}{suffix}" for suffix in (".yml", ".yaml")
        ]
        fragments = [p for p in fragments if p.is_file()]
        if fragments:
            # This service's OWN fragment is the whole answer: the root stack
            # is the union of every service, so reading it here would let one
            # service's consumer certify all the others.
            found.update(fragments)
        else:
            for pattern in DEPLOY_GLOBS:
                found.update(p for p in parent.glob(pattern) if p.is_file())

    conf = project / FLEET_SERVICES_CONF
    if conf.is_file():
        for svc in read_services_conf(conf):
            for prefix in ("svc-", ""):
                for suffix in (".yml", ".yaml"):
                    frag = project / f"{prefix}{svc}{suffix}"
                    if frag.is_file():
                        found.add(frag)
    return sorted(found)


def read_services_conf(path: Path) -> list[str]:
    """Service names from a fleet ``services.conf`` (one per line, ``#``
    comments) — the same parse the boot-contract gate does in sh."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    names = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            names.append(line)
    return names


#: The action transport that needs no consumer process: handlers are called
#: in the emitting process. Anything else ("bus", "nats", "kafka", …) puts a
#: broker between the emit and the handler, and the handler side of that is a
#: ``consume_actions`` process or nothing.
INPROCESS_TRANSPORT = "inprocess"


def _literal_or_getenv_default(node: Optional[ast.AST]) -> Optional[str]:
    """A settings value that is either a literal or ``os.getenv("X", "lit")``
    — the shape the generated settings use for every transport."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and _call_name(node.func) in ("getenv", "environ"):
        if len(node.args) > 1:
            return _literal_or_getenv_default(node.args[1])
    return None


def declared_action_transports(settings_files: Iterable[Path]) -> set[str]:
    """Every ``STAPEL_COMM["ACTION_TRANSPORT"]`` declared in these settings.

    Empty means nothing declares one, which is stapel-core's default
    (``inprocess``) — not an unknown.
    """
    transports: set[str] = set()
    for path in settings_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if "STAPEL_COMM" not in names or not isinstance(node.value, ast.Dict):
                continue
            for key, value in zip(node.value.keys, node.value.values):
                if not (isinstance(key, ast.Constant)
                        and key.value == "ACTION_TRANSPORT"):
                    continue
                resolved = _literal_or_getenv_default(value)
                transports.add(resolved if resolved is not None else "?")
    return transports


def needs_action_consumer(settings_files: Iterable[Path]) -> bool:
    """True when some settings module puts a broker between an emitted action
    and its handler. In-process delivery needs no consumer, so demanding one
    would be a gate reporting a defect that does not exist; a transport this
    linter could not read ("?") is not a declaration of one either."""
    return any(
        transport not in (INPROCESS_TRANSPORT, "?")
        for transport in declared_action_transports(settings_files)
    )


def deploy_runs_consume_actions(deploy_files: Iterable[Path]) -> bool:
    """True when some deploy file starts the action consumer. Commented lines
    do not count — the emitted service fragment ships a commented
    ``serve_functions`` worker, and a gate satisfied by a comment is the kind
    of gate that lies."""
    for path in deploy_files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            if CONSUME_ACTIONS_CMD in line:
                return True
    return False


def parse_data_owners(
    settings_files: Iterable[Path],
) -> tuple[Optional[dict[str, tuple[str, ...]]], Optional[Path]]:
    """``STAPEL_GDPR["DATA_OWNERS"]`` as ``{owner: subject types}``.

    Both declared shapes are read: the 0.5.0 map, and the legacy plain list
    (which means ``["account"]`` for every name). Returns ``(None, path)``
    when ``STAPEL_GDPR`` is present but the value is computed rather than
    literal — unparseable is not the same as missing, and a gate must not
    invent a finding out of what it could not read.
    """
    host: Optional[Path] = None
    for path in settings_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if "STAPEL_GDPR" not in names:
                continue
            host = path
            if not isinstance(node.value, ast.Dict):
                return None, host
            for key, value in zip(node.value.keys, node.value.values):
                if not (isinstance(key, ast.Constant) and key.value == "DATA_OWNERS"):
                    continue
                if isinstance(value, ast.Dict):
                    owners: dict[str, tuple[str, ...]] = {}
                    for okey, oval in zip(value.keys, value.values):
                        if not (isinstance(okey, ast.Constant)
                                and isinstance(okey.value, str)):
                            return None, host
                        owners[okey.value] = _resolve_str_seq(oval, {}, {})
                    return owners, host
                if isinstance(value, (ast.List, ast.Tuple)):
                    legacy = _resolve_str_seq(value, {}, {})
                    return {name: ("account",) for name in legacy}, host
                return None, host
            # STAPEL_GDPR declared, DATA_OWNERS absent: no owner has a part.
            return {}, host
    return None, host


def find_gdpr_host_settings(
    project: Path, own_installed: set[str], own_settings: list[Path]
) -> tuple[list[Path], Optional[str]]:
    """Settings files of the service that runs stapel_gdpr — this project, or
    a sibling service in the same fleet. Returns (files, service label)."""
    if any(canon_dist(app) == "stapel_gdpr" for app in own_installed):
        return own_settings, None
    parent = project.parent
    if not (parent / FLEET_SERVICES_CONF).is_file():
        return [], None
    for sibling in sorted(p for p in parent.iterdir() if p.is_dir()):
        if sibling == project:
            continue
        files = find_settings_files(sibling)
        if not files:
            continue
        installed, _ = parse_settings(files)
        if any(canon_dist(app) == "stapel_gdpr" for app in installed):
            return files, sibling.name
    return [], None


def _installed_app_anchor(
    settings_files: Iterable[Path], pkg: str, fallback: str
) -> tuple[str, int]:
    """Where the project names this app, so the finding points at the line
    that created the obligation."""
    needle = canon_dist(pkg)
    for path in settings_files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, 1):
            if needle in line:
                return str(path), lineno
    return fallback, 1


# ---------------------------------------------------------------------------
# imports actually present in the project (ADO004)
# ---------------------------------------------------------------------------


def collect_imported_top_levels(project: Path) -> set[str]:
    names: set[str] = set()
    for py in _walk_py(project):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    names.add(node.module.split(".")[0])
    return names


def _build_dist_import_map() -> dict[str, set[str]]:
    """Reverse of ``importlib.metadata.packages_distributions()``: canonical
    dist name → the top-level import names it actually installs. This is the
    authoritative source (correctly maps ``django-debug-toolbar`` → the
    ``debug_toolbar`` import, ``PyJWT`` → ``jwt``, …)."""
    mapping: dict[str, set[str]] = {}
    try:
        pkg_dists = importlib_metadata.packages_distributions()
    except Exception:  # pragma: no cover - defensive
        pkg_dists = {}
    for import_name, dists in pkg_dists.items():
        for dist in dists:
            mapping.setdefault(canon_dist(dist), set()).add(import_name)
    return mapping


def _dist_import_names(
    canon: str, dist_import_map: dict[str, set[str]]
) -> Optional[set[str]]:
    """Resolve a canonical dist to its top-level import name(s), or None when
    it cannot be determined (dist not installed and not in the alias table)."""
    if canon in dist_import_map:
        return dist_import_map[canon]
    if canon in IMPORT_ALIASES:
        return {IMPORT_ALIASES[canon]}
    return None


# ---------------------------------------------------------------------------
# git (ADO003)
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )


def git_branch_state(project: Path) -> Optional[dict]:
    """Return {branch, main, merged} or None when not a git repo."""
    top = _git(project, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return None
    root = Path(top.stdout.strip())
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    main = None
    for cand in ("main", "master"):
        if _git(root, "rev-parse", "--verify", "--quiet", cand).returncode == 0:
            main = cand
            break
    merged = False
    if main is not None:
        merged = _git(root, "merge-base", "--is-ancestor", "HEAD", main).returncode == 0
    return {"root": root, "branch": branch, "main": main, "merged": merged}


def find_migration_doc(project: Path, git_root: Optional[Path]) -> Optional[Path]:
    for base in filter(None, (project, git_root)):
        cand = base / "STAPEL-MIGRATION.md"
        if cand.is_file():
            return cand
    return None


# ---------------------------------------------------------------------------
# lint driver
# ---------------------------------------------------------------------------


def lint_project(
    project: Path,
    *,
    search_roots: Optional[list[Path]] = None,
    notes: Optional[list[str]] = None,
) -> list[Finding]:
    project = project.resolve()
    if search_roots is None:
        search_roots = [project.parent]
    if notes is None:
        notes = []
    findings: list[Finding] = []

    req_files = find_requirements(project)
    settings_files = find_settings_files(project)
    pins = parse_requirements(req_files)
    installed_apps, root_urlconfs = parse_settings(settings_files)
    urlconf_files = discover_urlconfs(project, root_urlconfs)

    # installed stapel modules (union of requirements + INSTALLED_APPS)
    stapel_pkgs: set[str] = {
        p.canon for p in pins if is_stapel_module(p.dist)
    }
    stapel_pkgs.update(canon_dist(a) for a in installed_apps if is_stapel_module(a))

    # urlconf analysis
    mounts: set[str] = set()
    routes: list[Route] = []
    urlconf_by_route: dict[int, str] = {}
    for uf in urlconf_files:
        m, r = parse_urlconf(uf)
        mounts.update(m)
        for route in r:
            urlconf_by_route[id(route)] = str(uf)
            routes.append(route)
    headless = scan_headless_markers(list(urlconf_files) + settings_files)

    urlconf_anchor = str(urlconf_files[0]) if urlconf_files else str(project)

    if not urlconf_files:
        notes.append(
            "stapel-adoption-lint: no ROOT_URLCONF file found "
            "(config/urls.py convention) — ADO001/ADO002 skipped"
        )

    # ------------------------------------------------------------------ ADO001
    if urlconf_files:
        for pkg in sorted(stapel_pkgs):
            if not module_has_urls(pkg, search_roots):
                continue  # nothing to mount (library-only module)
            if canon_dist(pkg) in {canon_dist(m) for m in mounts}:
                continue
            if module_short(pkg) in headless:
                continue
            findings.append(Finding(
                urlconf_anchor, 1, "ADO001",
                f"module '{pkg}' is installed and ships a urlconf but is not "
                f"mounted — add path(..., include(\"{canon_dist(pkg)}.urls\")) "
                f"to the ROOT_URLCONF, or declare intentional headless use with "
                f"'# stapel: headless {module_short(pkg)}'",
            ))

    # ------------------------------------------------------------------ ADO002
    if urlconf_files and routes:
        module_schemas: dict[str, dict[str, list[str]]] = {}
        for pkg in sorted(stapel_pkgs):
            schema = load_module_schema_paths(pkg, search_roots)
            if schema is None:
                notes.append(
                    f"stapel-adoption-lint: no docs/schema.json for '{pkg}' "
                    f"— ADO002 duplicate check skipped for it"
                )
                continue
            module_schemas[pkg] = schema
        for route in routes:
            norm = normalize_route(route.route)
            if not norm:
                continue
            for pkg, schema in module_schemas.items():
                if norm in schema:
                    ops = ", ".join(schema[norm]) or "(operation)"
                    findings.append(Finding(
                        urlconf_by_route.get(id(route), urlconf_anchor), route.line,
                        "ADO002",
                        f"custom route '{route.route}' duplicates an operation of "
                        f"installed module '{pkg}': {ops} — remove the local route "
                        f"and use the module (or its extension point) instead of "
                        f"forking the wire contract",
                    ))
                    break

    # ------------------------------------------------------------------ ADO005
    installed_canon = {canon_dist(a) for a in installed_apps}
    gdpr_owners = []
    for pkg in sorted(installed_canon):
        if not is_stapel_module(pkg):
            continue
        decl = read_gdpr_owner(pkg, search_roots)
        if decl is not None:
            gdpr_owners.append(decl)

    if gdpr_owners:
        deploy_files = find_deploy_files(project)
        host_settings, host_label = find_gdpr_host_settings(
            project, installed_apps, settings_files
        )
        data_owners, host_path = parse_data_owners(host_settings)

        if not needs_action_consumer(settings_files):
            # In-process delivery (stapel-core's default, and every monolith):
            # the handler runs in the emitting process, so there is no
            # consumer process to look for.
            runs_consumer = True
        elif not deploy_files:
            notes.append(
                "stapel-adoption-lint: no compose/deploy file found for this "
                "service (docker-compose*.yml, or a <svc>.yml next to "
                "services.conf) — the ADO005 consumer check is skipped"
            )
            runs_consumer = True
        else:
            runs_consumer = deploy_runs_consume_actions(deploy_files)

        if not host_settings:
            notes.append(
                "stapel-adoption-lint: no service in this project or fleet "
                "installs stapel_gdpr — the ADO005 DATA_OWNERS check is skipped"
            )
        elif data_owners is None:
            notes.append(
                f"stapel-adoption-lint: STAPEL_GDPR in "
                f"{host_path or '(settings)'} is not a literal this linter can "
                f"read — the ADO005 DATA_OWNERS check is skipped"
            )

        for decl in gdpr_owners:
            anchor, line = _installed_app_anchor(
                settings_files, decl.pkg, urlconf_anchor
            )
            if not runs_consumer:
                where = ", ".join(str(p) for p in deploy_files)
                findings.append(Finding(
                    anchor, line, "ADO005",
                    f"'{decl.pkg}' is installed and is a gdpr data owner (it "
                    f"ships schemas/consumes/gdpr.erasure.requested.json) but "
                    f"no '{CONSUME_ACTIONS_CMD}' process runs in this service's "
                    f"deploy ({where}) — its erasure subscriber is never "
                    f"delivered to, so every erasure request times out on a "
                    f"silent owner; add a consume_actions service/command to "
                    f"the compose",
                ))
            if data_owners is None:
                continue
            if decl.owner is None:
                notes.append(
                    f"stapel-adoption-lint: '{decl.pkg}' declares gdpr "
                    f"participation but its OWNER/GDPR_OWNER constant is not a "
                    f"literal this linter can read — DATA_OWNERS membership not "
                    f"checked for it"
                )
                continue
            host_where = str(host_path) if host_path else "the gdpr host settings"
            if host_label:
                host_where += f" (service '{host_label}')"
            if decl.owner not in data_owners:
                findings.append(Finding(
                    anchor, line, "ADO005",
                    f"'{decl.pkg}' is installed and is a gdpr data owner but "
                    f"'{decl.owner}' is not listed in STAPEL_GDPR"
                    f"[\"DATA_OWNERS\"] in {host_where} — the orchestrator "
                    f"creates no part for it, so its data survives an erasure "
                    f"that reports itself complete; add "
                    f"\"{decl.owner}\": {list(decl.subject_types) or ['account']}",
                ))
                continue
            missing = [
                subject for subject in decl.subject_types
                if subject not in data_owners[decl.owner]
            ]
            if missing:
                findings.append(Finding(
                    anchor, line, "ADO005",
                    f"owner '{decl.owner}' ({decl.pkg}) is listed in "
                    f"STAPEL_GDPR[\"DATA_OWNERS\"] in {host_where} without the "
                    f"subject type(s) the library claims: "
                    f"{', '.join(missing)} — an erasure of those subjects "
                    f"never reaches this owner; declare "
                    f"\"{decl.owner}\": {list(decl.subject_types)}",
                ))

    # ------------------------------------------------------------------ ADO003
    state = git_branch_state(project)
    if state is not None:
        git_root = state["root"]
        doc = find_migration_doc(project, git_root)
        if doc is not None:
            try:
                done = bool(_DONE_RE.search(doc.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError):
                done = False
            on_main = state["branch"] in ("main", "master")
            if done and not on_main and not state["merged"]:
                where = (
                    f"not merged into {state['main']}" if state["main"]
                    else "and no main/master branch exists"
                )
                findings.append(Finding(
                    str(doc), 1, "ADO003",
                    f"STAPEL-MIGRATION.md records done work but the current "
                    f"branch '{state['branch']}' is {where} — a finished "
                    f"migration must ship on main, not linger on a branch",
                    level="warning",
                ))

    # ------------------------------------------------------------------ ADO004
    imported = collect_imported_top_levels(project)
    settings_strings = collect_settings_strings(settings_files)
    dist_import_map = _build_dist_import_map()
    seen_dist: set[str] = set()
    for pin in pins:
        if pin.canon in seen_dist:
            continue
        seen_dist.add(pin.canon)
        if is_stapel_module(pin.dist):
            continue  # referenced by dotted string, never imported
        if pin.canon in RUNTIME_ONLY_DISTS:
            continue
        import_names = _dist_import_names(pin.canon, dist_import_map)
        if import_names is None:
            continue  # cannot resolve — leave alone
        if imported & import_names:
            continue
        if settings_strings & import_names:
            continue  # configured by string (INSTALLED_APPS/backend/…), not dead
        findings.append(Finding(
            pin.path, pin.line, "ADO004",
            f"requirement '{pin.dist}' (imported as "
            f"{'/'.join(sorted(import_names))}) is not imported anywhere in the "
            f"project — dead pin; drop it or start using it",
            level="warning",
        ))

    findings.sort(key=lambda f: (f.path, f.line, f.rule))
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-adoption-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "project_dir", nargs="?", default=".",
        help="Project directory to lint (default: .)",
    )
    parser.add_argument(
        "--workspace", action="append", default=[],
        help="Extra root to search for sibling stapel-<mod> repos and their "
             "docs/schema.json (repeatable). The project's parent is always "
             "searched.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Machine output: findings + counts",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Promote warnings to errors (exit 1 on any finding)",
    )
    args = parser.parse_args(argv)

    project = Path(args.project_dir)
    if not project.is_dir():
        print(f"Error: not a directory: {project}", file=sys.stderr)
        return 2

    search_roots = [project.resolve().parent] + [Path(w).resolve() for w in args.workspace]
    notes: list[str] = []
    findings = lint_project(project, search_roots=search_roots, notes=notes)

    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level != "error"]
    failing = findings if args.strict else errors

    if args.json:
        print(json.dumps(
            {
                "ok": not failing,
                "errors": len(errors),
                "warnings": len(warnings),
                "findings": [f.to_dict() for f in findings],
                "notes": notes,
            },
            indent=2, sort_keys=True, ensure_ascii=False,
        ))
    else:
        for note in notes:
            print(note, file=sys.stderr)
        for finding in findings:
            print(finding)
        if findings:
            parts = []
            if errors:
                parts.append(f"{len(errors)} error{'s' if len(errors) != 1 else ''}")
            if warnings:
                parts.append(f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}")
            print(f"\n{', '.join(parts)} found in {project}.")
        else:
            print(f"No adoption issues found in {project}.")

    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
