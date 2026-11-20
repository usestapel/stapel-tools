"""The KMP emitter (``stapel-create-project --mobile kmp``): plan, refusals, wiring.

The fast tests here hold the plan to its promises without a JDK. The proof
that the planned tree BUILDS and RUNS (Gradle, Android, desktop, Xcode, the
iOS simulator, the sign-in walk against the scaffolded backend) is the slow
opt-in test at the bottom: it needs the toolchain and a stapel-kmp Maven
repository and is SKIPPED, with the reason, when either is missing — a
machine that could not run it never reports it green.
"""
from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from stapel_tools.composition.emitter import build_composition, emitter_for
from stapel_tools.composition.kmp import contract, registry, vendor
from stapel_tools.composition.kmp.emitter import (
    default_app_id,
    kotlin_package,
    plan_routes,
)
from stapel_tools.composition.model import CompositionError, PlatformRequest

ROOT = Path(__file__).resolve().parents[1]
CTX = {"slug": "acme", "title": "Acme $ App", "url": "https://acme.example"}


def _auth(model="public", kinds=("phone", "email", "password")):
    from stapel_tools._auth_model import AuthComposition

    return AuthComposition(model=model, kinds=kinds, stage="prototype")


def _request(**options):
    base = {"repo": "local", "dev_port": 8000,
            "custom": (("acme", "/api/", "..", ("/api/schema/",)),)}
    base.update(options)
    return PlatformRequest("kmp", options=tuple(base.items()))


def _comp(modules=("auth",), auth=None, project_type="minimal", request=None, **kw):
    request = request or _request()
    comp = build_composition(
        ctx=CTX, project_type=project_type, feature_libs=list(modules),
        auth_comp=auth if auth is not None else _auth(), mobile=request, **kw)
    return comp, request


def _plan(**kw):
    comp, request = _comp(**kw)
    emitter = emitter_for("kmp")
    emitter.validate(comp, request)
    return emitter.plan(comp, request)


