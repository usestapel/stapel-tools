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
    from . import providers, registry, vendor

    registry.kmp_bindings()
    registry.shell_templates()
    providers.swift_packages()
    providers.google_services_plugin_version()
    swift = {providers.APP_DELEGATE_FILE}
    for provider in providers.PROVIDERS.values():
        swift.update(f for f in (provider.core_file,) if f)
        for cap in (provider.analytics, provider.push):
            if cap is not None and cap.bridge_file:
                swift.add(cap.bridge_file)
                providers.bridge_abi(cap.bridge_file)
    return {
        "commit": vendor.load_pin()["commit"],
        **{rel: vendor.read_bytes(rel) for rel in (
            "gradle/gradle-wrapper.jar", "gradle/gradle-wrapper.properties", "gradle/gradlew")},
        **{rel: vendor.read_bytes(rel).decode("utf-8") for rel in sorted(swift)},
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


#: The media uploader the profiles avatar change talks to (stapel-kmp ``bricks/cdn/domain``).
KMP_AVATAR_UPLOAD_ARTIFACT = "stapel-cdn"
KMP_AVATAR_UPLOAD_FACTORY = "stapel.cdn.di.cdnModule"


def _feature_artifacts(f: dict) -> list[str]:
    """Every stapel-kmp artifact the analytics / push selection adds to the app."""
    from . import providers as pv

    out: list[str] = []
    if f.get("avatar"):
        out.append(KMP_AVATAR_UPLOAD_ARTIFACT)
    if f["analytics"] is not None:
        out.append(pv.ANALYTICS_ARTIFACT)
        out += [pv.PROVIDERS[s].analytics.artifact for s in f["analytics"]]
    if f["push"] is not None:
        out += list(pv.PUSH_ARTIFACTS)
        out += [pv.PROVIDERS[p].push.artifact for p in f["push"] if pv.PROVIDERS[p].push]
    return out


def reg_released(artifact: str) -> bool:
    from .registry import kmp_registry

    return bool(kmp_registry().get("artifacts", {}).get(artifact, {}).get("released", False))


def _pbx_id(name: str) -> str:
    """A stable 24-hex object id for an object the emitter adds to the Xcode project."""
    import hashlib

    return hashlib.sha256(f"stapel-kmp:{name}".encode()).hexdigest()[:24].upper()


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
        from .registry import KMP_AVATAR_UPLOAD_MODULE, KMP_INSTALL_REQUIRES

        for key in installed:
            needs, why = KMP_INSTALL_REQUIRES.get(key, ((), ""))
            absent = [n for n in needs if n not in installed]
            if absent:
                raise CompositionError(
                    f"--mobile kmp: {key} cannot be installed without {', '.join(absent)} ({why}). "
                    f"Select {', '.join(absent)} with a sign-in model (not --no-auth / "
                    "--auth-model none), or drop --mobile.")
        if request.option("avatar_upload") and (
                "profiles" not in installed or KMP_AVATAR_UPLOAD_MODULE not in comp.modules):
            raise CompositionError(
                "--mobile-avatar-upload: the avatar upload is the profiles brick's and needs "
                f"stapel-{KMP_AVATAR_UPLOAD_MODULE} on the backend; select profiles")
        features = self._features(comp, request)
        self._validate_features(comp, request, features)
        missing = unreleased_artifacts(installed, offline=bool(request.option("offline")))
        missing = sorted(set(missing) | {a for a in _feature_artifacts(features)
                                         if not reg_released(a)})
        if missing and request.option("repo", "central") == "central":
            raise CompositionError(
                "--mobile kmp: these stapel-kmp artifacts are not on Maven Central yet: "
                + ", ".join(missing)
                + ". Pass --mobile-maven-repo local (mavenLocal, after `./gradlew "
                "publishToMavenLocal` in a stapel-kmp checkout) or --mobile-maven-repo <dir> "
                "(its `publishAllPublicationsToStagingRepository` output).")

    # -- analytics and push ---------------------------------------------------------------------

    def _features(self, comp: Composition, request: PlatformRequest) -> dict:
        """The resolved ``--mobile-analytics`` / ``--mobile-push`` selection (ARCH-PUSH-ANALYTICS §7).

        ``analytics`` / ``push`` are None when the flag is absent; a bare flag means firebase. The
        first-party sink joins whenever the backend has the analytics module."""
        from . import providers as pv

        sinks = pv.parse_list(request.option("analytics"), pv.ANALYTICS_SINKS, "--mobile-analytics")
        if sinks is not None:
            sinks = sinks or ["firebase"]
            if pv.ANALYTICS_MODULE in comp.modules and "stapel" not in sinks:
                sinks.append("stapel")
        push = pv.parse_list(request.option("push"), pv.PUSH_PROVIDERS, "--mobile-push")
        if push is not None:
            push = push or ["firebase"]
        consent = request.option("consent") or "opt-in"
        ask = request.option("push_ask") or "on-demand"
        config = request.option("firebase_config") or {}
        return {"analytics": sinks, "push": push, "consent": consent, "ask": ask, "config": config,
                "avatar": bool(request.option("avatar_upload"))}

    def _validate_features(self, comp: Composition, request: PlatformRequest, f: dict) -> None:
        from . import providers as pv
        from .registry import kmp_registry

        if f["consent"] not in pv.CONSENT_DEFAULTS:
            raise CompositionError(f"--mobile-consent {f['consent']!r}: one of {', '.join(pv.CONSENT_DEFAULTS)}")
        if f["ask"] not in pv.PUSH_ASK:
            raise CompositionError(f"--mobile-push-ask {f['ask']!r}: one of {', '.join(pv.PUSH_ASK)}")
        sinks, push = f["analytics"], f["push"]
        if sinks is not None and "stapel" in sinks and pv.ANALYTICS_MODULE not in comp.modules:
            raise CompositionError(
                "--mobile-analytics stapel: the first-party sink posts to the project's own "
                f"stapel-{pv.ANALYTICS_MODULE} backend, which is not among the modules. Add "
                f"{pv.ANALYTICS_MODULE} to --modules, or choose firebase / appmetrica.")
        if push is not None:
            if push == ["appmetrica"]:
                raise CompositionError(
                    "--mobile-push appmetrica: the notifications backend addresses Firebase registration "
                    "tokens only and cannot reach AppMetrica-held ones (backend follow-up N5). Use "
                    "--mobile-push firebase, or firebase,appmetrica once stapel-kmp ships an AppMetrica "
                    "push provider.")
            if pv.PUSH_MODULE not in comp.modules:
                raise CompositionError(
                    f"--mobile-push: push registers each device with the {pv.PUSH_MODULE} backend module "
                    f"(stapel-{pv.PUSH_MODULE}), which is not among the modules. Add {pv.PUSH_MODULE} to "
                    "--modules, or drop --mobile-push.")
            if not self._auth_installed(comp):
                raise CompositionError(
                    "--mobile-push: a device is registered for a signed-in account and this project's "
                    "app has no completable sign-in (auth not selected, --no-auth or --auth-model none). "
                    "Select auth with a sign-in model, or drop --mobile-push.")
            for key in push:
                if pv.PROVIDERS[key].push is None:
                    raise CompositionError(
                        f"--mobile-push {','.join(push)}: stapel-kmp ships no {key} push provider at the "
                        f"vendored commit (only analytics). Use --mobile-push firebase.")
        artifacts = kmp_registry().get("artifacts", {})
        absent = [a for a in _feature_artifacts(f) if a not in artifacts]
        if absent:
            raise CompositionError(
                "--mobile kmp: the vendored stapel-kmp registry names no artifact "
                f"{', '.join(absent)}; re-vendor stapel-kmp (python -m stapel_tools.composition.kmp.vendor "
                "--from <stapel-kmp>) or drop the provider.")
        config = f["config"]
        if config:
            firebase = "firebase" in (sinks or ()) or "firebase" in (push or ())
            if not firebase:
                raise CompositionError(
                    "--mobile-firebase-config: firebase is selected by neither --mobile-analytics nor "
                    "--mobile-push; select it, or drop the config.")
            app_id = request.option("app_id") or default_app_id(comp.slug, comp.url)
            for platform, meta in sorted(config.items()):
                if app_id not in meta["ids"]:
                    what = "package_name" if platform == "android" else "BUNDLE_ID"
                    raise CompositionError(
                        f"--mobile-firebase-config {platform}={meta['path']}: its {what} "
                        f"{', '.join(meta['ids']) or '(none)'} is not the app id {app_id} (--mobile-id). "
                        f"Download the config of the Firebase app registered for {app_id}, or pass "
                        f"--mobile-id {meta['ids'][0] if meta['ids'] else '<its id>'}.")

    # -- plan -----------------------------------------------------------------------------------

    def plan(self, comp: Composition, request: PlatformRequest) -> EmitPlan:  # noqa: C901
        from ..bricks import BRICK_UI
        from ..react.bindings import REACT_BINDINGS
        from .registry import kmp_bindings, kmp_registry, shell_templates

        from .providers import PUSH_MODULE
        from .registry import KMP_AVATAR_UPLOAD_MODULE

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
            elif (key == KMP_AVATAR_UPLOAD_MODULE and request.option("avatar_upload")) or (
                    key == PUSH_MODULE and request.option("push") is not None):
                headless.append(key)
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

        avatar: Optional[dict] = None
        if "profiles" in installed:
            from .registry import KMP_AVATAR_UPLOAD_MODULE

            uploader = KMP_AVATAR_UPLOAD_MODULE if KMP_AVATAR_UPLOAD_MODULE in comp.modules else None
            avatar = {"uploader": uploader, "picker": None}
            notes.append(
                "mobile: profiles avatar change is not drawn until the app passes its picker: "
                "ProfilesBrick(picker = …)"
                + ("" if uploader else f"; it also needs stapel-{KMP_AVATAR_UPLOAD_MODULE} on the "
                   "backend (--mobile-avatar-upload)"))

        features = self._features(comp, request)
        features["installed"] = tuple(installed)
        fx = _plan_features(features, pkg=pkg, pkg_path=pkg_path, app_id=app_id)
        notes.extend(fx["notes"])

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
            **({"avatarUpload": avatar} if avatar is not None else {}),
            **fx["echo"],
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
            **fx["ctx"],
        }

        # -- Gradle build ------------------------------------------------------------------
        write("settings.gradle.kts", _render("settings.gradle.kts", ctx))
        write("build.gradle.kts", _render("build.gradle.kts", ctx))
        write("gradle.properties", _render("gradle.properties", ctx))
        write("gradle/wrapper/gradle-wrapper.jar", _VENDORED["gradle/gradle-wrapper.jar"])
        write("gradle/wrapper/gradle-wrapper.properties",
              _VENDORED["gradle/gradle-wrapper.properties"].decode("utf-8"))
        write("gradlew", _VENDORED["gradle/gradlew"].decode("utf-8"), 0o755)
        write(".gitignore", _render("gitignore", ctx))

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
        imports += fx["imports"]
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
        write(f"{ios}/iosApp/iOSApp.swift", _render("ios/iOSApp.swift", ctx))
        write(f"{ios}/iosApp/ContentView.swift", _template("ios/ContentView.swift"))
        write(f"{ios}/iosApp/Info.plist", _render("ios/Info.plist", ctx))
        write(f"{ios}/iosApp/Assets.xcassets/Contents.json", _template("ios/Assets.Contents.json"))
        write(f"{ios}/iosApp/Assets.xcassets/AccentColor.colorset/Contents.json",
              _template("ios/AccentColor.Contents.json"))
        write(f"{ios}/iosApp/Assets.xcassets/AppIcon.appiconset/Contents.json",
              _template("ios/AppIcon.Contents.json"))
        write(f"{ios}/iosApp/Preview Content/Preview Assets.xcassets/Contents.json",
              _template("ios/Assets.Contents.json"))

        for rel, content in fx["files"].items():
            if isinstance(content, tuple):
                name, extra = content
                content = _render(name, {**ctx, **extra})
            write(rel, content)

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


