"""§57 owner directive — dev/prod docker-compose + nginx canon, entrypoint
canon, AGENTS.md, pre-commit README canon. Covers monolith (the "recommended"
/ only-wired-up-so-far project type; microservices/minimal frontend wiring
is an explicit follow-up, not built here — see AGENTS_MD's FRONTEND_SECTION
only rendering for has_frontend=True)."""
import json
import re
import shutil
import subprocess

import pytest
import yaml

from stapel_tools.create_project import create_project

# stapel-gdpr declares DATA_OWNERS required (docs/capabilities.json
# required_settings) and generation is refused without it: an app installed
# with no data-owner inventory cannot pass its own boot check.
GDPR_CONFIG = {"gdpr": {
    "DATA_OWNERS": ["auth", "profiles"],
    "DATA_OWNERS_VERSION": "2026-01-01.1",
}}


def _create(tmp_path, name, project_type, modules=None, **kwargs):
    if "module_config" not in kwargs and modules and "gdpr" in modules:
        kwargs["module_config"] = GDPR_CONFIG
    create_project(
        name=name,
        project_type=project_type,
        title=name.capitalize(),
        url="https://x.dev",
        company_name="X",
        company_email="x@x.dev",
        modules=modules or ["core"],
        output_dir=tmp_path,
        use_submodules=False,
        init_git=False,
        **kwargs,
    )
    return tmp_path / name


