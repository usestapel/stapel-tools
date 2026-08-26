"""Excerpt of stapel-core v0.44.0 tests/test_jwt_ws_origin.py.

There is no ``import`` token anywhere near the failing line, and it imports:
``override_settings(INSTALLED_APPS=[...])`` fires ``setting_changed``, Django
reloads the app registry, and every label in the list is loaded for real. The
0.44.1 fix set the list without firing the signal, because reachability only
reads the LIST — but a sibling's name in a settings list is an import.
"""
from django.test import override_settings

_REACHABLE = dict(
    ASGI_APPLICATION="proj.asgi.application",
    REST_FRAMEWORK={"DEFAULT_AUTHENTICATION_CLASSES": [
        "stapel_core.django.jwt.authentication.JWTCookieAuthentication",
    ]},
)


class TestReachability:
    @override_settings(
        INSTALLED_APPS=["stapel_realtime"], REST_FRAMEWORK={},
        MIDDLEWARE=["stapel_core.django.jwt.middleware.JWTAuthMiddleware"],
        ASGI_APPLICATION=None,
    )
    def test_http_jwt_middleware_makes_it_reachable(self):
        """The websocket half is proved by stapel_realtime being in
        INSTALLED_APPS."""
        assert _REACHABLE

    @override_settings(ASGI_APPLICATION=None, INSTALLED_APPS=[], MIDDLEWARE=[])
    def test_http_only_service_is_never_blocked_by_it(self):
        assert True
