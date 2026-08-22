"""Identity trust is DECLARED by the scaffold, not inherited from a library default.

The incident (app.ironmemo.com, 2026-08-15..16, task #349). stapel-core 0.24
flipped two defaults in a MINOR release, and every product that had never
stated them silently changed mode:

* ``JWT_CREATE_USERS_FROM_TOKEN`` True -> False. Seven of eight services began
  answering 401 to every user who signed up after the bump — a valid token
  naming a user_id the local database had never seen was refused as stale
  instead of materialising the shadow row. Visible only as "JWT Auth Failed -
  User creation failed", once per request.
* ``SECURE_PROXY_SSL_HEADER`` unconditional -> opt-in via
  ``STAPEL_TRUST_PROXY_SSL_HEADER``. Behind the https reverse proxy
  ``request.build_absolute_uri()`` started composing ``http://``, so the OAuth
  ``redirect_uri`` stopped matching the one registered with Google and GitHub
  and both providers refused the handshake.

Neither is a hardening bug in the library — both new defaults are the safer
ones. The defect is that the SERVICE never said which mode it was in, so a
version bump was allowed to answer for it. This module pins the scaffold's half
of the fix: every generated service states its identity-trust mode with a value
chosen from its ROLE, and every generated deploy env states what it trusts
about the proxy in front of it.

``CFG007`` (tests below) is the fleet-wide half — a service that mounts Stapel
JWT authentication and leaves the setting unstated is an error, everywhere,
not just in the one product where it was found by hand.
"""
import re
import textwrap
from pathlib import Path

from stapel_tools.config_lint import lint_project
from stapel_tools.create_project import create_project

DECL = re.compile(r"^JWT_CREATE_USERS_FROM_TOKEN\s*=\s*(True|False)\s*$", re.M)


def _create(tmp_path, name, project_type, modules):
    create_project(
        name=name,
        project_type=project_type,
        title=name,
        url="https://x.dev",
        company_name="X",
        company_email="x@x.dev",
        modules=modules,
        output_dir=tmp_path,
        use_submodules=False,
        init_git=False,
    )
    return tmp_path / name


def _declared_value(settings_file: Path) -> str:
    m = DECL.search(settings_file.read_text())
    assert m, (
        f"{settings_file} does not state JWT_CREATE_USERS_FROM_TOKEN at all — "
        f"it inherits whichever way stapel-core happens to default"
    )
    return m.group(1)


def _env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


# ---------------------------------------------------------------------------
# 1. the generated settings state the mode, with the value their ROLE implies
# ---------------------------------------------------------------------------


class TestGeneratedSettingsStateTheMode:
    def test_monolith_consumer_declares_true(self, tmp_path):
        """No stapel_auth installed: this service does not mint tokens and has
        no user table of its own authority — it consumes a neighbouring auth
        service's tokens, so an unknown subject must materialise locally."""
        proj = _create(tmp_path, "mono", "monolith", ["core"])
        base = proj / "svc-mono" / "config" / "settings" / "base.py"
        assert _declared_value(base) == "True"

    def test_monolith_issuer_declares_false(self, tmp_path):
        """stapel_auth installed: this service IS the issuer. An unknown
        subject in a token it signed itself is a stale token, and creating the
        row from it would forge an account."""
        proj = _create(tmp_path, "monoauth", "monolith", ["core", "auth"])
        base = proj / "svc-monoauth" / "config" / "settings" / "base.py"
        assert _declared_value(base) == "False"

    def test_microservices_services_declare_their_role(self, tmp_path):
        """The microservices preset ships the stack, not the services — they
        arrive through stapel-new-service. Both roles exist here at once, which
        is exactly the topology the setting is about."""
        from stapel_tools.new_service import scaffold_service

        proj = _create(tmp_path, "micro", "microservices", ["core"])
        scaffold_service(
            slug="identity", title="Identity", project_root=proj, prefix="svc-",
            stapel_apps=["stapel_auth"],
        )
        scaffold_service(slug="orders", title="Orders", project_root=proj, prefix="svc-")
        settings_of = lambda d: proj / d / "config" / "settings" / "base.py"  # noqa: E731
        assert _declared_value(settings_of("svc-identity")) == "False"
        assert _declared_value(settings_of("svc-orders")) == "True"

    def test_minimal_consumer_declares_true(self, tmp_path):
        proj = _create(tmp_path, "mini", "minimal", ["core"])
        assert _declared_value(proj / "config" / "settings.py") == "True"

    def test_minimal_issuer_declares_false(self, tmp_path):
        proj = _create(tmp_path, "miniauth", "minimal", ["core", "auth"])
        assert _declared_value(proj / "config" / "settings.py") == "False"

    def test_the_comment_explains_both_roles(self, tmp_path):
        """A bare `= True` teaches nobody which way to flip it. The line has to
        arrive with both roles spelled out, or the next service copies the
        value instead of answering the question."""
        proj = _create(tmp_path, "mono2", "monolith", ["core"])
        text = (proj / "svc-mono2" / "config" / "settings" / "base.py").read_text()
        head = text[: text.index("JWT_CREATE_USERS_FROM_TOKEN =")]
        assert "issuer" in head.lower(), "the comment never names the ISSUER role"
        assert "consumer" in head.lower(), "the comment never names the CONSUMER role"


