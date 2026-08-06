"""Docker Compose templates for project scaffolding."""
import dataclasses
import re

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
# with /staticfiles/, /media/, /<any-backend-slug>/, or one of a feature
# lib's own reserved sub-surfaces (/<mod>/api|swagger|schema.json|admin —
# never a lib's bare root, which the frontend router owns; see
# create_project._reserved_paths_manifest / the project's own
# reserved-paths.json) — nginx enforces the split by prefix-match
# SPECIFICITY (longest prefix wins), independent of the order location
# blocks appear in this file. Documented again in the project's AGENTS.md §5.
NGINX_CONF = """\
server {
    listen 80;
    server_name _;
    client_max_body_size 50m;
    resolver 127.0.0.11 valid=10s;

    # Non-obvious trap (owner postmortem: "/admin loses the port"): nginx
    # ITSELF redirects /admin -> /admin/ BEFORE proxy_pass, and builds an
    # ABSOLUTE Location URL from the internal $server_port (80 inside the
    # container). Port 80 is the http default so it gets omitted — the
    # browser lands on the host WITHOUT the externally mapped port (:8080
    # etc). port_in_redirect / server_name_in_redirect do NOT fix this
    # (both still use the internal port). absolute_redirect off makes
    # nginx emit a RELATIVE Location, which the browser resolves against
    # the address bar — correct for any external port mapping. Pairs with
    # proxy_set_header Host $http_host below (keeps the port for redirects
    # the BACKEND builds).
    absolute_redirect off;

    location /staticfiles/ {
        alias /staticfiles/;
    }

    location /media/ {
        alias /media/;
    }

    # stapel-new-service appends one location block per backend service
    # above this line (each under its own reserved /<slug>/ prefix).

    # ─── SPA cache canon (owner directive, 2026-07-26) ──────────────────────
    # Live incident that made this a canon (app.ironmemo.com stand): the entry
    # document was served with BOTH `expires 1d` AND
    # `add_header Cache-Control "public, must-revalidate"`. nginx emits BOTH
    # headers and the browser takes max-age=86400 — a freshly deployed
    # frontend fix stayed invisible for up to 24h, and the verification of
    # that fix read the STALE bundle and wrongly concluded the fix had failed.
    #
    # The two halves are opposites, on purpose:
    #   * hashed build artifacts (vite writes dist/assets/<name>-<hash>.js|css)
    #     are CONTENT-ADDRESSED — a new deploy is a new URL — so they are
    #     cached for a year, immutably.
    #   * index.html is the ONLY unhashed file; it is what points at the
    #     current hashes. Caching it means a deploy does not reach users until
    #     the cache expires, so it must revalidate on EVERY load.
    #
    # nginx details this depends on:
    #   * NEVER combine an `expires` directive with an explicit
    #     `add_header Cache-Control` — nginx adds its own Cache-Control on top
    #     of yours and the response carries two conflicting ones (the incident
    #     above). `expires off;` makes nginx add nothing, leaving exactly one
    #     explicit header.
    #   * `add_header` does NOT merge with a parent block's add_header: a
    #     location that declares ANY add_header REPLACES every header
    #     inherited from server/http. Both locations below are therefore
    #     self-contained. This server block declares no add_header of its own
    #     today — if one is ever added there, it must be repeated inside each
    #     location here.
    #   * a regex location always wins over the `/` prefix location no matter
    #     where either appears in this file — the order below is readability
    #     only, exactly like the reserved prefixes above.
    location ~* ^/assets/.*\\.(?:js|mjs|css|woff2?|ttf|otf|eot|svg|png|jpe?g|gif|ico|webp|avif|map)$ {
        root {{FRONTEND_ROOT}};
        expires off;
        # NOT `always` — deliberately, unlike the entry document below.
        # `always` also stamps this header onto ERROR responses, so a 404 for a
        # chunk that is not on disk yet comes back "immutable, max-age=1 year":
        # the browser caches the chunk's ABSENCE for a year and will not
        # recheck even on reload, leaving that user with a permanently broken
        # app. Measured live on both stands 2026-08-05; guarded by NGX005.
        # Without `always` nginx still adds the header to 2xx/3xx — 304
        # included, so revalidation is unaffected.
        add_header Cache-Control "public, max-age=31536000, immutable";
    }
{{FRONTEND_EXTRA_LOCATIONS}}

    # Prod canon (§57): the built frontend, populated into this volume by
    # the one-shot frontend-build service (see docker-compose.yml). SPA
    # fallback so client-side routes resolve on a hard refresh — the fallback
    # is served FROM THIS location, so the no-cache header below is the one
    # the entry document gets. Kept last for readability only — prefix-match
    # specificity (not file order) is what actually keeps this from shadowing
    # the reserved blocks above.
    #
    # `root` points at the `current` SYMLINK inside the frontend volume, not at
    # the volume itself. Each build lands in its own `<build-id>/` directory and
    # the one-shot writer repoints `current` at it. What that replaces was
    # `rm -rf /output/* && cp -r dist/. /output/`: a multi-second window where
    # the site 404s, and — worse — the previous build's HASHED assets deleted
    # out from under every browser tab that was open across the deploy, which
    # then breaks on its next chunk fetch. nginx follows the symlink per
    # request, so a repoint takes effect immediately with no reload.
    location / {
        root {{FRONTEND_ROOT}};
        try_files $uri $uri/ /index.html;
        expires off;
        add_header Cache-Control "no-cache, must-revalidate" always;
    }
}
"""

