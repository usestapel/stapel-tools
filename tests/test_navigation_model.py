"""The navigation model (navigation-templates spec §3): the block, its
schema, the defaults by archetype, the per-size-class plan and every refusal
NAV-E01…E09 — each one run, each one seen to fire."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from stapel_tools.composition import navigation as N
from stapel_tools.composition.emitter import build_composition
from stapel_tools.composition.react.registry import react_entries
from stapel_tools.composition.react.shell import plan_react_shell

ROOT = Path(__file__).resolve().parents[1]


def _dest(i: int, *, surface: str = "public", top: bool = True, parent=None,
          menu: bool = True) -> N.Destination:
    return N.Destination(id=f"x.d{i}", top=top, parent=parent, menu_visible=menu,
                         surface=surface, order=i, path=f"/d{i}")


def _plan(block=None, *, n=3, auth_model="public", platform="react", modules=("auth",),
          **kw) -> N.PlatformNavigation:
    dests = kw.pop("destinations", [_dest(i) for i in range(n)])
    return N.plan_platform(
        block, platform=platform, destinations=dests, modules=modules,
        auth_model=auth_model, has_sign_in=auth_model in ("public", "internal"),
        sign_in_id="auth.login", **kw)


def _comp(modules, *, navigation=None, landing=False, auth=True, app_nav=(), bound=(),
          strict=False, shell=None, app_labels=None):
    from stapel_tools._auth_model import AuthComposition
    from stapel_tools.create_project import _expand_with_requires

    feats = _expand_with_requires(list(modules))
    return build_composition(
        ctx={"slug": "g", "title": "G"}, project_type="monolith", feature_libs=feats,
        want_auth=auth and "auth" in feats,
        auth_comp=AuthComposition(model="public") if auth and "auth" in feats else None,
        want_landing=landing, navigation=navigation, app_nav=tuple(app_nav),
        app_bindings={"react": tuple(bound)}, strict=strict, shell=shell,
        app_labels=app_labels,
    )


def _code(exc_info) -> str:
    return exc_info.value.code


# --- schema ---------------------------------------------------------------


def test_shipped_schema_equals_the_generated_one():
    shipped = json.loads((ROOT / "stapel_tools/data/navigation.schema.json").read_text())
    assert shipped == N.navigation_schema()


def test_shipped_presets_equal_the_table():
    shipped = json.loads((ROOT / "stapel_tools/data/nav-presets.json").read_text())
    assert shipped == N.presets_document()


def test_schema_enum_per_size_class_is_the_validity_matrix():
    schema = N.navigation_schema()
    for size_class in N.SIZE_CLASSES:
        enum = schema["properties"]["primary"]["properties"][size_class]["enum"]
        assert enum == [c for c, spec in N.CARRIERS.items() if size_class in spec["validIn"]]


def test_unknown_key_is_refused_before_planning():
    with pytest.raises(N.NavigationError) as exc:
        N.validate_block({"primry": {}})
    assert _code(exc) == "NAV-E00"


def test_presets_document_lists_what_each_platform_draws():
    doc = N.presets_document()
    assert set(doc["presets"]) == {"shop", "app", "dashboard", "site", "dock", "tool"}
    assert {"shop", "dashboard"} <= set(doc["drawable"]["react"])
    assert "app" not in doc["drawable"]["react"]


# --- defaults -------------------------------------------------------------


@pytest.mark.parametrize("modules,expected", [
    (["shop"], "shop"), (["classified"], "shop"), (["booking"], "shop"),
    (["categories", "listings"], "shop"), (["social"], "app"), (["chat"], "app"),
    (["auth", "notifications"], "dashboard"),
])
def test_default_preset_by_archetype(modules, expected):
    assert N.default_preset(modules=modules, has_accounts=True, landing=False,
                            candidates=3) == expected


def test_default_without_accounts_and_destinations_is_tool_or_site():
    assert N.default_preset(modules=[], has_accounts=False, landing=False, candidates=0) == "tool"
    assert N.default_preset(modules=[], has_accounts=False, landing=True, candidates=1) == "site"


def test_a_default_the_platform_cannot_draw_falls_back_with_a_note():
    nav = _plan(None, modules=("social",))
    assert nav.chrome == "AppShell" and nav.preset == "dashboard"
    assert any("default preset app is not drawn on react" in n for n in nav.notes)


def test_an_explicit_preset_the_platform_cannot_draw_is_e08():
    with pytest.raises(N.NavigationError) as exc:
        _plan({"preset": "app"})
    assert _code(exc) == "NAV-E08"
    assert "queued on this platform" in str(exc.value)


def test_legacy_shell_ids_are_aliases():
    assert N.alias_block("topbar-only") == {"preset": "shop"}
    assert N.alias_block("topbar-sidenav") == {"preset": "dashboard"}
    assert N.alias_block("sidenav-only") == {"preset": "dashboard", "header": "none"}
    assert N.alias_block(None, "Stack")["platforms"]["kmp"]["primary"]["compact"] == "stack"


# --- refusals -------------------------------------------------------------


def test_e01_carrier_outside_its_size_class():
    with pytest.raises(N.NavigationError) as exc:
        _plan({"primary": {"compact": "side-panel"}})
    assert _code(exc) == "NAV-E01"
    assert "valid on compact" in str(exc.value)


def test_e01_bottom_tabs_on_expanded_names_the_reason():
    with pytest.raises(N.NavigationError) as exc:
        _plan({"primary": {"expanded": "bottom-tabs"}})
    assert _code(exc) == "NAV-E01" and "pointer" in str(exc.value)


def test_e02_eight_pinned_with_a_bar():
    dests = [_dest(i) for i in range(8)]
    with pytest.raises(N.NavigationError) as exc:
        _plan({"preset": "shop", "destinations": {"pinned": [d.id for d in dests]}},
              destinations=dests)
    assert _code(exc) == "NAV-E02"


def test_e03_stack_with_two_destinations():
    with pytest.raises(N.NavigationError) as exc:
        _plan({"preset": "tool"}, n=2)
    assert _code(exc) == "NAV-E03"


def test_e04_account_none_with_a_sign_in_route():
    with pytest.raises(N.NavigationError) as exc:
        _plan({"slots": {"account": "none"}})
    assert _code(exc) == "NAV-E04"


def test_account_none_without_accounts_is_fine():
    nav = _plan({"slots": {"account": "none"}}, auth_model=None)
    assert nav.resolved["slots"]["account"] is None


def test_e05_headerless_without_rail_or_panel():
    with pytest.raises(N.NavigationError) as exc:
        _plan({"preset": "shop", "header": "none"})
    assert _code(exc) == "NAV-E05"


def test_e06_unknown_ids():
    with pytest.raises(N.NavigationError) as exc:
        _plan({"destinations": {"hidden": ["nope.x"]}})
    assert _code(exc) == "NAV-E06"
    with pytest.raises(N.NavigationError) as exc:
        _plan({"slots": {"search": "nope.box"}})
    assert _code(exc) == "NAV-E06"


def test_e06_role_mismatch():
    with pytest.raises(N.NavigationError) as exc:
        _plan({"slots": {"cart": "notifications.bell"}}, embeds=["notifications.bell"])
    assert _code(exc) == "NAV-E06" and "role" in str(exc.value)


def test_e06_hiding_the_sign_in_route():
    dests = [_dest(0), N.Destination(id="auth.login", top=True, parent=None,
                                     menu_visible=False, surface="public", order=0)]
    with pytest.raises(N.NavigationError) as exc:
        _plan({"destinations": {"hidden": ["auth.login"]}}, destinations=dests)
    assert _code(exc) == "NAV-E06"


def test_e07_two_candidates_for_an_exclusive_slot(monkeypatch):
    monkeypatch.setitem(N.EMBED_ROLES, "x.account_two", "account")
    with pytest.raises(N.NavigationError) as exc:
        _plan({}, embeds=["auth.account_menu", "x.account_two"])
    assert _code(exc) == "NAV-E07"


def test_e08_header_none_is_queued_on_react():
    with pytest.raises(N.NavigationError) as exc:
        _plan({"preset": "dashboard", "header": "none"})
    assert _code(exc) == "NAV-E08"


def test_e09_unbound_own_page_under_strict():
    comp = _comp(["shop"], strict=True, app_nav=[
        {"id": "app.cart", "labelKey": "app.nav.cart", "icon": "cart", "path": "/cart",
         "menuVisibleDefault": False}])
    with pytest.raises(N.NavigationError) as exc:
        plan_react_shell(comp, react_entries(comp.modules))
    assert _code(exc) == "NAV-E09"


def test_unbound_own_page_without_strict_is_pending():
    comp = _comp(["shop"], app_nav=[
        {"id": "app.cart", "labelKey": "app.nav.cart", "icon": "cart", "path": "/cart",
         "menuVisibleDefault": False, "via": "categories.catalog"}])
    shell = plan_react_shell(comp, react_entries(comp.modules))
    assert "app.cart" in shell.resolved["pending"]
    assert shell.reach["/cart"] == "via:categories.catalog"


# --- destinations ---------------------------------------------------------


@pytest.mark.parametrize("n", [0, 1, 2, 4, 5, 6, 9])
@pytest.mark.parametrize("preset", ["shop", "dashboard", "site"])
def test_nothing_is_dropped(n, preset):
    nav = _plan({"preset": preset}, n=n)
    candidates = [f"x.d{i}" for i in range(n)]
    for audience in nav.resolved["audiences"].values():
        for plan in audience.values():
            assert sorted(plan["visible"] + plan["more"]) == sorted(candidates)


def test_a_capped_carrier_keeps_first_cap_minus_one_and_more():
    # stapel-shell's BottomBar draws "More", so the cap holds there.
    nav = _plan({"preset": "app"}, n=7, platform="kmp")
    compact = nav.resolved["audiences"]["member"]["compact"]
    assert compact["carrier"] == "bottom-tabs"
    assert len(compact["visible"]) == 4 and len(compact["more"]) == 3


# --- every destination reachable at every size class (NAV-E10) -------------


def test_a_dock_past_its_cap_gives_way_to_the_drawer_and_says_why():
    """PublicShell 0.20.1's dock has no More: six destinations on the island
    would leave the sixth unreachable on a phone."""
    nav = _plan({"preset": "shop"}, n=7)
    compact = nav.resolved["audiences"]["member"]["compact"]
    assert compact["carrier"] == "drawer"
    assert compact["more"] == [] and len(compact["visible"]) == 7
    assert nav.primary["compact"] == "drawer"
    assert any("island -> drawer" in n and "no More" in n for n in nav.notes)


def test_a_dock_within_its_cap_stays_the_dock():
    nav = _plan({"preset": "shop"}, n=5)
    assert nav.resolved["audiences"]["member"]["compact"]["carrier"] == "island"
    assert not any("island -> drawer" in n for n in nav.notes)


def test_an_explicit_island_past_its_cap_is_refused_not_switched():
    with pytest.raises(N.NavigationError) as err:
        _plan({"preset": "shop", "primary": {"compact": "island"}}, n=7)
    assert err.value.code == "NAV-E10"
    assert "choose drawer for compact" in str(err.value)


def test_the_reachability_check_fires_on_a_stranded_destination():
    """Inversion: the invariant itself, fed a plan the fallback did not fix."""
    chrome = N.PLATFORM_CHROMES["react"][0]
    audiences = {"member": {"compact": {"carrier": "island", "visible": ["a"], "more": ["b"]},
                            "medium": {"carrier": "top-tabs", "visible": ["a"], "more": ["b"]},
                            "expanded": {"carrier": None, "visible": [], "more": ["a"]}}}
    with pytest.raises(N.NavigationError) as err:
        N._check_reachable(audiences, chrome)
    assert err.value.code == "NAV-E10" and "member compact: b" in str(err.value)
    audiences["member"]["compact"]["more"] = []
    N._check_reachable(audiences, chrome)  # top-tabs overflow and an undrawn bar are fine


# --- audiences: staff, guest per destination, hidden subtrees ---------------


def _admin_dests():
    return [_dest(0), _dest(1, surface="member"),
            N.Destination(id="admin.root", top=True, parent=None, menu_visible=True,
                          surface="member", order=90, path="/staff"),
            N.Destination(id="admin.users", top=False, parent="admin.root", menu_visible=True,
                          surface="member", order=1, path="users")]


def test_the_admin_section_is_shown_to_staff_only_on_the_storefront_chrome():
    nav = _plan({"preset": "shop"}, destinations=_admin_dests())
    aud = nav.resolved["audiences"]
    assert "admin.root" not in aud["member"]["expanded"]["visible"]
    assert "admin.root" in aud["staff"]["expanded"]["visible"]
    assert "admin.root" not in aud["guest"]["expanded"]["visible"]
    assert nav.resolved["staff"] == {"section": "admin.root", "audience": "staff"}


def test_appshell_keeps_the_admin_section_listed_and_switched_off_for_members():
    nav = _plan({"preset": "dashboard"}, destinations=_admin_dests())
    assert "staff" not in nav.resolved["audiences"]
    assert "admin.root" in nav.resolved["audiences"]["member"]["expanded"]["visible"]


def test_a_storefront_archetype_hides_member_destinations_from_a_guest_by_default():
    nav = _plan(None, destinations=[_dest(0), _dest(1), _dest(2, surface="member")],
                modules=("auth", "shop"))
    assert nav.resolved["guest"]["memberDestinations"] == "hide"
    assert "x.d2" not in nav.resolved["audiences"]["guest"]["expanded"]["visible"]
    dash = _plan(None, destinations=[_dest(0), _dest(1), _dest(2, surface="member")])
    assert dash.resolved["guest"]["memberDestinations"] == "wall"


def test_guest_wall_and_hide_per_destination():
    dests = [_dest(0), _dest(1, surface="member"), _dest(2, surface="member")]
    nav = _plan({"preset": "shop", "guest": {"memberDestinations": "hide", "wall": ["x.d2"]}},
                destinations=dests)
    assert nav.resolved["audiences"]["guest"]["expanded"]["visible"] == ["x.d0", "x.d2"]
    nav = _plan({"preset": "shop", "guest": {"memberDestinations": "wall", "hide": ["x.d2"]}},
                destinations=dests)
    assert nav.resolved["audiences"]["guest"]["expanded"]["visible"] == ["x.d0", "x.d1"]
    with pytest.raises(N.NavigationError):
        _plan({"guest": {"wall": ["x.d1"], "hide": ["x.d1"]}}, destinations=dests)
    with pytest.raises(N.NavigationError):
        _plan({"guest": {"wall": ["nope.x"]}}, destinations=dests)


def test_a_bar_with_one_destination_is_not_drawn_and_says_so():
    nav = _plan({"preset": "shop"}, n=1)
    assert nav.resolved["audiences"]["member"]["compact"]["carrier"] is None
    assert any("is not drawn" in n for n in nav.notes)


def test_guest_wall_keeps_member_destinations_visible():
    dests = [_dest(0), _dest(1), _dest(2, surface="member")]
    wall = _plan({"preset": "shop"}, destinations=dests)
    hide = _plan({"preset": "shop", "guest": {"memberDestinations": "hide"}},
                 destinations=dests)
    assert wall.resolved["audiences"]["guest"]["expanded"]["visible"] == [
        "x.d0", "x.d1", "x.d2"]
    assert hide.resolved["audiences"]["guest"]["expanded"]["visible"] == ["x.d0", "x.d1"]


def test_internal_accounts_give_the_guest_a_header_only_shell():
    nav = _plan({"preset": "dashboard"}, auth_model="internal")
    assert nav.resolved["audiences"]["guest"]["compact"]["carrier"] == "stack"
    assert nav.resolved["guest"]["memberDestinations"] == "hide"


# --- the composition ------------------------------------------------------


def test_storefront_composite_resolves_to_shop_on_public_shell():
    comp = _comp(["shop"])
    shell = plan_react_shell(comp, react_entries(comp.modules))
    assert shell.shape == "storefront" and shell.routes == "public"
    assert shell.resolved["preset"] == "shop" and shell.resolved["source"] == "default"
    assert shell.resolved["signIn"] == "auth.login"
    assert shell.sign_in_path == "/login"


def test_dashboard_preset_on_a_shop_keeps_the_monolith_routes():
    comp = _comp(["shop"], navigation={"preset": "dashboard"})
    shell = plan_react_shell(comp, react_entries(comp.modules))
    assert shell.shape == "app" and shell.routes == "app"


def test_every_default_archetype_reaches_every_route():
    for modules in (["shop"], ["classified"], ["booking"], ["social"], ["auth"], []):
        comp = _comp(modules)
        shell = plan_react_shell(comp, react_entries(comp.modules))
        assert not [n for n in shell.nav.notes if n.startswith("unreachable")], modules


def test_four_guarantees_of_any_default():
    for modules in (["shop"], ["classified"], ["booking"], ["social"], ["auth"], []):
        comp = _comp(modules)
        resolved = plan_react_shell(comp, react_entries(comp.modules)).resolved
        assert resolved["chrome"] in ("PublicShell", "AppShell")      # mounted
        assert resolved["slots"]["brand"] == "shell.brand"             # a brand
        assert resolved["home"] == "shell.home"                       # a home
        assert resolved["slots"]["theme"] == "shell.theme"            # the theme switch
        if "auth" in comp.modules:
            assert resolved["signIn"] == "auth.login"                 # sign-in


def test_orders_is_pending_until_npm_serves_it():
    from stapel_tools.create_project import FRONTEND_REACT_LIBS, pending_react_pairs

    assert FRONTEND_REACT_LIBS["orders"]["released"] is False
    assert pending_react_pairs(["auth", "orders"]) == ["orders"]
    comp = _comp(["shop", "orders"])
    entries = react_entries(comp.modules)
    assert "orders" not in {e["key"] for e in entries}
    resolved = plan_react_shell(comp, entries).resolved
    assert any(p.startswith("orders: @stapel/orders-react is not on npm") for p in
               resolved["pending"])


def test_orders_binding_mounts_once_released(monkeypatch):
    from stapel_tools import create_project as cp

    monkeypatch.setitem(cp.FRONTEND_REACT_LIBS, "orders",
                        {k: v for k, v in cp.FRONTEND_REACT_LIBS["orders"].items()
                         if k != "released"})
    comp = _comp(["shop", "orders"])
    shell = plan_react_shell(comp, react_entries(comp.modules))
    assert shell.paths["orders.cart"] == "/cart"
    assert shell.reach["/cart"] == "embed:orders.cart_badge"
    assert shell.reach["/checkout"] == "via:orders.cart"
    assert shell.resolved["slots"]["cart"] == "orders.cart_badge"


# --- own labels, own search control, hidden subtrees (plan_react_shell) -----

_PARTS = {"id": "app.parts", "labelKey": "app.nav.parts", "icon": "apps", "path": "/parts"}


def test_an_own_page_without_a_label_is_refused_under_strict():
    comp = _comp(["shop"], strict=True, app_nav=[_PARTS], bound=["app.parts"])
    with pytest.raises(N.NavigationError) as exc:
        plan_react_shell(comp, react_entries(comp.modules))
    assert _code(exc) == "NAV-E11" and "app.nav.parts" in str(exc.value)


def test_an_own_page_with_its_label_plans_under_strict():
    comp = _comp(["shop"], strict=True, app_nav=[_PARTS], bound=["app.parts"],
                 app_labels={"en": {"app.nav.parts": "Parts"}})
    shell = plan_react_shell(comp, react_entries(comp.modules))
    assert shell.resolved["labels"] == {"file": "src/app.labels.json", "missing": []}


def test_an_own_page_without_a_label_is_a_note_and_listed_otherwise():
    comp = _comp(["shop"], app_nav=[_PARTS], bound=["app.parts"])
    shell = plan_react_shell(comp, react_entries(comp.modules))
    assert shell.resolved["labels"]["missing"] == ["app.parts"]
    assert any("no label for app.nav.parts" in n for n in shell.resolved["notes"])


def test_hiding_a_parent_hides_its_subtree_under_strict():
    """Hiding admin.root alone used to be NAV-E06 for every admin.* child."""
    comp = _comp(["shop"], strict=True,
                 navigation={"destinations": {"hidden": ["admin.root"]}})
    shell = plan_react_shell(comp, react_entries(comp.modules))
    children = [e["id"] for m in shell.manifests for e in m["entries"]
                if e["placement"].get("parentId") == "admin.root"]
    assert children, "the shop has admin screens to hide"
    for entry_id in children:
        assert shell.reach[shell.paths[entry_id]] == "hidden", entry_id


def test_an_own_search_control_fills_the_search_slot_and_ends_the_pending_results():
    comp = _comp(["shop"], navigation={"slots": {"search": "app.search_box"}},
                 bound=["app.search_box"])
    shell = plan_react_shell(comp, react_entries(comp.modules))
    assert shell.resolved["slots"]["search"] == "app.search_box"
    results = shell.paths["search.results"]
    assert shell.reach[results] == "slot:search:app.search_box"
    assert not any(p.startswith("search.results") for p in shell.resolved["pending"])
    bare = _comp(["shop"])
    bare_shell = plan_react_shell(bare, react_entries(bare.modules))
    assert any(p.startswith("search.results") for p in bare_shell.resolved["pending"])


def test_an_unbound_own_search_control_is_e09_under_strict_and_empty_otherwise():
    comp = _comp(["shop"], strict=True, navigation={"slots": {"search": "app.search_box"}})
    with pytest.raises(N.NavigationError) as exc:
        plan_react_shell(comp, react_entries(comp.modules))
    assert _code(exc) == "NAV-E09"
    loose = _comp(["shop"], navigation={"slots": {"search": "app.search_box"}})
    shell = plan_react_shell(loose, react_entries(loose.modules))
    assert shell.resolved["slots"]["search"] is None
    with pytest.raises(N.NavigationError):
        bad = _comp(["shop"], navigation={"slots": {"cart": "app.cart_box"}},
                    bound=["app.cart_box"])
        plan_react_shell(bad, react_entries(bad.modules))


def test_an_absolute_child_path_under_the_account_is_its_address():
    """`/account/orders` under account.root used to resolve to
    `/account//account/orders` while routes.tsx mounted `/account/orders`."""
    entry = {"id": "app.orders", "labelKey": "app.nav.orders", "icon": "apps",
             "path": "/account/orders", "level": "submenu", "parentId": "account.root",
             "surface": "member"}
    comp = _comp(["shop"], app_nav=[entry], bound=["app.orders"],
                 app_labels={"en": {"app.nav.orders": "Orders"}})
    shell = plan_react_shell(comp, react_entries(comp.modules))
    assert shell.paths["app.orders"] == "/account/orders"
    relative = _comp(["shop"], app_nav=[{**entry, "path": "orders"}], bound=["app.orders"])
    assert plan_react_shell(relative, react_entries(relative.modules)).paths[
        "app.orders"] == "/account/orders"
