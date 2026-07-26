"""SPA cache canon gate (owner directive, 2026-07-26) — stapel-nginx-cache-lint.

The fixtures below are the ACTUAL shapes involved in the app.ironmemo.com
incident, copied from that stand's `service-configs/nginx/nginx.ssl.conf`:
the entry document carried BOTH `expires 1d` AND
`add_header Cache-Control "public, must-revalidate"`, nginx emitted two
Cache-Control headers, browsers combined them into max-age=86400, and a
freshly deployed frontend fix stayed invisible for up to 24 hours — while a
live verification of that fix read the stale bundle and concluded wrongly.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from stapel_tools import nginx_cache_lint as ncl

# ---------------------------------------------------------------------------
# fixtures — verbatim shapes
# ---------------------------------------------------------------------------

#: the defect, exactly as it was served in production
IRONMEMO_BROKEN = """\
server {
  listen 443 ssl http2;
  server_name _;

  # React frontend - main site at root
  location / {
    root /frontend-react;
    try_files $uri $uri/ /index.html =404;
    expires 1d;
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Cache-Control "public, must-revalidate";
  }

  # React static assets
  location ~* ^/(static|assets)/.*\\.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
    root /frontend-react;
    expires 30d;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Cache-Control "public, immutable";
  }

  # Django static files at /static
  location /staticfiles {
    alias /static;
    expires 30d;
    add_header Cache-Control "public, immutable";
  }

  # Django media files at /media
  location /media {
    alias /media;
    expires 30d;
    add_header Cache-Control "public";
  }

  location /auth {
    set $upstream_auth iron-auth:8000;
    proxy_pass http://$upstream_auth;
    add_header Access-Control-Allow-Origin "*" always;
  }
}
"""

#: the canon the scaffold now emits — must be entirely clean
CANON = """\
server {
  listen 80;
  server_name _;

  location /staticfiles/ {
    alias /staticfiles/;
  }

  location /media/ {
    alias /media/;
  }

  location ~* ^/assets/.*\\.(?:js|mjs|css|woff2?|ttf|otf|eot|svg|png|jpe?g|gif|ico|webp|avif|map)$ {
    root /usr/share/nginx/html;
    expires off;
    add_header Cache-Control "public, max-age=31536000, immutable" always;
  }

  location / {
    root /usr/share/nginx/html;
    try_files $uri $uri/ /index.html;
    expires off;
    add_header Cache-Control "no-cache, must-revalidate" always;
  }
}
"""


def _lint(text, name="nginx.conf"):
    from pathlib import Path
    return ncl.lint_conf(Path(name), text)


def _rules(findings):
    return sorted({f.rule for f in findings})


def _by_rule(findings, rule):
    return [f for f in findings if f.rule == rule]


# ---------------------------------------------------------------------------
# the incident
# ---------------------------------------------------------------------------


class TestIronmemoIncident:
    def test_entry_document_cacheable_is_an_error(self):
        """NGX001 — `expires 1d` on the SPA fallback: the deploy does not land."""
        findings = _by_rule(_lint(IRONMEMO_BROKEN), "NGX001")
        assert len(findings) == 1, findings
        finding = findings[0]
        assert finding.level == "error"
        assert finding.line == 9, "anchored on the `expires 1d;` line"
        assert "86400s" in finding.message
        assert "expires off" in finding.message  # the canon fix is in the message

    def test_double_cache_control_header_is_an_error(self):
        """NGX003 — the actual mechanism: nginx emits two Cache-Control headers."""
        findings = _by_rule(_lint(IRONMEMO_BROKEN), "NGX003")
        lines = sorted(f.line for f in findings)
        # entry document, the hashed-asset block, /staticfiles and /media all
        # combine `expires` with an explicit add_header Cache-Control.
        assert lines == [12, 20, 27, 34], findings
        assert all(f.level == "error" for f in findings)
        assert "RFC 9111" in findings[0].message

    def test_hashed_asset_block_is_immutable_so_no_NGX002(self):
        """The one thing ironmemo got right — 30d + immutable. Only the
        double-header rule fires there, not the immutability rule."""
        assets = [f for f in _lint(IRONMEMO_BROKEN) if f.line in (19, 20, 21)]
        assert _rules(assets) == ["NGX003"]

    def test_media_is_not_mistaken_for_a_hashed_asset(self):
        """/media is user uploads — never content-hashed. Demanding
        `immutable` there would be a false positive that gets the gate
        switched off."""
        assert not [f for f in _by_rule(_lint(IRONMEMO_BROKEN), "NGX002")]

    def test_proxy_locations_are_untouched(self):
        """/auth proxies to an upstream: cache policy belongs to the upstream,
        and it has no entry document of its own."""
        assert not [f for f in _lint(IRONMEMO_BROKEN) if f.line >= 40]

    def test_whole_conf_rule_set(self):
        assert _rules(_lint(IRONMEMO_BROKEN)) == ["NGX001", "NGX003"]


# ---------------------------------------------------------------------------
# the canon must be clean (a gate that cries wolf gets deleted)
# ---------------------------------------------------------------------------


class TestCanonIsClean:
    def test_scaffold_canon_has_no_findings(self):
        assert _lint(CANON) == []

    def test_expires_off_composes_with_add_header(self):
        """`expires off` makes nginx add NOTHING — the one value that is safe
        to combine with an explicit Cache-Control. NGX003 must not fire."""
        conf = """\