def _alias(artifact: str) -> str:
    return "libs." + artifact.replace("-", ".")


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def _plan_features(f: dict, *, pkg: str, pkg_path: str, app_id: str) -> dict:
    """What the analytics / push / avatar-upload selection adds: template values, files, echo, notes.

    ARCH-PUSH-ANALYTICS §4.3 (iOS shell), §6 (config files), §7 (what is emitted per platform)."""
    from . import providers as pv

    sinks, push = f["analytics"], f["push"]
    caps: list[tuple[str, str, pv.Capability]] = []   # (provider, "analytics" | "push", capability)
    for key in sinks or ():
        caps.append((key, "analytics", pv.PROVIDERS[key].analytics))
    for key in push or ():
        caps.append((key, "push", pv.PROVIDERS[key].push))
    vendor_caps = [c for c in caps if c[0] != "stapel"]
    bridged = [c for c in vendor_caps if c[2].bridge_file]
    vendors = list(dict.fromkeys(c[0] for c in vendor_caps))
    firebase = "firebase" in vendors
    appmetrica = "appmetrica" in vendors
    config = f["config"]
    out: dict = {"ctx": {}, "files": {}, "notes": [], "echo": {}, "imports": []}
    ctx = out["ctx"]
    files = out["files"]
    for key in ("FEATURE_DEPS", "FRAMEWORK_EXPORTS", "IOS_MAIN_DEPS", "SECRETS_SRC", "SECRETS_TASK",
                "FEATURE_LINES", "AFTER_BUILD", "HOME_ARGS", "HOME_PARAMS", "HOME_EXTRAS",
                "ANDROID_IMPORTS", "ANDROID_EXTRA", "ACTIVITY_IMPORTS", "PUSH_INTENT", "PERMISSIONS",
                "GRADLE_HEAD", "GRADLE_TAIL", "GITIGNORE_EXTRA", "DELEGATE", "BRIDGES", "PLIST_KEYS",
                "PBX_BUILD_FILES", "PBX_FRAMEWORK_FILES", "PBX_PRODUCT_DEPS", "PBX_PACKAGE_REFS",
                "PBX_PACKAGE_SECTIONS", "XCCONFIG_EXTRA", "SMOKE_MARKERS"):
        ctx[key] = ""

    # -- shared: dependencies, framework exports --------------------------------------------
    ctx["FEATURE_DEPS"] = "".join(f"                implementation({_alias(a)})\n"
                                  for a in _feature_artifacts(f))
    if bridged:
        ctx["FRAMEWORK_EXPORTS"] = (
            "            // The provider bridges keep their plain names in the header (the Swift halves conform to them).\n"
            + "".join(f"            export({_alias(c[2].artifact)})\n" for c in bridged))
        ctx["IOS_MAIN_DEPS"] = ("        iosMain.dependencies {\n"
                                + "".join(f"            api({_alias(c[2].artifact)})\n" for c in bridged)
                                + "        }\n")

    # -- App.kt ----------------------------------------------------------------------------
    lines = []
    if f.get("avatar"):
        out["imports"].append(f"import {KMP_AVATAR_UPLOAD_FACTORY}\n")
        lines.append("    // stapel-cdn: the media uploader the profiles avatar change sends the picture to.\n"
                     f"    modules({KMP_AVATAR_UPLOAD_FACTORY.rsplit('.', 1)[1]}())\n")
    if sinks is not None:
        lines.append("    modules(*appAnalyticsModules(target).toTypedArray())\n")
    if push is not None:
        lines.append("    modules(*appPushModules().toTypedArray())\n")
    if vendor_caps:
        lines.append("    modules(*platformProviderModules().toTypedArray())\n")
    ctx["FEATURE_LINES"] = "".join(lines)
    if sinks is not None or push is not None:
        ctx["AFTER_BUILD"] = (".also { app ->\n" + ("    attachPush(app)\n" if push is not None else "")
                              + ("    bindAnalyticsIdentity(app)\n" if sinks is not None else "") + "}")
        ctx["HOME_ARGS"] = "context"
        ctx["HOME_PARAMS"] = "context: BrickContext"
        ctx["HOME_EXTRAS"] = (("        AnalyticsConsentSheet(context.koin)\n" if sinks is not None else "")
                              + ("        PushSwitch(context.koin)\n" if push is not None else ""))

    if sinks is not None:
        registries = [cls for brick, cls in pv.EVENT_REGISTRIES.items() if brick in f.get("installed", ())]
        registry = " + ".join(f"{c.rsplit('.', 1)[1]}.registry" for c in registries) or "null"
        imports = "".join(f"import {c}\n" for c in registries)
        collector = ""
        if "stapel" in sinks:
            cap = pv.PROVIDERS["stapel"].analytics
            imports += f"import {cap.module}\nimport stapel.analytics.collector.CollectorOptions\n"
            collector = f"    {cap.module_name}(CollectorOptions(source = target)),\n"
        files[f"shared/src/commonMain/kotlin/{pkg_path}/AppAnalytics.kt"] = ("AppAnalytics.kt", {
            "ANALYTICS_IMPORTS": "".join(sorted(imports.splitlines(keepends=True))),
            "CONSENT_DEFAULT": "OptIn" if f["consent"] == "opt-in" else "OptOut",
            "REGISTRY": registry, "COLLECTOR_LINE": collector})
    if push is not None:
        files[f"shared/src/commonMain/kotlin/{pkg_path}/AppPush.kt"] = ("AppPush.kt", {
            "ASK_POLICY": "OnDemand" if f["ask"] == "on-demand" else "AfterSignIn",
            "ASK_DOC": ("only when the person turns notifications on ([PushSwitch])" if f["ask"] == "on-demand"
                        else "after the first sign-in of an installation (iOS: provisional, no prompt)")})

    # -- per-platform provider modules ----------------------------------------------------------
    if vendor_caps:
        files[f"shared/src/commonMain/kotlin/{pkg_path}/AppProviders.kt"] = ("AppProviders.kt", {})
        for target in ("android", "ios", "jvm"):
            imports, calls = [], []
            for key, _kind, cap in vendor_caps:
                imports.append(f"import {cap.module}\n")
                if target == "ios":
                    arg = _lower_first(cap.bridge_interface.rsplit(".", 1)[1])
                elif key == "appmetrica":
                    arg = "APPMETRICA_API_KEY"
                else:
                    arg = ""
                calls.append(f"    {cap.module_name}({arg}),\n")
            if appmetrica and target != "ios":
                imports.append(f"import {pkg}.generated.APPMETRICA_API_KEY\n")
            files[f"shared/src/{target}Main/kotlin/{pkg_path}/AppProviders.{target}.kt"] = (
                "AppProviders.platform.kt", {"PROVIDER_IMPORTS": "".join(sorted(set(imports))),
                                             "PROVIDER_CALLS": "".join(calls)})
    if sinks is not None or push is not None:
        asserts, gate_imports = [], ["import kotlin.test.assertNotNull\n"]
        if sinks is not None:
            gate_imports += ["import stapel.analytics.AnalyticsControl\n", "import stapel.analytics.AnalyticsSink\n"]
            want = ", ".join(json.dumps(s) for s in sorted(["log", *sinks]))
            asserts.append("            assertNotNull(app.koin.getOrNull<AnalyticsControl>())\n"
                           f"            assertEquals(setOf({want}), app.koin.getAll<AnalyticsSink>().map {{ it.id }}.toSet())\n")
        if push is not None:
            gate_imports += ["import stapel.push.PushController\n", "import stapel.push.PushProvider\n"]
            want = ", ".join(json.dumps(p) for p in sorted(push))
            asserts.append("            assertNotNull(app.koin.getOrNull<PushController>())\n"
                           f"            assertEquals(setOf({want}), app.koin.getAll<PushProvider>().map {{ it.id }}.toSet())\n")
        files[f"shared/src/commonTest/kotlin/{pkg_path}/ProvidersGateTest.kt"] = ("ProvidersGateTest.kt", {
            "GATE_IMPORTS": "".join(sorted(gate_imports)), "GATE_ASSERTS": "".join(asserts)})

    # -- secrets (AppMetrica API key) -------------------------------------------------------------
    if appmetrica:
        ctx["SECRETS_SRC"] = "            kotlin.srcDir(generateAppSecrets)\n"
        ctx["SECRETS_TASK"] = _SECRETS_TASK.replace("{{PKG_PATH}}", pkg_path).replace("{{PKG}}", pkg)
        files["stapel.secrets.properties.example"] = (
            "# Copy to stapel.secrets.properties (gitignored) or set the environment variable instead.\n"
            "# STAPEL_APPMETRICA_API_KEY / appmetrica.apiKey: the AppMetrica application's API key.\n"
            "appmetrica.apiKey=\n")

    # -- Android ------------------------------------------------------------------------------
    if push is not None:
        ctx["PERMISSIONS"] = '    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />\n'
        ctx["ANDROID_IMPORTS"] = "import android.content.Intent\nimport stapel.push.firebase.handleFirebasePushIntent\n"
        ctx["ANDROID_EXTRA"] = (
            "\n/** Hands a tapped notification (the activity's `onNewIntent`) to the push provider; true when it was one. */\n"
            "fun handlePushIntent(intent: Intent): Boolean = handleFirebasePushIntent(intent)\n")
        ctx["ACTIVITY_IMPORTS"] = f"import {pkg}.handlePushIntent\n"
        ctx["PUSH_INTENT"] = ("        // A tapped notification while the app runs; the launch intent's tap is read by the provider.\n"
                              "        if (handlePushIntent(intent)) return\n")
    if firebase:
        ctx["GRADLE_HEAD"] = _GRADLE_FIREBASE_HEAD.replace("{{VERSION}}", pv.google_services_plugin_version())
        ctx["GRADLE_TAIL"] = _GRADLE_FIREBASE_TAIL
    ignored = []
    if firebase:
        ignored += ["androidApp/google-services.json", "iosApp/iosApp/GoogleService-Info.plist"]
    if appmetrica:
        ignored.append("stapel.secrets.properties")
    if ignored:
        ctx["GITIGNORE_EXTRA"] = ("\n# Deploy inputs (README \"Analytics and push\"); the *.example files show their shape.\n"
                                  + "".join(f"{p}\n" for p in ignored))

    # -- iOS shell ------------------------------------------------------------------------------
    swift: list[str] = []
    if push is not None:
        swift.append(pv.APP_DELEGATE_FILE)
    for key in vendors:
        swift.append(pv.PROVIDERS[key].core_file)
    swift += [c[2].bridge_file for c in bridged]
    for rel in swift:
        files[f"iosApp/iosApp/Stapel/{rel.rsplit('/', 1)[1]}"] = _VENDORED[rel]
    bridge_lines = []
    if appmetrica:
        bridge_lines.append("        StapelAppMetrica.apiKey = IosBridgesKt.appMetricaApiKey()\n")
    if push is not None:
        push_bridges = ", ".join(f"{c[2].bridge_class}.shared" for c in bridged if c[1] == "push")
        bridge_lines.append(f"        StapelAppDelegate.handlers = [{push_bridges}]\n")
        ctx["DELEGATE"] = ("    // The one app delegate (Stapel/StapelAppDelegate.swift): APNs and notification callbacks,\n"
                           "    // forwarded explicitly (no SDK swizzling).\n"
                           "    @UIApplicationDelegateAdaptor(StapelAppDelegate.self) var delegate\n\n")
    if bridged:
        params = ", ".join(f"{_lower_first(c[2].bridge_interface.rsplit('.', 1)[1]).removesuffix('Bridge')}: "
                           f"{c[2].bridge_class}.shared" for c in bridged)
        bridge_lines.append(f"        IosBridgesKt.installBridges({params})\n")
        ctx["BRIDGES"] = ("        // The vendor bridges (Stapel/), handed to Kotlin before the first screen creates the app.\n"
                          + "".join(bridge_lines))
        names = [(c[2].bridge_interface, _lower_first(c[2].bridge_interface.rsplit(".", 1)[1])) for c in bridged]
        files[f"shared/src/iosMain/kotlin/{pkg_path}/IosBridges.kt"] = ("IosBridges.kt", {
            "BRIDGE_IMPORTS": "".join(f"import {fq}\n" for fq, _ in sorted(names)),
            "BRIDGE_VARS": "".join(f"internal var {v}: {fq.rsplit('.', 1)[1]}? = null\n" for fq, v in names),
            "BRIDGE_PARAMS": ", ".join(f"{v.removesuffix('Bridge')}: {fq.rsplit('.', 1)[1]}?" for fq, v in names),
            "BRIDGE_ASSIGN": "".join(f"    {v} = {v.removesuffix('Bridge')}\n" for _, v in names),
            "BRIDGE_EXTRA": ("\n/** The AppMetrica API key of this build (stapel.secrets.properties / "
                             "STAPEL_APPMETRICA_API_KEY); empty = unconfigured. */\n"
                             f"fun appMetricaApiKey(): String = {pkg}.generated.APPMETRICA_API_KEY ?: \"\"\n")
            if appmetrica else ""})
    plist = []
    if sinks is not None and "firebase" in sinks and f["consent"] == "opt-in":
        plist.append(("FIREBASE_ANALYTICS_COLLECTION_ENABLED", False))
    if firebase:
        plist.append(("FirebaseAppDelegateProxyEnabled", False))
    if push is not None and "firebase" in push:
        plist.append(("FirebaseMessagingAutoInitEnabled", False))
    # Without a config file each vendor provider says so at start, and the app runs (§6 rule 3).
    markers = []
    if firebase and "ios" not in config:
        if push is not None:
            markers.append("stapel.push unavailable")
        if "firebase" in (sinks or ()):
            markers.append("stapel.analytics unavailable firebase unconfigured")
    if markers:
        ctx["SMOKE_MARKERS"] = ("# The vendor providers without their config: named no-ops, the app runs.\n"
                                + "".join(f"wait_for '{m}' 30\n" for m in markers))
    ctx["PLIST_KEYS"] = "".join(f"\t<key>{k}</key>\n\t<{'true' if v else 'false'}/>\n" for k, v in plist)
    if push is not None:
        files["iosApp/Configuration/iosApp.entitlements"] = ("iosApp.entitlements",
                                                             {"APS_ENVIRONMENT": "$(APS_ENVIRONMENT)"})
        ctx["XCCONFIG_EXTRA"] = (
            "\n// Push Notifications capability: the aps-environment entitlement (development; Release builds production).\n"
            "CODE_SIGN_ENTITLEMENTS=Configuration/iosApp.entitlements\n"
            "APS_ENVIRONMENT=development\n"
            "APS_ENVIRONMENT[config=Release]=production\n")
    packages: dict[str, list[str]] = {}
    for key, _kind, cap in vendor_caps:
        packages.setdefault(pv.PROVIDERS[key].swift_package, [])
        packages[pv.PROVIDERS[key].swift_package] += [p for p in cap.products
                                                      if p not in packages[pv.PROVIDERS[key].swift_package]]
    if packages:
        spm = pv.swift_packages()
        products = [(pkg_name, product) for pkg_name, prods in packages.items() for product in prods]
        ref = {p: _pbx_id(f"package:{p}") for p in packages}
        prod = {x: _pbx_id(f"product:{x}") for _, x in products}
        bf = {x: _pbx_id(f"buildfile:{x}") for _, x in products}
        ctx["PBX_BUILD_FILES"] = (
            "/* Begin PBXBuildFile section */\n"
            + "".join(f"\t\t{bf[x]} /* {x} in Frameworks */ = {{isa = PBXBuildFile; productRef = {prod[x]} /* {x} */; }};\n"
                      for _, x in products)
            + "/* End PBXBuildFile section */\n\n")
        ctx["PBX_FRAMEWORK_FILES"] = "".join(f"\t\t\t\t{bf[x]} /* {x} in Frameworks */,\n" for _, x in products)
        ctx["PBX_PRODUCT_DEPS"] = "".join(f"\t\t\t\t{prod[x]} /* {x} */,\n" for _, x in products)
        ctx["PBX_PACKAGE_REFS"] = (
            "\t\t\tpackageReferences = (\n"
            + "".join(f"\t\t\t\t{ref[p]} /* XCRemoteSwiftPackageReference \"{p}\" */,\n" for p in packages)
            + "\t\t\t);\n")
        ctx["PBX_PACKAGE_SECTIONS"] = (
            "\n/* Begin XCRemoteSwiftPackageReference section */\n"
            + "".join(f"\t\t{ref[p]} /* XCRemoteSwiftPackageReference \"{p}\" */ = {{\n"
                      "\t\t\tisa = XCRemoteSwiftPackageReference;\n"
                      f"\t\t\trepositoryURL = \"{spm[p]['url']}\";\n"
                      "\t\t\trequirement = {\n\t\t\t\tkind = exactVersion;\n"
                      f"\t\t\t\tversion = {spm[p]['exactVersion']};\n\t\t\t}};\n\t\t}};\n" for p in packages)
            + "/* End XCRemoteSwiftPackageReference section */\n\n"
            "/* Begin XCSwiftPackageProductDependency section */\n"
            + "".join(f"\t\t{prod[x]} /* {x} */ = {{\n\t\t\tisa = XCSwiftPackageProductDependency;\n"
                      f"\t\t\tpackage = {ref[p]} /* XCRemoteSwiftPackageReference \"{p}\" */;\n"
                      f"\t\t\tproductName = {x};\n\t\t}};\n" for p, x in products)
            + "/* End XCSwiftPackageProductDependency section */\n")

    # -- config files (§6) ------------------------------------------------------------------------
    if firebase:
        files["androidApp/google-services.json.example"] = json.dumps(_firebase_android_example(app_id), indent=2) + "\n"
        files["iosApp/GoogleService-Info.plist.example"] = _firebase_ios_example(app_id)
        if "android" in config:
            files["androidApp/google-services.json"] = config["android"]["data"]
        if "ios" in config:
            files["iosApp/iosApp/GoogleService-Info.plist"] = config["ios"]["data"]
        missing = [name for platform, name in (("android", "mobile/androidApp/google-services.json"),
                                               ("ios", "mobile/iosApp/iosApp/GoogleService-Info.plist"))
                   if platform not in config]
        if missing:
            out["notes"].append(
                "mobile: Firebase is wired without " + " and ".join(missing) + " — its providers report "
                "unconfigured until the file is there (gitignored; the *.example next to it shows the "
                "shape); an Android release build refuses without it")
    configured = {"android": "android" in config, "ios": "ios" in config} if firebase else None

    # -- the echo ---------------------------------------------------------------------------------
    echo = out["echo"]
    if sinks is not None:
        echo["analytics"] = {"sinks": sinks, "consent": f["consent"]}
    if push is not None:
        echo["push"] = {"providers": push, "ask": f["ask"], "rich": False,
                        "configured": configured if "firebase" in push else None}
    if caps:
        spm = pv.swift_packages()
        provs: dict[str, dict] = {}
        for key, kind, cap in caps:
            entry = provs.setdefault(key, {"artifacts": {}})
            entry["artifacts"][kind] = cap.artifact
            if cap.bridge_file:
                constant, abi = pv.bridge_abi(cap.bridge_file)
                entry.setdefault("bridgeAbi", {})[kind] = {"constant": constant, "abi": abi}
            p = pv.PROVIDERS[key]
            if p.swift_package:
                entry.setdefault("ios", {"spm": {**spm[p.swift_package], "products": []}})
                entry["ios"]["spm"]["products"] += [x for x in cap.products if x not in entry["ios"]["spm"]["products"]]
            if key == "firebase":
                entry["ios"]["configFile"] = p.ios_config
                entry["android"] = {"gradlePlugin": {"id": "com.google.gms.google-services",
                                                     "version": pv.google_services_plugin_version()},
                                    "configFile": p.android_config}
                entry["configured"] = configured
        echo["providers"] = provs
    return out


