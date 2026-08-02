"""
stapel-env-address-lint — the "address that belongs to the environment,
frozen into a file that outlives it" gate (``docs/pending/env-address-class.md``
v1, ``docs/pending/env-address-class-v2.md`` v2 — v2 is the current policy,
v1's measurement and rule text survive into EADDR001 verbatim), in the
``stapel-nginx-cache-lint`` / ``stapel-config-lint`` idiom (rule codes,
``--json``, ``--strict``, exit 1 on any error).

The incident this closes: ``eil meet``'s ``nginx/prod.conf`` carried
``set $livekit_upstream 172.17.0.1:7880`` — the gateway of Docker's DEFAULT
bridge, not the gateway of the network the nginx container actually lived in.
Docker recreated the server, the real network's gateway became ``172.18.0.1``,
and every ``/rtc`` call black-holed for 60s — a full day of "the stand is
slow" before anyone read it as a wrong address rather than load. A second,
independent contact with the same address class then took the whole site
down: the gate that DID catch the address problem refused to start nginx at
all (v1's fail-closed policy), which is a worse failure mode than the outage
it prevented — see v2 §2. This linter is L1 of the four-layer incident
breakdown (v2 §1): the layer a static check can actually close. It is
deliberately NOT "the fix" for the class — v2's own measurement is that this
rule would have caught incident #1 (the sutki of "load") but neither #2 (the
gate blocking startup — a *policy* defect, not a file-content one) nor #3 (the
firewall never letting the docker bridge through, nor #4 (the LiveKit
try/except swallowing every failed call). Those three get their own
mechanisms — the reachability gate (v2 §3.4), the deploy preflight (v2 §3.5)
and the health dependency registry (v2 §3.6) — composed elsewhere.

Rules
-----
EADDR001  (error) A literal RFC1918 address (``10/8``, ``172.16/12``,
          ``192.168/16``) used as an ENDPOINT (not a CIDR range) in a
          deploy-class file: ``*.conf``/``*.conf.template``, ``*.yml``,
          ``*.yaml``, ``.env*``, ``*.toml``, ``*.ini``, ``Dockerfile*`` — the
          files that get copied/mounted between environments. Not scanned:
          code (test fixtures reasonably hardcode private IPs), ``*.md``
          (prose), ``*.json`` (Grafana dumps), test/vendored directories
          (``SKIP_DIRS``, shared with ``stapel-config-lint``).

          Excluded by construction (not RFC1918, so the regex never matches
          them): loopback ``127/8`` and the bind address ``0.0.0.0``.
          Excluded by form: a CIDR **with a mask** (``172.16.0.0/12``,
          ``10.0.0.0/8`` — a policy range: allowlist, ``set_real_ip_from``,
          ``ufw``, never an endpoint) and the sentinel ``@host-gateway`` (not
          an IP literal at all — never matches the address regex, no special
          casing needed). Suppressed with ``# stapel: env-address-ok
          <reason>`` on the offending line (the ``# noqa: CFG001`` idiom —
          visible in review, not a blanket switch).

          Message: docker-gateway-shaped addresses (``172.16-31.x.1``) get an
          intensified message naming the incident class directly — this
          exact shape is what the outage was.

          Measured against the fleet (v1 §1, 2026-08-02): "any private IP
          literal in a deploy-class file" found 83 hits, 3 true / 80 false
          (48 ``0.0.0.0`` binds, 29 loopback/resolver, ~4 deliberate public
          IPs); "any private IP anywhere, code included" found 65, 3 true /
          62 false. EADDR001's narrower formulation (endpoint, not CIDR, not
          loopback, deploy-class files only) found exactly the 3 true
          positives (the incident's own three config copies) at 0 false
          positives. Re-measured as part of this build — see the module's
          test suite and the build report for whether the fleet numbers still
          hold.

EADDR002  (error) An nginx conf under ``service-configs/nginx*/`` has an
          **env-boundary** upstream — a literal IP, ``host.docker.internal``,
          the ``@host-gateway`` sentinel, or an envsubst ``${VAR}`` whose
          compose-file default resolves to one of those (never a project's
          own docker-DNS service name) — while the project's compose file(s)
          do not mount an upstream-reachability gate script into the nginx
          service's ``/docker-entrypoint.d/``. Redefined under v2 (the gate
          itself no longer fails startup — v2 §3.4): the rule's job is
          unchanged (transport the gate into a project that has not adopted
          it yet — v1 §2.3, "the rule IS the delivery channel"), only the
          gate it is asking for is the v2 (self-healing, always-exit-0)
          shape. The fix message prints the exact mount + healthcheck +
          ``UPSTREAM_GATE_TARGETS`` snippet.

          A location already gated the v2 way — ``include
          .../stapel-gate/<name>.conf;`` inside the location — is also
          env-boundary (that include IS the point), and is used as
          corroborating evidence that the mount should exist: it is a
          stronger signal than a literal, because the include file is
          nothing without the script that writes it.

EADDR003  (warning) An env-boundary proxy location (same classification as
          EADDR002 — literal/host.docker.internal/@host-gateway/unresolved
          envsubst-var target, or a v2 ``stapel-gate`` include) has no
          explicit ``proxy_connect_timeout`` of 10s or less. Half the cost of
          the original incident was that the connect attempt sat for nginx's
          default 60s before failing, which reads exactly like server load
          instead of like a wrong/unreachable address — a fast connect
          failure is a diagnosis, a slow one is camouflage.

What this deliberately does NOT check
--------------------------------------
* Public IPs (``node_ip: 91.193.43.65``-shaped values) — a different,
  narrower incident family (v1/v2 §4/§5); RFC1918-only keeps false positives
  at zero, which is the property that let this rule survive contact with the
  real fleet at all.
* Whether an env-boundary target is actually REACHABLE — that is the gate's
  job (v2 §3.4) and the preflight's job (v2 §3.5), never a static linter's;
  detecting "an address of this SHAPE exists in a file that outlives its
  environment" is the whole and only claim EADDR001 makes.
* Compose files reached only through ``include:`` (a nested compose file):
  EADDR002/003's compose-default resolution only reads the files it is
  pointed at directly.
* A location gated the OLD way (a literal/envsubst-var ``proxy_pass``
  directly, no include) whose value coincidentally does resolve today — that
  is exactly what EADDR001 catches on the file-content side, and the gate
  catches on the reachability side; this rule's job is only "is the
  transport (script mount) present".

Suppression: ``# noqa`` (blanket) or ``# noqa: EADDR00x`` on the offending
line (EADDR002/003: the ``location``/mount line).

Exit codes: 0 clean (warnings allowed), 1 errors present (``--strict`` also
fails on warnings), 2 usage/environment errors.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config_lint import SKIP_DIRS
from .nginx_cache_lint import (
    Directive,
    NginxParseError,
    discover_confs,
    iter_locations,
    location_label,
    parse_conf,
    parse_nginx_time,
)

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
# noqa (shared shape with nginx_cache_lint / config_lint)
# ---------------------------------------------------------------------------


def _noqa_rules(line: str) -> Optional[set[str]]:
    if "# noqa" not in line and "# stapel:" not in line:
        return None
    if "# stapel: env-address-ok" in line:
        return set()  # blanket suppress on this line — the v1 idiom
    if "# noqa:" not in line:
        return set() if "# noqa" in line else None
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
# EADDR001 — literal RFC1918 endpoint in a deploy-class file
# ---------------------------------------------------------------------------

_IPV4_RE = re.compile(
    r"(?<![0-9.])(?:(?:25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])\.){3}"
    r"(?:25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])(?![0-9.])"
)

_DEPLOY_EXTENSIONS = {".yml", ".yaml", ".toml", ".ini"}
_HOST_GATEWAY_SENTINEL = "@host-gateway"


def _is_deploy_class_file(path: Path) -> bool:
    name = path.name
    if name.startswith(".env"):
        return True
    if name.startswith("Dockerfile"):
        return True
    if name.endswith(".conf") or name.endswith(".conf.template"):
        return True
    return path.suffix in _DEPLOY_EXTENSIONS


def _walk_deploy_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fname in sorted(filenames):
            p = Path(dirpath) / fname
            if _is_deploy_class_file(p):
                yield p


def _octets(ip: str) -> Optional[list[int]]:
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    try:
        values = [int(p) for p in parts]
    except ValueError:
        return None
    if any(v > 255 for v in values):
        return None
    return values


def is_rfc1918(ip: str) -> bool:
    octets = _octets(ip)
    if octets is None:
        return False
    a, b = octets[0], octets[1]
    if a == 10:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 192 and b == 168:
        return True
    return False


def is_docker_gateway_shaped(ip: str) -> bool:
    """``172.16-31.x.1`` — the exact shape of the incident's own address
    (a docker bridge's gateway is always the network's ``.1``)."""
    octets = _octets(ip)
    if octets is None:
        return False
    return octets[0] == 172 and 16 <= octets[1] <= 31 and octets[3] == 1


_ENDPOINT_MSG = (
    "literal private address `{ip}` in {label} — this class of value "
    "(a Docker bridge/network gateway, a host's LAN address) is assigned by "
    "the ENVIRONMENT and changes when the network/host is recreated; a file "
    "that is copied or mounted between environments must never carry it as a "
    "number. Use the compose-network DNS name for an in-network peer, the "
    "`@host-gateway` sentinel (resolved from `/proc/net/route` at container "
    "start — see the upstream-reachability gate) for a host-network "
    "dependency, or an env-var substituted at deploy time. Suppress a "
    "deliberate exception with `# stapel: env-address-ok <reason>`."
)

_GATEWAY_MSG = (
    "literal `{ip}` in {label} has the exact shape of a Docker bridge "
    "gateway (`172.16-31.x.1`) — THIS is the incident class that took down "
    "meettoday's WebRTC signaling for a full day (read as \"load\") and then "
    "the whole site (a fail-closed gate that believed the frozen number): "
    "`docker0`'s gateway is a daemon-level constant, NOT the gateway of the "
    "network this container actually runs in, and it is reassigned on every "
    "network/container recreation. Use `@host-gateway` (computed from "
    "`/proc/net/route` per-container, per-start) instead. Suppress a "
    "deliberate exception with `# stapel: env-address-ok <reason>`."
)


def lint_file_eaddr001(path: Path, *, src: Optional[str] = None) -> list[Finding]:
    text = src if src is not None else path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    findings: list[Finding] = []
    name = str(path)
    for lineno, line in enumerate(lines, start=1):
        for match in _IPV4_RE.finditer(line):
            ip = match.group(0)
            end = match.end()
            # CIDR: an address immediately followed by "/<digits>" is a
            # policy RANGE (allowlist / set_real_ip_from / ufw), not an
            # endpoint — out of scope by form (v1 §1). A comment explaining
            # the address is still scanned — no special-casing for "#",
            # since a stale address named in prose is just as misleading.
            if end < len(line) and line[end] == "/" and end + 1 < len(line) \
                    and line[end + 1].isdigit():
                continue
            if not is_rfc1918(ip):
                continue
            if _suppressed(lines, "EADDR001", lineno):
                continue
            label = f"{name}:{lineno}"
            if is_docker_gateway_shaped(ip):
                msg = _GATEWAY_MSG.format(ip=ip, label=label)
            else:
                msg = _ENDPOINT_MSG.format(ip=ip, label=label)
            findings.append(Finding(name, lineno, "EADDR001", msg, "error"))
    return findings


def lint_eaddr001(project: Path, *, notes: Optional[list[str]] = None) -> list[Finding]:
    if notes is None:
        notes = []
    findings: list[Finding] = []
    for path in _walk_deploy_files(project):
        try:
            findings.extend(lint_file_eaddr001(path))
        except (OSError, UnicodeDecodeError) as exc:
            notes.append(f"stapel-env-address-lint: {path}: unreadable ({exc}) — skipped.")
    return findings


# ---------------------------------------------------------------------------
# EADDR002 / EADDR003 — env-boundary nginx upstreams + the v2 gate
# ---------------------------------------------------------------------------

_GATE_FIX_MESSAGE = """\
no upstream-reachability gate mounted for the nginx service — copy the
canonical script (stapel_tools._upstream_gate_template.UPSTREAM_GATE_SH,
verified live against real containers — see that module's docstring) to
service-configs/nginx/40-upstream-gate.sh, then add:
  volumes:
    - ./service-configs/nginx/40-upstream-gate.sh:/docker-entrypoint.d/40-upstream-gate.sh:ro
  environment:
    - UPSTREAM_GATE_TARGETS=rtc={upstream_var}
  healthcheck:
    test: ["CMD", "/docker-entrypoint.d/40-upstream-gate.sh", "--once"]
    interval: 30s
    timeout: 10s
    retries: 3
    start_period: 15s
(readiness signal only — never wire `depends_on: condition: service_healthy`
onto nginx from this: an unhealthy nginx here still serves the rest of the
site). The gate ALWAYS lets nginx start (v2 policy: config errors fail
closed, environment/reachability errors degrade loudly instead — see
docs/pending/env-address-class-v2.md §2); it writes /etc/nginx/stapel-gate/
include files nginx locations `include`, self-healing on every healthcheck."""


def _compose_service_names(text: str) -> set[str]:
    """Best-effort top-level ``services:`` key extraction — line-based (no
    YAML dependency; stapel-tools ships with zero runtime dependencies).
    Does not follow ``include:``d compose files (documented limitation)."""
    names: set[str] = set()
    in_services = False
    services_indent: Optional[int] = None
    for line in text.splitlines():
        if not in_services:
            if re.match(r"^services:\s*(#.*)?$", line):
                in_services = True
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            break  # back to another top-level section (volumes:, networks:)
        match = re.match(r"^(\s*)([A-Za-z0-9_.-]+):(\s*(#.*)?)$", line)
        if not match:
            continue
        this_indent = len(match.group(1))
        if services_indent is None:
            services_indent = this_indent
        if this_indent == services_indent:
            names.add(match.group(2))
    return names


def _compose_var_default(text: str, var_name: str) -> Optional[str]:
    """``VAR=${VAR:-default}`` / ``VAR: "${VAR:-default}"`` in a compose
    service's ``environment:`` block — the value that actually reaches the
    nginx container when the deploy env does not override it."""
    pattern = re.compile(
        rf"""{re.escape(var_name)}\s*[=:]\s*["']?\$\{{{re.escape(var_name)}:-([^"'}}]+)\}}"""
    )
    match = pattern.search(text)
    return match.group(1) if match else None


def _find_gate_mount(compose_texts: list[str]) -> bool:
    for text in compose_texts:
        for line in text.splitlines():
            if "/docker-entrypoint.d/" in line and re.search(
                r"upstream[-_]?gate|check[-_]?upstreams?", line, re.IGNORECASE
            ):
                return True
    return False


def _classify_target(raw: str, known_services: set[str], compose_texts: list[str]) -> bool:
    """True when *raw* (an nginx proxy_pass/set target, ``http://`` stripped)
    is env-boundary: belongs to the environment, not to this project's own
    compose network."""
    target = raw.strip()
    if target.startswith("http://"):
        target = target[len("http://"):]
    elif target.startswith("https://"):
        target = target[len("https://"):]
    host = target.split(":", 1)[0].split("/", 1)[0]

    if host.startswith(_HOST_GATEWAY_SENTINEL) or host == _HOST_GATEWAY_SENTINEL:
        return True
    if host == "host.docker.internal":
        return True
    if is_rfc1918(host) or _octets(host) is not None:
        return True  # any literal IPv4, not just RFC1918 — narrow scan surface already
    if host.startswith("${") and host.endswith("}"):
        var_name = host[2:-1]
        for text in compose_texts:
            default = _compose_var_default(text, var_name)
            if default is None:
                continue
            default_host = default.split(":", 1)[0]
            if (
                default_host.startswith(_HOST_GATEWAY_SENTINEL)
                or default_host == "host.docker.internal"
                or _octets(default_host) is not None
            ):
                return True
            if default_host in known_services:
                return False
        return False  # unresolved var, no compose evidence — do not guess
    if host.startswith("$"):
        return False  # nginx-native variable ($stapel_backend) — resolved elsewhere in-file
    if not known_services:
        return False  # no compose file read — cannot tell hostname from service name
    return host not in known_services


def _resolve_set_vars(root) -> dict[str, str]:
    """``set $name value;`` directives anywhere in the parsed tree, keyed by
    ``$name`` — resolves the deferred-resolution idiom
    (``set $x http://host:port; proxy_pass http://$x;``)."""
    table: dict[str, str] = {}

    def walk(block):
        for directive in block.own("set"):
            if len(directive.args) >= 2 and directive.args[0].startswith("$"):
                table[directive.args[0]] = directive.args[1]
        for child in block.blocks:
            walk(child)

    walk(root)
    return table


def _location_env_boundary_targets(
    loc, set_vars: dict[str, str], known_services: set[str], compose_texts: list[str]
) -> tuple[bool, Optional[Directive]]:
    """(is_env_boundary, anchor_directive) for one location block."""
    for directive in loc.own("include"):
        if directive.args and "stapel-gate" in directive.args[0]:
            return True, directive
    for directive in loc.own("proxy_pass"):
        if not directive.args:
            continue
        target = directive.args[0]
        if target.startswith("http://") or target.startswith("https://"):
            bare = target.split("://", 1)[1]
        else:
            bare = target
        if bare.startswith("$") and bare.split("/", 1)[0] in set_vars:
            bare = set_vars[bare.split("/", 1)[0]]
            if bare.startswith("http://") or bare.startswith("https://"):
                bare = bare.split("://", 1)[1]
        if _classify_target(bare, known_services, compose_texts):
            return True, directive
    return False, None


def lint_eaddr002_003(
    project: Path, *, notes: Optional[list[str]] = None
) -> list[Finding]:
    if notes is None:
        notes = []
    findings: list[Finding] = []

    confs = discover_confs(project)
    if not confs:
        return findings

    compose_paths = sorted(project.glob("docker-compose*.yml")) + sorted(
        project.glob("docker-compose*.yaml")
    )
    compose_texts = []
    for p in compose_paths:
        try:
            compose_texts.append(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    known_services: set[str] = set()
    for text in compose_texts:
        known_services |= _compose_service_names(text)

    any_env_boundary = False
    upstream_var_hint = "${LIVEKIT_UPSTREAM}"

    for conf in confs:
        try:
            text = conf.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            notes.append(f"stapel-env-address-lint: {conf}: unreadable ({exc}) — skipped.")
            continue
        try:
            root = parse_conf(text)
        except NginxParseError as exc:
            notes.append(f"stapel-env-address-lint: {conf}: unparseable ({exc}) — skipped.")
            continue

        lines = text.splitlines()
        set_vars = _resolve_set_vars(root)

        for loc in iter_locations(root):
            is_boundary, anchor = _location_env_boundary_targets(
                loc, set_vars, known_services, compose_texts
            )
            if not is_boundary:
                continue
            any_env_boundary = True
            anchor_line = anchor.line if anchor else loc.line

            # ------------------------------------------------------ EADDR003
            timeouts = loc.own("proxy_connect_timeout")
            ok = False
            for directive in timeouts:
                if not directive.args:
                    continue
                seconds = parse_nginx_time(directive.args[0])
                if seconds is not None and 0 < seconds <= 10:
                    ok = True
                    break
            if not ok and not _suppressed(lines, "EADDR003", loc.line, anchor_line):
                findings.append(Finding(
                    str(conf), loc.line, "EADDR003",
                    f"{location_label(loc)} proxies to an env-boundary "
                    f"upstream but has no `proxy_connect_timeout` of 10s or "
                    f"less — nginx's own default (60s) means an unreachable "
                    f"address sits and LOOKS like server load instead of "
                    f"failing fast and diagnosably. Add "
                    f"`proxy_connect_timeout 5s;` next to the include/"
                    f"proxy_pass.",
                    "warning",
                ))

    # EADDR002 anchors on the first nginx conf file (the mount lives in a
    # different file — compose — so there is no single offending line inside
    # a conf; same idiom as config_lint's CFG000/CFG005 project-level
    # findings, which anchor on line 0/1 of the file that names the gap).
    if any_env_boundary and not _find_gate_mount(compose_texts):
        upstream_conf = confs[0]
        if not _suppressed(upstream_conf.read_text(encoding="utf-8").splitlines(), "EADDR002", 1):
            findings.append(Finding(
                str(upstream_conf), 1, "EADDR002",
                _GATE_FIX_MESSAGE.format(upstream_var=upstream_var_hint),
                "error",
            ))

    return findings


# ---------------------------------------------------------------------------
# project driver
# ---------------------------------------------------------------------------


def lint_project(project: Path, *, notes: Optional[list[str]] = None) -> list[Finding]:
    if notes is None:
        notes = []
    project = project.resolve()
    findings: list[Finding] = []
    findings.extend(lint_eaddr001(project, notes=notes))
    findings.extend(lint_eaddr002_003(project, notes=notes))
    findings.sort(key=lambda f: (f.path, f.line, f.rule))
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-env-address-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "project_dir", nargs="?", default=".",
        help="Project directory to lint (default: .)",
    )
    parser.add_argument("--json", action="store_true", help="Machine output")
    parser.add_argument(
        "--strict", action="store_true", help="Fail on warnings too (EADDR003)",
    )
    args = parser.parse_args(argv)

    project = Path(args.project_dir)
    if not project.is_dir():
        print(f"Error: not a directory: {project}", file=sys.stderr)
        return 2

    notes: list[str] = []
    findings = lint_project(project, notes=notes)
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
        else:
            print(f"No env-address issues found in {project}.")

    if errors:
        return 1
    if args.strict and warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
