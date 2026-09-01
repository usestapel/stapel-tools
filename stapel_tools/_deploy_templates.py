"""``deploy/`` scaffold — owner decision, §57 revision: the generator emits
prod/stage deploy scripts WITH a hard gate against the default/committed dev
env. The committed ``.env.local`` (see _local_env_templates.py) is safe exactly
because these scripts (and stapel-core's prodguard at boot) refuse it.

Two files, both POSIX sh (no bashisms — runs on any stand):

- ``deploy/check-env.sh <env-file>`` — the gate itself, standalone so CI can
  call it too. Refuses an env carrying ANY dev marker:
    * ``STAPEL_LOCAL_ENV=1``           (the committed dev env's explicit flag)
    * ``SECRET_KEY``/``JWT_SECRET_KEY`` with a placeholder/dev prefix
      (django-insecure-, dev-insecure-, change_me/changeme) or missing
      SECRET_KEY entirely
    * ``DEBUG=true``                  (any casing)
    * ``DJANGO_ENV`` set to anything but ``prod``
    * ``POSTGRES_PASSWORD`` in the known default set (stapel/change_me/…)
    * ``DJANGO_SUPERUSER_PASSWORD=admin`` (the dev default)
    * ``EMAIL_PROVIDER=mock`` / ``SMS_PROVIDER=mock`` set explicitly
  Mirrors (a superset of) what stapel-core's prodguard enforces at boot —
  the script version fails BEFORE containers restart, the prodguard version
  fails even if someone bypasses the script. NOTE for core (candidate): the
  in-boot guard today covers secrets/db-password only; a prodguard-class
  system check for "mock provider while not DEBUG" does not exist yet.

- ``deploy/deploy.sh [env-file]`` — gate + build + up over the PROD compose
  (docker-compose.yml; never docker-compose.local.yml). Default env-file:
  ``.env`` at the project root. Run it on the stand, from anywhere.

- ``deploy/preflight.sh [env-file]`` (env-address-class-v2.md §3.5) —
  probes every env-boundary upstream (``PREFLIGHT_TARGETS``, same
  ``name=host:port`` shape as the nginx gate's ``UPSTREAM_GATE_TARGETS``)
  from a ONE-SHOT container placed in the project's own compose network —
  the actual seam nginx will use, not a guess from the host's own routing.
  Reuses ``service-configs/nginx/40-upstream-gate.sh``'s ``--probe-only``
  mode (single source of truth for the resolve/probe/triage diagnosis, so
  preflight and the running gate can never disagree). Called from
  ``deploy.sh`` BEFORE ``up`` — the ONE place in this whole class where
  fail-closed on a reachability problem is the correct answer: stopping a
  deploy before it restarts anything is not the same as taking the running
  site down (env-address-class-v2.md §3.5's own distinction). No
  ``PREFLIGHT_TARGETS`` set -> nothing to probe (a project with no
  host-network dependency), exits 0.

- ``deploy/verify-stand-state.sh [env-file]`` — the POST-condition gate.
  Everything above runs BEFORE ``up`` and therefore verifies an intention;
  this one runs after and verifies the RESULT. Nothing restarting beyond a
  baseline captured before ``up`` and compared as a DELTA (a lifetime restart
  counter reported as "since this deploy" is a false claim), nothing
  unhealthy, nothing dead non-zero, and no service running behind its own
  migrations. ``--baseline`` writes the snapshot; the same script owns both
  ends so the format cannot drift.

- ``deploy/smoke-services.sh [host]`` — probes every declared service's
  ``/api/health/`` through the stand's own nginx and makes the result the
  verdict. Two traps it is built not to have: a loop that ``set -e``s out on
  the first unreachable service (reporting two of eight while exiting zero),
  and a check whose subject is absent reading as healthy.

Three more, each one a live incident turned into a gate:

- ``deploy/release-static.sh <built-dir> <target-root> [release-id]`` — the
  canonical ``releases/`` + ``current`` static deploy, so nobody hand-rolls
  one from a memory note again. INCIDENT: a static frontend was deployed by
  following a stale note instead of reading the host, and ``rsync --delete``
  ran over the release ROOT — the root held the ``current`` symlink, so the
  live release was deleted (15 minutes of 404s). Two rules fall out and both
  are mechanical here: deploy control reads FACTS from the target
  (``readlink current`` before AND after, the after being the verdict), and
  ``--delete`` is refused anywhere but inside the fresh per-release dir
  (``refuse_delete_over_root``). Standalone — deploy.sh does not call it.

- ``deploy/each.sh <list-file> <cmd> [args...]`` — the per-element outcome
  gate. INCIDENT: ``while read domain; do docker compose run certbot ...;
  done < list`` — the inner command ate the loop's stdin, so only the FIRST
  domain got a certificate, and the check accepted "certificate issued for
  the first site" as success FOR ALL of them. Rules: N inputs -> N verified
  outcomes (a result count that does not match the item count is itself a
  failure), and every item runs with stdin detached (``</dev/null``).
  Standalone — the runbooks call it.

- ``deploy/verify-host-config.sh`` — config that lives OUTSIDE the repo,
  detected at deploy time. INCIDENT: one vhost's CSP existed in two layers —
  a file in the repo and a hand-edited file on the host — and they drifted.
  Reads ``deploy/host-config.manifest`` (``<repo-relative-path>
  <target-absolute-path>`` per line), diffs each declared file against the
  target, and sweeps every managed target DIRECTORY for files no manifest
  line declares. Wired into deploy.sh right after check-env.sh, i.e. BEFORE
  build/up, while the old stand is still serving.
"""

