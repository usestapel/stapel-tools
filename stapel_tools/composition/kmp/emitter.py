"""The KMP emitter — the in-project ``mobile/`` app (Android, iOS, desktop JVM).

Plans, purely, the tree the stapel-kmp reference app (``samples/shop-mobile``)
has: a separate Gradle build consuming stapel-kmp by coordinates, one
``stapelApp { }`` assembling the installed bricks, the project's own backend
modules through the consumer codegen plugin, the theme the web wears byte for
byte, environments per platform, platform hosts (Android activity, desktop
``main``, an Xcode project from the wizard template) and the proof gates
(``scripts/mobile-check.sh``).

Everything the emitter knows about stapel-kmp comes from the vendored,
digest-pinned data (:mod:`.vendor`, :mod:`.registry`). Options arrive on the
:class:`PlatformRequest` (``create_project`` fills them from the CLI):

``offline`` (bool), ``app_id``, ``app_name``, ``repo`` (``central`` |
``local`` | a staging directory), ``dev_port`` (int), ``custom`` (tuple of
``(module, prefix, backend_dir)``), ``serve_well_known`` (bool: the project's
nginx serves the association files).
"""
from __future__ import annotations

import json
import re
from importlib import resources
from typing import ClassVar, Optional
from urllib.parse import urlparse

from ..model import Composition, CompositionError, EmitPlan, Gate, PlatformRequest

ROOT = "mobile"
DEFAULT_SHELL = "AdaptiveTabs"
HOME_ID = "app.home"
#: Kotlin hard keywords a package segment may not be.
_KOTLIN_KEYWORDS = frozenset({
    "as", "break", "class", "continue", "do", "else", "false", "for", "fun", "if", "in",
    "interface", "is", "null", "object", "package", "return", "super", "this", "throw", "true",
    "try", "typealias", "typeof", "val", "var", "when", "while",
})
_CONTAINER_ROOT_PATHS = {"account.root": "/app/account", "admin.root": "/app/admin"}
_KIND_REASONS = {
    "oauth": "needs the native OAuth hand-off (allow-listed app redirect + PKCE), not built yet",
    "magic": "needs the native magic-link hand-off, not built yet",
    "sso": "needs the native SSO hand-off, not built yet",
    "passkey": "needs WebAuthn origins for apps, not built yet",
    "qr": "the mobile client has no QR sign-in screen yet",
}


def _load_templates() -> dict[str, str]:
    root = resources.files(__package__).joinpath("templates")
    out: dict[str, str] = {}
    for entry in root.iterdir():
        if entry.is_file():
            out[entry.name] = entry.read_text(encoding="utf-8")
        else:
            for sub in entry.iterdir():
                out[f"{entry.name}/{sub.name}"] = sub.read_text(encoding="utf-8")
    return out


def _load_vendored() -> dict:
    """Everything plan() reads from the vendored stapel-kmp data, read once at import —
    plan() itself touches no file (the golden gate and Studio call it in memory)."""
    from . import registry, vendor

    registry.kmp_bindings()
    registry.shell_templates()
    return {
        "commit": vendor.load_pin()["commit"],
        **{rel: vendor.read_bytes(rel) for rel in (
            "gradle/gradle-wrapper.jar", "gradle/gradle-wrapper.properties", "gradle/gradlew")},
    }


_TEMPLATES = _load_templates()
_VENDORED = _load_vendored()


def _template(name: str) -> str:
    return _TEMPLATES[name]


def _render(name: str, ctx: dict) -> str:
    text = _template(name)
    for key, value in ctx.items():
        text = text.replace("{{" + key + "}}", str(value))
    left = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if left:
        raise AssertionError(f"template {name}: unrendered {sorted(set(left))}")
    return text


def _kt_string(text: str) -> str:
    return json.dumps(text, ensure_ascii=False).replace("$", "\\$")


def _xml(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "\\'"))


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def kotlin_package(slug: str) -> str:
    seg = re.sub(r"[^a-z0-9]+", "_", slug.lower()).strip("_") or "app"
    if seg[0].isdigit() or seg in _KOTLIN_KEYWORDS:
        seg = "app" + seg
    return seg