server {
  location / {
    try_files $uri /index.html;
    expires off;
    add_header Cache-Control "no-cache" always;
  }
}
"""
        assert _lint(conf) == []

    def test_bare_static_and_media_prefixes_are_not_hashed_assets(self):
        conf = """\
server {
  location /static/ { alias /static/; }
  location /media/ { alias /media/; }
  location /staticfiles/ { alias /staticfiles/; expires 30d; }
}
"""
        assert _by_rule(_lint(conf), "NGX002") == []


# ---------------------------------------------------------------------------
# individual rules
# ---------------------------------------------------------------------------


class TestEntryDocumentDetection:
    @pytest.mark.parametrize("body", [
        "try_files $uri $uri/ /index.html =404;",
        "try_files $uri $uri/ /index.html;",
        "try_files $uri /kmp/index.html;",
        "index index.html;",
    ])
    def test_spa_fallback_shapes(self, body):
        conf = "server {\n  location / {\n    root /f;\n    %s\n    expires 1d;\n  }\n}\n" % body
        assert _by_rule(_lint(conf), "NGX001"), body

    def test_location_matching_html_directly(self):
        conf = """\
server {
  location = /index.html {
    root /f;
    expires 1h;
  }
}
"""
        assert _by_rule(_lint(conf), "NGX001")

    def test_no_policy_at_all_is_a_warning_not_an_error(self):
        conf = """\
server {
  location / {
    root /f;
    try_files $uri $uri/ /index.html;
  }
}
"""
        findings = _lint(conf)
        assert _rules(findings) == ["NGX004"]
        assert findings[0].level == "warning"
        assert "heuristic freshness" in findings[0].message

    @pytest.mark.parametrize("value", ["off", "-1", "epoch", "0"])
    def test_non_cacheable_expires_values_are_accepted(self, value):
        conf = (
            "server {\n  location / {\n    root /f;\n"
            "    try_files $uri /index.html;\n    expires %s;\n  }\n}\n" % value
        )
        assert _by_rule(_lint(conf), "NGX001") == []

    @pytest.mark.parametrize("value", ["1d", "30d", "max", "1h30m", "12h"])
    def test_cacheable_expires_values_are_rejected(self, value):
        conf = (
            "server {\n  location / {\n    root /f;\n"
            "    try_files $uri /index.html;\n    expires %s;\n  }\n}\n" % value
        )
        assert _by_rule(_lint(conf), "NGX001"), value

    def test_max_age_without_expires_is_still_cacheable(self):
        conf = """\
