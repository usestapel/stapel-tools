"""``stapel-nav-plan --svg``: wireframes drawn from the resolved plan alone."""
import json
import xml.dom.minidom
from pathlib import Path

import pytest

from stapel_tools.composition.wireframe import CANVAS, label_for, render, write_wireframes
from stapel_tools.nav_cli import plan_main


def _plan(tmp_path: Path, *flags: str, svg: bool = True) -> tuple[dict, Path]:
    out = tmp_path / "svg"
    argv = [*flags, *(["--svg", str(out)] if svg else [])]
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert plan_main(argv) == 0
    return json.loads(buf.getvalue()), out


def _texts(svg: str) -> list[str]:
    doc = xml.dom.minidom.parseString(svg)
    return [n.firstChild.data for n in doc.getElementsByTagName("text") if n.firstChild]


def test_one_wireframe_per_platform_audience_and_size_class(tmp_path):
    plan, out = _plan(tmp_path, "--modules", "shop", "--mobile", "kmp", "--title", "Acme")
    names = sorted(p.name for p in out.iterdir())
    assert names == sorted(f"{platform}-{audience}-{size}.svg" for platform in ("react", "kmp")
                           for audience in ("guest", "member") for size in CANVAS)
    for path in out.iterdir():
        xml.dom.minidom.parse(str(path))


def test_the_carrier_shows_the_plans_destinations_and_more(tmp_path):
    plan, out = _plan(tmp_path, "--modules", "shop")
    view = plan["react"]["audiences"]["member"]["compact"]
    texts = _texts((out / "react-member-compact.svg").read_text())
    for dest in view["visible"]:
        assert label_for(dest) in texts
    assert ("More" in texts) == bool(view["more"])
    assert f"member · compact · {view['carrier']}" in texts


def test_a_guest_sees_sign_in_and_a_member_the_account(tmp_path):
    _, out = _plan(tmp_path, "--modules", "shop")
    assert "Sign in" in _texts((out / "react-guest-expanded.svg").read_text())
    member = _texts((out / "react-member-expanded.svg").read_text())
    assert "Account" in member and "Sign in" not in member


def test_a_drawer_preset_draws_the_menu_button(tmp_path):
    _, out = _plan(tmp_path, "--modules", "auth", "profiles", "--nav-preset", "dashboard")
    assert "Menu" in _texts((out / "react-member-compact.svg").read_text())
    assert "member · expanded · side-panel" in _texts((out / "react-member-expanded.svg").read_text())


def test_same_plan_same_bytes_and_labels_override(tmp_path):
    plan, _ = _plan(tmp_path, "--modules", "shop", svg=False)
    resolved = plan["react"]
    assert render(resolved, "guest", "medium") == render(resolved, "guest", "medium")
    dest = resolved["audiences"]["guest"]["medium"]["visible"][0]
    assert "Shop now" in _texts(render(resolved, "guest", "medium", labels={dest: "Shop now"}))


def test_labels_read_the_id():
    assert label_for("profiles.settings") == "Settings"
    assert label_for("account.root") == "Account"
    assert label_for("listings.my_listings") == "My listings"


def test_write_wireframes_skips_nothing_the_plan_has(tmp_path):
    plan, _ = _plan(tmp_path, "--modules", "shop", svg=False)
    written = write_wireframes(plan, tmp_path / "w")
    assert len(written) == 2 * len(CANVAS)


@pytest.mark.parametrize("carrier", ["bottom-tabs", "island", "top-tabs", "rail", "side-panel",
                                     "drawer", "stack"])
def test_every_carrier_renders(carrier):
    resolved = {"platform": "react", "header": "app", "signIn": None,
                "slots": {"brand": "shell.brand"},
                "audiences": {"member": {"compact": {"carrier": carrier, "visible": ["a.one"],
                                                     "more": []}}}}
    xml.dom.minidom.parseString(render(resolved, "member", "compact"))