# One extra frontend mounted under its own URL prefix (e.g. /kmp). Same cache
# canon as the root app, per-prefix: hashed assets immutable, entry document
# never cached. `alias` (not `root`) because the URL prefix is NOT part of the
# on-disk path.
NGINX_EXTRA_FRONTEND_BLOCK = """\

    # ─── frontend: {name} at {mount} ────────────────────────────────────────
    # The capture group is what `alias` substitutes: the URL prefix is not part
    # of the on-disk path, so /kmp/assets/x-<hash>.js must resolve to
    # <root>/assets/x-<hash>.js.
    location ~* ^{mount_re}(/assets/.*\\.(?:js|mjs|css|woff2?|ttf|otf|eot|svg|png|jpe?g|gif|ico|webp|avif|map))$ {{
        alias {root}$1;
        expires off;
        # No `always` — see the root app's asset location (NGX005).
        add_header Cache-Control "public, max-age=31536000, immutable";
    }}

    location {mount}/ {{
        alias {root}/;
        try_files $uri $uri/ {mount}/index.html;
        expires off;
        add_header Cache-Control "no-cache, must-revalidate" always;
    }}
"""


def render_nginx_conf(template: str, frontends: "list[Frontend] | None" = None) -> str:
    """Point the SPA locations at the delivered frontends.

    ``None`` keeps the historical single-frontend default (one app at ``/``)
    so callers that predate the delivery axis render exactly what they did
    before, apart from the ``/current`` swap directory.
    """
    fronts = DEFAULT_FRONTENDS if frontends is None else frontends
    root = next((f for f in fronts if f.is_root), None)
    if root is None:
        raise ValueError(
            "nginx conf: no frontend mounted at '/' — the SPA fallback has "
            "nothing to serve. Declare one, or generate a project without the "
            "frontend locations."
        )
    extra = "".join(
        NGINX_EXTRA_FRONTEND_BLOCK.format(
            name=f.name,
            mount=f.mount.rstrip("/"),
            mount_re=re.escape(f.mount.rstrip("/")),
            root=f.nginx_root,
        )
        for f in fronts
        if not f.is_root
    )
    return render_tokens(
        template,
        {
            "FRONTEND_ROOT": root.nginx_root,
            "FRONTEND_EXTRA_LOCATIONS": extra.rstrip("\n"),
        },
    )