class TestNewServiceStatesItToo:
    def test_scaffolded_service_declares_the_mode(self, tmp_path):
        """`stapel-new-service` is the other door into a project; a service
        added through it must arrive with the same declaration."""
        from stapel_tools.new_service import scaffold_service

        proj = _create(tmp_path, "mono3", "monolith", ["core"])
        scaffold_service(slug="extra", title="Extra", project_root=proj, prefix="svc-")
        base = proj / "svc-extra" / "config" / "settings" / "base.py"
        assert _declared_value(base) == "True"

    def test_scaffolded_auth_service_declares_false(self, tmp_path):
        from stapel_tools.new_service import scaffold_service

        proj = _create(tmp_path, "mono4", "monolith", ["core"])
        scaffold_service(
            slug="ident", title="Ident", project_root=proj, prefix="svc-",
            stapel_apps=["stapel_auth"],
        )
        base = proj / "svc-ident" / "config" / "settings" / "base.py"
        assert _declared_value(base) == "False"


# ---------------------------------------------------------------------------
# 2. the deploy env states what it trusts about the proxy in front of it
# ---------------------------------------------------------------------------


class TestDeployEnvDeclaresTheProxy:
    def test_monolith_env_declares_trust_proxy(self, tmp_path):
        proj = _create(tmp_path, "mono5", "monolith", ["core"])
        for name in (".env", ".env.example"):
            env = _env(proj / name)
            assert env.get("STAPEL_TRUST_PROXY_SSL_HEADER") == "True", (
                f"{name} does not state STAPEL_TRUST_PROXY_SSL_HEADER — behind "
                f"an https proxy every absolute URI this stack builds "
                f"(OAuth redirect_uri included) silently reverts to http://"
            )

    def test_microservices_env_declares_trust_proxy(self, tmp_path):
        proj = _create(tmp_path, "micro2", "microservices", ["core"])
        assert _env(proj / ".env.example").get("STAPEL_TRUST_PROXY_SSL_HEADER") == "True"

    def test_the_env_comment_states_the_precondition(self, tmp_path):
        """"Set it to True" is not the rule — "only when the single way in is a
        proxy that overwrites the header itself" is. Without that sentence the
        knob is an invitation to let a client declare its own protocol."""
        proj = _create(tmp_path, "mono6", "monolith", ["core"])
        text = (proj / ".env.example").read_text()
        head = text[: text.index("STAPEL_TRUST_PROXY_SSL_HEADER=")]
        assert "overwrite" in head.lower()
        assert "forge" in head.lower() or "spoof" in head.lower()

    def test_oauth_callback_base_url_is_declared_where_oauth_is(self, tmp_path):
        """A contract with a third party cannot be derived from a request
        header: Google/GitHub compare redirect_uri against what was registered,
        byte for byte. Wherever the env template offers OAuth credentials it
        must also offer the base the callback is built on."""
        proj = _create(tmp_path, "monoauth2", "monolith", ["core", "auth"])
        for name in (".env", ".env.example"):
            text = (proj / name).read_text()
            assert "OAUTH_CALLBACK_BASE_URL=" in text, (
                f"{name} offers OAuth credentials but no OAUTH_CALLBACK_BASE_URL"
            )

    def test_oauth_callback_base_url_actually_reaches_settings(self, tmp_path):
        """An env row nothing reads is decoration. stapel_auth resolves
        settings.OAUTH_CALLBACK_BASE_URL, so the generated settings must carry
        the read."""
        proj = _create(tmp_path, "monoauth3", "monolith", ["core", "auth"])
        base = (proj / "svc-monoauth3" / "config" / "settings" / "base.py").read_text()
        assert "OAUTH_CALLBACK_BASE_URL" in base

    def test_prod_settings_no_longer_claims_the_header_is_trusted(self, tmp_path):
        """The prod template used to say SECURE_PROXY_SSL_HEADER "already
        trusts X-Forwarded-Proto (set in the common library settings)". Since
        core 0.24 that is false, and a stale comment asserting a security
        property is worse than no comment."""
        proj = _create(tmp_path, "mono7", "monolith", ["core"])
        prod = (proj / "svc-mono7" / "config" / "settings" / "prod.py").read_text()
        assert "already trusts" not in prod


