"""Dev-env canon (§57 owner directive item 7 — a freshly generated project
must work right after `docker compose up`, zero manual config).

Two pieces:

1. ``DEV_MOCK_OTP_BLOCK`` — spliced into ``config/settings/dev.py`` (monolith
   /microservices, via new_service.make_context's DEV_MOCK_PROVIDERS token)
   and into ``config/settings.py``'s local branch (minimal preset) whenever
   stapel-auth is among the project's modules. Sets
   ``STAPEL_AUTH["USE_MOCK_SMS_OTP"/"USE_MOCK_EMAIL_OTP"] = True`` — verified
   against stapel-auth/otp/services.py: both PhoneVerificationService and
   EmailVerificationService log the code via
   ``logger.info(f"Mock OTP mode - Verification code for {phone}: {code}")``
   when the mock flag is set, so registration/login is completable by
   reading the dev log, no Twilio/SMTP credentials needed. These two keys
   are booleans, hence in stapel-auth's AppSettings ``no_env`` list (conf.py)
   — they CANNOT be set via a plain env var, only via the STAPEL_AUTH dict in
   settings, which is why this is a settings-file block, not an .env line.
   stapel-notifications needs NO override: EMAIL_PROVIDER/SMS_PROVIDER
   already default to "mock" (conf.py DEFAULTS) and its own _MockSMSProvider
   / _MockEmailProvider already log to `logger.info`.

2. ``.env.dev`` templates — a real, generated (not placeholder) dev
   environment file: fresh secrets (same generator as .env), Django
   superuser defaults for the entrypoint canon (bootstrap.sh), Vite/backend
   proxy targets, and a preset switch (``env_preset``): "standalone"
   (default) or "studio" (projects spun up FROM stapel-studio). The studio
   values are DOCUMENTED STUBS — no stapel-sender / studio-OAuth
   infrastructure exists yet; inventing a working implementation here would
   be lying about what this generates. Only the shape of the preset + a
   TODO trail is real.
"""

DEV_MOCK_OTP_BLOCK = """

# ─── Dev-only mock OTP (§57 owner directive item 2) ─────────────────────────
# Codes are logged, never sent (stapel_auth/otp/services.py: PhoneVerification
# -/EmailVerificationService both `logger.info` the code in mock mode) — a
# fresh checkout can complete registration/login by reading this service's
# log, no Twilio/SMTP credentials needed. USE_MOCK_SMS_OTP/USE_MOCK_EMAIL_OTP
# are booleans (stapel_auth AppSettings no_env list) — they can only be set
# here, never via a plain env var. Merges over any STAPEL_AUTH block already
# rendered above (module_config), rather than clobbering it.
STAPEL_AUTH = {
    **globals().get("STAPEL_AUTH", {}),
    "USE_MOCK_SMS_OTP": True,
    "USE_MOCK_EMAIL_OTP": True,
}
"""

# ── .env.dev — standalone preset (default) ─────────────────────────────────
ENV_DEV_STANDALONE = """\
# ─── .env.dev — local development (§57 owner directive item 7) ─────────────
# Generated at project creation with REAL secrets (not placeholders) so
# `docker compose -f docker-compose.dev.yml --env-file .env.dev up` works
# with zero manual configuration. Gitignored, like .env — never commit it.
#
# Channel-origin preset: standalone (this file) — a project created directly
# (not spun up from stapel-studio). See ENV_DEV_STUDIO for the studio
# variant (email/oauth stubs) — chosen via `--env-preset studio`.

DJANGO_ENV=local
DEBUG=true

# ─── Database (compose network; sqlite fallback for a native run below) ────
POSTGRES_USER=stapel
POSTGRES_PASSWORD={postgres_password}
POSTGRES_HOST=db
POSTGRES_PORT=5432
# Native run (backend on the host, not in Docker): comment the 4 lines above
# and uncomment this one instead — no Postgres/Docker needed at all for a
# quick backend-only spin-up.
# DATABASE_URL=sqlite:///db.sqlite3

# ─── Redis ───────────────────────────────────────────────────────────────
REDIS_URL=redis://redis:6379/0

# ─── Comm bus: inline by default (§57 item 1 — dev must never assume a
# heavyweight broker like Kafka is running). Matches whatever broker this
# project actually chose at creation (STAPEL_BUS_BACKEND below, if any);
# absent entirely means in-process + outbox, no broker required.
{broker_env}
# ─── App ─────────────────────────────────────────────────────────────────
SECRET_KEY={secret_key}
JWT_SECRET_KEY={jwt_secret_key}
ALLOWED_HOSTS=*
SITE_URL=http://localhost

# ─── Entrypoint canon (bootstrap.sh, §57 item 3) — Django's own
# createsuperuser --noinput reads these natively. Dev-only convenience
# credentials; change them (or unset to skip auto-creation) before any
# shared/staging use.
DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_EMAIL=admin@example.com
DJANGO_SUPERUSER_PASSWORD={superuser_password}

# ─── Frontend dev proxy targets (§57 item 1) — compose-network defaults;
# override here for a native run (e.g. backend/frontend on the host):
# BACKEND_UPSTREAM=localhost:8000, FRONTEND_DEV_UPSTREAM=localhost:5173.
BACKEND_UPSTREAM={backend_upstream}
FRONTEND_DEV_UPSTREAM=frontend:5173
VITE_BACKEND_TARGET=http://{backend_upstream}

# ─── Run command — Django's own dev server (autoreload, plain HTTP, no
# gunicorn workers) instead of the prod RUN_CMD in .env/.env.example.
RUN_CMD=python manage.py runserver 0.0.0.0:8000

# ─── Email (dev mock — stapel-notifications defaults EMAIL_PROVIDER/
# SMS_PROVIDER to "mock" already; nothing to set here for that). The
# non-notifications DEFAULT_FROM_EMAIL below is cosmetic only.
DEFAULT_FROM_EMAIL={company_name} <{company_email}>
"""

