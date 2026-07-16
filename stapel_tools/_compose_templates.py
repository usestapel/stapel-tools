"""Docker Compose templates for project scaffolding."""

# Runs on EVERY postgres startup (via the db service's command wrapper),
# creating any database listed in POSTGRES_MULTIPLE_DATABASES that does not
# exist yet. Running at every startup — not just first initdb — means adding
# a service later creates its database without wiping the data volume.
POSTGRES_ENSURE_DATABASES = """\
#!/bin/sh
set -eu

ensure_database_exists() {
    database=$1
    if psql -v ON_ERROR_STOP=1 -d postgres --username "$POSTGRES_USER" -lqt \\
        | cut -d '|' -f 1 | grep -qw "$database"; then
        echo "  database '$database' exists"
    else
        echo "  creating database '$database'"
        psql -v ON_ERROR_STOP=1 -d postgres --username "$POSTGRES_USER" <<-EOSQL
            CREATE DATABASE $database;
            GRANT ALL PRIVILEGES ON DATABASE $database TO $POSTGRES_USER;
EOSQL
    fi
}

if [ -n "${POSTGRES_MULTIPLE_DATABASES:-}" ]; then
    echo "Ensuring databases exist: $POSTGRES_MULTIPLE_DATABASES"
    for db in $(echo "$POSTGRES_MULTIPLE_DATABASES" | tr ',' ' '); do
        db=$(echo "$db" | xargs)
        [ -n "$db" ] && ensure_database_exists "$db"
    done
fi
"""

# Mounted at /etc/nginx/conf.d/nginx.conf. stapel-new-service appends a
# location block per service before the closing brace.
#
# Reserved namespace (§57 owner directive — monolith static/media collision
# check): /staticfiles/ and /media/ are BARE, PROJECT-WIDE prefixes, but each
# service's own STATIC_URL/MEDIA_URL is namespaced under them per slug
# (/staticfiles/<slug>/, /media/<slug>/ — see _templates.BASE_SETTINGS), and
# every backend service's API/admin/health routes live under their own
# /<slug>/ prefix (see stapel-new-service's per-service location block,
# _update_nginx). A frontend app must never define a client route starting
# with /staticfiles/, /media/ or /<any-backend-slug>/ — nginx enforces the
# split by prefix-match SPECIFICITY (longest prefix wins), independent of the
# order location blocks appear in this file. Documented again in the
# project's AGENTS.md §3.
NGINX_CONF = """\
server {
    listen 80;
    server_name _;
    client_max_body_size 50m;
    resolver 127.0.0.11 valid=10s;

    location /staticfiles/ {
        alias /staticfiles/;
    }

    location /media/ {
        alias /media/;
    }

    # stapel-new-service appends one location block per backend service
    # above this line (each under its own reserved /<slug>/ prefix).

    # Prod canon (§57): the built frontend, populated into this volume by
    # the one-shot frontend-build service (see docker-compose.yml). SPA
    # fallback so client-side routes resolve on a hard refresh. Kept last
    # for readability only — prefix-match specificity (not file order) is
    # what actually keeps this from shadowing the reserved blocks above.
    location / {
        root /usr/share/nginx/html;
        try_files $uri $uri/ /index.html;
    }
}
"""