# Dev-canon (§57): mounted via the nginx image's OWN envsubst-on-templates
# entrypoint (https://hub.docker.com/_/nginx — "Using environment variables
# in nginx configuration"; any *.template under /etc/nginx/templates/ is
# rendered to /etc/nginx/conf.d/ at container start). Proxy targets are ENV
# VARS with compose-network defaults (never hardcoded) — see
# docker-compose.local.yml's `nginx` service `environment:` block; override
# BACKEND_UPSTREAM / FRONTEND_LOCAL_UPSTREAM in .env for a native run (backend
# and/or frontend on the host, e.g. localhost:8000 / localhost:5173).
#
# Safe alongside nginx's OWN lowercase config variables ($host, $scheme, ...)
# — envsubst only replaces names that exist as environment variables, and
# none of those lowercase nginx variable names collide with real env vars
# (the same convention the official nginx image's own docs example uses).
NGINX_LOCAL_CONF_TEMPLATE = """\
server {
    listen 80;
    server_name _;
    client_max_body_size 50m;
    resolver 127.0.0.11 valid=10s;

    # Non-obvious trap (owner postmortem: "/admin loses the port"): nginx
    # ITSELF redirects /admin -> /admin/ BEFORE proxy_pass, and builds an
    # ABSOLUTE Location URL from the internal $server_port (80 inside the
    # container). Port 80 is the http default so it gets omitted — the
    # browser lands on the host WITHOUT the externally mapped port (:8080
    # etc). port_in_redirect / server_name_in_redirect do NOT fix this
    # (both still use the internal port). absolute_redirect off makes
    # nginx emit a RELATIVE Location, which the browser resolves against
    # the address bar — correct for any external port mapping. Pairs with
    # proxy_set_header Host $http_host below (keeps the port for redirects
    # the BACKEND builds).
    absolute_redirect off;

    # Deferred upstream resolution: proxy_pass with a VARIABLE makes nginx
    # resolve the upstream per request (via the resolver above) instead of
    # at config load — a literal `proxy_pass http://svc:8000` makes nginx
    # refuse to START at all while that container is down ("host not found
    # in upstream"), which would deadlock `compose up`'s ordering.
    set $stapel_backend http://${BACKEND_UPSTREAM};
    set $stapel_frontend http://${FRONTEND_LOCAL_UPSTREAM};

    # Host is forwarded as $http_host (NOT $host: that strips the port —
    # on a local stack at :8080, or any non-443 stand, absolute links and
    # redirects the backend builds would silently lose the port).
    #
    # Reserved namespace, checked BEFORE the Vite catch-all below (see
    # NGINX_CONF's prod twin + the project's AGENTS.md §5). Static/media
    # aren't collected in dev — Django's runserver serves them itself — so
    # they proxy to the backend like the API does, instead of the
    # alias-to-volume prod uses.
    location /staticfiles/ {
        proxy_pass $stapel_backend;
        proxy_set_header Host $http_host;
    }

    location /media/ {
        proxy_pass $stapel_backend;
        proxy_set_header Host $http_host;
    }

    # Backend locations below are GENERATED from the project's actual lib
    # selection (STAPEL_LIBS url_prefixes + the service slug + admin) — never
    # a hand-maintained list: adding a lib to the project regenerates its
    # rule by construction (owner directive: the "forgot /calendar in the
    # proxy" class of bug must be impossible).
{{BACKEND_LOCATIONS}}
    # Everything else — the Vite dev server (HMR websocket included).
    location / {
        proxy_pass $stapel_frontend;
        proxy_set_header Host $http_host;
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

#: Конфиг nats-server. Отдельным файлом, а не флагами, потому что
#: `max_payload` флагом задать НЕЛЬЗЯ — см. комментарий в NATS_SERVICE_BLOCK.
NATS_CONF = """\
port: 4222
http_port: 8222
jetstream {
  store_dir: /data
}

# Функция comm — это request-reply, и ответ целиком едет одним сообщением.
# Дефолтный потолок 1 МиБ структурированный ответ по реальному тексту
# перешагивает раньше, чем кажется (замер на ironmemo 06.08.2026: ответ
# отвергнут, вызывающий не услышал ничего и вышел по таймауту, хотя работа
# была сделана). 8 МиБ — запас, который сам NATS считает разумным; дальше
# ответ должен быть ссылкой, а не сообщением побольше.
max_payload: 8MB
"""

NATS_SERVICE_BLOCK = """\
  # Events (JetStream) + RPC (request-reply) for stapel_core.comm
  nats:
    image: nats:2.10-alpine
    restart: unless-stopped
    # ЗАПУСК ЧЕРЕЗ КОНФИГ-ФАЙЛ, НЕ ФЛАГИ. `max_payload` у nats-server
    # существует ТОЛЬКО как настройка конфига: флага `--max_payload` нет, и
    # на него сервер отвечает "flag provided but not defined", печатает usage
    # и выходит — то есть контейнер уходит в рестарт-петлю, а всё, что к нему
    # подключается, получает "Name or service not known". Ровно это и
    # случилось на стенде ironmemo 07.08.2026, потому что правку внесли, не
    # запустив. Отсюда же и гейт: e2e поднимает этот сервис живьём.
    command: ["-c", "/etc/nats/nats.conf"]
    volumes:
      - ./nats/nats.conf:/etc/nats/nats.conf:ro
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


