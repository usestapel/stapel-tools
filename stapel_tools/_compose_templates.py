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
}
"""

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

  nginx:
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
  static-content:
  media-content:
"""

MONOLITH_COMPOSE_LOCAL = """\
include:
  - docker-compose.base.yml

services:
  # Add services from their individual .yml files:
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

services:
  # Add services from their individual .yml files:
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

# ─── App ───────────────────────────────────────────────────────────────────
SECRET_KEY=change_me_to_a_long_random_string
JWT_SECRET_KEY=change_me_to_another_long_random_string
ALLOWED_HOSTS={domain}
SITE_URL={url}

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
RUN_CMD=gunicorn core.wsgi:application --bind 0.0.0.0:8000 --workers 2
"""

MONOLITH_GITIGNORE = """\
.env
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

  # RPC between services (comm Functions, STAPEL_COMM FUNCTION_TRANSPORT=nats)
  nats:
    image: nats:2.10-alpine
    restart: unless-stopped
    command: ["--jetstream", "--store_dir", "/data"]
    volumes:
      - nats-data:/data

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

  nginx:
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
  nats-data:
  kafka-data:
  static-content:
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

# ─── Kafka ─────────────────────────────────────────────────────────────────
KAFKA_BOOTSTRAP_SERVERS=kafka:9092

# ─── NATS (comm Function RPC) ──────────────────────────────────────────────
NATS_URL=nats://nats:4222

# ─── App ───────────────────────────────────────────────────────────────────
SECRET_KEY=change_me_to_a_long_random_string
JWT_SECRET_KEY=change_me_to_another_long_random_string
ALLOWED_HOSTS={domain}
SITE_URL={url}

# ─── Email ─────────────────────────────────────────────────────────────────
DEFAULT_FROM_EMAIL={company_name} <{company_email}>
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=

# ─── Run command ────────────────────────────────────────────────────────────
RUN_CMD=gunicorn core.wsgi:application --bind 0.0.0.0:8000 --workers 2
"""