# Dev-canon (§57): mounted via the nginx image's OWN envsubst-on-templates
# entrypoint (https://hub.docker.com/_/nginx — "Using environment variables
# in nginx configuration"; any *.template under /etc/nginx/templates/ is
# rendered to /etc/nginx/conf.d/ at container start). Proxy targets are ENV
# VARS with compose-network defaults (never hardcoded) — see
# docker-compose.dev.yml's `nginx` service `environment:` block; override
# BACKEND_UPSTREAM / FRONTEND_DEV_UPSTREAM in .env for a native run (backend
# and/or frontend on the host, e.g. localhost:8000 / localhost:5173).
#
# Safe alongside nginx's OWN lowercase config variables ($host, $scheme, ...)
# — envsubst only replaces names that exist as environment variables, and
# none of those lowercase nginx variable names collide with real env vars
# (the same convention the official nginx image's own docs example uses).
NGINX_DEV_CONF_TEMPLATE = """\
server {
    listen 80;
    server_name _;
    client_max_body_size 50m;
    resolver 127.0.0.11 valid=10s;

    # Reserved namespace, checked BEFORE the Vite catch-all below (see
    # NGINX_CONF's prod twin + the project's AGENTS.md §3). Static/media
    # aren't collected in dev — Django's runserver serves them itself — so
    # they proxy to the backend like the API does, instead of the
    # alias-to-volume prod uses.
    location /staticfiles/ {
        proxy_pass http://${BACKEND_UPSTREAM};
        proxy_set_header Host $host;
    }

    location /media/ {
        proxy_pass http://${BACKEND_UPSTREAM};
        proxy_set_header Host $host;
    }

    location /{{SLUG}}/ {
        proxy_pass http://${BACKEND_UPSTREAM};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
    }

    # Everything else — the Vite dev server (HMR websocket included).
    location / {
        proxy_pass http://${FRONTEND_DEV_UPSTREAM};
        proxy_set_header Host $host;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
"""


# ─── Broker building blocks ─────────────────────────────────────────────────
# Compose bases carry {{BROKER_SERVICES}} / {{BROKER_VOLUMES}} markers; the
# generator splices the chosen broker(s) in. Env templates carry
# {{BROKER_ENV}}. A dedicated Task broker (--task-broker) adds its blocks
# next to the event broker's.

NATS_SERVICE_BLOCK = """\
  # Events (JetStream) + RPC (request-reply) for stapel_core.comm
  nats:
    image: nats:2.10-alpine
    restart: unless-stopped
    command: ["--jetstream", "--store_dir", "/data"]
    volumes:
      - nats-data:/data

"""

KAFKA_SERVICE_BLOCK = """\
  kafka:
    image: apache/kafka:3.9.0
    restart: unless-stopped
    environment:
      KAFKA_NODE_ID: 1
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_LISTENERS: PLAINTEXT://:9092,CONTROLLER://:9093
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka:9093
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_AUTO_CREATE_TOPICS_ENABLE: "true"
    volumes:
      - kafka-data:/var/lib/kafka/data

"""

NATS_ENV_BLOCK = """\
# ─── NATS: events (JetStream) + RPC ─────────────────────────────────────────
STAPEL_BUS_BACKEND=nats
NATS_URL=nats://nats:4222
"""

KAFKA_ENV_BLOCK = """\
# ─── Kafka: events ──────────────────────────────────────────────────────────
STAPEL_BUS_BACKEND=kafka
KAFKA_BOOTSTRAP_SERVERS=kafka:9092
"""

# Task broker only (monolith --task-broker nats): Actions stay in-process;
# STAPEL_TASK_DISPATCH=bus makes stapel_core publish task.* events through
# the broker to a dedicated worker (STAPEL_COMM["TASK_DISPATCH"]).
TASK_ONLY_NATS_ENV_BLOCK = """\
# ─── NATS: broker for long-running Tasks only (Actions stay in-process) ────
STAPEL_BUS_BACKEND=nats
NATS_URL=nats://nats:4222
STAPEL_TASK_DISPATCH=bus
"""

_BROKER_SERVICES = {"nats": NATS_SERVICE_BLOCK, "kafka": KAFKA_SERVICE_BLOCK, "none": ""}
_BROKER_VOLUMES = {"nats": "  nats-data:\n", "kafka": "  kafka-data:\n", "none": ""}
_BROKER_ENV = {"nats": NATS_ENV_BLOCK, "kafka": KAFKA_ENV_BLOCK, "none": ""}
_BROKER_URL_LINES = {
    "nats": "NATS_URL=nats://nats:4222\n",
    "kafka": "KAFKA_BOOTSTRAP_SERVERS=kafka:9092\n",
}


