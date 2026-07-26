"""
stapel-nginx-cache-lint — the SPA cache canon gate (owner directive,
2026-07-26), in the ``stapel-config-lint`` / ``stapel-url-lint`` idiom (rule
codes, ``--json``, ``--strict``, exit 1 on any error).

The canon has exactly two halves, and they are opposites on purpose:

* The thin, UNHASHED entry document — ``index.html`` and the SPA-fallback
  location that serves it — must NEVER be cached. It is the only file that
  points at the current build's hashed URLs, so caching it means a deploy
  does not reach users until that cache expires.
* Content-hashed build artifacts (``/assets/<name>-<hash>.js|css``) are
  content-addressed — a new deploy is a new URL — so they must be cached
  long and ``immutable``.

The incident that made this machine-checkable (app.ironmemo.com stand): the
entry document was served with BOTH ``expires 1d`` AND
``add_header Cache-Control "public, must-revalidate"``. nginx emits its own
``Cache-Control`` for ``expires`` and appends yours on top, so the response
carried TWO ``Cache-Control`` headers; per RFC 9111 a client combines them
into ``max-age=86400, public, must-revalidate`` and honours the max-age. A
freshly deployed frontend fix stayed invisible for up to 24 hours — and a
live verification of that fix read the STALE bundle and drew the wrong
conclusion. The scaffold template was fixed; this gate is what catches the
same shape in a project that ALREADY exists, including hand-maintained confs
the scaffold never wrote.

Rules
-----
NGX001  (error) An entry-document / SPA-fallback location is CACHEABLE — it
        carries an ``expires`` with a positive lifetime, and/or a
        ``Cache-Control`` with a positive ``max-age``, and nothing anywhere in
        its effective header set forces revalidation (``no-cache`` /
        ``no-store`` / ``max-age=0``). A deploy does not reach users.
        Canon shape: ``expires off;`` +
        ``add_header Cache-Control "no-cache, must-revalidate" always;``.

NGX002  (error) A hashed-asset location is NOT immutable — its effective
        ``Cache-Control`` lacks ``immutable`` and/or caches for less than a
        day. Hashed artifacts are content-addressed; anything short of
        long+immutable pays a revalidation round-trip per asset per load for
        no benefit. Canon shape: ``expires off;`` +
        ``add_header Cache-Control "public, max-age=31536000, immutable" always;``.

NGX003  (error) A location emits BOTH an ``expires`` directive AND an explicit
        ``add_header Cache-Control`` (or two explicit ``Cache-Control``
        add_headers) — the double-header defect above. The response carries
        two ``Cache-Control`` headers and what the browser actually does is
        the *union*, not the one you wrote last. This fires on ANY location,
        entry document or not, cacheable or not: the ambiguity is the defect.
        Fix: ``expires off;`` (nginx then adds nothing) and keep exactly one
        explicit ``add_header Cache-Control``.

NGX004  (warning) An entry-document / SPA-fallback location declares NO cache
        policy at all — no ``expires``, no ``Cache-Control``. Not a
        double-header defect and not an explicit long cache, but the response
        then carries no ``Cache-Control`` either, and a browser is free to
        apply *heuristic* freshness (RFC 9111 §4.2.2) off ``Last-Modified``
        and serve a stale entry document without revalidating. Warning-level
        (same posture as CFG004/DOC001) because it is a silent risk rather
        than a live-stale guarantee, and because pre-canon projects have it
        everywhere; make it explicit with the canon shape above.

Nginx semantics this encodes
-----------------------------
* ``expires off`` makes nginx add NEITHER ``Expires`` NOR ``Cache-Control``
  — it is the only value that composes safely with an explicit add_header.
  ``expires -1`` / ``epoch`` emit ``no-cache`` (non-cacheable, but they still
  count as an emitted header for NGX003).
* ``expires`` is inherited from the enclosing ``server``/``http`` level; a
  location that declares none inherits one, and it then collides with that
  location's own ``add_header Cache-Control`` exactly the same way.
* ``add_header`` does NOT merge: a location that declares ANY ``add_header``
  REPLACES the whole inherited set. Inheritance is modelled accordingly.

What this deliberately does NOT check
--------------------------------------
* Directives inside nested ``if``/``limit_except`` blocks — nginx's own
  header inheritance there is famously surprising, and every such block in
  practice sits in a ``proxy_pass`` location that neither rule targets.
  Counting them would trade a real defect class for false positives.
* Whether a location's files are ACTUALLY content-hashed. NGX002 fires on the
  build-output naming canon (an ``assets``/``_next``/``_nuxt``/``chunks`` path
  segment, or a ``static`` segment on a location that also constrains the
  extension to js/css/fonts/images). A plain ``location /static/`` (Django
  collectstatic without a manifest storage) and ``location /media/`` (user
  uploads — never hashed) are NOT treated as hashed assets.
* Anything served by ``proxy_pass``: cache policy for an upstream belongs to
  the upstream, and a gateway location has no entry document of its own.

Suppression: ``# noqa`` (blanket) or ``# noqa: NGX002`` on the location's
opening line, or on the offending directive's line.

Live mode
---------
``--live BASE_URL`` checks the headers a deployed stand ACTUALLY serves,
which is the half a static check cannot reach (a fixed conf that was never
deployed, or a CDN/proxy in front rewriting them). It fetches the entry
document, asserts it revalidates, extracts the first hashed asset the
document itself references, and asserts that asset is immutable. Same rule
codes, so one vocabulary. No third-party dependency — stdlib urllib.

Exit codes: 0 clean (warnings allowed), 1 errors present (``--strict`` also
fails on warnings), 2 usage/environment errors.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------


@dataclass
class Finding:
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
# nginx config tokenizer / parser
#
# Modelled on nginx's own lexer (ngx_conf_read_token): `;`, `{` and `}` are
# token delimiters that terminate the word being read, EXCEPT for the `${name}`
# variable-brace form, which nginx tracks explicitly and which appears in every
# envsubst-rendered `*.conf.template`. A regex containing braces must be quoted
# in a real nginx conf (nginx documents this), and quoted tokens are read whole.
# ---------------------------------------------------------------------------


@dataclass
class _Token:
    text: str
    line: int
    quoted: bool


def _tokenize(src: str) -> list[_Token]:
    tokens: list[_Token] = []
    i, n, line = 0, len(src), 1
    buf = ""
    buf_line = 1
    in_word = False
    in_var_brace = False

    def flush() -> None:
        nonlocal buf, in_word
        if in_word:
            tokens.append(_Token(buf, buf_line, False))
            buf = ""
            in_word = False

    while i < n:
        ch = src[i]

        if ch == "\n":
            flush()
            line += 1
            i += 1
            continue

        if ch in " \t\r" and not in_var_brace:
            flush()
            i += 1
            continue

        if ch == "#" and not in_word:
            while i < n and src[i] != "\n":
                i += 1
            continue

        if ch in "\"'" and not in_word:
            quote = ch
            start_line = line
            i += 1
            val = ""
            while i < n:
                c = src[i]
                if c == "\\" and i + 1 < n:
                    val += src[i + 1]
                    i += 2
                    continue
                if c == quote:
                    i += 1
                    break
                if c == "\n":
                    line += 1
                val += c
                i += 1
            tokens.append(_Token(val, start_line, True))
            continue

        # ${VAR} — the brace belongs to the word, not to the block structure.
        if ch == "{" and in_word and buf.endswith("$"):
            in_var_brace = True
            buf += ch
            i += 1
            continue
        if in_var_brace:
            if ch == "}":
                in_var_brace = False
            buf += ch
            i += 1
            continue

        if ch in "{};":
            flush()
            tokens.append(_Token(ch, line, False))
            i += 1
            continue

        if not in_word:
            in_word = True
            buf = ""
            buf_line = line
        buf += ch
        i += 1

    flush()
    return tokens


@dataclass
class Directive:
    name: str
    args: list[str]
    line: int


@dataclass
class Block:
    name: str
    args: list[str]
    line: int
    directives: list[Directive] = field(default_factory=list)
    blocks: list["Block"] = field(default_factory=list)
    parent: Optional["Block"] = None

    def own(self, name: str) -> list[Directive]:
        return [d for d in self.directives if d.name == name]

    def ancestors(self) -> Iterable["Block"]:
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class NginxParseError(ValueError):
    pass


def parse_conf(src: str) -> Block:
    """Parse an nginx conf into a block tree. Raises NginxParseError on
    unbalanced braces."""
    root = Block(name="", args=[], line=1)
    stack = [root]
    pending: list[_Token] = []

    for tok in _tokenize(src):
        if not tok.quoted and tok.text == ";":
            if pending:
                stack[-1].directives.append(
                    Directive(pending[0].text, [t.text for t in pending[1:]], pending[0].line)
                )
                pending = []
            continue
        if not tok.quoted and tok.text == "{":
            if not pending:
                raise NginxParseError(f"line {tok.line}: '{{' with no directive name")
            block = Block(
                name=pending[0].text,
                args=[t.text for t in pending[1:]],
                line=pending[0].line,
                parent=stack[-1],
            )
            stack[-1].blocks.append(block)
            stack.append(block)
            pending = []
            continue
        if not tok.quoted and tok.text == "}":
            if len(stack) == 1:
                raise NginxParseError(f"line {tok.line}: unbalanced '}}'")
            stack.pop()
            continue
        pending.append(tok)

    if len(stack) != 1:
        raise NginxParseError(f"unclosed block '{stack[-1].name}' opened at line {stack[-1].line}")
    return root


def iter_locations(block: Block) -> Iterable[Block]:
    """Every ``location`` block in the tree, nested ones included."""
    for child in block.blocks:
        if child.name == "location":
            yield child
        yield from iter_locations(child)


# ---------------------------------------------------------------------------
# cache-header semantics
# ---------------------------------------------------------------------------

_TIME_UNITS = {
    "ms": 0.001, "s": 1, "m": 60, "h": 3600,
    "d": 86400, "w": 604800, "M": 2592000, "y": 31536000,
}
_TIME_RE = re.compile(r"(\d+)(ms|[smhdwMy])?")


def parse_nginx_time(value: str) -> Optional[int]:
    """nginx time value -> seconds. ``1d`` -> 86400, ``1h30m`` -> 5400.
    None when the value is not a plain time (a variable, a garbled token)."""
    text = value.strip()
    negative = text.startswith("-")
    if negative or text.startswith("+"):
        text = text[1:]
    if not text or not text[0].isdigit():
        return None
    total = 0.0
    pos = 0
    while pos < len(text):
        match = _TIME_RE.match(text, pos)
        if not match or match.start() != pos:
            return None
        total += int(match.group(1)) * _TIME_UNITS[match.group(2) or "s"]
        pos = match.end()
    seconds = int(total)
    return -seconds if negative else seconds


@dataclass
class CachePolicy:
    """The effective response cache policy of one location: what the client
    would actually see, across every source that contributes a header."""

    #: seconds from the largest positive max-age contributed by any source
    max_age: Optional[int] = None
    no_cache: bool = False
    no_store: bool = False
    immutable: bool = False
    must_revalidate: bool = False
    #: True when at least one source emits a Cache-Control header at all
    declared: bool = False

    @property
    def revalidates(self) -> bool:
        """The client is forced back to the origin on every use."""
        return self.no_cache or self.no_store or self.max_age == 0

    @property
    def cacheable(self) -> bool:
        """The client may reuse a stored response without asking."""
        return not self.revalidates and (self.max_age or 0) > 0


def parse_cache_control(value: str) -> CachePolicy:
    policy = CachePolicy(declared=True)
    for raw in value.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token == "no-cache" or token.startswith("no-cache="):
            policy.no_cache = True
        elif token == "no-store":
            policy.no_store = True
        elif token == "immutable":
            policy.immutable = True
        elif token == "must-revalidate":
            policy.must_revalidate = True
        elif token.startswith("max-age=") or token.startswith("s-maxage="):
            try:
                seconds = int(token.split("=", 1)[1].strip('"'))
            except ValueError:
                continue
            policy.max_age = seconds if policy.max_age is None else max(policy.max_age, seconds)
    return policy


def _merge(into: CachePolicy, other: CachePolicy) -> CachePolicy:
    """Combine two Cache-Control sources the way a client combines two
    Cache-Control header instances (RFC 9111 §5.2: the union of directives)."""
    return CachePolicy(
        max_age=(
            other.max_age if into.max_age is None
            else into.max_age if other.max_age is None
            else max(into.max_age, other.max_age)
        ),
        no_cache=into.no_cache or other.no_cache,
        no_store=into.no_store or other.no_store,
        immutable=into.immutable or other.immutable,
        must_revalidate=into.must_revalidate or other.must_revalidate,
        declared=into.declared or other.declared,
    )


def expires_policy(value: str) -> Optional[CachePolicy]:
    """The Cache-Control an ``expires`` directive makes nginx emit.
    None = nginx emits nothing (``expires off``) or the value is a variable
    we cannot evaluate statically."""
    text = value.strip()
    if text in ("off", ""):
        return None
    if text.startswith("$"):
        return None
    if text == "epoch":
        return CachePolicy(max_age=0, no_cache=True, declared=True)
    if text == "max":
        return CachePolicy(max_age=_TIME_UNITS["y"] * 10, declared=True)
    if text.startswith("@"):
        # `expires @15h30m` — an absolute time of day; cacheable until then.
        return CachePolicy(max_age=1, declared=True)
    seconds = parse_nginx_time(text)
    if seconds is None:
        return None
    if seconds < 0:
        return CachePolicy(max_age=0, no_cache=True, declared=True)
    return CachePolicy(max_age=seconds, declared=True)


# ---------------------------------------------------------------------------
# location classification
# ---------------------------------------------------------------------------

#: path segments that name a build-output directory (content-hashed by the
#: bundler: vite/CRA `assets`, next `_next`, nuxt `_nuxt`, sveltekit
#: `immutable`, generic `chunks`)
_HASHED_DIR_SEGMENTS = {"assets", "_next", "_nuxt", "chunks", "immutable"}
#: `static` is hashed only under a manifest storage — accepted as a hashed
#: location ONLY when the location also constrains the extension (the
#: build-artifact regex shape), never as a bare `location /static/`.
_AMBIGUOUS_DIR_SEGMENTS = {"static"}
_ASSET_EXTENSIONS = {"js", "mjs", "cjs", "css", "woff", "woff2", "map"}

_LOCATION_MODIFIERS = {"=", "~", "~*", "^~"}


def location_pattern(loc: Block) -> str:
    args = [a for a in loc.args if a not in _LOCATION_MODIFIERS]
    return args[0] if args else ""


def location_label(loc: Block) -> str:
    return "location " + " ".join(loc.args)


def _segments(pattern: str) -> set[str]:
    return {s.lower() for s in re.findall(r"[A-Za-z0-9_]+", pattern)}


def serves_from_disk(loc: Block) -> bool:
    """A location backed by the filesystem (root/alias), not an upstream."""
    if loc.own("proxy_pass") or loc.own("fastcgi_pass") or loc.own("uwsgi_pass"):
        return False
    return bool(loc.own("root") or loc.own("alias") or loc.own("try_files"))


def is_entry_document(loc: Block) -> bool:
    """The thin unhashed document a browser loads to discover the current
    build: an SPA fallback (``try_files ... /index.html``), a location that
    matches an .html file directly, or a directory index."""
    if not serves_from_disk(loc):
        return False
    for directive in loc.own("try_files"):
        for arg in directive.args:
            if arg.split("?")[0].endswith(".html"):
                return True
    pattern = location_pattern(loc)
    if pattern.endswith(".html") or pattern.endswith(".html$"):
        return True
    for directive in loc.own("index"):
        if any(a.endswith(".html") for a in directive.args):
            return True
    return False


def is_hashed_asset(loc: Block) -> bool:
    """A location serving content-addressed build output — a new build writes
    a new URL, so the old one can be cached forever."""
    if not serves_from_disk(loc):
        return False
    pattern = location_pattern(loc)
    segments = _segments(pattern)
    if segments & _HASHED_DIR_SEGMENTS:
        return True
    if segments & _AMBIGUOUS_DIR_SEGMENTS:
        # only when the location also pins build-artifact extensions
        return bool(segments & _ASSET_EXTENSIONS)
    return False


# ---------------------------------------------------------------------------
# effective header resolution (with nginx's inheritance rules)
# ---------------------------------------------------------------------------


@dataclass
class LocationCache:
    """Every Cache-Control source that applies to one location."""

    #: the `expires` directive in effect (own, else inherited)
    expires: Optional[Directive] = None
    #: True when `expires` came from an enclosing server/http block
    expires_inherited: bool = False
    #: `add_header Cache-Control` directives in effect
    cache_control_headers: list[Directive] = field(default_factory=list)
    #: True when those add_headers came from an enclosing block
    headers_inherited: bool = False

    @property
    def emits_expires_header(self) -> bool:
        return self.expires is not None and expires_policy(" ".join(self.expires.args)) is not None

    def policy(self) -> CachePolicy:
        policy = CachePolicy()
        if self.expires is not None:
            from_expires = expires_policy(" ".join(self.expires.args))
            if from_expires is not None:
                policy = _merge(policy, from_expires)
        for directive in self.cache_control_headers:
            policy = _merge(policy, parse_cache_control(header_value(directive)))
        return policy


def header_value(directive: Directive) -> str:
    """``add_header <name> <value> [always]`` — the VALUE only. nginx takes
    exactly one value argument (a multi-word value must be quoted, and a
    quoted token is read whole), so the optional trailing ``always`` flag is
    never part of it."""
    return directive.args[1] if len(directive.args) > 1 else ""


def _cache_control_add_headers(block: Block) -> list[Directive]:
    return [
        d for d in block.own("add_header")
        if d.args and d.args[0].lower() == "cache-control"
    ]


def resolve_cache(loc: Block) -> LocationCache:
    resolved = LocationCache()

    own_expires = loc.own("expires")
    if own_expires:
        resolved.expires = own_expires[-1]
    else:
        for ancestor in loc.ancestors():
            inherited = ancestor.own("expires")
            if inherited:
                resolved.expires = inherited[-1]
                resolved.expires_inherited = True
                break

    # add_header does NOT merge: any add_header in this block replaces the
    # whole inherited set, so inheritance only applies to a block that
    # declares none of its own.
    if loc.own("add_header"):
        resolved.cache_control_headers = _cache_control_add_headers(loc)
    else:
        for ancestor in loc.ancestors():
            if ancestor.own("add_header"):
                resolved.cache_control_headers = _cache_control_add_headers(ancestor)
                resolved.headers_inherited = True
                break

    return resolved


# ---------------------------------------------------------------------------
# noqa
# ---------------------------------------------------------------------------


def _noqa_rules(line: str) -> Optional[set[str]]:
    """None when no noqa; empty set = blanket noqa; else the listed rules."""
    if "# noqa" not in line:
        return None
    if "# noqa:" not in line:
        return set()
    tail = line.split("# noqa:", 1)[1]
    return {r.strip().upper() for r in tail.replace(";", ",").split(",") if r.strip()}


def _suppressed(lines: list[str], rule: str, *line_numbers: int) -> bool:
    for number in line_numbers:
        if not (0 < number <= len(lines)):
            continue
        rules = _noqa_rules(lines[number - 1])
        if rules is not None and (not rules or rule in rules):
            return True
    return False


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------

CANON_ENTRY = (
    'expires off; add_header Cache-Control "no-cache, must-revalidate" always;'
)
CANON_ASSET = (
    'expires off; add_header Cache-Control "public, max-age=31536000, immutable" always;'
)
#: below this, "long-lived" is not a fair description of a hashed artifact
MIN_IMMUTABLE_MAX_AGE = 86400


def lint_conf(path: Path, src: Optional[str] = None) -> list[Finding]:
    """Every cache finding in one nginx conf file."""
    text = src if src is not None else path.read_text(encoding="utf-8")
    lines = text.splitlines()
    root = parse_conf(text)
    findings: list[Finding] = []
    name = str(path)

    def add(rule: str, line: int, message: str, level: str = "error", *anchors: int) -> None:
        if _suppressed(lines, rule, line, *anchors):
            return
        findings.append(Finding(name, line, rule, message, level))

    for loc in iter_locations(root):
        label = location_label(loc)
        resolved = resolve_cache(loc)
        policy = resolved.policy()

        # ---------------------------------------------------------- NGX003
        # Two Cache-Control headers on the wire. Checked on EVERY location:
        # the ambiguity is the defect, independent of what is being served.
        expires_directive = resolved.expires
        if resolved.emits_expires_header and resolved.cache_control_headers:
            header = resolved.cache_control_headers[0]
            origin = (
                f" (inherited from the enclosing block, line {expires_directive.line})"
                if resolved.expires_inherited else ""
            )
            add(
                "NGX003", header.line,
                f"{label}: `expires {' '.join(expires_directive.args)}`"
                f"{origin} AND an explicit `add_header Cache-Control` — nginx emits "
                f"BOTH headers and a client combines them (RFC 9111 §5.2), so the "
                f"response really means the UNION of the two, not the one written "
                f"last. This is the app.ironmemo.com defect: `expires 1d` + "
                f'"public, must-revalidate" cached the entry document for 24h. '
                f"Use `expires off;` (nginx then adds nothing) and keep exactly one "
                f"explicit add_header.",
                "error", loc.line, expires_directive.line,
            )
        elif len(resolved.cache_control_headers) > 1 and not resolved.headers_inherited:
            add(
                "NGX003", resolved.cache_control_headers[1].line,
                f"{label}: {len(resolved.cache_control_headers)} `add_header "
                f"Cache-Control` directives in one block — nginx emits every one of "
                f"them and a client takes the union, not the last. Collapse them "
                f"into a single header.",
                "error", loc.line,
            )

        anchor = loc.line
        if expires_directive is not None and not resolved.expires_inherited:
            anchor = expires_directive.line

        # ---------------------------------------------------------- NGX001
        if is_entry_document(loc):
            if policy.cacheable:
                sources = []
                if resolved.emits_expires_header and expires_directive is not None:
                    sources.append(f"`expires {' '.join(expires_directive.args)}`")
                for directive in resolved.cache_control_headers:
                    sources.append(f"`Cache-Control: {header_value(directive)}`")
                add(
                    "NGX001", anchor,
                    f"{label} serves the unhashed entry document (SPA fallback / "
                    f"index.html) but is CACHEABLE for {policy.max_age}s via "
                    f"{' + '.join(sources)} — nothing here forces revalidation. The "
                    f"entry document is the only file that points at the current "
                    f"build's hashed URLs, so a deploy stays invisible to returning "
                    f"users until this expires. Canon: {CANON_ENTRY}",
                    "error", loc.line,
                )
            # ------------------------------------------------------ NGX004
            elif not policy.declared:
                add(
                    "NGX004", loc.line,
                    f"{label} serves the unhashed entry document but declares no "
                    f"cache policy at all — the response carries no Cache-Control, "
                    f"so a client may apply heuristic freshness (RFC 9111 §4.2.2) "
                    f"off Last-Modified and reuse a stale entry document without "
                    f"revalidating. Make it explicit: {CANON_ENTRY}",
                    "warning",
                )

        # ---------------------------------------------------------- NGX002
        if is_hashed_asset(loc):
            reasons = []
            if not policy.immutable:
                reasons.append("no `immutable`")
            if policy.revalidates:
                reasons.append("forced to revalidate on every load")
            elif (policy.max_age or 0) < MIN_IMMUTABLE_MAX_AGE:
                reasons.append(
                    f"max-age={policy.max_age if policy.declared else 'unset'} "
                    f"(< {MIN_IMMUTABLE_MAX_AGE}s)"
                )
            if reasons:
                add(
                    "NGX002", anchor,
                    f"{label} serves content-hashed build artifacts but is not "
                    f"cached immutably: {', '.join(reasons)}. A hashed filename is "
                    f"content-addressed — a new build is a new URL — so every load "
                    f"pays a revalidation round-trip for nothing. Canon: "
                    f"{CANON_ASSET}",
                    "error", loc.line,
                )

    findings.sort(key=lambda f: (f.line, f.rule))
    return findings


# ---------------------------------------------------------------------------
# project discovery
# ---------------------------------------------------------------------------

#: where a stapel project keeps its nginx confs (prod dir + the envsubst
#: local-dev dir, §57 local-nginx canon)
CONF_GLOBS = (
    "service-configs/nginx*/**/*.conf",
    "service-configs/nginx*/**/*.conf.template",
    "service-configs/nginx*/*.conf",
    "service-configs/nginx*/*.conf.template",
)


def discover_confs(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    found: set[Path] = set()
    for pattern in CONF_GLOBS:
        found.update(p for p in target.glob(pattern) if p.is_file())
    return sorted(found)


def lint_project(target: Path, *, notes: Optional[list[str]] = None) -> list[Finding]:
    if notes is None:
        notes = []
    confs = discover_confs(target)
    if not confs:
        notes.append(
            "stapel-nginx-cache-lint: no nginx conf found under "
            "service-configs/nginx*/ — nothing to check."
        )
        return []
    findings: list[Finding] = []
    for conf in confs:
        try:
            findings.extend(lint_conf(conf))
        except NginxParseError as exc:
            notes.append(f"stapel-nginx-cache-lint: {conf}: unparseable ({exc}) — skipped.")
        except (OSError, UnicodeDecodeError) as exc:
            notes.append(f"stapel-nginx-cache-lint: {conf}: unreadable ({exc}) — skipped.")
    findings.sort(key=lambda f: (f.path, f.line, f.rule))
    return findings


# ---------------------------------------------------------------------------
# live mode — what a deployed stand actually serves
# ---------------------------------------------------------------------------

_ASSET_REF_RE = re.compile(
    r"""(?:src|href)\s*=\s*["']([^"']*/(?:assets|_next|_nuxt|chunks)/[^"']+"""
    r"""\.(?:js|mjs|css))["']""",
    re.IGNORECASE,
)