_SECRETS_TASK = '''// --- secrets of this build: stapel.secrets.properties (gitignored) or the environment -> AppSecrets.kt ---
val generateAppSecrets = tasks.register("generateAppSecrets") {
    val secretsFile = rootProject.layout.projectDirectory.file("stapel.secrets.properties").asFile
    val envKey = providers.environmentVariable("STAPEL_APPMETRICA_API_KEY")
    val out = layout.buildDirectory.dir("generated/appSecrets")
    inputs.property("appmetrica", envKey.orElse(""))
    inputs.property("secretsFilePresent", secretsFile.exists())
    if (secretsFile.exists()) inputs.file(secretsFile)
    outputs.dir(out)
    doLast {
        val props = java.util.Properties()
        if (secretsFile.exists()) secretsFile.inputStream().use { props.load(it) }
        val key = envKey.orNull?.takeIf { it.isNotBlank() } ?: props.getProperty("appmetrica.apiKey")?.takeIf { it.isNotBlank() }
        require(key == null || key.matches(Regex("[A-Za-z0-9-]+"))) { "AppMetrica API key: letters, digits and '-' only" }
        val f = out.get().asFile.resolve("{{PKG_PATH}}/generated/AppSecrets.kt")
        f.parentFile.mkdirs()
        f.writeText(
            "// Generated from stapel.secrets.properties / the environment. Do not edit, never commit.\\npackage {{PKG}}.generated\\n\\n" +
                "internal val APPMETRICA_API_KEY: String? = " + (key?.let { "\\"$it\\"" } ?: "null") + "\\n",
        )
    }
}

'''

