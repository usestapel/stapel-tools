"""Generator tests — per-primitive broker selection (--task-broker)."""
import json
import subprocess
import sys

import pytest

from stapel_tools.create_project import create_project
from stapel_tools.new_service import _detect_transports, scaffold_service


def _create(tmp_path, name, project_type, broker=None, task_broker=None, modules=None):
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
        broker=broker,
        task_broker=task_broker,
    )
    return tmp_path / name


class TestMonolithTaskBroker:
    def test_nats_task_broker_wires_compose_env_settings(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", task_broker="nats")

        compose = (proj / "docker-compose.base.yml").read_text()
        assert "  nats:" in compose
        assert "  nats-data:" in compose
        assert "kafka" not in compose

        env = (proj / ".env.example").read_text()
        assert "STAPEL_BUS_BACKEND=nats" in env
        assert "NATS_URL=nats://nats:4222" in env
        assert "STAPEL_TASK_DISPATCH=bus" in env
        assert "routing" not in env

        settings = (proj / "svc-app" / "config" / "settings" / "base.py").read_text()
        # Tasks go to the broker; Actions stay in-process.
        assert '"TASK_DISPATCH": os.getenv("STAPEL_TASK_DISPATCH", "bus")' in settings
        assert '"ACTION_TRANSPORT": os.getenv("STAPEL_ACTION_TRANSPORT", "inprocess")' in settings
        assert '"FUNCTION_TRANSPORT": os.getenv("STAPEL_FUNCTION_TRANSPORT", "inprocess")' in settings

    def test_default_monolith_unchanged(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")

        compose = (proj / "docker-compose.base.yml").read_text()
        assert "nats" not in compose
        assert "kafka" not in compose

        env = (proj / ".env.example").read_text()
        assert "STAPEL_BUS_BACKEND" not in env
        assert "STAPEL_TASK_DISPATCH" not in env

        settings = (proj / "svc-app" / "config" / "settings" / "base.py").read_text()
        assert '"TASK_DISPATCH": os.getenv("STAPEL_TASK_DISPATCH", "action")' in settings

    def test_task_broker_same_as_event_broker_needs_no_routing(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", broker="nats", task_broker="nats")

        compose = (proj / "docker-compose.base.yml").read_text()
        assert compose.count("  nats:") == 1

        env = (proj / ".env.example").read_text()
        assert "STAPEL_BUS_BACKEND=nats" in env
        assert "STAPEL_TASK_DISPATCH" not in env
        assert "routing" not in env

        settings = (proj / "svc-app" / "config" / "settings" / "base.py").read_text()
        # Tasks already ride the bus via the Action transport.
        assert '"TASK_DISPATCH": os.getenv("STAPEL_TASK_DISPATCH", "action")' in settings
        assert '"ACTION_TRANSPORT": os.getenv("STAPEL_ACTION_TRANSPORT", "bus")' in settings

    def test_later_services_inherit_task_dispatch_from_env(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", task_broker="nats")
        assert _detect_transports(proj) == ("inprocess", "inprocess", "bus")

        scaffold_service(
            slug="worker", title="Worker", prefix="svc-", project_root=proj
        )
        settings = (proj / "svc-worker" / "config" / "settings" / "base.py").read_text()
        assert '"TASK_DISPATCH": os.getenv("STAPEL_TASK_DISPATCH", "bus")' in settings
        assert '"ACTION_TRANSPORT": os.getenv("STAPEL_ACTION_TRANSPORT", "inprocess")' in settings


class TestMicroservicesTaskBroker:
    def test_kafka_task_broker_adds_both_brokers_and_routing(self, tmp_path):
        proj = _create(tmp_path, "app", "microservices", task_broker="kafka")

        compose = (proj / "docker-compose.base.yml").read_text()
        assert "  nats:" in compose
        assert "  kafka:" in compose
        assert "  nats-data:" in compose
        assert "  kafka-data:" in compose

        env = (proj / ".env.example").read_text()
        assert "STAPEL_BUS_BACKEND=routing" in env
        assert 'STAPEL_BUS_ROUTES={"task.": "kafka", "": "nats"}' in env
        assert "NATS_URL=nats://nats:4222" in env
        assert "KAFKA_BOOTSTRAP_SERVERS=kafka:9092" in env

    def test_compose_yaml_stays_valid(self, tmp_path):
        yaml = pytest.importorskip("yaml")
        proj = _create(tmp_path, "app", "microservices", task_broker="kafka")
        data = yaml.safe_load((proj / "docker-compose.base.yml").read_text())
        assert {"nats", "kafka"} <= set(data["services"])
        assert {"nats-data", "kafka-data"} <= set(data["volumes"])

    def test_default_micro_unchanged(self, tmp_path):
        proj = _create(tmp_path, "app", "microservices")
        env = (proj / ".env.example").read_text()
        assert "STAPEL_BUS_BACKEND=nats" in env
        assert "routing" not in env
        assert "kafka" not in (proj / "docker-compose.base.yml").read_text()

    def test_task_broker_same_as_event_broker_needs_no_routing(self, tmp_path):
        proj = _create(
            tmp_path, "app", "microservices", broker="kafka", task_broker="kafka"
        )
        env = (proj / ".env.example").read_text()
        assert "STAPEL_BUS_BACKEND=kafka" in env
        assert "routing" not in env

    def test_later_services_detect_routing_backend(self, tmp_path):
        proj = _create(tmp_path, "app", "microservices", task_broker="kafka")
        # Actions ride the bus; Functions follow the default ("") route.
        assert _detect_transports(proj) == ("bus", "nats", "action")


class TestModuleWiring:
    """G10: a --modules choice must land in requirements AND be mounted in
    INSTALLED_APPS AND urls — a dependency that is installed but never added
    to INSTALLED_APPS is dead weight."""

    def test_minimal_wires_module_everywhere(self, tmp_path):
        proj = _create(tmp_path, "app", "minimal", modules=["core", "auth"])

        reqs = (proj / "requirements.txt").read_text()
        assert "stapel_auth @ git+" in reqs

        settings = (proj / "config" / "settings.py").read_text()
        assert '"stapel_auth",' in settings

        urls = (proj / "config" / "urls.py").read_text()
        assert 'include("stapel_auth.urls")' in urls

    def test_minimal_wires_multiple_modules(self, tmp_path):
        proj = _create(tmp_path, "app", "minimal", modules=["core", "auth", "billing"])

        reqs = (proj / "requirements.txt").read_text()
        settings = (proj / "config" / "settings.py").read_text()
        urls = (proj / "config" / "urls.py").read_text()
        for app in ("stapel_auth", "stapel_billing"):
            assert f"{app} @ git+" in reqs
            assert f'"{app}",' in settings
            assert f'include("{app}.urls")' in urls

    def test_minimal_no_modules_leaves_only_local_app(self, tmp_path):
        proj = _create(tmp_path, "app", "minimal", modules=["core"])
        # stapel_core (outbox app) is always present; no FEATURE module
        # (stapel_auth, stapel_billing, ...) may sneak in unrequested.
        settings = (proj / "config" / "settings.py").read_text()
        urls = (proj / "config" / "urls.py").read_text()
        for key in ("auth", "billing", "cdn", "notifications", "profiles",
                    "translate", "workspaces", "gdpr"):
            assert f"stapel_{key}" not in settings
            assert f"stapel_{key}" not in urls

    def test_monolith_wires_module_everywhere(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", modules=["core", "auth"])

        settings = (proj / "svc-app" / "config" / "settings" / "base.py").read_text()
        assert '"stapel_auth",' in settings

        urls = (proj / "svc-app" / "config" / "urls.py").read_text()
        assert 'include("stapel_auth.urls")' in urls


class TestGeneratedRequirementPins:
    """G11: generated minimal requirements pin the Django line the stapel
    suites are actually validated on — Django 6.x (workspace venv and the
    source codebases run 6; 5.x is untested) — so a fresh project cannot
    ride an incompatible or untested Django/DRF (version skew)."""

    def test_minimal_pins_django_and_drf_ranges(self, tmp_path):
        proj = _create(tmp_path, "app", "minimal", modules=["core"])
        reqs = (proj / "requirements.txt").read_text()
        assert "django>=6,<7" in reqs
        assert "djangorestframework>=3.14" in reqs
        # No stale floor pointing at the untested 4.x/5.x lines.
        assert "django>=4.2" not in reqs
        assert "django>=5" not in reqs


class TestOutboxHarness:
    """G7: every generated project ships the transactional-outbox + file-mailtrap
    integration harness (system-design §7.12.3 / §7.21) so no coder builds the
    pattern from scratch."""

    # Shared harness files, relative to the dir that owns the top-level tests
    # package (project root for minimal, the service dir for a service).
    HARNESS_FILES = [
        "tests/__init__.py",
        "tests/harness/__init__.py",
        "tests/harness/outbox.py",
        "tests/harness/wait.py",
        "tests/harness/mailtrap.py",
        "tests/test_outbox_harness_example.py",
        "var/mailtrap/.gitkeep",
    ]

    def test_minimal_generates_harness_files(self, tmp_path):
        proj = _create(tmp_path, "shop", "minimal", modules=["core"])
        # Minimal keeps its conftest inside tests/.
        for rel in [*self.HARNESS_FILES, "tests/conftest.py", "Makefile",
                    "pyproject.toml"]:
            assert (proj / rel).exists(), rel

        settings = (proj / "config" / "settings.py").read_text()
        assert "stapel_core.django.outbox" in settings
        assert '"OUTBOX_ENABLED": True' in settings
        assert "tests.harness.mailtrap.FileMailtrapBackend" in settings

    def test_monolith_service_generates_harness_files(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", modules=["core"])
        svc = proj / "svc-app"
        # Service conftest lives at the service root and carries the fixtures.
        for rel in [*self.HARNESS_FILES, "conftest.py"]:
            assert (svc / rel).exists(), rel
        assert "drain_outbox" in (svc / "conftest.py").read_text()
        assert "tests" in (svc / "pytest.ini").read_text().split("testpaths")[1]
        dev = (svc / "config" / "settings" / "dev.py").read_text()
        assert "FileMailtrapBackend" in dev

    def test_minimal_harness_example_test_passes(self, tmp_path):
        """The shipped example test proves the harness wiring end to end
        (producer -> outbox row, drain -> effect, rollback -> no row, mailtrap)
        in a scratch generation run with the current interpreter."""
        proj = _create(tmp_path, "shop", "minimal", modules=["core"])
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "tests/test_outbox_harness_example.py", "-q"],
            cwd=proj, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr


class TestMountConventions:
    """Follow-up to the arch-monolith-mounting P0: a scaffolded service must
    never hardcode a root-relative admin URL, and its "is there a dedicated
    auth service" setting must be spelled the way stapel_core.django.mounts /
    AdminLoginRedirectMiddleware actually read it (STAPEL_AUTH_SERVICE_PREFIX)
    — a name drift here silently breaks centralized admin login wiring in
    every generated service."""

    def test_monolith_login_redirect_is_a_url_name(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        settings = (proj / "svc-app" / "config" / "settings" / "base.py").read_text()
        assert 'LOGIN_REDIRECT_URL = "admin:index"' in settings
        assert "/admin/" not in settings.split("LOGIN_REDIRECT_URL")[1].split("\n")[0]

    def test_monolith_auth_prefix_setting_matches_core_convention(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        settings = (proj / "svc-app" / "config" / "settings" / "base.py").read_text()
        urls = (proj / "svc-app" / "config" / "urls.py").read_text()
        assert 'STAPEL_AUTH_SERVICE_PREFIX = os.getenv("STAPEL_AUTH_SERVICE_PREFIX", "")' in settings
        assert 'getattr(settings, "STAPEL_AUTH_SERVICE_PREFIX", "")' in urls
        # No stale un-prefixed name left over anywhere in the generated service.
        assert "settings, \"AUTH_SERVICE_PREFIX\"" not in urls
        assert '"AUTH_SERVICE_PREFIX"' not in settings.replace(
            "STAPEL_AUTH_SERVICE_PREFIX", ""
        )

    def test_microservices_scaffolded_service_same_conventions(self, tmp_path):
        # "microservices" only lays down the shared base; individual services
        # are added with scaffold_service() (stapel-new-service), same as a
        # monolith adding a second service — both paths render BASE_SETTINGS.
        proj = _create(tmp_path, "app", "microservices")
        scaffold_service(slug="auth", title="Auth", prefix="svc-", project_root=proj)

        settings = (proj / "svc-auth" / "config" / "settings" / "base.py").read_text()
        urls = (proj / "svc-auth" / "config" / "urls.py").read_text()
        assert 'LOGIN_REDIRECT_URL = "admin:index"' in settings
        assert 'STAPEL_AUTH_SERVICE_PREFIX = os.getenv("STAPEL_AUTH_SERVICE_PREFIX", "")' in settings
        assert 'getattr(settings, "STAPEL_AUTH_SERVICE_PREFIX", "")' in urls


class TestStapelServicesEnv:
    """admin-suite AS-4: the service-navigation registry is a deploy-config
    env-JSON, seeded by create-project and appended by new-service — not a
    framework hardcode."""

    def test_monolith_seeds_single_service(self, tmp_path):
        proj = _create(tmp_path, "shop", "monolith")
        env = (proj / ".env.example").read_text()
        line = next(
            ln for ln in env.splitlines() if ln.startswith("STAPEL_SERVICES=")
        )
        services = json.loads(line.split("=", 1)[1])
        # A monolith is one service; the generated URL_PREFIX is the slug, so
        # the seed carries that prefix (scaffold_service's append is a no-op —
        # idempotent on the already-present prefix).
        assert services == [{"name": "shop", "prefix": "shop"}]

    def test_microservices_seeds_empty_then_new_service_appends(self, tmp_path):
        proj = _create(tmp_path, "app", "microservices")
        env = (proj / ".env.example").read_text()
        assert "STAPEL_SERVICES=[]" in env

        scaffold_service(slug="auth", title="Auth", prefix="svc-", project_root=proj)
        scaffold_service(
            slug="billing", title="Billing", prefix="svc-", project_root=proj
        )
        line = next(
            ln for ln in (proj / ".env.example").read_text().splitlines()
            if ln.startswith("STAPEL_SERVICES=")
        )
        services = json.loads(line.split("=", 1)[1])
        assert services == [
            {"name": "Auth", "prefix": "auth"},
            {"name": "Billing", "prefix": "billing"},
        ]

    def test_new_service_is_idempotent(self, tmp_path):
        proj = _create(tmp_path, "app", "microservices")
        scaffold_service(slug="auth", title="Auth", prefix="svc-", project_root=proj)
        # Re-registering the same prefix must not duplicate the row.
        from stapel_tools.new_service import _update_stapel_services

        _update_stapel_services(proj, "auth", "Auth")
        line = next(
            ln for ln in (proj / ".env.example").read_text().splitlines()
            if ln.startswith("STAPEL_SERVICES=")
        )
        assert json.loads(line.split("=", 1)[1]) == [
            {"name": "Auth", "prefix": "auth"}
        ]


class TestAppLabelCollision:
    """Defect 3: a service/module named after a hosted Stapel app must not take
    the bare app label (django.contrib.auth is label='auth', stapel_profiles is
    label='profiles'). The scaffold gives its own app an explicit, collision-proof
    `<module>_local` label so `django.setup()` does not raise ImproperlyConfigured
    ('Application labels aren't unique')."""

    def test_service_app_gets_explicit_local_label(self, tmp_path):
        proj = _create(tmp_path, "app", "microservices")
        scaffold_service(slug="auth", title="Auth", prefix="svc-", project_root=proj)
        apps_py = (proj / "svc-auth" / "apps" / "auth" / "apps.py").read_text()
        # dotted path under the apps/ package, and a namespaced app LABEL
        assert 'name = "apps.auth"' in apps_py
        assert 'label = "auth_local"' in apps_py

    def test_service_named_profiles_avoids_stapel_profiles_label(self, tmp_path):
        proj = _create(tmp_path, "app", "microservices")
        scaffold_service(slug="profiles", title="Profiles", prefix="svc-", project_root=proj)
        apps_py = (proj / "svc-profiles" / "apps" / "profiles" / "apps.py").read_text()
        assert 'label = "profiles_local"' in apps_py

    def test_scaffolded_module_gets_explicit_local_label(self, tmp_path):
        from stapel_tools.new_module import scaffold_module

        svc = tmp_path / "svc-auth"
        (svc / "apps").mkdir(parents=True)
        (svc / "manage.py").write_text("# manage\n")
        scaffold_module("profiles", "Profiles", svc)
        apps_py = (svc / "apps" / "profiles" / "apps.py").read_text()
        # module keeps its dotted import path but the label is collision-proof
        assert 'name = "apps.profiles"' in apps_py
        assert 'label = "profiles_local"' in apps_py


class TestModuleConfig:
    """A5 (capability-config.md §4 p.1): create_project/scaffold_service render
    STAPEL_<MOD> = {...} blocks from module_config — only the provided
    (non-default) keys, with a comment pointing at the module's
    docs/capabilities.json; no config → byte-for-byte the previous output."""

    AUTH_CONFIG = {
        "auth": {"AUTH_PASSWORD_LOGIN": True, "AUTH_EMAIL_REGISTRATION": False}
    }
    AUTH_BLOCK = (
        "# auth: non-default capability axes only — defaults live in "
        "stapel_auth/conf.py;\n"
        "# the full axis list is stapel-auth/docs/capabilities.json "
        "(emitted by `make contract`).\n"
        "STAPEL_AUTH = {\n"
        '    "AUTH_PASSWORD_LOGIN": True,\n'
        '    "AUTH_EMAIL_REGISTRATION": False,\n'
        "}\n"
    )

    def _create(self, tmp_path, name, project_type, module_config, modules=None):
        create_project(
            name=name,
            project_type=project_type,
            title=name,
            url="https://x.dev",
            company_name="X",
            company_email="x@x.dev",
            modules=modules or ["core", "auth"],
            output_dir=tmp_path,
            use_submodules=False,
            init_git=False,
            module_config=module_config,
        )
        return tmp_path / name

    def test_minimal_renders_exact_block(self, tmp_path):
        proj = self._create(tmp_path, "app", "minimal", self.AUTH_CONFIG)
        settings = (proj / "config" / "settings.py").read_text()
        assert self.AUTH_BLOCK in settings
        # exactly one block, only the provided keys
        assert settings.count("STAPEL_AUTH") == 1
        assert "AUTH_PHONE_LOGIN" not in settings
        assert "{{STAPEL_MODULE_CONFIG}}" not in settings

    def test_monolith_renders_block_in_service_settings(self, tmp_path):
        proj = self._create(tmp_path, "app", "monolith", self.AUTH_CONFIG)
        settings = (proj / "svc-app" / "config" / "settings" / "base.py").read_text()
        assert self.AUTH_BLOCK in settings

    def test_multiple_modules_sorted_with_per_module_comments(self, tmp_path):
        config = {
            "gdpr": {"GDPR_EXPORT_ENABLED": True},
            "auth": {"AUTH_PASSWORD_LOGIN": True},
        }
        proj = self._create(
            tmp_path, "app", "minimal", config, modules=["core", "auth", "gdpr"]
        )
        settings = (proj / "config" / "settings.py").read_text()
        assert "STAPEL_AUTH = {" in settings
        assert "STAPEL_GDPR = {" in settings
        assert settings.index("STAPEL_AUTH") < settings.index("STAPEL_GDPR")
        assert "stapel-gdpr/docs/capabilities.json" in settings

    def test_no_config_is_byte_identical(self, tmp_path):
        """The seam is inert: module_config=None and module_config={} produce
        byte-for-byte the tree the scaffolder produced before A5."""
        a = self._create(tmp_path / "a", "app", "minimal", None)
        b = self._create(tmp_path / "b", "app", "minimal", {})

        def tree(root):
            return {
                p.relative_to(root): p.read_bytes()
                for p in sorted(root.rglob("*"))
                if p.is_file() and ".env" not in p.name  # .env carries secrets
            }

        assert tree(a) == tree(b)
        settings = (a / "config" / "settings.py").read_text()
        assert "STAPEL_AUTH" not in settings
        assert "{{STAPEL_MODULE_CONFIG}}" not in settings

    def test_config_for_unselected_module_is_hard_error(self, tmp_path):
        import pytest

        with pytest.raises(SystemExit, match="billing"):
            self._create(
                tmp_path, "app", "minimal",
                {"billing": {"BILLING_PROVIDER": "stripe"}},
                modules=["core", "auth"],
            )

    def test_microservices_rejects_module_config(self, tmp_path):
        import pytest

        with pytest.raises(SystemExit):
            self._create(tmp_path, "app", "microservices", self.AUTH_CONFIG)

    def test_minimal_project_checks_still_pass_with_config(self, tmp_path):
        """The generated settings module stays valid Python with a config block
        present. (Booting Django with stapel_auth mounted needs the module's
        full env — user model swap, JWT config — out of scope for tools CI;
        the core-only harness run in TestOutboxHarness covers the executable
        path this suite normally verifies.)"""
        proj = self._create(tmp_path, "shop", "minimal", self.AUTH_CONFIG)
        compile((proj / "config" / "settings.py").read_text(), "settings.py", "exec")
        assert self.AUTH_BLOCK in (proj / "config" / "settings.py").read_text()


class TestModuleConfigValidation:
    """The validation seam: sibling docs/capabilities.json (when present) is
    the axes+extension key surface; unknown key = hard error with the known
    keys; absent artifact = warn and pass through."""

    def _workspace(self, tmp_path, axes=("AUTH_PASSWORD_LOGIN",), extensions=()):
        docs = tmp_path / "stapel-auth" / "docs"
        docs.mkdir(parents=True)
        (docs / "capabilities.json").write_text(json.dumps({
            "module": "stapel-auth",
            "axes": [{"key": k} for k in axes],
            "extension_points": [{"name": n} for n in extensions],
        }))
        return tmp_path

    def test_unknown_key_is_hard_error_with_known_keys(self, tmp_path):
        import pytest

        from stapel_tools._module_config import validate_module_config

        root = self._workspace(tmp_path, axes=("AUTH_PASSWORD_LOGIN",
                                               "AUTH_EMAIL_LOGIN"))
        with pytest.raises(SystemExit) as exc:
            validate_module_config(
                {"auth": {"AUTH_NO_SUCH_KEY": True}},
                selected=["auth"], workspace_root=root,
            )
        message = str(exc.value)
        assert "AUTH_NO_SUCH_KEY" in message
        assert "AUTH_PASSWORD_LOGIN" in message  # the known-keys list
        assert "AUTH_EMAIL_LOGIN" in message

    def test_extension_surface_keys_are_known(self, tmp_path):
        from stapel_tools._module_config import validate_module_config

        root = self._workspace(
            tmp_path, axes=("AUTH_PASSWORD_LOGIN",),
            extensions=("OAUTH_PROVIDER_CLASSES",),
        )
        validate_module_config(  # does not raise
            {"auth": {"OAUTH_PROVIDER_CLASSES": ["x.Y"]}},
            selected=["auth"], workspace_root=root,
        )

    def test_absent_capabilities_warns_and_passes(self, tmp_path, capsys):
        from stapel_tools._module_config import validate_module_config

        validate_module_config(  # module not swept yet — no artifact
            {"auth": {"AUTH_ANYTHING_GOES": 1}},
            selected=["auth"], workspace_root=tmp_path,
        )
        assert "Warning" in capsys.readouterr().err

    def test_real_sibling_auth_keys_validate(self):
        """In the workspace checkout the default root finds the real
        stapel-auth capabilities.json; the A5 example keys are real axes."""
        from stapel_tools._module_config import known_config_keys

        known = known_config_keys("auth")
        if known is None:  # module CI checks out only this repo
            import pytest

            pytest.skip("stapel-auth sibling not present")
        assert {"AUTH_PASSWORD_LOGIN", "AUTH_EMAIL_REGISTRATION"} <= known


class TestInvalidCombos:
    def test_minimal_rejects_task_broker(self, tmp_path):
        with pytest.raises(SystemExit):
            _create(tmp_path, "app", "minimal", task_broker="nats")

    def test_minimal_rejects_kafka_task_broker(self, tmp_path):
        with pytest.raises(SystemExit):
            _create(tmp_path, "app", "minimal", task_broker="kafka")

    def test_monolith_rejects_kafka_task_broker(self, tmp_path):
        with pytest.raises(SystemExit):
            _create(tmp_path, "app", "monolith", task_broker="kafka")

    def test_minimal_allows_explicit_none(self, tmp_path):
        proj = _create(tmp_path, "app", "minimal", task_broker="none")
        assert (proj / "manage.py").exists()