def extract_hashed_asset(html: str) -> Optional[str]:
    """The first hashed build artifact the entry document itself references —
    the one URL we know is content-addressed without guessing."""
    match = _ASSET_REF_RE.search(html)
    return match.group(1) if match else None


def evaluate_live(
    entry_url: str,
    entry_headers: list[str],
    asset_url: Optional[str],
    asset_headers: Optional[list[str]],
) -> list[Finding]:
    """Rules NGX001-NGX003 against headers actually observed on the wire.
    Pure — the caller does the I/O."""
    findings: list[Finding] = []

    if len(entry_headers) > 1:
        findings.append(Finding(
            entry_url, 0, "NGX003",
            f"entry document returned {len(entry_headers)} Cache-Control headers "
            f"({entry_headers!r}) — a client takes their union, not the last. This "
            f"is the nginx `expires` + `add_header Cache-Control` double-header "
            f"defect, live on the stand.",
        ))

    entry_policy = CachePolicy()
    for value in entry_headers:
        entry_policy = _merge(entry_policy, parse_cache_control(value))

    if entry_policy.cacheable:
        findings.append(Finding(
            entry_url, 0, "NGX001",
            f"entry document is served cacheable for {entry_policy.max_age}s "
            f"(Cache-Control: {', '.join(entry_headers)}) — a deploy will not reach "
            f"returning users until that expires, and any live verification against "
            f"this URL may read a stale bundle. Canon: {CANON_ENTRY}",
        ))
    elif not entry_policy.declared:
        findings.append(Finding(
            entry_url, 0, "NGX004",
            "entry document is served with no Cache-Control header at all — a "
            "client may apply heuristic freshness and reuse it without "
            f"revalidating. Canon: {CANON_ENTRY}",
            level="warning",
        ))

    if asset_url is None:
        findings.append(Finding(
            entry_url, 0, "NGX002",
            "could not find a hashed build artifact referenced by the entry "
            "document (no /assets/*.js|css in the HTML) — the immutable half of "
            "the canon went unchecked.",
            level="warning",
        ))
        return findings

    if len(asset_headers or []) > 1:
        findings.append(Finding(
            asset_url, 0, "NGX003",
            f"hashed asset returned {len(asset_headers or [])} Cache-Control "
            f"headers ({asset_headers!r}) — a client takes their union.",
        ))

    asset_policy = CachePolicy()
    for value in asset_headers or []:
        asset_policy = _merge(asset_policy, parse_cache_control(value))

    reasons = []
    if not asset_policy.immutable:
        reasons.append("no `immutable`")
    if asset_policy.revalidates:
        reasons.append("forced to revalidate on every load")
    elif (asset_policy.max_age or 0) < MIN_IMMUTABLE_MAX_AGE:
        reasons.append(
            f"max-age={asset_policy.max_age if asset_policy.declared else 'unset'} "
            f"(< {MIN_IMMUTABLE_MAX_AGE}s)"
        )
    if reasons:
        findings.append(Finding(
            asset_url, 0, "NGX002",
            f"hashed build artifact is not cached immutably: {', '.join(reasons)} "
            f"(Cache-Control: {', '.join(asset_headers or []) or 'absent'}). "
            f"Canon: {CANON_ASSET}",
        ))

    return findings


