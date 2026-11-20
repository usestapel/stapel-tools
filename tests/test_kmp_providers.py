"""The KMP emitter's vendor providers: analytics, push, the Swift bridges, K17's media uploader.

ARCH-PUSH-ANALYTICS §4.3 / §6 / §7 (stapel-tools KMP emitter): what ``--mobile-analytics``,
``--mobile-consent``, ``--mobile-push``, ``--mobile-push-ask`` and ``--mobile-firebase-config``
plan per platform, and the refusals that come before anything is written.
"""
from __future__ import annotations

import json
import plistlib

import pytest

from stapel_tools.composition.emitter import build_composition, emitter_for
from stapel_tools.composition.kmp import vendor
from stapel_tools.composition.model import CompositionError, PlatformRequest

CTX = {"slug": "acme", "title": "Acme", "url": "https://acme.example"}
APP_ID = "example.acme"
BRIDGES = (
    "ios-bridges/core/StapelAppDelegate.swift",
    "ios-bridges/firebase/StapelFirebase.swift",
    "ios-bridges/firebase/StapelFirebasePush.swift",
    "ios-bridges/firebase/StapelFirebaseAnalytics.swift",
    "ios-bridges/appmetrica/StapelAppMetrica.swift",
    "ios-bridges/appmetrica/StapelAppMetricaAnalytics.swift",
)


def _auth(model="public", kinds=("phone", "email", "password")):
    from stapel_tools._auth_model import AuthComposition

    return AuthComposition(model=model, kinds=kinds, stage="prototype")


def _request(**options):
    base = {"repo": "local", "dev_port": 8000,
            "custom": (("acme", "/api/", "..", ("/api/schema/",)),)}
    base.update(options)
    return PlatformRequest("kmp", options=tuple(base.items()))


def _comp(modules=("auth",), auth=None, request=None):
    request = request or _request()
    comp = build_composition(
        ctx=CTX, project_type="minimal", feature_libs=list(modules),
        auth_comp=auth if auth is not None else _auth(), mobile=request)
    return comp, request


def _plan(modules=("auth",), auth=None, **options):
    comp, request = _comp(modules=modules, auth=auth, request=_request(**options))
    emitter = emitter_for("kmp")
    emitter.validate(comp, request)
    return emitter.plan(comp, request)


def _refused(modules=("auth",), auth=None, **options) -> str:
    comp, request = _comp(modules=modules, auth=auth, request=_request(**options))
    with pytest.raises(CompositionError) as err:
        emitter_for("kmp").validate(comp, request)
    return str(err.value)


class TestVendoredBridges:
    def test_the_swift_bridges_are_vendored_at_the_pin(self):
        pin = vendor.load_pin()
        for rel in BRIDGES:
            assert pin["files"][rel]["source"] == rel
            assert vendor.read_bytes(rel).startswith(b"// Stapel iOS bridge")

    def test_the_reference_apps_info_plist_is_vendored(self):
        plist = plistlib.loads(vendor.read_bytes("sample-ios/Info.plist"))
        assert plist["FirebaseAppDelegateProxyEnabled"] is False


SHARED = "mobile/shared/build.gradle.kts"
APP = "mobile/shared/src/commonMain/kotlin/acme/App.kt"
PLIST = "mobile/iosApp/iosApp/Info.plist"
PBX = "mobile/iosApp/iosApp.xcodeproj/project.pbxproj"
SWIFT_APP = "mobile/iosApp/iosApp/iOSApp.swift"
STAPEL_SWIFT = "mobile/iosApp/iosApp/Stapel/"
PUSH_MODULES = ("auth", "notifications")


def _text(plan, rel) -> str:
    content = plan.files[rel]
    return content.decode("utf-8") if isinstance(content, bytes) else content


def _plist(plan) -> dict:
    return plistlib.loads(_text(plan, PLIST).encode("utf-8"))


def _swift_files(plan) -> set[str]:
    return {rel[len(STAPEL_SWIFT):] for rel in plan.files if rel.startswith(STAPEL_SWIFT)}


def _fake_android_config(tmp_path, package="example.acme"):
    path = tmp_path / "google-services.json"
    path.write_text(json.dumps({"project_info": {"project_id": "fake"}, "client": [
        {"client_info": {"android_client_info": {"package_name": package}}}]}), encoding="utf-8")
    return path