def prod_host(url: str) -> Optional[str]:
    """The public host of the site URL; None for none, localhost or a bare IP (no deep links)."""
    host = urlparse(url).hostname if url else None
    if not host or "." not in host or re.fullmatch(r"[0-9.]+|[0-9a-f:]+", host):
        return None
    return host


def default_app_id(slug: str, url: str) -> str:
    """Reverse-DNS app id / bundle id from the site URL and the project name.

    ``https://golden.example`` + ``golden`` -> ``example.golden``; without a
    URL -> ``com.example.<name>``. Segments keep ``[a-z0-9]`` only (valid for
    both an Android application id and an iOS bundle id), each starting with a
    letter."""
    host = prod_host(url)
    labels = [s for s in (host or "").split(".") if s and s != "www"]
    name = _alnum(slug) or "app"
    segments = list(reversed(labels)) if labels else ["com", "example"]
    segments = [_alnum(s) for s in segments if _alnum(s)]
    if not segments or segments[-1] != name:
        segments.append(name)
    if len(segments) < 2:
        segments.insert(0, "app")
    return ".".join(s if s[0].isalpha() else "a" + s for s in segments)


def validate_app_id(app_id: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)+", app_id):
        raise CompositionError(
            f"--mobile-id {app_id!r}: use lowercase reverse-DNS segments of [a-z0-9], each "
            "starting with a letter (valid as both an Android application id and an iOS bundle id)")


def ios_product(slug: str) -> str:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", slug) if w]
    name = "".join(w[:1].upper() + w[1:] for w in words) or "App"
    return name if name[0].isalpha() else "App" + name


def plan_routes(entries, home_requires_auth: bool) -> tuple[dict[str, str], list[str]]:
    """id -> absolute path for the installed entries + the app's home; notes for the dropped."""
    routes: dict[str, str] = {}
    notes: list[str] = []
    for entry in sorted(entries, key=lambda e: (e.level != "top", e.order, e.id)):
        if entry.level == "top":
            routes[entry.id] = entry.path if entry.path.startswith("/") else "/app/" + entry.path
    routes[HOME_ID] = "/app/home"
    for entry in entries:
        if entry.level == "top":
            continue
        parent = entry.parent_id
        base = _CONTAINER_ROOT_PATHS.get(parent) or routes.get(parent)
        if base is None:
            notes.append(f"mobile: {entry.id} is not routed — its parent {parent} is not "
                         "installed in the mobile app")
            continue
        routes[entry.id] = base.rstrip("/") + "/" + entry.path
    return routes, notes


