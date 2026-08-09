"""Frontend-delivery gate (owner directive, 2026-08-05) —
stapel-frontend-delivery-lint.

The fixtures are the ACTUAL shapes of the app.ironmemo.com stand, the defect
the fable verdict (`tasks/fable/frontend-delivery-split-repo.md`) diagnosed:
`nginx.ssl.conf` serves `root /frontend-react`, `docker-compose.base.yml`
mounts that path from the host directory `./frontend-react`, and BOTH
`scripts/deploy_stand.sh` and `.gitlab-ci.yml` pass
`--exclude 'frontend-react'` to the rsync that pushes the repo to the stand.
nginx serves a directory no deploy ever fills — for months, with a perfectly
clean cache-canon gate above it, because every existing checker looked at one
side of the seam and never at the join.

The canon fixture is the §57 delivery shape taken from meettoday's
`docker-compose.prod.yml`: a one-shot `frontend-builder` (`restart: "no"`)
writes the `frontend_dist` volume, nginx mounts it read-only and starts on
`service_completed_successfully`.
"""
import json
from pathlib import Path

import pytest

from stapel_tools import frontend_delivery_lint as fdl

# ---------------------------------------------------------------------------
# verbatim fixtures
# ---------------------------------------------------------------------------

#: the served side — ironmemo `service-configs/nginx/nginx.ssl.conf`
IRONMEMO_CONF = """\
server {
  listen 443 ssl http2;
  server_name _;

  location /.well-known/ {
    alias /var/www/.well-known/;
  }

  location / {
    root /frontend-react;
    try_files $uri $uri/ /index.html =404;
    expires off;
    add_header Cache-Control "no-cache, must-revalidate" always;
  }

  location ~* ^/(static|assets)/.*\\.(js|css|woff2)$ {
    root /frontend-react;
    expires off;
    add_header Cache-Control "public, max-age=31536000, immutable" always;
  }

  location /kmp {
    alias /frontend-kmp;
    try_files $uri $uri/ /kmp/index.html;
  }

  location /auth {
    proxy_pass http://iron-auth:8000;
  }
}
"""

#: the mounted side — ironmemo `docker-compose.base.yml`
IRONMEMO_COMPOSE = """\
version: "3.1"

services:
  nginx:
    image: fholzer/nginx-brotli:latest
    entrypoint: ["/docker-entrypoint.sh"]
    volumes:
      - ./service-configs/nginx/nginx.ssl.conf:/etc/nginx/templates/nginx.ssl.conf:ro
      - ./frontend-react:/frontend-react
      - ./frontend-kmp:/frontend-kmp
      - static-content:/static
    restart: always

volumes:
  static-content:
"""

#: the deploy side — ironmemo `scripts/deploy_stand.sh`
IRONMEMO_DEPLOY = """\
#!/usr/bin/env bash
set -euo pipefail
rsync_to_stand \\
    -az --delete \\
    --exclude '__pycache__' \\
    --exclude 'node_modules' \\
    --exclude 'frontend-react' \\
    --exclude 'frontend-kmp' \\
    --exclude '.env' \\
    ./ "$REMOTE:$REMOTE_DIR"
"""

IRONMEMO_CI = """\
deploy_dev:
  stage: deploy
  script:
    - |
      rsync -avz --delete \\
        --exclude='.git' \\
        --exclude='frontend-react' \\
        --exclude='frontend-kmp' \\
        ./ ${DEPLOY_USER}@${DEPLOY_HOST}:${DEPLOY_PATH}/
"""

#: the canon — meettoday `docker-compose.prod.yml`, §57 delivery shape
CANON_COMPOSE = """\
services:
  frontend-builder:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    volumes:
      - frontend_dist:/dist
    entrypoint: ["sh", "-c", "cp -r /usr/share/nginx/html/* /dist/"]
    restart: "no"

  nginx:
    image: nginx:alpine
    volumes:
      - ${NGINX_CONF:-./nginx/prod.conf}:/etc/nginx/templates/default.conf.template:ro
      - frontend_dist:/usr/share/nginx/html:ro
      - django_static:/django_static:ro
    depends_on:
      frontend-builder:
        condition: service_completed_successfully
    restart: unless-stopped

volumes:
  django_static:
  frontend_dist:
"""