def _fake_ios_config(tmp_path, bundle="example.acme"):
    path = tmp_path / "GoogleService-Info.plist"
    path.write_bytes(plistlib.dumps({"BUNDLE_ID": bundle, "PROJECT_ID": "fake"}))
    return path


class TestAnalytics:
    def test_bare_flag_is_firebase_behind_the_hub_with_opt_in(self):
        plan = _plan(analytics="")
        assert plan.composition["analytics"] == {"sinks": ["firebase"], "consent": "opt-in"}
        analytics = _text(plan, "mobile/shared/src/commonMain/kotlin/acme/AppAnalytics.kt")
        assert "consentDefault = ConsentDefault.OptIn,\n" in analytics
        assert "registry = AuthEvents.registry,\n" in analytics
        # desktop keeps the hub's queue and consent in a per-user directory named by the namespace
        assert 'storageNamespace = "stapel-analytics-example.acme",\n' in analytics
        assert "import stapel.auth.api.AuthEvents\n" in analytics
        assert 'contribute<AnalyticsSink>("analytics.log") { LogSink(get()) }' in analytics
        shared = _text(plan, SHARED)
        assert "implementation(libs.stapel.analytics)\n" in shared
        assert "implementation(libs.stapel.analytics.firebase)\n" in shared
        assert "export(libs.stapel.analytics.firebase)" in shared
        assert "iosMain.dependencies {\n            api(libs.stapel.analytics.firebase)" in shared
        app = _text(plan, APP)
        assert "modules(*appAnalyticsModules(target).toTypedArray())" in app
        assert "modules(*platformProviderModules().toTypedArray())" in app
        assert "bindAnalyticsIdentity(app)" in app
        assert "AnalyticsConsentSheet(context.koin)" in app

    def test_opt_in_turns_vendor_collection_off_in_the_built_ios_app(self):
        assert _plist(_plan(analytics=""))["FIREBASE_ANALYTICS_COLLECTION_ENABLED"] is False
        opt_out = _plan(analytics="", consent="opt-out")
        assert "FIREBASE_ANALYTICS_COLLECTION_ENABLED" not in _plist(opt_out)
        assert "ConsentDefault.OptOut" in _text(opt_out, "mobile/shared/src/commonMain/kotlin/acme/AppAnalytics.kt")
        assert opt_out.composition["analytics"]["consent"] == "opt-out"

    def test_ios_shell_gets_the_vendored_bridges_and_the_swift_package(self):
        plan = _plan(analytics="")
        assert _swift_files(plan) == {"StapelFirebase.swift", "StapelFirebaseAnalytics.swift"}
        for name in _swift_files(plan):
            assert plan.files[STAPEL_SWIFT + name] == \
                vendor.read_bytes(f"ios-bridges/firebase/{name}").decode("utf-8")
        assert _plist(plan)["FirebaseAppDelegateProxyEnabled"] is False
        swift = _text(plan, SWIFT_APP)
        assert "IosBridgesKt.installBridges(firebaseAnalytics: StapelFirebaseAnalytics.shared)" in swift
        assert "UIApplicationDelegateAdaptor" not in swift
        pbx = _text(plan, PBX)
        assert 'repositoryURL = "https://github.com/firebase/firebase-ios-sdk";' in pbx
        assert "version = 13.2.0;" in pbx   # ios-bridges/Package.swift at the pin
        assert "productName = FirebaseAnalyticsCore;" in pbx
        assert "FirebaseMessaging" not in pbx
        bridges = _text(plan, "mobile/shared/src/iosMain/kotlin/acme/IosBridges.kt")
        assert "fun installBridges(firebaseAnalytics: FirebaseAnalyticsBridge?)" in bridges

    def test_the_echo_names_artifacts_bridge_abi_and_versions_from_the_pin(self):
        provs = _plan(analytics="").composition["providers"]
        firebase = provs["firebase"]
        assert firebase["artifacts"] == {"analytics": "stapel-analytics-firebase"}
        assert firebase["bridgeAbi"] == {"analytics": {"constant": "FIREBASE_ANALYTICS_BRIDGE_ABI", "abi": 1}}
        assert firebase["ios"]["spm"]["exactVersion"] == "13.2.0"
        assert firebase["android"]["gradlePlugin"]["version"] == "4.6.0"
        assert firebase["configured"] == {"android": False, "ios": False}

    def test_android_applies_google_services_only_when_the_file_exists(self):
        gradle = _text(_plan(analytics=""), "mobile/androidApp/build.gradle.kts")
        assert 'classpath("com.google.gms:google-services:4.6.0")' in gradle
        assert 'if (projectDir.resolve("google-services.json").exists())' in gradle
        assert 'if (firebaseConfig.exists()) apply(plugin = "com.google.gms.google-services")' in gradle
        assert 'tasks.matching { it.name == "preReleaseBuild" }' in gradle
        assert "google-services" not in _text(_plan(), "mobile/androidApp/build.gradle.kts")

    def test_first_party_sink_joins_when_the_backend_has_analytics(self):
        plan = _plan(modules=("auth", "analytics"), analytics="")
        assert plan.composition["analytics"]["sinks"] == ["firebase", "stapel"]
        analytics = _text(plan, "mobile/shared/src/commonMain/kotlin/acme/AppAnalytics.kt")
        assert "analyticsCollectorModule(CollectorOptions(source = target))," in analytics
        assert "implementation(libs.stapel.analytics.collector)" in _text(plan, SHARED)

    def test_stapel_alone_needs_no_vendor(self):
        plan = _plan(modules=("auth", "analytics"), analytics="stapel")
        assert plan.composition["analytics"]["sinks"] == ["stapel"]
        assert not _swift_files(plan)
        assert not any("AppProviders" in rel for rel in plan.files)
        assert "firebase" not in _text(plan, SHARED)

    def test_stapel_without_the_analytics_module_is_refused(self):
        assert "not among the modules" in _refused(analytics="stapel")

    def test_unknown_sink_is_refused(self):
        assert "one or more of firebase, appmetrica, stapel" in _refused(analytics="mixpanel")

    def test_appmetrica_instead_of_firebase_has_no_firebase_anywhere(self):
        plan = _plan(analytics="appmetrica")
        for rel, content in plan.files.items():
            text = content if isinstance(content, str) else ""
            assert "firebase" not in text.lower() or rel.endswith("README.md"), rel
        assert _swift_files(plan) == {"StapelAppMetrica.swift", "StapelAppMetricaAnalytics.swift"}
        pbx = _text(plan, PBX)
        assert 'repositoryURL = "https://github.com/appmetrica/appmetrica-sdk-ios";' in pbx
        assert "productName = AppMetricaCore;" in pbx
        assert "StapelAppMetrica.apiKey = IosBridgesKt.appMetricaApiKey()" in _text(plan, SWIFT_APP)
        assert "appMetricaAnalyticsModule(APPMETRICA_API_KEY)" in \
            _text(plan, "mobile/shared/src/androidMain/kotlin/acme/AppProviders.android.kt")
        assert "appMetricaAnalyticsModule(appMetricaAnalyticsBridge)" in \
            _text(plan, "mobile/shared/src/iosMain/kotlin/acme/AppProviders.ios.kt")
        assert "generateAppSecrets" in _text(plan, SHARED)
        assert "stapel.secrets.properties\n" in _text(plan, "mobile/.gitignore")
        assert "appmetrica.apiKey=\n" in _text(plan, "mobile/stapel.secrets.properties.example")

    def test_the_providers_gate_holds_the_running_app_to_the_plan(self):
        gate = _text(_plan(analytics=""), "mobile/shared/src/commonTest/kotlin/acme/ProvidersGateTest.kt")
        assert 'assertEquals(setOf("firebase", "log"), app.koin.getAll<AnalyticsSink>()' in gate
        assert "ProvidersGateTest" not in "".join(_plan().files)