def _docker_compose_config(project_dir, *files):
    """Validate via `docker compose config` when the Docker CLI is on PATH
    (directive: "compose is valid via docker compose config when docker is
    available — otherwise a YAML parse"); otherwise just parse every file
    as YAML."""
    if shutil.which("docker") is None:
        for f in files:
            yaml.safe_load((project_dir / f).read_text())
        return None
    args = ["docker", "compose"]
    for f in files:
        args += ["-f", str(project_dir / f)]
    args += ["--env-file", str(project_dir / ".env"), "config"]
    proc = subprocess.run(args, cwd=project_dir, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return yaml.safe_load(proc.stdout)


class TestMonolithDevProdComposeCanon:
    """Item 1: dev compose starts frontend (Vite) + backend (Django) + a
    local-nginx that routes the reserved backend namespace to Django and
    everything else to Vite; prod compose's nginx serves the built frontend
    + proxies api/admin/static/media to Django."""

    def test_dev_compose_starts_frontend_and_backend_and_is_valid(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        data = _docker_compose_config(proj, "docker-compose.local.yml")
        if data is None:
            return
        services = data["services"]
        assert "frontend" in services
        assert "svc-app" in services  # the backend actually got wired in
        assert "nginx" in services
        assert services["nginx"]["environment"]["BACKEND_UPSTREAM"] == "svc-app:8000"
        assert services["nginx"]["environment"]["FRONTEND_LOCAL_UPSTREAM"] == "frontend:5173"
        # Self-contained local stack (no include: — several compose versions
        # reject overriding an included service): nginx mounts the local
        # template dir at /etc/nginx/templates ONLY; conf.d stays writable
        # inside the container for the image's envsubst render step.
        mounts = {v["target"]: v["source"] for v in services["nginx"]["volumes"]}
        assert mounts["/etc/nginx/templates"] == str(proj / "service-configs" / "nginx-local")
        assert "/etc/nginx/conf.d" not in mounts
        # local stack never shares db state with a prod compose run
        assert "db-data-local" in data["volumes"]

    def test_prod_compose_builds_frontend_and_is_valid(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        data = _docker_compose_config(proj, "docker-compose.yml")
        if data is None:
            return
        services = data["services"]
        assert "frontend-build" in services
        assert services["frontend-build"]["restart"] == "no"
        assert "svc-app" in services
        # nginx itself comes from docker-compose.base.yml (included), not
        # this file's own `services:` — assert the frontend-dist volume it
        # mounts is declared.
        assert "frontend-dist" in data["volumes"]

    def test_backend_service_actually_gets_wired_into_compose_files(self, tmp_path):
        """Regression: `_update_compose_file`'s containment check used to
        false-positive against the commented example ("  # svc-app:") that
        ships in the monolith compose templates, silently leaving the
        backend never wired into docker-compose.yml/docker-compose.local.yml
        for a project's first/default service — found auditing this task."""
        proj = _create(tmp_path, "app", "monolith")
        dev = (proj / "docker-compose.local.yml").read_text()
        prod = (proj / "docker-compose.yml").read_text()
        for text in (dev, prod):
            assert "\n  svc-app:\n    extends:\n" in text

    def test_nginx_conf_reserves_static_media_and_serves_frontend(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        conf = (proj / "service-configs" / "nginx" / "nginx.conf").read_text()
        assert "location /staticfiles/" in conf
        assert "location /media/" in conf
        assert "root /usr/share/nginx/html" in conf
        assert "try_files $uri $uri/ /index.html" in conf
        # reserved-namespace prefixes come before the catch-all in the file
        # (readability only — nginx picks by prefix specificity, not order,
        # but keep the doc-order sane too).
        assert conf.index("/staticfiles/") < conf.index("location / {")
        assert conf.index("/media/") < conf.index("location / {")

    def test_nginx_port_safety_canon(self, tmp_path):
        """Owner nginx canon: Host forwarded as $http_host (keeps the port;
        $host strips it) + absolute_redirect off (nginx's own /admin ->
        /admin/ redirect otherwise bakes in the internal port 80 and drops
        the external mapping) — in BOTH generated server blocks."""
        proj = _create(tmp_path, "app", "monolith")
        prod = (proj / "service-configs" / "nginx" / "nginx.conf").read_text()
        local = (
            proj / "service-configs" / "nginx-local" / "default.conf.template"
        ).read_text()
        for conf in (prod, local):
            assert "absolute_redirect off;" in conf
            assert "proxy_set_header Host $host;" not in conf
        assert "proxy_set_header Host $http_host;" in local
        # per-service block appended into the prod conf by stapel-new-service
        assert "proxy_set_header Host $http_host;" in prod

    def test_nginx_dev_template_env_driven_proxy_targets(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        tmpl = (proj / "service-configs" / "nginx-local" / "default.conf.template").read_text()
        # env-driven, but through nginx VARIABLES (deferred resolution —
        # see TestGenerativeBackendPrefixes below)
        assert "set $stapel_backend http://${BACKEND_UPSTREAM};" in tmpl
        assert "set $stapel_frontend http://${FRONTEND_LOCAL_UPSTREAM};" in tmpl
        assert "location /app/ {" in tmpl  # the project's own slug, reserved
        assert "location /staticfiles/" in tmpl
        assert "location /media/" in tmpl
        # no hardcoded compose-network host:port baked into the conf itself
        assert "svc-app:8000" not in tmpl
        assert "frontend:5173" not in tmpl

    def test_only_a_single_bare_conf_file_in_nginx_dev_dir(self, tmp_path):
        """The nginx-local directory is mounted at /etc/nginx/templates and
        rendered into the container's own conf.d by the nginx image's
        envsubst step — the template MUST be named default.conf.template so
        the render OVERWRITES the image's shipped default site (any other
        name would leave two competing :80 server blocks), and no bare
        *.conf may sit beside it."""
        proj = _create(tmp_path, "app", "monolith")
        nginx_dev_dir = proj / "service-configs" / "nginx-local"
        conf_files = list(nginx_dev_dir.glob("*.conf"))
        assert conf_files == []
        assert (nginx_dev_dir / "default.conf.template").exists()


class TestFrontendScaffold:
    def test_frontend_dir_scaffolded_with_vite_react(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        frontend = proj / "frontend"
        for rel in (
            "package.json", "vite.config.ts", "index.html",
            "src/main.tsx", "src/App.tsx", "Dockerfile", ".gitignore",
            "stapel.theme.json",
        ):
            assert (frontend / rel).exists(), rel

        pkg = (frontend / "package.json").read_text()
        assert '"name": "app-frontend"' in pkg
        assert '"vite"' in pkg


class TestThemeJsonScaffold:
    """§68 P5 — the scaffold's `frontend/stapel.theme.json` is the neutral
    colour-role dictionary (not a design-system-specific vocabulary), and the
    generator is called via @stapel/tokens' own published `stapel-tokens`
    bin — never a vendored/forked copy of the engine (the exact failure mode
    the color-token-matrix spec closes)."""

    def test_theme_json_is_valid_json_with_neutral_roles(self, tmp_path):
        import json

        proj = _create(tmp_path, "app", "monolith")
        theme = json.loads((proj / "frontend" / "stapel.theme.json").read_text())
        core = theme["core"]
        for role in (
            "surface", "surface-raised", "surface-sunken", "surface-overlay",
            "text", "text-muted", "text-subtle", "text-on-accent",
            "border", "border-subtle", "focus-ring",
            "brand", "brand-hover", "brand-active", "brand-subtle",
            "link", "link-hover",
        ):
            assert role in core, role
            assert set(core[role]) == {"light", "dark"}
        for kind in ("success", "warning", "error", "info"):
            for suffix in ("", "-bg", "-border", "-on"):
                assert f"{kind}{suffix}" in core, f"{kind}{suffix}"
        # no design-system-specific / legacy ad-hoc vocabulary as a ROLE KEY
        # (e.g. "text-on-accent" legitimately contains "accent" as a
        # substring — check keys, not a raw substring search).
        for banned in (
            "colorPrimary", "colorBgLayout", "palette", "accent",
            "upperground-primary", "background-primary-subtle",
        ):
            assert banned not in core, banned

    def test_theme_json_ramps_are_private_hex_source(self, tmp_path):
        import json

        proj = _create(tmp_path, "app", "monolith")
        theme = json.loads((proj / "frontend" / "stapel.theme.json").read_text())
        assert "ramps" in theme
        assert "brand" in theme["ramps"]
        # core roles reference ramp.step, never a raw hex directly
        for role, pair in theme["core"].items():
            for shade in ("light", "dark"):
                ref = pair[shade]
                assert not ref.startswith("#"), (role, shade, ref)
                assert "." in ref, (role, shade, ref)

    def test_package_json_depends_on_stapel_tokens_and_wires_gen_scripts(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        pkg = (proj / "frontend" / "package.json").read_text()
        assert '"@stapel/tokens"' in pkg
        assert '"gen:tokens"' in pkg
        assert '"gen:tokens:check"' in pkg
        assert "stapel-tokens --theme ./stapel.theme.json" in pkg

    def test_precommit_has_tokens_check_hook_only_for_monolith(self, tmp_path):
        mono = _create(tmp_path / "a", "app", "monolith")
        mini = _create(tmp_path / "b", "app", "minimal")
        mono_cfg = (mono / ".pre-commit-config.yaml").read_text()
        assert "tokens-check" in mono_cfg
        assert "gen:tokens:check" in mono_cfg
        assert "tokens-check" not in (mini / ".pre-commit-config.yaml").read_text()

    def test_no_forked_generator_vendored_into_scaffold_templates(self):
        """Numeric gate: the scaffold must call @stapel/tokens' own
        `stapel-tokens` bin, never vendor a copy of its generator internals
        (`gen-tokens.mjs`/`tokens-lib.mjs` — the exact forked-generator
        failure mode §68 closes)."""
        import stapel_tools._frontend_templates as F
        import stapel_tools._precommit_templates as P

        for name in dir(F):
            # Uppercase names in this module are templates AND plain constants
            # (a photo count, an icon registry); only the strings can vendor a
            # generator.
            val = getattr(F, name)
            if name.isupper() and isinstance(val, str):
                assert "tokens-lib.mjs" not in val
                assert "gen-tokens.mjs" not in val
        for name in dir(P):
            if name.isupper() or name.startswith("_"):
                val = getattr(P, name)
                if isinstance(val, str):
                    assert "tokens-lib.mjs" not in val
                    assert "gen-tokens.mjs" not in val

    def test_agents_md_describes_brand_role_for_default_button(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        agents = (proj / "AGENTS.md").read_text()
        assert "stapel.theme.json" in agents
        assert "`brand`" in agents
        assert "gen:tokens" in agents

    def test_vite_config_reads_backend_target_from_env(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        config = (proj / "frontend" / "vite.config.ts").read_text()
        assert "env.VITE_BACKEND_TARGET" in config
        assert "http://svc-app:8000" in config  # compose-network default
        assert '"/app/"' in config  # reserved backend prefix proxied

    def test_app_tsx_calls_reserved_backend_prefix_not_absolute_url(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        app_tsx = (proj / "frontend" / "src" / "App.tsx").read_text()
        assert 'fetch("/app/api/health/")' in app_tsx
        assert "http://" not in app_tsx  # no absolute backend origin baked in

    def test_frontend_dockerfile_is_build_and_export_only_not_a_service(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        dockerfile = (proj / "frontend" / "Dockerfile").read_text()
        assert "FROM node:22-alpine AS build" in dockerfile
        assert "npm run build" in dockerfile
        # The export stage publishes via the shipped script, not a CMD
        # one-liner — see the next test for what the script has to do.
        assert "FROM build AS export" in dockerfile
        assert "frontend-publish" in dockerfile

    def test_publish_script_swaps_a_build_dir_instead_of_wiping_the_volume(self, tmp_path):
        """The shape it replaces was `rm -rf /output/* && cp -r dist/. /output/`.

        Two real defects in that one-liner, both user-visible on every deploy:
        the site 404s for as long as the copy takes, and the PREVIOUS build's
        content-hashed chunks are deleted, so any tab open across the deploy
        dies on its next chunk fetch. The replacement publishes into a
        per-build directory and repoints `current`, keeping N previous builds.
        """
        proj = _create(tmp_path, "app", "monolith")
        script = (proj / "frontend" / "frontend-publish.sh").read_text()
        assert "rm -rf /output/*" not in script
        assert "$OUT/$BUILD_ID" in script
        assert 'ln -sfn "$BUILD_ID" "$OUT/current"' in script
        assert "FRONTEND_KEEP_PREVIOUS" in script
        # ...and nginx must actually serve through that symlink, or the swap
        # is decorative.
        conf = (proj / "service-configs" / "nginx" / "nginx.conf").read_text()
        assert "root /usr/share/nginx/html/current;" in conf

    def test_nginx_waits_for_the_frontend_writer(self, tmp_path):
        """nginx starting before the volume is filled serves 404s until the
        one-shot finishes. The gate lives in the BASE on purpose: several
        docker compose versions refuse to override a service that arrived via
        `include:`, so gating from the prod overlay would work locally and
        fail on the stand."""
        proj = _create(tmp_path, "app", "monolith")
        base = (proj / "docker-compose.base.yml").read_text()
        assert "frontend-build:\n        condition: service_completed_successfully" in base


class TestEntrypointCanon:
    """Item 3: migrate + createsuperuser through Django's OWN --noinput flow
    (no hand-rolled Python importing models) + collectstatic."""

    def test_bootstrap_sh_has_no_custom_python_or_model_imports(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        script = (proj / "svc-app" / "bootstrap.sh").read_text()
        # The steps live in bootstrap.sh, the shared helpers in the step
        # runner it sources (scripts/bootstrap_lib.sh) — both are the canon.
        runner = (proj / "scripts" / "bootstrap_lib.sh").read_text()
        assert "python manage.py migrate --noinput" in script
        assert "python manage.py createsuperuser --noinput" in runner
        assert "python manage.py collectstatic" in script
        # never a hand-rolled Python import/model reference as a real
        # statement (only prose in comments describing the bug this avoids)
        # — the exact failure class found live: a bespoke entrypoint that
        # imported a model deleted in a later migration.
        code_lines = [ln for ln in script.splitlines() if not ln.strip().startswith("#")]
        code = "\n".join(code_lines)
        assert "import " not in code
        assert "python -c" not in code
        assert "apps." not in code

    def test_createsuperuser_is_gated_and_idempotent(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        runner = (proj / "scripts" / "bootstrap_lib.sh").read_text()
        assert 'DJANGO_SUPERUSER_USERNAME' in runner
        assert 'DJANGO_SUPERUSER_PASSWORD' in runner
        # tolerates "already exists" without failing the whole boot
        assert "createsuperuser --noinput ||" in runner
        # and the step itself is optional — a missing admin account is an
        # inconvenience, not a wrong service.
        assert 'optional "superuser"' in (proj / "svc-app" / "bootstrap.sh").read_text()


class TestAgentsAndPrecommitCanon:
    """Items 4/5: AGENTS.md (base OSS rules) + .pre-commit-config.yaml +
    README "Checks" section, emitted for every project type."""

    @pytest.mark.parametrize("ptype", ["monolith", "minimal", "microservices"])
    def test_agents_md_emitted_with_core_rule_codes(self, tmp_path, ptype):
        proj = _create(tmp_path, "app", ptype)
        agents = (proj / "AGENTS.md").read_text()
        for code in ("R001", "R005", "R006", "R007", "SWAP001", "SWAP002",
                     "CFG001", "CFG002", "URL001"):
            assert code in agents, code
        assert "stapel-verify" in agents

    def test_frontend_section_only_for_monolith(self, tmp_path):
        mono = _create(tmp_path / "a", "app", "monolith")
        mini = _create(tmp_path / "b", "app", "minimal")
        micro = _create(tmp_path / "c", "app", "microservices")

        mono_agents = (mono / "AGENTS.md").read_text()
        mini_agents = (mini / "AGENTS.md").read_text()
        micro_agents = (micro / "AGENTS.md").read_text()

        assert "no-raw-colors" in mono_agents
        assert "/app/" in mono_agents  # reserved namespace, this project's slug
        assert "no-raw-colors" not in mini_agents
        assert "no-raw-colors" not in micro_agents

    @pytest.mark.parametrize("ptype", ["monolith", "minimal", "microservices"])
    def test_precommit_config_runs_stapel_verify(self, tmp_path, ptype):
        proj = _create(tmp_path, "app", ptype)
        cfg = (proj / ".pre-commit-config.yaml").read_text()
        assert "stapel-verify ." in cfg
        parsed = yaml.safe_load(cfg)
        assert parsed["repos"][0]["hooks"][0]["entry"] == "stapel-verify ."

    def test_precommit_config_has_eslint_hook_only_for_monolith(self, tmp_path):
        mono = _create(tmp_path / "a", "app", "monolith")
        mini = _create(tmp_path / "b", "app", "minimal")
        assert "eslint" in (mono / ".pre-commit-config.yaml").read_text()
        assert "eslint" not in (mini / ".pre-commit-config.yaml").read_text()

    @pytest.mark.parametrize("ptype", ["monolith", "minimal", "microservices"])
    def test_readme_has_checks_section(self, tmp_path, ptype):
        proj = _create(tmp_path, "app", ptype)
        readme = (proj / "README.md").read_text()
        assert "## Checks" in readme
        assert "pre-commit install" in readme


class TestStaticMediaNamespaceReservation:
    """Item 2: does the monolith template already reserve a namespace for
    static/media so a frontend catch-all can't collide? Yes — verified here
    against the ACTUAL generated settings, not just the nginx conf comment."""

    def test_backend_static_media_are_namespaced_per_slug(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        settings = (proj / "svc-app" / "config" / "settings" / "base.py").read_text()
        # Plain string literals, not f-strings — {{SLUG}} is substituted at
        # render time textually, so an f-prefix here would just be an
        # extraneous-prefix lint violation (ruff F541) with no interpolation
        # of its own.
        assert 'STATIC_URL = "/staticfiles/app/"' in settings
        assert 'MEDIA_URL = "/media/app/"' in settings

    def test_nginx_reserves_bare_prefixes_before_any_backend_or_frontend_route(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        conf = (proj / "service-configs" / "nginx" / "nginx.conf").read_text()
        # /staticfiles/ and /media/ are bare, project-wide reservations (not
        # per-slug in nginx — the per-slug split happens inside Django via
        # STATIC_URL/MEDIA_URL above); a service's own api/admin/health also
        # gets its own reserved /<slug>/ block (added by stapel-new-service).
        assert "location /staticfiles/" in conf
        assert "location /media/" in conf
        assert "location /app" in conf  # per-service block, appended live


class TestGenerativeBackendPrefixes:
    """Owner directive: proxy rules are GENERATED from the actual lib
    selection (STAPEL_LIBS url_prefixes + slug + admin + static/media) —
    never a hand-maintained list. Add a lib -> its rule appears in ALL
    THREE surfaces (local nginx, prod nginx, vite proxy) by construction;
    the live-run "forgot /calendar in the proxy" bug is unrepresentable.

    Revised after a SECOND live-run collision this same mechanism then
    caused: reserving a lib's BARE root (`/calendar/`) shadowed the
    identically-named frontend SPA page. Each lib now reserves only its
    named sub-surfaces (api/swagger/schema.json/admin) — see
    TestModuleRootStaysFrontends below for the exact regression."""

    def _create_with(self, tmp_path, modules):
        create_project(
            name="app", project_type="monolith", title="App",
            url="https://x.dev", company_name="X", company_email="x@x.dev",
            modules=modules, output_dir=tmp_path,
            use_submodules=False, init_git=False,
        )
        return tmp_path / "app"

    def test_selected_lib_prefixes_present_in_all_three_surfaces(self, tmp_path):
        proj = self._create_with(tmp_path, ["core", "auth", "calendar"])
        local = (
            proj / "service-configs" / "nginx-local" / "default.conf.template"
        ).read_text()
        prod = (proj / "service-configs" / "nginx" / "nginx.conf").read_text()
        vite = (proj / "frontend" / "vite.config.ts").read_text()

        # The slug/admin fixed reservations still reserve their WHOLE subtree.
        for prefix in ("app", "admin"):
            assert f"location /{prefix}/ " in local, (prefix, "local nginx")
            assert (
                f"location /{prefix}/ " in prod or f"location /{prefix} " in prod
            ), (prefix, "prod nginx")
            assert f'"/{prefix}/"' in vite, (prefix, "vite proxy")

        # Each selected lib reserves only its named sub-surfaces — never the
        # bare root (see reserved-paths.json / TestModuleRootStaysFrontends).
        for mod in ("auth", "calendar"):
            for sub, modifier in (
                (f"{mod}/api", "^~"),
                (f"{mod}/swagger", "^~"),
                (f"{mod}/admin", "^~"),
            ):
                assert f"location {modifier} /{sub}/ " in local, (sub, "local nginx")
                assert f"location {modifier} /{sub}/ " in prod, (sub, "prod nginx")
                assert f'"/{sub}/"' in vite, (sub, "vite proxy")
            schema = f"{mod}/schema.json"
            assert f"location = /{schema} " in local, (schema, "local nginx")
            assert f"location = /{schema} " in prod, (schema, "prod nginx")
            assert f'"/{schema}"' in vite, (schema, "vite proxy")

    def test_unselected_lib_prefix_absent(self, tmp_path):
        proj = self._create_with(tmp_path, ["core", "auth"])
        vite = (proj / "frontend" / "vite.config.ts").read_text()
        local = (
            proj / "service-configs" / "nginx-local" / "default.conf.template"
        ).read_text()
        assert '"/calendar/api/"' not in vite
        assert "location ^~ /calendar/api/" not in local

    def test_headless_lib_reserves_no_prefix(self, tmp_path):
        # attributes is http=False (pure library, mounts nowhere) — it must
        # not claim a URL prefix anywhere.
        proj = self._create_with(tmp_path, ["core", "attributes"])
        vite = (proj / "frontend" / "vite.config.ts").read_text()
        assert '"/attributes/' not in vite


class TestModuleRootStaysFrontends:
    """Regression (owner postmortem): a generated nginx/Vite rule used to
    reserve a lib's BARE root (`location /calendar/`), shadowing the
    identically-named frontend SPA page ("/calendar" — the calendar view).
    The fix: only the module's named sub-surfaces are reserved; the bare
    root and any other sub-path are the frontend catch-all's. Verified by
    PARSING the generated configs (not string search) so a location's shape
    (prefix vs sub-path vs exact) can't silently regress."""

    def _create_with(self, tmp_path, modules):
        create_project(
            name="app", project_type="monolith", title="App",
            url="https://x.dev", company_name="X", company_email="x@x.dev",
            modules=modules, output_dir=tmp_path,
            use_submodules=False, init_git=False,
        )
        return tmp_path / "app"

    @staticmethod
    def _location_paths(conf_text: str) -> list[str]:
        """Every ``location <modifier> <path> {`` path this generated conf
        declares (modifier-agnostic — ``^~``/``=``/bare all parse the same
        way here), by simple line-oriented parsing (no third-party nginx
        config parser dependency; the generated shape is fixed and simple
        enough that this is exact, not approximate)."""
        paths = []
        for line in conf_text.splitlines():
            line = line.strip()
            if not line.startswith("location") or not line.endswith("{"):
                continue
            body = line[len("location"):-1].strip()
            parts = body.split()
            path = parts[-1] if parts else ""
            paths.append(path)
        return paths

    def test_no_bare_calendar_location_in_local_nginx(self, tmp_path):
        proj = self._create_with(tmp_path, ["core", "calendar"])
        local = (
            proj / "service-configs" / "nginx-local" / "default.conf.template"
        ).read_text()
        paths = self._location_paths(local)
        assert "/calendar/" not in paths
        assert "/calendar/api/" in paths

    def test_no_bare_calendar_location_in_prod_nginx(self, tmp_path):
        proj = self._create_with(tmp_path, ["core", "calendar"])
        prod = (proj / "service-configs" / "nginx" / "nginx.conf").read_text()
        paths = self._location_paths(prod)
        assert "/calendar/" not in paths
        assert "/calendar/api/" in paths

    def test_no_bare_calendar_key_in_vite_proxy(self, tmp_path):
        proj = self._create_with(tmp_path, ["core", "calendar"])
        vite = (proj / "frontend" / "vite.config.ts").read_text()
        assert '"/calendar/":' not in vite
        assert '"/calendar/api/":' in vite

    def test_reserved_paths_json_never_lists_a_bare_module_root(self, tmp_path):
        import json

        proj = self._create_with(tmp_path, ["core", "auth", "calendar"])
        manifest = json.loads((proj / "reserved-paths.json").read_text())
        prefixes = manifest["reservedPathPrefixes"]
        assert "/calendar" not in prefixes
        assert "/auth" not in prefixes
        assert "/calendar/api" in prefixes
        assert "/auth/api" in prefixes

    def test_eslint_rule_frees_bare_root_but_catches_api_subpath(self, tmp_path):
        """End-to-end against the REAL @stapel/eslint-plugin data layer (not
        a reimplementation) — the owner's exact collision report: routing
        "/calendar" must miss every reserved prefix (the frontend page
        survives), routing "/calendar/api/v1/x" must hit one (the backend
        surface is still guarded). Skips if the sibling stapel-react
        checkout isn't present (this repo doesn't depend on it)."""
        import json
        import shutil
        import subprocess
        from pathlib import Path

        data_js = (
            Path(__file__).resolve().parents[2]
            / "stapel-react" / "packages" / "eslint-plugin" / "lib" / "data.js"
        )
        if not data_js.is_file() or not shutil.which("node"):
            pytest.skip("sibling stapel-react/packages/eslint-plugin checkout or node not available")

        proj = self._create_with(tmp_path, ["core", "calendar"])
        reserved = proj / "reserved-paths.json"
        script = f"""
import {{ loadReservedPathCatalog }} from {json.dumps(str(data_js))};
const catalog = loadReservedPathCatalog({{ reservedPathsFile: {json.dumps(str(reserved))} }});
console.log(JSON.stringify({{
  bareRoot: catalog.matches("/calendar"),
  apiSubpath: catalog.matches("/calendar/api/v1/x"),
}}));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True, text=True, check=True,
        )
        out = json.loads(result.stdout.strip().splitlines()[-1])
        assert out["bareRoot"] is None, "bare /calendar must stay the frontend's"
        assert out["apiSubpath"] == "/calendar/api", "the API sub-path must still be guarded"

    def test_local_nginx_starts_without_backend_deferred_resolution(self, tmp_path):
        """proxy_pass must go through a VARIABLE ($stapel_backend) — a
        literal host makes nginx refuse to start while the backend container
        is down, deadlocking compose up ordering (found live)."""
        proj = self._create_with(tmp_path, ["core"])
        local = (
            proj / "service-configs" / "nginx-local" / "default.conf.template"
        ).read_text()
        assert "set $stapel_backend http://${BACKEND_UPSTREAM};" in local
        assert "proxy_pass $stapel_backend;" in local
        assert "proxy_pass http://${BACKEND_UPSTREAM};" not in local


class TestFrontendReactWiring:
    """Frontend wiring gap (owner directive): a project scaffolded with
    feature libs that have a published ``@stapel/<module>-react`` pair gets
    that pair's dep + provider wiring generated for free — never a generic
    shell that silently drops the frontend counterpart of a selected
    backend module. Selections with no react-paired module stay the exact
    prior clean shell (regression)."""

    REACT_PACKAGES = {
        "auth": "@stapel/auth-react",
        "billing": "@stapel/billing-react",
        "calendar": "@stapel/calendar-react",
        "notifications": "@stapel/notifications-react",
        "profiles": "@stapel/profiles-react",
        "recordings": "@stapel/recordings-react",
        "workspaces": "@stapel/workspaces-react",
    }

    @staticmethod
    def _imported_packages(*sources: str) -> set[str]:
        """Every non-relative package name imported across the given source
        texts — scoped packages collapse a `/default` (or any other)
        subpath back to the bare package name (`@stapel/auth-react/default`
        -> `@stapel/auth-react`) so a dep-presence check lines up with
        package.json keys."""
        import re

        packages: set[str] = set()
        for src in sources:
            for m in re.finditer(r'from\s+"([^"]+)"', src):
                spec = m.group(1)
                if spec.startswith("."):
                    continue  # relative import — not an npm dep
                if spec.startswith("@"):
                    parts = spec.split("/", 2)
                    packages.add("/".join(parts[:2]))
                else:
                    packages.add(spec.split("/", 1)[0])
        return packages

    def test_monolith_with_two_react_paired_modules_wires_exactly_those_react_deps(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", modules=["core", "billing", "calendar"])
        import json

        pkg = json.loads((proj / "frontend" / "package.json").read_text())
        deps = pkg["dependencies"]
        react_deps = {k for k in deps if k.endswith("-react")}
        # The pairs the selection asked for, and NOT a third pair's package —
        # that is the invariant here. `@stapel/shell-react` is not a third
        # pair: both of these publish a nav manifest, so the container routes
        # their screens and needs the shell that draws the menu (and the antd
        # skin the shell is written against). This test used to assert the
        # opposite about antd on the belief that billing and calendar were
        # headless; both ship a `./default` subpath and an antd peer dep.
        assert react_deps == {
            "@stapel/billing-react",
            "@stapel/calendar-react",
            "@stapel/shell-react",
        }
        assert "antd" in deps
        assert "@stapel/tokens-antd" in deps
        # support deps every react pair needs are present
        assert "@stapel/core" in deps
        assert "@tanstack/react-query" in deps

    def test_monolith_with_antd_skinned_module_pulls_antd_bridge(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", modules=["core", "auth"])
        import json

        pkg = json.loads((proj / "frontend" / "package.json").read_text())
        deps = pkg["dependencies"]
        # Read the expected pin from the registry rather than repeating the
        # number here: a duplicated literal turns every legitimate version
        # bump into a red test that says nothing about behaviour. What this
        # asserts is the LINK — the registry's pin is what lands in the
        # generated package.json.
        from stapel_tools.create_project import FRONTEND_REACT_LIBS

        expected = FRONTEND_REACT_LIBS["auth"]["version"]
        assert deps["@stapel/auth-react"] == f"^{expected}"
        assert "antd" in deps
        assert "@stapel/tokens-antd" in deps

    def test_generated_app_imports_resolve_to_deps_present_in_package_json(self, tmp_path):
        """The numeric compile-conceptually gate: parse every import in the
        generated App.tsx + modules.tsx and assert each non-relative spec's
        package is declared in package.json (dependencies or
        devDependencies) — proof the generated app wouldn't 501 on
        `npm install` with a missing/undeclared package. The subject is a
        selection of pairs that publish NO nav manifest — cdn, reviews and
        attributes, each for a reason recorded in the pair itself — because a
        nav-bearing selection activates react-router routing instead of
        App.tsx (P1, TestFrontendNavWiring's own equivalent gate covers THAT
        shape). It used to be billing/calendar/recordings/workspaces, which
        were nav-less only because this registry had not mirrored the
        manifests they publish."""
        import json

        proj = _create(
            tmp_path, "app", "monolith",
            modules=["core", "cdn", "reviews", "attributes"],
        )
        frontend = proj / "frontend"
        app_tsx = (frontend / "src" / "App.tsx").read_text()
        modules_tsx = (frontend / "src" / "modules.tsx").read_text()
        pkg = json.loads((frontend / "package.json").read_text())
        declared = {*pkg.get("dependencies", {}), *pkg.get("devDependencies", {})}

        imported = self._imported_packages(app_tsx, modules_tsx)
        # "react" itself + every @stapel/*-react + support deps must resolve
        assert imported, "expected at least one non-relative import"
        missing = imported - declared
        assert not missing, f"imported but not declared: {missing}"

    def test_react_module_dep_versions_come_from_the_registry(self, tmp_path):
        """Every selected pair's registry pin is what lands in package.json.

        This used to hold a SECOND copy of the version table and assert
        equality against it, under a docstring claiming the pins were
        "verified against npm" — which they were not: nothing here talks to
        npm, so all it really proved was that one hand-maintained table
        matched another. The registry's `@stapel/core` pin sat at 0.6.2
        while the generator emitted code importing `PackageNavManifest`, a
        type published later, and this test stayed green through all of it.
        The generated frontend simply did not compile, and the CI step that
        would have said so was unreachable behind two earlier failures.

        So: assert the LINK (registry → package.json), and let the e2e job's
        `vite build` be what proves the pins are actually installable and
        sufficient — a real build, not a mirror of a constant."""
        import json

        from stapel_tools.create_project import FRONTEND_REACT_LIBS

        keys = ["auth", "billing", "calendar", "notifications", "profiles",
                "recordings", "workspaces"]
        proj = _create(tmp_path, "app", "monolith", modules=["core", *keys])
        pkg = json.loads((proj / "frontend" / "package.json").read_text())
        deps = pkg["dependencies"]
        for key in keys:
            expected = FRONTEND_REACT_LIBS[key]["version"]
            assert deps[self.REACT_PACKAGES[key]] == f"^{expected}", key

    def test_modules_tsx_wires_provider_and_runtime_per_selected_pair(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", modules=["core", "billing", "recordings"])
        modules_tsx = (proj / "frontend" / "src" / "modules.tsx").read_text()
        assert "createBillingRuntime({ baseUrl: \"/billing/api/v1/\" })" in modules_tsx
        assert "createRecordingsRuntime({ baseUrl: \"/recordings/api/v1/\" })" in modules_tsx
        assert "<BillingProvider runtime={billingRuntime}>" in modules_tsx
        assert "<RecordingsProvider runtime={recordingsRuntime}>" in modules_tsx
        assert "registerBillingI18n(i18n)" in modules_tsx
        assert "registerRecordingsI18n(i18n)" in modules_tsx
        assert "export function ModulesProvider" in modules_tsx
        assert "export function ModulesPanel" in modules_tsx
        # no default skin for either pair -> ModulesPanel mounts nothing
        assert "return null;" in modules_tsx

    def test_modules_tsx_mounts_only_the_zero_config_default_components(self, tmp_path):
        """auth's AuthPanel and notifications' NotificationFeedList are
        genuinely zero-required-prop `/default` components and get mounted;
        workspaces ships a `/default` subpath too but every one of its
        components requires a `workspaceId` the scaffold cannot fabricate —
        it must stay provider-only, never a guessed mount."""
        proj = _create(
            tmp_path, "app", "monolith",
            modules=["core", "auth", "notifications", "workspaces"],
        )
        modules_tsx = (proj / "frontend" / "src" / "modules.tsx").read_text()
        assert "<AuthPanel />" in modules_tsx
        assert "<NotificationFeedList />" in modules_tsx
        assert "WorkspaceSettings" not in modules_tsx
        assert "MembersManager" not in modules_tsx
        assert '@stapel/workspaces-react/default"' not in modules_tsx

    def test_app_tsx_switches_to_module_aware_template_and_mounts_modules_provider(self, tmp_path):
        """"reviews" publishes no nav manifest (reviews render inside a
        listing page and a seller page, never on a route of their own), so it
        stays on the flat single-page App.tsx/ModulesPanel shape — a
        nav-bearing selection like "profiles" or "billing" activates
        react-router routing instead (P1, TestFrontendNavWiring)."""
        proj = _create(tmp_path, "app", "monolith", modules=["core", "reviews"])
        app_tsx = (proj / "frontend" / "src" / "App.tsx").read_text()
        assert 'from "./modules.js"' in app_tsx
        assert "<ModulesProvider>" in app_tsx
        assert "<ModulesPanel />" in app_tsx
        # still hits the reserved backend prefix, never an absolute origin
        assert 'fetch("/app/api/health/")' in app_tsx
        assert "http://" not in app_tsx

    def test_first_selected_pair_is_the_default_stapel_provider_client(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", modules=["core", "billing", "auth"])
        # registry order (STAPEL_LIBS order), not CLI arg order: auth sorts
        # before billing in STAPEL_LIBS, so auth is the primary client.
        modules_tsx = (proj / "frontend" / "src" / "modules.tsx").read_text()
        assert "client={authRuntime.client}" in modules_tsx
        assert "billing: billingRuntime.client," in modules_tsx

    def test_only_non_react_paired_libs_produce_the_prior_clean_shell(self, tmp_path):
        """Regression: a selection with zero react-paired modules must not
        gain modules.tsx, must not switch App.tsx templates, and
        package.json's dependencies stay exactly {react, react-dom}.

        This test used to use "gdpr", then "currencies" — each was the same
        shape until its pair shipped and FRONTEND_REACT_LIBS registered it
        (`@stapel/currencies-react` 0.3.0, this wave). The subject moves to
        "docs", still genuinely pairless with an empty `requires` list (so
        nothing it pulls in is paired either). THAT churn is the test working:
        the invariant it guards — a lib with no pair contributes nothing to
        the frontend — has not moved once."""
        import json

        proj = _create(tmp_path, "app", "monolith", modules=["core", "docs", "translate"])
        frontend = proj / "frontend"
        assert not (frontend / "src" / "modules.tsx").exists()
        app_tsx = (frontend / "src" / "App.tsx").read_text()
        assert "./modules" not in app_tsx
        assert "ModulesProvider" not in app_tsx

        pkg = json.loads((frontend / "package.json").read_text())
        assert pkg["dependencies"] == {"react": "^19.1.0", "react-dom": "^19.1.0"}

    def test_headless_lib_with_no_react_pair_scaffolds_with_zero_frontend_wiring(self, tmp_path):
        """vault (http=False, no @stapel/vault-react package exists) must not
        appear anywhere in the frontend wiring surface.

        This test used to use `attributes`, which was the same shape until
        `@stapel/attributes-react` shipped — the pair now exists, so the
        subject moved to a lib that is still genuinely pairless. That IS the
        maintenance this test asks for; the invariant it guards (a lib with no
        pair contributes nothing to the frontend) has not moved."""
        import json

        proj = _create(tmp_path, "app", "monolith", modules=["core", "vault", "billing"])
        pkg = json.loads((proj / "frontend" / "package.json").read_text())
        assert not any("vault" in dep for dep in pkg["dependencies"])
        modules_tsx = (proj / "frontend" / "src" / "modules.tsx").read_text()
        assert "vault" not in modules_tsx.lower()


class TestFrontendNavWiring:
    """P1 scripted-fullstack navigation — SCAFFOLD half (the lib-side core,
    ``@stapel/shell-react``'s ``resolveNav``/``<AppShell/>``, already
    shipped to stapel-react main; not yet published to npm). ``--auth``/
    ``--landing``/a selected pair with mirrored nav entries turns on
    react-router v7 wiring; a selection with none of the three stays the
    exact prior clean shell (regression, mirrored from
    TestFrontendReactWiring's own byte-identical test)."""

    @staticmethod
    def _extract_installed_manifests(nav_generated_ts: str) -> list[dict]:
        import re

        m = re.search(
            r"INSTALLED_NAV_MANIFESTS: readonly PackageNavManifest\[\] = (\[.*?\]) as const;",
            nav_generated_ts, re.DOTALL,
        )
        assert m, "INSTALLED_NAV_MANIFESTS literal not found in nav.generated.ts"
        import json

        return json.loads(m.group(1))

    @staticmethod
    def _resolve_nav_mirror(manifests: list[dict], overrides: dict | None = None) -> list[dict]:
        """A Python port of ``@stapel/shell-react``'s own ``resolveNav``
        (``packages/shell-react/src/headless/resolveNav.ts``) — used ONLY to
        verify the generated ``nav.generated.ts``'s baked
        ``INSTALLED_NAV_MANIFESTS`` resolves to the expected ``RESOLVED_NAV``
        shape, without executing JS/TS (this repo can't ``npm ci`` — the
        package isn't published yet; see this class's own numeric-gate
        note). Mirrors the algorithm exactly: resolve each entry's
        menuVisible/order (override ?? default), nest submenu entries under
        their resolved top (dropping orphans), sort by (order, id), then
        filter out any entry whose resolved menuVisible is false — a top
        that resolves invisible drops its WHOLE subtree, same as the real
        implementation's documented behaviour."""
        overrides = overrides or {}

        def resolve_one(e: dict) -> dict:
            o = overrides.get(e["id"], {})
            return {
                **e,
                "order": o.get("order", e["order"]),
                "menuVisible": o.get("menuVisible", e["menuVisibleDefault"]),
            }

        all_entries = [e for m in manifests for e in m["entries"]]
        tops = {e["id"]: resolve_one(e) for e in all_entries if e["placement"]["level"] == "top"}
        children_by_parent: dict[str, list[dict]] = {}
        for e in all_entries:
            if e["placement"]["level"] != "submenu":
                continue
            parent_id = e["placement"].get("parentId")
            if parent_id not in tops:
                continue
            children_by_parent.setdefault(parent_id, []).append(resolve_one(e))

        result = []
        for top in sorted(tops.values(), key=lambda e: (e["order"], e["id"])):
            if not top["menuVisible"]:
                continue
            kids = children_by_parent.get(top["id"])
            if kids is None:
                result.append(top)
                continue
            visible = sorted((k for k in kids if k["menuVisible"]), key=lambda e: (e["order"], e["id"]))
            result.append({**top, "children": visible})
        return result

    def test_no_flags_no_nav_module_scaffold_is_byte_identical_to_app_tsx(self, tmp_path):
        """Regression (mirrors TestFrontendReactWiring's own byte-identical
        test): no --auth, no --landing, no selected pair with nav entries
        -> App.tsx/main.tsx are the EXACT prior clean-shell output, and no
        routing artifact (routes.tsx/nav.generated.ts/ProtectedRoute.tsx/
        stapel.nav.json/LandingPage.tsx) exists at all.

        Uses "docs" rather than "gdpr"/"currencies" for the same reason as
        TestFrontendReactWiring's sibling regression test above: both of those
        are react-paired now (FRONTEND_REACT_LIBS), so neither fits a test
        about the NO-nav-pair shell."""
        import stapel_tools._frontend_templates as F
        from stapel_tools._compose_templates import render_tokens

        proj = _create(tmp_path, "app", "monolith", modules=["core", "docs", "translate"])
        frontend = proj / "frontend"
        app_tsx = (frontend / "src" / "App.tsx").read_text()
        expected_app_tsx = render_tokens(F.APP_TSX, {"SLUG": "app", "TITLE": "App"})
        assert app_tsx == expected_app_tsx
        assert (frontend / "src" / "main.tsx").read_text() == F.MAIN_TSX
        assert (frontend / "tsconfig.json").read_text() == F.TSCONFIG_JSON
        for rel in (
            "src/routes.tsx", "src/nav.generated.ts", "src/ProtectedRoute.tsx",
            "stapel.nav.json", "src/LandingPage.tsx",
        ):
            assert not (frontend / rel).exists(), rel

    def test_auth_profiles_notifications_wires_login_route_and_resolved_nav(self, tmp_path):
        """``--modules auth,profiles,notifications --auth --landing``:
        routes.tsx has a "/login" route importing AuthPanel, and
        nav.generated.ts's baked INSTALLED_NAV_MANIFESTS resolves (via the
        Python resolveNav port above) to EXACTLY the top-level menuVisible
        entries these three pairs declare — notifications.feed,
        profiles.settings, profiles.connections — plus the container's own
        admin root, which joins because auth's admin skin hangs five screens
        from it. auth.login is NOT among them: its mirrored
        `menuVisibleDefault` is `false` (a sign-in screen is never a menu
        tab), so the real resolveNav algorithm filters it out of RESOLVED_NAV
        entirely — it still gets its own "/login" ROUTE (routing != the menu),
        just no tab. Same for profiles.public (`/u/:userId` is reached from a
        listing or a conversation, never from the chrome). Submenu entries
        (auth.security, notifications.push) are never top-level either."""
        proj = _create(
            tmp_path, "app", "monolith",
            modules=["core", "auth", "profiles", "notifications"],
            want_auth=True, want_landing=True,
        )
        frontend = proj / "frontend"
        routes_tsx = (frontend / "src" / "routes.tsx").read_text()
        assert 'path: "/login"' in routes_tsx
        assert '{ path: "/login", element: <AuthPanel /> }' in routes_tsx
        assert 'AuthPanel' in routes_tsx and '"@stapel/auth-react/default"' in routes_tsx

        nav_ts = (frontend / "src" / "nav.generated.ts").read_text()
        manifests = self._extract_installed_manifests(nav_ts)
        resolved = self._resolve_nav_mirror(manifests)
        top_ids = [e["id"] for e in resolved]
        assert top_ids == [
            "notifications.feed", "profiles.settings", "profiles.connections",
            "admin.root",
        ]
        settings = next(e for e in resolved if e["id"] == "profiles.settings")
        assert [c["id"] for c in settings["children"]] == [
            "auth.security", "notifications.push",
        ]

    def test_the_generated_container_speaks_the_pinned_shells_contract(self, tmp_path):
        """Whatever the pin says, the generated project must TYPECHECK against
        the shell it installs: `mode` required below the floor, absent above
        it (see FRONTEND_SHELL_SELF_THEMING_FLOOR)."""
        from stapel_tools.create_project import shell_self_themes

        proj = _create(
            tmp_path, "app", "monolith",
            modules=["core", "auth", "profiles", "notifications"],
            want_auth=True,
        )
        routes_tsx = (proj / "frontend" / "src" / "routes.tsx").read_text()
        if shell_self_themes():
            assert 'mode="light"' not in routes_tsx
        else:
            assert 'mode="light"' in routes_tsx

    def test_the_staff_fact_is_handed_down_once_the_shell_pin_carries_the_prop(
        self, monkeypatch
    ):
        """`<AppShell/>` reads no session by design (the rule that keeps
        `resolveNav` pure), so the container hands down `user.is_staff` — the
        very field `AdminGate` refuses on, so the menu and the screen cannot
        disagree. Gated on the PIN, because the prop is not in every published
        shell (see FRONTEND_SHELL_STAFF_PROP_FLOOR)."""
        from stapel_tools import _frontend_templates as F
        from stapel_tools import create_project as C

        monkeypatch.setattr(C, "FRONTEND_SHELL_REACT_VERSION",
                            C.FRONTEND_SHELL_SELF_THEMING_FLOOR)
        pairs = F.nav_wired_pairs(
            [{"key": k, **C.FRONTEND_REACT_LIBS[k]} for k in ("auth", "profiles")],
            auth_wired=True,
        )
        src = F.render_routes_tsx(
            F.build_nav_route_plan(pairs),
            auth_wired=True, want_landing=False, app_route_present=True,
        )
        assert "function AppChrome()" in src
        assert "      nav={RESOLVED_NAV}" in src
        assert "      staff={user?.is_staff === true}" in src
        assert 'import { useAuthSessionState } from "@stapel/auth-react";' in src
        assert "<AppChrome />" in src
        # This pin predates `navBadges`, so no count is handed down and no
        # pair's counting hook is imported.
        assert "navBadges" not in src

    def test_a_shell_pin_without_the_prop_does_not_get_it_invented(self, monkeypatch):
        """The 0.54.0 class in a TypeScript costume: the prop exists in the
        stapel-react checkout and not in the published tarball, and a scaffold
        that emitted it regardless generated a project that does not compile.
        The pin decides, so the emission moves in the same commit the pin
        does."""
        from stapel_tools import _frontend_templates as F
        from stapel_tools import create_project as C

        monkeypatch.setattr(C, "FRONTEND_SHELL_REACT_VERSION", "0.6.0")
        pairs = F.nav_wired_pairs(
            [{"key": k, **C.FRONTEND_REACT_LIBS[k]} for k in ("auth", "profiles")],
            auth_wired=True,
        )
        src = F.render_routes_tsx(
            F.build_nav_route_plan(pairs),
            auth_wired=True, want_landing=False, app_route_present=True,
        )
        assert "AppChrome" not in src
        assert "staff=" not in src
        # ...and it keeps the `mode` the published shell REQUIRES.
        assert '<AppShell nav={RESOLVED_NAV} mode="light" />' in src

    def test_the_shell_floor_is_measured_not_remembered(self):
        from stapel_tools.create_project import (
            FRONTEND_SHELL_SELF_THEMING_FLOOR,
            shell_self_themes,
        )

        assert shell_self_themes("0.6.0") is False
        assert shell_self_themes(FRONTEND_SHELL_SELF_THEMING_FLOOR) is True
        assert shell_self_themes("0.7.2") is True
        assert shell_self_themes("1.0.0") is True

    def test_without_auth_there_is_no_staff_fact_to_hand_down(self, tmp_path):
        """No auth pair, no session: the shell's own default (absent means
        false) is then the honest answer, and inventing a session read would
        not compile.

        The chrome COMPONENT can still be there — notifications contributes a
        `navBadges` count, and a count is not a session. What must be absent
        is the staff fact and everything that would read one."""
        proj = _create(
            tmp_path, "app", "monolith", modules=["core", "notifications"],
        )
        routes_tsx = (proj / "frontend" / "src" / "routes.tsx").read_text()
        assert "staff=" not in routes_tsx
        assert "useAuthSessionState" not in routes_tsx
        assert "navBadges" in routes_tsx

    def test_a_selection_with_no_badge_source_and_no_auth_mounts_the_shell_bare(
        self, tmp_path
    ):
        """The other half of the same rule: nothing to hand down means no
        local chrome component at all, and the shell is the route element."""
        proj = _create(tmp_path, "app", "monolith", modules=["core", "billing"])
        routes_tsx = (proj / "frontend" / "src" / "routes.tsx").read_text()
        assert "element: <AppShell nav={RESOLVED_NAV}" in routes_tsx
        assert "AppChrome" not in routes_tsx
        assert "navBadges" not in routes_tsx

    def test_landing_only_scaffold_has_landing_route_and_no_app_protected_tree(self, tmp_path):
        """``--landing`` with no auth, no nav-bearing module: "/" mounts
        LandingPage, and there is no "/app" route at all (no ProtectedRoute,
        no AppShell, no nav.generated.ts/@stapel/shell-react dependency)."""
        import json

        proj = _create(tmp_path, "app", "monolith", modules=["core"], want_landing=True)
        frontend = proj / "frontend"
        routes_tsx = (frontend / "src" / "routes.tsx").read_text()
        assert 'element: <LandingPage />' in routes_tsx
        assert '"/app"' not in routes_tsx
        assert "ProtectedRoute" not in routes_tsx
        assert "AppShell" not in routes_tsx
        assert (frontend / "src" / "LandingPage.tsx").exists()
        assert not (frontend / "src" / "ProtectedRoute.tsx").exists()
        assert not (frontend / "src" / "nav.generated.ts").exists()
        assert not (frontend / "stapel.nav.json").exists()

        pkg = json.loads((frontend / "package.json").read_text())
        deps = pkg["dependencies"]
        assert "react-router" in deps
        assert "@stapel/shell-react" not in deps

    def test_generated_router_imports_resolve_to_declared_deps(self, tmp_path):
        """The numeric compile-conceptually gate (mirrors
        TestFrontendReactWiring's own): parse every non-relative import
        across every generated routing source and assert its package is
        declared in package.json. The ONE gate this can't cover — an actual
        `npm ci && npm run build` — is deferred to post-publish (see this
        class's own module docstring): @stapel/shell-react isn't on npm yet,
        and auth-react/profiles-react/notifications-react's shipped
        `nav-manifest.json`/`NavEntry` core types aren't in their last
        PUBLISHED release either."""
        import json

        proj = _create(
            tmp_path, "app", "monolith",
            modules=["core", "auth", "profiles", "notifications"],
            want_auth=True, want_landing=True,
        )
        frontend = proj / "frontend"
        sources = [
            (frontend / "src" / f).read_text()
            for f in (
                "routes.tsx", "nav.generated.ts", "ProtectedRoute.tsx",
                "LandingPage.tsx", "main.tsx",
            )
        ]
        pkg = json.loads((frontend / "package.json").read_text())
        declared = {*pkg.get("dependencies", {}), *pkg.get("devDependencies", {})}

        imported = TestFrontendReactWiring._imported_packages(*sources)
        assert imported, "expected at least one non-relative import"
        missing = imported - declared
        assert not missing, f"imported but not declared: {missing}"

    def test_no_auth_flag_excludes_auth_from_route_tree_but_keeps_runtime_wiring(self, tmp_path):
        """``--no-auth`` with "auth" still selected as a module: the auth
        RUNTIME still wires into modules.tsx (ModulesProvider/AuthProvider),
        but none of its screens join the route/nav tree — no "/login"
        route, no ProtectedRoute, no auth entries in nav.generated.ts."""
        proj = _create(
            tmp_path, "app", "monolith",
            modules=["core", "auth", "profiles"], want_auth=False,
        )
        frontend = proj / "frontend"
        # profiles alone still activates routing (it carries nav entries).
        routes_tsx = (frontend / "src" / "routes.tsx").read_text()
        assert '"/login"' not in routes_tsx
        assert "AuthPanel" not in routes_tsx
        assert not (frontend / "src" / "ProtectedRoute.tsx").exists()
        nav_ts = (frontend / "src" / "nav.generated.ts").read_text()
        assert "auth.login" not in nav_ts
        assert "auth.security" not in nav_ts
        # the runtime is still wired (unrelated to routing) — existing
        # modules.tsx behavior, untouched by this task.
        modules_tsx = (frontend / "src" / "modules.tsx").read_text()
        assert "AuthProvider" in modules_tsx

    def test_router_deps_pinned_from_live_npm_v7_range(self, tmp_path):
        """`react-router` is pinned to the latest v7 release (verified via
        `npm view "react-router@^7" version`, NOT the plain `npm view
        react-router version` dist-tag — that's a v8 major, incompatible
        with @stapel/shell-react's own peerDependencies range) — see
        create_project.FRONTEND_ROUTER_DEPS's own comment."""
        import json

        from stapel_tools.create_project import FRONTEND_ROUTER_DEPS

        proj = _create(tmp_path, "app", "monolith", modules=["core"], want_landing=True)
        pkg = json.loads((proj / "frontend" / "package.json").read_text())
        # The MAJOR is the assertion; the patch is read off the constant
        # rather than retyped here, because a second copy of a pin is a second
        # place to forget when the pin moves — and this test would then be
        # red for a reason that has nothing to do with what it guards.
        assert pkg["dependencies"]["react-router"] == "^" + FRONTEND_ROUTER_DEPS["react-router"]
        assert pkg["dependencies"]["react-router"].startswith("^7.")


class TestGeneratedCeleryWiring:
    """Found by the e2e live circle: without config/celery.py every
    @shared_task in an installed lib binds to Celery's default UNCONFIGURED
    app (amqp://localhost) — stapel-auth's login-notification .delay() then
    500s the login. The scaffold now wires the standard app + eager local
    execution."""

    def test_service_gets_celery_app_and_config_init_import(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        celery_py = (proj / "svc-app" / "config" / "celery.py").read_text()
        assert 'Celery("app")' in celery_py
        assert 'config_from_object("django.conf:settings", namespace="CELERY")' in celery_py
        init = (proj / "svc-app" / "config" / "__init__.py").read_text()
        assert "from .celery import app as celery_app" in init

    def test_dev_settings_run_tasks_eagerly(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        dev = (proj / "svc-app" / "config" / "settings" / "dev.py").read_text()
        assert "CELERY_TASK_ALWAYS_EAGER = True" in dev
        assert "CELERY_TASK_EAGER_PROPAGATES = False" in dev

    def test_minimal_gets_the_same_wiring_brokerless(self, tmp_path):
        proj = _create(tmp_path, "app", "minimal")
        assert (proj / "config" / "celery.py").exists()
        celery_py = (proj / "config" / "celery.py").read_text()
        assert "config.settings" in celery_py
        assert "config.settings.base" not in celery_py
        settings = (proj / "config" / "settings.py").read_text()
        assert "CELERY_TASK_ALWAYS_EAGER = True" in settings


class TestCdnFrontendAutoWiring:
    """cdn auto-wiring (cdn-scaffold-autowire.md) — the frontend half of the
    4-point recipe generalized from the hand-applied meettoday avatar fix: a
    stopgap `cdn`-keyed client registered in the generated
    `<StapelProvider clients={{...}}>` (no dedicated `@stapel/cdn-react`
    pair exists yet — promoting this into one is a separate follow-up), and
    — when profiles-react is ALSO wired — an `avatarUrlFor(ref)` helper
    (frontend/src/lib/cdn.ts) passed into `ProfileSettings`. nginx's
    `client_max_body_size`/`/media/`/`/cdn/api/` proxy and the Vite dev
    proxy are already GENERATED unconditionally per-lib (STAPEL_LIBS'
    default url_prefix — TestGenerativeBackendPrefixes) — asserted again
    here as this feature's own numeric gate, not because they needed new
    code."""

    def test_cdn_client_is_the_real_pair_now_that_one_exists(self, tmp_path):
        """`@stapel/cdn-react` 0.2.0 exists, so selecting the cdn backend
        wires the REAL pair — its own runtime and its own client under the
        `cdn` key. The old stopgap (`cdn: <other>Runtime.client`, from the
        months when no pair existed) must NOT also be emitted: it would
        shadow the real client with a borrowed one."""
        proj = _create(tmp_path, "app", "monolith", modules=["core", "profiles", "cdn"])
        modules_tsx = (proj / "frontend" / "src" / "modules.tsx").read_text()
        assert 'createCdnRuntime({ baseUrl: "/cdn/api/v1/" })' in modules_tsx
        assert "<CdnProvider runtime={cdnRuntime}>" in modules_tsx
        # The borrowed client is gone.
        assert "cdn: profilesRuntime.client," not in modules_tsx
        # Registry order makes cdn the provider's DEFAULT client here, and
        # `useStapelClient("cdn")` falls through to the default when no
        # per-module override is present (core config.tsx:55-62) — so the seam
        # resolves to the real cdn client either way.
        assert "client={cdnRuntime.client}" in modules_tsx

    def test_l0_pair_contributes_a_catalogue_and_no_provider(self, tmp_path):
        """`@stapel/attributes-react` is L0 — stapel-attributes has no HTTP
        surface at all, so the pair ships no client, no queries and no
        provider. The registry says so by carrying no `create_runtime`, and
        the generated registry must read that rather than crash on it (it
        used to `KeyError` the moment such a pair was registered)."""
        proj = _create(tmp_path, "app", "monolith", modules=["core", "attributes", "profiles"])
        modules_tsx = (proj / "frontend" / "src" / "modules.tsx").read_text()
        assert "registerAttributesI18n(i18n);" in modules_tsx
        assert "attributesRuntime" not in modules_tsx
        assert "AttributesProvider" not in modules_tsx

    def test_avatar_url_for_helper_written_and_wired_into_profile_settings(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", modules=["core", "profiles", "cdn"])
        cdn_lib = proj / "frontend" / "src" / "lib" / "cdn.ts"
        assert cdn_lib.exists()
        content = cdn_lib.read_text()
        assert "export function avatarUrlFor(ref: string): string {" in content
        assert '`/media/app/${type}/${hash}/160w.webp`' in content
        # profiles carries a nav mirror, so this is the LIVE route (not
        # modules.tsx's ModulesPanel, which is unreachable once routing is
        # active — see render_routes_tsx's own docstring).
        routes = (proj / "frontend" / "src" / "routes.tsx").read_text()
        assert 'import { avatarUrlFor } from "./lib/cdn.js";' in routes
        assert "<ProfileSettings avatarUrlFor={avatarUrlFor} />" in routes

    def test_cdn_without_profiles_wires_the_pair_but_no_avatar_helper(self, tmp_path):
        """cdn selected alongside a react-paired module OTHER than profiles:
        the cdn pair wires normally (any consumer can call
        `useStapelClient("cdn")`), but there is no ProfileSettings to wire an
        avatarUrlFor prop into, so lib/cdn.ts is never written."""
        proj = _create(tmp_path, "app", "monolith", modules=["core", "billing", "cdn"])
        modules_tsx = (proj / "frontend" / "src" / "modules.tsx").read_text()
        assert 'createCdnRuntime({ baseUrl: "/cdn/api/v1/" })' in modules_tsx
        assert "<CdnProvider runtime={cdnRuntime}>" in modules_tsx
        assert "cdn: billingRuntime.client," not in modules_tsx
        assert not (proj / "frontend" / "src" / "lib" / "cdn.ts").exists()

    def test_stapel_image_dep_added_when_media_wired(self, tmp_path):
        # profiles (avatar_image) OR cdn wires a StapelImage read path → the
        # <Image> renderer must be a dependency (AGENTS.md §7).
        proj = _create(tmp_path, "app", "monolith", modules=["core", "profiles"])
        pkg = json.loads((proj / "frontend" / "package.json").read_text())
        assert "@stapel/image" in pkg["dependencies"]

    def test_no_stapel_image_dep_without_media(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", modules=["core", "billing"])
        pkg = json.loads((proj / "frontend" / "package.json").read_text())
        assert "@stapel/image" not in pkg.get("dependencies", {})

    def test_agents_md_carries_media_render_rule_when_media(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", modules=["core", "profiles"])
        agents = (proj / "AGENTS.md").read_text()
        assert "Rendering images" in agents
        assert "<Image meta=" in agents
        assert "never a bare `<img src>`" in agents.replace("NEVER", "never").lower() or \
            "never render an image ref with a bare" in agents.lower()
        assert "preview_b64" in agents

    def test_agents_md_omits_media_rule_without_media(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", modules=["core", "billing"])
        agents = (proj / "AGENTS.md").read_text()
        assert "Rendering images" not in agents

    def test_nginx_and_vite_proxy_cdn_with_raised_body_size(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", modules=["core", "profiles", "cdn"])
        prod_nginx = (proj / "service-configs" / "nginx" / "nginx.conf").read_text()
        local_nginx = (
            proj / "service-configs" / "nginx-local" / "default.conf.template"
        ).read_text()
        vite = (proj / "frontend" / "vite.config.ts").read_text()
        for text in (prod_nginx, local_nginx):
            assert "client_max_body_size 50m;" in text
            assert "location /media/" in text
        assert "location ^~ /cdn/api/" in prod_nginx
        assert "location ^~ /cdn/api/" in local_nginx
        assert '"/cdn/api/"' in vite
        assert '"/media/"' in vite

    def test_without_cdn_frontend_is_byte_identical_to_pre_autowire_output(self, tmp_path):
        """Regression: profiles alone (no cdn) must not register a `cdn`
        client, must not write lib/cdn.ts, and ProfileSettings mounts bare
        (no avatarUrlFor prop) — the exact pre-autowire scaffold."""
        proj = _create(tmp_path, "app", "monolith", modules=["core", "profiles"])
        modules_tsx = (proj / "frontend" / "src" / "modules.tsx").read_text()
        assert "cdn" not in modules_tsx
        assert not (proj / "frontend" / "src" / "lib").exists()
        routes = (proj / "frontend" / "src" / "routes.tsx").read_text()
        assert "avatarUrlFor" not in routes
        assert "<ProfileSettings />" in routes
        prod_nginx = (proj / "service-configs" / "nginx" / "nginx.conf").read_text()
        assert "/cdn/" not in prod_nginx


def _nginx_locations(conf: str) -> list[tuple[str, str]]:
    """Split an nginx server block into its ``location`` blocks.

    Returns ``(header, body)`` pairs with ``#`` comments stripped, so the
    assertions below read only DIRECTIVES — never the prose in a comment
    that happens to quote a bad directive.
    """
    stripped = "\n".join(line.split("#", 1)[0] for line in conf.splitlines())
    blocks, depth, header, body = [], 0, None, []
    for line in stripped.splitlines():
        if header is None:
            if line.strip().startswith("location ") and line.rstrip().endswith("{"):
                header, depth, body = line.strip(), 1, []
            continue
        depth += line.count("{") - line.count("}")
        if depth == 0:
            blocks.append((header, "\n".join(body)))
            header = None
        else:
            body.append(line)
    return blocks


class TestSpaCacheCanon:
    """Owner directive (2026-07-26): the thin entry document is the ONLY
    unhashed file, so it must never be cached; hashed build artifacts are
    content-addressed, so they must be cached immutably for a year.

    Live incident this encodes: on the app.ironmemo.com stand the entry
    document carried BOTH ``expires 1d`` and an explicit
    ``add_header Cache-Control "public, must-revalidate"`` — nginx emits both
    headers, the browser takes max-age=86400, and a freshly deployed frontend
    fix stayed invisible for up to 24h (its verification read the stale
    bundle and wrongly reported the fix as failed).
    """

    def _prod_conf(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        return (proj / "service-configs" / "nginx" / "nginx.conf").read_text()

    def _entry_location(self, conf):
        for header, body in _nginx_locations(conf):
            if "/index.html" in body:
                return header, body
        raise AssertionError("no location serves the SPA entry document")

    def test_entry_document_is_never_cached(self, tmp_path):
        conf = self._prod_conf(tmp_path)
        _, body = self._entry_location(conf)
        assert "expires off;" in body
        cache_control = re.search(
            r'add_header\s+Cache-Control\s+"([^"]+)"', body
        )
        assert cache_control, "entry document has no explicit Cache-Control"
        value = cache_control.group(1)
        assert "no-cache" in value
        assert "must-revalidate" in value
        # no positive freshness lifetime may sneak in next to no-cache
        assert not re.search(r"max-age=[1-9]", value), value
        assert "immutable" not in value

    def test_hashed_assets_are_immutable_for_a_year(self, tmp_path):
        conf = self._prod_conf(tmp_path)
        hashed = [
            (h, b)
            for h, b in _nginx_locations(conf)
            if "max-age=31536000" in b
        ]
        assert len(hashed) == 1, "expected exactly one immutable-asset location"
        header, body = hashed[0]
        assert "immutable" in body
        assert "expires off;" in body

        # The location must actually match vite's hashed build output and
        # must NOT swallow the unhashed entry document.
        pattern = re.match(r"location\s+~\*?\s+(\S+)\s*\{", header)
        assert pattern, f"asset location is not a regex location: {header}"
        rx = re.compile(pattern.group(1), re.IGNORECASE)
        for hit in (
            "/assets/index-DQ9k2Zx1.js",
            "/assets/index-B7hUq0.css",
            "/assets/inter-latin-a91f.woff2",
            "/assets/index-DQ9k2Zx1.js.map",
        ):
            assert rx.search(hit), hit
        for miss in ("/index.html", "/", "/app/api/health/", "/favicon-unhashed"):
            assert not rx.search(miss), miss

    def test_no_location_mixes_expires_with_an_explicit_cache_control(self, tmp_path):
        """THE defect. `expires <time>` makes nginx emit its own
        Cache-Control; an explicit add_header does NOT replace it, so the
        response carries two conflicting headers and the browser keeps the
        permissive one. Only `expires off;` (nginx adds nothing) may sit
        next to an explicit Cache-Control.
        """
        conf = self._prod_conf(tmp_path)
        for header, body in _nginx_locations(conf):
            expires = re.findall(r"^\s*expires\s+(\S+?);", body, re.MULTILINE)
            has_cc = "add_header Cache-Control" in body
            for value in expires:
                assert not (has_cc and value != "off"), (
                    f"{header}: `expires {value};` next to an explicit "
                    "Cache-Control add_header — nginx emits BOTH headers "
                    "(the app.ironmemo.com stale-bundle incident)"
                )

    def test_cache_headers_are_self_contained_per_location(self, tmp_path):
        """`add_header` does not merge: a location declaring any add_header
        REPLACES every header inherited from the server block. The generated
        server block must therefore declare no add_header of its own (else
        the two cache locations would silently drop it)."""
        conf = self._prod_conf(tmp_path)
        in_locations = sum(
            body.count("add_header") for _, body in _nginx_locations(conf)
        )
        stripped = "\n".join(line.split("#", 1)[0] for line in conf.splitlines())
        assert stripped.count("add_header") == in_locations

    def test_dev_template_stays_cache_directive_free(self, tmp_path):
        """Dev serves the frontend by PROXYING to the Vite dev server, which
        sets its own no-cache/HMR headers; the prod canon does not apply and
        must not be half-copied here (a stray `expires` would be the same
        defect in a different file)."""
        proj = _create(tmp_path, "app", "monolith")
        tmpl = (
            proj / "service-configs" / "nginx-local" / "default.conf.template"
        ).read_text()
        stripped = "\n".join(line.split("#", 1)[0] for line in tmpl.splitlines())
        assert "expires" not in stripped
        assert "Cache-Control" not in stripped


class TestSplitRepoFrontendDelivery:
    """The microservice topology had NO frontend delivery at all.

    Its nginx mounted only `./service-configs/nginx` — no frontend volume, no
    writer, no gate. The canon lived in the monolith template and did not
    travel. Measured live on ironmemo (2026-08-05): nginx served
    `root /frontend-react`, a bind onto a host directory that both
    `scripts/deploy_stand.sh` and `.gitlab-ci.yml` explicitly EXCLUDED from
    rsync, so no build ever landed there. For months that read as "the
    frontend does not update" and was repeatedly misdiagnosed as caching.
    """

    def test_micro_project_declares_a_frontend_and_a_writer(self, tmp_path):
        proj = _create(tmp_path, "app", "microservices")
        base = (proj / "docker-compose.base.yml").read_text()
        prod = (proj / "docker-compose.yml").read_text()
        # nginx has something to serve...
        assert "frontend-dist:/usr/share/nginx/html:ro" in base
        # ...someone fills it...
        assert "frontend-build:" in prod
        assert "- frontend-dist:/output" in prod
        # ...and nginx does not start before they have.
        assert "condition: service_completed_successfully" in base

    def test_micro_frontend_is_pinned_by_env_not_built_here(self, tmp_path):
        """A microservice project's frontend is a SEPARATE repository, so
        compose cannot build it — it pulls a published dist-carrier image
        pinned in the env template. The pin lives in git precisely because
        deploy regenerates .env from the template on every run: a tag edited
        on the stand disappears without a word."""
        proj = _create(tmp_path, "app", "microservices")
        prod = (proj / "docker-compose.yml").read_text()
        env = (proj / ".env.example").read_text()
        assert "${FRONTEND_IMAGE}:${FRONTEND_TAG}" in prod
        assert "\n    build:\n" not in prod  # nothing to build: another repo owns it
        assert "FRONTEND_IMAGE=" in env
        assert "FRONTEND_TAG=" in env

    def test_micro_compose_parses(self, tmp_path):
        proj = _create(tmp_path, "app", "microservices")
        data = _docker_compose_config(proj, "docker-compose.yml")
        if data is None:
            return
        assert "frontend-build" in data["services"]
        assert data["services"]["frontend-build"]["restart"] == "no"
        assert "frontend-dist" in data["volumes"]


class TestFrontendAxis:
    """`delivery` is a configuration axis, not three code paths."""

    def test_host_delivery_has_no_writer_and_says_so(self):
        from stapel_tools._compose_templates import (
            MONOLITH_COMPOSE_BASE,
            MONOLITH_COMPOSE_PROD,
            Frontend,
            render_compose_base,
            render_frontend_delivery,
        )

        f = Frontend(delivery="host", host_path="./frontend-react")
        base = render_compose_base(MONOLITH_COMPOSE_BASE, "none", "none", [f])
        prod = render_frontend_delivery(MONOLITH_COMPOSE_PROD, [f])
        assert "- ./frontend-react:/usr/share/nginx/html:ro" in base
        # Nothing to wait for — and the comment says why, rather than the file
        # quietly looking complete.
        assert "depends_on: []" in base
        assert "delivery=host" in prod
        assert "FED001" in prod

    def test_host_delivery_without_a_source_is_refused(self):
        from stapel_tools._compose_templates import Frontend

        with pytest.raises(ValueError, match="host_path"):
            Frontend(delivery="host")

    def test_unknown_delivery_is_refused(self):
        from stapel_tools._compose_templates import Frontend

        with pytest.raises(ValueError, match="delivery must be one of"):
            Frontend(delivery="scp")

    def test_second_frontend_gets_its_own_volume_prefix_and_env(self):
        from stapel_tools._compose_templates import (
            MICRO_COMPOSE_PROD,
            NGINX_CONF,
            Frontend,
            render_frontend_delivery,
            render_nginx_conf,
        )

        fronts = [Frontend(delivery="image"), Frontend(name="kmp", mount="/kmp", delivery="image")]
        prod = render_frontend_delivery(MICRO_COMPOSE_PROD, fronts)
        conf = render_nginx_conf(NGINX_CONF, fronts)
        assert "${FRONTEND_KMP_IMAGE}:${FRONTEND_KMP_TAG}" in prod
        assert "- frontend-dist-kmp:/output" in prod
        # alias, not root: the URL prefix is not part of the on-disk path.
        assert "alias /usr/share/nginx/html-kmp/current/;" in conf

    def test_a_project_with_no_root_frontend_is_refused(self):
        from stapel_tools._compose_templates import (
            NGINX_CONF,
            Frontend,
            render_nginx_conf,
        )

        with pytest.raises(ValueError, match="no frontend mounted at"):
            render_nginx_conf(NGINX_CONF, [Frontend(name="kmp", mount="/kmp")])


class TestVocabulariesPairAndTheVocabularyClientSeam:
    """`@stapel/vocabularies-react` 0.1.0 was on npm and absent from
    FRONTEND_REACT_LIBS, so no generated project could install it — and the
    seam it exists for (attributes-v2 §3.4: this pair's
    `createVocabularyClient` handed to `@stapel/attributes-react`'s
    `VocabularyClientProvider`) sat OUTSIDE the generated provider nesting.
    Every storefront that wanted a `ref_select` editor hand-wired those four
    lines into a GENERATED file, which is a merge conflict on the next
    re-generation and a silent regression when it is resolved the wrong way.

    The join is a property of the CONTAINER, and the container is what this
    repo writes — so it is declared once, in the registry's `seam` key, and
    emitted from there.
    """

    def test_the_pair_is_registered_with_its_published_version(self):
        from stapel_tools.create_project import FRONTEND_REACT_LIBS

        entry = FRONTEND_REACT_LIBS["vocabularies"]
        assert entry["package"] == "@stapel/vocabularies-react"
        assert entry["create_runtime"] == "createVocabulariesRuntime"
        assert entry["provider"] == "VocabulariesProvider"
        assert entry["register_i18n"] == "registerVocabulariesI18n"

    def test_the_pair_claims_no_nav_surface_because_it_publishes_none(self):
        """Not an oversight: the pair ships no `nav-manifest.json` at all —
        its term select is drawn inside somebody else's editor, exactly like
        cdn's uploader and reviews' stars. The drift gate's own rule for that
        case is "claims nothing, publishes nothing = in sync"; a mirror here
        would make `check_nav_manifest_sync` red, not greener."""
        from stapel_tools.create_project import FRONTEND_REACT_LIBS

        assert "nav" not in FRONTEND_REACT_LIBS["vocabularies"]
        assert "default_component" not in FRONTEND_REACT_LIBS["vocabularies"]

    def test_the_seam_declares_both_ends_and_the_pair_that_owns_the_provider(self):
        from stapel_tools.create_project import FRONTEND_REACT_LIBS

        seam = FRONTEND_REACT_LIBS["vocabularies"]["seam"]
        assert seam["factory"] == "createVocabularyClient"
        assert seam["provider"] == "VocabularyClientProvider"
        assert seam["provider_pair"] == "attributes"
        assert (
            seam["provider_package"]
            == FRONTEND_REACT_LIBS[seam["provider_pair"]]["package"]
        )
        assert seam["base_url"] == "/vocabularies/api/v1/"

    def test_a_monolith_selecting_it_installs_the_pair_and_reserves_its_api(
        self, tmp_path
    ):
        from stapel_tools.create_project import FRONTEND_REACT_LIBS

        proj = _create(
            tmp_path, "app", "monolith", modules=["attributes", "vocabularies"]
        )
        pkg = json.loads((proj / "frontend" / "package.json").read_text())
        # Read off the registry, never typed: a pin bump must not need a
        # second edit here to stay honest about what it installed.
        assert (
            pkg["dependencies"]["@stapel/vocabularies-react"]
            == f"^{FRONTEND_REACT_LIBS['vocabularies']['version']}"
        )
        reserved = json.loads((proj / "reserved-paths.json").read_text())
        assert "/vocabularies/api" in reserved["reservedPathPrefixes"]
        # The bare root is the SPA's, as for every other module.
        assert "/vocabularies" not in reserved["reservedPathPrefixes"]

    def test_the_seam_is_emitted_inside_the_generated_nesting(self, tmp_path):
        proj = _create(
            tmp_path, "app", "monolith", modules=["attributes", "vocabularies"]
        )
        modules_tsx = (proj / "frontend" / "src" / "modules.tsx").read_text()
        # The provider comes from the DECLARING pair's package, merged into
        # the import that package already had — not a second import line.
        assert (
            'import { registerAttributesI18n, VocabularyClientProvider } '
            'from "@stapel/attributes-react";' in modules_tsx
        )
        assert "createVocabularyClient" in modules_tsx
        assert (
            'const vocabulariesClient = createVocabularyClient('
            '{ baseUrl: "/vocabularies/api/v1/" });' in modules_tsx
        )
        # Inside the nesting, wrapping children — not beside it.
        assert "<VocabularyClientProvider value={vocabulariesClient}>" in modules_tsx
        assert "</VocabularyClientProvider>" in modules_tsx
        opened = modules_tsx.index("<VocabularyClientProvider")
        assert modules_tsx.index("<VocabulariesProvider") < opened
        assert opened < modules_tsx.index("{children}")

    def test_a_monolith_cannot_select_it_without_attributes(self, tmp_path):
        """The backend registry settles this half: `stapel-vocabularies`
        floors itself at `stapel-attributes>=0.5` (the `VocabularyResolver`
        protocol lives in L1), so `requires` pulls attributes in and the seam
        is always emittable in a monolith."""
        from stapel_tools.create_project import _expand_with_requires

        assert _expand_with_requires(["vocabularies"]) == [
            "attributes", "vocabularies",
        ]
        proj = _create(tmp_path, "app", "monolith", modules=["vocabularies"])
        modules_tsx = (proj / "frontend" / "src" / "modules.tsx").read_text()
        assert "<VocabularyClientProvider value={vocabulariesClient}>" in modules_tsx

    def test_a_container_without_attributes_gets_the_runtime_and_no_seam(self):
        """A public container names its pairs DIRECTLY, with no backend
        `requires` to expand — so this selection is reachable there. The
        provider is `@stapel/attributes-react`'s export, and emitting it for
        a container that does not install that package is a build error, so
        the seam is simply not written. The pair's own runtime and provider
        still are: a client nothing reads is wiring, not a broken screen."""
        from stapel_tools import _frontend_templates as F
        from stapel_tools.create_project import FRONTEND_REACT_LIBS

        entries = [
            {"key": k, **FRONTEND_REACT_LIBS[k]} for k in ("auth", "vocabularies")
        ]
        assert F.seam_pairs(entries) == []
        modules_tsx = F.render_public_modules_tsx(entries)
        assert "createVocabulariesRuntime" in modules_tsx
        assert "<VocabulariesProvider runtime={vocabulariesRuntime}>" in modules_tsx
        assert "VocabularyClientProvider" not in modules_tsx
        assert "createVocabularyClient" not in modules_tsx


class TestGeoModerationCurrenciesOnboarding:
    """Three pairs that were PUBLISHED on npm and absent from
    FRONTEND_REACT_LIBS, so `--modules geo` (or moderation, or currencies)
    scaffolded a backend the frontend could not see — and two storefront
    presets carried hand-written `pending` reasons saying exactly that.

    Each is registered off `npm view`, never a typed number, and each brings a
    fact the registry had no shape for until now:

      * moderation — a four-entry nav manifest whose screens need
        NAV_ENTRY_MOUNTS rows.
      * geo and currencies — a `baseUrl` that ends at the module MOUNT rather
        than at `/<key>/api/v1/`, because both spell the `api/v1/` half in
        their own api layer.
    """

    PAIRS = ("geo", "moderation", "currencies")

    def test_all_three_are_registered(self):
        from stapel_tools.create_project import FRONTEND_REACT_LIBS

        for key in self.PAIRS:
            assert key in FRONTEND_REACT_LIBS, key
            entry = FRONTEND_REACT_LIBS[key]
            assert entry["package"] == f"@stapel/{key}-react"
            assert entry["create_runtime"] == (
                f"create{key.capitalize()}Runtime"
                if key != "currencies"
                else "createCurrenciesRuntime"
            )
            assert entry["provider"].endswith("Provider")
            assert entry["register_i18n"].startswith("register")

    def test_the_backend_registry_already_had_all_three(self):
        """The whole defect in one assertion: `--modules` drives BOTH sides
        by construction, and the backend halves had been selectable the whole
        time."""
        from stapel_tools.create_project import FRONTEND_REACT_LIBS, STAPEL_LIBS

        for key in self.PAIRS:
            assert key in STAPEL_LIBS, key
            assert key in FRONTEND_REACT_LIBS, key

    def test_only_moderation_claims_a_nav_surface(self):
        """geo is one FIELD inside another pair's form and currencies is a
        formatter — neither publishes entries (geo publishes no manifest at
        all; currencies publishes an empty one), which is the sync gate's
        "claims nothing, publishes nothing" case."""
        from stapel_tools.create_project import FRONTEND_REACT_LIBS

        assert FRONTEND_REACT_LIBS["geo"].get("nav") is None
        assert FRONTEND_REACT_LIBS["currencies"].get("nav") is None
        assert len(FRONTEND_REACT_LIBS["moderation"]["nav"]) == 4

    def test_moderation_entries_hang_off_the_roots_that_resolve_them(self):
        from stapel_tools import _frontend_templates as F
        from stapel_tools.create_project import FRONTEND_REACT_LIBS

        nav = FRONTEND_REACT_LIBS["moderation"]["nav"]
        by_id = {e["id"]: e for e in nav}
        assert set(by_id) == {
            "moderation.policy",
            "account.appeals",
            "admin.moderation",
            "admin.moderation-appeals",
        }
        # The one PUBLIC screen: a DSA disclosure a signed-out visitor reads.
        assert by_id["moderation.policy"]["surface"] == "public"
        assert by_id["moderation.policy"]["requiresAuth"] is False
        assert by_id["account.appeals"]["placement"]["parentId"] == "account.root"
        for key in ("admin.moderation", "admin.moderation-appeals"):
            assert by_id[key]["placement"]["parentId"] == "admin.root"
        # Generation refuses an unknown icon or an undeclared parent rather
        # than degrading — so the mirror has to pass its own contract.
        F.validate_nav_entries(nav)

    def test_every_moderation_screen_has_a_mount_row_and_needs_no_prop(self):
        """All four `/default` components take zero required props (read off
        the pair's own `src/default/*.tsx`), so each mounts directly — no
        route params, no container-supplied slot, no placeholder."""
        from stapel_tools import _frontend_templates as F
        from stapel_tools.create_project import FRONTEND_REACT_LIBS

        for entry in FRONTEND_REACT_LIBS["moderation"]["nav"]:
            mount = F.NAV_ENTRY_MOUNTS.get(entry["id"])
            assert mount == {}, entry["id"]

    def test_the_mount_table_still_covers_every_registered_entry(self):
        """The safety net is a placeholder, not the plan: a newly mirrored
        entry with no row shows up here rather than as a blank page."""
        from stapel_tools import _frontend_templates as F
        from stapel_tools.create_project import FRONTEND_REACT_LIBS

        for pair in FRONTEND_REACT_LIBS.values():
            for entry in pair.get("nav", ()):
                assert entry["id"] in F.NAV_ENTRY_MOUNTS, entry["id"]

    def test_geo_and_currencies_take_the_module_mount_as_their_baseUrl(self):
        """`api/geoApi.ts` spells `MAP_CONFIG_PATH = "api/v1/map/config"` and
        `api/currenciesApi.ts` spells `CURRENCIES_LIST_PATH = "api/v1/"`, so
        the runtime's `baseUrl` ends at the MOUNT. The registry's uniform
        `/<key>/api/v1/` would double the prefix — a 404 behind a screen that
        looks perfectly wired."""
        from stapel_tools import _frontend_templates as F
        from stapel_tools.create_project import FRONTEND_REACT_LIBS

        for key, expected in (("geo", "/geo/"), ("currencies", "/currencies/")):
            entry = {"key": key, **FRONTEND_REACT_LIBS[key]}
            assert F.pair_base_url(entry) == expected

    def test_every_other_pair_keeps_the_uniform_versioned_prefix(self):
        """The override is a REGISTRY fact declared by the pair that departs
        from the default, never a branch in the emitter — so the majority is
        untouched and a reader can see which two are different."""
        from stapel_tools import _frontend_templates as F
        from stapel_tools.create_project import FRONTEND_REACT_LIBS

        overrides = {
            k for k, v in FRONTEND_REACT_LIBS.items() if v.get("base_url")
        }
        assert overrides == {"geo", "currencies"}
        for key, info in FRONTEND_REACT_LIBS.items():
            if key in overrides:
                continue
            assert F.pair_base_url({"key": key, **info}) == f"/{key}/api/v1/"

    def test_a_monolith_selecting_them_wires_the_runtimes_it_can_reach(
        self, tmp_path
    ):
        import json

        proj = _create(
            tmp_path, "app", "monolith",
            modules=["geo", "moderation", "currencies"],
        )
        modules_tsx = (proj / "frontend" / "src" / "modules.tsx").read_text()
        assert 'const geoRuntime = createGeoRuntime({ baseUrl: "/geo/" });' in modules_tsx
        assert (
            'const currenciesRuntime = createCurrenciesRuntime'
            '({ baseUrl: "/currencies/" });' in modules_tsx
        )
        assert (
            'const moderationRuntime = createModerationRuntime'
            '({ baseUrl: "/moderation/api/v1/" });' in modules_tsx
        )
        pkg = json.loads((proj / "frontend" / "package.json").read_text())
        from stapel_tools.create_project import FRONTEND_REACT_LIBS

        for key in self.PAIRS:
            assert (
                pkg["dependencies"][f"@stapel/{key}-react"]
                == f"^{FRONTEND_REACT_LIBS[key]['version']}"
            )

    def test_the_moderation_screens_become_routes_in_the_monolith(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", modules=["auth", "moderation"])
        routes_tsx = (proj / "frontend" / "src" / "routes.tsx").read_text()
        for component in ("PolicyDisclosurePane", "AppealPanel", "ModerationQueue",
                          "AppealsQueue"):
            assert component in routes_tsx, component
        # The two admin screens arrive through the container-owned admin root,
        # which means they are also behind the container's own staff gate.
        assert "<AdminGate><ModerationQueue /></AdminGate>" in routes_tsx


class TestTheShellCatalogueIsRegisteredInTheMonolithToo:
    """The storefront container registered `registerShellI18n` from the day it
    was written; the monolith never did. Every string the chrome draws —
    "Open menu", the admin section's staff-only reason, and from shell 0.10.0
    the four `shell.theme.*` labels of the switch it now puts at the foot of
    the Sider — therefore rendered as a raw key on a surface every route
    shares.
    """

    def test_a_nav_bearing_monolith_registers_the_shell_catalogue(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", modules=["auth", "profiles"])
        modules_tsx = (proj / "frontend" / "src" / "modules.tsx").read_text()
        assert 'import { registerShellI18n } from "@stapel/shell-react";' in modules_tsx
        assert "registerShellI18n(i18n);" in modules_tsx
        # The container's own copy still goes on LAST — the documented order.
        assert modules_tsx.index("registerShellI18n(i18n);") < modules_tsx.index(
            'i18n.registerBundle("en"'
        )

    def test_a_container_with_no_shell_does_not_import_one(self, tmp_path):
        """`@stapel/shell-react` is only a dependency when something mounts
        it. Registering a catalogue from a package that is not installed is a
        build error, not a missing string."""
        import json

        proj = _create(tmp_path, "app", "monolith", modules=["core", "cdn"])
        pkg = json.loads((proj / "frontend" / "package.json").read_text())
        assert "@stapel/shell-react" not in pkg["dependencies"]
        modules_tsx = (proj / "frontend" / "src" / "modules.tsx").read_text()
        assert "shell-react" not in modules_tsx


class TestTheMonolithHonoursTheMountTable:
    """`NAV_ENTRY_MOUNTS` decides how a nav entry is mounted, and until now
    exactly one of the two containers read it. The public storefront ran the
    table through `public_mount_plan`; the monolith mounted `<{Component} />`
    for every entry, bare.

    Nine of the fleet's published screens REQUIRE a prop, so a generated
    monolith that selected the pair did not typecheck. Measured, not
    remembered: `tsc -b` over a container with every selectable pair reported
    exactly nine `TS2741 Property '<x>' is missing` errors —
    `InviteAcceptPage.token`, `ListingDetailPane.id`,
    `ListingComposerPage.features`, `SharedRecordingView.linkToken`,
    `PublicProfilePage.userId`, `RecordingDetailPane.recordingId`,
    `PushSettingsPane.getToken`, `FormBuilderPane.formId`,
    `ResponsesPane.formId` — every one already answered by a row this
    renderer never read.
    """

    ALL_PAIRS = ("auth", "notifications", "profiles", "listings", "categories",
                 "cdn", "recordings", "forms", "workspaces", "attributes")

    def _routes(self, tmp_path, modules):
        proj = _create(tmp_path, "app", "monolith", modules=list(modules))
        return proj, (proj / "frontend" / "src" / "routes.tsx").read_text()

    def test_a_route_parameter_screen_goes_through_a_wrapper(self, tmp_path):
        proj, src = self._routes(tmp_path, ["auth", "profiles"])
        assert '{ path: "/u/:userId", element: <PublicProfilePageRoute /> },' in src
        assert "<PublicProfilePage />" not in src
        wrapper = (
            proj / "frontend" / "src" / "pages" / "PublicProfilePageRoute.tsx"
        ).read_text()
        assert 'import { useParams } from "react-router";' in wrapper
        assert "const userId = rawUserId;" in wrapper
        assert '<RouteParamProblem param="userId" />' in wrapper
        assert "<PublicProfilePage userId={userId} />" in wrapper

    def test_a_prop_no_container_can_mint_gets_the_named_placeholder(
        self, tmp_path
    ):
        """`PushSettingsPane` needs `getToken()` — a push subscription token
        minted by the host's OWN service worker. A generated container has
        none, and a stub returning a fake token registers a device that can
        never be delivered to."""
        proj, src = self._routes(tmp_path, ["auth", "notifications", "profiles"])
        assert 'entryId="notifications.push"' in src
        assert 'missing={["getToken"]}' in src
        assert "<PushSettingsPane />" not in src
        assert (proj / "frontend" / "src" / "NavPlaceholder.tsx").is_file()

    def test_the_placeholder_copy_is_registered_not_left_as_raw_keys(
        self, tmp_path
    ):
        proj, _ = self._routes(tmp_path, ["auth", "notifications", "profiles"])
        assert (proj / "frontend" / "src" / "i18n" / "keys.ts").is_file()
        modules_tsx = (proj / "frontend" / "src" / "modules.tsx").read_text()
        assert (
            'import { registerStorefrontI18n } from "./i18n/keys.js";' in modules_tsx
        )
        assert 'registerStorefrontI18n(i18n, "en");' in modules_tsx

    def test_the_cross_pair_composer_is_composed_here_too(self, tmp_path):
        proj, src = self._routes(
            tmp_path, ["auth", "attributes", "categories", "cdn", "listings"]
        )
        assert "<ListingComposePage />" in src
        page = (
            proj / "frontend" / "src" / "pages" / "ListingComposePage.tsx"
        ).read_text()
        assert "useCategoryFeatures" in page
        assert "<MediaGalleryField bag={images} />" in page

    def test_a_composite_short_of_a_member_pair_names_the_gap(self, tmp_path):
        _, src = self._routes(tmp_path, ["auth", "attributes", "categories", "listings"])
        assert 'entryId="listings.compose"' in src
        assert "gallerySlot (needs @stapel/cdn-react)" in src
        assert "<ListingComposePage />" not in src

    def test_no_registered_pair_leaves_a_required_prop_unmounted(self, tmp_path):
        """The invariant behind the nine: for every screen the monolith
        routes, the element it emits is either a bare mount whose component
        needs nothing, a wrapper that supplies what the route knows, a local
        component, or a placeholder that NAMES what is missing. Never a bare
        mount of a component with a required prop."""
        from stapel_tools import _frontend_templates as F
        from stapel_tools.create_project import FRONTEND_REACT_LIBS, STAPEL_LIBS

        keys = [k for k in FRONTEND_REACT_LIBS if k in STAPEL_LIBS]
        entries = [{"key": k, **FRONTEND_REACT_LIBS[k]} for k in keys]
        pairs = F.nav_wired_pairs(entries, auth_wired=True)
        plan = F.build_nav_route_plan(pairs)
        mounts = F.monolith_mount_plan(plan, pairs=tuple(keys))
        for route in (*plan["absolute_routes"], *plan["app_children"]):
            entry = route["entry"]
            if entry.get("_local"):
                continue
            mount = F.NAV_ENTRY_MOUNTS[entry["id"]]
            element = mounts["elements"][entry["id"]]
            needs_more = bool(
                mount.get("route_params")
                or mount.get("adapter")
                or mount.get("container")
                or mount.get("option_props")
                or mount.get("composite")
            )
            if needs_more:
                assert element != f'<{entry["component"]["export"]} />', entry["id"]