server {
  location / {
    root /f;
    try_files $uri /index.html;
    add_header Cache-Control "public, max-age=600" always;
  }
}
"""
        assert _by_rule(_lint(conf), "NGX001")

    def test_no_cache_wins_over_a_positive_max_age(self):
        """A client honours no-cache regardless of max-age, so this is not a
        staleness bug — but it IS two headers, so NGX003 still fires."""
        conf = """\
server {
  location / {
    root /f;
    try_files $uri /index.html;
    expires 1d;
    add_header Cache-Control "no-cache" always;
  }
}
"""
        assert _rules(_lint(conf)) == ["NGX003"]


class TestHashedAssets:
    def test_assets_without_immutable_is_an_error(self):
        conf = """\
server {
  location /assets/ {
    root /usr/share/nginx/html;
    add_header Cache-Control "public, max-age=31536000" always;
  }
}
"""
        findings = _by_rule(_lint(conf), "NGX002")
        assert len(findings) == 1
        assert "no `immutable`" in findings[0].message

    def test_assets_cached_too_briefly_is_an_error(self):
        conf = """\
server {
  location /assets/ {
    root /f;
    add_header Cache-Control "public, max-age=60, immutable" always;
  }
}
"""
        assert "max-age=60" in _by_rule(_lint(conf), "NGX002")[0].message

    def test_assets_forced_to_revalidate_is_an_error(self):
        conf = """\
server {
  location /assets/ {
    root /f;
    add_header Cache-Control "no-cache" always;
  }
}
"""
        assert "revalidate" in _by_rule(_lint(conf), "NGX002")[0].message

    @pytest.mark.parametrize("path", ["/assets/", "/_next/static/", "/_nuxt/", "/chunks/"])
    def test_build_output_dirs_are_recognised(self, path):
        conf = "server {\n  location %s {\n    root /f;\n  }\n}\n" % path
        assert _by_rule(_lint(conf), "NGX002"), path

    def test_static_only_when_extension_constrained(self):
        """`static` alone is ambiguous (Django collectstatic is hashed only
        under a manifest storage); a regex pinning js/css is build output."""
        loose = "server {\n  location /static/ {\n    alias /s/;\n  }\n}\n"
        pinned = (
            "server {\n  location ~* ^/static/.*\\.(js|css)$ {\n    root /f;\n  }\n}\n"
        )
        assert _by_rule(_lint(loose), "NGX002") == []
        assert _by_rule(_lint(pinned), "NGX002")


class TestDoubleHeaderRule:
    def test_inherited_server_expires_collides_with_location_add_header(self):
        """`expires` is inherited from server level — the collision happens
        even though the location declares no `expires` of its own."""
        conf = """\
server {
  expires 1d;
  location /api/ {
    proxy_pass http://up;
    add_header Cache-Control "public, max-age=60" always;
  }
}
"""
        findings = _by_rule(_lint(conf), "NGX003")
        assert len(findings) == 1
        assert "inherited from the enclosing block, line 2" in findings[0].message

    def test_two_explicit_cache_control_headers_in_one_block(self):
        conf = """\
server {
  location /x {
    proxy_pass http://up;
    add_header Cache-Control "public" always;
    add_header Cache-Control "max-age=60" always;
  }
}
"""
        assert len(_by_rule(_lint(conf), "NGX003")) == 1

    def test_add_header_does_not_merge_across_levels(self):
        """nginx: a location declaring ANY add_header replaces the inherited
        set — so a server-level Cache-Control does NOT reach this location and
        there is no double header."""
        conf = """\
server {
  add_header Cache-Control "public, max-age=600" always;
  location /x {
    proxy_pass http://up;
    add_header X-Frame-Options "DENY" always;
  }
}
"""
        assert _by_rule(_lint(conf), "NGX003") == []

    def test_inherited_cache_control_reaches_a_location_with_no_add_header(self):
        conf = """\
