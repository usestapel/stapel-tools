"""
stapel-authz-lint — the "credentials verified, authorization never asked" gate,
in the ``stapel-url-lint`` / ``stapel-api-lint`` idiom (rule codes, ``--json``,
``--strict``, exit 1 on any error).

Why this exists
----------------
On 2026-08-24 stapel-core shipped five security releases (0.38.0 - 0.43.0) for
what turned out to be **one defect class wearing five costumes**: every place
where the code proved *who you are* and then never asked *what you may have*.

* ``JWTCookieLoginView`` is the admin login view (its template is
  ``admin/login.html``), but it named no ``authentication_form``, so Django
  used the plain ``AuthenticationForm`` — which checks ``is_active`` and
  nothing else. ``form_valid()`` then called ``login()``,
  ``create_tokens()`` and ``set_jwt_cookies()`` with no staff check anywhere
  in its body. Any active account's own password minted a fleet-wide JWT
  pair. The file *did* read ``is_staff`` three times — all three in
  ``dispatch()``'s already-authenticated branch, none on the minting path.
* ``JWTRefreshView`` called ``refresh_access_token(token)`` with no user
  loader. The default meant "re-mint from the presented token's own claims",
  which are up to ``JWT_REFRESH_TOKEN_LIFETIME`` (7 days) old: a revoked
  staff flag came back to life on refresh.
* Two ``get_user()`` overrides returned ``User.objects.get(pk=...)``
  directly, silently dropping the ``user_can_authenticate()`` check
  ``ModelBackend.get_user`` performs — so a deactivated account kept a live
  session for the whole life of its session cookie.
* Both blacklists wrote through ``django.core.cache.cache``, whose real key
  is built from the *deployment's* ``KEY_PREFIX`` — which every service in a
  split deployment sets differently, on purpose. ``auth`` wrote
  ``auth:1:jwt_blacklist:<jti>``, ``profiles`` read
  ``stapel_profiles:1:jwt_blacklist:<jti>``. "Log out everywhere" was a
  per-service illusion for months, which is what made the login bypass
  unrecoverable while it was live.

Every one of those was found by a human reading code. None of them was found
by a test, because each component's own suite was green: the login view did
log people in, the refresh endpoint did return a token, the blacklist did
blacklist. The defect lived in what was **not** written, and nothing in the
fleet reads for absence. This linter does.

The shape of the class, in one sentence: *authentication answered, then
authorization skipped* — a password checked and a privilege assumed, a
signature checked and a revocation not consulted, an identity resolved and a
lifecycle flag not re-read.

Rules
-----
AUTHZ001  (error) A ``LoginView`` subclass that defines ``form_valid`` and
          neither names an ``Admin*`` authentication form (class attribute or
          ``get_form_class``) nor reads authorization (``is_staff`` /
          ``is_superuser`` / a permission call) inside ``form_valid``'s own
          body. This is the live bypass verbatim: ``form_valid`` is the method
          that runs *after* the password is correct, so a privilege gate that
          is not in it — or in the form class that decided the password was
          enough — does not exist on that path.

AUTHZ002  (error) ``create_tokens`` / ``set_jwt_cookies`` called in a function
          that also calls ``login()`` or ``form.get_user()``, with no
          authorization read earlier in the same function. The general form of
          AUTHZ001: credentials verified, authorization never consulted,
          tokens issued. A token outlives the request that minted it, so the
          check has to happen before the mint, not after.

AUTHZ003  (warning) An explicit ``refresh_access_token(x, None)`` (or
          ``load_user_data=None``). Since stapel-core 0.39.0 the django-layer
          provider defaults to the database loader; passing ``None`` keeps the
          framework-free meaning "re-mint from the token's own claims", which
          resurrects revoked flags. Warning, not error: it is a legitimate
          choice in a framework-free caller — it just has to be a *decision*,
          typed out and visible in review, rather than an omission.

AUTHZ004  (error) A ``get_user()`` method override that returns / assigns
          ``...objects.get(...)`` with no ``is_active`` or
          ``user_can_authenticate`` check in the body. ``get_user`` is what
          resolves ``request.user`` from the session on every request *after*
          the one that authenticated; an override that drops the lifecycle
          check makes deactivation take effect at the next login, which is
          precisely the login that will not happen.

AUTHZ005  (error) A blacklist/revocation entry read or written through
          ``django.core.cache.cache``. Django builds the real key from the
          deployment's ``KEY_PREFIX``, so a per-service prefix makes the entry
          invisible to peers sharing the same Redis. Revocation must go
          through the fleet-wide namespace —
          ``stapel_core.core.revocation_store.revocation_cache()`` — which
          borrows the deployment's own connection with ``KEY_PREFIX`` and
          ``VERSION`` forced to fleet values.

AUTHZ006  (error) A DRF view or ``@action`` that is reachable **without
          authentication** (``AllowAny``, an empty ``permission_classes``, or
          ``IsAuthenticatedOrReadOnly`` on a read method) reads a manager that
          **bypasses the default visibility filter** (``all_objects``,
          ``_base_manager``, ``with_deleted()``, ``all_with_deleted()``,
          ``only_deleted()``) and answers with **identity or moderation
          state** — ``owner``/``owner_id``/``user_id``/``created_by``,
          ``moderation_*``, or the lifecycle ``status`` of a row the default
          filter would have hidden. Each half is ordinary on its own;
          together they are an enumeration oracle. This is stapel-listings
          0.8.0 verbatim: ``GET /listings/{pk}/status/`` was ``AllowAny``,
          read ``Listing.all_objects`` and returned ``owner_id`` plus
          ``moderation_status``. Listing ids are sequential, so an anonymous
          walk of the ids harvested — for every listing in the deployment,
          other people's drafts, rejected and soft-deleted rows included — who
          owns it and what a moderator decided. Found by doing exactly that
          against a live stand; AUTHZ001-005 were blind to it because nothing
          on that path mints a credential or overrides ``get_user``.

          A branch on ownership or permission inside the function (or a named
          predicate in the same module) silences the rule, because that is
          precisely the fix — see ``_MSG_006``.

Suppression
-----------
``# noqa: AUTHZ00N`` on the reported line, same escape as every other stapel
linter. A bare ``# noqa`` suppresses all of them. Use it for the case the rule
cannot see — and write the reason next to it, because the next reader's only
alternative is to re-derive the whole argument.

What these rules deliberately do NOT try to be
-----------------------------------------------
A type checker. There is no import-time resolution of what a base class
actually is, no cross-module call graph, no dataflow. Recognition is
syntactic, with one hop of same-module helper resolution (a ``form_valid``
that calls a local ``has_admin_access()`` which itself reads ``is_staff``
passes AUTHZ001, because extracting the check to a helper is the first thing
anyone does and a rule that punished it would be turned off within a week).
The honest consequence — an authorization helper *imported from another
module* is invisible to this linter and reads as absence — is stated in each
rule's section of the README, not hidden.

Exit codes: 0 clean, 1 errors present (``--strict`` promotes AUTHZ003 to an
error), 2 usage/environment errors.
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

# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------

#: Attribute/keyword/string names that ARE an authorization read. Kept short
#: and specific on purpose: every addition here is a way for a real defect to
#: pass, so a name earns its place by being an authorization fact, not by
#: being nearby one. ``is_active`` is NOT here — it is an account-lifecycle
#: fact (AUTHZ004's subject), and treating it as a privilege check is exactly
#: the confusion that shipped the bypass.
AUTHZ_ATTRS = frozenset({
    "is_staff",
    "is_superuser",
    "is_admin",
})

#: Call names that ARE an authorization read wherever they appear.
AUTHZ_CALLS = frozenset({
    "has_perm",
    "has_perms",
    "has_module_perms",
    "has_admin_access",
    "has_staff_access",
    "check_permissions",
    "has_permission",
})

#: Names that make ``get_user``'s body lifecycle-aware (AUTHZ004).
LIFECYCLE_NAMES = frozenset({
    "is_active",
    "user_can_authenticate",
})

#: The two mint calls AUTHZ002 watches for.
MINT_CALLS = frozenset({"create_tokens", "set_jwt_cookies"})

#: Identifier fragments that mark a revocation context (AUTHZ005).
#: Bare "ban" is deliberately absent — it matches "banner" and "urban".
REVOCATION_WORDS = (
    "blacklist",
    "blocklist",
    "denylist",
    "revoke",
    "revocation",
    "revoked",
    "banned",
)

#: Cache operations worth flagging. Reads are included with writes: reading a
#: revocation from the wrong namespace is the other half of the same defect,
#: and it is the half that answers "not revoked" to a revoked token.
CACHE_OPS = frozenset({
    "set", "add", "get", "delete", "clear", "touch",
    "set_many", "get_many", "delete_many", "incr", "decr",
})

# --- AUTHZ006 vocabulary ---------------------------------------------------

#: DRF permission classes that let an UNAUTHENTICATED caller through on any
#: method.
OPEN_PERMISSIONS = frozenset({"AllowAny"})

#: DRF permission classes that let an unauthenticated caller through on a SAFE
#: method only. They are treated exactly like ``AllowAny`` for a read handler:
#: a signed-in stranger is still a stranger, and an oracle that costs an
#: attacker one free account is not meaningfully closed.
READ_OPEN_PERMISSIONS = frozenset({
    "IsAuthenticatedOrReadOnly",
    "DjangoModelPermissionsOrAnonReadOnly",
})

#: HTTP methods that make an ``@action`` a read.
SAFE_HTTP_METHODS = frozenset({"get", "head", "options"})

#: DRF handler methods that are reads on a generic view / viewset.
READ_HANDLERS = frozenset({"get", "list", "retrieve"})

#: Manager attributes that deliberately escape the default visibility filter.
#: ``_default_manager`` is NOT here — it IS the default filter. Derived from
#: the fleet's own soft-delete conventions (``stapel-listings``' ``objects =
#: ListingManager()`` / ``all_objects = models.Manager()``, and the legacy
#: marketplace copy of the same pair).
UNFILTERED_MANAGERS = frozenset({
    "all_objects",
    "_base_manager",
    "unfiltered_objects",
    "objects_with_deleted",
    "raw_objects",
    "all_objects_including_deleted",
})

#: Queryset/manager methods that re-admit the rows the default manager hides.
#: ``only_deleted`` is included: it is not a narrowing of the default filter,
#: it is a jump over it into exactly the rows nobody outside is meant to see.
UNFILTERED_QUERYSET_CALLS = frozenset({
    "with_deleted",
    "all_with_deleted",
    "including_deleted",
    "only_deleted",
    "unfiltered",
})

#: Response field names that name a PERSON or a MODERATOR'S VERDICT. Anything
#: here, reached anonymously over an id space, is an enumeration oracle.
#: ``status`` earns its place only in this rule's company: AUTHZ006 already
#: requires that the row came from the unfiltered manager, so the status being
#: disclosed is the status of a row the default filter would have hidden —
#: someone else's draft, rejected or soft-deleted listing.
DISCLOSURE_FIELDS = frozenset({
    "owner",
    "owner_id",
    "user_id",
    "created_by",
    "created_by_id",
    "author",
    "author_id",
    "status",
})

#: Prefix match, so ``moderation_status``/``moderation_reason``/
#: ``moderation_notes`` are all covered without enumerating them.
DISCLOSURE_PREFIXES = ("moderation",)

#: Names whose appearance IN A DECISION means the code asked "may THIS caller
#: see THIS row?". Reading ``owner_id`` into a response is a disclosure;
#: comparing it is a gate — which is why this vocabulary is only ever consulted
#: inside a boolean expression (see ``_gate_tests``).
GATE_ATTRS = frozenset({
    "owner",
    "owner_id",
    "user_id",
    "created_by",
    "created_by_id",
    "author_id",
    "is_authenticated",
    "is_anonymous",
}) | AUTHZ_ATTRS

#: Calls that ARE such a decision. ``has_permission`` covers the fleet's
#: ``IsServiceRequest().has_permission(request, None)`` idiom.
GATE_CALLS = frozenset({
    "has_permission",
    "has_object_permission",
    "check_object_permissions",
}) | AUTHZ_CALLS


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
# small AST helpers
# ---------------------------------------------------------------------------


def _final_name(node: ast.AST) -> str:
    """Last identifier of a Name/Attribute chain (``a.b.c`` -> ``"c"``)."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _dotted(node: ast.AST) -> str:
    """Dotted source of a Name/Attribute chain, or "" if it is not one."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr}" if head else ""
    return ""


def _call_name(node: ast.Call) -> str:
    return _final_name(node.func)


def _noqa_rules(line: str) -> Optional[set]:
    if "# noqa" not in line:
        return None
    if "# noqa:" not in line:
        return set()
    tail = line.split("# noqa:", 1)[1]
    # The FIRST token of each comma-separated part, so a written reason on the
    # same line ("# noqa: AUTHZ001 - storefront login, not an admin view")
    # still suppresses. Every one of these rules asks for a reason; a parser
    # that then refused to read the line would be arguing with its own advice.
    rules = set()
    for chunk in tail.replace(";", ",").split(","):
        token = chunk.strip().split()[:1]
        if token:
            rules.add(token[0])
    return rules


def _functions(tree: ast.Module):
    """Every function/method in the module, with its enclosing class (or None).

    Nested functions are yielded too, carrying the class of the method they
    live in — a closure that mints tokens is the same defect as a method that
    does, and hiding one inside the other must not be an escape.
    """
    def walk(node, cls):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                yield from walk(child, child)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield child, cls
                yield from walk(child, cls)
            else:
                yield from walk(child, cls)

    yield from walk(tree, None)


# ---------------------------------------------------------------------------
# the authorization-read recognizer (shared by AUTHZ001 and AUTHZ002)
# ---------------------------------------------------------------------------


def _direct_authz_nodes(node: ast.AST) -> list:
    """Nodes inside ``node`` that read authorization *without* any helper hop."""
    hits = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr in AUTHZ_ATTRS:
            hits.append(sub)
        elif isinstance(sub, ast.Name) and sub.id in AUTHZ_ATTRS:
            hits.append(sub)
        elif isinstance(sub, ast.Constant) and sub.value in AUTHZ_ATTRS:
            # getattr(user, "is_staff", False) — the defensive read the fixed
            # core code uses, and the one a naive attribute matcher misses.
            hits.append(sub)
        elif isinstance(sub, ast.keyword) and sub.arg in AUTHZ_ATTRS:
            # User.objects.filter(is_staff=True)
            hits.append(sub)
        elif isinstance(sub, ast.Call) and _call_name(sub) in AUTHZ_CALLS:
            hits.append(sub)
    return hits


def _authz_helpers(tree: ast.Module) -> frozenset:
    """Names of same-module functions that themselves read authorization.

    One transitive layer, to a fixed point over the module's own functions:
    ``form_valid`` -> ``has_admin_access`` -> ``is_staff`` passes, because
    extracting a check into a named predicate is what good code does and a
    rule that treated it as absence would be suppressed on sight. A helper
    imported from ANOTHER module is not resolved — see the module docstring.
    """
    names = set()
    bodies = {}
    for func, _cls in _functions(tree):
        bodies.setdefault(func.name, []).append(func)
        if _direct_authz_nodes(func):
            names.add(func.name)
    for _ in range(3):
        grew = False
        for name, funcs in bodies.items():
            if name in names:
                continue
            for func in funcs:
                if any(
                    isinstance(sub, ast.Call) and _call_name(sub) in names
                    for sub in ast.walk(func)
                ):
                    names.add(name)
                    grew = True
                    break
        if not grew:
            break
    return frozenset(names)


def _authz_reads(node: ast.AST, helpers: frozenset) -> list:
    """Every authorization read inside ``node``, helper calls included."""
    hits = _direct_authz_nodes(node)
    if helpers:
        hits += [
            sub for sub in ast.walk(node)
            if isinstance(sub, ast.Call) and _call_name(sub) in helpers
        ]
    return hits


# ---------------------------------------------------------------------------
# AUTHZ001 — a LoginView subclass whose form_valid asks nothing
# ---------------------------------------------------------------------------


def _is_login_view_base(base: ast.AST) -> bool:
    name = _final_name(base)
    return bool(name) and name.endswith("LoginView")


def _names_admin_form(cls: ast.ClassDef) -> bool:
    """Does the class name an ``Admin*Form`` anywhere in its body?

    Covers both shapes core ended up needing: the class attribute
    (``authentication_form = AdminAuthenticationForm``) and the lazy
    ``get_form_class()`` that imports it at call time to stay
    app-registry safe.
    """
    for sub in ast.walk(cls):
        ident = ""
        if isinstance(sub, (ast.Name, ast.Attribute)):
            ident = _final_name(sub)
        elif isinstance(sub, ast.alias):
            ident = (sub.asname or sub.name).rsplit(".", 1)[-1]
        if ident.startswith("Admin") and ident.endswith("Form"):
            return True
    return False


def _check_authz001(tree: ast.Module, helpers: frozenset) -> list:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(_is_login_view_base(b) for b in node.bases):
            continue
        form_valid = next(
            (
                b for b in node.body
                if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))
                and b.name == "form_valid"
            ),
            None,
        )
        if form_valid is None:
            # No override: Django's own form_valid logs in and mints nothing.
            continue
        if _names_admin_form(node):
            continue
        if _authz_reads(form_valid, helpers):
            continue
        found.append((form_valid.lineno, node.name))
    return found


# ---------------------------------------------------------------------------
# AUTHZ002 — mint after a credential check, with no authorization read before
# ---------------------------------------------------------------------------


def _django_auth_aliases(tree: ast.Module) -> frozenset:
    """Local names bound to ``django.contrib.auth`` (the module or its
    ``login``), so ``self.client.login(...)`` in a test is not mistaken for
    the real thing."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bound = alias.asname or alias.name
                dotted = f"{node.module}.{alias.name}"
                if dotted == "django.contrib.auth.login":
                    names.add(bound)
                elif dotted == "django.contrib.auth":
                    names.add(f"{bound}.login")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "django.contrib.auth":
                    bound = alias.asname or "django.contrib.auth"
                    names.add(f"{bound}.login")
    return frozenset(names)