def render_compose_base(
    template: str,
    broker: str,
    task_broker: str = "none",
    frontends: "list[Frontend] | None" = None,
) -> str:
    """Splice the chosen broker(s) (nats | kafka | none) into a compose base.

    *task_broker* adds a second broker dedicated to Tasks when it differs
    from the event broker.

    *frontends* fills the frontend-delivery tokens (see
    :func:`render_frontend_delivery`). ``None`` means "this base carries no
    frontend tokens" (the local stack) and is left alone; an EMPTY list means
    "declared, and there are none" — the tokens collapse to nothing.
    """
    brokers = [b for b in ("nats", "kafka") if b in (broker, task_broker)]
    services = "".join(_BROKER_SERVICES[b] for b in brokers)
    volumes = "".join(_BROKER_VOLUMES[b] for b in brokers)
    rendered = template.replace("{{BROKER_SERVICES}}", services).replace(
        "{{BROKER_VOLUMES}}", volumes
    )
    if frontends is not None:
        rendered = render_frontend_delivery(rendered, frontends)
    return rendered


# ---------------------------------------------------------------------------
# frontend delivery — ONE renderer for every topology
# ---------------------------------------------------------------------------
#
# Why this exists (verdict 2026-08-05, tasks/fable/frontend-delivery-split-repo.md;
# tracker #237). The monolith template carried the §57 canon — a one-shot
# `frontend-build` populating a volume, with nginx gated on
# `service_completed_successfully` — and the microservice template carried
# NOTHING: its nginx mounted only `./service-configs/nginx`. The canon did not
# travel. Live consequence on ironmemo: nginx served `root /frontend-react`, a
# bind onto a host directory that both the deploy script and CI explicitly
# EXCLUDED from rsync, so no build ever landed there. For months that read as
# "the frontend does not update" and was repeatedly misdiagnosed as caching.
#
# So the delivery shape is rendered from one function for all three templates.
# Adding a topology cannot silently skip it: the templates carry the tokens,
# and a token left unrendered is a visible `{{...}}` in generated YAML.
#
# `depends_on` deliberately lives in the BASE, not in an overlay. Several
# docker compose versions refuse to override a service that arrived through
# `include:` ("services.nginx conflicts with imported resource" — the same
# reason MONOLITH_COMPOSE_LOCAL is self-contained, see its header comment), and
# nginx is defined in the base. Gating nginx from the prod overlay would
# therefore work on the author's machine and fail on the stand.


@dataclasses.dataclass(frozen=True)
class Frontend:
    """One frontend app delivered to a project's nginx.

    ``delivery`` is the configuration axis the verdict asks for — a project
    switches transport without a different code path:

    * ``build`` — compose builds it from ``context`` (same repo; the monolith
      default, byte-identical to the pre-existing §57 shape).
    * ``image`` — a prebuilt dist-carrier image from a registry, pinned by
      ``${<PREFIX>_IMAGE}:${<PREFIX>_TAG}``. This is the split-repo answer:
      the frontend repo publishes an immutable ``sha-<gitsha>`` tag, and the
      BACKEND repo holds the pin, so "which frontend goes with this backend"
      is answerable from one repo's history.
    * ``host`` — a plain bind mount from ``host_path``. Legacy/escape hatch,
      and the shape ironmemo is in today. Permitted, but it proves nothing
      about delivery, which is exactly why FED001 (stapel-frontend-delivery-
      lint) demands a writer for it.

    Naming rule: the FIRST frontend (``mount == "/"``) keeps the historical
    bare names — volume ``frontend-dist``, container path
    ``/usr/share/nginx/html``, service ``frontend-build`` — so existing
    generated projects and their nginx confs stay valid. Additional frontends
    are suffixed by name (``frontend-dist-kmp``, ``/usr/share/nginx/html-kmp``,
    ``frontend-kmp-build``).
    """

    name: str = "frontend"
    mount: str = "/"
    delivery: str = "build"
    context: str = "./frontend"
    host_path: str = ""

    DELIVERIES = ("build", "image", "host")

    def __post_init__(self) -> None:
        if self.delivery not in self.DELIVERIES:
            raise ValueError(
                f"frontend {self.name!r}: delivery must be one of "
                f"{', '.join(self.DELIVERIES)}, got {self.delivery!r}"
            )
        if self.delivery == "host" and not self.host_path:
            raise ValueError(
                f"frontend {self.name!r}: delivery='host' needs host_path — a bind "
                "mount with no source is the 'nginx serves a directory nobody "
                "fills' shape this machinery exists to abolish"
            )

    @property
    def is_root(self) -> bool:
        return self.mount == "/"

    @property
    def suffix(self) -> str:
        return "" if self.is_root else f"-{self.name}"

    @property
    def volume(self) -> str:
        return f"frontend-dist{self.suffix}"

    @property
    def container_path(self) -> str:
        """Where nginx sees this frontend's build tree (the parent of
        ``current``, not the served root — see :attr:`nginx_root`)."""
        return f"/usr/share/nginx/html{self.suffix}"

    @property
    def nginx_root(self) -> str:
        """What belongs in nginx's ``root``/``alias``.

        The extra ``/current`` is the atomic-swap layout: each build lands in
        its own ``<build-id>/`` directory and ``current`` is repointed at it.
        The shape it replaces was ``rm -rf /output/* && cp -r dist/. /output/``,
        which (a) left a multi-second window where the site 404s and (b) DELETED
        the previous build's hashed assets, so every browser tab open across a
        deploy broke on its next chunk fetch.
        """
        return f"{self.container_path}/current"

    @property
    def service(self) -> str:
        return f"frontend{self.suffix}-build" if self.suffix else "frontend-build"

    @property
    def env_prefix(self) -> str:
        base = "FRONTEND" if self.is_root else f"FRONTEND_{self.name.upper()}"
        return re.sub(r"[^A-Z0-9_]", "_", base)


