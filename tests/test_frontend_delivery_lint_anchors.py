"""FED compose reader: YAML anchors, aliases and merge keys are resolved."""
import re

import pytest

from stapel_tools import frontend_delivery_lint as fdl
from test_frontend_delivery_lint import (
    CANON_COMPOSE,
    CANON_CONF,
    INCIDENT_FILES,
    by_rule,
    project,
    rules,
)

#: INCIDENT_COMPOSE with the nginx volumes and restart coming from an x- block
ANCHORED_INCIDENT_COMPOSE = """\
version: "3.1"

x-nginx-common: &nginx-common
  restart: always
  volumes: &nginx-volumes
    - ./service-configs/nginx/nginx.ssl.conf:/etc/nginx/templates/nginx.ssl.conf:ro
    - ./frontend-react:/frontend-react
    - ./frontend-kmp:/frontend-kmp
    - static-content:/static

services:
  nginx:
    <<: *nginx-common
    image: fholzer/nginx-brotli:latest
    entrypoint: ["/docker-entrypoint.sh"]

volumes:
  static-content:
"""

ANCHORED_CANON_COMPOSE = """\
x-restart: &restart
  restart: unless-stopped
x-ro-dist: &dist
  volumes:
    - ${NGINX_CONF:-./nginx/prod.conf}:/etc/nginx/templates/default.conf.template:ro
    - frontend_dist:/usr/share/nginx/html:ro
    - django_static:/django_static:ro

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
    <<: [*dist, *restart]
    image: nginx:alpine
    depends_on:
      frontend-builder:
        condition: service_completed_successfully

volumes:
  django_static:
  frontend_dist:
"""


def _shape(findings, root):
    """Findings without the fixture root and line numbers, which legitimately differ."""
    return sorted(
        (f.rule, re.sub(r"\d+", "N", f.message.replace(str(root), "<root>")))
        for f in findings
    )


def test_anchored_incident_matches_expanded(tmp_path):
    a = project(tmp_path / "a", INCIDENT_FILES)
    expanded = fdl.lint_project(a)
    anchored_files = dict(INCIDENT_FILES)
    anchored_files["docker-compose.base.yml"] = ANCHORED_INCIDENT_COMPOSE
    b = project(tmp_path / "b", anchored_files)
    anchored = fdl.lint_project(b)
    assert by_rule(expanded, "FED001")
    assert "FED005" not in rules(anchored)
    assert _shape(anchored, b.resolve()) == _shape(expanded, a.resolve())


def test_anchored_canon_matches_expanded(tmp_path):
    a = project(tmp_path / "a", {
        "nginx/prod.conf": CANON_CONF, "docker-compose.prod.yml": CANON_COMPOSE,
    })
    b = project(tmp_path / "b", {
        "nginx/prod.conf": CANON_CONF, "docker-compose.prod.yml": ANCHORED_CANON_COMPOSE,
    })
    expanded, anchored = fdl.lint_project(a), fdl.lint_project(b)
    assert "FED005" not in rules(anchored)
    assert _shape(anchored, b.resolve()) == _shape(expanded, a.resolve())


def test_merge_precedence_explicit_then_earlier_source():
    root = fdl.parse_yaml_subset(
        "x-a: &a\n  image: a\n  restart: a\n  user: a\n"
        "x-b: &b\n  image: b\n  restart: b\n  hostname: b\n"
        "svc:\n  <<: [*a, *b]\n  image: own\n"
    )
    svc = root.get("svc")
    assert svc.scalar("image") == "own"
    assert svc.scalar("restart") == "a"
    assert svc.scalar("user") == "a"
    assert svc.scalar("hostname") == "b"


def test_alias_keeps_anchor_line_numbers():
    root = fdl.parse_yaml_subset(
        "x-vols: &vols\n  - ./a:/a\n  - ./b:/b\nsvc:\n  volumes: *vols\n"
    )
    assert root.get("svc").strings("volumes") == [("./a:/a", 2), ("./b:/b", 3)]


def test_scalar_anchor_and_alias():
    root = fdl.parse_yaml_subset("a: &img nginx:1\nb: *img\n")
    assert root.scalar("b") == "nginx:1"


def test_undefined_alias_raises():
    with pytest.raises(fdl.ComposeParseError, match="undefined YAML alias"):
        fdl.parse_yaml_subset("svc:\n  volumes: *missing\n")


def test_merge_of_a_scalar_raises():
    with pytest.raises(fdl.ComposeParseError, match="not a mapping"):
        fdl.parse_yaml_subset("x: &x hello\nsvc:\n  <<: *x\n")
