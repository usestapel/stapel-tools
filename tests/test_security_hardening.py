"""SEC-4/SEC-6 (security-programme.md §5.3): generated-project settings
hardening + the prod-guard that refuses placeholder secrets, and the
connective tissue between the two — stapel-create-project writes real random
secrets into .env specifically so the guard only ever fires on a
copy-pasted .env.example.
"""
import importlib.util
import os
import subprocess
import sys

import pytest

from stapel_tools.create_project import create_project

# security-programme.md B2: the placeholders shipped in .env.example before
# SEC-6, and the library's dev-only Postgres default (B6).
PLACEHOLDER_SECRET = "change_me_to_a_long_random_string"
PLACEHOLDER_JWT_SECRET = "change_me_to_another_long_random_string"
PLACEHOLDER_DB_PASSWORD = "change_me"
DEV_DEFAULT_DB_PASSWORD = "stapel"


def _create(tmp_path, name, project_type, modules=None):
    create_project(
        name=name,
        project_type=project_type,
        title=name,
        url="https://x.dev",
        company_name="X",
        company_email="x@x.dev",
        modules=modules or ["core"],
        output_dir=tmp_path,
        use_submodules=False,
        init_git=False,
    )
    return tmp_path / name


def _read_env(path):
    """Parse a simple KEY=value .env file into a dict (ignores comments)."""
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def _boot(cwd, settings_module, extra_env):
    """Boot Django settings the way gunicorn/WSGI actually does (django.setup()
    directly) — not `manage.py`, whose early INSTALLED_APPS probe swallows
    ImproperlyConfigured and silently skips app loading instead of failing,
    making `manage.py check` useless for exercising this guard."""
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": settings_module, **extra_env}
    return subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=cwd, env=env, capture_output=True, text=True,
    )