def render_tokens(template: str, ctx: dict) -> str:
    """Replace ``{{KEY}}`` tokens by simple string substitution — NOT
    ``str.format()``, which would choke on the literal ``${VAR:-default}``
    compose/nginx-envsubst syntax these templates are full of (single braces,
    used pervasively — see e.g. ``${POSTGRES_USER:-stapel}`` above)."""
    result = template
    for key, value in ctx.items():
        result = result.replace(f"{{{{{key}}}}}", str(value))
    return result


def render_compose_base(template: str, broker: str, task_broker: str = "none") -> str:
    """Splice the chosen broker(s) (nats | kafka | none) into a compose base.

    *task_broker* adds a second broker dedicated to Tasks when it differs
    from the event broker.
    """
    brokers = [b for b in ("nats", "kafka") if b in (broker, task_broker)]
    services = "".join(_BROKER_SERVICES[b] for b in brokers)
    volumes = "".join(_BROKER_VOLUMES[b] for b in brokers)
    return template.replace("{{BROKER_SERVICES}}", services).replace(
        "{{BROKER_VOLUMES}}", volumes
    )


def _broker_env(broker: str, task_broker: str) -> str:
    if task_broker in ("none", broker):
        return _BROKER_ENV[broker]
    if broker == "none":
        # Broker exists for Tasks only — Actions stay in-process.
        return TASK_ONLY_NATS_ENV_BLOCK
    # Two brokers: RoutingBus splits by topic prefix — task.* to the task
    # broker, everything else to the event broker.
    urls = "".join(
        _BROKER_URL_LINES[b] for b in ("nats", "kafka") if b in (broker, task_broker)
    )
    return (
        f"# ─── Brokers: {broker} for events/RPC, {task_broker} for Tasks ─────────────────\n"
        "STAPEL_BUS_BACKEND=routing\n"
        f'STAPEL_BUS_ROUTES={{"task.": "{task_broker}", "": "{broker}"}}\n'
        f"{urls}"
    )


def render_env(template: str, broker: str, ctx: dict, task_broker: str = "none") -> str:
    # Format first ({{BROKER_ENV}} collapses to {BROKER_ENV}), then splice the
    # broker block in — it may contain literal braces (STAPEL_BUS_ROUTES JSON)
    # that str.format must never see.
    rendered = template.format(**ctx)
    return rendered.replace("{BROKER_ENV}", _broker_env(broker, task_broker))


MONOLITH_COMPOSE_BASE = """\
# Shared infrastructure — included by all environments.
# Do not put service-specific overrides here.
services:
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: "${POSTGRES_USER:-stapel}"
      POSTGRES_PASSWORD: "${POSTGRES_PASSWORD}"
      POSTGRES_MULTIPLE_DATABASES: "stapel_main"
    volumes:
      - db-data:/var/lib/postgresql/data
      - ./service-configs/postgres/ensure-databases.sh:/usr/local/bin/ensure-databases.sh:ro
    command: >
      bash -c "
        docker-entrypoint.sh postgres &
        until pg_isready -U $${POSTGRES_USER:-stapel}; do sleep 1; done
        /usr/local/bin/ensure-databases.sh
        wait
      "
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER:-stapel}"]
      interval: 5s
      timeout: 5s
      retries: 10

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    volumes:
      - redis-data:/data

{{BROKER_SERVICES}}  nginx:
    image: nginx:alpine
    restart: unless-stopped
    ports:
      - "${HTTP_PORT:-80}:80"
    volumes:
      - ./service-configs/nginx:/etc/nginx/conf.d:ro
      - static-content:/staticfiles:ro
      - media-content:/media:ro
      - frontend-dist:/usr/share/nginx/html:ro
    depends_on: []

volumes:
  db-data:
  redis-data:
{{BROKER_VOLUMES}}  static-content:
  media-content:
  frontend-dist:
"""

