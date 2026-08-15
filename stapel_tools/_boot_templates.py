"""Boot contract — what every generated service runs before it serves.

Neither of the two shapes a hand-written bootstrap ends up in is correct:

* no ``set -e``: a failed ``migrate`` is stepped over and the server starts on
  a schema its code does not match. The process binds a port, the healthcheck
  passes, and the stand reports healthy while every endpoint touching the
  missing table returns 500.
* a blanket ``set -e``: every step becomes fatal, including the ones that are
  not. A failed ``collectstatic`` takes the API down over a CSS file, someone
  deletes the line, and the first shape is back.

So the step runner names the decision per step instead of in one flag:
``require`` (failure makes the service *wrong*) or ``optional`` (failure makes
it *ugly*), and ``bootstrap_done`` asserts the END STATE with
``migrate --check`` rather than trusting the step statuses. Statuses get lost
— to a pipe, a subshell, a later edit — so "bootstrap succeeded on an
unmigrated database" must not be expressible, and after that closer it is not.

The classification travels with the generator: a generated project's author
never has to rediscover which steps are which.

Emitted files (see create_project / new_service):
  scripts/bootstrap_lib.sh          the step runner, baked into every image
  scripts/service_schema_health.py  canonical schema-drift probe
  scripts/verify_boot_contract.sh   the meta-gate over every service
  <svc>/bootstrap.sh                the steps, classified
  <svc>/config/schema_health.py     per-service copy of the canonical probe
"""

BOOTSTRAP_LIB_PATH = "/usr/local/lib/stapel-bootstrap.sh"