def _fetch(url: str, timeout: float) -> tuple[list[str], str]:
    """(all Cache-Control header instances, body). Kept tiny and stdlib-only —
    stapel-tools has zero runtime dependencies on purpose."""
    from urllib.request import Request, urlopen

    request = Request(url, headers={"User-Agent": "stapel-nginx-cache-lint"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 (explicit URL)
        raw = response.read()
        headers = response.headers.get_all("Cache-Control") or []
    charset = "utf-8"
    try:
        body = raw.decode(charset, errors="replace")
    except LookupError:
        body = raw.decode("utf-8", errors="replace")
    return list(headers), body


def lint_live(base_url: str, *, timeout: float = 10.0) -> list[Finding]:
    from urllib.parse import urljoin

    entry_url = base_url if base_url.endswith("/") else base_url + "/"
    entry_headers, html = _fetch(entry_url, timeout)

    asset_ref = extract_hashed_asset(html)
    asset_url = urljoin(entry_url, asset_ref) if asset_ref else None
    asset_headers = None
    if asset_url is not None:
        asset_headers, _ = _fetch(asset_url, timeout)

    return evaluate_live(entry_url, entry_headers, asset_url, asset_headers)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-nginx-cache-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "target", nargs="?", default=".",
        help="Project directory (scans service-configs/nginx*/), or one conf file "
             "(default: .)",
    )
    parser.add_argument(
        "--live", metavar="BASE_URL",
        help="Also check the headers a deployed stand ACTUALLY serves: fetches the "
             "entry document and the first hashed asset it references.",
    )
    parser.add_argument(
        "--timeout", type=float, default=10.0, help="--live request timeout (seconds)",
    )
    parser.add_argument("--json", action="store_true", help="Machine output")
    parser.add_argument(
        "--strict", action="store_true", help="Fail on warnings too (NGX004)",
    )
    args = parser.parse_args(argv)

    target = Path(args.target)
    notes: list[str] = []
    findings: list[Finding] = []

    if not args.live or args.target != ".":
        if not target.exists():
            print(f"Error: no such file or directory: {target}", file=sys.stderr)
            return 2
        findings.extend(lint_project(target, notes=notes))

    if args.live:
        try:
            findings.extend(lint_live(args.live, timeout=args.timeout))
        except Exception as exc:  # network/DNS/TLS — an unreachable stand is a gate failure
            print(f"Error: --live {args.live}: {exc}", file=sys.stderr)
            return 2

    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level != "error"]

    if args.json:
        print(json.dumps(
            {
                "ok": not errors and not (args.strict and warnings),
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
            print(f"\n{len(errors)} error(s), {len(warnings)} warning(s).")
        elif not discover_confs(target) and not args.live:
            # Never say "clean" about something never read. A gate that
            # reports success on zero inputs is the same defect class this
            # linter exists to catch (a checker that carries its own copy of
            # the answer, or in this case no input at all, and passes).
            print(f"Checked 0 nginx confs under {target} — nothing was verified.")
        else:
            print(f"No SPA cache-canon issues found in {target}.")

    if errors:
        return 1
    if args.strict and warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