def _is_credential_call(node: ast.Call, auth_names: frozenset) -> bool:
    """``login(user)`` from django.contrib.auth, or ``form.get_user()``."""
    dotted = _dotted(node.func)
    if dotted and dotted in auth_names:
        return True
    if isinstance(node.func, ast.Attribute) and node.func.attr == "get_user":
        holder = _dotted(node.func.value).lower()
        if "form" in holder:
            return True
    return False


def _check_authz002(tree: ast.Module, helpers: frozenset) -> list:
    auth_names = _django_auth_aliases(tree)
    found = []
    for func, _cls in _functions(tree):
        mints = [
            sub for sub in ast.walk(func)
            if isinstance(sub, ast.Call) and _call_name(sub) in MINT_CALLS
        ]
        if not mints:
            continue
        if not any(
            isinstance(sub, ast.Call) and _is_credential_call(sub, auth_names)
            for sub in ast.walk(func)
        ):
            continue
        first_mint = min(m.lineno for m in mints)
        reads = _authz_reads(func, helpers)
        if any(r.lineno < first_mint for r in reads):
            continue
        found.append((first_mint, func.name))
    return found


# ---------------------------------------------------------------------------
# AUTHZ003 — an explicit "re-mint from the token's own claims"
# ---------------------------------------------------------------------------


