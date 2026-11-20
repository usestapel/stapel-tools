"""Vendor providers of the KMP emitter: analytics sinks and push providers.

ARCH-PUSH-ANALYTICS §7 (stapel-kmp ``tasks/kmp-2026-11-19``). Firebase is the default behind the
Stapel facades, AppMetrica pluggable for analytics, the first-party collector when the project's
backend has the ``analytics`` module.

stapel-kmp's ``kmp-registry.json`` has no ``providers`` section at the vendored commit; it lists
the provider ARTIFACTS (checked here: a provider whose artifact the registry does not name is
refused). What the registry does not carry is in :data:`PROVIDERS`, each fact read from the
pinned stapel-kmp source it names. The versions are NOT copied here: the Swift packages come from
the vendored ``ios-bridges/Package.swift`` (what CI compiles the bridges against), the Android
``google-services`` plugin from the reference app's ``androidApp/build.gradle.kts``, each bridge's
protocol version from the header of its vendored Swift file.

:func:`read_firebase_config` is the one function here that opens a file: ``create_project`` calls
it for ``--mobile-firebase-config`` and hands the result to the emitter, whose ``plan()`` reads
nothing.
"""
from __future__ import annotations

import json
import plistlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from ..model import CompositionError
from . import vendor

ANALYTICS_SINKS: tuple[str, ...] = ("firebase", "appmetrica", "stapel")
PUSH_PROVIDERS: tuple[str, ...] = ("firebase", "appmetrica")
CONSENT_DEFAULTS: tuple[str, ...] = ("opt-in", "opt-out")
PUSH_ASK: tuple[str, ...] = ("on-demand", "after-sign-in")
#: The backend module the first-party sink posts to.
ANALYTICS_MODULE = "analytics"
#: The backend module push registers devices with.
PUSH_MODULE = "notifications"


@dataclass(frozen=True)
class Capability:
    """One provider's analytics or push half."""

    artifact: str
    #: Fully qualified Koin factory (``stapel.<…>.di.<name>``).
    module: str
    #: Vendored Swift file implementing the Kotlin bridge (None: no iOS bridge, e.g. the collector).
    bridge_file: Optional[str] = None
    #: Fully qualified Kotlin bridge interface the Swift class conforms to.
    bridge_interface: Optional[str] = None
    #: Swift package products the app target links for this half.
    products: tuple[str, ...] = ()

    @property
    def bridge_class(self) -> Optional[str]:
        return Path(self.bridge_file).stem if self.bridge_file else None

    @property
    def module_name(self) -> str:
        return self.module.rsplit(".", 1)[1]


@dataclass(frozen=True)
class Provider:
    key: str
    analytics: Optional[Capability] = None
    push: Optional[Capability] = None
    #: Vendored Swift file shared by both halves (configures the SDK once).
    core_file: Optional[str] = None
    #: Swift package (the last path segment of its URL in ios-bridges/Package.swift).
    swift_package: Optional[str] = None
    #: Platform config file the vendor console hands out.
    android_config: Optional[str] = None
    ios_config: Optional[str] = None


#: Facts of stapel-kmp at the vendored commit: capabilities/stapel-{analytics,push}-<vendor>,
#: capabilities/stapel-analytics-collector, ios-bridges/<vendor>/.
PROVIDERS: dict[str, Provider] = {
    "firebase": Provider(
        key="firebase",
        analytics=Capability(
            artifact="stapel-analytics-firebase",
            module="stapel.analytics.firebase.di.firebaseAnalyticsModule",
            bridge_file="ios-bridges/firebase/StapelFirebaseAnalytics.swift",
            bridge_interface="stapel.analytics.firebase.FirebaseAnalyticsBridge",
            products=("FirebaseAnalyticsCore",),
        ),
        push=Capability(
            artifact="stapel-push-firebase",
            module="stapel.push.firebase.di.firebasePushModule",
            bridge_file="ios-bridges/firebase/StapelFirebasePush.swift",
            bridge_interface="stapel.push.firebase.FirebasePushBridge",
            products=("FirebaseMessaging",),
        ),
        core_file="ios-bridges/firebase/StapelFirebase.swift",
        swift_package="firebase-ios-sdk",
        android_config="google-services.json",
        ios_config="GoogleService-Info.plist",
    ),
    "appmetrica": Provider(
        key="appmetrica",
        analytics=Capability(
            artifact="stapel-analytics-appmetrica",
            module="stapel.analytics.appmetrica.di.appMetricaAnalyticsModule",
            bridge_file="ios-bridges/appmetrica/StapelAppMetricaAnalytics.swift",
            bridge_interface="stapel.analytics.appmetrica.AppMetricaAnalyticsBridge",
            products=("AppMetricaCore",),
        ),
        core_file="ios-bridges/appmetrica/StapelAppMetrica.swift",
        swift_package="appmetrica-sdk-ios",
    ),
    "stapel": Provider(
        key="stapel",
        analytics=Capability(
            artifact="stapel-analytics-collector",
            module="stapel.analytics.collector.di.analyticsCollectorModule",
        ),
    ),
}

