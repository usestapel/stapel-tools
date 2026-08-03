"""
_upstream_gate_template — the canonical env-address-class-v2 upstream
reachability gate script (docs/pending/env-address-class-v2.md §3.4),
exported as data so the SAME script text ships to every consumer: EADDR002's
fix message (env_address_lint.py), the scaffold (once a project adds a
host-network dependency), and eil meet's own nginx/40-upstream-gate.sh.

Verified live (not by inspection) against real containers before being
frozen here: nginx starts with the target unreachable and the site stays up,
the healthcheck self-heals the include file + reloads nginx in BOTH
directions, all four triage branches print the right diagnosis (three
against real network conditions, the firewall/silent-drop branch against a
controlled fake-nc harness — this sandbox's virtualized docker networking
answers even unroutable addresses, so a genuine packet black hole could not
be reproduced live here), the /proc/net/route gateway math matches the
spec's own worked example bit-for-bit (``01D7A8C0`` -> ``192.168.215.1``),
and ``--probe-only`` (what deploy/preflight.sh shells out to) returns 0/1/2
for reachable/unreachable/config-error against a real one-shot container.

See docs/pending/env-address-class-v2.md §3.3-§3.5 for the full design.
"""
from __future__ import annotations

UPSTREAM_GATE_SH = r"""#!/bin/sh
# 40-upstream-gate.sh — env-boundary upstream reachability gate, v2.
# docs/pending/env-address-class-v2.md §3.4. Replaces the v1 gate
# (upstream-gate.sh): v1 exit-1'd nginx on an unreachable env-boundary
# upstream (a "critical or optional?" fail-closed policy) and, on first
# contact with a real server, took the WHOLE site down over an unreachable
# LiveKit alone — the gate correctly NAMED the problem and incorrectly
# escalated its blast radius from one feature to the whole product. v2's
# rule: config errors fail closed (an empty target list, a target not shaped
# like host:port); environment errors — unreachable, wrong network, firewall
# — NEVER fail closed. They degrade loudly instead.
#
# Two roles, one file:
#   * /docker-entrypoint.d/40-upstream-gate.sh — runs before nginx starts.
#     Probes every configured target, writes ONE include file per target
#     under /etc/nginx/stapel-gate/<name>.conf (proxy_pass on success,
#     immediate 503 + diagnosis on failure), and ALWAYS exits 0. nginx starts
#     either way; each gated location `include`s its own file.
#   * `--once` (the compose healthcheck) — single probe per target, no
#     startup retry loop. Whenever the probed reality has drifted from what
#     the include file currently serves, it rewrites the include and runs
#     `nginx -s reload` — self-healing in BOTH directions (LiveKit died on
#     the fly -> next healthcheck flips to 503; LiveKit came back -> next
#     healthcheck flips back to proxy_pass) with no long-lived daemon.
#     Exits 0 when every target is currently reachable, 1 on any deviation
#     (a healthcheck failure is a readiness SIGNAL for `docker compose ps` —
#     never wire `depends_on: condition: service_healthy` onto nginx from
#     this: an "unhealthy" nginx here is still correctly serving the rest of
#     the site).
#
# Config (env):
#   UPSTREAM_GATE_TARGETS   space-separated "name=host:port" list, e.g.
#                           "rtc=172.18.0.1:7880". CONFIG ERROR (exit 1,
#                           nginx does NOT start) if this is unset/empty
#                           while the gate is mounted — an empty list would
#                           silently disable the whole gate, which is a
#                           defect in itself (v1's own rule, kept in v2).
#                           A target whose value is not host:port shaped is
#                           the same class of config error.
#   UPSTREAM_GATE_TIMEOUT   seconds to keep retrying at STARTUP ONLY
#                           (default 30) before writing the degraded include
#                           and moving on — never fails the container for
#                           running out this budget, only decides what the
#                           FIRST include says.
#
# Sentinel: a target's host may be `@host-gateway` — resolved HERE, from
# THIS container's own /proc/net/route default route, recomputed on every
# invocation. Deliberately NOT `host.docker.internal` /
# `dockerd --host-gateway-ip`: that is a DAEMON-level constant (docker0's own
# address, e.g. 172.17.0.1 by default), not the gateway of whatever network
# this container actually landed in — the exact wrong belief
# env-address-class.md v1 shipped with, and the exact belief that let the
# original 172.17 vs 172.18 mismatch through undetected.

set -u

ME="upstream-gate"
ONCE=0
[ "${1:-}" = "--once" ] && ONCE=1
GATE_DIR="/etc/nginx/stapel-gate"

TARGETS="${UPSTREAM_GATE_TARGETS:-}"
TIMEOUT="${UPSTREAM_GATE_TIMEOUT:-30}"

log() { echo "$ME: $*" >&2; }

# --probe-only name=host:port — probe exactly ONE target and print the same
# triage diagnosis, no include file, no nginx reload. This is what
# deploy/preflight.sh (env-address-class-v2.md §3.5) shells out to from a
# one-shot container placed in the project's own compose network, BEFORE a
# deploy restarts anything — reusing this exact resolve/probe/triage code so
# preflight and the running gate can never disagree about what "reachable"
# means.
if [ "${1:-}" = "--probe-only" ]; then
    entry="${2:-}"
    case "$entry" in
        *=*) : ;;
        *) log "CONFIG ERROR: \"$entry\" is not name=host:port"; exit 2 ;;
    esac
fi

mkdir -p "$GATE_DIR"

# ---------------------------------------------------------------------------
# @host-gateway resolution — THIS container's default route.
# ---------------------------------------------------------------------------

resolve_host_gateway() {
    hexgw="$(awk '$2 == "00000000" {print $3; exit}' /proc/net/route 2>/dev/null)"
    if [ -z "$hexgw" ]; then
        return 1
    fi
    b0="${hexgw%??????}"
    rest="${hexgw#??}"
    b1="${rest%????}"
    rest="${rest#??}"
    b2="${rest%??}"
    b3="${rest#??}"
    printf '%d.%d.%d.%d\n' "$(( 0x$b3 ))" "$(( 0x$b2 ))" "$(( 0x$b1 ))" "$(( 0x$b0 ))"
}

# host[:port-suffix-already-split-off] -> resolved host (sentinel expanded).
resolve_host() {
    case "$1" in
        @host-gateway)
            if ! gw="$(resolve_host_gateway)"; then
                echo ""
                return 1
            fi
            echo "$gw"
            ;;
        *)
            echo "$1"
            ;;
    esac
}

# ---------------------------------------------------------------------------
# probe: (a) is *this* container's own default gateway (informs the triage,
# independent of the target's literal shape), (b) getent (system resolver —
# the same path nginx itself uses for a proxy_pass with no variables), (c)
# nc -z -w3 TCP connect.
#
# Prints ONE triage box (v2 §3.4 — four causes distinguished mechanically):
#   1. name does not resolve at all
#   2. target has gateway shape (x.y.z.1, private) but is NOT this
#      container's actual gateway -> wrong-network diagnosis
#   3. resolves, is (or looks like) the right gateway/host, TCP times out ->
#      firewall, with the exact ufw command
#   4. resolves, TCP connection refused -> service not up / wrong port
# ---------------------------------------------------------------------------

is_gateway_shaped() {
    case "$1" in
        10.*.*.1|172.1[6-9].*.1|172.2[0-9].*.1|172.3[01].*.1|192.168.*.1) return 0 ;;
        *) return 1 ;;
    esac
}

probe() {
    name="$1"; hostport="$2"
    case "$hostport" in
        *:*) raw_host="${hostport%:*}"; port="${hostport##*:}" ;;
        *)   echo "config-error: \"$hostport\" is not host:port"; return 2 ;;
    esac

    if ! host="$(resolve_host "$raw_host")" || [ -z "$host" ]; then
        cat <<EOF
============ SEAM DEGRADED: $name -> $raw_host:$port ============
resolve: @host-gateway sentinel FAILED — /proc/net/route has no default
route in this container (unusual; check the container's network attach).
=> cannot determine this network's gateway at all.
serving: $name -> 503 (instant); the rest of the site is unaffected.
recheck: automatically (healthcheck), or restart the container.
===============================================================
EOF
        return 1
    fi

    my_gateway="$(resolve_host_gateway 2>/dev/null || true)"

    ip="$(getent hosts "$host" 2>/dev/null | awk 'NR==1{print $1}')"
    if [ -z "$ip" ]; then
        cat <<EOF
============ SEAM DEGRADED: $name -> $host:$port ============
resolve: FAILED — "$host" is not in /etc/hosts and no DNS answer.
=> not in this container's DNS/extra_hosts; check the compose
   extra_hosts/environment value, or use the @host-gateway sentinel.
serving: $name -> 503 (instant); the rest of the site is unaffected.
recheck: automatically (healthcheck), or restart the container.
===============================================================
EOF
        return 1
    fi

    # Timed, not just pass/fail: busybox nc's exit code alone does not
    # distinguish "connection refused" (target host answered instantly with
    # RST — verified live: 0.00s) from "packets silently dropped" (a
    # firewall drop — the connect sits for the full timeout). -v/verbose
    # text is NOT a reliable signal across busybox/openbsd nc builds
    # (verified live: busybox nc's -v prints nothing usable to grep for
    # "refused"), but the WALL-CLOCK DURATION of the failed attempt is:
    # near-instant is refused, near the full budget is a silent drop.
    probe_timeout=3
    start_s="$(date +%s 2>/dev/null || echo 0)"
    if nc -z -w "$probe_timeout" "$ip" "$port" 2>/dev/null; then
        return 0
    fi
    end_s="$(date +%s 2>/dev/null || echo 0)"
    elapsed=$(( end_s - start_s ))

    if is_gateway_shaped "$ip" && [ -n "$my_gateway" ] && [ "$ip" != "$my_gateway" ]; then
        cat <<EOF
============ SEAM DEGRADED: $name -> $host:$port ============
resolve: ok ($ip); tcp: unreachable
default gateway of THIS network: $my_gateway (does NOT match $ip)
=> "$ip" is the gateway of a DIFFERENT docker network — a stale/frozen
   address. Use the @host-gateway sentinel instead of a literal IP so this
   is recomputed per container, per network, every start.
serving: $name -> 503 (instant); the rest of the site is unaffected.
recheck: automatically (healthcheck), or restart the container.
===============================================================
EOF
        return 1
    fi

    if [ "$elapsed" -lt $(( probe_timeout - 1 )) ]; then
        cat <<EOF
============ SEAM DEGRADED: $name -> $host:$port ============
resolve: ok ($ip); tcp: CONNECTION REFUSED
=> host is up but nothing is listening on port $port — the service is not
   started yet, or the port is wrong.
serving: $name -> 503 (instant); the rest of the site is unaffected.
recheck: automatically (healthcheck), check \`docker compose ps\`.
===============================================================
EOF
    else
        cat <<EOF
============ SEAM DEGRADED: $name -> $host:$port ============
resolve: ok ($ip); tcp: TIMEOUT after ${elapsed}s (budget ${probe_timeout}s)
$( [ -n "$my_gateway" ] && echo "default gateway of THIS network: $my_gateway" )
=> address resolves and packets are being dropped silently — almost
   certainly a host firewall not letting this docker bridge through.
   fix: ufw allow from 172.16.0.0/12 to any port $port proto tcp
serving: $name -> 503 (instant); the rest of the site is unaffected.
recheck: automatically (healthcheck), or once the firewall rule is added.
===============================================================
EOF
    fi
    return 1
}

# ---------------------------------------------------------------------------
# include-file writer
# ---------------------------------------------------------------------------

write_include() {
    name="$1"; hostport="$2"; ok="$3"; diagnosis="$4"
    file="$GATE_DIR/$name.conf"
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if [ "$ok" -eq 0 ]; then
        raw_host="${hostport%:*}"; port="${hostport##*:}"
        resolved_host="$(resolve_host "$raw_host")"
        cat > "$file" <<EOF
# stapel-gate: $name reachable at $ts — resolved $raw_host -> $resolved_host
proxy_pass http://$resolved_host:$port;
EOF
    else
        cat > "$file" <<EOF
# stapel-gate: $name DEGRADED at $ts
# $(echo "$diagnosis" | tr '\n' ' ' | sed 's/#/(hash)/g')
add_header X-Stapel-Degraded "$name" always;
default_type text/plain;
return 503 "seam degraded: $name ($hostport) unreachable — see nginx error log / docker compose logs nginx for diagnosis\n";
EOF
    fi
}

# current include state: 0 = currently serving proxy_pass (reachable last we
# wrote), 1 = currently serving 503 (degraded last we wrote), 2 = no include
# yet.
current_state() {
    file="$GATE_DIR/$1.conf"
    [ -f "$file" ] || { echo 2; return; }
    grep -q '^proxy_pass' "$file" && echo 0 || echo 1
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

if [ "${1:-}" = "--probe-only" ]; then
    name="${entry%%=*}"; hostport="${entry#*=}"
    if diagnosis="$(probe "$name" "$hostport")"; then
        log "$name: OK ($hostport)"
        exit 0
    fi
    printf '%s\n' "$diagnosis" | while IFS= read -r line; do log "$line"; done
    exit 1
fi

if [ -z "$TARGETS" ]; then
    log "CONFIG ERROR: UPSTREAM_GATE_TARGETS is empty — the gate is mounted"
    log "but has nothing to check. An empty list would silently disable the"
    log "gate, which is itself a defect; refusing to start. Set"
    log "UPSTREAM_GATE_TARGETS=\"name=host:port ...\", or unmount the gate."
    exit 1
fi

# Config-shape validation FIRST, over every entry, before any probing: a
# malformed target is a config error (fails closed, does not start) — it
# will never self-heal by retrying, unlike an environment/reachability
# problem, so it must not be handled inside the retry loop below.
for entry in $TARGETS; do
    case "$entry" in
        *=*) : ;;
        *) log "CONFIG ERROR: \"$entry\" is not name=host:port"; exit 1 ;;
    esac
    name="${entry%%=*}"; hostport="${entry#*=}"
    if [ -z "$name" ] || [ -z "$hostport" ]; then
        log "CONFIG ERROR: \"$entry\" is not name=host:port"
        exit 1
    fi
    case "$hostport" in
        *:*)
            port="${hostport##*:}"
            case "$port" in
                ''|*[!0-9]*)
                    log "CONFIG ERROR: \"$hostport\" (target \"$name\") has a non-numeric port"
                    exit 1
                    ;;
            esac
            ;;
        *)
            log "CONFIG ERROR: \"$hostport\" (target \"$name\") is not host:port"
            exit 1
            ;;
    esac
done

overall_rc=0
reloaded=0

for entry in $TARGETS; do
    name="${entry%%=*}"; hostport="${entry#*=}"

    if [ "$ONCE" -eq 1 ]; then
        # healthcheck: exactly one probe, no retry loop.
        if diagnosis="$(probe "$name" "$hostport")"; then
            ok=0
        else
            ok=1
        fi
        prev_state="$(current_state "$name")"
        if [ "$ok" -eq 0 ]; then want_state=0; else want_state=1; fi
        if [ "$prev_state" != "$want_state" ]; then
            write_include "$name" "$hostport" "$ok" "$diagnosis"
            reloaded=1
            log "$name: state changed ($prev_state -> $want_state) — include rewritten"
        fi
        [ "$ok" -eq 0 ] || overall_rc=1
    else
        # startup: retry within TIMEOUT budget, but ALWAYS exit 0 in the end
        # regardless of outcome — the whole point of v2.
        start_ts=$(date +%s 2>/dev/null || echo 0)
        deadline=$(( start_ts + TIMEOUT ))
        ok=1
        diagnosis=""
        while :; do
            if diagnosis="$(probe "$name" "$hostport")"; then
                ok=0
                break
            fi
            now=$(date +%s 2>/dev/null || echo 0)
            [ "$now" -ge "$deadline" ] && break
            log "waiting for $name ($hostport) ..."
            sleep 2
        done
        write_include "$name" "$hostport" "$ok" "$diagnosis"
        if [ "$ok" -eq 0 ]; then
            log "$name: OK"
        else
            printf '%s\n' "$diagnosis" | while IFS= read -r line; do log "$line"; done
        fi
    fi
done

if [ "$ONCE" -eq 1 ]; then
    if [ "$reloaded" -eq 1 ]; then
        nginx -s reload 2>&1 | while IFS= read -r line; do log "$line"; done
    fi
    exit "$overall_rc"
fi

# Startup ALWAYS exits 0 — config errors above already exited 1 on their own;
# everything past that point is an environment condition, and v2's rule is
# that those never block nginx from starting.
exit 0
"""