class KmpEmitter:
    platform: ClassVar[str] = "kmp"

    # -- validation -----------------------------------------------------------------------------

    def _sign_in(self, comp: Composition) -> tuple[Optional[tuple[str, ...]], tuple[str, ...]]:
        """(kinds the app renders | None = the backend's defaults, kinds left out)."""
        kinds = comp.auth.kinds if comp.auth is not None else None
        if kinds is None:
            return None, ()
        from .registry import MOBILE_AUTH_KINDS

        return (tuple(k for k in kinds if k in MOBILE_AUTH_KINDS),
                tuple(k for k in kinds if k not in MOBILE_AUTH_KINDS))

    def _auth_installed(self, comp: Composition) -> bool:
        from .registry import kmp_bindings

        return ("auth" in comp.modules and comp.want_auth and "auth" in kmp_bindings()
                and comp.auth_model not in ("none",))

    def validate(self, comp: Composition, request: PlatformRequest) -> None:
        from .registry import kmp_bindings, shell_templates, unreleased_artifacts

        if comp.project_type not in ("minimal", "monolith"):
            raise CompositionError(
                f"--mobile kmp: project type {comp.project_type!r} is not supported yet "
                "(minimal, monolith)")
        shell = request.shell or DEFAULT_SHELL
        if shell not in shell_templates():
            raise CompositionError(
                f"--mobile-shell {shell!r}: one of {', '.join(shell_templates())}")
        app_id = request.option("app_id")
        if app_id:
            validate_app_id(app_id)
        if self._auth_installed(comp):
            rendered, absent = self._sign_in(comp)
            if rendered is not None and not rendered:
                raise CompositionError(
                    "--mobile kmp: none of the selected sign-in kinds "
                    f"({', '.join(absent)}) can be completed by the mobile client — it completes "
                    "phone, email and password today ("
                    + "; ".join(f"{k}: {_KIND_REASONS.get(k, 'not supported')}" for k in absent)
                    + "). Add one of phone,email,password to --auth-kinds, or drop --mobile.")
        installed = tuple(k for k in comp.modules if k in kmp_bindings()
                          and (k != "auth" or self._auth_installed(comp)))
        missing = unreleased_artifacts(installed, offline=bool(request.option("offline")))
        if missing and request.option("repo", "central") == "central":
            raise CompositionError(
                "--mobile kmp: these stapel-kmp artifacts are not on Maven Central yet: "
                + ", ".join(missing)
                + ". Pass --mobile-maven-repo local (mavenLocal, after `./gradlew "
                "publishToMavenLocal` in a stapel-kmp checkout) or --mobile-maven-repo <dir> "
                "(its `publishAllPublicationsToStagingRepository` output).")

    # -- plan -----------------------------------------------------------------------------------

    def plan(self, comp: Composition, request: PlatformRequest) -> EmitPlan:  # noqa: C901
        from ..bricks import BRICK_UI
        from ..react.bindings import REACT_BINDINGS
        from .registry import kmp_bindings, kmp_registry, shell_templates

        reg = kmp_registry()
        bindings = kmp_bindings()
        files: dict[str, object] = {}
        modes: dict[str, int] = {}
        notes: list[str] = []

        def write(rel: str, content, mode: Optional[int] = None) -> None:
            files[f"{ROOT}/{rel}"] = content
            if mode is not None:
                modes[f"{ROOT}/{rel}"] = mode

        pkg = kotlin_package(comp.slug)
        pkg_path = pkg.replace(".", "/")
        app_id = request.option("app_id") or default_app_id(comp.slug, comp.url)
        title = (request.option("app_name") or comp.title or comp.slug).replace("\n", " ")
        scheme = _alnum(comp.slug) or "app"
        if not scheme[0].isalpha():
            scheme = "app" + scheme
        product = ios_product(comp.slug)
        host = prod_host(comp.url)
        offline = bool(request.option("offline"))
        repo = request.option("repo", "central")
        dev_port = int(request.option("dev_port", 8000))
        shell = request.shell or DEFAULT_SHELL

        # -- what is installed, what is pending ---------------------------------------------
        auth_installed = self._auth_installed(comp)
        installed: list[str] = []
        pending: list[dict] = []
        headless: list[str] = []
        for key in comp.modules:
            if key == "core":
                continue
            binding = bindings.get(key)
            if binding is not None and binding.ui_artifact and not binding.descriptor_present:
                pending.append({"module": key, "reason": f"stapel-kmp ships no {binding.descriptor_class} "
                                "at the vendored commit"})
            elif binding is not None and binding.ui_artifact and (key != "auth" or auth_installed):
                installed.append(key)
            elif key == "auth" and binding is not None:
                pending.append({"module": key, "reason": "auth is not wired (--no-auth / --auth-model none)"})
            elif key in BRICK_UI or key in REACT_BINDINGS:
                pending.append({"module": key, "reason": "no stapel-kmp library registered"})
            else:
                headless.append(key)
        for item in pending:
            notes.append(f"mobile: {item['module']} is pending — {item['reason']}")

        # -- sign-in methods --------------------------------------------------------------
        sign_in_methods: Optional[dict] = None
        walk = False
        if auth_installed:
            rendered, absent = self._sign_in(comp)
            sign_in_methods = {
                "rendered": list(rendered) if rendered is not None else "backend defaults",
                "absent": [{"kind": k, "reason": _KIND_REASONS.get(k, "not supported")} for k in absent],
            }
            for kind in absent:
                notes.append(f"mobile: sign-in kind {kind} is absent from the app — "
                             f"{_KIND_REASONS.get(kind, 'not supported')}")
            walk = rendered is None or "email" in rendered

        # -- navigation plan -------------------------------------------------------------
        entries = []
        embeds = []
        for key in installed:
            brick = BRICK_UI.get(key)
            if brick is None:
                continue
            entries += [e for e in brick.nav if e.id in bindings[key].entries]
            embeds += [e for e in brick.embeds if e.id in bindings[key].embeds]
        home_requires_auth = auth_installed
        routes, route_notes = plan_routes(entries, home_requires_auth)
        notes.extend(route_notes)
        template_slots = shell_templates()[shell]["slots"]
        slots: dict[str, list[str]] = {}
        dropped: list[str] = []
        for embed in sorted(embeds, key=lambda e: (e.slot, e.order, e.id)):
            if embed.surface == "member" and not auth_installed:
                dropped.append(embed.id)
                notes.append(f"mobile: embed {embed.id} dropped — member-only and no sign-in")
            elif embed.slot in template_slots:
                slots.setdefault(embed.slot, []).append(embed.id)
            else:
                dropped.append(embed.id)
                notes.append(f"mobile: embed {embed.id} dropped — shell {shell} has no {embed.slot}")
        sign_in = "auth.login" if auth_installed and "auth.login" in routes else None

        # -- the project's own backend modules ------------------------------------------
        custom = list(request.option("custom", ()) or ())
        custom_names = [c[0] for c in custom]

        composition = {
            "platform": "kmp",
            "shell": {"template": shell, "slots": slots, "overrides": {}, "dropped": dropped},
            "auth": {"model": comp.auth_model, "gated": auth_installed},
            "theme": comp.theme or "default",
            "modules": [m for m in comp.modules],
            "installed": installed,
            "custom": custom_names,
            "pending": pending,
            "headless": headless,
            "offline": offline,
            "signIn": sign_in,
            "signInMethods": sign_in_methods,
            "routes": routes,
            "app": {"id": app_id, "name": title, "scheme": scheme, "package": pkg,
                    "iosProduct": product},
            "stapelKmp": {"train": reg["train"], "group": reg["group"],
                          "commit": _VENDORED["commit"]},
        }

        ctx = {
            "PKG": pkg, "PKG_PATH": pkg_path, "NAMESPACE": pkg, "SLUG": comp.slug,
            "APP_ID": app_id, "SCHEME": scheme, "IOS_PRODUCT": product, "HOME_ID": HOME_ID,
            "TITLE_KT": title, "TITLE_KT_STRING": _kt_string(title), "TITLE_XML": _xml(title),
            "GROUP": reg["group"], "CATALOG": reg["catalog"], "TRAIN": reg["train"],
            "REPO": repo, "ROOT_NAME": f"{comp.slug}-mobile", "DEV_PORT": dev_port,
        }

        # -- Gradle build ------------------------------------------------------------------
        write("settings.gradle.kts", _render("settings.gradle.kts", ctx))
        write("build.gradle.kts", _render("build.gradle.kts", ctx))
        write("gradle.properties", _render("gradle.properties", ctx))
        write("gradle/wrapper/gradle-wrapper.jar", _VENDORED["gradle/gradle-wrapper.jar"])
        write("gradle/wrapper/gradle-wrapper.properties",
              _VENDORED["gradle/gradle-wrapper.properties"].decode("utf-8"))
        write("gradlew", _VENDORED["gradle/gradlew"].decode("utf-8"), 0o755)
        write(".gitignore", _template("gitignore"))

        def alias(artifact: str) -> str:
            return "libs." + artifact.replace("-", ".")

        brick_deps = "".join(
            f"                implementation({alias(bindings[k].ui_artifact)})\n" for k in installed)
        testing_deps = "".join(
            f"                implementation({alias(bindings[k].testing_artifact)})\n"
            for k in installed if bindings[k].testing_artifact)
        offline_dep = (f"                implementation({alias(reg['app']['offline']['artifact'])})\n"
                       if offline else "")
        write("shared/build.gradle.kts", _render("shared.build.gradle.kts", {
            **ctx, "BRICK_DEPS": brick_deps, "TESTING_DEPS": testing_deps,
            "OFFLINE_DEP": offline_dep,
            "CUSTOM_MODULES": ", ".join(json.dumps(m) for m in custom_names),
        }))

        # -- shared sources ----------------------------------------------------------------
        imports: list[str] = []
        brick_calls: list[str] = []
        for key in installed:
            b = bindings[key]
            imports.append(f"import {b.descriptor_class}\n")
            brick_calls.append(f"{b.descriptor_name}()")
        offline_line = ""
        if offline:
            module = reg["app"]["offline"]["module"]
            imports.append(f"import {module}\n")
            offline_line = f"    offline({module.rsplit('.', 1)[1]}(directory = dataDirectory))\n"
        bricks_line = f"    bricks({', '.join(brick_calls)})\n" if brick_calls else ""
        hosts = json.dumps(host) if host else ""
        offline_ctx = {
            "DATA_DIR_PARAM": "    dataDirectory: String? = platformDataDirectory(),\n" if offline else "",
            "DATA_DIR_DOC": (" * @param dataDirectory where the durable cache keeps its databases "
                             "(null = the platform default).\n") if offline else "",
            "TEST_DATA_ARG": ", dataDirectory = testDataDirectory()" if offline else "",
        }
        ctx.update(offline_ctx)
        if offline:
            write(f"shared/src/commonMain/kotlin/{pkg_path}/DataDirectory.kt", _render("DataDirectory.kt", ctx))
            for target in ("android", "ios"):
                write(f"shared/src/{target}Main/kotlin/{pkg_path}/DataDirectory.{target}.kt",
                      _render("DataDirectory.mobile.kt", ctx))
            write(f"shared/src/jvmMain/kotlin/{pkg_path}/DataDirectory.jvm.kt",
                  _render("DataDirectory.jvm.kt", ctx))
            write(f"shared/src/commonTest/kotlin/{pkg_path}/TestDataDirectory.kt",
                  _render("TestDataDirectory.kt", ctx))
            for target in ("jvm", "ios"):
                write(f"shared/src/{target}Test/kotlin/{pkg_path}/TestDataDirectory.{target}.kt",
                      _render(f"TestDataDirectory.{target}.kt", ctx))
        write(f"shared/src/commonMain/kotlin/{pkg_path}/App.kt", _render("App.kt", {
            **ctx, "IMPORTS": "".join(sorted(imports)), "OFFLINE_LINE": offline_line,
            "BRICKS_LINE": bricks_line, "HOSTS": hosts,
            "HOME_REQUIRES_AUTH": "true" if home_requires_auth else "false",
        }))
        write(f"shared/src/androidMain/kotlin/{pkg_path}/AndroidApp.kt", _render("AndroidApp.kt", ctx))
        write(f"shared/src/iosMain/kotlin/{pkg_path}/MainViewController.kt",
              _render("MainViewController.kt", ctx))
        write(f"shared/src/jvmMain/kotlin/{pkg_path}/DesktopApp.kt", _render("DesktopApp.kt", ctx))
        write(f"shared/src/commonTest/kotlin/{pkg_path}/TestPlatform.kt", _render("TestPlatform.kt", ctx))
        write(f"shared/src/jvmTest/kotlin/{pkg_path}/TestPlatform.jvm.kt",
              _render("TestPlatform.jvm.kt", ctx))
        write(f"shared/src/iosTest/kotlin/{pkg_path}/TestPlatform.ios.kt",
              _render("TestPlatform.ios.kt", ctx))
        write(f"shared/src/commonTest/kotlin/{pkg_path}/NavGateTest.kt", _render("NavGateTest.kt", {
            **ctx,
            "PLANNED_ROUTES": ", ".join(f"{json.dumps(k)} to {json.dumps(v)}" for k, v in routes.items()),
            "PLANNED_SLOTS": ", ".join(
                f"{json.dumps(s)} to listOf({', '.join(json.dumps(i) for i in ids)})"
                for s, ids in slots.items()),
            "PLANNED_SIGN_IN": json.dumps(sign_in) if sign_in else "null",
        }))
        if walk:
            write(f"shared/src/walkTest/kotlin/{pkg_path}/SignInWalkTest.kt",
                  _render("SignInWalkTest.kt", ctx))
        elif auth_installed:
            notes.append("mobile: no sign-in walk test — the walk signs in by email OTP and "
                         "email is not among the selected kinds")

        # -- hosts -------------------------------------------------------------------------
        write("androidApp/build.gradle.kts", _render("androidApp.build.gradle.kts", ctx))
        app_links = ""
        if host:
            app_links = (
                '            <intent-filter android:autoVerify="true">\n'
                '                <action android:name="android.intent.action.VIEW" />\n'
                '                <category android:name="android.intent.category.DEFAULT" />\n'
                '                <category android:name="android.intent.category.BROWSABLE" />\n'
                f'                <data android:scheme="https" android:host="{_xml(host)}" '
                'android:pathPrefix="/app" />\n'
                '            </intent-filter>\n')
        write("androidApp/src/main/AndroidManifest.xml",
              _render("AndroidManifest.xml", {**ctx, "APP_LINKS": app_links}))
        write(f"androidApp/src/main/kotlin/{pkg_path}/android/MainActivity.kt",
              _render("MainActivity.kt", ctx))
        write("desktopApp/build.gradle.kts", _render("desktopApp.build.gradle.kts", ctx))
        write(f"desktopApp/src/main/kotlin/{pkg_path}/desktop/Main.kt", _render("Main.kt", ctx))
        ios = "iosApp"
        write(f"{ios}/iosApp.xcodeproj/project.pbxproj", _render("ios/project.pbxproj", ctx))
        write(f"{ios}/iosApp.xcodeproj/project.xcworkspace/contents.xcworkspacedata",
              _template("ios/contents.xcworkspacedata"))
        write(f"{ios}/Configuration/Config.xcconfig", _render("ios/Config.xcconfig", ctx))
        write(f"{ios}/iosApp/iOSApp.swift", _template("ios/iOSApp.swift"))
        write(f"{ios}/iosApp/ContentView.swift", _template("ios/ContentView.swift"))
        write(f"{ios}/iosApp/Info.plist", _render("ios/Info.plist", ctx))
        write(f"{ios}/iosApp/Assets.xcassets/Contents.json", _template("ios/Assets.Contents.json"))
        write(f"{ios}/iosApp/Assets.xcassets/AccentColor.colorset/Contents.json",
              _template("ios/AccentColor.Contents.json"))
        write(f"{ios}/iosApp/Assets.xcassets/AppIcon.appiconset/Contents.json",
              _template("ios/AppIcon.Contents.json"))
        write(f"{ios}/iosApp/Preview Content/Preview Assets.xcassets/Contents.json",
              _template("ios/Assets.Contents.json"))

        # -- scripts, make, data -----------------------------------------------------------
        smoke_link = f"{scheme}://app/home"
        write("scripts/ios-smoke.sh", _render("ios-smoke.sh", {**ctx, "SMOKE_LINK": smoke_link}), 0o755)
        write("scripts/mobile-check.sh", _template("mobile-check.sh"), 0o755)
        recipes = "".join(
            f"\t@cd {backend_dir} && $(PYTHON) -c 'import stapel_tools' 2>/dev/null || "
            f"{{ echo 'contracts: the backend environment ({backend_dir}) needs stapel-tools: "
            f"pip install stapel-tools'; exit 1; }}\n"
            f"\tcd {backend_dir} && $(PYTHON) manage.py shell -c "
            f"\"from stapel_tools.composition.kmp.contract import main; "
            f"main(['--module', '{m}', '--prefix', '{prefix}', '--out', "
            f"'$(CURDIR)/contracts/{m}'"
            + "".join(f", '--exclude', '{x}'" for x in exclude)
            + "])\"\n"
            for m, prefix, backend_dir, exclude in custom)
        write("Makefile", "PYTHON ?= python\n" + _render("Makefile", {**ctx, "CONTRACT_RECIPES": recipes}))
        write("contracts/.gitkeep", "")
        write("stapel.composition.json", json.dumps(composition, indent=2) + "\n")
        write("stapel.theme.json", comp.theme_json)
        prod_url = (comp.url.rstrip("/") + "/") if host else "https://example.invalid/"
        if not host:
            notes.append("mobile: no --url — the prod environment's baseUrl is "
                         "https://example.invalid/ until stapel.environments.json is edited")
        environments = {
            "default": "dev",
            "environments": {
                "dev": {"posture": "prototype", "baseUrl": {
                    "android": f"http://10.0.2.2:{dev_port}/",
                    "ios": f"http://127.0.0.1:{dev_port}/",
                    "desktop": f"http://127.0.0.1:{dev_port}/",
                }},
                "prod": {"posture": "live", "baseUrl": prod_url},
            },
        }
        write("stapel.environments.json", json.dumps(environments, indent=2) + "\n")

        # -- deep-link association files ------------------------------------------------
        aasa = {"applinks": {"details": [{
            "appIDs": [f"TEAM_ID_PLACEHOLDER.{app_id}"],
            "components": [{"/": "/app/*"}, {"/": "/login"}],
        }]}}
        assetlinks = [{
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {"namespace": "android_app", "package_name": app_id,
                       "sha256_cert_fingerprints": ["SHA256_FINGERPRINT_PLACEHOLDER_OF_THE_RELEASE_SIGNING_KEY"]},
        }]
        serve = bool(request.option("serve_well_known"))
        well_known_dir = "service-configs/nginx/well-known" if serve else f"{ROOT}/well-known"
        files[f"{well_known_dir}/apple-app-site-association"] = json.dumps(aasa, indent=2) + "\n"
        files[f"{well_known_dir}/assetlinks.json"] = json.dumps(assetlinks, indent=2) + "\n"
        nginx_locations: tuple[str, ...] = ()
        if serve:
            nginx_locations = tuple(
                f"    location = /.well-known/{name} {{\n"
                "        default_type application/json;\n"
                f"        alias /etc/nginx/conf.d/well-known/{name};\n"
                "    }\n"
                for name in ("apple-app-site-association", "assetlinks.json"))
        else:
            notes.append("mobile: this project type has no nginx — serve "
                         "mobile/well-known/* at https://<domain>/.well-known/ for universal "
                         "links / app links")

        write("README.md", _readme(ctx, composition, well_known_dir, serve, dev_port, custom))
        gates = (
            Gate("mobile-generated-and-tokens", ("./gradlew", ":shared:stapelGenerateCheck",
                                                 ":shared:checkStapelTokens"), ROOT, ("jdk",)),
            Gate("mobile-nav-gate", ("./gradlew", ":shared:jvmTest"), ROOT, ("jdk",)),
            Gate("mobile-android", ("./gradlew", ":androidApp:assembleDebug"), ROOT, ("jdk", "android")),
            Gate("mobile-desktop-smoke", ("./gradlew", ":desktopApp:run", "--args=--smoke"), ROOT, ("jdk",)),
            Gate("mobile-ios", ("scripts/ios-smoke.sh",), ROOT, ("jdk", "xcode")),
        )
        return EmitPlan(
            platform=self.platform, root=ROOT, files=files, modes=modes,
            composition=composition, notes=tuple(notes), nginx_locations=nginx_locations,
            gates=gates,
        )


