"""What the scaffold writes for the navigation model: one layout route over
every route, the chrome, the own-pages seam, the generated gates, the root
composition, the CLI's refusals before anything is written, the dry run and
regenerate."""
from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from stapel_tools import nav_cli
from stapel_tools.composition.emitter import build_composition, emitter_for

COMMON = ["--title", "Nav App", "--url", "https://nav.example", "--company-name", "Acme",
          "--company-email", "a@nav.example", "--no-git", "--no-submodules"]


def _plan(modules, **kw):
    from stapel_tools._auth_model import AuthComposition
    from stapel_tools.create_project import _expand_with_requires

    feats = _expand_with_requires(list(modules))
    comp = build_composition(
        ctx={"slug": "nav", "title": "Nav App"}, project_type="monolith", feature_libs=feats,
        want_auth="auth" in feats,
        auth_comp=AuthComposition(model="public") if "auth" in feats else None, **kw)
    return emitter_for("react").plan(comp, comp.platform("react"))


def _routes(plan) -> str:
    return plan.files["frontend/src/routes.tsx"]


def _top_level_routes(src: str) -> list[str]:
    """The route objects directly inside `routes` (two-space indent)."""
    body = src.split("export const routes: RouteObject[] = [", 1)[1].split("\n];", 1)[0]
    return [ln for ln in body.splitlines() if re.match(r"^  \{", ln)]


def _composition(plan) -> dict:
    return json.loads(plan.files["frontend/stapel.composition.json"])


@pytest.mark.parametrize("modules,chrome", [
    (["shop"], "PublicShell"), (["auth"], "AppShell"), (["social"], "AppShell"),
    ([], "AppShell"),
])
def test_one_layout_route_over_every_route(modules, chrome):
    plan = _plan(modules)
    src = _routes(plan)
    assert len(_top_level_routes(src)) == 1, src
    assert src.count('id: "stapel-shell"') == 1
    assert f"<{chrome}" in plan.files["frontend/src/chrome.generated.tsx"]
    nav = _composition(plan)["navigation"]["resolved"]
    assert nav["chrome"] == chrome and nav["bare"] == []


def test_shop_mounts_sign_in_and_public_routes_inside_the_shell():
    src = _routes(_plan(["shop"]))
    inside = src.split('id: "stapel-shell"', 1)[1]
    for path in ('"/login"', '"/c"', '"/l/:id"', '"/account"'):
        assert path in inside
    assert "<MemberGate />" in inside


def test_dashboard_wraps_the_member_area_inside_the_same_layout():
    src = _routes(_plan(["auth", "notifications"]))
    inside = src.split('id: "stapel-shell"', 1)[1]
    assert 'path: "/login"' in inside and 'path: "/app"' in inside
    assert "<ProtectedRoute>" in inside and "<Outlet />" in inside


def test_zero_destinations_still_mount_the_shell_with_a_home():
    plan = _plan([])
    assert "frontend/src/pages/Home.tsx" in plan.files
    assert "{ index: true, element: <Home /> }" in _routes(plan)
    assert "frontend/src/App.tsx" not in plan.files


def test_guest_sees_sign_in_member_sees_the_account_menu():
    chrome = _plan(["shop"]).files["frontend/src/chrome.generated.tsx"]
    assert 'useActiveSessionStatus() === "authenticated"' in chrome
    assert "accountSlot={member ? <TopbarEnd /> : <SignInEntry />}" in chrome
    assert 'const SIGN_IN_PATH = "/login";' in chrome
    dashboard = _plan(["auth"]).files["frontend/src/chrome.generated.tsx"]
    assert '"topbar.end": <SignInEntry />' in dashboard or (
        '"topbar.end": <><TopbarEndGuest /><SignInEntry /></>' in dashboard)


def test_no_accounts_no_sign_in_entry():
    chrome = _plan(["categories", "listings"]).files["frontend/src/chrome.generated.tsx"]
    assert "SignInEntry" not in chrome and "useActiveSessionStatus" not in chrome


def test_nav_links_are_anchored_to_the_mounted_paths():
    nav = _plan(["shop"]).files["frontend/src/nav.generated.ts"]
    paths = json.loads(nav.split("NAV_PATHS: Readonly<Record<string, string>> = ", 1)[1]
                       .split(";\n", 1)[0])
    assert paths["profiles.settings"] == "/account/settings"
    assert paths["account.root"] == "/account"
    assert "linkPath: NAV_PATHS[entry.id] ?? entry.linkPath" in nav


def test_generated_gates_are_written():
    plan = _plan(["shop"])
    for name in ("nav.test.ts", "shell.test.tsx", "shell-lint.test.ts", "generated.test.ts"):
        assert f"frontend/src/__tests__/{name}" in plan.files
    shell_test = plan.files["frontend/src/__tests__/shell.test.tsx"]
    assert 'a[href="/login"]' in shell_test and '"public-shell"' in shell_test