CHECK_ENV_SH = """\
#!/bin/sh
# deploy/check-env.sh — hard gate against deploying a dev/default env
# (generated by stapel-create-project; owner decision, §57 revision).
#
# Usage: deploy/check-env.sh <env-file>
# Exit 0 = env acceptable for a stand; exit 1 = dev/default markers found.
set -eu

ENV_FILE="${1:?usage: check-env.sh <env-file>}"

if [ ! -f "$ENV_FILE" ]; then
    echo "check-env: $ENV_FILE does not exist" >&2
    exit 1
fi

fail=0
say() { echo "check-env: $ENV_FILE: $1" >&2; fail=1; }

# Read a key's value (last assignment wins, comments ignored).
get() {
    grep -E "^[[:space:]]*$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- \
        | tr -d '"' | tr -d "'" || true
}

lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

# 1. The committed dev env carries an explicit machine-readable flag.
if [ -n "$(get STAPEL_LOCAL_ENV)" ]; then
    say "STAPEL_LOCAL_ENV is set — this is the COMMITTED LOCAL DEV env (.env.local). Deploying it is forbidden; generate a real prod env (fresh random secrets, see .env.example)."
fi

# 2. Placeholder / dev-marked secrets (same prefix family stapel-core's
#    prodguard refuses at boot; the script fails before containers restart).
for key in SECRET_KEY JWT_SECRET_KEY; do
    value="$(lower "$(get "$key")")"
    case "$value" in
        django-insecure-*|dev-insecure-*|change_me*|changeme*)
            say "$key is a dev/placeholder value ('$(get "$key" | cut -c1-24)…'). Generate a real random secret." ;;
    esac
done
if [ -z "$(get SECRET_KEY)" ]; then
    say "SECRET_KEY is missing/empty. A stand env must set a real random secret."
fi

# 3. Debug / env-tier markers.
case "$(lower "$(get DEBUG)")" in
    true|1|yes) say "DEBUG=true — a stand never runs with DEBUG on." ;;
esac
env_tier="$(get DJANGO_ENV)"
if [ -n "$env_tier" ] && [ "$env_tier" != "prod" ]; then
    say "DJANGO_ENV=$env_tier — a stand env must set DJANGO_ENV=prod (or omit it for the compose prod settings tier)."
fi

# 4. Default credentials.
case "$(lower "$(get POSTGRES_PASSWORD)")" in
    stapel|change_me|changeme|"")
        say "POSTGRES_PASSWORD is the default/placeholder. Set a real random password." ;;
esac
if [ "$(lower "$(get DJANGO_SUPERUSER_PASSWORD)")" = "admin" ]; then
    say "DJANGO_SUPERUSER_PASSWORD=admin is the dev default. Set a real password or unset to skip superuser auto-creation."
fi

# 5. Mock providers pinned explicitly (dev-only convenience).
for key in EMAIL_PROVIDER SMS_PROVIDER; do
    if [ "$(lower "$(get "$key")")" = "mock" ]; then
        say "$key=mock — a stand must use a real provider."
    fi
done

if [ "$fail" -ne 0 ]; then
    echo "" >&2
    echo "check-env: REFUSING to deploy a default/dev env. Generate a real prod env — the default is forbidden (see .env.example; stapel-core prodguard would refuse these values at boot anyway)." >&2
    exit 1
fi
echo "check-env: $ENV_FILE looks deployable (no dev/default markers)."
"""

PREFLIGHT_SH = """\
#!/bin/sh
# deploy/preflight.sh — probe every env-boundary upstream from a ONE-SHOT
# container in the project's own compose network, BEFORE deploy.sh restarts
# anything (generated by stapel-create-project;
# docs/pending/env-address-class-v2.md §3.5).
#
# Why a separate probe from the host, not just trusting the running gate:
# the running nginx gate (service-configs/nginx/40-upstream-gate.sh) NEVER
# blocks a start on an unreachable upstream (v2 policy — environment errors
# degrade loudly, they do not fail closed, so a broken seam does not take
# the rest of the site with it). That is exactly right for a container that
# is ALREADY serving traffic — and exactly wrong for a deploy that has not
# happened yet: here, refusing to proceed costs nothing (the OLD containers
# are still up and serving), so failing closed on a real reachability
# problem is strictly better than shipping a known-broken seam. This is the
# ONE place in the whole class where fail-closed is the right answer.
#
# Usage: deploy/preflight.sh [env-file]      (default: .env)
# Reads PREFLIGHT_TARGETS ("name=host:port ..." — same shape as the nginx
# gate's UPSTREAM_GATE_TARGETS) from the given env-file, or from the
# PREFLIGHT_TARGETS process env (process env wins). Not set/empty -> nothing
# to probe (a project with no host-network dependency configured) -> exit 0.
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

ENV_FILE="${1:-.env}"

get() {
    [ -f "$ENV_FILE" ] || return 0
    grep -E "^[[:space:]]*$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- \\
        | tr -d '"' | tr -d "'" || true
}

TARGETS="${PREFLIGHT_TARGETS:-$(get PREFLIGHT_TARGETS)}"

if [ -z "$TARGETS" ]; then
    echo "preflight: PREFLIGHT_TARGETS not set (checked env $ENV_FILE and the process environment) — nothing to probe, skipping."
    exit 0
fi

GATE_SCRIPT="service-configs/nginx/40-upstream-gate.sh"
if [ ! -f "$GATE_SCRIPT" ]; then
    echo "preflight: PREFLIGHT_TARGETS is set but $GATE_SCRIPT does not exist — nothing to reuse its probe/diagnosis logic from. Add the gate script (docs/pending/env-address-class-v2.md §3.4) before wiring PREFLIGHT_TARGETS." >&2
    exit 1
fi

# The default compose project name IS the directory name (docker compose's
# own rule) — override with PREFLIGHT_NETWORK or COMPOSE_PROJECT_NAME if a
# project's compose invocation pins a different one (-p / .env's
# COMPOSE_PROJECT_NAME).
NETWORK="${PREFLIGHT_NETWORK:-${COMPOSE_PROJECT_NAME:-$(basename "$PROJECT_ROOT")}_default}"

target_count=$(echo "$TARGETS" | wc -w | tr -d ' ')
echo "preflight: probing $target_count target(s) in network \\"$NETWORK\\" (the actual seam nginx will use) ..."

fail=0
for entry in $TARGETS; do
    name="${entry%%=*}"
    if docker run --rm --network "$NETWORK" \\
        -v "$PROJECT_ROOT/$GATE_SCRIPT:/gate.sh:ro" \\
        alpine:3.20 sh /gate.sh --probe-only "$entry"; then
        echo "preflight: $name OK"
    else
        echo "preflight: $name UNREACHABLE from network $NETWORK — see diagnosis above." >&2
        fail=1
    fi
done

if [ "$fail" -ne 0 ]; then
    echo "" >&2
    echo "preflight: REFUSING to deploy — at least one env-boundary upstream is unreachable from the project's own network. The OLD containers are still up; fix the seam, then re-run deploy.sh." >&2
    exit 1
fi

echo "preflight: all targets reachable."
"""