class TestGeneratedSecretsAreRandom:
    """SEC-6: stapel-create-project writes fresh random secrets into .env;
    .env.example keeps placeholders (safe to commit)."""

    def test_monolith_env_has_no_placeholders(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        env = _read_env(proj / ".env")
        example = _read_env(proj / ".env.example")

        assert example["SECRET_KEY"] == PLACEHOLDER_SECRET
        assert example["JWT_SECRET_KEY"] == PLACEHOLDER_JWT_SECRET
        assert example["POSTGRES_PASSWORD"] == PLACEHOLDER_DB_PASSWORD

        assert env["SECRET_KEY"] != PLACEHOLDER_SECRET
        assert env["JWT_SECRET_KEY"] != PLACEHOLDER_JWT_SECRET
        assert env["POSTGRES_PASSWORD"] not in (PLACEHOLDER_DB_PASSWORD, DEV_DEFAULT_DB_PASSWORD)
        assert len(env["SECRET_KEY"]) >= 50
        assert len(env["JWT_SECRET_KEY"]) >= 50
        assert env["SECRET_KEY"] != env["JWT_SECRET_KEY"]

    def test_monolith_secrets_are_unique_per_project(self, tmp_path):
        proj_a = _create(tmp_path, "app-a", "monolith")
        proj_b = _create(tmp_path, "app-b", "monolith")
        env_a = _read_env(proj_a / ".env")
        env_b = _read_env(proj_b / ".env")
        assert env_a["SECRET_KEY"] != env_b["SECRET_KEY"]
        assert env_a["POSTGRES_PASSWORD"] != env_b["POSTGRES_PASSWORD"]

    def test_minimal_env_has_no_placeholders(self, tmp_path):
        proj = _create(tmp_path, "app", "minimal")
        env = _read_env(proj / ".env")
        example = _read_env(proj / ".env.example")

        assert example["SECRET_KEY"] == PLACEHOLDER_SECRET
        assert env["SECRET_KEY"] != PLACEHOLDER_SECRET
        assert len(env["SECRET_KEY"]) >= 50

    def test_microservices_env_has_no_placeholders(self, tmp_path):
        proj = _create(tmp_path, "app", "microservices")
        env = _read_env(proj / ".env")
        assert env["SECRET_KEY"] != PLACEHOLDER_SECRET
        assert env["POSTGRES_PASSWORD"] not in (PLACEHOLDER_DB_PASSWORD, DEV_DEFAULT_DB_PASSWORD)


class TestProdSettingsTemplateContent:
    """SEC-4 items 1-3: SECURE_* / HSTS / CSP / cookie hardening are present
    in the generated prod tier (static content check — cheap, and pins the
    exact conservative defaults called for: 86400s HSTS, no subdomains, no
    preload)."""

    def test_monolith_prod_settings(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        prod = (proj / "svc-app" / "config" / "settings" / "prod.py").read_text()

        assert "SECURE_SSL_REDIRECT" in prod
        assert 'SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "86400"))' in prod
        assert "SECURE_HSTS_INCLUDE_SUBDOMAINS = False" in prod
        assert "SECURE_HSTS_PRELOAD = False" in prod
        assert "SECURE_CONTENT_TYPE_NOSNIFF = True" in prod
        assert "SESSION_COOKIE_SECURE = True" in prod
        assert "CSRF_COOKIE_SECURE = True" in prod
        assert "JWT_COOKIE_SECURE = True" in prod
        assert "SECURE_CSP_REPORT_ONLY" in prod
        assert "ContentSecurityPolicyMiddleware" in prod
        assert "guard_secret(\"SECRET_KEY\", SECRET_KEY)" in prod
        assert "guard_secret(\"JWT_SECRET_KEY\", JWT_SECRET_KEY)" in prod
        assert "guard_db_password(DATABASES[\"default\"].get(\"PASSWORD\"))" in prod

    def test_minimal_gets_a_prod_profile(self, tmp_path):
        """B8: the minimal preset previously had no prod profile at all."""
        proj = _create(tmp_path, "app", "minimal")
        settings = (proj / "config" / "settings.py").read_text()

        assert "DJANGO_ENV" in settings
        assert "_IS_PROD" in settings
        assert "SECURE_SSL_REDIRECT" in settings
        assert "SECURE_HSTS_SECONDS" in settings
        assert "guard_secret" in settings
        # Local DX must stay unaffected by default.
        assert 'DEBUG = os.getenv("DEBUG", "false" if _IS_PROD else "true")' in settings


class TestProdGuardRuntimeBehavior:
    """The guard as it actually behaves at process boot (django.setup(), the
    same call gunicorn/WSGI make) — not `manage.py`, see `_boot()` docstring."""

    def test_monolith_prod_boots_with_generated_secrets(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        svc = proj / "svc-app"
        env = _read_env(proj / ".env")
        result = _boot(svc, "config.settings.prod", {
            "SECRET_KEY": env["SECRET_KEY"],
            "JWT_SECRET_KEY": env["JWT_SECRET_KEY"],
            "POSTGRES_PASSWORD": env["POSTGRES_PASSWORD"],
        })
        assert result.returncode == 0, result.stderr

    def test_monolith_prod_rejects_placeholder_secret_key(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        svc = proj / "svc-app"
        env = _read_env(proj / ".env")
        result = _boot(svc, "config.settings.prod", {
            "SECRET_KEY": PLACEHOLDER_SECRET,
            "JWT_SECRET_KEY": env["JWT_SECRET_KEY"],
            "POSTGRES_PASSWORD": env["POSTGRES_PASSWORD"],
        })
        assert result.returncode != 0
        assert "ImproperlyConfigured" in result.stderr
        assert "SECRET_KEY" in result.stderr

    def test_monolith_prod_rejects_placeholder_db_password(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        svc = proj / "svc-app"
        env = _read_env(proj / ".env")
        result = _boot(svc, "config.settings.prod", {
            "SECRET_KEY": env["SECRET_KEY"],
            "JWT_SECRET_KEY": env["JWT_SECRET_KEY"],
            "POSTGRES_PASSWORD": PLACEHOLDER_DB_PASSWORD,
        })
        assert result.returncode != 0
        assert "POSTGRES_PASSWORD" in result.stderr

    def test_monolith_prod_rejects_default_dev_db_password(self, tmp_path):
        """B6: unset POSTGRES_PASSWORD falls back to the library's dev
        default ('stapel') — that default is fine for local Compose but must
        not pass the prod guard."""
        proj = _create(tmp_path, "app", "monolith")
        svc = proj / "svc-app"
        env = _read_env(proj / ".env")
        boot_env = {
            "SECRET_KEY": env["SECRET_KEY"],
            "JWT_SECRET_KEY": env["JWT_SECRET_KEY"],
        }
        # Explicitly unset POSTGRES_PASSWORD so get_default_database() falls
        # back to its 'stapel' dev default rather than inheriting the parent
        # shell's value.
        merged = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.prod", **boot_env}
        merged.pop("POSTGRES_PASSWORD", None)
        result = subprocess.run(
            [sys.executable, "-c", "import django; django.setup()"],
            cwd=svc, env=merged, capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "POSTGRES_PASSWORD" in result.stderr

    def test_minimal_local_default_boots_with_no_env_at_all(self, tmp_path):
        """DX invariant: the default (DJANGO_ENV unset -> 'local') must keep
        working with zero configuration, exactly like before this change."""
        proj = _create(tmp_path, "app", "minimal")
        env = {k: v for k, v in os.environ.items() if k not in ("DJANGO_ENV", "SECRET_KEY")}
        env["DJANGO_SETTINGS_MODULE"] = "config.settings"
        result = subprocess.run(
            [sys.executable, "-c", "import django; django.setup()"],
            cwd=proj, env=env, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_minimal_prod_boots_with_generated_secret(self, tmp_path):
        proj = _create(tmp_path, "app", "minimal")
        env = _read_env(proj / ".env")
        result = _boot(proj, "config.settings", {
            "DJANGO_ENV": "prod",
            "SECRET_KEY": env["SECRET_KEY"],
        })
        assert result.returncode == 0, result.stderr

    def test_minimal_prod_rejects_placeholder_secret(self, tmp_path):
        proj = _create(tmp_path, "app", "minimal")
        result = _boot(proj, "config.settings", {
            "DJANGO_ENV": "prod",
            "SECRET_KEY": PLACEHOLDER_SECRET,
        })
        assert result.returncode != 0
        assert "ImproperlyConfigured" in result.stderr

    def test_minimal_prod_rejects_missing_secret(self, tmp_path):
        proj = _create(tmp_path, "app", "minimal")
        env = {k: v for k, v in os.environ.items() if k != "SECRET_KEY"}
        env["DJANGO_SETTINGS_MODULE"] = "config.settings"
        env["DJANGO_ENV"] = "prod"
        result = subprocess.run(
            [sys.executable, "-c", "import django; django.setup()"],
            cwd=proj, env=env, capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "ImproperlyConfigured" in result.stderr


def _manage_check(manage_dir, settings_module, env_values):
    """`manage.py check` — the system-check phase, which is where a module's
    own deploy gates live (stapel_auth.E003 & co). Distinct from `_boot()`
    above: django.setup() alone never runs the check registry, so a settings
    template can boot fine and still hand the client a project whose very
    first `manage.py check` is red."""
    env = {**os.environ, **env_values, "DJANGO_SETTINGS_MODULE": settings_module}
    return subprocess.run(
        [sys.executable, "manage.py", "check"],
        cwd=manage_dir, env=env, capture_output=True, text=True,
    )


_AUTH_INSTALLED = importlib.util.find_spec("stapel_auth") is not None
_needs_auth = pytest.mark.skipif(
    not _AUTH_INSTALLED,
    reason="stapel-auth not importable (bare stapel-tools checkout); CI's Tests "
           "job installs it, so this gate does run for real there",
)


class TestFrontendUrlReachesTheProdTier:
    """The generator used to hand the client a project that was red out of the
    box: no settings tier and no generated .env carried FRONTEND_URL, so
    ``manage.py check`` under any DEBUG=False tier failed stapel_auth.E003 —
    every off-session redirect the auth pair issues (SSO callback, magic link,
    QR, OTP-challenge, security verification links) would have resolved
    against an empty base, or worse against some other module's leftover dev
    default. The check was right; the templates were not.

    The contract these tests lock in is the one E003's own hint states: a real
    origin reaches prod/staging through the environment, and any dev fallback
    is confined to a dev-only settings layer — never the shared base module
    prod inherits.
    """

    @_needs_auth
    def test_monolith_prod_check_is_green_with_the_generated_env(self, tmp_path):
        """The load-bearing gate: the client's first `manage.py check` against
        the tier a real deploy runs (prod, DEBUG=False), fed the project's own
        generated .env — nothing hand-set."""
        proj = _create(tmp_path, "app", "monolith", modules=["core", "auth"])
        result = _manage_check(
            proj / "svc-app", "config.settings.prod", _read_env(proj / ".env"),
        )
        assert result.returncode == 0, result.stdout + result.stderr

    @_needs_auth
    def test_monolith_prod_check_fails_loud_without_frontend_url(self, tmp_path):
        """The gate above must be live, not vacuous: drop the one key and the
        same command has to go red on E003 again."""
        proj = _create(tmp_path, "app", "monolith", modules=["core", "auth"])
        env = _read_env(proj / ".env")
        env.pop("FRONTEND_URL")
        env["FRONTEND_URL"] = ""
        result = _manage_check(proj / "svc-app", "config.settings.prod", env)
        assert result.returncode != 0
        assert "stapel_auth.E003" in result.stdout + result.stderr

    @_needs_auth
    def test_monolith_boot_smoke_check_is_green_standalone(self, tmp_path):
        """The boot-smoke tier runs with no .env sourced at all (that is its
        whole point — a fresh checkout, no docker), and with DEBUG=False, so
        it needs its OWN gate-only fallback the way it already seeds
        SECRET_KEY."""
        proj = _create(tmp_path, "app", "monolith", modules=["core", "auth"])
        result = _manage_check(proj / "svc-app", "config.settings.boot_smoke", {})
        assert result.returncode == 0, result.stdout + result.stderr

    @_needs_auth
    def test_minimal_prod_check_is_green_with_the_generated_env(self, tmp_path):
        proj = _create(tmp_path, "app", "minimal", modules=["core", "auth"])
        result = _manage_check(
            proj, "config.settings", {**_read_env(proj / ".env"), "DJANGO_ENV": "prod"},
        )
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize("project_type,env_dir", [
        ("monolith", "."), ("microservices", "."), ("minimal", "."),
    ])
    def test_generated_env_carries_a_real_origin(self, tmp_path, project_type, env_dir):
        """Both halves: .env.example documents the key (it gets committed) and
        the generated .env carries the project's declared public URL, not a
        localhost placeholder that would ship to prod."""
        proj = _create(tmp_path, "app", project_type)
        for name in (".env", ".env.example"):
            env = _read_env(proj / env_dir / name)
            assert env["FRONTEND_URL"] == "https://x.dev", name

    def test_dev_fallback_is_confined_to_the_dev_layer(self, tmp_path):
        """E003's hint, enforced: base.py (what prod.py star-imports) reads the
        env with NO fallback; the localhost default exists only in dev.py."""
        proj = _create(tmp_path, "app", "monolith")
        settings = proj / "svc-app" / "config" / "settings"
        base = (settings / "base.py").read_text()
        dev = (settings / "dev.py").read_text()
        prod = (settings / "prod.py").read_text()

        assert 'FRONTEND_URL = os.getenv("FRONTEND_URL", "")' in base
        # No *code* line in base may hand FRONTEND_URL an origin of its own —
        # comments explaining why are exactly what we want there.
        base_code = [
            line for line in base.splitlines()
            if "FRONTEND_URL" in line and not line.lstrip().startswith("#")
        ]
        assert base_code == ['FRONTEND_URL = os.getenv("FRONTEND_URL", "")']
        assert 'FRONTEND_URL = FRONTEND_URL or "http://localhost"' in dev
        # prod adds nothing of its own — it inherits base's env read, so there
        # is no second place a stale default could hide.
        assert "FRONTEND_URL" not in prod

    def test_minimal_dev_fallback_is_confined_to_the_non_prod_branch(self, tmp_path):
        proj = _create(tmp_path, "app", "minimal")
        settings = (proj / "config" / "settings.py").read_text()
        assert 'FRONTEND_URL = os.getenv("FRONTEND_URL", "")' in settings
        assert 'if not FRONTEND_URL and not _IS_PROD:' in settings

    def test_committed_dev_env_carries_a_local_origin(self, tmp_path):
        """.env.local is committed and dev-only (STAPEL_LOCAL_ENV=1) — it may
        and should carry the local origin, since deploy/check-env.sh refuses
        to ship it anywhere."""
        proj = _create(tmp_path, "app", "monolith")
        env = _read_env(proj / ".env.local")
        assert env["STAPEL_LOCAL_ENV"] == "1"
        assert env["FRONTEND_URL"] == "http://localhost"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