BOOTSTRAP_LIB_SH = """\
# Stapel bootstrap step runner. POSIX sh - every service runs it under `sh`.
#
# Baked into each image by the service Dockerfile:
#     COPY scripts/bootstrap_lib.sh /usr/local/lib/stapel-bootstrap.sh
# and sourced by the service's bootstrap.sh:
#     . /usr/local/lib/stapel-bootstrap.sh || exit 1
#
# Verbs - every preparation step must go through one of them:
#
#     require  <label> <command...>   failure aborts the boot
#     optional <label> <command...>   failure is reported, the boot continues
#     bootstrap_done                  asserts the end state, then reports
#
# Why not a blanket `set -e`: it makes every step fatal, including the ones
# that are not, and the first time a cosmetic step takes the API down someone
# deletes the flag - which makes the next failed migration silent. Naming the
# verb per step keeps the decision in the script, where it can be argued with.
#
# The compose command is `sh bootstrap.sh && $RUN_CMD`, so a non-zero exit
# here means the server process is never reached.
#
# Output is ASCII only: `$var` followed by a multibyte character is read as
# part of the variable name by some shells, which silently blanks the value.

STAPEL_BOOT_STEPS=0
STAPEL_BOOT_DEGRADED=""

# Migrations run against the direct DB host: a pooler in transaction mode
# does not support DDL.
DB_HOST_DIRECT="${POSTGRES_HOST_DIRECT:-db}"
DB_PORT_DIRECT="${POSTGRES_PORT_DIRECT:-5432}"

db_direct() {
    POSTGRES_HOST="$DB_HOST_DIRECT" POSTGRES_PORT="$DB_PORT_DIRECT" "$@"
}

wait_for_database() {
    echo "[bootstrap] waiting for database ${DB_HOST_DIRECT}:${DB_PORT_DIRECT}"
    until pg_isready -h "$DB_HOST_DIRECT" -p "$DB_PORT_DIRECT" -U "$POSTGRES_USER"; do
        sleep 1
    done
    echo "[bootstrap] database is ready"
}

# Django's own env-driven flow (DJANGO_SUPERUSER_USERNAME/EMAIL/PASSWORD).
# Nothing here imports a model, so it cannot go stale against the schema.
ensure_superuser() {
    if [ -z "${DJANGO_SUPERUSER_USERNAME:-}" ] || [ -z "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
        echo "[bootstrap] no DJANGO_SUPERUSER_* in the env, skipping"
        return 0
    fi
    # Django exits non-zero when the username already exists - that is
    # "nothing to do", not a failure.
    python manage.py createsuperuser --noinput ||
        echo "[bootstrap] superuser '${DJANGO_SUPERUSER_USERNAME}' already exists"
    return 0
}

_stapel_boot_run() {
    _kind="$1"
    _label="$2"
    shift 2
    STAPEL_BOOT_STEPS=$((STAPEL_BOOT_STEPS + 1))
    echo "[bootstrap] step: ${_label}"
    # `&&` rather than `if/then/fi`: after an `if` whose condition failed and
    # which has no `else`, `$?` is the status of the `if` statement itself,
    # which is 0. That reports every failure as "exit 0".
    "$@" && return 0
    _status=$?
    if [ "$_kind" = "require" ]; then
        echo "[bootstrap] FATAL: ${_label} failed (exit ${_status})" >&2
        echo "[bootstrap] refusing to serve on a half-prepared deployment" >&2
        exit 1
    fi
    echo "[bootstrap] DEGRADED: ${_label} failed (exit ${_status}), continuing" >&2
    STAPEL_BOOT_DEGRADED="${STAPEL_BOOT_DEGRADED} ${_label}"
    return 0
}

require() { _stapel_boot_run require "$@"; }

optional() { _stapel_boot_run optional "$@"; }

bootstrap_done() {
    # End-state assertion, not a step status. Statuses get lost - to a pipe, a
    # subshell, a future edit - so the thing that actually matters is checked
    # directly against the database: a bootstrap that reports success while
    # the schema is behind the code is not expressible.
    #
    # `migrate --check` exits non-zero on an unapplied migration and runs the
    # Django system checks on the way (no --skip-checks anywhere: it silences
    # every check, not the one in the way).
    echo "[bootstrap] verifying the schema is at head"
    db_direct python manage.py migrate --check && _stapel_boot_head=0 || _stapel_boot_head=$?
    if [ "$_stapel_boot_head" != "0" ]; then
        echo "[bootstrap] FATAL: schema is behind the code, or a system check failed" >&2
        echo "[bootstrap] refusing to serve on a half-prepared deployment" >&2
        exit 1
    fi
    if [ -n "$STAPEL_BOOT_DEGRADED" ]; then
        echo "[bootstrap] done: ${STAPEL_BOOT_STEPS} steps, DEGRADED:${STAPEL_BOOT_DEGRADED}"
    else
        echo "[bootstrap] done: ${STAPEL_BOOT_STEPS} steps, all ok"
    fi
}
"""

# The service entrypoint. Every step is classified, with the reason stated on
# the line - a generated project must not need its author to rediscover the
# require/optional distinction.
BOOTSTRAP_SH = """\
#!/bin/sh
# Service entrypoint. Deliberately NOT `set -e`: see the step runner sourced
# below. Every step goes through `require` (failure makes this service wrong)
# or `optional` (failure makes it ugly), and `bootstrap_done` asserts the end
# state instead of trusting the step statuses.
#
# No project-specific Python here - this script only shells out to manage.py,
# so it can never go stale against the schema.
. "${STAPEL_BOOTSTRAP_LIB:-/usr/local/lib/stapel-bootstrap.sh}" || exit 1

# require: nothing below can run without the database.
require "database" wait_for_database

# require: serving on a schema the code does not match is the whole reason
# this contract exists.
require "migrations" db_direct python manage.py migrate --noinput

# optional: a missing admin account is an inconvenience, not a wrong service,
# and the step is skipped entirely when DJANGO_SUPERUSER_* is unset.
optional "superuser" ensure_superuser

# optional: a failed collectstatic serves unstyled admin pages. Making this
# fatal takes the whole API down over one asset.
optional "collectstatic" python manage.py collectstatic --noinput --clear --verbosity 0

bootstrap_done
"""

