"""§57 owner directive — dev/prod docker-compose + nginx canon, entrypoint
canon, AGENTS.md, pre-commit README canon. Covers monolith (the "recommended"
/ only-wired-up-so-far project type; microservices/minimal frontend wiring
is an explicit follow-up, not built here — see AGENTS_MD's FRONTEND_SECTION
only rendering for has_frontend=True)."""
import shutil
import subprocess

import pytest
import yaml

from stapel_tools.create_project import create_project


def _create(tmp_path, name, project_type, modules=None, **kwargs):
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
    (directive: "compose валиден docker compose config'ом если docker
    доступен — иначе YAML-парс"); otherwise just parse every file as YAML."""
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
    """§68 Ф5 — the scaffold's `frontend/stapel.theme.json` is the neutral
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
            if name.isupper():
                assert "tokens-lib.mjs" not in getattr(F, name)
                assert "gen-tokens.mjs" not in getattr(F, name)
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
        assert "cp -r dist/." in dockerfile


class TestEntrypointCanon:
    """Item 3: migrate + createsuperuser through Django's OWN --noinput flow
    (no hand-rolled Python importing models) + collectstatic."""

    def test_bootstrap_sh_has_no_custom_python_or_model_imports(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        script = (proj / "svc-app" / "bootstrap.sh").read_text()
        assert "python manage.py migrate --noinput" in script
        assert "python manage.py createsuperuser --noinput" in script
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
        script = (proj / "svc-app" / "bootstrap.sh").read_text()
        assert 'DJANGO_SUPERUSER_USERNAME' in script
        assert 'DJANGO_SUPERUSER_PASSWORD' in script
        # tolerates "already exists" without failing the whole boot
        assert "createsuperuser --noinput || " in script


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
        assert react_deps == {"@stapel/billing-react", "@stapel/calendar-react"}
        # headless-only pairs (no `/default` skin) never pull antd in
        assert "antd" not in deps
        assert "@stapel/tokens-antd" not in deps
        # support deps every react pair needs are present
        assert "@stapel/core" in deps
        assert "@tanstack/react-query" in deps

    def test_monolith_with_antd_skinned_module_pulls_antd_bridge(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", modules=["core", "auth"])
        import json

        pkg = json.loads((proj / "frontend" / "package.json").read_text())
        deps = pkg["dependencies"]
        assert deps["@stapel/auth-react"] == "^0.5.2"
        assert "antd" in deps
        assert "@stapel/tokens-antd" in deps

    def test_generated_app_imports_resolve_to_deps_present_in_package_json(self, tmp_path):
        """The numeric compile-conceptually gate: parse every import in the
        generated App.tsx + modules.tsx and assert each non-relative spec's
        package is declared in package.json (dependencies or
        devDependencies) — proof the generated app wouldn't 501 on
        `npm install` with a missing/undeclared package. Modules with no
        FRONTEND_REACT_LIBS "nav" mirror (billing/calendar/recordings/
        workspaces) — a nav-bearing selection (auth/profiles/notifications)
        activates react-router routing instead of App.tsx (Ф1,
        TestFrontendNavWiring's own equivalent gate covers THAT shape)."""
        import json

        proj = _create(
            tmp_path, "app", "monolith",
            modules=["core", "billing", "calendar", "recordings", "workspaces"],
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

    def test_react_module_dep_versions_match_published_stapel_react_pins(self, tmp_path):
        """Exact version pins (§ verify against npm) — no invented packages,
        no stale/mismatched version strings."""
        import json

        expected = {
            "auth": "0.5.2", "billing": "0.5.0", "calendar": "0.5.0",
            "notifications": "0.5.0", "profiles": "0.6.0",
            "recordings": "0.4.0", "workspaces": "0.6.0",
        }
        proj = _create(tmp_path, "app", "monolith", modules=["core", *expected.keys()])
        pkg = json.loads((proj / "frontend" / "package.json").read_text())
        deps = pkg["dependencies"]
        for key, version in expected.items():
            assert deps[self.REACT_PACKAGES[key]] == f"^{version}", key

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
        """"billing" carries no FRONTEND_REACT_LIBS "nav" mirror, so it
        stays on the flat single-page App.tsx/ModulesPanel shape — a
        nav-bearing selection like "profiles" activates react-router
        routing instead (Ф1, TestFrontendNavWiring)."""
        proj = _create(tmp_path, "app", "monolith", modules=["core", "billing"])
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
        package.json's dependencies stay exactly {react, react-dom}."""
        import json

        proj = _create(tmp_path, "app", "monolith", modules=["core", "gdpr", "translate"])
        frontend = proj / "frontend"
        assert not (frontend / "src" / "modules.tsx").exists()
        app_tsx = (frontend / "src" / "App.tsx").read_text()
        assert "./modules" not in app_tsx
        assert "ModulesProvider" not in app_tsx

        pkg = json.loads((frontend / "package.json").read_text())
        assert pkg["dependencies"] == {"react": "^19.1.0", "react-dom": "^19.1.0"}

    def test_headless_lib_with_no_react_pair_scaffolds_with_zero_frontend_wiring(self, tmp_path):
        """attributes (http=False, no @stapel/attributes-react package
        exists) must not appear anywhere in the frontend wiring surface."""
        import json

        proj = _create(tmp_path, "app", "monolith", modules=["core", "attributes", "billing"])
        pkg = json.loads((proj / "frontend" / "package.json").read_text())
        assert not any("attributes" in dep for dep in pkg["dependencies"])
        modules_tsx = (proj / "frontend" / "src" / "modules.tsx").read_text()
        assert "attributes" not in modules_tsx.lower()


class TestFrontendNavWiring:
    """Ф1 scripted-fullstack navigation — SCAFFOLD half (the lib-side core,
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
        stapel.nav.json/LandingPage.tsx) exists at all."""
        import stapel_tools._frontend_templates as F
        from stapel_tools._compose_templates import render_tokens

        proj = _create(tmp_path, "app", "monolith", modules=["core", "gdpr", "translate"])
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
        Python resolveNav port above) to EXACTLY 2 top-level menuVisible
        entries — notifications.feed and profiles.settings (with
        auth.security nested as profiles.settings' one child). auth.login is
        NOT a 3rd top-level entry: its mirrored `menuVisibleDefault` is
        `false` (a sign-in screen is never a menu tab), so the real
        resolveNav algorithm filters it out of RESOLVED_NAV entirely — it
        still gets its own "/login" ROUTE (routing != the menu), just no
        tab. auth.security is a submenu, never a 4th top-level entry."""
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
        assert top_ids == ["notifications.feed", "profiles.settings"]
        assert len(resolved) == 2
        settings = next(e for e in resolved if e["id"] == "profiles.settings")
        assert [c["id"] for c in settings["children"]] == ["auth.security"]

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

        proj = _create(tmp_path, "app", "monolith", modules=["core"], want_landing=True)
        pkg = json.loads((proj / "frontend" / "package.json").read_text())
        assert pkg["dependencies"]["react-router"] == "^7.18.1"
        assert not pkg["dependencies"]["react-router"].startswith("^8")


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