#: the canon served side — meettoday `nginx/prod.conf` (root at SERVER level,
#: inherited by the SPA-fallback location; the resolver must walk ancestors)
CANON_CONF = """\
server {
    listen 443 ssl;
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
        expires off;
        add_header Cache-Control "no-cache, must-revalidate" always;
    }

    location /static/ {
        alias /django_static/;
    }
}
"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def project(tmp_path: Path, files: dict, dirs=()) -> Path:
    root = tmp_path / "proj"
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    for name in dirs:
        (root / name).mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    return root


def rules(findings):
    return sorted(f.rule for f in findings)


def by_rule(findings, rule):
    return [f for f in findings if f.rule == rule]


IRONMEMO_FILES = {
    "service-configs/nginx/nginx.ssl.conf": IRONMEMO_CONF,
    "docker-compose.base.yml": IRONMEMO_COMPOSE,
    "scripts/deploy_stand.sh": IRONMEMO_DEPLOY,
    ".gitlab-ci.yml": IRONMEMO_CI,
    "scripts/env.stand.template": "IMAGE_TAG_AUTH=iron-auth:local\n",
}


# ---------------------------------------------------------------------------
# the incident
# ---------------------------------------------------------------------------


class TestIronmemoIncident:
    def test_served_directory_excluded_from_the_deploy_is_an_error(self, tmp_path):
        """FED001 — the join nothing else looked at: nginx root ↔ rsync
        --exclude. This is the live bug."""
        target = project(tmp_path, IRONMEMO_FILES, dirs=["frontend-react", "frontend-kmp"])
        findings = by_rule(fdl.lint_project(target), "FED001")

        served = {f.message.split("serves `")[1].split("`")[0] for f in findings}
        assert served == {"/frontend-react", "/frontend-kmp"}
        assert all(f.level == "error" for f in findings)

    def test_the_message_names_both_the_mount_and_every_exclusion(self, tmp_path):
        """A finding that does not carry its evidence gets argued with, not
        fixed: the mount line AND each --exclude site must be in the text."""
        target = project(tmp_path, IRONMEMO_FILES, dirs=["frontend-react", "frontend-kmp"])
        react = [
            f for f in by_rule(fdl.lint_project(target), "FED001")
            if "/frontend-react" in f.message
        ]
        assert react
        message = react[0].message
        assert "docker-compose.base.yml:" in message
        assert "deploy_stand.sh:" in message and "--exclude 'frontend-react'" in message
        assert ".gitlab-ci.yml:" in message

    def test_the_entry_document_and_the_hashed_assets_both_report(self, tmp_path):
        """Both halves of the SPA are undeliverable, and both say so: a fix
        that only re-points one is not a fix."""
        target = project(tmp_path, IRONMEMO_FILES, dirs=["frontend-react", "frontend-kmp"])
        react = [
            f for f in by_rule(fdl.lint_project(target), "FED001")
            if "/frontend-react" in f.message
        ]
        assert len(react) == 2
        assert any("location /" == f.message.split(" serves")[0] for f in react)
        assert any("assets" in f.message.split(" serves")[0] for f in react)

    def test_ci_alone_is_enough_to_fire(self, tmp_path):
        """Half the projects have no deploy script — the CI exclusion is the
        same defect and must be found on its own."""
        files = dict(IRONMEMO_FILES)
        del files["scripts/deploy_stand.sh"]
        target = project(tmp_path, files, dirs=["frontend-react", "frontend-kmp"])
        findings = by_rule(fdl.lint_project(target), "FED001")
        assert findings and all(".gitlab-ci.yml" in f.message for f in findings)

    def test_the_canon_shape_is_clean(self, tmp_path):
        """§57: a one-shot writer fills a named volume, nginx mounts it ro.
        Nothing to report — including no FED006, because the repo builds it."""
        target = project(tmp_path, {
            "nginx/prod.conf": CANON_CONF,
            "docker-compose.prod.yml": CANON_COMPOSE,
            "scripts/env.stand.template": "DEBUG=0\n",
        })
        notes = []
        findings = fdl.lint_project(target, notes=notes)
        assert findings == [], [str(f) for f in findings]


# ---------------------------------------------------------------------------
# FED001 — the writer must be provable, not plausible
# ---------------------------------------------------------------------------


class TestWriterProof:
    def _volume_project(self, tmp_path, writer_block: str) -> Path:
        compose = (
            "services:\n"
            "  nginx:\n"
            "    image: nginx:alpine\n"
            "    volumes:\n"
            "      - frontend_dist:/usr/share/nginx/html:ro\n"
            f"{writer_block}"
            "\nvolumes:\n"
            "  frontend_dist:\n"
        )
        return project(tmp_path, {
            "nginx/prod.conf": CANON_CONF,
            "docker-compose.prod.yml": compose,
        })

    def test_named_volume_with_no_writer_at_all(self, tmp_path):
        """An empty volume serves 404s for the whole frontend."""
        target = self._volume_project(tmp_path, "")
        findings = by_rule(fdl.lint_project(target), "FED001")
        assert len(findings) == 1
        assert "no service" in findings[0].message.lower()

    def test_read_only_writer_is_not_a_writer(self, tmp_path):
        target = self._volume_project(tmp_path, (
            "  exporter:\n"
            "    image: repo/dist:sha-abc\n"
            "    restart: \"no\"\n"
            "    volumes:\n"
            "      - frontend_dist:/output:ro\n"
        ))
        assert rules(fdl.lint_project(target)) == ["FED001"]

    def test_long_running_writer_is_not_a_delivery_mechanism(self, tmp_path):
        """A daemon with write access proves nothing about the bundle existing
        BEFORE nginx starts serving it (§57 waits on a one-shot exit)."""
        target = self._volume_project(tmp_path, (
            "  backend:\n"
            "    image: repo/backend:sha-abc\n"
            "    restart: unless-stopped\n"
            "    volumes:\n"
            "      - frontend_dist:/app/frontend\n"
        ))
        findings = by_rule(fdl.lint_project(target), "FED001")
        assert len(findings) == 1
        assert "backend" in findings[0].message

    def test_one_shot_by_restart_no_is_accepted(self, tmp_path):
        target = self._volume_project(tmp_path, (
            "  frontend-build:\n"
            "    image: repo/dist:sha-abc\n"
            "    restart: \"no\"\n"
            "    volumes:\n"
            "      - frontend_dist:/output\n"
        ))
        assert fdl.lint_project(target) == []

    def test_one_shot_by_output_mount_is_accepted(self, tmp_path):
        """`/output` is the §57 export contract (`_frontend_templates.py`), so
        it identifies a one-shot writer even without `restart: "no"`."""
        target = self._volume_project(tmp_path, (
            "  frontend-build:\n"
            "    image: repo/dist:sha-abc\n"
            "    volumes:\n"
            "      - frontend_dist:/output\n"
        ))
        assert fdl.lint_project(target) == []

    def test_nothing_mounted_at_the_served_path(self, tmp_path):
        target = project(tmp_path, {
            "nginx/prod.conf": CANON_CONF,
            "docker-compose.prod.yml": (
                "services:\n"
                "  nginx:\n"
                "    image: nginx:alpine\n"
                "    volumes:\n"
                "      - ./nginx/prod.conf:/etc/nginx/conf.d/default.conf:ro\n"
            ),
        })
        findings = by_rule(fdl.lint_project(target), "FED001")
        assert len(findings) == 1
        assert "mounts NOTHING" in findings[0].message

    def test_a_locally_built_image_that_copies_into_the_path_is_accepted(self, tmp_path):
        """The other legitimate writer from the verdict: `COPY` in the image."""
        target = project(tmp_path, {
            "nginx/prod.conf": CANON_CONF,
            "docker-compose.prod.yml": (
                "services:\n"
                "  nginx:\n"
                "    build:\n"
                "      context: ./docker/nginx\n"
                "    volumes:\n"
                "      - ./nginx/prod.conf:/etc/nginx/conf.d/default.conf:ro\n"
            ),
            "docker/nginx/Dockerfile": (
                "FROM nginx:alpine\n"
                "COPY --from=build /app/dist/ /usr/share/nginx/html/\n"
            ),
        })
        assert fdl.lint_project(target) == []

    def test_absolute_host_path_cannot_be_proved(self, tmp_path):
        target = project(tmp_path, {
            "nginx/prod.conf": CANON_CONF,
            "docker-compose.prod.yml": (
                "services:\n"
                "  nginx:\n"
                "    image: nginx:alpine\n"
                "    volumes:\n"
                "      - /srv/frontend:/usr/share/nginx/html:ro\n"
            ),
        })
        findings = by_rule(fdl.lint_project(target), "FED001")
        assert len(findings) == 1
        assert "ABSOLUTE host path" in findings[0].message

    def test_bind_source_missing_from_the_repo(self, tmp_path):
        files = dict(IRONMEMO_FILES)
        files["scripts/deploy_stand.sh"] = "#!/usr/bin/env bash\nrsync -az ./ $REMOTE\n"
        del files[".gitlab-ci.yml"]
        target = project(tmp_path, files)  # no frontend-react/ directory created
        findings = by_rule(fdl.lint_project(target), "FED001")
        assert findings and all("does not exist" in f.message for f in findings)

    def test_no_compose_at_all_is_reported_not_assumed_fine(self, tmp_path):
        target = project(tmp_path, {"nginx/prod.conf": CANON_CONF})
        notes = []
        findings = fdl.lint_project(target, notes=notes)
        assert rules(findings) == ["FED001"]
        assert any("docker-compose" in note for note in notes)

    def test_no_nginx_service_is_reported(self, tmp_path):
        target = project(tmp_path, {
            "nginx/prod.conf": CANON_CONF,
            "docker-compose.prod.yml": "services:\n  backend:\n    image: repo/backend:sha-abc\n",
        })
        findings = by_rule(fdl.lint_project(target), "FED001")
        assert len(findings) == 1
        assert "no compose service" in findings[0].message


# ---------------------------------------------------------------------------
# FED006 — transported, but nobody builds it
# ---------------------------------------------------------------------------


class TestHostBindWarning:
    def test_bind_that_reaches_the_stand_is_a_warning_not_silence(self, tmp_path):
        files = dict(IRONMEMO_FILES)
        files["scripts/deploy_stand.sh"] = "#!/usr/bin/env bash\nrsync -az ./ $REMOTE\n"
        del files[".gitlab-ci.yml"]
        target = project(tmp_path, files, dirs=["frontend-react", "frontend-kmp"])
        findings = fdl.lint_project(target)
        assert rules(findings) == ["FED006", "FED006", "FED006"]
        assert all(f.level == "warning" for f in findings)

    def test_strict_fails_on_it(self, tmp_path, capsys):
        files = dict(IRONMEMO_FILES)
        files["scripts/deploy_stand.sh"] = "#!/usr/bin/env bash\nrsync -az ./ $REMOTE\n"
        del files[".gitlab-ci.yml"]
        target = project(tmp_path, files, dirs=["frontend-react", "frontend-kmp"])
        assert fdl.main([str(target)]) == 0
        assert fdl.main([str(target), "--strict"]) == 1


# ---------------------------------------------------------------------------
# FED002 — the tag has to name one immutable build
# ---------------------------------------------------------------------------


def _tagged(tmp_path, image, *, file_name="docker-compose.prod.yml", env=""):
    compose = (
        "services:\n"
        "  frontend-build:\n"
        f"    image: {image}\n"
        "    restart: \"no\"\n"
        "    volumes:\n"
        "      - frontend_dist:/output\n"
        "  nginx:\n"
        "    image: nginx:alpine\n"
        "    volumes:\n"
        "      - frontend_dist:/usr/share/nginx/html:ro\n"
        "\nvolumes:\n"
        "  frontend_dist:\n"
    )
    files = {"nginx/prod.conf": CANON_CONF, file_name: compose}
    if env:
        files["scripts/env.stand.template"] = env
    return project(tmp_path, files)


class TestMutableTags:
    @pytest.mark.parametrize("image", [
        "registry.example.com/ironmemo/frontend:latest",
        "registry.example.com/ironmemo/frontend:dev",
        "registry.example.com/ironmemo/frontend:main",
        "registry.example.com/ironmemo/frontend",
    ])
    def test_mutable_tags_are_errors(self, tmp_path, image):
        target = _tagged(tmp_path, image)
        assert rules(fdl.lint_project(target)) == ["FED002"]

    @pytest.mark.parametrize("image", [
        "registry.example.com/ironmemo/frontend:sha-9a34f21",
        "registry.example.com/ironmemo/frontend@sha256:" + "a" * 64,
        "registry.example.com/ironmemo/frontend:1.4.2",
    ])
    def test_immutable_references_are_clean(self, tmp_path, image):
        assert fdl.lint_project(_tagged(tmp_path, image)) == []

    def test_a_variable_tag_is_resolved_through_the_env_template(self, tmp_path):
        """The pin lives in the template (verdict §2.2), so that is where the
        gate has to look before deciding the tag is immutable."""
        pinned = _tagged(
            tmp_path / "a", "repo/frontend:${FRONTEND_REACT_TAG}",
            env="FRONTEND_REACT_TAG=sha-9a34f21\n",
        )
        assert fdl.lint_project(pinned) == []

        mutable = _tagged(
            tmp_path / "b", "repo/frontend:${FRONTEND_REACT_TAG}",
            env="FRONTEND_REACT_TAG=latest\n",
        )
        assert rules(fdl.lint_project(mutable)) == ["FED002"]

    def test_a_latest_default_counts_as_latest(self, tmp_path):
        target = _tagged(
            tmp_path, "repo/frontend:${FRONTEND_REACT_TAG:-latest}",
            env="FRONTEND_REACT_TAG=\n",
        )
        assert rules(fdl.lint_project(target)) == ["FED002"]

    def test_the_local_stack_is_exempt(self, tmp_path):
        """Local dev is where `latest` belongs; the rule is about stands."""
        target = _tagged(
            tmp_path, "repo/frontend:latest", file_name="docker-compose.local.yml",
        )
        assert by_rule(fdl.lint_project(target), "FED002") == []

    def test_noqa_suppresses_it(self, tmp_path):
        compose = (
            "services:\n"
            "  frontend-build:\n"
            "    image: repo/frontend:latest  # noqa: FED002\n"
            "    restart: \"no\"\n"
            "    volumes:\n"
            "      - frontend_dist:/output\n"
            "  nginx:\n"
            "    image: nginx:alpine\n"
            "    volumes:\n"
            "      - frontend_dist:/usr/share/nginx/html:ro\n"
            "\nvolumes:\n"
            "  frontend_dist:\n"
        )
        target = project(tmp_path, {
            "nginx/prod.conf": CANON_CONF, "docker-compose.prod.yml": compose,
        })
        assert fdl.lint_project(target) == []


# ---------------------------------------------------------------------------
# FED003 — the pin has to survive .env regeneration
# ---------------------------------------------------------------------------


class TestEnvTemplate:
    def _project(self, tmp_path, env_body=None):
        files = {
            "nginx/prod.conf": CANON_CONF,
            "docker-compose.prod.yml": (
                "services:\n"
                "  frontend-build:\n"
                "    image: ${FRONTEND_REACT_IMAGE}:${FRONTEND_REACT_TAG}\n"
                "    restart: \"no\"\n"
                "    volumes:\n"
                "      - frontend_dist:/output\n"
                "  nginx:\n"
                "    image: nginx:alpine\n"
                "    volumes:\n"
                "      - frontend_dist:/usr/share/nginx/html:ro\n"
                "\nvolumes:\n"
                "  frontend_dist:\n"
            ),
        }
        if env_body is not None:
            files["scripts/env.stand.template"] = env_body
        return project(tmp_path, files)

    def test_missing_keys_are_errors(self, tmp_path):
        target = self._project(tmp_path, "DEBUG=0\n")
        findings = by_rule(fdl.lint_project(target), "FED003")
        assert {f.message.split("`")[1] for f in findings} == {
            "${FRONTEND_REACT_IMAGE}", "${FRONTEND_REACT_TAG}",
        }

    def test_declared_keys_are_clean(self, tmp_path):
        target = self._project(
            tmp_path,
            "FRONTEND_REACT_IMAGE=registry.example.com/ironmemo/frontend\n"
            "FRONTEND_REACT_TAG=sha-9a34f21\n",
        )
        assert by_rule(fdl.lint_project(target), "FED003") == []

    def test_no_env_template_at_all_says_so(self, tmp_path):
        target = self._project(tmp_path, None)
        notes = []
        findings = by_rule(fdl.lint_project(target, notes=notes), "FED003")
        assert findings and "no env template" in findings[0].message
        assert any("env template" in note for note in notes)

    def test_a_default_does_not_excuse_absence(self, tmp_path):
        target = project(tmp_path, {
            "nginx/prod.conf": CANON_CONF,
            "docker-compose.prod.yml": (
                "services:\n"
                "  frontend-build:\n"
                "    image: repo/frontend:${FRONTEND_REACT_TAG:-sha-000}\n"
                "    restart: \"no\"\n"
                "    volumes:\n"
                "      - frontend_dist:/output\n"
                "  nginx:\n"
                "    image: nginx:alpine\n"
                "    volumes:\n"
                "      - frontend_dist:/usr/share/nginx/html:ro\n"
                "\nvolumes:\n"
                "  frontend_dist:\n"
            ),
            "scripts/env.stand.template": "DEBUG=0\n",
        })
        findings = by_rule(fdl.lint_project(target), "FED003")
        assert len(findings) == 1
        assert "default" in findings[0].message


# ---------------------------------------------------------------------------
# FED004 — a stub, and it says so out loud
# ---------------------------------------------------------------------------


class TestContractSnapshot:
    def test_absent_mechanism_is_a_note_not_a_silent_pass(self, tmp_path):
        target = project(tmp_path, {
            "nginx/prod.conf": CANON_CONF, "docker-compose.prod.yml": CANON_COMPOSE,
        })
        notes = []
        fdl.lint_project(target, notes=notes)
        assert any("FED004" in note and "no contract snapshot" in note for note in notes)

    def test_snapshot_without_a_digest_field(self, tmp_path):
        target = project(tmp_path, {
            "nginx/prod.conf": CANON_CONF,
            "docker-compose.prod.yml": CANON_COMPOSE,
            "frontend/dist/build-info.json": json.dumps({"built_at": "2026-08-05T10:00:00Z"}),
        })
        findings = by_rule(fdl.lint_project(target), "FED004")
        assert len(findings) == 1
        assert "no backend-surface digest" in findings[0].message

    def test_snapshot_disagreeing_with_the_pin(self, tmp_path):
        target = project(tmp_path, {
            "nginx/prod.conf": CANON_CONF,
            "docker-compose.prod.yml": CANON_COMPOSE,
            "frontend/dist/build-info.json": json.dumps({"backend_surface": "abc123"}),
            "scripts/env.stand.template": "FRONTEND_CONTRACT_DIGEST=def456\n",
        })
        findings = by_rule(fdl.lint_project(target), "FED004")
        assert len(findings) == 1
        assert "abc123" in findings[0].message and "def456" in findings[0].message

    def test_agreeing_snapshot_is_clean(self, tmp_path):
        target = project(tmp_path, {
            "nginx/prod.conf": CANON_CONF,
            "docker-compose.prod.yml": CANON_COMPOSE,
            "frontend/dist/build-info.json": json.dumps({"backend_surface": "abc123"}),
            "scripts/env.stand.template": "FRONTEND_CONTRACT_DIGEST=abc123\n",
        })
        assert fdl.lint_project(target) == []


# ---------------------------------------------------------------------------
# FED005 — unreadable is reported, never skipped
# ---------------------------------------------------------------------------


class TestUnreadableIsLoud:
    def test_root_through_a_variable(self, tmp_path):
        conf = (
            "server {\n"
            "  location / {\n"
            "    root $frontend_dir;\n"
            "    try_files $uri /index.html;\n"
            "  }\n"
            "}\n"
        )
        target = project(tmp_path, {
            "nginx/prod.conf": conf, "docker-compose.prod.yml": CANON_COMPOSE,
        })
        findings = by_rule(fdl.lint_project(target), "FED005")
        assert len(findings) == 1
        assert findings[0].level == "error"

    def test_a_capture_tail_still_has_a_knowable_directory(self, tmp_path):
        """`alias /frontend-kmp/$1` — the tail is dynamic, the directory is
        not; reporting FED005 here would be noise, so the prefix is used."""
        assert fdl._literal_prefix("/frontend-kmp/$1") == "/frontend-kmp"
        assert fdl._literal_prefix("$frontend_dir") is None
        assert fdl._literal_prefix("${FRONT}/dist") is None
        assert fdl._literal_prefix("relative/dist") is None

    def test_unparseable_conf(self, tmp_path):
        target = project(tmp_path, {
            "nginx/prod.conf": "server {\n  location / {\n    root /x;\n",
            "docker-compose.prod.yml": CANON_COMPOSE,
        })
        findings = by_rule(fdl.lint_project(target), "FED005")
        assert len(findings) == 1
        assert "does not parse" in findings[0].message

    def test_server_level_include_that_resolves_to_nothing(self, tmp_path):
        conf = (
            "server {\n"
            "  include /etc/nginx/extra/*.conf;\n"
            "  location / {\n"
            "    root /usr/share/nginx/html;\n"
            "    try_files $uri /index.html;\n"
            "  }\n"
            "}\n"
        )
        target = project(tmp_path, {
            "nginx/prod.conf": conf, "docker-compose.prod.yml": CANON_COMPOSE,
        })
        assert rules(fdl.lint_project(target)) == ["FED005"]

    def test_include_inside_a_location_is_not_reported(self, tmp_path):
        """It can only add directives to a location we already see — it cannot
        hide a root. meettoday's `/rtc` gate include is exactly this shape."""
        conf = (
            "server {\n"
            "  root /usr/share/nginx/html;\n"
            "  location / {\n"
            "    try_files $uri /index.html;\n"
            "  }\n"
            "  location /rtc {\n"
            "    include /etc/nginx/stapel-gate/rtc.conf;\n"
            "  }\n"
            "}\n"
        )
        target = project(tmp_path, {
            "nginx/prod.conf": conf, "docker-compose.prod.yml": CANON_COMPOSE,
        })
        assert fdl.lint_project(target) == []

    def test_mount_source_through_a_variable(self, tmp_path):
        target = project(tmp_path, {
            "nginx/prod.conf": CANON_CONF,
            "docker-compose.prod.yml": (
                "services:\n"
                "  nginx:\n"
                "    image: nginx:alpine\n"
                "    volumes:\n"
                "      - ${FRONTEND_DIR}:/usr/share/nginx/html:ro\n"
            ),
        })
        findings = by_rule(fdl.lint_project(target), "FED005")
        assert len(findings) == 1
        assert "variable" in findings[0].message

    def test_compose_with_yaml_anchors_is_reported(self, tmp_path):
        target = project(tmp_path, {
            "nginx/prod.conf": CANON_CONF,
            "docker-compose.prod.yml": (
                "x-common: &common\n"
                "  restart: unless-stopped\n"
                "services:\n"
                "  nginx:\n"
                "    <<: *common\n"
                "    image: nginx:alpine\n"
            ),
        })
        findings = by_rule(fdl.lint_project(target), "FED005")
        assert findings and "merge key" in findings[0].message

    def test_no_root_in_effect_anywhere(self, tmp_path):
        conf = "server {\n  location / {\n    try_files $uri /index.html;\n  }\n}\n"
        target = project(tmp_path, {
            "nginx/prod.conf": conf, "docker-compose.prod.yml": CANON_COMPOSE,
        })
        findings = by_rule(fdl.lint_project(target), "FED005")
        assert len(findings) == 1
        assert "no `root`/`alias`" in findings[0].message