DEPLOY_SH = """\
#!/bin/sh
# deploy/deploy.sh — prod/stage deploy over the PROD compose file
# (generated by stapel-create-project; owner decision, §57 revision).
#
# Usage (from anywhere; env-file path is relative to the project root):
#   deploy/deploy.sh              # uses .env
#   deploy/deploy.sh .env.stage   # a stage env you generated
#
# Env-file naming canon: `.env.local` is the COMMITTED local-machine env
# (never deployable — the gate refuses it). The names `.env.dev`,
# `.env.stage`, `.env.prod` are RESERVED for STANDS: generated per stand
# with real random secrets (shape: .env.example), gitignored, never
# committed. "dev" in a file name means the dev STAND, not your machine.
#
# HARD GATE: refuses to run against an env carrying dev/default markers
# (the committed .env.local, placeholder secrets, DEBUG=true, mock providers)
# — see deploy/check-env.sh. Generate a real env per stand; never reuse the
# committed local-machine one.
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

ENV_FILE="${1:-.env}"

sh "$SCRIPT_DIR/check-env.sh" "$ENV_FILE"

# Config the repo declares but the HOST owns. Runs BEFORE build/up on purpose:
# a file hand-edited on the host outside the repo (the vhost CSP incident) must
# surface while the old stand is still serving and nothing has been rebuilt —
# after `up` the drift is either shipped over or silently still there.
if [ -f "$SCRIPT_DIR/host-config.manifest" ]; then
    sh "$SCRIPT_DIR/verify-host-config.sh"
fi

sh "$SCRIPT_DIR/preflight.sh" "$ENV_FILE"

echo "deploy: building images (docker-compose.yml, env: $ENV_FILE)..."
docker compose --env-file "$ENV_FILE" -f docker-compose.yml build

# Restart baseline, taken BEFORE anything is restarted. RestartCount is
# lifetime-of-container, so without this snapshot the post-condition gate can
# only report a number it cannot attribute to this deploy.
sh "$SCRIPT_DIR/verify-stand-state.sh" --baseline "$ENV_FILE"

echo "deploy: starting services..."
docker compose --env-file "$ENV_FILE" -f docker-compose.yml up -d --remove-orphans

# Post-condition: is the deployment in the state it claims to be in? Nothing
# above this line looks at the RESULT of `up` - they all ran before it.
sh "$SCRIPT_DIR/verify-stand-state.sh" "$ENV_FILE"
sh "$SCRIPT_DIR/smoke-services.sh" "${SMOKE_HOST:-localhost}"

echo "deploy: done. Status:"
docker compose --env-file "$ENV_FILE" -f docker-compose.yml ps
"""

