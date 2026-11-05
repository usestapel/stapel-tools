"""stapel-orders in the registry: cart, checkout and orders over opaque
purchasables (PyPI 0.1.0).

Orders is opt-in and NOT a shop companion: companions expand transitively,
classified requires shop, and a classified ad is contacted, not bought. The
shop preset (stapel-shop >= 0.2.38) carries the ``listing`` purchasable, so
``--modules shop orders`` sells the catalog with no glue; the generated
settings import that preset at runtime, so the default cannot drift.
"""
from stapel_tools._wiring import beat_sources
from stapel_tools.assemble_scaffold import assemble_scaffold
from stapel_tools.create_project import STAPEL_LIBS, _expand_with_requires


def _v(text):
    return tuple(int(p) for p in text.split("."))


class TestRegistryEntry:
    def test_registered_with_the_published_pin(self):
        info = STAPEL_LIBS["orders"]
        assert info["dir"] == "stapel_orders"
        assert info["pin"] == "0.1.0"
        assert info["ahead_of_pypi"] is False
        assert info["http"] is True
        assert info["url_prefix"] == "orders/api/"
        assert info["requires"] == []

    def test_purge_beat_is_declared(self):
        assert STAPEL_LIBS["orders"]["beat"] == (
            "stapel_orders.beat", "get_orders_beat_schedule",
        )
        assert ("stapel_orders.beat", "get_orders_beat_schedule") in beat_sources(["orders"])

    def test_shop_pin_carries_the_listing_purchasable(self):
        # 0.2.38 is the preset release with STAPEL_ORDERS["PURCHASABLE_TYPES"]
        # ["listing"]; it requires stapel-listings >= 0.28 (the Function), so
        # the listings pin cannot stay below it.
        assert _v(STAPEL_LIBS["shop"]["pin"]) >= (0, 2, 38)
        assert _v(STAPEL_LIBS["listings"]["pin"]) >= (0, 28, 0)


class TestSelection:
    def test_shop_alone_stays_cart_free(self):
        assert "orders" not in _expand_with_requires(["shop"])

    def test_classified_stays_cart_free(self):
        assert "orders" not in _expand_with_requires(["classified"])

    def test_shop_and_orders_together(self):
        expanded = _expand_with_requires(["shop", "orders"])
        assert {"shop", "listings", "orders"} <= set(expanded)


class TestMountedInGeneratedProject:
    def test_orders_mounts_under_its_v1_prefix(self, tmp_path):
        result = assemble_scaffold("app", libs=["orders"], output_dir=tmp_path, verify=False)
        assert "orders" in result.libs_applied
        settings = (result.project_dir / "config" / "settings.py").read_text()
        urls = (result.project_dir / "config" / "urls.py").read_text()
        assert '"stapel_orders"' in settings
        assert 'path("orders/api/", include("stapel_orders.urls"))' in urls
        assert "stapel_orders.beat" in settings