# ---------------------------------------------------------------------------
# suppression
# ---------------------------------------------------------------------------


class TestNoqa:
    def test_noqa_on_the_location_line(self, tmp_path):
        conf = IRONMEMO_CONF.replace("  location / {", "  location / {  # noqa: FED001")
        conf = conf.replace("  location /kmp {", "  location /kmp {  # noqa")
        conf = conf.replace(
            "  location ~* ^/(static|assets)/.*\\.(js|css|woff2)$ {",
            "  location ~* ^/(static|assets)/.*\\.(js|css|woff2)$ {  # noqa: FED001",
        )
        files = dict(IRONMEMO_FILES)
        files["service-configs/nginx/nginx.ssl.conf"] = conf
        target = project(tmp_path, files, dirs=["frontend-react", "frontend-kmp"])
        assert fdl.lint_project(target) == []

    def test_noqa_on_the_mount_line(self, tmp_path):
        compose = IRONMEMO_COMPOSE.replace(
            "      - ./frontend-react:/frontend-react",
            "      - ./frontend-react:/frontend-react  # noqa: FED001",
        ).replace(
            "      - ./frontend-kmp:/frontend-kmp",
            "      - ./frontend-kmp:/frontend-kmp  # noqa: FED001",
        )
        files = dict(IRONMEMO_FILES)
        files["docker-compose.base.yml"] = compose
        target = project(tmp_path, files, dirs=["frontend-react", "frontend-kmp"])
        assert fdl.lint_project(target) == []