class TestPlan:
    def test_tree_matches_the_reference_app_layout(self):
        plan = _plan()
        files = set(plan.files)
        for rel in (
            "settings.gradle.kts", "build.gradle.kts", "gradle.properties", "gradlew",
            "gradle/wrapper/gradle-wrapper.jar", "gradle/wrapper/gradle-wrapper.properties",
            "stapel.composition.json", "stapel.theme.json", "stapel.environments.json",
            "shared/build.gradle.kts", "shared/src/commonMain/kotlin/acme/App.kt",
            "shared/src/androidMain/kotlin/acme/AndroidApp.kt",
            "shared/src/iosMain/kotlin/acme/MainViewController.kt",
            "shared/src/jvmMain/kotlin/acme/DesktopApp.kt",
            "shared/src/commonTest/kotlin/acme/NavGateTest.kt",
            "shared/src/walkTest/kotlin/acme/SignInWalkTest.kt",
            "androidApp/src/main/AndroidManifest.xml", "desktopApp/src/main/kotlin/acme/desktop/Main.kt",
            "iosApp/iosApp.xcodeproj/project.pbxproj", "iosApp/Configuration/Config.xcconfig",
            "scripts/ios-smoke.sh", "scripts/mobile-check.sh", "Makefile", "README.md",
        ):
            assert f"mobile/{rel}" in files, rel
        assert plan.modes["mobile/gradlew"] == 0o755
        assert plan.modes["mobile/scripts/mobile-check.sh"] == 0o755

    def test_composition_names_installed_custom_and_routes(self):
        doc = _plan().composition
        assert doc["platform"] == "kmp"
        assert doc["installed"] == ["auth"]
        assert doc["custom"] == ["acme"]
        assert doc["signIn"] == "auth.login"
        assert doc["routes"] == {"auth.login": "/login", "app.home": "/app/home"}
        assert doc["shell"]["slots"] == {"topbar.end": ["auth.account_menu"]}
        assert doc["offline"] is False

    def test_theme_is_the_compositions_theme_byte_for_byte(self):
        comp, request = _comp(theme="ocean")
        plan = emitter_for("kmp").plan(comp, request)
        assert plan.files["mobile/stapel.theme.json"] == comp.theme_json

    def test_offline_adds_the_artifact_and_one_line(self):
        off = _plan()
        on = _plan(request=_request(offline=True))
        assert "stapel.cache.room" not in off.files["mobile/shared/build.gradle.kts"]
        assert "implementation(libs.stapel.cache.room)" in on.files["mobile/shared/build.gradle.kts"]
        app = on.files["mobile/shared/src/commonMain/kotlin/acme/App.kt"]
        assert app.count("offline(cacheRoomModule(directory = dataDirectory))") == 1
        # desktop has no platform cache directory: the app names one
        assert "mobile/shared/src/jvmMain/kotlin/acme/DataDirectory.jvm.kt" in on.files
        assert "offline(" not in off.files["mobile/shared/src/commonMain/kotlin/acme/App.kt"]

    def test_pending_lists_every_selected_module_without_a_kmp_pair(self):
        comp, request = _comp(modules=("auth", "profiles", "listings"), project_type="monolith")
        plan = emitter_for("kmp").plan(comp, request)
        # profiles has a KMP brick since stapel-kmp 5a9b3e7; listings has none yet.
        assert [p["module"] for p in plan.composition["pending"]] == ["listings"]
        assert any("listings is pending" in n for n in plan.notes)

    def test_environments_per_platform_and_prod_from_the_url(self):
        env = json.loads(_plan().files["mobile/stapel.environments.json"])
        dev = env["environments"]["dev"]["baseUrl"]
        assert dev["android"] == "http://10.0.2.2:8000/"
        assert dev["ios"] == dev["desktop"] == "http://127.0.0.1:8000/"
        assert env["environments"]["prod"]["baseUrl"] == "https://acme.example/"

    def test_title_is_escaped_for_kotlin_and_xml(self):
        plan = _plan()
        assert '"Acme \\$ App"' in plan.files["mobile/desktopApp/src/main/kotlin/acme/desktop/Main.kt"]
        assert 'android:label="Acme $ App"' in plan.files["mobile/androidApp/src/main/AndroidManifest.xml"]

    def test_bricks_come_from_their_library_descriptor(self):
        plan = _plan()
        binding = registry.kmp_bindings()["auth"]
        app = plan.files["mobile/shared/src/commonMain/kotlin/acme/App.kt"]
        assert f"import {binding.descriptor_class}\n" in app
        assert f"bricks({binding.descriptor_name}())" in app

    def test_a_brick_without_a_present_descriptor_is_pending(self, monkeypatch):
        from dataclasses import replace

        real = registry.kmp_bindings()
        absent = {**real, "auth": replace(real["auth"], descriptor_present=False)}
        monkeypatch.setattr(registry, "kmp_bindings", lambda: absent)
        plan = _plan()
        assert plan.composition["installed"] == []
        assert plan.composition["pending"][0]["module"] == "auth"
        assert "bricks(" not in plan.files["mobile/shared/src/commonMain/kotlin/acme/App.kt"]

    def test_wrapper_jar_is_the_pinned_gradle_one(self):
        jar = _plan().files["mobile/gradle/wrapper/gradle-wrapper.jar"]
        pin = vendor.load_pin()
        assert hashlib.sha256(jar).hexdigest() == pin["gradle"]["wrapperJarSha256"]

    def test_plan_opens_no_file(self, monkeypatch):
        comp, request = _comp(request=_request(offline=True))
        emitter_for("kmp").plan(comp, request)   # warm-up: imports

        def refuse(*a, **k):
            raise AssertionError(f"plan() opened {a[:1]}")

        monkeypatch.setattr(builtins, "open", refuse)
        monkeypatch.setattr(io, "open", refuse)
        monkeypatch.setattr(Path, "read_bytes", refuse)
        monkeypatch.setattr(Path, "read_text", refuse)
        plan = emitter_for("kmp").plan(comp, request)
        assert plan.root == "mobile"


class TestRefusals:
    def test_no_completable_kind_refuses(self):
        comp, request = _comp(auth=_auth(kinds=("oauth", "passkey")))
        with pytest.raises(CompositionError, match="none of the selected sign-in kinds"):
            emitter_for("kmp").validate(comp, request)

    def test_mixed_kinds_leave_the_others_out_and_say_so(self):
        plan = _plan(auth=_auth(kinds=("email", "oauth", "magic")))
        methods = plan.composition["signInMethods"]
        assert methods["rendered"] == ["email"]
        assert [a["kind"] for a in methods["absent"]] == ["oauth", "magic"]
        assert any("oauth is absent" in n for n in plan.notes)

    def test_no_email_means_no_walk_test_and_a_note(self):
        plan = _plan(auth=_auth(kinds=("phone",)))
        assert not any("SignInWalkTest" in f for f in plan.files)
        assert any("no sign-in walk test" in n for n in plan.notes)

    def test_unreleased_train_needs_an_explicit_repository(self):
        comp, request = _comp(request=_request(repo="central"))
        if not registry.unreleased_artifacts(("auth",), offline=False):
            pytest.skip("the vendored train is on Maven Central")
        with pytest.raises(CompositionError, match="--mobile-maven-repo"):
            emitter_for("kmp").validate(comp, request)

    def test_microservices_is_refused(self):
        comp, request = _comp(project_type="microservices")
        with pytest.raises(CompositionError, match="not supported yet"):
            emitter_for("kmp").validate(comp, request)

    def test_bad_app_id_is_refused(self):
        comp, request = _comp(request=_request(app_id="Com.Bad_Id"))
        with pytest.raises(CompositionError, match="--mobile-id"):
            emitter_for("kmp").validate(comp, request)