_GRADLE_FIREBASE_HEAD = '''// Firebase (stapel.composition.json "analytics" / "push"): androidApp/google-services.json is a gitignored
// deploy input (google-services.json.example shows its shape). The google-services plugin is applied only when
// the file is there; without it the Firebase providers report unconfigured and the app runs.
buildscript {
    if (projectDir.resolve("google-services.json").exists()) {
        repositories { google(); mavenCentral() }
        dependencies { classpath("com.google.gms:google-services:{{VERSION}}") }
    }
}

'''

_GRADLE_FIREBASE_TAIL = '''
// Without google-services.json the Firebase providers are no-ops that say so (`stapel.analytics unavailable
// firebase unconfigured google-services.json`, `stapel.push unavailable …`); a release build refuses instead.
val firebaseConfig = layout.projectDirectory.file("google-services.json").asFile
if (firebaseConfig.exists()) apply(plugin = "com.google.gms.google-services")
tasks.matching { it.name == "preReleaseBuild" }.configureEach {
    val config = firebaseConfig
    doFirst {
        if (!config.exists()) {
            throw GradleException(
                "Firebase: a release build without androidApp/google-services.json would ship analytics and push dead. " +
                    "Put the Firebase app's file there (gitignored; google-services.json.example shows its shape).",
            )
        }
    }
}
'''