def _check_authz003(tree: ast.Module) -> list:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node) != "refresh_access_token":
            continue
        explicit_none = (
            len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value is None
        ) or any(
            kw.arg in ("load_user_data", "load_user")
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is None
            for kw in node.keywords
        )
        if explicit_none:
            found.append(node.lineno)
    return found


# ---------------------------------------------------------------------------
# AUTHZ004 — a get_user() override that dropped Django's own check
# ---------------------------------------------------------------------------


def _is_objects_get(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "objects"
    )


def _lifecycle_helpers(tree: ast.Module) -> frozenset:
    names = set()
    for func, _cls in _functions(tree):
        for sub in ast.walk(func):
            ident = ""
            if isinstance(sub, ast.Attribute):
                ident = sub.attr
            elif isinstance(sub, ast.Name):
                ident = sub.id
            elif isinstance(sub, ast.keyword):
                ident = sub.arg or ""
            elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                ident = sub.value
            if ident in LIFECYCLE_NAMES:
                names.add(func.name)
                break
    return frozenset(names)


def _has_lifecycle_check(func: ast.AST, helpers: frozenset) -> bool:
    for sub in ast.walk(func):
        ident = ""
        if isinstance(sub, ast.Attribute):
            ident = sub.attr
        elif isinstance(sub, ast.Name):
            ident = sub.id
        elif isinstance(sub, ast.keyword):
            ident = sub.arg or ""
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            ident = sub.value
        if ident in LIFECYCLE_NAMES:
            return True
        if isinstance(sub, ast.Call) and _call_name(sub) in helpers:
            return True
    return False