MONOLITH_COMPOSE_LOCAL = """\
include:
  - docker-compose.base.yml

services:
  # Prod canon (§57): one-shot build — populates the frontend-dist volume
  # nginx serves from (see docker-compose.base.yml's nginx `frontend-dist`
  # mount + service-configs/nginx/nginx.conf's `location /`). Not a
  # long-lived service: `restart: "no"`, runs once per `docker compose up`
  # and again on demand via `docker compose run --rm frontend-build`.
  frontend-build:
    build:
      context: ./frontend
    restart: "no"
    volumes:
      - frontend-dist:/output

  # Add backend services from their individual .yml files:
  # svc-app:
  #   extends:
  #     file: svc-app.yml
  #     service: svc-app
  #   volumes:
  #     - ./svc-app:/app
  #     - ./stapel_core:/app/stapel_core:ro
"""

MONOLITH_COMPOSE_DEV = """\
include:
  - docker-compose.base.yml

# NOTE: `volumes:` is declared BEFORE `services:` on purpose — `services:`
# must stay the LAST top-level section in this file: stapel-new-service's
# _update_compose_file() appends each backend service by raw text at EOF,
# assuming whatever comes last is the services mapping. A top-level key
# after `services:` would make an appended service block parse as a nested
# key under THAT section instead (a real compose-breaking class of bug).
volumes:
  frontend-node-modules:

services:
  # Dev canon (§57): plain node image — no bespoke Dockerfile to go stale —
  # runs the Vite dev server with hot reload; logs via
  # `docker compose -f docker-compose.dev.yml logs -f frontend`.
  frontend:
    image: node:22-alpine
    working_dir: /app
    command: sh -c "npm install && npm run dev -- --host 0.0.0.0 --port 5173"
    environment:
      VITE_BACKEND_TARGET: "http://${BACKEND_UPSTREAM:-{{BACKEND_UPSTREAM_DEFAULT}}}"
    volumes:
      - ./frontend:/app
      - frontend-node-modules:/app/node_modules
    restart: unless-stopped

  # dev-nginx canon: same `nginx` service as prod, but its conf.d/templates
  # source is swapped to service-configs/nginx-dev/ (envsubst template that
  # proxies the reserved backend namespace + everything else to Vite,
  # instead of serving the (unbuilt, in dev) frontend-dist volume) —
  # compose merges service overrides by mount TARGET, so only the changed
  # targets need repeating here (static-content/media-content stay from the
  # base's nginx service). Proxy targets are env vars with compose-network
  # defaults — override in .env for a native run.
  nginx:
    volumes:
      - ./service-configs/nginx-dev:/etc/nginx/conf.d:ro
      - ./service-configs/nginx-dev:/etc/nginx/templates:ro
    environment:
      BACKEND_UPSTREAM: "${BACKEND_UPSTREAM:-{{BACKEND_UPSTREAM_DEFAULT}}}"
      FRONTEND_DEV_UPSTREAM: "${FRONTEND_DEV_UPSTREAM:-frontend:5173}"
    depends_on:
      - frontend

  # Add backend services from their individual .yml files:
  # svc-app:
  #   extends:
  #     file: svc-app.yml
  #     service: svc-app
"""