VERIFY_STAND_STATE_SH = """\
#!/bin/sh
# deploy/verify-stand-state.sh - the POST-`up` deployment gate: is the
# deployment in the state it claims to be in?
#
# A healthcheck answers "did the process bind a port". That can pass every ten
# seconds for twelve hours on a stand whose service is serving 500s because a
# migration failed at boot, and it says nothing at all about a worker that
# never started. This is what looks.
#
# Three questions, all answered against the RUNNING stack:
#
#   1. Did the containers settle? A container whose health is still `starting`
#      has not answered anything yet, so the verdict waits for it (bounded).
#   2. Is any container restarting, unhealthy, or dead non-zero? Restarts are
#      counted as a DELTA against a baseline taken before `up` - a lifetime
#      counter reported as "since this deploy" is a claim the gate did not
#      measure, which is the same defect it exists to catch.
#   3. Is any service running on a schema behind its own code? Asked with
#      `manage.py migrate --check` INSIDE each container, so the verdict comes
#      from the code that is actually running. No --skip-checks anywhere: it
#      silences every check, not the one in the way.
#
# Usage:
#   deploy/verify-stand-state.sh --baseline [env-file]   snapshot before `up`
#   deploy/verify-stand-state.sh [env-file]              the gate, after `up`
#
# Env: DOCKER, COMPOSE_FILE, SERVICES_CONF, SERVICE_PREFIX, MAX_RESTARTS,
#      SETTLE_TIMEOUT (seconds), RESTART_BASELINE (snapshot path).
#
# Deliberately not `set -e`: this gate reports every failure it finds. A gate
# that stops at the first problem tells you about one of four.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

MODE="check"
if [ "${1:-}" = "--baseline" ]; then
    MODE="baseline"
    shift
fi
ENV_FILE="${1:-.env}"

DOCKER="${DOCKER:-docker}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
SERVICES_CONF="${SERVICES_CONF:-$PROJECT_ROOT/services.conf}"
SERVICE_PREFIX="${SERVICE_PREFIX:-svc-}"
# A container may restart once or twice while a dependency comes up. Three
# restarts SINCE THE BASELINE is a loop, not a race.
MAX_RESTARTS="${MAX_RESTARTS:-3}"
SETTLE_TIMEOUT="${SETTLE_TIMEOUT:-180}"
RESTART_BASELINE="${RESTART_BASELINE:-${TMPDIR:-/tmp}/stapel-restart-baseline-$(basename "$PROJECT_ROOT")}"

compose() { $DOCKER compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }

container_ids() { compose ps --all -q 2>/dev/null; }

INSPECT_FMT='{{.Id}} {{.Name}} {{.State.Status}} {{.RestartCount}} {{.State.ExitCode}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'

# ── baseline mode ────────────────────────────────────────
if [ "$MODE" = "baseline" ]; then
    ids="$(container_ids)"
    {
        echo "# taken $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        if [ -n "$ids" ]; then
            # Keyed on container ID, not name: a container recreated by the
            # deploy keeps its name but gets a fresh ID and a zeroed count, so
            # it is simply absent here and correctly reads as zero.
            $DOCKER inspect --format '{{.Id}} {{.Name}} {{.RestartCount}}' $ids 2>/dev/null
        fi
    } > "$RESTART_BASELINE"
    echo "[gate] restart baseline written to $RESTART_BASELINE"
    exit 0
fi

failures=0
fail() {
    failures=$((failures + 1))
    echo "  FAIL: $*" >&2
}

# ── service list ─────────────────────────────────────────
# Read it, then assert the count: a loop that silently stops halfway is the
# defect this gate exists to catch, so it must not have it.
services="$(sed 's/#.*//; s/[[:space:]]//g' "$SERVICES_CONF" 2>/dev/null | grep -v '^$')"
declared="$(printf '%s\\n' "$services" | grep -c '^..*$')"
service_count=0
for s in $services; do service_count=$((service_count + 1)); done
if [ "$service_count" -ne "$declared" ]; then
    echo "REFUSING: parsed $service_count services from $SERVICES_CONF, file declares $declared" >&2
    exit 1
fi
echo "[gate] $service_count service(s) declared: $services"

ids="$(container_ids)"
if [ -z "$ids" ]; then
    echo "REFUSING: no containers in this compose project" >&2
    exit 1
fi

# ── 1. settle ────────────────────────────────────────────
# `starting` is not a verdict; judging it would flag every deploy. Bounded, and
# running out of time is itself a failure - not a reason to judge anyway.
echo "[gate] waiting for healthchecks to settle (max ${SETTLE_TIMEOUT}s)"
waited=0
settled=0
while :; do
    starting="$($DOCKER inspect --format "$INSPECT_FMT" $ids 2>/dev/null | grep -c ' starting$')"
    if [ "$starting" = "0" ]; then
        settled=1
        break
    fi
    [ "$waited" -ge "$SETTLE_TIMEOUT" ] && break
    sleep 5
    waited=$((waited + 5))
    ids="$(container_ids)"
done
if [ "$settled" -ne 1 ]; then
    fail "healthchecks did not settle within ${SETTLE_TIMEOUT}s ($starting container(s) still starting)"
fi

# ── 2. container state ───────────────────────────────────
echo "[gate] container state"
baseline_taken=""
if [ -f "$RESTART_BASELINE" ]; then
    baseline_taken="$(sed -n '1s/^# taken //p' "$RESTART_BASELINE")"
fi
if [ -n "$baseline_taken" ]; then
    echo "[gate]   restart delta measured against the baseline taken $baseline_taken"
else
    # Not silently skipped, and not reported as something it is not. Without a
    # baseline the only honest statement about a restart count is its lifetime
    # value, which no deploy can be blamed for.
    echo "[gate]   restart-delta check NOT ARMED: no baseline at $RESTART_BASELINE"
    echo "[gate]   lifetime restart counts are reported below as context only"
    echo "[gate]   (deploy.sh takes the baseline; run this script with --baseline before \\`up\\`)"
fi

states="${TMPDIR:-/tmp}/stapel-stand-state.$$"
$DOCKER inspect --format "$INSPECT_FMT" $ids > "$states" 2>/dev/null
checked=0
while read -r id name status restarts exitcode health; do
    [ -n "${id:-}" ] || continue
    checked=$((checked + 1))
    name="${name#/}"

    before=0
    if [ -n "$baseline_taken" ]; then
        before="$(awk -v want="$id" '$1 == want {print $3; exit}' "$RESTART_BASELINE")"
        before="${before:-0}"
    fi
    delta=$((restarts - before))
    [ "$delta" -lt 0 ] && delta=0

    case "$status" in
        restarting)
            fail "$name is restarting now (lifetime restart count $restarts)" ;;
        exited|dead)
            # A one-shot writer (frontend-build) exits 0 by design; anything
            # else is a container that did not survive the deploy.
            if [ "$exitcode" != "0" ]; then
                fail "$name is $status with exit code $exitcode"
            fi ;;
        running)
            if [ "$health" = "unhealthy" ]; then
                fail "$name is running but unhealthy"
            fi
            if [ -n "$baseline_taken" ]; then
                if [ "$delta" -ge "$MAX_RESTARTS" ]; then
                    fail "$name restarted $delta times since the baseline at $baseline_taken (crash loop; lifetime $restarts)"
                fi
            elif [ "$restarts" -gt 0 ]; then
                echo "  note: $name has $restarts lifetime restarts"
                echo "        (no baseline: this deploy is not implicated either way)"
            fi ;;
    esac
done < "$states"
rm -f "$states"
echo "[gate]   inspected $checked containers"

# ── 3. schema drift ──────────────────────────────────────
# A container answering 200 on a schema behind its code is the defect that
# started this. Asked of every service, and a service that cannot answer is a
# failure too - "could not check" is not "fine".
echo "[gate] schema at head"
for s in $services; do
    out="$(compose exec -T "${SERVICE_PREFIX}${s}" python manage.py migrate --check 2>&1)"
    rc=$?
    if [ "$rc" -ne 0 ]; then
        fail "${SERVICE_PREFIX}${s}: schema behind code, or a system check failed (exit $rc)"
        echo "$out" | tail -5 | sed 's/^/        /' >&2
    else
        echo "  ok:   ${SERVICE_PREFIX}${s} at head"
    fi
done

if [ "$failures" -ne 0 ]; then
    echo "[gate] REFUSING TO ACCEPT THIS DEPLOY: $failures problem(s)" >&2
    exit 1
fi
echo "[gate] deployment is in the state it claims"
"""