class TestCookieTransportIsStatedInEveryTier:
    """The same class of defect as the two in the docstring, found by the
    sweep this task asked for: ``SESSION_COOKIE_SECURE`` / ``CSRF_COOKIE_SECURE``
    / ``JWT_COOKIE_SECURE`` were stated ONLY in the prod tier. Every other tier
    inherited the library value — which the newer stapel-core flips to True.
    A Secure cookie on a plain-HTTP non-localhost origin (``dev.<slug>.local``,
    a LAN IP, an http staging host) is silently dropped by the browser: the
    admin login form 403s on a CSRF cookie that was never stored, and a
    successful JWT login is followed by an anonymous next request.
    """

    COOKIES = ("SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE", "JWT_COOKIE_SECURE")

    def test_dev_tier_states_all_three_false(self, tmp_path):
        proj = _create(tmp_path, "monoc", "monolith", ["core"])
        dev = (proj / "svc-monoc" / "config" / "settings" / "dev.py").read_text()
        for key in self.COOKIES:
            assert re.search(rf"^{key}\s*=\s*False\s*$", dev, re.M), key

    def test_prod_tier_still_states_all_three_true(self, tmp_path):
        proj = _create(tmp_path, "monod", "monolith", ["core"])
        prod = (proj / "svc-monod" / "config" / "settings" / "prod.py").read_text()
        for key in self.COOKIES:
            assert re.search(rf"^{key}\s*=\s*True\s*$", prod, re.M), key

    def test_minimal_states_them_in_both_branches(self, tmp_path):
        proj = _create(tmp_path, "minic", "minimal", ["core"])
        text = (proj / "config" / "settings.py").read_text()
        for key in self.COOKIES:
            assert re.search(rf"^\s+{key} = True$", text, re.M), f"{key} prod branch"
            assert re.search(rf"^\s+{key} = False$", text, re.M), f"{key} non-prod branch"


# ---------------------------------------------------------------------------
# 3. CFG007 — the fleet-wide gate, not one product's hand-written script
# ---------------------------------------------------------------------------


def _project(tmp_path, settings_body: str, *, name="proj", pkg="config") -> Path:
    root = tmp_path / name
    (root / pkg / "settings").mkdir(parents=True)
    (root / "manage.py").write_text("import os\n", encoding="utf-8")
    (root / pkg / "settings" / "base.py").write_text(
        textwrap.dedent(settings_body), encoding="utf-8"
    )
    return root


STAR_IMPORT = """
    from stapel_core.django.settings import *  # noqa

    SERVICE_NAME = "thing"
    INSTALLED_APPS = COMMON_INSTALLED_APPS + ["apps.thing"]
"""


def _codes(root: Path) -> list[str]:
    return [f.rule for f in lint_project(root)]


def _cfg007(root: Path):
    return [f for f in lint_project(root) if f.rule == "CFG007"]