def _firebase_android_example(app_id: str) -> dict:
    """The shape of google-services.json with fake identifiers (never a real key)."""
    return {
        "project_info": {"project_number": "000000000000", "project_id": "example-project",
                         "storage_bucket": "example-project.firebasestorage.app"},
        "client": [{
            "client_info": {"mobilesdk_app_id": "1:000000000000:android:0000000000000000",
                            "android_client_info": {"package_name": app_id}},
            "oauth_client": [],
            "api_key": [{"current_key": "REPLACE_WITH_THE_FIREBASE_PROJECTS_KEY"}],
            "services": {"appinvite_service": {"other_platform_oauth_client": []}},
        }],
        "configuration_version": "1",
    }


def _firebase_ios_example(app_id: str) -> str:
    import plistlib

    return plistlib.dumps({
        "API_KEY": "REPLACE_WITH_THE_FIREBASE_PROJECTS_KEY",
        "GCM_SENDER_ID": "000000000000",
        "PLIST_VERSION": "1",
        "BUNDLE_ID": app_id,
        "PROJECT_ID": "example-project",
        "STORAGE_BUCKET": "example-project.firebasestorage.app",
        "IS_ADS_ENABLED": False,
        "IS_ANALYTICS_ENABLED": False,
        "IS_APPINVITE_ENABLED": True,
        "IS_GCM_ENABLED": True,
        "IS_SIGNIN_ENABLED": True,
        "GOOGLE_APP_ID": "1:000000000000:ios:0000000000000000",
    }, sort_keys=False).decode("utf-8")