class TestFirebaseConfig:
    def _config(self, tmp_path, **ids):
        from stapel_tools.composition.kmp.providers import read_firebase_config

        parts = []
        if "android" in ids:
            parts.append(f"android={_fake_android_config(tmp_path, ids['android'])}")
        if "ios" in ids:
            parts.append(f"ios={_fake_ios_config(tmp_path, ids['ios'])}")
        return read_firebase_config(",".join(parts))

    def test_matching_files_are_written_gitignored_and_recorded(self, tmp_path):
        config = self._config(tmp_path, android=APP_ID, ios=APP_ID)
        plan = _plan(analytics="", firebase_config=config)
        assert plan.files["mobile/androidApp/google-services.json"] == config["android"]["data"]
        assert plan.files["mobile/iosApp/iosApp/GoogleService-Info.plist"] == config["ios"]["data"]
        ignore = _text(plan, "mobile/.gitignore")
        assert "androidApp/google-services.json\n" in ignore
        assert "iosApp/iosApp/GoogleService-Info.plist\n" in ignore
        assert plan.composition["providers"]["firebase"]["configured"] == {"android": True, "ios": True}
        assert not any("wired without" in n for n in plan.notes)

    def test_a_mismatched_id_is_refused_naming_both(self, tmp_path):
        config = self._config(tmp_path, android="com.other.app")
        message = _refused(analytics="", firebase_config=config)
        assert "com.other.app" in message and APP_ID in message and "--mobile-id" in message
        config = self._config(tmp_path, ios="com.other.ios")
        assert "BUNDLE_ID com.other.ios" in _refused(analytics="", firebase_config=config)

    def test_a_config_without_firebase_selected_is_refused(self, tmp_path):
        config = self._config(tmp_path, android=APP_ID)
        assert "firebase is selected by neither" in _refused(firebase_config=config)

    def test_a_file_that_is_not_a_firebase_config_is_refused(self, tmp_path):
        from stapel_tools.composition.kmp.providers import read_firebase_config

        bad = tmp_path / "x.json"
        bad.write_text("{}", encoding="utf-8")
        assert read_firebase_config(f"android={bad}")["android"]["ids"] == []
        bad.write_text("not json", encoding="utf-8")
        with pytest.raises(CompositionError, match="not a Firebase google-services.json"):
            read_firebase_config(f"android={bad}")
        with pytest.raises(CompositionError, match="use android="):
            read_firebase_config(f"web={bad}")

    def test_absent_config_wires_a_noop_with_examples_holding_no_key(self):
        import re

        plan = _plan(analytics="")
        assert "mobile/androidApp/google-services.json" not in plan.files
        assert any("wired without" in n for n in plan.notes)
        for rel in ("mobile/androidApp/google-services.json.example", "mobile/iosApp/GoogleService-Info.plist.example"):
            text = _text(plan, rel)
            assert APP_ID in text
            assert not re.search(r"AIza[0-9A-Za-z_-]{35}", text)
        # the example sits outside the synchronized iosApp/ folder: it is not bundled into the app
        assert not any(rel.startswith("mobile/iosApp/iosApp/") and rel.endswith(".example") for rel in plan.files)