server {
  add_header Cache-Control "public, max-age=600" always;
  location / {
    root /f;
    try_files $uri /index.html;
  }
}
"""
        assert _by_rule(_lint(conf), "NGX001")


# ---------------------------------------------------------------------------
# parser robustness — a gate that crashes on a real conf is not a gate
# ---------------------------------------------------------------------------


class TestParser:
    def test_envsubst_template_variables(self):
        """`service-configs/nginx-local/default.conf.template` is rendered by
        the nginx image's envsubst entrypoint and is full of ${VAR} — the
        braces there are part of the token, not block structure."""
        conf = """\
server {
  set $stapel_backend http://${BACKEND_UPSTREAM};
  location / {
    proxy_pass $stapel_backend;
  }
}
"""
        root = ncl.parse_conf(conf)
        assert [b.name for b in root.blocks] == ["server"]
        assert len(list(ncl.iter_locations(root))) == 1

    def test_quoted_semicolon_does_not_end_the_directive(self):
        conf = """\
server {
  location / {
    root /f;
    try_files $uri /index.html;
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    expires 1d;
  }
}
"""
        assert _by_rule(_lint(conf), "NGX001")

    def test_comments_and_braces_in_comments(self):
        conf = """\
server {
  # a comment with a brace { and a semicolon ;
  location / {
    root /f;  # trailing comment
    try_files $uri /index.html;
    expires 1d;
  }
}
"""
        assert _by_rule(_lint(conf), "NGX001")

    def test_nested_if_block_parses(self):
        conf = """\
server {
  location /cdn {
    proxy_pass http://up;
    if ($request_method = 'OPTIONS') {
      add_header Cache-Control "no-store";
      return 204;
    }
  }
}
"""
        ncl.parse_conf(conf)  # must not raise

    def test_unbalanced_braces_raise(self):
        with pytest.raises(ncl.NginxParseError):
            ncl.parse_conf("server {\n  location / {\n    root /f;\n}\n")

    @pytest.mark.parametrize("value,seconds", [
        ("1d", 86400), ("30d", 2592000), ("12h", 43200),
        ("1h30m", 5400), ("31536000", 31536000), ("1y", 31536000), ("-1", -1),
    ])
    def test_nginx_time_parsing(self, value, seconds):
        assert ncl.parse_nginx_time(value) == seconds


class TestNoqa:
    def test_blanket_noqa_on_the_location_line(self):
        conf = """\
server {
  location / {  # noqa
    root /f;
    try_files $uri /index.html;
    expires 1d;
    add_header Cache-Control "public" always;
  }
}
"""
        assert _lint(conf) == []

    def test_targeted_noqa_suppresses_only_its_rule(self):
        conf = """\
