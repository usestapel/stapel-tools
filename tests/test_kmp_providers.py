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