SMOKE_SERVICES_SH = """\
#!/bin/sh
# deploy/smoke-services.sh - probe /api/health/ on every declared service and
# make the result the VERDICT.
#
# Two traps this is built not to have, both seen in hand-written smoke loops:
#
#   * `code="$(curl ...)"` under `set -e` - a service whose curl cannot connect
#     ends the script mid-loop. The remaining services are never probed, and
#     their absence from the output reads as "not printed" rather than "not
#     checked". Hence the probed-count assertion at the end.
#   * nothing ever compares the code to anything. A wall of 502s must not be a
#     successful deploy.
#
# Usage: deploy/smoke-services.sh [host]      # default localhost (the stand
#                                             # itself, through its own nginx)
# Env:   SERVICES_CONF, CURL_TIMEOUT (default 10)
#
# Exit 0 only if every declared service answered 200 with a schema probe in
# the body.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

TARGET_HOST="${1:-localhost}"
SERVICES_CONF="${SERVICES_CONF:-$PROJECT_ROOT/services.conf}"
CURL_TIMEOUT="${CURL_TIMEOUT:-10}"

services="$(sed 's/#.*//; s/[[:space:]]//g' "$SERVICES_CONF" 2>/dev/null | grep -v '^$')"
declared="$(printf '%s\\n' "$services" | grep -c '^..*$')"

if [ "$declared" -eq 0 ]; then
    echo "smoke: $SERVICES_CONF declares no services - nothing to probe."
    exit 0
fi

body="${TMPDIR:-/tmp}/stapel-smoke-body.$$"
trap 'rm -f "$body"' EXIT

failures=0
probed=0
for s in $services; do
    path="/$s/api/health/"
    # `|| true`: a failed curl must not end the loop. The status is discarded
    # on purpose - %{http_code} is 000 on a connection failure, which IS the
    # verdict, and it is checked below.
    code="$(curl -sk --max-time "$CURL_TIMEOUT" -o "$body" -w '%{http_code}' "https://$TARGET_HOST$path" 2>/dev/null || true)"
    code="${code:-000}"
    if [ "$code" = "000" ]; then
        code="$(curl -s --max-time "$CURL_TIMEOUT" -o "$body" -w '%{http_code}' "http://$TARGET_HOST$path" 2>/dev/null || true)"
        code="${code:-000}"
    fi
    probed=$((probed + 1))

    verdict="ok"
    if [ "$code" != "200" ]; then
        verdict="FAIL (http $code)"
    elif ! grep -q '"schema"' "$body"; then
        # Presence only. A check whose subject is absent is indistinguishable
        # from a check that passes, so the key has to be there - that is how a
        # container still running a pre-probe image gets caught.
        #
        # The VERDICT is deliberately not read here.
        # deploy/verify-stand-state.sh asks `manage.py migrate --check` inside
        # each container, which is the authority: it uses the code that is
        # actually running. Two mechanisms answering the same question is how
        # they come to disagree.
        verdict="FAIL (no schema probe in health body - stale image?)"
    fi
    [ "$verdict" = "ok" ] || failures=$((failures + 1))
    printf "  %-16s %-28s %s  %s\\n" "$s" "$path" "$code" "$verdict"
done

if [ "$probed" -ne "$declared" ]; then
    echo "SMOKE INCOMPLETE: probed $probed of $declared services" >&2
    exit 1
fi
if [ "$failures" -ne 0 ]; then
    echo "SMOKE FAILED: $failures of $declared services are not serving correctly" >&2
    exit 1
fi
echo "OK: $declared/$declared services healthy and serving a schema probe"
"""