class TestPush:
    def test_bare_flag_is_firebase_with_the_notifications_registry(self):
        plan = _plan(modules=PUSH_MODULES, push="")
        assert plan.composition["push"] == {"providers": ["firebase"], "ask": "on-demand", "rich": False,
                                            "configured": {"android": False, "ios": False}}
        push = _text(plan, "mobile/shared/src/commonMain/kotlin/acme/AppPush.kt")
        assert "pushModule(PushOptions(ask = AskPolicy.OnDemand, routing = notificationsPushRouting()))" in push
        assert "notificationsPushModule()," in push
        shared = _text(plan, SHARED)
        for alias in ("stapel.push", "stapel.notifications.push", "stapel.push.firebase"):
            assert f"implementation(libs.{alias})\n" in shared
        assert "export(libs.stapel.push.firebase)" in shared
        app = _text(plan, APP)
        assert "attachPush(app)" in app and "PushSwitch(context.koin)" in app
        assert "notifications" in plan.composition["headless"]
        assert all(p["module"] != "notifications" for p in plan.composition["pending"])
        assert plan.composition["providers"]["firebase"]["bridgeAbi"]["push"] == \
            {"constant": "FIREBASE_PUSH_BRIDGE_ABI", "abi": 1}

    def test_after_sign_in(self):
        plan = _plan(modules=PUSH_MODULES, push="", push_ask="after-sign-in")
        assert "AskPolicy.AfterSignIn" in _text(plan, "mobile/shared/src/commonMain/kotlin/acme/AppPush.kt")
        assert "--mobile-push-ask 'later'" in _refused(modules=PUSH_MODULES, push="", push_ask="later")

    def test_ios_shell_delegate_bridges_plist_and_entitlement(self):
        plan = _plan(modules=PUSH_MODULES, push="")
        assert _swift_files(plan) == {"StapelAppDelegate.swift", "StapelFirebase.swift", "StapelFirebasePush.swift"}
        assert plan.files[STAPEL_SWIFT + "StapelAppDelegate.swift"] == \
            vendor.read_bytes("ios-bridges/core/StapelAppDelegate.swift").decode("utf-8")
        swift = _text(plan, SWIFT_APP)
        assert "@UIApplicationDelegateAdaptor(StapelAppDelegate.self) var delegate" in swift
        assert "StapelAppDelegate.handlers = [StapelFirebasePush.shared]" in swift
        assert "IosBridgesKt.installBridges(firebasePush: StapelFirebasePush.shared)" in swift
        plist = _plist(plan)
        assert plist["FirebaseAppDelegateProxyEnabled"] is False
        assert plist["FirebaseMessagingAutoInitEnabled"] is False
        assert "FIREBASE_ANALYTICS_COLLECTION_ENABLED" not in plist
        entitlements = plistlib.loads(plan.files["mobile/iosApp/Configuration/iosApp.entitlements"].encode())
        assert entitlements == {"aps-environment": "$(APS_ENVIRONMENT)"}
        xcconfig = _text(plan, "mobile/iosApp/Configuration/Config.xcconfig")
        assert "CODE_SIGN_ENTITLEMENTS=Configuration/iosApp.entitlements\n" in xcconfig
        assert "APS_ENVIRONMENT[config=Release]=production\n" in xcconfig
        assert "productName = FirebaseMessaging;" in _text(plan, PBX)

    def test_the_reference_apps_firebase_plist_keys_are_all_emitted(self):
        reference = plistlib.loads(vendor.read_bytes("sample-ios/Info.plist"))
        plist = _plist(_plan(modules=PUSH_MODULES, push=""))
        for key, value in reference.items():
            if key.startswith("Firebase"):
                assert plist.get(key) == value, key

    def test_android_permission_and_tapped_notification(self):
        plan = _plan(modules=PUSH_MODULES, push="")
        manifest = _text(plan, "mobile/androidApp/src/main/AndroidManifest.xml")
        assert '<uses-permission android:name="android.permission.POST_NOTIFICATIONS" />' in manifest
        activity = _text(plan, "mobile/androidApp/src/main/kotlin/acme/android/MainActivity.kt")
        assert "if (handlePushIntent(intent)) return" in activity
        assert "fun handlePushIntent(intent: Intent): Boolean = handleFirebasePushIntent(intent)" in \
            _text(plan, "mobile/shared/src/androidMain/kotlin/acme/AndroidApp.kt")
        assert "POST_NOTIFICATIONS" not in _text(_plan(), "mobile/androidApp/src/main/AndroidManifest.xml")

    def test_push_and_analytics_share_one_firebase_and_one_package(self):
        plan = _plan(modules=PUSH_MODULES, push="", analytics="")
        assert _swift_files(plan) == {"StapelAppDelegate.swift", "StapelFirebase.swift",
                                      "StapelFirebasePush.swift", "StapelFirebaseAnalytics.swift"}
        pbx = _text(plan, PBX)
        assert pbx.count("isa = XCRemoteSwiftPackageReference;") == 1
        assert "productName = FirebaseMessaging;" in pbx and "productName = FirebaseAnalyticsCore;" in pbx
        gate = _text(plan, "mobile/shared/src/commonTest/kotlin/acme/ProvidersGateTest.kt")
        assert 'assertEquals(setOf("firebase"), app.koin.getAll<PushProvider>()' in gate

    def test_ios_smoke_waits_for_the_named_noops_without_config(self, tmp_path):
        smoke = _text(_plan(modules=PUSH_MODULES, push="", analytics=""), "mobile/scripts/ios-smoke.sh")
        assert "wait_for 'stapel.push unavailable' 30\n" in smoke
        assert "wait_for 'stapel.analytics unavailable firebase unconfigured' 30\n" in smoke
        from stapel_tools.composition.kmp.providers import read_firebase_config

        config = read_firebase_config(f"ios={_fake_ios_config(tmp_path)}")
        configured = _text(_plan(modules=PUSH_MODULES, push="", firebase_config=config), "mobile/scripts/ios-smoke.sh")
        assert "unavailable" not in configured
        assert "unavailable" not in _text(_plan(), "mobile/scripts/ios-smoke.sh")

    def test_without_the_notifications_module_is_refused(self):
        message = _refused(push="")
        assert "stapel-notifications" in message and "Add notifications to --modules" in message

    def test_appmetrica_alone_is_refused_n5(self):
        message = _refused(modules=PUSH_MODULES, push="appmetrica")
        assert "N5" in message and "--mobile-push firebase" in message

    def test_appmetrica_alongside_is_refused_while_stapel_kmp_ships_no_provider(self):
        assert "ships no appmetrica push provider" in _refused(modules=PUSH_MODULES, push="firebase,appmetrica")

    def test_without_a_completable_sign_in_is_refused(self):
        message = _refused(modules=PUSH_MODULES, auth=_auth(model="none", kinds=None), push="")
        assert "no completable sign-in" in message