DEFAULT_FRONTENDS = [Frontend()]


def _frontend_nginx_mounts(frontends: "list[Frontend]") -> str:
    lines = []
    for f in frontends:
        if f.delivery == "host":
            lines.append(f"      - {f.host_path}:{f.container_path}:ro\n")
        else:
            lines.append(f"      - {f.volume}:{f.container_path}:ro\n")
    return "".join(lines)


def _frontend_nginx_depends(frontends: "list[Frontend]") -> str:
    """nginx must not start before each one-shot writer has finished.

    A ``host`` frontend has no writer in compose at all — nothing to wait for —
    and that is precisely the state FED001 refuses to accept silently.
    """
    waited = [f for f in frontends if f.delivery != "host"]
    if not waited:
        return "    depends_on: []\n"
    out = ["    depends_on:\n"]
    for f in waited:
        out.append(f"      {f.service}:\n")
        out.append("        condition: service_completed_successfully\n")
    return "".join(out)


def _frontend_volumes(frontends: "list[Frontend]") -> str:
    return "".join(f"  {f.volume}:\n" for f in frontends if f.delivery != "host")


_FRONTEND_SERVICE_HEADER = """\

  # Prod canon (§57): one-shot writer — populates the {volume} volume nginx
  # serves from (see docker-compose.base.yml). Not a long-lived service:
  # `restart: "no"`, runs once per `docker compose up` and again on demand via
  # `docker compose run --rm {service}`.
"""

_FRONTEND_SERVICE_BUILD = """\
  {service}:
    build:
      context: {context}
    restart: "no"
    environment:
      FRONTEND_KEEP_PREVIOUS: "${{FRONTEND_KEEP_PREVIOUS:-2}}"
    volumes:
      - {volume}:/output
"""

_FRONTEND_SERVICE_IMAGE = """\
  {service}:
    # Split-repo delivery: the frontend repo publishes this dist-CARRIER image
    # (build stage + a one-shot copy into /output) — NOT an nginx image. The
    # project's own nginx stays the single boundary that owns reserved paths,
    # TLS, the proxy table and the cache canon; a frontend repo shipping its own
    # nginx would put half of that outside this repo's gates.
    #
    # The tag is pinned in scripts/env.stand.template (git), never edited on the
    # stand: deploy regenerates .env from that template every run, so a hand
    # edit there disappears silently. Bumping the frontend is a commit here.
    image: "${{{prefix}_IMAGE}}:${{{prefix}_TAG}}"
    restart: "no"
    environment:
      FRONTEND_KEEP_PREVIOUS: "${{FRONTEND_KEEP_PREVIOUS:-2}}"
    volumes:
      - {volume}:/output
"""

_FRONTEND_SERVICE_HOST = """\
  # {name}: delivery=host — nginx binds {host_path} straight from the stand's
  # filesystem, so compose has no writer for it and cannot gate nginx on one.
  # Something OUTSIDE this file has to put a build there; stapel-frontend-
  # delivery-lint (FED001) is what checks that something actually does, and
  # that the deploy script/CI does not exclude the path from its rsync.
"""