# ---------------------------------------------------------------------------
# zero input — never call unread things clean
# ---------------------------------------------------------------------------


class TestZeroInput:
    def test_empty_project_reports_what_was_not_checked(self, tmp_path, capsys):
        target = project(tmp_path, {"README.md": "# nothing here\n"})
        notes = []
        assert fdl.lint_project(target, notes=notes) == []
        assert any("nginx conf" in note for note in notes)
        assert any("docker-compose" in note for note in notes)

        assert fdl.main([str(target)]) == 0
        out = capsys.readouterr()
        assert "nothing was verified" not in out.out  # wording is the linter's own
        assert "Checked 0 nginx conf(s)" in out.out

    def test_confs_without_a_frontend_location_say_so(self, tmp_path):
        conf = "server {\n  location /api/ {\n    proxy_pass http://backend:8000;\n  }\n}\n"
        target = project(tmp_path, {
            "nginx/prod.conf": conf, "docker-compose.prod.yml": CANON_COMPOSE,
        })
        notes = []
        assert fdl.lint_project(target, notes=notes) == []
        assert any("none of the nginx confs" in note for note in notes)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_json_output_and_exit_code(self, tmp_path, capsys):
        target = project(tmp_path, IRONMEMO_FILES, dirs=["frontend-react", "frontend-kmp"])
        assert fdl.main([str(target), "--json"]) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["errors"] == 3
        assert {f["rule"] for f in payload["findings"]} == {"FED001"}
        assert payload["notes"]

    def test_clean_project_exits_zero(self, tmp_path, capsys):
        target = project(tmp_path, {
            "nginx/prod.conf": CANON_CONF, "docker-compose.prod.yml": CANON_COMPOSE,
        })
        assert fdl.main([str(target)]) == 0
        assert "No frontend-delivery issues found" in capsys.readouterr().out

    def test_missing_target_is_a_usage_error(self, tmp_path):
        assert fdl.main([str(tmp_path / "nope")]) == 2