class TestNaming:
    @pytest.mark.parametrize("slug,url,want", [
        ("golden", "https://golden.example", "example.golden"),
        ("shop", "https://www.shop.example.com", "com.example.shop"),
        ("my-app", "", "com.example.myapp"),
        ("x", "http://127.0.0.1:8000", "com.example.x"),
    ])
    def test_app_id(self, slug, url, want):
        assert default_app_id(slug, url) == want

    def test_kotlin_package_avoids_keywords_and_digits(self):
        assert kotlin_package("my-shop") == "my_shop"
        assert kotlin_package("object") == "appobject"
        assert kotlin_package("9lives") == "app9lives"

    def test_submenu_without_its_parent_is_dropped_with_a_note(self):
        from stapel_tools.composition.bricks import BRICK_UI

        entries = [e for e in BRICK_UI["auth"].nav if e.id in ("auth.login", "auth.security")]
        routes, notes = plan_routes(entries, True)
        assert "auth.security" not in routes
        assert any("auth.security" in n for n in notes)


class TestVendorAndContract:
    def test_vendored_digests_match_the_pin(self):
        assert vendor.digest_problems() == []

    def test_a_tampered_file_fails_the_digest_check(self, tmp_path, monkeypatch):
        copy = tmp_path / "vendored"
        shutil.copytree(vendor.VENDORED, copy)
        (copy / "shell-templates.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(vendor, "VENDORED", copy)
        monkeypatch.setattr(vendor, "PIN", copy / "pin.json")
        assert any("shell-templates.json" in p for p in vendor.digest_problems())

    def test_contract_slice_keeps_the_module_and_its_closure(self):
        schema = {
            "openapi": "3.0.3",
            "paths": {
                "/api/notes/": {"get": {"responses": {"200": {"content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Note"}}}}}}},
                "/api/schema/": {"get": {}},
                "/auth/api/v1/me/": {"get": {"responses": {"200": {"content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Me"}}}}}}},
            },
            "components": {"schemas": {
                "Note": {"properties": {"tag": {"$ref": "#/components/schemas/Tag"}}},
                "Tag": {"type": "string"}, "Me": {"type": "object"},
            }, "securitySchemes": {"jwtAuth": {"type": "http"}}},
        }
        out = contract.slice_schema(schema, "/api/", ("/api/schema/",))
        assert list(out["paths"]) == ["/api/notes/"]
        assert set(out["components"]["schemas"]) == {"Note", "Tag"}
        assert "jwtAuth" in out["components"]["securitySchemes"]


class TestNavGateKmpRow:
    def test_inversion_a_present_descriptor_with_a_drifted_manifest_fails(self, monkeypatch, capsys):
        sys.path.insert(0, str(ROOT / "scripts"))
        import check_nav_manifest_sync as gate

        real = registry.kmp_bindings()["auth"]
        from dataclasses import replace

        present = replace(real, descriptor_present=True)
        monkeypatch.setattr(registry, "kmp_bindings", lambda: {"auth": present})
        manifest = {"destinations": [{**d, "path": "/sign-in"} if d["id"] == "auth.login" else d
                                     for d in registry.project_kmp("auth")],
                    "embeds": [{"id": "auth.account_menu"}]}
        monkeypatch.setattr(registry, "nav_manifest", lambda key: manifest)
        assert gate.check_kmp(Path("/nonexistent")) == 1
        assert "differ from the neutral entries" in capsys.readouterr().out

    def test_a_matching_manifest_is_compared_green(self, monkeypatch, capsys):
        sys.path.insert(0, str(ROOT / "scripts"))
        from dataclasses import replace

        import check_nav_manifest_sync as gate

        real = registry.kmp_bindings()["auth"]
        present = replace(real, descriptor_present=True)
        monkeypatch.setattr(registry, "kmp_bindings", lambda: {"auth": present})
        manifest = {"destinations": [{**d, "presentation": "screen", "requires": []}
                                     for d in registry.project_kmp("auth")],
                    "embeds": [{"id": "auth.account_menu"}]}
        monkeypatch.setattr(registry, "nav_manifest", lambda key: manifest)
        assert gate.check_kmp(Path("/nonexistent")) == 0
        assert "1 brick(s) with a present descriptor compared (auth)" in capsys.readouterr().out