class TestAvatarUploadWiring:
    """K17: the avatar upload's uploader is stapel-cdn's cdnModule(), wired next to ProfilesBrick()."""

    def test_the_cdn_module_is_wired_with_its_artifact(self):
        plan = _plan(modules=("auth", "profiles", "cdn"), avatar_upload=True)
        app = _text(plan, APP)
        assert "import stapel.cdn.di.cdnModule\n" in app
        assert "    bricks(AuthBrick(), ProfilesBrick())\n" in app
        assert "    modules(cdnModule())\n" in app
        assert "implementation(libs.stapel.cdn)\n" in _text(plan, SHARED)
        assert "cdn" in plan.composition["headless"]

    def test_without_avatar_upload_no_cdn_module(self):
        plan = _plan(modules=("auth", "profiles"))
        assert "cdnModule" not in _text(plan, APP)
        assert "stapel.cdn" not in _text(plan, SHARED)


def test_plan_with_every_provider_opens_no_file(monkeypatch, tmp_path):
    import builtins
    import io
    from pathlib import Path

    from stapel_tools.composition.kmp.providers import read_firebase_config

    config = read_firebase_config(f"android={_fake_android_config(tmp_path)},ios={_fake_ios_config(tmp_path)}")
    comp, request = _comp(modules=("auth", "notifications", "analytics"), request=_request(
        analytics="firebase,appmetrica,stapel", push="", firebase_config=config))
    emitter_for("kmp").validate(comp, request)

    def refuse(*a, **k):
        raise AssertionError(f"plan() opened {a[:1]}")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(io, "open", refuse)
    monkeypatch.setattr(Path, "read_bytes", refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    assert emitter_for("kmp").plan(comp, request).root == "mobile"


class TestCli:
    def _run(self, tmp_path, *flags):
        from stapel_tools.create_project import main
        import sys

        argv = ["stapel-create-project", "mob", "--type", "minimal", "--title", "Mob",
                "--url", "https://mob.example", "--company-name", "Acme",
                "--company-email", "a@mob.example", "--no-git", "--output-dir", str(tmp_path), *flags]
        old = sys.argv
        sys.argv = argv
        try:
            main()
        finally:
            sys.argv = old
        return tmp_path / "mob"

    def test_provider_flags_need_mobile(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            self._run(tmp_path, "--modules", "auth", "--mobile-push", "--mobile-analytics")
        err = capsys.readouterr().err
        assert "--mobile-analytics" in err and "--mobile-push" in err and "need --mobile kmp" in err

    def test_push_refusal_happens_before_anything_is_written(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            self._run(tmp_path, "--modules", "auth", "--auth-model", "public", "--mobile", "kmp",
                      "--mobile-maven-repo", "local", "--mobile-push")
        assert "Add notifications to --modules" in capsys.readouterr().err
        assert not (tmp_path / "mob").exists()

    def test_config_mismatch_is_refused_before_anything_is_written(self, tmp_path, capsys):
        config = _fake_android_config(tmp_path, "com.other.app")
        out = tmp_path / "out"
        with pytest.raises(SystemExit):
            self._run(out, "--modules", "auth", "--auth-model", "public", "--mobile", "kmp",
                      "--mobile-maven-repo", "local", "--mobile-analytics",
                      "--mobile-firebase-config", f"android={config}")
        assert "com.other.app" in capsys.readouterr().err
        assert not (out / "mob").exists()

    def test_analytics_and_push_are_written_and_echoed(self, tmp_path):
        project = self._run(tmp_path, "--modules", "auth", "notifications", "--auth-model", "public",
                            "--auth-kinds", "phone,email,password", "--mobile", "kmp",
                            "--mobile-maven-repo", "local", "--mobile-analytics", "--mobile-push",
                            "--mobile-push-ask", "after-sign-in", "--mobile-id", "example.mob")
        doc = json.loads((project / "mobile/stapel.composition.json").read_text())
        assert doc["analytics"] == {"sinks": ["firebase"], "consent": "opt-in"}
        assert doc["push"]["ask"] == "after-sign-in"
        assert (project / "mobile/iosApp/iosApp/Stapel/StapelFirebasePush.swift").is_file()
        assert "## Analytics and push" in (project / "mobile/README.md").read_text()