server {
  location / {  # noqa: NGX003
    root /f;
    try_files $uri /index.html;
    expires 1d;
    add_header Cache-Control "public" always;
  }
}
"""
        assert _rules(_lint(conf)) == ["NGX001"]


# ---------------------------------------------------------------------------
# project discovery + CLI
# ---------------------------------------------------------------------------


def _write_project(tmp_path, conf_text, name="nginx.conf"):
    conf_dir = tmp_path / "service-configs" / "nginx"
    conf_dir.mkdir(parents=True)
    (conf_dir / name).write_text(conf_text)
    return tmp_path


class TestProjectAndCLI:
    def test_discovers_service_configs_nginx(self, tmp_path):
        _write_project(tmp_path, IRONMEMO_BROKEN)
        findings = ncl.lint_project(tmp_path)
        assert _rules(findings) == ["NGX001", "NGX003"]

    def test_discovers_local_conf_template(self, tmp_path):
        local = tmp_path / "service-configs" / "nginx-local"
        local.mkdir(parents=True)
        (local / "default.conf.template").write_text(IRONMEMO_BROKEN)
        assert ncl.lint_project(tmp_path)

    def test_project_without_nginx_is_a_note_not_a_failure(self, tmp_path):
        notes = []
        assert ncl.lint_project(tmp_path, notes=notes) == []
        assert notes and "no nginx conf found" in notes[0]

    def test_cli_exit_1_on_the_defect(self, tmp_path, capsys):
        _write_project(tmp_path, IRONMEMO_BROKEN)
        assert ncl.main([str(tmp_path)]) == 1
        assert "NGX001" in capsys.readouterr().out

    def test_cli_exit_0_on_the_canon(self, tmp_path, capsys):
        _write_project(tmp_path, CANON)
        assert ncl.main([str(tmp_path)]) == 0

    def test_cli_json(self, tmp_path, capsys):
        _write_project(tmp_path, IRONMEMO_BROKEN)
        ncl.main([str(tmp_path), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["errors"] == 5
        assert {f["rule"] for f in payload["findings"]} == {"NGX001", "NGX003"}

    def test_cli_strict_fails_on_the_warning(self, tmp_path):
        conf = "server {\n  location / {\n    root /f;\n    try_files $uri /index.html;\n  }\n}\n"
        _write_project(tmp_path, conf)
        assert ncl.main([str(tmp_path)]) == 0
        assert ncl.main([str(tmp_path), "--strict"]) == 1

    def test_cli_on_a_single_file(self, tmp_path):
        conf = tmp_path / "nginx.ssl.conf"
        conf.write_text(IRONMEMO_BROKEN)
        assert ncl.main([str(conf)]) == 1

    def test_cli_missing_target_is_exit_2(self, tmp_path):
        assert ncl.main([str(tmp_path / "nope")]) == 2

    def test_zero_confs_never_reports_clean(self, tmp_path, capsys):
        """"No issues found" about a target it never read is a lie.

        A directory with no nginx conf printed the note "nothing to check"
        to stderr and "No SPA cache-canon issues found" to stdout — and a
        reader (or a CI log scraper) sees the second line. A gate that
        reports success on zero inputs is the defect class this linter
        exists to catch, so it must say what it actually did.
        """
        (tmp_path / "service-configs").mkdir()
        assert ncl.main([str(tmp_path)]) == 0  # still not a failure: a
        # library repo legitimately has no nginx conf at all
        out = capsys.readouterr().out
        assert "Checked 0 nginx confs" in out
        assert "No SPA cache-canon issues found" not in out


# ---------------------------------------------------------------------------
# live mode — the half a static check cannot reach
# ---------------------------------------------------------------------------

_ENTRY_HTML = (
    '<!doctype html><html><head>'
    '<script type="module" crossorigin src="/assets/index-D4f8x2a1.js"></script>'
    '<link rel="stylesheet" crossorigin href="/assets/index-9bK2p0Zz.css">'
    '</head><body><div id="root"></div></body></html>'
)


class _Handler(BaseHTTPRequestHandler):
    #: set per-test: {path: [(header, value), ...]}
    headers_by_path: dict = {}

    def log_message(self, *args):  # keep the test output clean
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        body = (_ENTRY_HTML if path == "/" else "console.log(1)").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html" if path == "/" else "text/javascript")
        self.send_header("Content-Length", str(len(body)))
        for name, value in self.headers_by_path.get(path, []):
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


# NB: not named `live_server` — pytest-django ships a fixture by that name
# which skips itself ("no Django settings") and takes precedence here.
@pytest.fixture
def header_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _base(server):
    return f"http://127.0.0.1:{server.server_address[1]}/"


class TestLiveMode:
    def test_the_stand_as_it_actually_served_it(self, header_server):
        """What app.ironmemo.com really put on the wire: TWO Cache-Control
        headers (nginx's own from `expires 1d`, plus the explicit one)."""
        _Handler.headers_by_path = {
            "/": [
                ("Cache-Control", "max-age=86400"),
                ("Cache-Control", "public, must-revalidate"),
            ],
            "/assets/index-D4f8x2a1.js": [("Cache-Control", "public, immutable")],
        }
        findings = ncl.lint_live(_base(header_server))
        rules = sorted({f.rule for f in findings})
        assert "NGX001" in rules, "entry document cacheable for a day"
        assert "NGX003" in rules, "two Cache-Control headers on the wire"
        assert "86400s" in " ".join(f.message for f in findings)

    def test_the_canon_on_the_wire_is_clean(self, header_server):
        _Handler.headers_by_path = {
            "/": [("Cache-Control", "no-cache, must-revalidate")],
            "/assets/index-D4f8x2a1.js": [
                ("Cache-Control", "public, max-age=31536000, immutable"),
            ],
        }
        assert ncl.lint_live(_base(header_server)) == []

    def test_asset_not_immutable_on_the_wire(self, header_server):
        _Handler.headers_by_path = {
            "/": [("Cache-Control", "no-store")],
            "/assets/index-D4f8x2a1.js": [("Cache-Control", "public, max-age=300")],
        }
        findings = ncl.lint_live(_base(header_server))
        assert [f.rule for f in findings] == ["NGX002"]

    def test_missing_cache_control_on_the_wire_is_a_warning(self, header_server):
        _Handler.headers_by_path = {
            "/": [],
            "/assets/index-D4f8x2a1.js": [
                ("Cache-Control", "public, max-age=31536000, immutable"),
            ],
        }
        findings = ncl.lint_live(_base(header_server))
        assert [(f.rule, f.level) for f in findings] == [("NGX004", "warning")]

    def test_cli_live_exit_codes(self, header_server, tmp_path):
        _Handler.headers_by_path = {
            "/": [
                ("Cache-Control", "max-age=86400"),
                ("Cache-Control", "public, must-revalidate"),
            ],
            "/assets/index-D4f8x2a1.js": [("Cache-Control", "public, immutable")],
        }
        assert ncl.main([str(tmp_path), "--live", _base(header_server)]) == 1
        _Handler.headers_by_path = {
            "/": [("Cache-Control", "no-cache, must-revalidate")],
            "/assets/index-D4f8x2a1.js": [
                ("Cache-Control", "public, max-age=31536000, immutable"),
            ],
        }
        assert ncl.main([str(tmp_path), "--live", _base(header_server)]) == 0

    def test_unreachable_stand_is_exit_2(self, tmp_path):
        assert ncl.main([str(tmp_path), "--live", "http://127.0.0.1:1/"]) == 2

    def test_asset_extraction(self):
        assert ncl.extract_hashed_asset(_ENTRY_HTML) == "/assets/index-D4f8x2a1.js"
        assert ncl.extract_hashed_asset("<html><body>no assets</body></html>") is None

    def test_no_asset_reference_is_a_warning_not_a_pass(self):
        findings = ncl.evaluate_live("http://x/", ["no-cache"], None, None)
        assert [(f.rule, f.level) for f in findings] == [("NGX002", "warning")]


# ---------------------------------------------------------------------------
# wiring — the gate has to actually run somewhere
# ---------------------------------------------------------------------------


class TestWiring:
    def test_stapel_verify_composes_the_nginx_cache_linter(self, tmp_path):
        """stapel-verify is what every generated project's pre-commit runs;
        composing the checker there is what reaches projects that already
        exist without regenerating them."""
        from stapel_tools import verify

        _write_project(tmp_path, IRONMEMO_BROKEN)
        reports = verify.verify_project(tmp_path)
        by_name = {r.name: r for r in reports}
        assert "stapel-nginx-cache-lint" in by_name
        report = by_name["stapel-nginx-cache-lint"]
        assert report.errors == 5
        assert {f["rule"] for f in report.findings} == {"NGX001", "NGX003"}

    def test_stapel_verify_exit_code_carries_the_failure(self, tmp_path, capsys):
        from stapel_tools import verify

        _write_project(tmp_path, IRONMEMO_BROKEN)
        assert verify.main([str(tmp_path)]) == 1
        assert "NGX001" in capsys.readouterr().out

    def test_console_script_is_declared(self):
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        scripts = tomllib.loads(pyproject.read_text())["project"]["scripts"]
        assert scripts["stapel-nginx-cache-lint"] == "stapel_tools.nginx_cache_lint:main"
