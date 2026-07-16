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


def _create(tmp_path, name, project_type, modules=None):
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
    dev-nginx that routes the reserved backend namespace to Django and
    everything else to Vite; prod compose's nginx serves the built frontend
    + proxies api/admin/static/media to Django."""

    def test_dev_compose_starts_frontend_and_backend_and_is_valid(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        data = _docker_compose_config(proj, "docker-compose.dev.yml")
        if data is None:
            return
        services = data["services"]
        assert "frontend" in services
        assert "svc-app" in services  # the backend actually got wired in
        assert "nginx" in services
        assert services["nginx"]["environment"]["BACKEND_UPSTREAM"] == "svc-app:8000"
        assert services["nginx"]["environment"]["FRONTEND_DEV_UPSTREAM"] == "frontend:5173"
        # dev-nginx conf.d source is overridden away from prod's — merge by
        # mount TARGET, not appended alongside it.
        conf_sources = [
            v["source"] for v in services["nginx"]["volumes"] if v["target"] == "/etc/nginx/conf.d"
        ]
        assert conf_sources == [str(proj / "service-configs" / "nginx-dev")]

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
        backend never wired into docker-compose.yml/docker-compose.dev.yml
        for a project's first/default service — found auditing this task."""
        proj = _create(tmp_path, "app", "monolith")
        dev = (proj / "docker-compose.dev.yml").read_text()
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

    def test_nginx_dev_template_env_driven_proxy_targets(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        tmpl = (proj / "service-configs" / "nginx-dev" / "nginx-dev.conf.template").read_text()
        assert "proxy_pass http://${BACKEND_UPSTREAM}" in tmpl
        assert "proxy_pass http://${FRONTEND_DEV_UPSTREAM}" in tmpl
        assert "location /app/ {" in tmpl  # the project's own slug, reserved
        assert "location /staticfiles/" in tmpl
        assert "location /media/" in tmpl
        # no hardcoded compose-network host:port baked into the conf itself
        assert "svc-app:8000" not in tmpl
        assert "frontend:5173" not in tmpl

    def test_only_a_single_bare_conf_file_in_nginx_dev_dir(self, tmp_path):
        """The dev nginx-dev directory is mounted at BOTH /etc/nginx/conf.d
        and /etc/nginx/templates (see docker-compose.dev.yml) — safe only
        because it contains no bare *.conf that conf.d's `include *.conf`
        would double-load; only the *.template envsubst source."""
        proj = _create(tmp_path, "app", "monolith")
        nginx_dev_dir = proj / "service-configs" / "nginx-dev"
        conf_files = list(nginx_dev_dir.glob("*.conf"))
        assert conf_files == []
        assert (nginx_dev_dir / "nginx-dev.conf.template").exists()


class TestFrontendScaffold:
    def test_frontend_dir_scaffolded_with_vite_react(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")
        frontend = proj / "frontend"
        for rel in (
            "package.json", "vite.config.ts", "index.html",
            "src/main.tsx", "src/App.tsx", "Dockerfile", ".gitignore",
        ):
            assert (frontend / rel).exists(), rel

        pkg = (frontend / "package.json").read_text()
        assert '"name": "app-frontend"' in pkg
        assert '"vite"' in pkg

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
        assert 'STATIC_URL = f"/staticfiles/{{SLUG}}/"'.replace("{{SLUG}}", "app") in settings or \
            'STATIC_URL = f"/staticfiles/app/"' in settings
        assert 'MEDIA_URL = f"/media/app/"' in settings

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
