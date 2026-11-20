"""ui-wiring protocol 3: routes judged by the app's navigation plan.

A generated app mounts its home as ``{ index: true, element: <Home/> }``
inside the shell's pathless layout, sections with relative children, and the
project's own pages by id (``<AppPage id="app.x"/>``, bound in
``src/app.pages.ts``). Protocol 2 matched ``path`` literals only: it was red on
``/`` and green on an own page nobody bound.

Inversion: every test here is red on the protocol-2 script (0.86.1); run with
``UI_WIRING_SCRIPT_UNDER_TEST=<that script>`` to see it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_ui_wiring import ROOT, SCRIPT, _needs_node

from stapel_tools import ui_wiring

API = "/acme/api"


def _script() -> Path:
    return Path(os.environ.get("UI_WIRING_SCRIPT_UNDER_TEST") or SCRIPT)


def _run_script(script: Path, project: Path, manifest: dict, *extra: str
                ) -> tuple[int, str, dict | None]:
    ts = _needs_node()
    mf = project / "manifest.json"
    mf.write_text(json.dumps(manifest))
    proc = subprocess.run(["node", str(script), "--frontend", str(project / "frontend"), *extra,
                           str(mf)], capture_output=True, text=True,
                          env={**os.environ, "UI_WIRING_TYPESCRIPT": ts})
    out = proc.stdout + proc.stderr
    return proc.returncode, out, ui_wiring.parse_result(out)


def _manifest(criteria: dict) -> dict:
    return ui_wiring.manifest("T-002-frontend", criteria)


# a project in the generated shape ────────────────────────────────────────────

SHOP_ROUTES = """import { createBrowserRouter } from "react-router";
import type { RouteObject } from "react-router";
import { MemberGate } from "./MemberGate.js";
import { AppChrome } from "./chrome.generated.js";
import { LandingPage } from "./LandingPage.js";
import { AccountHome } from "./AccountHome.js";
import { AppPage } from "./AppPage.js";
import { NotFoundPage } from "./NotFoundPage.js";
import { AuthPanel } from "@stapel/auth-react/default";