def _readme_features(composition: dict) -> list[str]:
    analytics, push = composition.get("analytics"), composition.get("push")
    if analytics is None and push is None:
        return []
    lines = ["", "## Analytics and push", ""]
    if analytics is not None:
        lines += [
            f"Analytics sinks: {', '.join(f'`{s}`' for s in analytics['sinks'])} and the log sink; consent "
            f"default `{analytics['consent']}` (`shared/.../AppAnalytics.kt`, with the consent sheet the home",
            "screen shows while the answer is pending: nothing leaves the device before \"Allow\").",
        ]
    if push is not None:
        lines += [
            f"Push: {', '.join(f'`{p}`' for p in push['providers'])}, permission asked `{push['ask']}` "
            "(`shared/.../AppPush.kt`); devices register with the notifications",
            "backend for the signed-in account. iOS: the Push Notifications capability is the",
            "`aps-environment` entitlement (`iosApp/Configuration/iosApp.entitlements`).",
        ]
    providers = composition.get("providers", {})
    if "firebase" in providers:
        lines += [
            "",
            "Firebase config files are deploy inputs, never committed (`.gitignore`): put the Firebase",
            "app's `google-services.json` in `androidApp/` and its `GoogleService-Info.plist` in",
            "`iosApp/iosApp/` (the `*.example` files show the shape; the app id must be the app's). Without",
            "them the Firebase providers report unconfigured (`stapel.analytics unavailable firebase",
            "unconfigured …`, `stapel.push unavailable …`) and the app runs; an Android release build refuses.",
            "The iOS shell's Swift bridges (`iosApp/iosApp/Stapel/`) come from stapel-kmp `ios-bridges/`;",
            "re-scaffold when a bridge's protocol version (`providers` in `stapel.composition.json`) moves.",
        ]
    if "appmetrica" in providers:
        lines += [
            "",
            "AppMetrica's API key: `STAPEL_APPMETRICA_API_KEY` at build time or `stapel.secrets.properties`",
            "(gitignored; `stapel.secrets.properties.example`). Without it the sink reports unconfigured.",
        ]
    return lines


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
    lines += _readme_features(composition)
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