def test_generated_hashes_cover_the_generated_files():
    plan = _plan(["shop"])
    recorded = _composition(plan)["navigation"]["resolved"]["generated"]
    import hashlib

    assert set(recorded) >= {"frontend/src/routes.tsx", "frontend/src/nav.generated.ts",
                             "frontend/src/chrome.generated.tsx"}
    for path, digest in recorded.items():
        assert hashlib.sha256(plan.files[path].encode()).hexdigest() == digest


def test_reach_covers_every_mounted_route():
    plan = _plan(["shop"])
    reach = _composition(plan)["navigation"]["resolved"]["reach"]
    for path in re.findall(r'path: "(/[^"]*)"', _routes(plan)):
        assert path in reach or path.startswith("/account"), path


def test_landing_page_renders_inside_the_shell():
    plan = _plan(["auth"], want_landing=True)
    landing = plan.files["frontend/src/LandingPage.tsx"]
    assert "100vh" not in landing and "<main" not in landing
    assert "{ index: true, element: <LandingPage /> }" in _routes(plan)


def test_own_pages_are_mounted_inside_the_shell_and_placed_in_the_nav():
    app_nav = [
        {"id": "app.cart", "labelKey": "app.nav.cart", "icon": "cart", "path": "/cart",
         "menuVisibleDefault": True, "order": 55},
        {"id": "app.orders", "labelKey": "app.nav.orders", "icon": "list-ordered",
         "path": "orders", "level": "submenu", "parentId": "account.root",
         "surface": "member"},
    ]
    plan = _plan(["shop"], app_nav=tuple(app_nav), app_bindings={"react": ("app.cart",)})
    src = _routes(plan)
    assert '{ path: "/cart", element: <AppPage id="app.cart" /> }' in src
    assert '{ path: "orders", element: <AppPage id="app.orders" /> }' in src
    nav = _composition(plan)["navigation"]["resolved"]
    assert nav["reach"]["/cart"] == "menu:app.cart"
    assert nav["reach"]["/account/orders"] == "menu:app.orders"
    assert nav["pending"] == [p for p in nav["pending"] if not p.startswith("app.cart")]
    assert "app.orders" in nav["pending"]
    assert "frontend/src/AppPage.tsx" in plan.files
    assert '"app.cart"' in plan.files["frontend/src/nav.generated.ts"]


# --- the CLI --------------------------------------------------------------


def _create(tmp_path, *flags, name="navapp"):
    return subprocess.run(
        [sys.executable, "-m", "stapel_tools.create_project", name, "--type", "monolith",
         *COMMON, "--output-dir", str(tmp_path), *flags],
        capture_output=True, text=True,
    )


@pytest.mark.parametrize("flags,code", [
    (["--modules", "shop", "--nav", "compact=side-panel"], "NAV-E01"),
    (["--modules", "shop", "--nav-preset", "tool"], "NAV-E03"),
    (["--modules", "shop", "--nav-slot", "account=none"], "NAV-E04"),
    (["--modules", "shop", "--nav-preset", "shop", "--nav-header", "none"], "NAV-E05"),
    (["--modules", "shop", "--nav-hide", "auth.login"], "NAV-E06"),
    (["--modules", "shop", "--nav-preset", "app"], "NAV-E08"),
])
def test_refusals_exit_1_and_write_nothing(tmp_path, flags, code):
    result = _create(tmp_path, *flags)
    assert result.returncode == 1, result.stdout + result.stderr
    assert code in result.stderr
    assert not (tmp_path / "navapp").exists()


def test_e02_from_the_cli(tmp_path):
    pins = ",".join(["categories.catalog", "listings.compose", "profiles.settings",
                     "profiles.connections", "account.root", "admin.root"])
    result = _create(tmp_path, "--modules", "shop", "--nav-pin", pins)
    assert result.returncode == 1 and "NAV-E02" in result.stderr


def test_topbar_only_is_now_rendered_by_the_monolith(tmp_path):
    result = _create(tmp_path, "--modules", "auth", "--shell", "topbar-only")
    assert result.returncode == 0, result.stderr
    echo = json.loads((tmp_path / "navapp" / "frontend" / "stapel.composition.json").read_text())
    assert echo["navigation"]["resolved"]["preset"] == "shop"
    assert echo["navigation"]["resolved"]["chrome"] == "PublicShell"