export const routes: RouteObject[] = [
  {
    children: [
      {
        id: "stapel-shell",
        element: <AppChrome />,
        children: [
          { index: true, element: <LandingPage /> },
          { path: "/login", element: <AuthPanel /> },
          { path: "/catalog", element: <AppPage id="app.catalog" /> },
          { path: "/parts/:id", element: <AppPage id="app.part" /> },
          {
            element: <MemberGate />,
            children: [
              {
                path: "/account",
                children: [
                  { index: true, element: <AccountHome /> },
                  { path: "orders", element: <AppPage id="app.orders" /> },
                ],
              },
            ],
          },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
];

export const router = createBrowserRouter(routes);
"""

APP_PAGE = """import { APP_PAGES } from "./app.pages.js";
export function AppPage({ id }: { readonly id: string }) {
  const Page = APP_PAGES[id];
  return Page ? <Page /> : null;
}
"""

LANDING = """export function LandingPage() {
  return <main data-testid="page-landing"><h1>Parts for your car</h1></main>;
}
"""

CATALOG = """export function CatalogPage() {
  return <main data-testid="page-catalog"><h1>Catalog</h1></main>;
}
"""

PART = """import { Button } from "antd";
import { addToCart } from "../api/orders.js";
export function PartPage() {
  return (
    <main data-testid="page-part">
      <h1>Part</h1>
      <Button data-testid="add-to-cart" onClick={() => addToCart(1)}>Add</Button>
    </main>
  );
}
"""

ORDERS_API = f"""export const addToCart = (part: number) =>
  fetch("{API}/orders/v1/cart/items/", {{ method: "POST", body: String(part) }});
"""

PAGES = """/**
 * The project's own pages.
 */
import type { ComponentType } from "react";
import { CatalogPage } from "./pages/CatalogPage.js";
import { PartPage } from "./pages/PartPage.js";

export const APP_PAGES: Readonly<Record<string, ComponentType>> = {
  // "app.cart": CartPage,
  "app.catalog": CatalogPage,
  "app.part": PartPage,
};
"""

APP_NAV = [
    {"id": "app.catalog", "labelKey": "app.catalog.label", "icon": "apps", "level": "top",
     "path": "/catalog", "surface": "public", "menuVisibleDefault": True, "order": 10},
    {"id": "app.part", "labelKey": "app.part.label", "icon": "apps", "level": "top",
     "path": "/parts/:id", "surface": "public", "menuVisibleDefault": False, "order": 50,
     "via": "app.catalog"},
    {"id": "app.orders", "labelKey": "app.orders.label", "icon": "apps", "level": "submenu",
     "parentId": "account.root", "path": "orders", "surface": "member", "order": 60},
]

PLAN_PATHS = {
    "auth.login": "/login",
    "app.catalog": "/catalog",
    "app.part": "/parts/:id",
    "account.root": "/account",
    "app.orders": "/account/orders",
}


def _project(tmp_path: Path, *, src: dict[str, str] | None = None, paths: dict | None = None,
             reach: dict | None = None, app_nav: list | None = None, plan: bool = True) -> Path:
    files = {
        "routes.tsx": SHOP_ROUTES, "AppPage.tsx": APP_PAGE, "LandingPage.tsx": LANDING,
        "AccountHome.tsx": 'export const AccountHome = () => <main data-testid="account-home" />;\n',
        "MemberGate.tsx": "export const MemberGate = () => null;\n",
        "NotFoundPage.tsx": "export const NotFoundPage = () => null;\n",
        "chrome.generated.tsx": "export const AppChrome = () => null;\n",
        "app.pages.ts": PAGES, "pages/CatalogPage.tsx": CATALOG, "pages/PartPage.tsx": PART,
        "api/orders.ts": ORDERS_API,
        **(src or {}),
    }
    fe = tmp_path / "frontend"
    for rel, text in files.items():
        if text is None:
            continue
        p = fe / "src" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (fe / "package.json").write_text('{"name": "fe", "private": true}\n')
    if plan:
        resolved = {"home": "shell.home", "signIn": "auth.login", "audiences": {"guest": {}},
                    "paths": PLAN_PATHS if paths is None else paths,
                    "reach": reach or {"/": "home", "/login": "signIn"}}
        (tmp_path / "stapel.composition.json").write_text(json.dumps(
            {"version": 1, "navigation": {}, "resolved": {"react": resolved}}))
    (tmp_path / "stapel.app-nav.json").write_text(json.dumps(
        {"version": 1, "entries": APP_NAV if app_nav is None else app_nav}))
    return tmp_path


def _run(project: Path, criteria: dict) -> tuple[int, str, dict | None]:
    return _run_script(_script(), project, _manifest(criteria), "--api-base", API)


HOME = {"C6": {"pages": [{"route": "/", "h1": True, "testids": ["page-landing"]}]}}


def test_the_home_index_route_inside_the_pathless_shell_is_mounted(tmp_path):
    rc, out, result = _run(_project(tmp_path), HOME)
    assert rc == 0, out
    assert ("PASS C6 / route: shell.home in the navigation plan, mounted at src/routes.tsx:18, "
            "page src/LandingPage.tsx") in out
    assert result["protocol"] == ui_wiring.PROTOCOL == 3 and result["routes"] == "plan"


def test_an_own_page_bound_in_app_pages_is_mounted_with_either_param_notation(tmp_path):
    criteria = {"C7": {"pages": [
        {"route": "/catalog", "h1": True, "testids": ["page-catalog"]},
        {"route": "/parts/<id>", "h1": True, "testids": ["page-part", "add-to-cart"],
         "actions": [{"testid": "add-to-cart", "handler": "click",
                      "request": {"method": "POST", "path": f"{API}/orders/v1/cart/items/"}}]},
    ]}}
    rc, out, result = _run(_project(tmp_path), criteria)
    assert rc == 0, out
    assert ("PASS C7 /parts/<id> route: app.part at /parts/:id in the navigation plan, mounted "
            "at src/routes.tsx:21, page src/pages/PartPage.tsx") in out
    assert "PASS C7 /catalog route: app.catalog in the navigation plan" in out
    assert result["criteria"][0]["stale"] == []


def test_an_unbound_own_page_is_red_with_the_binding_to_add(tmp_path):
    orders = {"C8": {"pages": [{"route": "/account/orders", "testids": ["page-orders"]}]}}
    project = _project(tmp_path, src={
        "orders/OrdersPage.tsx": 'export const OrdersPage = () => <main data-testid="page-orders" />;\n'})
    rc, out, result = _run(project, orders)
    assert rc == 1, out
    assert ("FAIL C8 /account/orders route: app.orders is mounted at /account/orders but has no "
            "page: bind app.orders in frontend/src/app.pages.ts (\"app.orders\": <PageComponent>, "
            "imported there)") in out
    assert result["failed"] == 1
    # Bound, it is green: the relative child of /account resolves.
    pages = PAGES.replace('  "app.part": PartPage,\n',
                          '  "app.part": PartPage,\n  "app.orders": OrdersPage,\n').replace(
        'import { PartPage } from "./pages/PartPage.js";\n',
        'import { PartPage } from "./pages/PartPage.js";\n'
        'import { OrdersPage } from "./orders/OrdersPage.js";\n')
    (project / "frontend" / "src" / "app.pages.ts").write_text(pages)
    rc, out, _ = _run(project, orders)
    assert rc == 0, out
    assert "page src/orders/OrdersPage.tsx" in out


def test_a_path_no_page_is_mounted_at_is_red_with_the_own_page_to_add(tmp_path):
    rc, out, result = _run(_project(tmp_path), {"C9": {"pages": [
        {"route": "/cart", "testids": ["page-catalog"]}]}})
    assert rc == 1, out
    assert ("FAIL C9 /cart route: no page of the app is mounted at /cart: add it as an own page "
            "in stapel.app-nav.json (id app.<screen>, path /cart) and bind it in "
            "frontend/src/app.pages.ts") in out
    assert result["criteria"][0]["stale"] == []


def test_a_declared_page_the_routes_were_not_regenerated_from(tmp_path):
    nav = [*APP_NAV, {"id": "app.cart", "labelKey": "app.cart.label", "icon": "cart",
                      "level": "top", "path": "/cart", "surface": "public"}]
    project = _project(tmp_path, app_nav=nav)
    criteria = {"C9": {"pages": [{"route": "/cart", "testids": ["page-catalog"]}]}}
    rc, out, result = _run(project, criteria)
    assert rc == 1, out
    assert ("FAIL C9 /cart route: app.cart is declared in stapel.app-nav.json but the generated "
            "routes do not mount /cart: bind app.cart in frontend/src/app.pages.ts "
            "(\"app.cart\": <PageComponent>) and run `stapel-nav-regenerate .` at the project "
            "root") in out
    assert result["criteria"][0]["stale"] == []
    (project / "frontend" / "src" / "app.pages.ts").write_text(
        PAGES.replace('  // "app.cart": CartPage,\n', '  "app.cart": CatalogPage,\n'))
    rc, out, result = _run(project, criteria)
    assert rc == 1, out
    line = ("/cart route: app.cart is declared in stapel.app-nav.json and bound in "
            "frontend/src/app.pages.ts, but the generated routes were not regenerated from it, "
            "so /cart is not mounted: run `stapel-nav-regenerate .` at the project root; the "
            "entry and its binding are right")
    assert f"FAIL C9 {line}" in out
    assert result["criteria"][0]["stale"] == [line]


DASHBOARD_ROUTES = """import { Navigate, Outlet } from "react-router";
import type { RouteObject } from "react-router";
import { AppChrome } from "./chrome.generated.js";
import { ProtectedRoute } from "./ProtectedRoute.js";
import { AppPage } from "./AppPage.js";

export const routes: RouteObject[] = [
  {
    id: "stapel-shell",
    element: <AppChrome />,
    children: [
      { index: true, element: <Navigate to="/app" replace /> },
      {
        path: "/app",
        element: (
          <ProtectedRoute>
            <Outlet />
          </ProtectedRoute>
        ),
        children: [
          { path: "account/orders", element: <ProtectedRoute><AppPage id="app.orders" /></ProtectedRoute> },
        ],
      },
    ],
  },
];
"""


def test_a_route_under_the_dashboard_section_root_and_its_home_redirect(tmp_path):
    pages = ('import { OrdersPage } from "./orders/index.js";\n'
             'export const APP_PAGES = { "app.orders": OrdersPage };\n')
    project = _project(tmp_path, src={
        "routes.tsx": DASHBOARD_ROUTES, "app.pages.ts": pages,
        "ProtectedRoute.tsx": "export const ProtectedRoute = ({ children }: { children: unknown }) => children;\n",
        "orders/index.ts": 'export { OrdersPage } from "./OrdersPage.js";\n',
        "orders/OrdersPage.tsx": ('import { OrdersTable } from "./OrdersTable.js";\n'
                                  "export function OrdersPage() {\n"
                                  "  return <section><h1>Orders</h1><OrdersTable /></section>;\n}\n"),
        "orders/OrdersTable.tsx": 'export const OrdersTable = () => <table data-testid="orders-table" />;\n',
        "LandingPage.tsx": None,
    }, paths={"app.orders": "/app/account/orders"}, reach={"/": "home", "/app": "section"})
    rc, out, _ = _run(project, {
        "C2": {"pages": [{"route": "/account/orders", "testids": ["orders-table"]}]},
        "C1": {"pages": [{"route": "/", "testids": ["orders-table"]}]},
    })
    assert ("PASS C2 /account/orders route: app.orders at /app/account/orders in the navigation "
            "plan, mounted at src/routes.tsx:21, page src/orders/OrdersPage.tsx") in out, out
    assert ("FAIL C1 / route: / (redirects to /app at src/routes.tsx:12) mounts no page") in out
    assert rc == 1


def test_a_root_testid_outside_the_mounted_page_is_red(tmp_path):
    project = _project(tmp_path, src={
        "LandingPage.tsx": "export function LandingPage() {\n  return <main><h1>Home</h1></main>;\n}\n",
        "home/Hero.tsx": 'export const Hero = () => <section data-testid="page-landing" />;\n',
    })
    rc, out, _ = _run(project, {"C6": {"pages": [{"route": "/", "testids": ["page-landing"]}]}})
    assert rc == 1, out
    assert ("FAIL C6 / route: the page at / is src/LandingPage.tsx (shell.home), but its root "
            "testid page-landing is rendered in src/home/Hero.tsx, which src/LandingPage.tsx does "
            "not import: render data-testid=\"page-landing\" in the page mounted at /") in out
    # Imported by the page, it is the page's.
    (project / "frontend" / "src" / "LandingPage.tsx").write_text(
        'import { Hero } from "./home/Hero.js";\n'
        "export function LandingPage() {\n  return <main><h1>Home</h1><Hero /></main>;\n}\n")
    rc, out, _ = _run(project, {"C6": {"pages": [{"route": "/", "testids": ["page-landing"]}]}})
    assert rc == 0, out


def test_a_real_page_defect_stays_red_under_the_plan(tmp_path):
    dead = LANDING.replace("<h1>Parts for your car</h1>",
                           '<h1>Parts</h1><button data-testid="home-search">Go</button>')
    rc, out, result = _run(_project(tmp_path, src={"LandingPage.tsx": dead}), {"C6": {"pages": [{
        "route": "/", "h1": True, "testids": ["page-landing", "home-search"],
        "actions": [{"testid": "home-search", "handler": "click"}]}]}})
    assert rc == 1, out
    assert "PASS C6 / route: shell.home in the navigation plan" in out
    assert ("FAIL C6 / action home-search click: <button> at src/LandingPage.tsx:2 has no click "
            "handler") in out
    assert result["failed"] == 1


def test_without_a_plan_the_route_literals_stand(tmp_path):
    rc, out, result = _run(_project(tmp_path, plan=False), HOME)
    assert rc == 1, out
    assert "FAIL C6 / route: no route object or <Route> declares this path" in out
    assert "no navigation plan: route literals)" in out
    assert result["routes"] == "literal"


# a generated shop ─────────────────────────────────────────────────────────────


def test_a_generated_shop_with_its_own_pages(tmp_path):
    """stapel-create-project's shop, two own pages declared and bound, one
    declared and not bound, regenerated; the project's own script judges it."""
    _needs_node()
    from stapel_tools.create_project import create_project
    from stapel_tools.nav_cli import regenerate_main

    create_project(name="shop", project_type="monolith", title="Shop", url="https://shop.example",
                   company_name="Acme", company_email="hi@shop.example", modules=["shop"],
                   output_dir=tmp_path, use_submodules=False, init_git=False)
    root = tmp_path / "shop"
    script = root / ui_wiring.SCRIPT_PATH
    assert script.read_text() == ui_wiring.script_source()
    if os.environ.get("UI_WIRING_SCRIPT_UNDER_TEST"):
        shutil.copyfile(_script(), script)
    (root / "stapel.app-nav.json").write_text(json.dumps({"version": 1, "entries": [
        {**APP_NAV[0], "id": "app.parts", "labelKey": "app.parts.label", "path": "/parts"},
        {**APP_NAV[1], "via": "app.parts"},
        APP_NAV[2],
    ]}))
    src = root / "frontend" / "src"
    (src / "pages").mkdir(exist_ok=True)
    (src / "pages" / "PartsPage.tsx").write_text(
        'export function PartsPage() {\n  return <main data-testid="page-parts"><h1>Parts</h1></main>;\n}\n')
    (src / "pages" / "PartPage.tsx").write_text(
        'export function PartPage() {\n  return <main data-testid="page-part"><p>Part</p></main>;\n}\n')
    (src / "app.pages.ts").write_text(
        'import type { ComponentType } from "react";\n'
        'import { PartsPage } from "./pages/PartsPage.js";\n'
        'import { PartPage } from "./pages/PartPage.js";\n'
        "export const APP_PAGES: Readonly<Record<string, ComponentType>> = {\n"
        '  "app.parts": PartsPage,\n  "app.part": PartPage,\n};\n')
    assert regenerate_main([str(root)]) == 0
    assert '<AppPage id="app.orders" />' in (src / "routes.tsx").read_text()
    rc, out, result = _run_script(script, root, _manifest({
        "C1": {"pages": [{"route": "/", "testids": ["storefront-home"]}]},
        "C2": {"pages": [{"route": "/parts", "h1": True, "testids": ["page-parts"]}]},
        "C3": {"pages": [{"route": "/parts/<id>", "h1": True, "testids": ["page-part"]}]},
        "C4": {"pages": [{"route": "/account/orders", "testids": ["page-parts"]}]},
        "C5": {"pages": [{"route": "/cart", "testids": ["page-parts"]}]},
        "C6": {"pages": [{"route": "/account/settings", "testids": ["page-parts"]}]},
    }))
    assert rc == 1, out
    by_id = {row["id"]: row for row in result["criteria"]}
    assert by_id["C1"]["ok"] and by_id["C2"]["ok"], out
    assert "page src/StorefrontHome.tsx" in out and "page src/pages/PartsPage.tsx" in out
    assert by_id["C3"]["failed"] == ["/parts/<id> h1: src/pages/PartPage.tsx renders no <h1> or "
                                     "level-1 Title"]
    assert by_id["C4"]["failed"] == [
        "/account/orders route: app.orders is mounted at /account/orders but has no page: bind "
        "app.orders in frontend/src/app.pages.ts (\"app.orders\": <PageComponent>, imported there)"]
    assert by_id["C5"]["failed"][0].startswith(
        "/cart route: no page of the app is mounted at /cart: add it as an own page")
    assert by_id["C6"]["ok"], out
    assert "a @stapel pair's screen (<ProfileSettings> from @stapel/profiles-react/default)" in out
    assert result["routes"] == "plan" and result["protocol"] == 3


# the result parser and the upgrade of a pristine protocol-2 script ─────────


def test_parse_result_reads_every_protocol_into_one_shape():
    p1 = ui_wiring.parse_result('UI_WIRING_RESULT {"ok": true, "checks": 2, "failed": 0, '
                                '"criteria": [{"task": "T", "id": "C1", "ok": true, "failed": []}]}')
    assert p1["protocol"] == 1 and p1["unverifiable"] == 0 and p1["routes"] == "literal"
    assert p1["criteria"][0]["unverifiable"] == [] and p1["criteria"][0]["stale"] == []
    p2 = ui_wiring.parse_result('UI_WIRING_RESULT {"ok": false, "checks": 2, "failed": 1, '
                                '"unverifiable": 0, "protocol": 2, "criteria": []}')
    assert p2["protocol"] == 2 and p2["routes"] == "literal"
    p3 = ui_wiring.parse_result(
        'UI_WIRING_RESULT {"ok": false, "checks": 1, "failed": 1, "unverifiable": 0, '
        '"protocol": 3, "routes": "plan", "criteria": [{"task": "T", "id": "C9", "ok": false, '
        '"failed": ["/cart route: x"], "unverifiable": [], "stale": ["/cart route: x"]}]}')
    assert p3["routes"] == "plan" and p3["criteria"][0]["stale"] == ["/cart route: x"]
    err = ui_wiring.parse_result('UI_WIRING_RESULT {"ok": false, "error": "e", "checks": 0, '
                                 '"protocol": 3}')
    assert err["error"] == "e" and err["checks"] == 0


def _released(tag: str) -> str | None:
    proc = subprocess.run(["git", "-C", str(ROOT), "show", f"{tag}:stapel_tools/data/ui-wiring.mjs"],
                          capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def test_a_pristine_protocol_2_script_is_upgraded():
    import hashlib

    raw = _released("v0.86.1")
    if raw is None:
        pytest.skip("tag v0.86.1 not fetched")
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert ui_wiring.RELEASED_SCRIPTS[sha] == ("0.75.0", 2)
    first, rest = raw.split("\n", 1)
    stamped = f"{first}\n// stapel-tools 0.86.1 ui-wiring protocol 2 sha256 {sha}\n{rest}"
    stamp = ui_wiring.inspect_script(stamped)
    assert stamp.pristine and stamp.protocol == 2 and stamp.version == "0.86.1"
    decision = ui_wiring.backfill_decision(stamped)
    assert decision.action == "upgrade", decision
    assert "routes through the navigation plan" in decision.reason
    assert "request base" not in decision.reason