# ── .env.dev — studio preset (STUB — infra not built yet) ──────────────────
ENV_DEV_STUDIO = """\
# ─── .env.dev — local development (§57 owner directive item 7) ─────────────
# Generated at project creation with REAL secrets (not placeholders) so
# `docker compose -f docker-compose.dev.yml --env-file .env.dev up` works
# with zero manual configuration. Gitignored, like .env — never commit it.
#
# Channel-origin preset: STUDIO — this project was spun up FROM stapel-studio.
# The two blocks marked STUB below are DOCUMENTED PLACEHOLDERS: neither the
# generic stapel-sender email transport nor a stapel-studio OAuth app exists
# yet. Do not treat the keys below as working config — they are the shape a
# future studio integration will fill in, so a studio-originated project's
# .env.dev is forward-compatible without a second migration later.

DJANGO_ENV=local
DEBUG=true

# ─── Database (compose network; sqlite fallback for a native run below) ────
POSTGRES_USER=stapel
POSTGRES_PASSWORD={postgres_password}
POSTGRES_HOST=db
POSTGRES_PORT=5432
# Native run: comment the 4 lines above, uncomment this one instead.
# DATABASE_URL=sqlite:///db.sqlite3

# ─── Redis ───────────────────────────────────────────────────────────────
REDIS_URL=redis://redis:6379/0

# ─── Comm bus: inline by default (see standalone preset's comment) ─────────
{broker_env}
# ─── App ─────────────────────────────────────────────────────────────────
SECRET_KEY={secret_key}
JWT_SECRET_KEY={jwt_secret_key}
ALLOWED_HOSTS=*
SITE_URL=http://localhost

# ─── Entrypoint canon (bootstrap.sh, §57 item 3) ────────────────────────────
DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_EMAIL=admin@example.com
DJANGO_SUPERUSER_PASSWORD={superuser_password}

# ─── Frontend dev proxy targets (§57 item 1) ────────────────────────────────
BACKEND_UPSTREAM={backend_upstream}
FRONTEND_DEV_UPSTREAM=frontend:5173
VITE_BACKEND_TARGET=http://{backend_upstream}

# ─── STUB — studio sender (TODO §57 studio preset) ──────────────────────────
# TODO(§57 studio preset): no generic stapel-sender email transport exists
# yet — this is a documented placeholder key only, NOT a working provider.
# Once it ships, wire STAPEL_NOTIFICATIONS["EMAIL_PROVIDER"] to it instead
# of "mock" (stapel-notifications conf.py DEFAULTS).
NOTIFICATIONS_EMAIL_PROVIDER_STUDIO_TODO=stapel_sender.EmailProvider

# ─── STUB — "Login via Stapel Studio" OAuth (TODO §57 studio preset) ────────
# TODO(§57 studio preset): no stapel-studio OAuth application/infra exists
# yet — these are documented placeholder keys only. Once it ships, add a
# "stapel_studio" entry to STAPEL_AUTH["OAUTH_PROVIDERS"] (stapel-auth
# conf.py) with these credentials so the login screen can offer a
# "Login via Stapel Studio" button.
STAPEL_STUDIO_OAUTH_CLIENT_ID=
STAPEL_STUDIO_OAUTH_CLIENT_SECRET=

# ─── Run command — Django's own dev server (autoreload, plain HTTP, no
# gunicorn workers) instead of the prod RUN_CMD in .env/.env.example.
RUN_CMD=python manage.py runserver 0.0.0.0:8000

# ─── Email (dev mock fallback until the sender stub above is real) ─────────
DEFAULT_FROM_EMAIL={company_name} <{company_email}>
"""

ENV_DEV_PRESETS = {"standalone": ENV_DEV_STANDALONE, "studio": ENV_DEV_STUDIO}