SCHEMA_HEALTH_PY = '''\
"""Schema-drift probe for /api/health/ and /api/metrics/.

Copied verbatim from ``scripts/service_schema_health.py``;
``scripts/verify_boot_contract.sh`` asserts every service's copy still matches
it byte for byte. The copy is the delivery: this module has to be importable
in the image, under a local mount, and in a native test run alike.

Three states, not two
---------------------
A bool has two values, so an inability to ask would become a negative verdict:
a database restart would make every service report drift and the alert would
fire saying something untrue. ``schema_state()`` returns AT_HEAD, BEHIND or
UNKNOWN, and each consumer is told which of the three it is looking at:

  * /api/metrics/ carries the full truth. ``<prefix>schema_at_head`` is
    emitted ONLY when the state was determined, so an unreachable database
    makes the series stop rather than drop to zero;
    ``<prefix>schema_probe_ok`` is emitted always, so "the probe cannot
    answer" is itself observable. The two alert rules read these, and only
    these.
  * /api/health/ can only say ok/error, so its bool is the conservative one:
    it reports a problem only for a positively determined BEHIND.
  * The deploy gate does not read this at all. ``manage.py migrate --check``
    inside the container is the single authority at deploy time, and two
    mechanisms answering the same question is how they come to disagree.

Registered as a NON-critical dependency check: /api/health/ keeps answering
200 while naming the state. A 503 on drift would pull every backend out of
rotation during a normal rolling migration, and with ``restart:
unless-stopped`` it would turn a failed migration into an unrecoverable loop.
The status code is a routing decision; the body and the metrics are the truth.
"""
import logging
import threading
import time

from django.conf import settings
from django.db import Error as DatabaseError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

logger = logging.getLogger(__name__)

AT_HEAD = "at_head"
BEHIND = "behind"
UNKNOWN = "unknown"

# A determined verdict is worth caching: migration files cannot change inside
# a running process. A non-answer is NOT cached - pinning "I could not tell"
# for thirty seconds makes a two-second blip outlive itself.
_TTL_SECONDS = 30

_lock = threading.Lock()
_checked_at = 0.0
_state = UNKNOWN
_registered = False


def unapplied_migrations():
    """Migrations on disk the database has not applied.

    Same definition as ``manage.py migrate --check``, deliberately: the boot
    gate, the deploy gate and this probe must not disagree about "behind".
    """
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    return [f"{m.app_label}.{m.name}" for m, _backwards in executor.migration_plan(targets)]


def schema_state():
    """AT_HEAD, BEHIND or UNKNOWN. Never raises."""
    global _checked_at, _state
    now = time.monotonic()
    with _lock:
        if _state is not UNKNOWN and _checked_at and now - _checked_at < _TTL_SECONDS:
            return _state
        try:
            pending = unapplied_migrations()
        except DatabaseError as exc:
            # Routine in operation - a database restart, a DNS blip. Not
            # silent: <prefix>schema_probe_ok goes to 0 and has its own rule.
            logger.warning("schema probe could not reach the database: %s", exc)
            _state = UNKNOWN
            return _state
        except Exception:
            # A broken migration graph, an inconsistent history. Still not a
            # verdict of "behind", but worth the trace.
            logger.exception("schema probe failed unexpectedly")
            _state = UNKNOWN
            return _state
        if pending:
            logger.error("schema is behind the code, unapplied migrations: %s", ", ".join(pending))
        _checked_at = now
        _state = BEHIND if pending else AT_HEAD
        return _state


def no_drift_detected():
    """Bool for /api/health/: False only when drift was positively determined.

    ``register_dependency_check`` coerces the probe with ``bool(probe())``, so
    a third value cannot be expressed there. Of the two available answers this
    is the one that is not a lie when the state is UNKNOWN: nothing has been
    detected. The distinction lives in the metrics.
    """
    return schema_state() is not BEHIND


def _metrics():
    """Prometheus fragment carrying all three states."""
    prefix = getattr(settings, "STAPEL_METRICS_PREFIX", "stapel_")
    service = getattr(settings, "SERVICE_NAME", "unknown").lower().replace(" ", "_")
    state = schema_state()
    lines = [
        f"# HELP {prefix}schema_probe_ok Whether the schema state could be determined",
        f"# TYPE {prefix}schema_probe_ok gauge",
        f'{prefix}schema_probe_ok{{service="{service}"}} {0 if state is UNKNOWN else 1}',
    ]
    if state is not UNKNOWN:
        # Deliberately absent when undetermined: a series that drops to 0
        # because the database was unreachable is the false verdict this
        # module exists to avoid.
        lines += [
            f"# HELP {prefix}schema_at_head Whether the schema is at the code's head",
            f"# TYPE {prefix}schema_at_head gauge",
            f'{prefix}schema_at_head{{service="{service}"}} {1 if state is AT_HEAD else 0}',
        ]
    return "\\n".join(lines)


def register_schema_check():
    """Register the probe on /api/health/ and /api/metrics/. Idempotent."""
    global _registered
    if _registered:
        return
    from stapel_core.django.monitoring.health import (
        register_dependency_check,
        register_metrics_exporter,
    )

    register_dependency_check("schema", no_drift_detected, critical=False)
    register_metrics_exporter(_metrics)
    _registered = True
'''