RELEASE_STATIC_SH = '''\
#!/bin/sh
# deploy/release-static.sh - the canonical releases/ + current static deploy.
#
# INCIDENT this exists to make unrepeatable: a static frontend was deployed by
# following a stale memory note about the host's layout instead of reading the
# host, and `rsync --delete` was pointed at the release ROOT. The root is where
# the `current` symlink lives, so --delete removed the live release: 15 minutes
# of 404s. Two rules, both mechanical below:
#
#   1. FACTS FIRST. `readlink <root>/current` is read from the TARGET before
#      anything happens and again after the flip. The second read is the
#      VERDICT - this script cannot report a successful deploy it did not
#      observe. A note, a runbook or a memory is never the input.
#   2. `rsync --delete` is allowed ONLY into the fresh per-release directory,
#      never against <root> (refuse_delete_over_root below).
#
# Usage: deploy/release-static.sh <built-dir> <target-root> [release-id]
# Env:   DEPLOY_HOST     ssh host; empty = <target-root> is a LOCAL path
#        KEEP_RELEASES   how many release dirs to keep (default 5)
set -eu

SRC="${1:?usage: release-static.sh <built-dir> <target-root> [release-id]}"
ROOT="${2:?usage: release-static.sh <built-dir> <target-root> [release-id]}"
ROOT="${ROOT%/}"
SRC="${SRC%/}"
DEPLOY_HOST="${DEPLOY_HOST:-}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"

refuse() {
    echo "release-static: REFUSING: $*" >&2
    exit 1
}

[ -d "$SRC" ] || refuse "built dir $SRC does not exist"

# One indirection for every target-side command, so local and remote runs
# cannot diverge (and so the whole script is testable without a host).
run_target() {
    if [ -n "$DEPLOY_HOST" ]; then
        ssh "$DEPLOY_HOST" "$1"
    else
        sh -c "$1"
    fi
}

# Content hash with POSIX tools only (cksum, not sha256sum/shasum - those two
# have different names on different stands, which is how a "deterministic" id
# becomes a stand-specific one).
content_hash() {
    ( cd "$1" && find . -type f | LC_ALL=C sort | while IFS= read -r f; do
        printf '%s ' "$f"
        cksum < "$f"
    done ) | cksum | awk '{printf "%08x", $1}'
}

RELEASE_ID="${3:-}"
if [ -z "$RELEASE_ID" ]; then
    RELEASE_ID="$(date -u '+%Y%m%dT%H%M%SZ')-$(content_hash "$SRC")"
fi
DEST="$ROOT/releases/$RELEASE_ID"

# --- FACTS, read from the target, before anything changes -----------------
before="$(run_target "readlink '$ROOT/current' 2>/dev/null || true")"
echo "release-static: target=${DEPLOY_HOST:-<local>} root=$ROOT"
echo "release-static: current(before)=${before:-<none>}"

# A `current` that exists but is not a symlink means the layout is NOT the one
# this script assumes. Guessing here is the incident, so it refuses instead:
# the flip below would move the new link INTO that directory.
if run_target "[ -e '$ROOT/current' ] && [ ! -L '$ROOT/current' ]"; then
    refuse "$ROOT/current exists and is NOT a symlink. This script only manages a releases/ + current layout; it will not guess what to do with a real file/directory there. Nothing has been changed."
fi

run_target "mkdir -p '$ROOT/releases'"

# --- the --delete guard ---------------------------------------------------
refuse_delete_over_root() {
    dest="${1%/}"
    if [ "$dest" = "$ROOT" ]; then
        refuse "rsync --delete destination is the release ROOT ($ROOT). That root holds the 'current' symlink and the other releases - deleting into it is exactly the incident (15 min of 404s). --delete only ever goes inside $ROOT/releases/<id>/."
    fi
    case "$dest" in
        "$ROOT"/releases/?*) : ;;
        *) refuse "rsync --delete destination '$dest' is not inside $ROOT/releases/. --delete is allowed nowhere else." ;;
    esac
    # A symlink in the destination listing means this is not the fresh
    # per-release dir it is supposed to be (a release root, or a reused path
    # someone linked into). --delete would follow the layout down.
    links="$(run_target "find '$dest' -maxdepth 1 -type l -print 2>/dev/null | head -n 5 || true")"
    if [ -n "$links" ]; then
        refuse "rsync --delete destination '$dest' contains symlink(s): $(echo $links). This is not a fresh release directory."
    fi
}

run_target "mkdir -p '$DEST'"
refuse_delete_over_root "$DEST"

# The default id embeds the content hash, so re-running the same build is a
# no-op into the same dir. An EXPLICIT id that is already the live release is
# not: --delete then edits what is being served right now. Said out loud
# rather than refused - the runbooks do use a fixed id on purpose.
if [ "$before" = "releases/$RELEASE_ID" ]; then
    echo "release-static: NOTE - releases/$RELEASE_ID is the LIVE release; this uploads into the directory currently being served (no atomic flip is possible for it)."
fi

echo "release-static: uploading $SRC -> $DEST"
if [ -n "$DEPLOY_HOST" ]; then
    rsync -a --delete "$SRC/" "$DEPLOY_HOST:$DEST/"
else
    rsync -a --delete "$SRC/" "$DEST/"
fi

# --- the flip -------------------------------------------------------------
# `mv -Tf` (never dereference the destination) is GNU-only: on BSD/macOS mv it
# is an illegal option. And plain `mv -f newlink current` is NOT a portable
# substitute - when `current` is a symlink to a directory, both GNU mv (without
# -T) and BSD mv FOLLOW it and drop the new link INSIDE the old release, so the
# site stays on the old release while the deploy reports success. Verified on
# darwin. So: try the GNU form, fall back to `ln -sfn` (-n/-h = do not follow,
# supported by GNU, BSD and busybox ln), and let the post-fact readlink below
# be the actual verdict either way.
TMPLINK="current.flip.$$"
if ! run_target "cd '$ROOT' && ln -s 'releases/$RELEASE_ID' '$TMPLINK' && mv -Tf '$TMPLINK' current 2>/dev/null"; then
    run_target "cd '$ROOT' && rm -f '$TMPLINK'"
    run_target "cd '$ROOT' && ln -sfn 'releases/$RELEASE_ID' current"
fi

# --- POST-FACT: the verdict is what the target says, not what we did ------
after="$(run_target "readlink '$ROOT/current' 2>/dev/null || true")"
echo "release-static: current(after)=${after:-<none>}"
if [ "$after" != "releases/$RELEASE_ID" ]; then
    refuse "the flip did not take: $ROOT/current points at '${after:-<none>}', expected 'releases/$RELEASE_ID'. The new release is uploaded at $DEST; the site is NOT on it."
fi

# --- prune, only ever inside releases/ ------------------------------------
listing="$(run_target "ls -1 '$ROOT/releases' 2>/dev/null || true" | LC_ALL=C sort)"
total="$(printf '%s\\n' "$listing" | grep -c '^..*$' || true)"
if [ "${total:-0}" -gt "$KEEP_RELEASES" ]; then
    prune=$((total - KEEP_RELEASES))
    printf '%s\\n' "$listing" | head -n "$prune" | while IFS= read -r old; do
        [ -n "$old" ] || continue
        # never the one current points at, and never a path outside releases/
        [ "$old" != "$RELEASE_ID" ] || continue
        case "$old" in */*|..|.) continue ;; esac
        echo "release-static: pruning old release $old"
        run_target "rm -rf '$ROOT/releases/$old'"
    done
fi

echo "release-static: OK - $ROOT/current -> releases/$RELEASE_ID (kept last $KEEP_RELEASES releases)"
'''

