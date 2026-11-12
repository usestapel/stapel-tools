"""A listings selection without stapel-geo can publish.

stapel-listings refuses a listing with no coordinates by default
(REQUIRE_LOCATION_ON_PUBLISH); shop and booking select no geo module, so the
composer had no way to ask for a location and every publish answered 400
``listing_location_required``. The wiring now turns the requirement off when
no geo module is selected, under any explicit value.
"""
import pytest

from stapel_tools import _wiring
from stapel_tools.create_project import create_project


def _base(tmp_path, modules):
    create_project(
        name="app", project_type="monolith", title="App", url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=modules,
        output_dir=tmp_path, use_submodules=False, init_git=False,
    )
    return (tmp_path / "app" / "svc-app" / "config" / "settings" / "base.py").read_text()


@pytest.mark.parametrize("composite", ["shop", "booking"])
def test_composites_without_geo_publish_without_a_location(tmp_path, composite):
    assert '"REQUIRE_LOCATION_ON_PUBLISH": False' in _base(tmp_path, [composite])


def test_classified_with_geo_keeps_the_library_default(tmp_path):
    assert "REQUIRE_LOCATION_ON_PUBLISH" not in _base(tmp_path, ["classified"])


def test_explicit_value_wins():
    ns = {"STAPEL_LISTINGS": {"REQUIRE_LOCATION_ON_PUBLISH": True}}
    exec(_wiring.render_wiring(["listings"], flavor="minimal"), ns)  # noqa: S102
    assert ns["STAPEL_LISTINGS"]["REQUIRE_LOCATION_ON_PUBLISH"] is True
    ns = {}
    exec(_wiring.render_wiring(["listings"], flavor="minimal"), ns)  # noqa: S102
    assert ns["STAPEL_LISTINGS"]["REQUIRE_LOCATION_ON_PUBLISH"] is False