def _check_authz004(tree: ast.Module) -> list:
    helpers = _lifecycle_helpers(tree)
    found = []
    for func, cls in _functions(tree):
        # An override, i.e. a METHOD. A module-level ``get_user(request)``
        # helper is a different animal and is not Django's backend contract.
        if cls is None or func.name != "get_user":
            continue
        gets = [sub for sub in ast.walk(func) if _is_objects_get(sub)]
        if not gets:
            continue
        if _has_lifecycle_check(func, helpers - {func.name}):
            continue
        found.append((min(g.lineno for g in gets), cls.name))
    return found


# ---------------------------------------------------------------------------
# AUTHZ005 — revocation written into a per-service cache namespace
# ---------------------------------------------------------------------------


def _django_cache_aliases(tree: ast.Module) -> frozenset:
    """Local names bound to the ``django.core.cache.cache`` singleton.

    Only the singleton: ``caches[alias]`` is how the shared-namespace store
    is BUILT (``revocation_store._build``'s fallback), so flagging it would
    make the fix flag itself.
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bound = alias.asname or alias.name
                dotted = f"{node.module}.{alias.name}"
                if dotted == "django.core.cache.cache":
                    names.add(bound)
                elif dotted == "django.core.cache":
                    names.add(f"{bound}.cache")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "django.core.cache":
                    bound = alias.asname or "django.core.cache"
                    names.add(f"{bound}.cache")
    return frozenset(names)


def _revocation_context(
    call: ast.Call, func: Optional[ast.AST], cls: Optional[ast.ClassDef],
) -> bool:
    """Is THIS cache call about revocation?

    Three signals, any of which is enough:

    * the enclosing function's name (``blacklist_user``, ``_blacklisted``);
    * the enclosing class's name and its class-level constants
      (``TokenBlacklist.clear_all`` has revocation in neither its own name nor
      its arguments — only in its class and its ``jwt_blacklist`` prefix);
    * an identifier or string literal *inside the call itself* — the key
      expression: ``cache.get(f"user_blacklisted:{uid}")``,
      ``cache.delete(BLACKLIST_KEY.format(...))``.

    Deliberately NOT a signal: prose anywhere in the enclosing function. A
    docstring that mentions the blacklist next to an unrelated
    ``cache.clear()`` is how a rule earns its first false positive and its
    first blanket suppression.
    """
    haystacks = []
    if func is not None:
        haystacks.append(getattr(func, "name", ""))
    if cls is not None:
        haystacks.append(cls.name)
        for sub in cls.body:
            if isinstance(sub, ast.Assign):
                for target in sub.targets:
                    haystacks.append(_final_name(target))
                if isinstance(sub.value, ast.Constant) and isinstance(sub.value.value, str):
                    haystacks.append(sub.value.value)
    for sub in ast.walk(call):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            haystacks.append(sub.value)
        elif isinstance(sub, (ast.Name, ast.Attribute)):
            haystacks.append(_final_name(sub))
    blob = " ".join(h for h in haystacks if h).lower()
    return any(word in blob for word in REVOCATION_WORDS)


def _is_cache_op(node: ast.AST, aliases: frozenset) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in CACHE_OPS
        and _dotted(node.func.value) in aliases
    )


def _check_authz005(tree: ast.Module) -> list:
    # The module that IMPLEMENTS the shared namespace is exempt: it is the
    # only legitimate place a raw cache handle appears in a revocation
    # context, and a rule that flags its own remedy teaches people the rule
    # is wrong.
    defines_remedy = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "revocation_cache"
        for node in ast.walk(tree)
    )
    if defines_remedy:
        return []

    aliases = _django_cache_aliases(tree)
    if not aliases:
        return []

    # Which function/class each cache call sits in, so a call is judged in
    # its own context (module-level calls get None/None and are judged on the
    # key expression alone).
    owner: dict = {}
    for func, cls in _functions(tree):
        for sub in ast.walk(func):
            if _is_cache_op(sub, aliases):
                # _functions yields an enclosing function before the ones
                # nested in it, so the last write is the innermost owner.
                owner[id(sub)] = (func, cls)

    found = []
    for node in ast.walk(tree):
        if not _is_cache_op(node, aliases):
            continue
        func, cls = owner.get(id(node), (None, None))
        if _revocation_context(node, func, cls):
            found.append((node.lineno, node.func.attr))
    return sorted(set(found))


# ---------------------------------------------------------------------------
# AUTHZ006 — an anonymous read of the unfiltered manager that names people
# ---------------------------------------------------------------------------


def _is_disclosure_field(name: str) -> bool:
    return bool(name) and (
        name in DISCLOSURE_FIELDS or name.startswith(DISCLOSURE_PREFIXES)
    )


def _string_items(node: ast.AST) -> list:
    """String constants of a list/tuple/set literal (``fields = [...]``)."""
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return []
    return [
        el.value for el in node.elts
        if isinstance(el, ast.Constant) and isinstance(el.value, str)
    ]


def _is_serializer_class(cls: ast.ClassDef) -> bool:
    if cls.name.endswith("Serializer"):
        return True
    return any(_final_name(b).endswith("Serializer") for b in cls.bases)


def _serializer_disclosures(tree: ast.Module) -> dict:
    """``{serializer class name: {disclosing field names}}`` for one module.

    Three declaration shapes, because all three are in the fleet:

    * explicit fields (``owner_id = serializers.CharField()``);
    * ``Meta.fields`` / ``Meta.read_only_fields`` string lists;
    * the literal dict a hand-written ``to_representation`` returns.

    ``fields = "__all__"`` is deliberately NOT expanded into the model's
    columns — there is no model resolution here, and guessing would be the
    rule's first false positive. It reads as "no declared disclosure", which
    is a blind spot the message states out loud.
    """
    index: dict = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not _is_serializer_class(node):
            continue
        fields = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign):
                for target in sub.targets:
                    name = _final_name(target)
                    if _is_disclosure_field(name):
                        fields.add(name)
                    if name in ("fields", "read_only_fields"):
                        fields.update(
                            f for f in _string_items(sub.value)
                            if _is_disclosure_field(f)
                        )
            elif isinstance(sub, ast.AnnAssign):
                name = _final_name(sub.target)
                if _is_disclosure_field(name):
                    fields.add(name)
            elif isinstance(sub, ast.Dict):
                fields.update(
                    key.value for key in sub.keys
                    if isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and _is_disclosure_field(key.value)
                )
        # Same name in two modules merges rather than overwrites: the index is
        # keyed by bare name (no import resolution), so the honest reading of
        # a collision is "one of these is what the view returns".
        index.setdefault(node.name, set()).update(fields)
    return index


def _gate_tests(node: ast.AST) -> list:
    """Boolean expressions this function makes a DECISION on.

    A gate is a branch, so only branch conditions count: ``if`` / ``while`` /
    ternary / ``assert`` tests, and a boolean expression that is returned or
    assigned (``return str(user.pk) == str(listing.owner_id)`` — the shape the
    real fix's predicate ends in).

    A plain ``return f(x)`` is NOT a gate region. That distinction is the whole
    point: ``return Response({"owner_id": obj.owner_id})`` must stay a
    disclosure and not be mistaken for an ownership check.
    """
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, (ast.If, ast.IfExp, ast.While, ast.Assert)):
            out.append(sub.test)
        elif isinstance(sub, ast.Return) and isinstance(
            sub.value, (ast.Compare, ast.BoolOp, ast.UnaryOp)
        ):
            out.append(sub.value)
        elif isinstance(sub, ast.Assign) and isinstance(
            sub.value, (ast.Compare, ast.BoolOp, ast.UnaryOp)
        ):
            out.append(sub.value)
    return out


def _has_gate(node: ast.AST, helpers: frozenset) -> bool:
    for test in _gate_tests(node):
        for sub in ast.walk(test):
            ident = ""
            if isinstance(sub, ast.Attribute):
                ident = sub.attr
            elif isinstance(sub, ast.Name):
                ident = sub.id
            elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                ident = sub.value
            elif isinstance(sub, ast.keyword):
                ident = sub.arg or ""
            if ident in GATE_ATTRS:
                return True
            if isinstance(sub, ast.Call) and (
                _call_name(sub) in GATE_CALLS or _call_name(sub) in helpers
            ):
                return True
    return False


def _gate_helpers(tree: ast.Module) -> frozenset:
    """Same-module functions that are themselves an authorization decision.

    One transitive layer to a fixed point, exactly as ``_authz_helpers`` does
    for AUTHZ001/002: the real stapel-listings fix extracted the decision into
    ``_may_see_full_status(request, listing)``, and a rule that read a named
    predicate as absence would flag the remedy.
    """
    names = set()
    bodies: dict = {}
    for func, _cls in _functions(tree):
        bodies.setdefault(func.name, []).append(func)
        if _has_gate(func, frozenset()):
            names.add(func.name)
    for _ in range(3):
        grew = False
        for name, funcs in bodies.items():
            if name in names:
                continue
            for func in funcs:
                if _has_gate(func, frozenset(names)):
                    names.add(name)
                    grew = True
                    break
        if not grew:
            break
    return frozenset(names)


def _decorator_calls(func: ast.AST, name: str) -> list:
    return [
        d for d in getattr(func, "decorator_list", [])
        if isinstance(d, ast.Call) and _call_name(d) == name
    ]


def _kwarg(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _permission_names(node) -> Optional[list]:
    """Permission class names of a ``permission_classes`` list literal.

    ``None`` means "not declared here"; ``[]`` means an explicitly empty list,
    which in DRF is no permission at all.
    """
    if node is None:
        return None
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        # A computed value (``PERMS``, ``a + b``) is not read. Unknown, not open.
        return None
    return [_final_name(el) for el in node.elts]


def _class_permission_names(cls: Optional[ast.ClassDef]) -> Optional[list]:
    if cls is None:
        return None
    for sub in cls.body:
        if isinstance(sub, ast.Assign) and any(
            _final_name(t) == "permission_classes" for t in sub.targets
        ):
            return _permission_names(sub.value)
    return None


def _is_open(perms: Optional[list], read_only: bool) -> bool:
    """Can an UNAUTHENTICATED caller reach this handler?

    DRF ANDs the list, so ``[AllowAny, IsOwner]`` is *not* open — every entry
    has to be permissive for the handler to be.
    """
    if perms is None:
        # Undeclared. The project's DEFAULT_PERMISSION_CLASSES decides, and
        # settings are not resolved here — silence, not a guess.
        return False
    if not perms:
        return True
    allowed = OPEN_PERMISSIONS | (READ_OPEN_PERMISSIONS if read_only else frozenset())
    return all(p in allowed for p in perms)


def _handler_shape(func, cls) -> Optional[tuple]:
    """``(permission names, read_only)`` if ``func`` is an anonymous-reachable
    HTTP handler, else ``None``.

    Three shapes, and nothing else: a DRF ``@action``, a read handler on a
    view class, and an ``@api_view`` function. ``get_queryset`` and friends
    are deliberately not handlers — they are not what a URL routes to, and
    judging them would double every finding.
    """
    actions = _decorator_calls(func, "action")
    if actions:
        methods = _string_items(_kwarg(actions[0], "methods")) or ["get"]
        read_only = all(m.lower() in SAFE_HTTP_METHODS for m in methods)
        perms = _permission_names(_kwarg(actions[0], "permission_classes"))
        if perms is None:
            perms = _class_permission_names(cls)
        return perms, read_only

    api_views = _decorator_calls(func, "api_view")
    if api_views:
        methods = _string_items(api_views[0].args[0] if api_views[0].args else None)
        read_only = bool(methods) and all(
            m.lower() in SAFE_HTTP_METHODS for m in methods
        )
        perms = None
        for dec in _decorator_calls(func, "permission_classes"):
            perms = _permission_names(dec.args[0] if dec.args else None)
        return perms, read_only

    if cls is not None and func.name in READ_HANDLERS:
        return _class_permission_names(cls), True

    return None


def _unfiltered_reads(func: ast.AST) -> list:
    """``(lineno, source-ish name)`` for every escape of the default filter."""
    hits = []
    for sub in ast.walk(func):
        if isinstance(sub, ast.Attribute) and sub.attr in UNFILTERED_MANAGERS:
            hits.append((sub.lineno, sub.attr))
        elif isinstance(sub, ast.Call) and _call_name(sub) in UNFILTERED_QUERYSET_CALLS:
            hits.append((sub.lineno, f"{_call_name(sub)}()"))
    return hits


def _response_disclosures(func: ast.AST, index: dict) -> set:
    """Disclosing fields this handler can put on the wire.

    Two sources, both syntactic: a serializer class named anywhere in the
    handler (its body or its ``@extend_schema(responses=...)``) whose declared
    fields the index knows, and the string keys of a literal dict built in the
    body.
    """
    fields: set = set()
    for sub in ast.walk(func):
        if isinstance(sub, (ast.Name, ast.Attribute)):
            fields |= index.get(_final_name(sub), set())
    for stmt in getattr(func, "body", []):
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Dict):
                fields.update(
                    key.value for key in sub.keys
                    if isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and _is_disclosure_field(key.value)
                )
    return fields


def _check_authz006(tree: ast.Module, index: dict) -> list:
    helpers = _gate_helpers(tree)
    found = []
    for func, cls in _functions(tree):
        shape = _handler_shape(func, cls)
        if shape is None:
            continue
        perms, read_only = shape
        if not _is_open(perms, read_only):
            continue
        unfiltered = _unfiltered_reads(func)
        if not unfiltered:
            continue
        fields = _response_disclosures(func, index)
        if not fields:
            continue
        if _has_gate(func, helpers):
            # The handler decides who sees what. That IS the remedy — keep the
            # capability, narrow the disclosure — so it must not be flagged.
            continue
        perm_text = ", ".join(perms) if perms else "no permission_classes"
        found.append((
            func.lineno,
            func.name,
            perm_text,
            sorted({name for _line, name in unfiltered})[0],
            sorted(fields),
        ))
    return found


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------

_MSG_001 = (
    "{cls}.form_valid() runs AFTER the password was accepted and names no "
    "Admin* authentication form and reads no is_staff/is_superuser: the "
    "privilege gate does not exist on this path. This is the stapel-core "
    "0.38.0 bypass verbatim — a plain AuthenticationForm checks is_active "
    "only, so any active account's own password minted a fleet-wide JWT "
    "pair. Gate it in BOTH places: get_form_class() -> "
    "AdminAuthenticationForm, and an explicit refusal in form_valid() before "
    "login() and before any mint. Suppress a deliberate non-admin cookie "
    "login with '# noqa: AUTHZ001' and say why"
)

_MSG_002 = (
    "{func}() verifies a credential (login()/form.get_user()) and mints "
    "tokens ({mint}) with no authorization read before the mint. A correct "
    "password is proof of identity, not a grant of access, and a token "
    "outlives the request that issued it — read is_staff/is_superuser (or a "
    "named predicate in this module) and refuse BEFORE the mint. Suppress "
    "with '# noqa: AUTHZ002' where the caller has already decided"
)

_MSG_003 = (
    "refresh_access_token(..., None) re-mints from the presented token's OWN "
    "claims, which are as old as the refresh token (JWT_REFRESH_TOKEN_LIFETIME, "
    "7 days by default): a staff flag revoked in between comes back to life, "
    "and a deactivated account gets a fresh access token. Pass the database "
    "loader (load_user_by_uid) unless this caller is framework-free — and if "
    "it is, keep the explicit None and mark it '# noqa: AUTHZ003' with the "
    "reason"
)

_MSG_004 = (
    "{cls}.get_user() returns objects.get() with no is_active / "
    "user_can_authenticate check. get_user is what resolves request.user "
    "from the session on EVERY request after the one that authenticated, so "
    "an override that drops the check ModelBackend.get_user performs lets a "
    "deactivated account keep a live session for the whole life of the "
    "session cookie — deactivation would only take effect at the next login, "
    "which is precisely the login that will not happen"
)

_MSG_005 = (
    "revocation cache.{op}() through django.core.cache.cache — Django builds "
    "the real key from THIS deployment's KEY_PREFIX, which every service in a "
    "split deployment sets differently, so the entry is invisible to peers "
    "sharing the same Redis (auth wrote auth:1:jwt_blacklist:<jti>, profiles "
    "read stapel_profiles:1:jwt_blacklist:<jti>). Use the fleet-wide "
    "namespace: stapel_core.core.revocation_store.revocation_cache()"
)

_MSG_006 = (
    "{func}() is reachable with no authentication ({perm}), reads {mgr} — the "
    "manager that deliberately bypasses the default visibility filter — and "
    "answers with {fields}. Each half is ordinary alone; together they are an "
    "enumeration oracle. Ids are usually sequential, so an anonymous walk of "
    "them harvests who owns every row in the deployment and what a moderator "
    "decided about it, other people's drafts, rejected and soft-deleted rows "
    "included. This is stapel-listings 0.8.0 verbatim: GET "
    "/listings/{{pk}}/status/ was AllowAny over Listing.all_objects returning "
    "owner_id and moderation_status, and it was found by walking the ids "
    "against a live stand. The fix is NOT to delete the endpoint or lock it to "
    "services: a real browser client needs this probe to tell 'removed' from "
    "'never existed', and locking it broke the product. KEEP THE CAPABILITY, "
    "REMOVE THE DISCLOSURE — full view for a fleet service and for the row's "
    "own owner, one boolean for everyone else (a signed-in stranger is still "
    "a stranger, or the oracle costs an attacker one free account). Branch on "
    "ownership/permission in this function, or call a named predicate in this "
    "module, and the rule goes quiet, because that branch IS the fix. WHAT "
    "THIS RULE CANNOT SEE: it reads statically declared serializer fields and "
    "literal dict keys only — a serializer chosen at runtime, a response built "
    "from **kwargs or a comprehension, fields='__all__', and a column aliased "
    "to an innocuous name are all invisible to it, so silence here is not "
    "proof the response is clean. Suppress with '# noqa: AUTHZ006' and write "
    "the reason"
)


# ---------------------------------------------------------------------------
# lint driver
# ---------------------------------------------------------------------------


def _parse(path: Path):
    try:
        src = path.read_text(encoding="utf-8")
        return src, ast.parse(src, filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return None, None


def build_serializer_index(root: Path) -> dict:
    """Serializer-name -> disclosing fields, across a whole tree.

    AUTHZ006 needs it because the view names ``ListingStatusSerializer`` and
    the fields live in ``serializers.py`` next door. There is no import
    resolution: the index is keyed by the bare class name and a collision
    merges. Linting a SINGLE FILE builds the index from that file alone, so a
    serializer defined elsewhere is invisible — the documented cost of not
    building a cross-module symbol table in a linter.
    """
    index: dict = {}
    for py in _walk_py(root):
        _src, tree = _parse(py)
        if tree is None:
            continue
        for name, fields in _serializer_disclosures(tree).items():
            index.setdefault(name, set()).update(fields)
    return index


def lint_file(path: Path, serializer_index: Optional[dict] = None) -> list:
    src, tree = _parse(path)
    if tree is None:
        return []
    if serializer_index is None:
        serializer_index = _serializer_disclosures(tree)

    lines = src.splitlines()
    helpers = _authz_helpers(tree)
    raw: list = []

    for line, cls_name in _check_authz001(tree, helpers):
        raw.append(Violation(str(path), line, "AUTHZ001", _MSG_001.format(cls=cls_name)))
    for line, func_name in _check_authz002(tree, helpers):
        raw.append(Violation(
            str(path), line, "AUTHZ002",
            _MSG_002.format(func=func_name, mint="create_tokens/set_jwt_cookies"),
        ))
    for line in _check_authz003(tree):
        raw.append(Violation(str(path), line, "AUTHZ003", _MSG_003, level="warning"))
    for line, cls_name in _check_authz004(tree):
        raw.append(Violation(str(path), line, "AUTHZ004", _MSG_004.format(cls=cls_name)))
    for line, op in _check_authz005(tree):
        raw.append(Violation(str(path), line, "AUTHZ005", _MSG_005.format(op=op)))
    for line, func_name, perm, mgr, fields in _check_authz006(tree, serializer_index):
        raw.append(Violation(
            str(path), line, "AUTHZ006",
            _MSG_006.format(
                func=func_name, perm=perm, mgr=mgr, fields=", ".join(fields),
            ),
        ))

    violations = []
    for violation in raw:
        text = lines[violation.line - 1] if 0 < violation.line <= len(lines) else ""
        suppressed = _noqa_rules(text)
        if suppressed is not None and (not suppressed or violation.rule in suppressed):
            continue
        violations.append(violation)
    return violations


def lint_project(project: Path, notes: Optional[list] = None) -> list:
    project = Path(project).resolve()
    violations: list = []
    scanned = 0
    index = build_serializer_index(project)
    for py in _walk_py(project):
        scanned += 1
        violations.extend(lint_file(py, index))
    if notes is not None:
        notes.append(f"stapel-authz-lint: {scanned} python file(s) scanned")
    violations.sort(key=lambda v: (v.path, v.line, v.rule))
    return violations


def lint_paths(paths: Iterable) -> list:
    violations: list = []
    for raw in paths:
        root = Path(raw)
        if not root.exists():
            raise SystemExit(f"Error: path does not exist: {root}")
        if root.is_file():
            # AUTHZ006 needs the serializers that live NEXT TO the view, so a
            # single-file run indexes the file's own directory. Wider than the
            # module, narrower than the repo, and it makes `stapel-authz-lint
            # views.py` behave the way the caller expects.
            violations.extend(
                lint_file(root, build_serializer_index(root.parent))
            )
        else:
            violations.extend(lint_project(root))
    violations.sort(key=lambda v: (v.path, v.line, v.rule))
    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-authz-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths", nargs="*", default=["."],
        help="Project roots, module repos or single files to lint (default: .)",
    )
    parser.add_argument("--json", action="store_true", help="Machine output")
    parser.add_argument(
        "--strict", action="store_true",
        help="Promote AUTHZ003 (explicit re-mint from token claims) to an error",
    )
    args = parser.parse_args(argv)

    violations = lint_paths(args.paths)
    errors = [
        v for v in violations
        if v.level == "error" or (args.strict and v.level == "warning")
    ]
    warnings = [v for v in violations if v not in errors]

    if args.json:
        print(json.dumps(
            {
                "ok": not errors,
                "errors": len(errors),
                "warnings": len(warnings),
                "violations": [v.to_dict() for v in violations],
            },
            indent=2, sort_keys=True, ensure_ascii=False,
        ))
    else:
        for violation in violations:
            print(violation)
        if violations:
            print(f"\n{len(errors)} error(s), {len(warnings)} warning(s) found.")
        else:
            print("No violations found.")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