EACH_SH = '''\
#!/bin/sh
# deploy/each.sh - run a command once per list item and verify EVERY outcome.
#
# INCIDENT: `while read domain; do docker compose run certbot ...; done < list`
# — the inner command inherited (and ate) the loop's stdin, so the loop saw EOF
# after the first line and only the FIRST domain got a certificate. The check
# then read "certificate issued for the FIRST site" and accepted it as success FOR
# ALL the domains. One success must never pass for all.
#
# So, two rules, both here:
#   * every item runs with stdin detached (`</dev/null`) - the inner command
#     cannot consume the list;
#   * N inputs -> N verified outcomes. Exit 0 only if every item passed AND the
#     number of results equals the number of items; a short result table is a
#     failure in itself, not a shorter success.
#
# Usage: deploy/each.sh <list-file> <cmd> [args...]
#   Runs: <cmd> [args...] <item>    once per non-empty, non-comment line.
# Exit:  0 = all items passed; 1 = any item failed or the tally is short.
set -u

LIST="${1:?usage: each.sh <list-file> <cmd> [args...]}"
shift
if [ "$#" -lt 1 ]; then
    echo "each: no command given (usage: each.sh <list-file> <cmd> [args...])" >&2
    exit 2
fi
if [ ! -f "$LIST" ]; then
    echo "each: list file $LIST does not exist" >&2
    exit 2
fi

ITEMS="${TMPDIR:-/tmp}/stapel-each-items.$$"
trap 'rm -f "$ITEMS"' EXIT

sed 's/^[[:space:]]*//; s/[[:space:]]*$//' "$LIST" \\
    | grep -v '^#' | grep -v '^$' > "$ITEMS" || true
total="$(grep -c '^' "$ITEMS" || true)"
total="${total:-0}"

if [ "$total" -eq 0 ]; then
    # "nothing was checked" is not a pass - that is the same shape of claim the
    # incident made.
    echo "each: $LIST contains no items - nothing was run, so nothing is verified." >&2
    exit 1
fi

echo "each: $total item(s) from $LIST, command: $*"

passed=0
results=0
while IFS= read -r item; do
    [ -n "$item" ] || continue
    # </dev/null is the fix: the inner command gets an empty stdin of its own
    # and can never swallow the remaining items.
    if "$@" "$item" </dev/null; then
        rc=0
    else
        rc=$?
    fi
    results=$((results + 1))
    if [ "$rc" -eq 0 ]; then
        passed=$((passed + 1))
        printf '  PASS  %s\\n' "$item"
    else
        printf '  FAIL  %s (exit %s)\\n' "$item" "$rc"
    fi
done < "$ITEMS"

echo "each: $passed/$total passed"

if [ "$results" -ne "$total" ]; then
    echo "each: INCOMPLETE - $results outcome(s) for $total item(s). Some items were never run; a short table is not a shorter success." >&2
    exit 1
fi
if [ "$passed" -ne "$total" ]; then
    echo "each: FAILED - $((total - passed)) of $total item(s) did not pass." >&2
    exit 1
fi
'''