#: The app delegate every push bridge answers through (ios-bridges/core).
APP_DELEGATE_FILE = "ios-bridges/core/StapelAppDelegate.swift"
#: Facade and adapter artifacts the features add next to the provider artifacts.
ANALYTICS_ARTIFACT = "stapel-analytics"
PUSH_ARTIFACTS = ("stapel-push", "stapel-notifications-push")

#: Generated typed-event registries per brick (stapel-kmp codegen ``FlowEvents``: the brick's
#: ``<Brick>Events.registry``, at the vendored commit only the auth brick generates one).
EVENT_REGISTRIES: dict[str, str] = {"auth": "stapel.auth.api.AuthEvents"}


@lru_cache(maxsize=1)
def swift_packages() -> dict[str, dict[str, str]]:
    """package name -> {"url", "exactVersion"} from the vendored ios-bridges/Package.swift."""
    text = vendor.read_bytes("ios-bridges/Package.swift").decode("utf-8")
    out = {}
    for url, version in re.findall(r'\.package\(url:\s*"([^"]+)",\s*exact:\s*"([^"]+)"\)', text):
        out[url.rstrip("/").rsplit("/", 1)[1]] = {"url": url, "exactVersion": version}
    return out


@lru_cache(maxsize=1)
def google_services_plugin_version() -> str:
    text = vendor.read_bytes("sample-android/androidApp.build.gradle.kts").decode("utf-8")
    match = re.search(r"com\.google\.gms:google-services:([0-9][0-9.]*)", text)
    if not match:
        raise RuntimeError("the vendored reference androidApp build names no google-services plugin version")
    return match.group(1)


@lru_cache(maxsize=None)
def bridge_abi(bridge_file: str) -> tuple[str, int]:
    """(Kotlin constant, version) the vendored Swift bridge declares it implements."""
    head = vendor.read_bytes(bridge_file).decode("utf-8").split("\nimport ", 1)[0]
    match = re.search(r"([A-Z][A-Z_]*_BRIDGE_ABI)\s*=?\s*(\d+)", head)
    if not match:
        raise RuntimeError(f"vendored {bridge_file} names no *_BRIDGE_ABI in its header")
    return match.group(1), int(match.group(2))


def parse_list(value: Optional[str], allowed: tuple[str, ...], flag: str) -> Optional[list[str]]:
    """None = flag absent; "" = bare flag (empty list); else the comma list, checked."""
    if value is None:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    unknown = [v for v in items if v not in allowed]
    if unknown:
        raise CompositionError(f"{flag} {','.join(unknown)}: one or more of {', '.join(allowed)}")
    return list(dict.fromkeys(items))


def read_firebase_config(spec: str) -> dict[str, dict]:
    """``android=<path>,ios=<path>`` -> {platform: {"path", "ids", "data"}}; the files' app ids.

    ``ids`` are the package names of every Android client in ``google-services.json`` and the
    ``BUNDLE_ID`` of ``GoogleService-Info.plist``; the emitter compares them with the app id."""
    out: dict[str, dict] = {}
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        platform, sep, raw = part.partition("=")
        if not sep or platform not in ("android", "ios") or not raw:
            raise CompositionError(
                f"--mobile-firebase-config {part!r}: use android=<google-services.json>,"
                "ios=<GoogleService-Info.plist> (either or both)")
        path = Path(raw).expanduser()
        if not path.is_file():
            raise CompositionError(f"--mobile-firebase-config {platform}={raw}: no such file")
        data = path.read_bytes()
        try:
            if platform == "android":
                doc = json.loads(data)
                ids = [c["client_info"]["android_client_info"]["package_name"]
                       for c in doc.get("client", [])]
            else:
                ids = [plistlib.loads(data)["BUNDLE_ID"]]
        except (ValueError, KeyError, TypeError, plistlib.InvalidFileException) as exc:
            want = "google-services.json" if platform == "android" else "GoogleService-Info.plist"
            raise CompositionError(
                f"--mobile-firebase-config {platform}={raw}: not a Firebase {want} "
                f"({type(exc).__name__}: {exc})") from exc
        out[platform] = {"path": str(path), "ids": ids, "data": data}
    if not out:
        raise CompositionError("--mobile-firebase-config: name android=<path> and/or ios=<path>")
    return out