# ── the meta-gate ───────────────────────────────────────────────────────────
# Fixing one bootstrap fixes one bootstrap. This is what keeps the NEXT
# service from being written the old way.
VERIFY_BOOT_CONTRACT_SH = """\
#!/bin/sh
# scripts/verify_boot_contract.sh - the boot contract, checked across every
# service in services.conf.
#
# Checks, per service:
#   1. bootstrap.sh exists, sources the step runner, and ends with bootstrap_done
#   2. bootstrap.sh does NOT use a blanket `set -e` (it makes cosmetic steps fatal)
#   3. migrations are a `require` step
#   4. no manage.py step escapes the require/optional verbs
#   5. the Dockerfile bakes the step runner into the image
#   6. the compose fragment chains bootstrap.sh into the server with &&
#   7. config/schema_health.py matches scripts/service_schema_health.py
#   8. config/urls.py registers the schema probe
#
# Reports every violation, then exits non-zero. Not `set -e`: a linter that
# stops at the first finding makes you run it once per defect.
#
# Env: SERVICES_CONF   service list  (default: <root>/services.conf)
#      SERVICE_PREFIX  directory prefix per service (default: svc-)
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONF="${SERVICES_CONF:-$ROOT/services.conf}"
CANON="$ROOT/scripts/service_schema_health.py"
SERVICE_PREFIX="${SERVICE_PREFIX:-svc-}"
LIB_PATH="%(lib_path)s"

violations=0
bad() {
    violations=$((violations + 1))
    echo "  $*" >&2
}

if [ ! -f "$CONF" ]; then
    echo "ERROR: no service list at $CONF" >&2
    exit 1
fi
if [ ! -f "$CANON" ]; then
    echo "ERROR: no canonical schema probe at $CANON" >&2
    exit 1
fi

# Read the list, then assert the count: a loop that silently stops halfway is
# the genre of defect this gate exists to catch, so it must not have it.
services="$(sed 's/#.*//; s/[[:space:]]//g' "$CONF" | grep -v '^$')"
declared="$(printf '%%s\\n' "$services" | grep -c '^..*$')"
count=0

for s in $services; do
    count=$((count + 1))
    dir="$ROOT/${SERVICE_PREFIX}${s}"
    boot="$dir/bootstrap.sh"
    dockerfile="$dir/Dockerfile"
    compose="$ROOT/${SERVICE_PREFIX}${s}.yml"
    probe="$dir/config/schema_health.py"
    urls="$dir/config/urls.py"

    if [ ! -f "$boot" ]; then
        bad "${SERVICE_PREFIX}${s}: no bootstrap.sh"
        continue
    fi

    # 1/2/3. the step runner is in use, and nothing overrides it wholesale
    grep -q "$LIB_PATH" "$boot" ||
        bad "${SERVICE_PREFIX}${s}/bootstrap.sh: does not source $LIB_PATH"
    grep -qE '^[[:space:]]*bootstrap_done[[:space:]]*$' "$boot" ||
        bad "${SERVICE_PREFIX}${s}/bootstrap.sh: no bootstrap_done (the schema-at-head assertion)"
    grep -qE '^[[:space:]]*set[[:space:]]+-[a-z]*e' "$boot" &&
        bad "${SERVICE_PREFIX}${s}/bootstrap.sh: blanket 'set -e' - it makes cosmetic steps fatal; classify each step with require/optional instead"
    grep -qE '^[[:space:]]*require[[:space:]]+"migrations"' "$boot" ||
        bad "${SERVICE_PREFIX}${s}/bootstrap.sh: migrations are not a require step"

    # 4. no step escapes the verbs. Every manage.py invocation must be an
    #    argument to require/optional, or inside a function that is (a
    #    function body is indented; a bare step at column 0 is not).
    hits="${TMPDIR:-/tmp}/stapel-boot-contract.$$"
    grep -n "manage.py" "$boot" > "$hits" 2>/dev/null || true
    while IFS= read -r hit; do
        [ -n "$hit" ] || continue
        lineno="${hit%%%%:*}"
        text="${hit#*:}"
        case "$text" in
            *require\\ *|*optional\\ *|*'#'*) continue ;;
        esac
        printf '%%s' "$text" | grep -qE '^[[:space:]]+' && continue
        bad "${SERVICE_PREFIX}${s}/bootstrap.sh:$lineno: manage.py step outside require/optional"
    done < "$hits"
    rm -f "$hits"

    # 5. the image carries the step runner
    if [ -f "$dockerfile" ]; then
        grep -q "COPY scripts/bootstrap_lib.sh $LIB_PATH" "$dockerfile" ||
            bad "${SERVICE_PREFIX}${s}/Dockerfile: does not COPY scripts/bootstrap_lib.sh to $LIB_PATH"
    else
        bad "${SERVICE_PREFIX}${s}: no Dockerfile"
    fi

    # 6. a failed bootstrap must not be stepped over by the compose command.
    #    `sh bootstrap.sh; $RUN_CMD` would start the server regardless.
    if [ -f "$compose" ]; then
        grep -qE 'sh bootstrap\\.sh[[:space:]]*&&' "$compose" ||
            bad "${SERVICE_PREFIX}${s}.yml: bootstrap.sh is not chained into the server command with &&"
    else
        bad "${SERVICE_PREFIX}${s}: no ${SERVICE_PREFIX}${s}.yml"
    fi

    # 7/8. the schema probe is present, unmodified, and registered
    if [ -f "$probe" ]; then
        cmp -s "$CANON" "$probe" ||
            bad "${SERVICE_PREFIX}${s}/config/schema_health.py: diverged from scripts/service_schema_health.py"
    else
        bad "${SERVICE_PREFIX}${s}: no config/schema_health.py (schema drift would be invisible from outside)"
    fi
    if [ -f "$urls" ]; then
        grep -q "register_schema_check()" "$urls" ||
            bad "${SERVICE_PREFIX}${s}/config/urls.py: does not call register_schema_check()"
    else
        bad "${SERVICE_PREFIX}${s}: no config/urls.py"
    fi
done

if [ "$count" -ne "$declared" ]; then
    echo "ERROR: checked $count services, $CONF declares $declared" >&2
    exit 1
fi
if [ "$violations" -ne 0 ]; then
    echo "ERROR: $violations boot-contract violation(s)" >&2
    exit 1
fi
echo "OK: $count service(s) satisfy the boot contract"
""" % {"lib_path": BOOTSTRAP_LIB_PATH}