class TestCfg007FiresWhereTheIncidentWas:
    def test_star_import_without_declaration_is_an_error(self, tmp_path):
        """The exact ironmemo shape: `from stapel_core.django.settings import *`
        pulls in REST_FRAMEWORK's DEFAULT_AUTHENTICATION_CLASSES =
        JWTCookieAuthentication. The service is on Stapel JWT auth without
        having typed a single JWT line — and answered for nothing."""
        root = _project(tmp_path, STAR_IMPORT)
        found = _cfg007(root)
        assert len(found) == 1, _codes(root)
        assert found[0].level == "error"

    def test_declaring_it_clears_the_finding(self, tmp_path):
        root = _project(tmp_path, STAR_IMPORT + "\nJWT_CREATE_USERS_FROM_TOKEN = True\n")
        assert _cfg007(root) == []

    def test_false_clears_it_too(self, tmp_path):
        """The rule is about the ANSWER existing, not about which answer."""
        root = _project(tmp_path, STAR_IMPORT + "\nJWT_CREATE_USERS_FROM_TOKEN = False\n")
        assert _cfg007(root) == []

    def test_declaration_may_live_in_a_sibling_settings_module(self, tmp_path):
        """Settings are a package; a project that answers in prod.py has
        answered."""
        root = _project(tmp_path, STAR_IMPORT)
        (root / "config" / "settings" / "prod.py").write_text(
            "from .base import *  # noqa\n\nJWT_CREATE_USERS_FROM_TOKEN = False\n",
            encoding="utf-8",
        )
        assert _cfg007(root) == []

    def test_installing_stapel_auth_is_a_mount_too(self, tmp_path):
        """A service can mount Stapel identity without star-importing core's
        settings — installing the auth module is the same question."""
        root = _project(
            tmp_path,
            """
            INSTALLED_APPS = [
                "django.contrib.admin",
                "stapel_auth",
                "apps.thing",
            ]
            """,
        )
        assert len(_cfg007(root)) == 1

    def test_naming_the_authentication_class_is_a_mount_too(self, tmp_path):
        root = _project(
            tmp_path,
            """
            REST_FRAMEWORK = {
                "DEFAULT_AUTHENTICATION_CLASSES": [
                    "stapel_core.django.jwt.authentication.JWTCookieAuthentication",
                ],
            }
            """,
        )
        assert len(_cfg007(root)) == 1

    def test_a_project_with_no_stapel_identity_is_silent(self, tmp_path):
        """The rule must not fire on a project that mounts no Stapel JWT auth
        at all — a knob nobody reads is not a missing declaration."""
        root = _project(
            tmp_path,
            """
            INSTALLED_APPS = ["django.contrib.admin"]
            REST_FRAMEWORK = {"DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema"}
            """,
        )
        assert _cfg007(root) == []

    def test_a_named_import_from_core_settings_is_not_a_mount(self, tmp_path):
        """`from stapel_core.django.settings import get_common_templates` (the
        minimal preset) borrows one helper — it does not inherit the
        REST_FRAMEWORK block, so it is not evidence of a JWT mount."""
        root = _project(
            tmp_path,
            """
            from stapel_core.django.settings import get_common_templates

            INSTALLED_APPS = ["django.contrib.admin"]
            TEMPLATES = get_common_templates(".")
            """,
        )
        assert _cfg007(root) == []

    def test_the_message_teaches_both_roles(self, tmp_path):
        """Whoever hits this has to be able to pick a value from the message
        alone — that is the difference between a gate and an obstacle."""
        root = _project(tmp_path, STAR_IMPORT)
        msg = _cfg007(root)[0].message
        assert "JWT_CREATE_USERS_FROM_TOKEN" in msg
        assert "issuer" in msg.lower() and "consumer" in msg.lower()
        assert "False" in msg and "True" in msg

    def test_it_is_reported_at_the_settings_file(self, tmp_path):
        root = _project(tmp_path, STAR_IMPORT)
        found = _cfg007(root)[0]
        assert found.path.endswith("base.py")
        assert found.line > 0

    def test_one_finding_per_project_not_per_settings_module(self, tmp_path):
        """dev.py/prod.py/test.py all re-export base; reporting the same
        unanswered question four times trains people to skim the output."""
        root = _project(tmp_path, STAR_IMPORT)
        for tier in ("dev", "prod", "test"):
            (root / "config" / "settings" / f"{tier}.py").write_text(
                "from .base import *  # noqa\n", encoding="utf-8"
            )
        assert len(_cfg007(root)) == 1

    def test_a_library_checkout_is_not_a_service(self, tmp_path):
        """stapel-core itself star-imports nothing, but a lib whose own test
        settings do must not be told to pick a product's identity mode."""
        root = tmp_path / "stapel_thing"
        root.mkdir()
        (root / "pyproject.toml").write_text(
            '[project]\nname = "stapel-thing"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        (root / "settings.py").write_text(
            "from stapel_core.django.settings import *  # noqa\n", encoding="utf-8"
        )
        assert _cfg007(root) == []

    def test_suppressible_on_the_mount_line(self, tmp_path):
        root = _project(
            tmp_path,
            """
            from stapel_core.django.settings import *  # noqa: CFG007
            """,
        )
        assert _cfg007(root) == []