def _readme(ctx: dict, composition: dict, well_known_dir: str, serve: bool, dev_port: int,
            custom: list) -> str:
    app = composition["app"]
    pending = composition["pending"]
    lines = [
        f"# {ctx['TITLE_KT']} — mobile app",
        "",
        "Generated by `stapel-create-project --mobile kmp`: one Kotlin Multiplatform app for Android, iOS",
        "and desktop (JVM), built from stapel-kmp libraries by Maven coordinates. What the scaffold planned",
        "is `stapel.composition.json`; `shared/src/commonTest/.../NavGateTest.kt` holds the running app to it.",
        "",
        "| Path | What |",
        "|---|---|",
        "| `shared/` | the app: `App.kt` (`createApp` = one `stapelApp { }`), platform entry points, tests |",
        "| `androidApp/`, `desktopApp/`, `iosApp/` | platform hosts (Activity, `main`, Xcode project) |",
        "| `contracts/<module>/` | contracts of the project's own backend modules (`make contracts`) |",
        "| `stapel.theme.json` | the web's theme, byte for byte |",
        "| `stapel.environments.json` | base URLs per environment and platform (`-Pstapel.env=prod`) |",
        "",
        "## Run",
        "",
        "```sh",
        "make check                     # every proof gate this machine can run (PASS / FAIL / NOT RUN)",
        f"make walk WALK_BASE_URL=http://127.0.0.1:{dev_port}/   # + the sign-in walk against the backend",
        "./gradlew :desktopApp:run      # desktop",
        "./gradlew :androidApp:assembleDebug",
        "open iosApp/iosApp.xcodeproj   # or scripts/ios-smoke.sh",
        "```",
        "",
        "Toolchain: JDK 21, the Android SDK (`ANDROID_HOME`), Xcode. The Android emulator reaches the",
        "host's backend at `10.0.2.2`; the iOS simulator and desktop at `127.0.0.1`.",
        "",
        "## The project's own backend modules",
        "",
    ]
    if custom:
        lines += [
            "After the backend gains endpoints, `make contracts` writes each module's contract to",
            "`contracts/<module>/` and regenerates its typed client into",
            "`shared/src/commonMain/generated/api/<module>/` (commit both). The client is bound in Koin",
            "automatically (`AppClients.kt`, generated); `stapelGenerateCheck` fails on drift.",
            "",
            "Modules: " + ", ".join(f"`{c[0]}` (mount `{c[1]}`)" for c in custom) + ".",
        ]
    else:
        lines.append("None.")
    if pending:
        lines += ["", "## Not on mobile yet", ""]
        lines += [f"- `{p['module']}`: {p['reason']}" for p in pending]
    methods = composition.get("signInMethods")
    if methods and methods["absent"]:
        lines += ["", "## Sign-in methods left out", ""]
        lines += [f"- `{a['kind']}`: {a['reason']}" for a in methods["absent"]]
    lines += [
        "",
        "## Before a store build — what the owner provides",
        "",
        f"- [ ] **Application / bundle id**: `{app['id']}` (change with `--mobile-id` at scaffold time,",
        "      or in `androidApp/build.gradle.kts` and `iosApp/Configuration/Config.xcconfig`).",
        "- [ ] **Apple signing**: the Team ID in `iosApp/Configuration/Config.xcconfig` (`TEAM_ID=`) and a",
        "      distribution certificate + provisioning profile for that bundle id.",
        "- [ ] **Android signing**: an upload keystore and a `signingConfigs.release` block in",
        "      `androidApp/build.gradle.kts` reading it from the environment (never committed).",
        f"- [ ] **Universal links / app links**: replace `TEAM_ID_PLACEHOLDER` in `{well_known_dir}/apple-app-site-association`",
        f"      and `SHA256_FINGERPRINT_PLACEHOLDER_…` in `{well_known_dir}/assetlinks.json` (the release key's SHA-256).",
        "      " + ("The project's nginx serves both at `/.well-known/`." if serve
                    else "Serve both at `https://<domain>/.well-known/` (this project type has no nginx)."),
        "      On iOS add the Associated Domains capability (`applinks:<domain>`) once the team is set.",
        "- [ ] **Prod base URL**: `stapel.environments.json` → `prod.baseUrl`; build with `-Pstapel.env=prod`.",
        "- [ ] **App icons**: `iosApp/iosApp/Assets.xcassets/AppIcon.appiconset` and Android launcher icons.",
        "- [ ] **Cleartext**: `android:usesCleartextTraffic` and `NSAllowsLocalNetworking` are for local",
        "      development; remove them for a store build that only talks HTTPS.",
        "",
    ]
    return "\n".join(lines)
