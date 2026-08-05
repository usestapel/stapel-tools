"""
stapel-frontend-delivery-lint — the frontend DELIVERY gate (owner directive,
2026-08-05; fable verdict ``tasks/fable/frontend-delivery-split-repo.md`` §3),
in the ``stapel-nginx-cache-lint`` idiom (rule codes, ``--json``, ``--strict``,
exit 1 on any error, ``# noqa: FED001`` suppression).

``stapel-nginx-cache-lint`` answers "when the browser asks for the bundle, is
it allowed to reuse a stale one". This gate answers the question NOBODY was
asking, which is why it cost months: **is there a bundle on that disk at all,
and who put it there.**

The incident that made this machine-checkable (app.ironmemo.com stand):

* ``service-configs/nginx/nginx.ssl.conf:83`` — ``location /`` serves
  ``root /frontend-react``.
* ``docker-compose.base.yml:167`` — nginx mounts that path from a HOST
  directory bind, ``./frontend-react:/frontend-react``.
* ``scripts/deploy_stand.sh:83`` and ``.gitlab-ci.yml`` (job ``deploy_dev``)
  BOTH pass ``--exclude 'frontend-react'`` to the rsync that pushes the repo
  to the stand.

So nginx serves a directory that no deploy ever fills, and the cache canon
above it is immaculate. The defect survived for months because every existing
gate looked at one side of the seam: the conf was valid nginx, the compose was
valid compose, the deploy script rsynced successfully. Nothing looked at the
JOIN — "nginx root ↔ who writes to that path". That join is this module.

Rules
-----
FED001  (error) **nginx serves a frontend directory with no provable writer.**
        For every location that serves the SPA entry document or hashed build
        artifacts from disk, the ``root``/``alias`` path is resolved (own
        directive, else inherited from the enclosing ``server``/``http``), then
        matched against the mounts of the nginx service in the stand compose
        files, and the mount must have a demonstrable writer:

        * **named volume** — some other service mounts the same volume
          writable (no ``:ro``) AND is a one-shot writer (``restart: "no"``
          and/or a mount target under ``/output`` — the §57 monolith canon:
          ``frontend-build`` writes ``frontend-dist``, nginx starts only on
          ``service_completed_successfully``). A long-running writer is
          reported too: it means "some daemon might write there", which is not
          a delivery mechanism.
        * **host bind** — the source must be a repo-relative path that
          actually reaches the stand. This is the half that catches the live
          bug: the source is cross-checked against every ``--exclude`` in the
          deploy scripts (``scripts/deploy_stand.sh``, ``deploy/*.sh``,
          ``scripts/deploy*.sh``) and in CI (``.gitlab-ci.yml``,
          ``.github/workflows/*.yml``). Without that second check a gate says
          "a bind exists, therefore fine" and stays silent on ironmemo.
          An ABSOLUTE host path (``/var/www/...``) is also reported: nothing
          in the repository can prove what fills it.
        * **no mount at all** — the container serves whatever the image
          carries. Accepted only when the nginx service is built from a local
          Dockerfile whose ``COPY`` lands inside the served path.

FED002  (error) **A mutable image tag for the frontend outside the local
        stack.** In every compose file except the local one (``*local*``), a
        frontend service's ``image:`` must not resolve to ``latest`` / ``dev``
        / ``main`` / ``master`` / ``edge`` / ``stable`` / no tag at all.
        Verdict §2: the pair "which backend ↔ which frontend" is only
        reconstructable from git history if the tag is immutable —
        ``sha-<gitsha>`` or an ``@sha256:`` digest. A mutable tag also makes
        rollback a lie. Tags written as ``${FRONTEND_*_TAG}`` are resolved
        through the env template before judging, and a ``${VAR:-latest}``
        default counts as ``latest``.

FED003  (error) **A ``FRONTEND_*`` image/tag/digest variable is used in compose
        but absent from the env template.** ``deploy_stand.sh`` regenerates
        ``.env`` on the stand from ``scripts/env.stand.template`` on every
        deploy, so a value hand-pinned on the stand silently disappears and
        compose substitutes the EMPTY STRING — ``image: repo:`` — which is a
        different failure every time. The template is the only place the pin
        survives (verdict §2.2). A ``:-default`` does not excuse absence: the
        default is exactly how a stand ends up running an unintended build.

FED004  (error) **The frontend/backend contract snapshot disagrees with
        itself.** Verdict §2.4: the dist image carries ``dist/build-info.json``
        (or an OCI label) with the digest of the backend surface it was
        generated against, so a split-repo pair cannot drift silently.
        HONEST SCOPE, stated because pretending otherwise is the defect this
        whole file exists to prevent: **no snapshot mechanism exists in the
        fleet yet** — ``stapel-surface`` emits a usage surface and
        ``stapel-gen-client`` regenerates a typed client, but neither writes a
        digest anybody records. So FED004 checks PRESENCE AND AGREEMENT OF THE
        FIELD, never semantics: if a snapshot exists it must carry a non-empty
        backend-surface digest, and if the repo also pins one (env-template
        ``FRONTEND_CONTRACT_DIGEST`` or a compose label), the two must be
        equal. It never recomputes the surface and cannot tell you the pair is
        actually compatible. When no snapshot exists at all this is NOT a
        silent skip — :func:`lint_project` returns a note saying the mechanism
        is not in place and FED004 verified nothing.

FED005  (error) **Something on the delivery path could not be read, so no
        claim is made about it.** A ``root``/``alias`` whose value starts with
        a variable or is not absolute; a conf that does not parse; a
        server/http-level ``include`` that could contribute locations we never
        saw; a mount whose source is a variable; a compose file that does not
        parse, or that uses YAML anchors / merge keys / an unresolvable
        ``extends`` for a service this gate depends on.

        Why error and not a quiet skip: a conservative skip is a
        false-negative machine, and it is precisely what let ironmemo run for
        months — every isolated checker "passed" on the part it could see.
        A gate that cannot read the seam must SAY SO, loudly, in the same
        channel as a real defect. Suppress a genuinely irrelevant one with
        ``# noqa: FED005``; that leaves a reviewed decision in the file
        instead of silence.

FED006  (warning) **Host-bind delivery: transported, but freshness unproven.**
        The bind source does reach the stand (not excluded anywhere), so
        FED001 is satisfied — but nothing in the repository builds it. Whoever
        deploys must have run the build locally first. Legacy ``host``
        delivery is an allowed axis (verdict §4), so this is a warning, not an
        error; ``--strict`` fails on it.

Suppression: ``# noqa`` (blanket) or ``# noqa: FED001`` on the finding's line,
on the location's opening line, or on the compose mount's line.

What this deliberately does NOT check
--------------------------------------
* **Delivery that happens outside the repository.** An external ansible role,
  a manual ``scp``, a CI job in the FRONTEND repo pushing straight to the
  stand — invisible here. Verdict §5: the honesty boundary of this gate is
  "what the repository describes". FED001 catches a missing *mechanism*, not a
  missing *intention*.
* **Whether the bundle on the disk is CURRENT.** Proving a writer exists is
  not proving it ran, ran last, or produced the build the backend expects
  (that is FED004's job, and FED004 is a stub by construction — see above).
* **Compose semantics beyond the subset parsed here.** stapel-tools ships zero
  runtime dependencies on purpose (see ``config_manifest.py``), so PyYAML is
  not available and this module parses the compose subset it needs with an
  indentation reader: mappings, block sequences, scalars, block scalars,
  quoted values, ``include:``, and one-level-at-a-time ``extends:``. It does
  NOT implement anchors, merge keys, flow mappings, or multi-document files —
  every one of those is reported as FED005 rather than skipped.
* Which compose file belongs to which stand. Mounts are unioned across all
  discovered compose files (minus ``*local*`` for FED002), because a stapel
  project's nginx service is routinely assembled from ``include:``d bases plus
  overrides, and per-stack resolution would need the whole compose merge
  algorithm to be trustworthy.

Exit codes: 0 clean (warnings allowed), 1 errors present (``--strict`` also
fails on warnings), 2 usage/environment errors.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

# The nginx side is NOT re-implemented: the tokenizer, the block tree, the
# location classifiers and the noqa machinery are the ones stapel-nginx-cache-
# lint already runs over these same confs. Two parsers over one conf language
# is exactly how two gates end up disagreeing about what the conf says.
from .nginx_cache_lint import (
    Block,
    Directive,
    Finding,
    NginxParseError,
    is_entry_document,
    is_hashed_asset,
    iter_locations,
    location_label,
    parse_conf,
)
from .nginx_cache_lint import _suppressed as suppressed

__all__ = [
    "Finding",
    "ComposeFile",
    "ComposeService",
    "Mount",
    "StaticRoot",
    "lint_project",
    "main",
]


# ---------------------------------------------------------------------------
# a compose reader (the subset this gate needs), without a YAML dependency
# ---------------------------------------------------------------------------


@dataclass
class YamlNode:
    """A node of the parsed subset: exactly one of value/mapping/sequence."""

    line: int
    value: Optional[str] = None
    mapping: dict = field(default_factory=dict)
    sequence: list = field(default_factory=list)

    def get(self, key: str) -> Optional["YamlNode"]:
        return self.mapping.get(key)

    def scalar(self, key: str) -> Optional[str]:
        node = self.mapping.get(key)
        return node.value if node is not None else None

    def strings(self, key: str) -> list[tuple[str, int]]:
        """A block sequence of scalars as (text, line) pairs."""
        node = self.mapping.get(key)
        if node is None:
            return []
        if node.value is not None:
            return [(node.value, node.line)]
        return [(item.value, item.line) for item in node.sequence if item.value is not None]


class ComposeParseError(ValueError):
    pass


_KEY_RE = re.compile(r"^([^\s\"'#][^:]*?)\s*:(?:\s+(.*))?$")
_BLOCK_SCALARS = {"|", ">", "|-", ">-", "|+", ">+"}


def _strip_comment(text: str) -> str:
    out: list[str] = []
    quote: Optional[str] = None
    for ch in text:
        if quote is not None:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (not out or out[-1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _physical_lines(src: str) -> list[tuple[int, str, int]]:
    """(indent, content, line-number) for every meaningful line."""
    rows: list[tuple[int, str, int]] = []
    for number, raw in enumerate(src.splitlines(), start=1):
        if raw.lstrip().startswith("#"):
            continue
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        if stripped.lstrip().startswith("---") or stripped.lstrip().startswith("..."):
            raise ComposeParseError(f"line {number}: multi-document YAML is not supported")
        indent = len(stripped) - len(stripped.lstrip(" "))
        if "\t" in raw[:indent]:
            raise ComposeParseError(f"line {number}: tab indentation")
        rows.append((indent, stripped.strip(), number))
    return rows


def _parse_rows(rows: list[tuple[int, str, int]], start: int, indent: int) -> tuple[YamlNode, int]:
    node = YamlNode(line=rows[start][2] if start < len(rows) else 0)
    index = start
    while index < len(rows):
        row_indent, text, number = rows[index]
        if row_indent < indent:
            break

        if text.startswith("- "):
            item_text = text[2:].strip()
            index += 1
            match = _KEY_RE.match(item_text)
            if match:
                # `- key: value` — a mapping item; its siblings are the
                # deeper-indented lines that follow.
                item = YamlNode(line=number)
                key = _unquote(match.group(1))
                value = (match.group(2) or "").strip()
                if value:
                    item.mapping[key] = YamlNode(line=number, value=_unquote(value))
                    child, index = _parse_rows(rows, index, row_indent + 2)
                    item.mapping.update(child.mapping)
                else:
                    child, index = _parse_rows(rows, index, row_indent + 2)
                    item.mapping[key] = child
                node.sequence.append(item)
            else:
                node.sequence.append(YamlNode(line=number, value=_unquote(item_text)))
            continue

        if text == "-":
            index += 1
            child, index = _parse_rows(rows, index, row_indent + 2)
            node.sequence.append(child)
            continue

        match = _KEY_RE.match(text)
        if not match:
            raise ComposeParseError(f"line {number}: not a key or a list item: {text!r}")
        key = _unquote(match.group(1))
        value = (match.group(2) or "").strip()
        index += 1
        if value in _BLOCK_SCALARS:
            chunk: list[str] = []
            while index < len(rows) and rows[index][0] > row_indent:
                chunk.append(rows[index][1])
                index += 1
            node.mapping[key] = YamlNode(line=number, value="\n".join(chunk))
            continue
        if value:
            node.mapping[key] = YamlNode(line=number, value=_unquote(value))
            continue
        child, index = _parse_rows(rows, index, row_indent + 1)
        child.line = number
        node.mapping[key] = child
    return node, index


def parse_yaml_subset(src: str) -> YamlNode:
    """Parse the compose subset this gate reads. Raises ComposeParseError."""
    if re.search(r"(^|\s)<<\s*:", src, re.MULTILINE):
        raise ComposeParseError("YAML merge key `<<:` is not resolved by this reader")
    if re.search(r"(^|\s)[&*][A-Za-z0-9_][A-Za-z0-9_.-]*(\s|$)", src, re.MULTILINE):
        raise ComposeParseError("YAML anchors/aliases are not resolved by this reader")
    rows = _physical_lines(src)
    if not rows:
        return YamlNode(line=1)
    root, _ = _parse_rows(rows, 0, 0)
    return root


# ---------------------------------------------------------------------------
# the compose model
# ---------------------------------------------------------------------------


def split_mount(spec: str) -> list[str]:
    """Split a short-form mount on ``:`` while keeping ``${VAR:-default}``
    whole — ``${NGINX_CONF:-./nginx/prod.conf}:/etc/nginx/x.conf:ro`` is three
    parts, not five."""
    parts: list[str] = []
    buf = ""
    depth = 0
    index = 0
    while index < len(spec):
        ch = spec[index]
        if ch == "$" and spec[index + 1 : index + 2] == "{":
            depth += 1
            buf += "${"
            index += 2
            continue
        if ch == "}" and depth:
            depth -= 1
            buf += ch
            index += 1
            continue
        if ch == ":" and depth == 0:
            parts.append(buf)
            buf = ""
            index += 1
            continue
        buf += ch
        index += 1
    parts.append(buf)
    return parts


@dataclass
class Mount:
    source: Optional[str]
    target: str
    mode: str
    line: int
    raw: str

    @property
    def read_only(self) -> bool:
        return "ro" in [m.strip() for m in self.mode.split(",") if m.strip()]

    @property
    def is_named_volume(self) -> bool:
        return bool(self.source) and "/" not in (self.source or "") and not self.has_variable

    @property
    def is_bind(self) -> bool:
        return bool(self.source) and "/" in (self.source or "") and not self.has_variable

    @property
    def has_variable(self) -> bool:
        return "$" in (self.source or "")


@dataclass
class ComposeService:
    name: str
    line: int
    image: Optional[str] = None
    image_line: int = 0
    build_context: Optional[str] = None
    restart: Optional[str] = None
    mounts: list[Mount] = field(default_factory=list)
    labels: list[tuple[str, int]] = field(default_factory=list)
    extends: Optional[tuple[str, str]] = None

    @property
    def one_shot(self) -> bool:
        """The §57 delivery shape: a container that runs once and exits, so
        nginx can wait on ``service_completed_successfully``."""
        if (self.restart or "").strip('"\'') == "no":
            return True
        return any(m.target.rstrip("/").endswith("/output") or m.target.rstrip("/") == "/output"
                   for m in self.mounts)


@dataclass
class ComposeFile:
    path: Path
    services: dict = field(default_factory=dict)
    volumes: set = field(default_factory=set)
    includes: list = field(default_factory=list)
    lines: list = field(default_factory=list)

    @property
    def is_local_stack(self) -> bool:
        return "local" in self.path.stem.lower()


def _service_from_node(name: str, node: YamlNode) -> ComposeService:
    service = ComposeService(name=name, line=node.line)
    service.image = node.scalar("image")
    image_node = node.get("image")
    service.image_line = image_node.line if image_node is not None else node.line
    service.restart = node.scalar("restart")

    build = node.get("build")
    if build is not None:
        service.build_context = build.value if build.value is not None else build.scalar("context")

    extends = node.get("extends")
    if extends is not None and extends.value is None:
        target = extends.scalar("service")
        file_name = extends.scalar("file")
        if target:
            service.extends = (file_name or "", target)

    for text, line in node.strings("volumes"):
        parts = split_mount(text)
        if len(parts) == 1:
            service.mounts.append(Mount(None, parts[0], "", line, text))
        else:
            service.mounts.append(
                Mount(parts[0], parts[1], ",".join(parts[2:]), line, text)
            )
    long_form = node.get("volumes")
    if long_form is not None and long_form.sequence:
        for item in long_form.sequence:
            if item.value is not None:
                continue
            source = item.mapping.get("source")
            target = item.mapping.get("target")
            if target is not None and target.value:
                service.mounts.append(Mount(
                    source.value if source is not None else None,
                    target.value,
                    "ro" if (item.mapping.get("read_only") or YamlNode(0)).value == "true" else "",
                    item.line,
                    "(long-form mount)",
                ))

    labels = node.get("labels")
    if labels is not None:
        for text, line in node.strings("labels"):
            service.labels.append((text, line))
        for key, child in labels.mapping.items():
            service.labels.append((f"{key}={child.value or ''}", child.line))
    return service


def load_compose(path: Path) -> ComposeFile:
    text = path.read_text(encoding="utf-8")
    root = parse_yaml_subset(text)
    compose = ComposeFile(path=path, lines=text.splitlines())
    services = root.get("services")
    if services is not None:
        for name, node in services.mapping.items():
            compose.services[name] = _service_from_node(name, node)
    volumes = root.get("volumes")
    if volumes is not None:
        compose.volumes.update(volumes.mapping.keys())
    include = root.get("include")
    if include is not None:
        for item in include.sequence:
            if item.value:
                compose.includes.append(item.value)
            elif item.mapping.get("path") is not None:
                compose.includes.append(item.mapping["path"].value or "")
    return compose


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

#: both layouts a stapel project uses for its nginx confs: the microservice
#: `service-configs/nginx*/` tree and the monolith `nginx/` tree.
CONF_GLOBS = (
    "service-configs/nginx*/**/*.conf",
    "service-configs/nginx*/**/*.conf.template",
    "nginx/**/*.conf",
    "nginx/**/*.conf.template",
    "deploy/nginx/**/*.conf",
    "deploy/nginx/**/*.conf.template",
)

COMPOSE_GLOBS = ("docker-compose*.yml", "docker-compose*.yaml", "compose*.yml", "compose*.yaml")

DEPLOY_SCRIPT_GLOBS = (
    "scripts/deploy*.sh",
    "deploy/*.sh",
    "deploy*.sh",
    "scripts/*deploy*.sh",
)

CI_GLOBS = (".gitlab-ci.yml", ".github/workflows/*.yml", ".github/workflows/*.yaml")

ENV_TEMPLATE_GLOBS = (
    "scripts/env.stand.template",
    "scripts/env*.template",
    "env.stand.template",
    "deploy/env*.template",
    ".env.example",
    ".env.template",
    ".env.sample",
)


def _glob_all(target: Path, patterns: Iterable[str]) -> list[Path]:
    found: set[Path] = set()
    for pattern in patterns:
        found.update(p for p in target.glob(pattern) if p.is_file())
    return sorted(found)


def discover_confs(target: Path) -> list[Path]:
    return _glob_all(target, CONF_GLOBS)


def discover_composes(target: Path) -> list[Path]:
    return _glob_all(target, COMPOSE_GLOBS)


# ---------------------------------------------------------------------------
# the nginx side: which paths serve the frontend
# ---------------------------------------------------------------------------


@dataclass
class StaticRoot:
    """One on-disk path a frontend location serves."""

    conf: Path
    line: int
    label: str
    path: str
    kind: str  # "entry" | "asset"


def _resolve_root(loc: Block) -> tuple[Optional[Directive], str]:
    """The directive that decides where this location reads from disk, and
    whether it was ``alias``, ``root`` or an inherited ``root``."""
    own_alias = loc.own("alias")
    if own_alias:
        return own_alias[-1], "alias"
    own_root = loc.own("root")
    if own_root:
        return own_root[-1], "root"
    for ancestor in loc.ancestors():
        inherited = ancestor.own("root")
        if inherited:
            return inherited[-1], "inherited root"
    return None, ""


def _literal_prefix(value: str) -> Optional[str]:
    """The literal directory a root/alias value names.

    ``/frontend-kmp/$1`` -> ``/frontend-kmp`` (a capture-substituted tail still
    lives under a knowable directory). ``$dir/x`` or ``${DIR}`` -> None: the
    path itself is unknown, and pretending otherwise is the false-negative we
    are here to prevent (FED005)."""
    text = value.strip()
    if not text.startswith("/"):
        return None
    head = text.split("$", 1)[0]
    head = head.rstrip("/")
    return head or "/"


def collect_frontend_roots(conf: Path, src: Optional[str] = None) -> tuple[list[StaticRoot], list[Finding]]:
    """Frontend-serving disk paths in one conf, plus FED005 for everything on
    that path we could not read."""
    text = src if src is not None else conf.read_text(encoding="utf-8")
    lines = text.splitlines()
    name = str(conf)
    findings: list[Finding] = []
    roots: list[StaticRoot] = []

    try:
        root_block = parse_conf(text)
    except NginxParseError as exc:
        findings.append(Finding(
            name, 1, "FED005",
            f"nginx conf does not parse ({exc}) — the delivery gate cannot tell what "
            f"this file serves, so it makes NO claim about it. A silent skip here is "
            f"the false-negative machine that hid the ironmemo frontend for months.",
        ))
        return [], findings

    def add(rule: str, line: int, message: str, level: str = "error", *anchors: int) -> None:
        if suppressed(lines, rule, line, *anchors):
            return
        findings.append(Finding(name, line, rule, message, level))

    # server/http-level `include` may pull in whole locations we never saw.
    # Inside a `location` it can only add directives to a location we already
    # have, so it cannot hide a root.
    known = {p.name for p in ([conf] if conf.is_file() else [])}
    if conf.parent.is_dir():
        known |= {p.name for p in conf.parent.rglob("*") if p.is_file()}
    for block in _iter_blocks(root_block):
        if block.name == "location":
            continue
        for directive in block.own("include"):
            arg = directive.args[0] if directive.args else ""
            base = arg.rsplit("/", 1)[-1]
            # A glob (`include /etc/nginx/conf.d/*.conf`) is unresolvable BY
            # CONSTRUCTION: what it pulls in depends on what the container
            # mounts, not on what this repository contains. Matching it
            # against a same-suffixed file here would be a guess dressed up
            # as a check.
            if not any(ch in base for ch in "*?[") and base in known:
                continue
            add(
                "FED005", directive.line,
                f"`include {arg}` at {block.name or 'top'} level resolves to nothing in "
                f"this repository — it may define locations (and therefore frontend "
                f"roots) this gate never saw. Vendor the file, or `# noqa: FED005` with "
                f"a reason.",
            )

    for loc in iter_locations(root_block):
        entry = is_entry_document(loc)
        asset = is_hashed_asset(loc)
        if not entry and not asset:
            continue
        directive, kind = _resolve_root(loc)
        label = location_label(loc)
        if directive is None:
            # try_files-only SPA fallback with no root anywhere: nginx falls
            # back to its compiled-in prefix. Unknowable from the repo.
            add(
                "FED005", loc.line,
                f"{label} serves the frontend from disk but no `root`/`alias` is in "
                f"effect (none of its own, none inherited) — the served directory is "
                f"nginx's compile-time prefix, which this repository does not describe. "
                f"State the root explicitly.",
            )
            continue
        value = " ".join(directive.args)
        path = _literal_prefix(value)
        if path is None:
            add(
                "FED005", directive.line,
                f"{label}: `{kind} {value}` is not a literal absolute path (variable or "
                f"relative) — the gate cannot say which directory is served, so it "
                f"cannot say whether anything fills it. This is reported, not skipped: "
                f"a quiet skip is exactly how a served-but-never-written directory "
                f"survives review.",
                "error", loc.line,
            )
            continue
        # A blanket/FED001 noqa on the location line drops the root from the
        # model entirely — the suppression is a reviewed decision about this
        # location, so it must also silence the mount-side message.
        if suppressed(lines, "FED001", loc.line, directive.line):
            continue
        roots.append(StaticRoot(
            conf=conf, line=directive.line, label=label, path=path,
            kind="entry" if entry else "asset",
        ))

    return roots, findings


def _iter_blocks(block: Block) -> Iterable[Block]:
    for child in block.blocks:
        yield child
        yield from _iter_blocks(child)


# ---------------------------------------------------------------------------
# the deploy side: what never reaches the stand
# ---------------------------------------------------------------------------

_EXCLUDE_RE = re.compile(r"--exclude[=\s]+['\"]?([^'\"\s\\]+)")


@dataclass
class Exclusion:
    pattern: str
    path: Path
    line: int


def collect_exclusions(target: Path) -> list[Exclusion]:
    """Every ``--exclude`` in the deploy scripts and CI of this repo. These are
    rsync patterns: an unanchored one matches at ANY depth."""
    found: list[Exclusion] = []
    for path in _glob_all(target, DEPLOY_SCRIPT_GLOBS) + _glob_all(target, CI_GLOBS):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for match in _EXCLUDE_RE.finditer(line):
                found.append(Exclusion(match.group(1), path, number))
    return found


def normalize_bind_source(source: str) -> str:
    text = source.strip()
    for prefix in ("./", ".\\"):
        if text.startswith(prefix):
            text = text[2:]
    return text.rstrip("/")


def excluded_by(source: str, pattern: str) -> bool:
    """rsync exclude semantics, reduced to what a deploy script actually
    writes: an anchored pattern (leading ``/``) matches from the transfer root,
    an unanchored one matches any path component."""
    clean = normalize_bind_source(source)
    if not clean:
        return False
    anchored = pattern.startswith("/")
    pattern_clean = pattern.strip("/")
    if not pattern_clean:
        return False
    if fnmatch.fnmatch(clean, pattern_clean):
        return True
    if anchored:
        return False
    components = clean.split("/")
    if any(fnmatch.fnmatch(component, pattern_clean) for component in components):
        return True
    # `--exclude 'frontend-react'` also kills `frontend-react/dist`
    return clean.startswith(pattern_clean.rstrip("*") + "/")


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------

_NGINX_SERVICE_RE = re.compile(r"nginx|caddy-static", re.IGNORECASE)
_FRONTEND_SERVICE_RE = re.compile(r"(^|[-_])(frontend|front-end|spa)([-_a-z0-9]*)$|frontend", re.IGNORECASE)
_MUTABLE_TAGS = {"latest", "dev", "develop", "main", "master", "edge", "stable", ""}
_FRONTEND_VAR_RE = re.compile(r"\$\{(FRONTEND[A-Z0-9_]*_(?:TAG|IMAGE|DIGEST))(?::-([^}]*))?\}")


def is_nginx_service(service: ComposeService) -> bool:
    if _NGINX_SERVICE_RE.search(service.name):
        return True
    if service.image and _NGINX_SERVICE_RE.search(service.image):
        return True
    return any(m.target.startswith("/etc/nginx") for m in service.mounts)


def is_frontend_service(service: ComposeService) -> bool:
    return bool(_FRONTEND_SERVICE_RE.search(service.name))


def _covers(mount_target: str, path: str) -> bool:
    target = mount_target.rstrip("/") or "/"
    wanted = path.rstrip("/") or "/"
    return wanted == target or wanted.startswith(target + "/") or target.startswith(wanted + "/")


def _specificity(mount: Mount) -> int:
    """Longest matching mount target wins — the most specific mount is the one
    that actually backs the path."""
    return len(mount.target.rstrip("/"))


def _dockerfile_fills(target: Path, context: str, path: str) -> Optional[tuple[Path, int]]:
    """A locally built nginx image whose ``COPY`` lands inside the served
    path — the one way a mount-less root is legitimately filled."""
    context_path = (target / normalize_bind_source(context)).resolve()
    dockerfile = context_path / "Dockerfile"
    if not dockerfile.is_file():
        return None
    try:
        text = dockerfile.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.upper().startswith("COPY "):
            continue
        parts = [p for p in stripped.split()[1:] if not p.startswith("--")]
        if not parts:
            continue
        destination = parts[-1]
        if destination.startswith("/") and _covers(destination, path):
            return dockerfile, number
    return None


@dataclass
class _Model:
    target: Path
    composes: list
    exclusions: list
    env_keys: dict = field(default_factory=dict)
    env_files: list = field(default_factory=list)

    @property
    def services(self) -> list:
        return [s for c in self.composes for s in c.services.values()]

    @property
    def stand_services(self) -> list:
        return [s for c in self.composes if not c.is_local_stack for s in c.services.values()]

    def compose_of(self, service: ComposeService) -> ComposeFile:
        for compose in self.composes:
            if compose.services.get(service.name) is service:
                return compose
        return self.composes[0]


def _check_root(model: _Model, root: StaticRoot) -> list[Finding]:
    """FED001 for one served path — the join nothing else looks at."""
    nginx_services = [s for s in model.services if is_nginx_service(s)]

    if not model.composes:
        return [Finding(
            str(root.conf), root.line, "FED001",
            f"{root.label} serves `{root.path}` but this repository has no compose file "
            f"at all — nothing describes what is mounted there or who writes it. The "
            f"gate refuses to call an unverifiable path fine.",
        )]

    if not nginx_services:
        return [Finding(
            str(root.conf), root.line, "FED001",
            f"{root.label} serves `{root.path}` but no compose service in this "
            f"repository looks like the nginx that runs this conf (no `nginx` in a "
            f"service name or image, nothing mounted at /etc/nginx), so no mount can "
            f"be matched to the path and no writer can be proved.",
        )]

    candidates: list[tuple[ComposeService, Mount]] = []
    for service in nginx_services:
        for mount in service.mounts:
            if _covers(mount.target, root.path):
                candidates.append((service, mount))
    candidates.sort(key=lambda pair: _specificity(pair[1]), reverse=True)

    if not candidates:
        for service in nginx_services:
            if service.build_context:
                hit = _dockerfile_fills(model.target, service.build_context, root.path)
                if hit:
                    return []
        return [Finding(
            str(root.conf), root.line, "FED001",
            f"{root.label} serves `{root.path}`, but the nginx service "
            f"({', '.join(sorted(s.name for s in nginx_services))}) mounts NOTHING at "
            f"or above that path in any compose file, and no locally built image COPYs "
            f"into it. nginx will serve whatever the base image happens to have there "
            f"— in practice, nothing. Mount a frontend-dist volume (see the §57 canon: "
            f"one-shot writer + `depends_on: service_completed_successfully`).",
        )]

    service, mount = candidates[0]
    compose = model.compose_of(service)
    where = f"{compose.path}:{mount.line}"

    def mount_finding(message: str, level: str = "error", rule: str = "FED001") -> Finding:
        if suppressed(compose.lines, rule, mount.line):
            return None  # type: ignore[return-value]
        return Finding(str(root.conf), root.line, rule, message, level)

    if mount.has_variable:
        finding = mount_finding(
            f"{root.label} serves `{root.path}`, mounted in service `{service.name}` from "
            f"`{mount.raw}` ({where}) — the source is a variable, so this repository does "
            f"not say what is delivered there. Reported, not skipped.",
            rule="FED005",
        )
        return [f for f in [finding] if f]

    if mount.is_named_volume:
        writers = [
            (writer, wm)
            for writer in model.services
            for wm in writer.mounts
            if wm.source == mount.source and not wm.read_only and writer is not service
        ]
        if not writers:
            finding = mount_finding(
                f"{root.label} serves `{root.path}` from the named volume "
                f"`{mount.source}` ({where}), but NO service in this repository mounts "
                f"that volume writable. The volume is created empty and stays empty: "
                f"nginx serves 404s for the entire frontend. Canon (§57): a one-shot "
                f"`frontend-build` service with `restart: \"no\"` mounting "
                f"`{mount.source}:/output`, and nginx `depends_on: condition: "
                f"service_completed_successfully`.",
            )
            return [f for f in [finding] if f]
        one_shot = [(w, m) for w, m in writers if w.one_shot]
        if not one_shot:
            names = ", ".join(sorted({w.name for w, _ in writers}))
            finding = mount_finding(
                f"{root.label} serves `{root.path}` from the named volume "
                f"`{mount.source}` ({where}); the only writable mounts of that volume "
                f"belong to long-running service(s) `{names}` (no `restart: \"no\"`, no "
                f"`/output` mount). A daemon that happens to have write access is not a "
                f"delivery mechanism — nothing guarantees the bundle exists before nginx "
                f"starts serving it. Canon (§57): a one-shot writer plus "
                f"`depends_on: condition: service_completed_successfully` on nginx.",
            )
            return [f for f in [finding] if f]
        return []

    if not mount.is_bind:
        finding = mount_finding(
            f"{root.label} serves `{root.path}` from an anonymous volume ({where}) — "
            f"an anonymous volume is recreated empty and nothing can write to it from "
            f"another service, because it has no name to mount by.",
        )
        return [f for f in [finding] if f]

    source = mount.source or ""
    if source.startswith("/"):
        finding = mount_finding(
            f"{root.label} serves `{root.path}` from the ABSOLUTE host path `{source}` "
            f"({where}) — nothing in this repository fills it, and nothing here can say "
            f"what does. If an out-of-band process owns it, record that with "
            f"`# noqa: FED001` on the mount and a comment; otherwise deliver through a "
            f"volume written by a one-shot builder (§57).",
        )
        return [f for f in [finding] if f]

    normalized = normalize_bind_source(source)
    hits = [e for e in model.exclusions if excluded_by(normalized, e.pattern)]
    if hits:
        evidence = "; ".join(
            f"{hit.path}:{hit.line} (`--exclude '{hit.pattern}'`)" for hit in hits[:4]
        )
        finding = mount_finding(
            f"{root.label} serves `{root.path}`, which nginx mounts from the host "
            f"directory `{source}` ({where}) — and the deploy path EXPLICITLY EXCLUDES "
            f"that directory: {evidence}. nginx therefore serves a directory that no "
            f"deploy ever fills; a fresh frontend never reaches the stand, which looks "
            f"exactly like \"the frontend did not update\". This is the "
            f"app.ironmemo.com defect (verdict tasks/fable/"
            f"frontend-delivery-split-repo.md). Fix: publish the build as a dist image "
            f"and deliver it through a one-shot writer into a named volume (§57/C′), or "
            f"stop excluding the directory.",
        )
        return [f for f in [finding] if f]

    source_path = model.target / normalized
    if not source_path.exists():
        finding = mount_finding(
            f"{root.label} serves `{root.path}` from the host directory `{source}` "
            f"({where}), which does not exist in this repository — the bind creates an "
            f"empty directory and nginx serves 404s.",
        )
        return [f for f in [finding] if f]

    finding = mount_finding(
        f"{root.label} serves `{root.path}` from the host bind `{source}` ({where}). The "
        f"directory does reach the stand (no deploy exclusion matches it), so delivery "
        f"is possible — but nothing in this repository BUILDS it: whoever deploys must "
        f"have produced the bundle by hand first, and nothing detects a stale or empty "
        f"one. Legacy `host` delivery is an allowed axis, hence a warning; the canon is "
        f"a dist image written into a volume by a one-shot builder (§57/C′).",
        level="warning", rule="FED006",
    )
    return [f for f in [finding] if f]


def _resolve_image_tag(image: str, env_keys: dict) -> tuple[Optional[str], str]:
    """(tag, how it was resolved). ``None`` tag = no tag at all."""
    text = image.strip()
    if "@" in text:
        return "@digest", "digest"

    def substitute(match: re.Match) -> str:
        key, default = match.group(1), match.group(2)
        if key in env_keys and env_keys[key]:
            return env_keys[key]
        return default if default is not None else ""

    resolved = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}", substitute, text)
    resolved = re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)", "", resolved)
    if "@" in resolved:
        return "@digest", "digest"
    head, _, tail = resolved.rpartition(":")
    if not head or "/" in tail:
        return None, resolved
    return tail, resolved


def _check_images(model: _Model) -> list[Finding]:
    findings: list[Finding] = []
    writers = {w.name for w in model.services if _writes_frontend_volume(model, w)}
    for compose in model.composes:
        if compose.is_local_stack:
            continue
        for service in compose.services.values():
            if not (is_frontend_service(service) or service.name in writers):
                continue
            if not service.image:
                continue
            tag, resolved = _resolve_image_tag(service.image, model.env_keys)
            if tag == "@digest":
                continue
            if tag is not None and tag.lower() not in _MUTABLE_TAGS:
                continue
            if suppressed(compose.lines, "FED002", service.image_line):
                continue
            shown = tag if tag is not None else "(no tag — implicitly `latest`)"
            findings.append(Finding(
                str(compose.path), service.image_line, "FED002",
                f"frontend service `{service.name}` runs a MUTABLE image tag on a stand "
                f"stack: `{service.image}` -> `{resolved}`, tag {shown}. \"Which "
                f"frontend is on this stand\" then has no answer, redeploying the same "
                f"backend commit can change the frontend under it, and rollback is a "
                f"lie. Canon (verdict §2): an immutable `sha-<gitsha>` tag or an "
                f"`@sha256:` digest, pinned in the env template so git is the single "
                f"place the pair is recorded.",
            ))
    return findings


def _writes_frontend_volume(model: _Model, service: ComposeService) -> bool:
    return any(
        m.target.rstrip("/").endswith("/output") or m.target.rstrip("/") in ("/output", "/dist")
        for m in service.mounts
        if not m.read_only
    )


def _check_env_vars(model: _Model) -> list[Finding]:
    findings: list[Finding] = []
    for compose in model.composes:
        if compose.is_local_stack:
            continue
        for number, line in enumerate(compose.lines, start=1):
            for match in _FRONTEND_VAR_RE.finditer(line):
                key, default = match.group(1), match.group(2)
                if key in model.env_keys:
                    continue
                if suppressed(compose.lines, "FED003", number):
                    continue
                if not model.env_files:
                    where = (
                        "this repository has no env template at all "
                        f"(looked for {', '.join(ENV_TEMPLATE_GLOBS)})"
                    )
                else:
                    where = "absent from " + ", ".join(str(p) for p in model.env_files)
                tail = (
                    f" The `:-{default}` default does not save it: a default is exactly "
                    f"how a stand quietly runs an unintended build."
                    if default is not None else
                    " compose substitutes the EMPTY STRING, so the stand pulls a "
                    "nonsense reference."
                )
                findings.append(Finding(
                    str(compose.path), number, "FED003",
                    f"`${{{key}}}` is used here but {where}. `deploy_stand.sh` "
                    f"regenerates the stand's `.env` from the template on every deploy, "
                    f"so a value pinned by hand on the stand disappears at the next "
                    f"deploy.{tail} Verdict §2.2: the template is the only place the pin "
                    f"survives.",
                ))
    return findings


_BUILD_INFO_FIELDS = ("backend_surface", "backend_surface_digest", "surface", "surface_digest")
_CONTRACT_ENV_KEYS = ("FRONTEND_CONTRACT_DIGEST", "FRONTEND_SURFACE_DIGEST")
_CONTRACT_LABEL_RE = re.compile(
    r"(build[-_.]info|frontend[.\-_]contract|surface[-_.]digest)\s*[=:]\s*(.*)", re.IGNORECASE
)


def _discover_build_info(target: Path) -> list[Path]:
    found: list[Path] = []
    for pattern in ("dist/build-info.json", "*/dist/build-info.json", "frontend*/build-info.json",
                    "*/build-info.json", "build-info.json"):
        for path in target.glob(pattern):
            if path.is_file() and "node_modules" not in path.parts:
                found.append(path)
    return sorted(set(found))


def _check_contract(model: _Model, notes: list) -> list[Finding]:
    """FED004 — presence/agreement of the contract snapshot, never semantics.
    See the module docstring: the snapshot mechanism does not exist in the
    fleet yet, and inventing a format here would make the gate lie."""
    findings: list[Finding] = []
    build_infos = _discover_build_info(model.target)
    labels: list[tuple[ComposeFile, ComposeService, str, int]] = []
    for compose in model.composes:
        for service in compose.services.values():
            for text, line in service.labels:
                if _CONTRACT_LABEL_RE.search(text):
                    labels.append((compose, service, text, line))

    pinned = {
        key: model.env_keys[key] for key in _CONTRACT_ENV_KEYS
        if model.env_keys.get(key)
    }

    if not build_infos and not labels:
        notes.append(
            "stapel-frontend-delivery-lint: FED004 — контрактный слепок не заведён "
            "(нет dist/build-info.json и нет compose-лейбла со слепком), проверять "
            "нечего. Это НЕ 'чисто': рассинхрон фронта и бэка сейчас ничем не "
            "ловится (вердикт §2.4)."
        )
        return findings

    for path in build_infos:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            findings.append(Finding(
                str(path), 1, "FED004",
                f"contract snapshot does not read as JSON ({exc}) — the one artifact "
                f"that is supposed to prove the frontend matches this backend cannot be "
                f"read.",
            ))
            continue
        if not isinstance(data, dict):
            findings.append(Finding(str(path), 1, "FED004",
                                    "contract snapshot is not a JSON object."))
            continue
        value = next((str(data[f]) for f in _BUILD_INFO_FIELDS if data.get(f)), None)
        if not value:
            findings.append(Finding(
                str(path), 1, "FED004",
                f"contract snapshot carries no backend-surface digest (expected one of "
                f"{', '.join(_BUILD_INFO_FIELDS)}) — the bundle records WHEN it was "
                f"built but not WHAT backend surface it was generated against, so a "
                f"split-repo pair can drift with nothing to compare.",
            ))
            continue
        for key, expected in pinned.items():
            if expected != value:
                findings.append(Finding(
                    str(path), 1, "FED004",
                    f"contract snapshot digest `{value}` disagrees with `{key}={expected}` "
                    f"pinned in the env template — the frontend on this stand was built "
                    f"against a different backend surface than the one this commit pins.",
                ))
    return findings


# ---------------------------------------------------------------------------
# project entry point
# ---------------------------------------------------------------------------


def _read_env_template(path: Path) -> dict:
    keys: dict = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return keys
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):]
        key, sep, value = stripped.partition("=")
        if not sep:
            continue
        key = key.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            keys.setdefault(key, _unquote(value.strip()))
    return keys


def lint_project(target: Path, *, notes: Optional[list] = None) -> list[Finding]:
    """Every frontend-delivery finding in one project directory."""
    if notes is None:
        notes = []
    findings: list[Finding] = []

    conf_paths = discover_confs(target)
    compose_paths = discover_composes(target)

    composes: list[ComposeFile] = []
    for path in compose_paths:
        try:
            composes.append(load_compose(path))
        except ComposeParseError as exc:
            findings.append(Finding(
                str(path), 1, "FED005",
                f"compose file not readable by this gate ({exc}) — every rule that "
                f"depends on it (who mounts what, who writes it, which tag runs) is "
                f"UNVERIFIED for this file. Reported instead of skipped on purpose.",
            ))
        except (OSError, UnicodeDecodeError) as exc:
            findings.append(Finding(str(path), 1, "FED005", f"compose file unreadable ({exc})."))

    env_files = _glob_all(target, ENV_TEMPLATE_GLOBS)
    env_keys: dict = {}
    for path in env_files:
        for key, value in _read_env_template(path).items():
            env_keys.setdefault(key, value)

    model = _Model(
        target=target,
        composes=composes,
        exclusions=collect_exclusions(target),
        env_keys=env_keys,
        env_files=env_files,
    )

    if not conf_paths:
        notes.append(
            f"stapel-frontend-delivery-lint: под {target} не найдено ни одного "
            f"nginx-конфа ({', '.join(CONF_GLOBS)}) — FED001/FED005 проверять нечего, "
            f"это не 'чисто'."
        )
    if not compose_paths:
        notes.append(
            "stapel-frontend-delivery-lint: не найдено ни одного docker-compose*.yml — "
            "доказать наполнение раздаваемых директорий нечем; FED002/FED003 не "
            "выполнялись."
        )
    if not env_files:
        notes.append(
            "stapel-frontend-delivery-lint: не найдено env-шаблона "
            f"({', '.join(ENV_TEMPLATE_GLOBS)}) — пин тега фронта негде хранить, "
            "FED003 сработает на любую FRONTEND_*-переменную."
        )

    roots: list[StaticRoot] = []
    for conf in conf_paths:
        try:
            conf_roots, conf_findings = collect_frontend_roots(conf)
        except (OSError, UnicodeDecodeError) as exc:
            findings.append(Finding(
                str(conf), 1, "FED005", f"nginx conf unreadable ({exc}) — not skipped: reported.",
            ))
            continue
        roots.extend(conf_roots)
        findings.extend(conf_findings)

    if not roots and conf_paths:
        notes.append(
            "stapel-frontend-delivery-lint: в найденных nginx-конфах нет ни одной "
            "локации, раздающей фронт с диска (entry-документ или хешированные "
            "ассеты) — FED001 нечего проверять."
        )

    for root in roots:
        try:
            conf_lines = root.conf.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            conf_lines = []
        for finding in _check_root(model, root):
            if suppressed(conf_lines, finding.rule, finding.line):
                continue
            findings.append(finding)

    findings.extend(_check_images(model))
    findings.extend(_check_env_vars(model))
    findings.extend(_check_contract(model, notes))

    findings.sort(key=lambda f: (f.path, f.line, f.rule))
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-frontend-delivery-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "target", nargs="?", default=".",
        help="Project directory (default: .)",
    )
    parser.add_argument("--json", action="store_true", help="Machine output")
    parser.add_argument("--strict", action="store_true", help="Fail on warnings too (FED006)")
    args = parser.parse_args(argv)

    target = Path(args.target)
    if not target.exists():
        print(f"Error: no such file or directory: {target}", file=sys.stderr)
        return 2
    if not target.is_dir():
        print(f"Error: {target} is not a project directory", file=sys.stderr)
        return 2

    notes: list = []
    findings = lint_project(target, notes=notes)
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
        elif not discover_confs(target) or not discover_composes(target):
            # Never say "clean" about something never read — the same posture
            # as stapel-nginx-cache-lint's zero-input branch. A gate that
            # reports success on zero inputs is the defect class this linter
            # exists to catch.
            print(
                f"Checked {len(discover_confs(target))} nginx conf(s) and "
                f"{len(discover_composes(target))} compose file(s) under {target} — "
                f"see the notes above for what was NOT verified."
            )
        else:
            print(f"No frontend-delivery issues found in {target}.")

    if errors:
        return 1
    if args.strict and warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