VERIFY_HOST_CONFIG_SH = '''\
#!/bin/sh
# deploy/verify-host-config.sh - config that lives OUTSIDE the repo, found at
# deploy time.
#
# INCIDENT: the same vhost's CSP existed in two layers - one file in the repo,
# one hand-edited on the host - and they drifted. Nothing in the deploy ever
# compared them, so the repo said one thing and the served site did another.
#
# What it checks, against deploy/host-config.manifest:
#   <repo-relative-path> <target-absolute-path>     one pair per line, # comments
#   1. every declared file: target content == repo content (missing on the
#      target counts as drift - the repo declares it should be there);
#   2. every managed target DIRECTORY: any file in it that no manifest line
#      declares is config outside the repo - the incident's second layer.
#
# Usage: deploy/verify-host-config.sh
# Env:   DEPLOY_HOST            ssh host; empty = the target paths are local
#        HOST_CONFIG_MANIFEST   manifest path (default deploy/host-config.manifest)
#
# Deliberately not `set -e` (same stance as verify-stand-state.sh): every
# finding is reported, then the verdict. A gate that stops at the first drift
# tells you about one of four.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

MANIFEST="${HOST_CONFIG_MANIFEST:-$SCRIPT_DIR/host-config.manifest}"
DEPLOY_HOST="${DEPLOY_HOST:-}"

if [ ! -f "$MANIFEST" ]; then
    echo "verify-host-config: no host-config manifest at $MANIFEST - nothing declared, nothing checked."
    exit 0
fi

run_target() {
    if [ -n "$DEPLOY_HOST" ]; then
        ssh "$DEPLOY_HOST" "$1"
    else
        sh -c "$1"
    fi
}

findings=0
finding() {
    findings=$((findings + 1))
    echo "  $*" >&2
}

WORK="${TMPDIR:-/tmp}/stapel-host-config.$$"
PAIRS="$WORK.pairs"
TARGETS="$WORK.targets"
DIRS="$WORK.dirs"
trap 'rm -f "$WORK".*' EXIT
: > "$TARGETS"
: > "$DIRS"

grep -v '^[[:space:]]*#' "$MANIFEST" | grep -v '^[[:space:]]*$' > "$PAIRS" || true

# Pass 1: collect the declared targets first, so the unmanaged sweep in pass 3
# knows the whole declared set regardless of the order lines appear in.
while read -r repo_path target_path rest; do
    [ -n "${repo_path:-}" ] || continue
    if [ "$repo_path" = "dir" ]; then
        finding "MANIFEST: the 'dir <repo-dir> <target-dir>' form is not supported in v1 - declare each file as its own <repo-path> <target-path> pair."
        continue
    fi
    if [ -z "${target_path:-}" ]; then
        finding "MANIFEST: line '$repo_path' has no target path (expected: <repo-relative-path> <target-absolute-path>)"
        continue
    fi
    echo "$target_path" >> "$TARGETS"
    dirname "$target_path" >> "$DIRS"
done < "$PAIRS"

# Pass 2: content, per declared file.
echo "verify-host-config: target=${DEPLOY_HOST:-<local>} manifest=$MANIFEST"
checked=0
while read -r repo_path target_path rest; do
    [ -n "${repo_path:-}" ] || continue
    [ "$repo_path" != "dir" ] || continue
    [ -n "${target_path:-}" ] || continue
    checked=$((checked + 1))
    if [ ! -f "$PROJECT_ROOT/$repo_path" ]; then
        finding "MANIFEST: $repo_path is declared but does not exist in the repo"
        continue
    fi
    if ! run_target "[ -f '$target_path' ]"; then
        finding "DRIFT: $target_path is MISSING on the target (the repo declares $repo_path belongs there)"
        continue
    fi
    run_target "cat '$target_path'" > "$WORK.file" 2>/dev/null
    if diff -u "$PROJECT_ROOT/$repo_path" "$WORK.file" > "$WORK.diff" 2>&1; then
        echo "  ok:    $repo_path == $target_path"
    else
        finding "DRIFT: $target_path differs from $repo_path - the host copy was edited outside the repo"
        sed -n '1,20p' "$WORK.diff" | sed 's/^/         /' >&2
    fi
    rm -f "$WORK.file" "$WORK.diff"
done < "$PAIRS"

# Pass 3: the unmanaged sweep. Every DISTINCT directory the manifest names is
# listed on the target; a file there that no manifest line declares is the
# second layer of config nobody's repo owns.
sort -u "$DIRS" > "$WORK.dirs.uniq"
while IFS= read -r d; do
    [ -n "$d" ] || continue
    # `! -type d`, not `-type f`: a hand-placed SYMLINK into another config is
    # the same second layer, and -type f would not see it.
    run_target "find '$d' -maxdepth 1 ! -type d -print 2>/dev/null || true" > "$WORK.listing"
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        if ! grep -Fxq "$f" "$TARGETS"; then
            finding "OUTSIDE THE REPO: $f lives in the managed directory $d but no manifest line declares it - config outside the repo drifts by construction (nothing in the repo can review it)."
        fi
    done < "$WORK.listing"
    rm -f "$WORK.listing"
done < "$WORK.dirs.uniq"

if [ "$findings" -ne 0 ]; then
    echo "" >&2
    echo "verify-host-config: $findings finding(s). Either the host copy is wrong (re-deploy it from the repo) or the repo is out of date (adopt the host's version into the repo, or declare the extra file in deploy/host-config.manifest). Two layers of the same config always drift." >&2
    exit 1
fi
echo "verify-host-config: OK - $checked declared file(s) match the target, no unmanaged config in the managed directories."
'''