def test_root_composition_is_input_and_echo_and_regenerate_folds_own_pages(tmp_path):
    result = _create(tmp_path, "--modules", "shop")
    assert result.returncode == 0, result.stderr
    root = tmp_path / "navapp"
    doc = json.loads((root / "stapel.composition.json").read_text())
    assert doc["generator"] == "stapel-create-project"
    assert doc["input"]["modules"][:1] == ["auth"] or "auth" in doc["input"]["modules"]
    assert doc["resolved"]["react"]["preset"] == "shop"
    assert json.loads((root / "stapel.app-nav.json").read_text()) == {"version": 1,
                                                                      "entries": []}
    # A coder leg adds a page: one entry, one binding, then regenerate.
    (root / "stapel.app-nav.json").write_text(json.dumps({"version": 1, "entries": [
        {"id": "app.cart", "labelKey": "app.nav.cart", "icon": "cart", "path": "/cart",
         "menuVisibleDefault": True}]}))
    pages = root / "frontend" / "src" / "app.pages.ts"
    pages.write_text(pages.read_text().replace(
        '  // "app.cart": CartPage,', '  "app.cart": CartPage,').replace(
        'import type { ComponentType } from "react";',
        'import type { ComponentType } from "react";\nimport { CartPage } from "./pages/CartPage.js";'))
    before_pages = pages.read_text()
    written = nav_cli.regenerate(root)
    assert "frontend/src/routes.tsx" in written
    assert pages.read_text() == before_pages
    routes = (root / "frontend" / "src" / "routes.tsx").read_text()
    assert '{ path: "/cart", element: <AppPage id="app.cart" /> }' in routes
    echo = json.loads((root / "frontend" / "stapel.composition.json").read_text())
    assert echo["navigation"]["resolved"]["reach"]["/cart"] == "menu:app.cart"
    assert "app.cart" not in echo["navigation"]["resolved"]["pending"]
    again = nav_cli.regenerate(root)
    assert again == written


def test_composition_file_drives_the_scaffold(tmp_path):
    spec = tmp_path / "composition.json"
    spec.write_text(json.dumps({"modules": ["auth", "notifications"],
                                "navigation": {"preset": "shop"}}))
    result = _create(tmp_path, "--composition", str(spec))
    assert result.returncode == 0, result.stderr
    echo = json.loads((tmp_path / "navapp" / "frontend" / "stapel.composition.json").read_text())
    assert echo["navigation"]["resolved"]["preset"] == "shop"
    # a flag on top wins
    result = _create(tmp_path, "--composition", str(spec), "--nav-preset", "dashboard",
                     name="navapp2")
    assert result.returncode == 0, result.stderr
    echo = json.loads((tmp_path / "navapp2" / "frontend" / "stapel.composition.json").read_text())
    assert echo["navigation"]["resolved"]["preset"] == "dashboard"


def test_nav_plan_prints_resolved_without_writing(capsys):
    assert nav_cli.plan_main(["--modules", "shop", "--mobile", "kmp"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["react"]["preset"] == "shop" and out["react"]["chrome"] == "PublicShell"
    assert out["kmp"]["chrome"] == "AdaptiveTabs"


def test_nav_plan_refusal_is_exit_1(capsys):
    assert nav_cli.plan_main(["--modules", "shop", "--nav", "expanded=drawer"]) == 1
    assert "NAV-E01" in capsys.readouterr().err


# --- the split repository ------------------------------------------------


def _repo_init(tmp_path, *flags):
    from stapel_tools import frontend_repo_init

    return frontend_repo_init.main([str(tmp_path / "web"), "--surface", "public",
                                    "--ci", "none", "--title", "Web", *flags])


def test_split_repo_shop_and_dashboard(tmp_path):
    assert _repo_init(tmp_path, "--preset", "shop") == 0
    web = tmp_path / "web"
    doc = json.loads((web / "stapel.composition.json").read_text())
    assert doc["navigation"]["resolved"]["chrome"] == "PublicShell"
    assert 'id: "stapel-shell"' in (web / "src" / "routes.tsx").read_text()
    assert (web / "src" / "__tests__" / "nav.test.ts").exists()
    other = tmp_path / "web2"
    from stapel_tools import frontend_repo_init

    assert frontend_repo_init.main([str(other), "--surface", "public", "--ci", "none",
                                    "--preset", "shop", "--nav-preset", "dashboard"]) == 0
    doc = json.loads((other / "stapel.composition.json").read_text())
    assert doc["navigation"]["resolved"]["chrome"] == "AppShell"
    assert "<AppShell" in (other / "src" / "AppChrome.tsx").read_text()
    assert "./AppChrome.js" in (other / "src" / "routes.tsx").read_text()


def test_split_repo_refusal(tmp_path):
    with pytest.raises(SystemExit) as exc:
        _repo_init(tmp_path, "--preset", "shop", "--nav", "compact=rail")
    assert "NAV-E01" in str(exc.value)


# --- mobile echo ----------------------------------------------------------


def test_mobile_echoes_the_navigation_block(tmp_path):
    result = _create(tmp_path, "--modules", "shop", "--mobile", "kmp",
                     "--mobile-maven-repo", "local")
    assert result.returncode == 0, result.stderr
    echo = json.loads((tmp_path / "navapp" / "mobile" / "stapel.composition.json").read_text())
    assert echo["navigation"]["resolved"]["chrome"] == "AdaptiveTabs"
    assert echo["shell"]["template"] == "AdaptiveTabs"
    root = json.loads((tmp_path / "navapp" / "stapel.composition.json").read_text())
    assert set(root["resolved"]) == {"react", "kmp"}


def test_mobile_carrier_not_drawn_yet_is_e08(tmp_path):
    result = _create(tmp_path, "--modules", "shop", "--mobile", "kmp",
                     "--mobile-maven-repo", "local", "--mobile-nav", "compact=island")
    assert result.returncode == 1 and "NAV-E08" in result.stderr
    assert not (tmp_path / "navapp").exists()