# ---------------------------------------------------------------------------
# the compose reader (no PyYAML: stapel-tools ships zero runtime deps)
# ---------------------------------------------------------------------------


class TestComposeReader:
    def test_mount_split_keeps_variable_defaults_whole(self):
        assert fdl.split_mount("${NGINX_CONF:-./nginx/prod.conf}:/etc/nginx/x.conf:ro") == [
            "${NGINX_CONF:-./nginx/prod.conf}", "/etc/nginx/x.conf", "ro",
        ]
        assert fdl.split_mount("./frontend-react:/frontend-react") == [
            "./frontend-react", "/frontend-react",
        ]
        assert fdl.split_mount("/app/node_modules") == ["/app/node_modules"]

    def test_reads_the_shapes_the_fleet_actually_writes(self, tmp_path):
        path = tmp_path / "docker-compose.prod.yml"
        path.write_text(CANON_COMPOSE, encoding="utf-8")
        compose = fdl.load_compose(path)
        assert set(compose.services) == {"frontend-builder", "nginx"}
        builder = compose.services["frontend-builder"]
        assert builder.build_context == "./frontend"
        assert builder.restart == "no" and builder.one_shot
        nginx = compose.services["nginx"]
        assert [m.target for m in nginx.mounts] == [
            "/etc/nginx/templates/default.conf.template",
            "/usr/share/nginx/html",
            "/django_static",
        ]
        assert nginx.mounts[1].read_only and nginx.mounts[1].is_named_volume
        assert compose.volumes == {"django_static", "frontend_dist"}

    def test_comments_inside_quoted_values_survive(self, tmp_path):
        path = tmp_path / "docker-compose.yml"
        path.write_text(
            'services:\n'
            '  nginx:\n'
            '    image: nginx:alpine   # pinned by hand\n'
            '    entrypoint: ["sh", "-c", "echo # not a comment"]\n',
            encoding="utf-8",
        )
        compose = fdl.load_compose(path)
        assert compose.services["nginx"].image == "nginx:alpine"

    def test_include_list_is_read(self, tmp_path):
        path = tmp_path / "docker-compose.yml"
        path.write_text(
            'version: "3.1"\n\ninclude:\n  - docker-compose.base.yml\n\nservices:\n'
            '  nginx:\n    image: nginx:alpine\n',
            encoding="utf-8",
        )
        assert fdl.load_compose(path).includes == ["docker-compose.base.yml"]

    def test_local_stack_detection(self, tmp_path):
        for name, expected in (("docker-compose.local.yml", True),
                               ("docker-compose.prod.yml", False),
                               ("docker-compose.dev.yml", False)):
            path = tmp_path / name
            path.write_text("services:\n  nginx:\n    image: nginx:alpine\n", encoding="utf-8")
            assert fdl.load_compose(path).is_local_stack is expected


class TestExcludeSemantics:
    @pytest.mark.parametrize("source,pattern,hit", [
        ("./frontend-react", "frontend-react", True),
        ("frontend-react", "frontend-react", True),
        ("./frontend-react/dist", "frontend-react", True),
        ("./frontend-react", "frontend-*", True),
        ("./frontend-react", "/frontend-react", True),
        ("./apps/frontend-react", "frontend-react", True),
        ("./apps/frontend-react", "/frontend-react", False),
        ("./frontend-react", "frontend-kmp", False),
        ("./frontend-react", "", False),
    ])
    def test_rsync_exclude_matching(self, source, pattern, hit):
        assert fdl.excluded_by(source, pattern) is hit

    def test_exclusions_are_collected_from_scripts_and_ci(self, tmp_path):
        target = project(tmp_path, IRONMEMO_FILES)
        patterns = {e.pattern for e in fdl.collect_exclusions(target)}
        assert {"frontend-react", "frontend-kmp"} <= patterns
        sources = {e.path.name for e in fdl.collect_exclusions(target)}
        assert sources == {"deploy_stand.sh", ".gitlab-ci.yml"}