class TestCli:
    def _run(self, tmp_path, *flags, name="mob", project_type="minimal"):
        from stapel_tools.create_project import main

        argv = ["stapel-create-project", name, "--type", project_type, "--title", "Mob",
                "--url", "https://mob.example", "--company-name", "Acme",
                "--company-email", "a@mob.example", "--no-git", "--output-dir", str(tmp_path),
                *flags]
        old = sys.argv
        sys.argv = argv
        try:
            main()
        finally:
            sys.argv = old
        return tmp_path / name

    def test_mobile_flags_need_mobile(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            self._run(tmp_path, "--modules", "auth", "--mobile-offline")
        assert "need --mobile kmp" in capsys.readouterr().err
        assert not (tmp_path / "mob").exists()

    def test_refusal_happens_before_anything_is_written(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            self._run(tmp_path, "--modules", "auth", "--auth-model", "public",
                      "--auth-kinds", "oauth", "--mobile", "kmp", "--mobile-maven-repo", "local")
        assert "none of the selected sign-in kinds" in capsys.readouterr().err
        assert not (tmp_path / "mob").exists()

    def test_monolith_serves_association_files_and_theme_matches_the_web(self, tmp_path):
        project = self._run(tmp_path, "--modules", "auth", "--theme", "ocean",
                            "--mobile", "kmp", "--mobile-maven-repo", "local",
                            project_type="monolith")
        assert (project / "mobile/stapel.theme.json").read_bytes() == \
            (project / "frontend/stapel.theme.json").read_bytes()
        conf = (project / "service-configs/nginx/nginx.conf").read_text()
        assert "location = /.well-known/apple-app-site-association" in conf
        assert "location = /.well-known/assetlinks.json" in conf
        aasa = json.loads((project / "service-configs/nginx/well-known/apple-app-site-association").read_text())
        assert aasa["applinks"]["details"][0]["appIDs"] == ["TEAM_ID_PLACEHOLDER.example.mob"]
        assert "mobile-check:" in (project / "Makefile").read_text()
        assert os.access(project / "mobile/gradlew", os.X_OK)

    def test_minimal_writes_association_files_under_mobile(self, tmp_path):
        project = self._run(tmp_path, "--modules", "auth", "--mobile", "kmp",
                            "--mobile-maven-repo", "local", "--mobile-id", "org.acme.mob")
        assert (project / "mobile/well-known/assetlinks.json").is_file()
        gradle = (project / "mobile/androidApp/build.gradle.kts").read_text()
        assert 'applicationId = "org.acme.mob"' in gradle
        assert "mobile-check:" in (project / "Makefile").read_text()


# --------------------------------------------------------------------------- slow, opt-in

_TOOLCHAIN_REASON = None
if os.environ.get("STAPEL_KMP_PROOF") != "1":
    _TOOLCHAIN_REASON = "opt-in: STAPEL_KMP_PROOF=1 (needs JDK 21, Android SDK, Xcode, a stapel-kmp repo)"
elif not os.environ.get("STAPEL_KMP_MAVEN_REPO"):
    _TOOLCHAIN_REASON = ("STAPEL_KMP_MAVEN_REPO unset: point it at a stapel-kmp staging repository "
                         "or 'local' (the train is not on Maven Central)")


@pytest.mark.slow
@pytest.mark.skipif(_TOOLCHAIN_REASON is not None, reason=_TOOLCHAIN_REASON or "")
def test_generated_app_passes_every_gate_and_signs_in(tmp_path):
    """Scaffold minimal+auth with --mobile kmp, bring the SAME backend up on a
    free local port, run mobile/scripts/mobile-check.sh with the walk. STRICT:
    a gate this machine cannot run fails the test instead of passing it."""
    import socket

    repo = os.environ["STAPEL_KMP_MAVEN_REPO"]
    t0 = time.monotonic()
    project = TestCli()._run(
        tmp_path, "--modules", "auth", "--auth-model", "public",
        "--auth-kinds", "phone,email,password", "--posture", "prototype",
        "--mobile", "kmp", "--mobile-maven-repo", repo, name="ksbproof")
    venv = project / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    subprocess.run([str(venv / "bin/pip"), "install", "-q", "-r", "requirements.txt"],
                   cwd=project, check=True)
    subprocess.run([str(venv / "bin/python"), "manage.py", "migrate", "--noinput"],
                   cwd=project, check=True, capture_output=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen(
        [str(venv / "bin/python"), "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
        cwd=project, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(5)
        result = subprocess.run(
            ["scripts/mobile-check.sh"], cwd=project / "mobile", capture_output=True, text=True,
            env={**os.environ, "STRICT": "1", "WALK_BASE_URL": f"http://127.0.0.1:{port}/"})
    finally:
        server.terminate()
        server.wait(timeout=30)
    print(f"scaffold -> all gates: {time.monotonic() - t0:.0f} s\n{result.stdout}")
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert "NOT RUN" not in result.stdout