class TestGeneratedProjectsPassTheirOwnGate:
    def test_monolith_is_cfg007_clean(self, tmp_path):
        proj = _create(tmp_path, "mono8", "monolith", ["core"])
        assert _cfg007(proj) == []

    def test_monolith_with_auth_is_cfg007_clean(self, tmp_path):
        proj = _create(tmp_path, "mono9", "monolith", ["core", "auth"])
        assert _cfg007(proj) == []

    def test_minimal_is_cfg007_clean(self, tmp_path):
        proj = _create(tmp_path, "mini2", "minimal", ["core"])
        assert _cfg007(proj) == []

    def test_microservices_is_cfg007_clean(self, tmp_path):
        proj = _create(tmp_path, "micro3", "microservices", ["core"])
        assert _cfg007(proj) == []


class TestCfg007ShipsInTheVerifyGate:
    def test_config_lint_is_documented_with_the_new_rule(self):
        from stapel_tools import config_lint

        assert "CFG007" in (config_lint.__doc__ or "")

    def test_verify_runs_config_lint_and_would_surface_it(self, tmp_path):
        """stapel-verify composes config_lint, so a project picks CFG007 up on
        its next stapel-tools upgrade with nothing to regenerate."""
        from stapel_tools.verify import verify_project

        root = _project(tmp_path, STAR_IMPORT)
        reports = {r.name: r for r in verify_project(root)}
        rules = [f["rule"] for f in reports["stapel-config-lint"].findings]
        assert "CFG007" in rules


class TestCfg007AnswersPerSettingsPackage:
    """Review findings on the first cut (2026-08-22): the rule answered for the
    whole tree and matched any 'stapel_auth' string. Both reproduced; both
    pinned here."""

    def test_one_service_cannot_answer_for_its_neighbour(self, tmp_path):
        """Microservices checkout, `stapel-verify .` at the repo root: svc-a
        states the setting, svc-b star-imports and does not. The finding must
        land on svc-b — a tree-wide 'someone said it' is the false negative the
        incident would have hidden behind."""
        root = tmp_path / "fleet"
        for svc, body in (
            ("svc-a", STAR_IMPORT + "\nJWT_CREATE_USERS_FROM_TOKEN = False\n"),
            ("svc-b", STAR_IMPORT),
        ):
            (root / svc / "config" / "settings").mkdir(parents=True)
            (root / svc / "manage.py").write_text("import os\n", encoding="utf-8")
            (root / svc / "config" / "settings" / "base.py").write_text(
                textwrap.dedent(body), encoding="utf-8"
            )
        found = _cfg007(root)
        assert [Path(f.path).parts[-4] for f in found] == ["svc-b"], _codes(root)

    def test_a_logger_named_stapel_auth_is_not_a_mount(self, tmp_path):
        """Only INSTALLED_APPS decides what is installed. A LOGGING block that
        names the library's logger mounts nothing."""
        root = _project(tmp_path, """
            LOGGING = {"loggers": {"stapel_auth": {"level": "INFO"}}}
            REST_FRAMEWORK = {"DEFAULT_SCHEMA_CLASS": "x"}
        """)
        assert _cfg007(root) == []

    def test_installed_app_is_a_mount(self, tmp_path):
        root = _project(tmp_path, """
            INSTALLED_APPS = ["django.contrib.auth", "stapel_auth"]
        """)
        assert len(_cfg007(root)) == 1

    def test_jwt_class_outside_rest_framework_is_not_a_mount(self, tmp_path):
        """A docstring or a comment-like constant naming the class path is not
        a wiring; REST_FRAMEWORK is."""
        root = _project(tmp_path, """
            NOTE = "stapel_core.django.jwt.JWTCookieAuthentication is what prod uses"
        """)
        assert _cfg007(root) == []
        root2 = _project(tmp_path, """
            REST_FRAMEWORK = {"DEFAULT_AUTHENTICATION_CLASSES": [
                "stapel_core.django.jwt.JWTCookieAuthentication"]}
        """, name="proj2")
        assert len(_cfg007(root2)) == 1