MONOLITH_ENV_TEMPLATE = """\
# ─── Database ──────────────────────────────────────────────────────────────
POSTGRES_USER=stapel
POSTGRES_PASSWORD=change_me
POSTGRES_HOST=db
POSTGRES_PORT=5432

# ─── Redis ─────────────────────────────────────────────────────────────────
REDIS_URL=redis://redis:6379/0

{{BROKER_ENV}}
# ─── App ───────────────────────────────────────────────────────────────────
SECRET_KEY=change_me_to_a_long_random_string
JWT_SECRET_KEY=change_me_to_another_long_random_string
ALLOWED_HOSTS={domain}
SITE_URL={url}

# ─── Service navigation (admin-suite AS-4) ──────────────────────────────────
# The admin/Swagger "Services" menu is driven by this deploy-config env-JSON.
# A monolith is a single service (one admin); the menu's "All Services"
# section collapses. Add tool/monitoring links via STAPEL_ADMIN["NAV_LINKS"]
# in settings.
STAPEL_SERVICES=[{{"name": "{title}", "prefix": "{slug}"}}]

# ─── Email ─────────────────────────────────────────────────────────────────
DEFAULT_FROM_EMAIL={company_name} <{company_email}>
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=

# ─── OAuth (optional) ───────────────────────────────────────────────────────
GOOGLE_OAUTH2_KEY=
GOOGLE_OAUTH2_SECRET=

# ─── Run command ────────────────────────────────────────────────────────────
RUN_CMD=gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2
"""

MONOLITH_GITIGNORE = """\
.env
.env.dev
*.pyc
__pycache__/
.venv/
venv/
*.egg-info/
dist/
build/
htmlcov/
.coverage
*.sqlite3
media/
staticfiles/
.DS_Store
"""

MICRO_COMPOSE_BASE = """\
# Shared infrastructure for microservices stack.
services:
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: "${POSTGRES_USER:-stapel}"
      POSTGRES_PASSWORD: "${POSTGRES_PASSWORD}"
      POSTGRES_MULTIPLE_DATABASES: ""
    volumes:
      - db-data:/var/lib/postgresql/data
      - ./service-configs/postgres/ensure-databases.sh:/usr/local/bin/ensure-databases.sh:ro
    command: >
      bash -c "
        docker-entrypoint.sh postgres &
        until pg_isready -U $${POSTGRES_USER:-stapel}; do sleep 1; done
        /usr/local/bin/ensure-databases.sh
        wait
      "
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER:-stapel}"]
      interval: 5s
      timeout: 5s
      retries: 10

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    volumes:
      - redis-data:/data

{{BROKER_SERVICES}}  nginx:
    image: nginx:alpine
    restart: unless-stopped
    ports:
      - "${HTTP_PORT:-80}:80"
    volumes:
      - ./service-configs/nginx:/etc/nginx/conf.d:ro
      - static-content:/staticfiles:ro
      - media-content:/media:ro
    depends_on: []

volumes:
  db-data:
  redis-data:
{{BROKER_VOLUMES}}  static-content:
  media-content:
"""

MICRO_COMPOSE_LOCAL = """\
include:
  - docker-compose.base.yml

services:
  # Add your services here.
  # Run: stapel-new-service <name> --prefix svc-
"""

MICRO_ENV_TEMPLATE = """\
# ─── Database ──────────────────────────────────────────────────────────────
POSTGRES_USER=stapel
POSTGRES_PASSWORD=change_me
POSTGRES_HOST=db
POSTGRES_PORT=5432

# ─── Redis ─────────────────────────────────────────────────────────────────
REDIS_URL=redis://redis:6379/0

{{BROKER_ENV}}
# ─── App ───────────────────────────────────────────────────────────────────
SECRET_KEY=change_me_to_a_long_random_string
JWT_SECRET_KEY=change_me_to_another_long_random_string
ALLOWED_HOSTS={domain}
SITE_URL={url}

# ─── Service navigation (admin-suite AS-4) ──────────────────────────────────
# The admin/Swagger "Services" menu is driven by this deploy-config env-JSON,
# shared verbatim across every service (compose passes the same .env to all).
# stapel-new-service appends a row here; add tool/monitoring links via
# STAPEL_ADMIN["NAV_LINKS"] in settings.
STAPEL_SERVICES=[]

# ─── Email ─────────────────────────────────────────────────────────────────
DEFAULT_FROM_EMAIL={company_name} <{company_email}>
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=

# ─── Run command ────────────────────────────────────────────────────────────
RUN_CMD=gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2
"""