def render_frontend_delivery(template: str, frontends: "list[Frontend]") -> str:
    """Fill a compose template's frontend-delivery tokens.

    Tokens (a template may carry any subset):
      ``{{FRONTEND_NGINX_MOUNTS}}``  — nginx volume/bind mounts
      ``{{FRONTEND_NGINX_DEPENDS}}`` — nginx ``depends_on`` gate (BASE only)
      ``{{FRONTEND_VOLUMES}}``       — top-level named volumes
      ``{{FRONTEND_SERVICES}}``      — the one-shot writer services (PROD only)
    """
    services = []
    for f in frontends:
        if f.delivery == "host":
            services.append(
                _FRONTEND_SERVICE_HOST.format(name=f.name, host_path=f.host_path)
            )
            continue
        services.append(
            _FRONTEND_SERVICE_HEADER.format(volume=f.volume, service=f.service)
        )
        if f.delivery == "build":
            services.append(
                _FRONTEND_SERVICE_BUILD.format(
                    service=f.service, context=f.context, volume=f.volume
                )
            )
        else:
            services.append(
                _FRONTEND_SERVICE_IMAGE.format(
                    service=f.service, prefix=f.env_prefix, volume=f.volume
                )
            )
    return render_tokens(
        template,
        {
            "FRONTEND_NGINX_MOUNTS": _frontend_nginx_mounts(frontends).rstrip("\n"),
            "FRONTEND_NGINX_DEPENDS": _frontend_nginx_depends(frontends).rstrip("\n"),
            "FRONTEND_VOLUMES": _frontend_volumes(frontends).rstrip("\n"),
            "FRONTEND_SERVICES": "".join(services).rstrip("\n"),
        },
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
{{FRONTEND_NGINX_MOUNTS}}
{{FRONTEND_NGINX_DEPENDS}}

volumes:
  db-data:
  redis-data:
{{BROKER_VOLUMES}}  static-content:
  media-content:
{{FRONTEND_VOLUMES}}
"""

MONOLITH_COMPOSE_PROD = """\
include:
  - docker-compose.base.yml

services:
{{FRONTEND_SERVICES}}

  # Add backend services from their individual .yml files:
  # svc-app:
  #   extends:
  #     file: svc-app.yml
  #     service: svc-app
  #   volumes:
  #     - ./svc-app:/app
  #     - ./stapel_core:/app/stapel_core:ro
"""

MONOLITH_COMPOSE_LOCAL = """\
# Local stack (docker-compose.local.yml) — the LOCAL MACHINE's compose file.
# Naming canon: `local` = this machine; dev/stage/prod compose names are
# reserved for STANDS (prod = docker-compose.yml, deployed via deploy/).
#
# SELF-CONTAINED on purpose — no `include:` of docker-compose.base.yml.
# Root cause (found live in CI): several docker compose versions reject
# overriding a service that arrives via include ("services.nginx conflicts
# with imported resource"), and the local stack NEEDS a differently-wired
# nginx (envsubst template proxying to Vite instead of serving the built
# frontend) and backend (dev settings, source bind mounts). So the local
# stack declares its own copies of the shared infra instead of fighting
# include semantics. Local volumes are suffixed -local so a prod compose
# run on the same machine never shares database state with the local stack.
#
# Run:  docker compose -f docker-compose.local.yml --env-file .env.local up
# Then: http://localhost:${HTTP_PORT:-8080}
#
# NOTE: `volumes:` is declared BEFORE `services:` on purpose — `services:`
# must stay the LAST top-level section in this file: stapel-new-service's
# _update_compose_file() appends each backend service by raw text at EOF.
volumes:
  db-data-local:
  redis-data-local:
{{BROKER_VOLUMES}}  frontend-node-modules:
  # named by svc-*.yml (extends brings the refs along); harmless locally —
  # the dev backend serves static/media itself via runserver.
  static-content:
  media-content:

services:
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: "${POSTGRES_USER:-stapel}"
      POSTGRES_PASSWORD: "${POSTGRES_PASSWORD:-stapel}"
      POSTGRES_MULTIPLE_DATABASES: "stapel_main,{{DB_NAME}}"
    volumes:
      - db-data-local:/var/lib/postgresql/data
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
      - redis-data-local:/data

{{BROKER_SERVICES}}  # Dev canon (§57): plain node image — no bespoke Dockerfile to go stale —
  # runs the Vite dev server with hot reload; logs via
  # `docker compose -f docker-compose.local.yml logs -f frontend`.
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

  # Local nginx: mounts service-configs/nginx-local at /etc/nginx/templates
  # ONLY (never conf.d — that stays writable inside the container so the
  # nginx image's own envsubst step can render
  # templates/default.conf.template -> conf.d/default.conf, OVERWRITING the
  # image's shipped default site; the template MUST be named
  # default.conf.template for that overwrite to happen). Proxy targets are
  # env vars with compose-network defaults — override in .env.local for a
  # native run (backend/frontend on the host).
  nginx:
    image: nginx:alpine
    restart: unless-stopped
    ports:
      - "${HTTP_PORT:-8080}:80"
    volumes:
      - ./service-configs/nginx-local:/etc/nginx/templates:ro
    environment:
      BACKEND_UPSTREAM: "${BACKEND_UPSTREAM:-{{BACKEND_UPSTREAM_DEFAULT}}}"
      FRONTEND_LOCAL_UPSTREAM: "${FRONTEND_LOCAL_UPSTREAM:-frontend:5173}"
    depends_on:
      - frontend

  # Backend service(s) appended below by stapel-new-service (dev-mode block:
  # .env.local + config.settings.dev + source bind mounts).
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

# Public origin of the FRONTEND — REQUIRED in prod/staging. Every off-session
# redirect a Stapel module issues is built on it (auth SSO callback / magic
# link / QR / OTP-challenge / security verification links, billing checkout +
# portal returns, notification links); stapel_auth.E003 refuses to boot with
# DEBUG=False and no value. Same origin as SITE_URL when nginx serves the built
# SPA from this stack (the default); point it at the SPA's own origin when the
# frontend is deployed separately. The dev value lives in .env.local /
# config/settings/dev.py, never in the shared settings base.
FRONTEND_URL={url}

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

# Project-root Makefile (studio controls contract, R3/§44 follow-up): the
# generated backend lives in {{DIR_NAME}}/, not at the root, so the root
# targets delegate into {{DIR_NAME}}/Makefile (SVC_MAKEFILE in _templates.py)
# rather than reimplementing lint/test/boot-smoke here. Target names and
# `controls: lint boot-smoke test` semantics match the minimal preset's
# Makefile (_minimal_templates.MINIMAL_MAKEFILE) 1:1, so a studio contract
# that runs `make -C <root> controls` behaves identically regardless of which
# preset generated the project. frontend/ lint/test is a separate stage (its
# own `npx eslint .` — see AGENTS.md's frontend section / pre-commit config)
# not wired into these targets yet — that is a follow-up, not a silent gap:
# these targets cover the backend, which MUST be controls-green from a fresh
# checkout.
MONOLITH_MAKEFILE = """\
# {{TITLE}} — project controls (backend). See frontend/README.md for the
# frontend's own lint/build — not yet wired into these targets (follow-up).
PYTHON ?= python

.PHONY: controls lint test boot-smoke

controls: lint boot-smoke test

lint:
\t$(MAKE) -C {{DIR_NAME}} lint PYTHON=$(PYTHON)

boot-smoke:
\t$(MAKE) -C {{DIR_NAME}} boot-smoke PYTHON=$(PYTHON)

test:
\t$(MAKE) -C {{DIR_NAME}} test PYTHON=$(PYTHON)
"""

MONOLITH_GITIGNORE = """\
.env
# Stand env files (dev/stage/prod STANDS — the reserved names) are generated
# per stand by the deploy flow (see deploy/) and MUST NOT be committed:
.env.dev
.env.stage
.env.prod
# .env.local is deliberately NOT ignored (owner decision, §57 revision): it
# is COMMITTED — strictly the LOCAL MACHINE env — so `clone → docker compose
# -f docker-compose.local.yml up` works for every developer. Safe because it
# carries only recognizable dev markers (django-insecure-dev-* keys, default
# postgres password) — deploy/check-env.sh and stapel-core's prodguard both
# refuse it outside local dev.
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
{{FRONTEND_NGINX_MOUNTS}}
{{FRONTEND_NGINX_DEPENDS}}

volumes:
  db-data:
  redis-data:
{{BROKER_VOLUMES}}  static-content:
  media-content:
{{FRONTEND_VOLUMES}}
"""

MICRO_COMPOSE_PROD = """\
include:
  - docker-compose.base.yml

services:
{{FRONTEND_SERVICES}}

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

# Public origin of the FRONTEND — REQUIRED in prod/staging, shared verbatim by
# every service (compose passes the same .env to all). Every off-session
# redirect a Stapel module issues is built on it (auth SSO callback / magic
# link / QR / OTP-challenge / security verification links, billing checkout +
# portal returns, notification links); stapel_auth.E003 refuses to boot with
# DEBUG=False and no value. The dev value lives in .env.local /
# config/settings/dev.py, never in the shared settings base.
FRONTEND_URL={url}

# ─── Frontend delivery ──────────────────────────────────────────────────────
# This project's frontend lives in its OWN repository, so compose cannot build
# it: it pulls the dist-carrier image that repo publishes and a one-shot service
# copies the bundle into the volume nginx serves (docker-compose.yml's
# `frontend-build`).
#
# FRONTEND_TAG must be an IMMUTABLE tag — `sha-<gitsha>`, the shape the frontend
# CI pushes. Never `latest`/`dev`: with a moving tag "which frontend is on this
# stand" has no answer and a redeploy silently changes the app. The pin belongs
# in git (this template), NOT in the stand's .env — deploy regenerates .env from
# here on every run, so an edit made on the server disappears without a word.
# Bumping the frontend is a commit in THIS repo; that is what makes the
# backend↔frontend pair readable from one repository's history.
# stapel-frontend-delivery-lint enforces both halves (FED002, FED003).
FRONTEND_IMAGE=registry.example.com/{name}/frontend
FRONTEND_TAG=sha-0000000
# How many previous builds stay on disk beside the live one. They exist so a
# browser tab opened BEFORE a deploy can still fetch its content-hashed chunks
# instead of dying with a chunk-load error.
FRONTEND_KEEP_PREVIOUS=2

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


# ─── Generative backend route prefixes (owner directive: never a list) ──────
# One reviewed block shape for every generated backend location — the SET of
# prefixes comes from the caller (create_project derives it from STAPEL_LIBS
# for the selected modules + the service slug + admin), so a new lib's rule
# appears by construction, not by remembering to edit a template.

def _nginx_location_path(prefix: str) -> tuple[str, str]:
    """Classify one reserved-prefix entry into (location-modifier, path).

    A bare entry (no ``/`` — the project's own slug, ``admin``) reserves its
    WHOLE subtree: a plain prefix location on ``/<prefix>/``. A module
    sub-surface entry (``"<mod>/api"``, ``"<mod>/swagger"``, ``"<mod>/admin"``)
    reserves only that named path: ``^~`` (a non-regex prefix location that
    wins over any later regex location, owner directive) on ``/<mod>/api/``.
    The one dotted, extension-terminated surface (``"<mod>/schema.json"``) is
    a single file, matched EXACTLY (``=``) rather than as a directory prefix —
    together these never reserve a module's bare root or an arbitrary
    sub-path (the "/calendar page vs backend" collision this fixes; see
    create_project._reserved_paths_manifest)."""
    if "/" not in prefix:
        return "", f"/{prefix}/"
    if prefix.endswith(".json"):
        return "=", f"/{prefix}"
    return "^~", f"/{prefix}/"


def nginx_local_backend_locations(prefixes: list[str]) -> str:
    """location blocks for the local-nginx envsubst template — one per
    reserved backend prefix/sub-surface, proxying to ${BACKEND_UPSTREAM}."""
    blocks = []
    for prefix in prefixes:
        modifier, path = _nginx_location_path(prefix)
        head = f"location {modifier} {path} {{" if modifier else f"location {path} {{"
        blocks.append(
            f"    {head}\n"
            "        proxy_pass $stapel_backend;\n"
            "        proxy_set_header Host $http_host;\n"
            "        proxy_set_header X-Real-IP $remote_addr;\n"
            "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            "        proxy_set_header X-Forwarded-Proto $scheme;\n"
            "        proxy_redirect off;\n"
            "    }\n"
        )
    return "\n".join(blocks)


def nginx_prod_backend_location(prefix: str, upstream_service: str) -> str:
    """One prod-nginx location block proxying a reserved backend
    prefix/sub-surface to a compose service (same shape stapel-new-service
    appends per service)."""
    modifier, path = _nginx_location_path(prefix)
    head = f"location {modifier} {path} {{" if modifier else f"location {path} {{"
    # nginx variable names allow only [A-Za-z0-9_] — sanitize every
    # separator a prefix/sub-surface entry can carry ("-", "/", the "."
    # in "schema.json").
    var_name = re.sub(r"[^A-Za-z0-9_]", "_", prefix)
    var = f"$upstream_{var_name}"
    return (
        f"\n  {head}\n"
        f"    set {var} {upstream_service}:8000;\n"
        f"    proxy_pass http://{var};\n"
        f"    proxy_set_header Host $http_host;\n"
        f"    proxy_set_header X-Real-IP $remote_addr;\n"
        f"    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        f"    proxy_set_header X-Forwarded-Proto $scheme;\n"
        f"    proxy_redirect off;\n"
        f"  }}\n"
    )
