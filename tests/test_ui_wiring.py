"""Static UI wiring check (VERDICT-D10 fork 1a): ``scripts/ui-wiring.mjs``.

Inversion: every test here is red on 0.73.0 (no module, no script, no make
target). The dead-button case is the defect a dogfood product shipped: an
"add to cart" button with no handler that never POSTs to the cart.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from stapel_tools import ui_wiring

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "stapel_tools" / "data" / "ui-wiring.mjs"
API = "/acme/api"


def _typescript() -> str | None:
    env = os.environ.get("UI_WIRING_TYPESCRIPT")
    if env and Path(env).is_dir():
        return env
    sibling = ROOT.parent / "stapel-react" / "node_modules" / "typescript"
    return str(sibling) if sibling.is_dir() else None


def _needs_node():
    ts = _typescript()
    if shutil.which("node") and ts:
        return ts
    reason = "node or typescript unavailable (set UI_WIRING_TYPESCRIPT)"
    if os.environ.get("CI"):
        pytest.fail(reason)  # a skipped gate is red on the runner
    pytest.skip(reason)


def _frontend(tmp_path: Path, files: dict[str, str]) -> Path:
    fe = tmp_path / "frontend"
    for rel, text in files.items():
        p = fe / "src" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (fe / "package.json").write_text('{"name": "fe", "private": true}\n')
    return fe


def _run(tmp_path: Path, fe: Path, criteria: dict, *, api_base: str = "") -> tuple[int, str, dict | None]:
    ts = _needs_node()
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps(ui_wiring.manifest("T-003-frontend", criteria)))
    extra = ["--api-base", api_base] if api_base else []
    proc = subprocess.run(["node", str(SCRIPT), "--frontend", str(fe), *extra, str(mf)],
                          capture_output=True, text=True,
                          env={**os.environ, "UI_WIRING_TYPESCRIPT": ts})
    out = proc.stdout + proc.stderr
    return proc.returncode, out, ui_wiring.parse_result(out)


ROUTES = """export const routes = [
  { path: "/parts/:id", element: null },
  { path: "/checkout", element: null },
];
"""

API_ORDERS = f"""export async function addToCart(part: number): Promise<void> {{
  await fetch(`${{BASE}}/orders/v1/cart/items/`, {{ method: "POST", body: JSON.stringify({{ part }}) }});
}}
export async function getCart(): Promise<unknown> {{
  return fetch("{API}/orders/v1/cart/");
}}
const BASE = "{API}";
"""

DEAD_PART = """import { Button } from "antd";
export function PartPage() {
  return (
    <main data-testid="page-part">
      <h1>Part</h1>
      <Button type="primary" data-testid="add-to-cart">Add</Button>
    </main>
  );
}
"""

WIRED_PART = """import { Button } from "antd";
import { addToCart } from "../api/orders.js";
export function PartPage() {
  return (
    <main data-testid="page-part">
      <h1>Part</h1>
      <Button type="primary" data-testid="add-to-cart" onClick={() => addToCart(1)}>Add</Button>
    </main>
  );
}
"""

ADD_TO_CART = {"C5": {"pages": [{
    "route": "/parts/<id>", "h1": True, "testids": ["page-part", "add-to-cart"],
    "actions": [{"testid": "add-to-cart", "handler": "click",
                 "request": {"method": "POST", "path": f"{API}/orders/v1/cart/items/"}}],
}]}}


# packaging and the scaffold ─────────────────────────────────────────────────


def test_the_script_ships_in_the_package():
    shipped = ui_wiring.script_source()
    assert "UI_WIRING_RESULT" in shipped
    stamp = shipped.splitlines()[1]
    assert stamp.startswith("// stapel-tools ") and " ui-wiring protocol 2 sha256 " in stamp
    assert shipped.replace(stamp + "\n", "", 1) == SCRIPT.read_text()


def test_the_monolith_scaffold_writes_the_script_and_the_make_target(tmp_path):
    from stapel_tools.create_project import create_project

    create_project(name="fe", project_type="monolith", title="Fe", url="https://fe.example",
                   company_name="Acme", company_email="hi@fe.example", modules=["core"],
                   output_dir=tmp_path, use_submodules=False, init_git=False)
    root = tmp_path / "fe"
    assert (root / ui_wiring.SCRIPT_PATH).read_text() == ui_wiring.script_source()
    make = (root / "Makefile").read_text()
    assert ("\nfrontend-wiring:\n\tnode scripts/ui-wiring.mjs --frontend frontend "
            '--api-base "$(API_BASE)" $(MANIFEST)\n') in make
    assert "MANIFEST ?= contracts/ui" in make
    assert "\nAPI_BASE ?= /fe/api\n" in make


def test_parse_result_reads_the_last_tagged_line():
    out = "PASS x\nUI_WIRING_RESULT {\"ok\": false, \"checks\": 3, \"failed\": 1}\n"
    assert ui_wiring.parse_result(out) == {"ok": False, "checks": 3, "failed": 1}
    assert ui_wiring.parse_result("make: *** [frontend-wiring] Error 2") is None


# the check ──────────────────────────────────────────────────────────────────


def test_a_dead_button_is_red_with_a_pointer_to_it(tmp_path):
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "api/orders.ts": API_ORDERS,
                              "catalog/PartPage.tsx": DEAD_PART})
    rc, out, result = _run(tmp_path, fe, ADD_TO_CART)
    assert rc == 1, out
    assert ("FAIL C5 /parts/<id> action add-to-cart click: <Button> at "
            "src/catalog/PartPage.tsx:6 has no click handler") in out
    assert ("FAIL C5 /parts/<id> action add-to-cart request: no POST "
            "/acme/api/orders/v1/cart/items/") in out
    assert result["ok"] is False and result["criteria"][0]["id"] == "C5"
    assert "PASS C5 /parts/<id> testid add-to-cart: src/catalog/PartPage.tsx:6" in out


def test_the_wired_button_is_green(tmp_path):
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "api/orders.ts": API_ORDERS,
                              "catalog/PartPage.tsx": WIRED_PART})
    rc, out, result = _run(tmp_path, fe, ADD_TO_CART)
    assert rc == 0, out
    assert result == {"ok": True, "checks": 7, "failed": 0, "unverifiable": 0, "protocol": 2,
                      "criteria": [{"task": "T-003-frontend", "id": "C5", "ok": True,
                                    "failed": [], "unverifiable": []}]}
    assert "POST /acme/api/orders/v1/cart/items/ in src/api/orders.ts:2" in out
    assert "PASS C5 /parts/<id> base: 3 API path(s) under /acme/api" in out


def test_a_get_path_never_stands_in_for_a_longer_one(tmp_path):
    api = f'export const getCart = () => fetch("{API}/orders/v1/cart/", {{ method: "POST" }});\n'
    part = WIRED_PART.replace("addToCart(1)", "getCart()").replace("addToCart", "getCart")
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "api/orders.ts": api,
                              "catalog/PartPage.tsx": part})
    rc, out, _ = _run(tmp_path, fe, ADD_TO_CART)
    assert rc == 1
    assert "action add-to-cart request: no POST /acme/api/orders/v1/cart/items/" in out


def test_the_method_must_be_there_too(tmp_path):
    api = API_ORDERS.replace('method: "POST", ', "")
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "api/orders.ts": api,
                              "catalog/PartPage.tsx": WIRED_PART})
    rc, out, _ = _run(tmp_path, fe, ADD_TO_CART)
    assert rc == 1 and "no POST /acme/api/orders/v1/cart/items/" in out


def test_a_submit_button_inside_a_form_on_finish_is_wired(tmp_path):
    page = f"""import {{ Button, Form }} from "antd";
const submit = (v: unknown) => fetch("{API}/orders/v1/checkout/", {{ method: "POST", body: JSON.stringify(v) }});
export function CheckoutPage() {{
  return (
    <main data-testid="page-checkout">
      <h1>Checkout</h1>
      <Form onFinish={{submit}}>
        <Button htmlType="submit" data-testid="checkout-submit">Order</Button>
      </Form>
    </main>
  );
}}
"""
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "checkout/CheckoutPage.tsx": page})
    rc, out, _ = _run(tmp_path, fe, {"C5": {"pages": [{
        "route": "/checkout", "h1": True, "testids": ["page-checkout", "checkout-submit"],
        "actions": [{"testid": "checkout-submit", "handler": "click",
                     "request": {"method": "POST", "path": f"{API}/orders/v1/checkout/"}}]}]}})
    assert rc == 0, out
    assert "<Button> Form onFinish at src/checkout/CheckoutPage.tsx:8" in out


def test_missing_ambiguous_testids_and_a_missing_h1_are_red(tmp_path):
    fe = _frontend(tmp_path, {
        "routes.tsx": ROUTES,
        "a/One.tsx": 'export const One = () => <div data-testid="part-card" />;\n',
        "b/Two.tsx": 'export const Two = () => <div data-testid="part-card" />;\n',
        "c/Page.tsx": 'export const P = () => <main data-testid="page-cart"><h2>x</h2></main>;\n',
        "c/Page.test.tsx": 'export const T = () => <div data-testid="only-in-tests" />;\n',
    })
    rc, out, result = _run(tmp_path, fe, {"C7": {"pages": [
        {"route": "/cart", "h1": True, "testids": ["page-cart", "part-card", "only-in-tests"]}]}})
    assert rc == 1
    assert "FAIL C7 /cart route: no route object or <Route> declares this path" in out
    assert "FAIL C7 /cart testid part-card: ambiguous, in 2 modules" in out
    assert "FAIL C7 /cart testid only-in-tests: rendered by no module under src/" in out
    assert "FAIL C7 /cart h1: src/c/Page.tsx renders no <h1> or level-1 Title" in out
    assert result["failed"] == 4


def test_a_nav_entry_is_not_a_route(tmp_path):
    fe = _frontend(tmp_path, {
        "nav.generated.ts": 'export const nav = [{ path: "/orders", label: "Orders" }];\n',
        "orders/Page.tsx": 'export const P = () => <main data-testid="page-orders"><h1>x</h1></main>;\n',
    })
    rc, out, _ = _run(tmp_path, fe, {"C5": {"pages": [
        {"route": "/orders", "h1": True, "testids": ["page-orders"]}]}})
    assert rc == 1
    assert "FAIL C5 /orders route: no route object or <Route> declares this path" in out
    (fe / "src" / "routes.tsx").write_text(
        'import { Route } from "react-router";\n'
        'export const r = <Route path="/orders" element={null} />;\n')
    rc, out, _ = _run(tmp_path, fe, {"C5": {"pages": [
        {"route": "/orders", "h1": True, "testids": ["page-orders"]}]}})
    assert rc == 0, out


def test_an_empty_manifest_cannot_run(tmp_path):
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES})
    rc, out, result = _run(tmp_path, fe, {"C1": {"pages": []}})
    assert rc == 2
    assert "checked 0" in out and result["checks"] == 0 and result["ok"] is False


def test_the_make_target_runs_the_script_on_a_generated_project(tmp_path):
    ts = _needs_node()
    if not shutil.which("make"):
        pytest.skip("make unavailable")
    root = tmp_path / "proj"
    (root / "scripts").mkdir(parents=True)
    (root / ui_wiring.SCRIPT_PATH).write_text(ui_wiring.script_source())
    (root / "Makefile").write_text("all:\n\t@true\n" + ui_wiring.MAKEFILE_BLOCK)
    _frontend(root, {"routes.tsx": ROUTES, "api/orders.ts": API_ORDERS,
                     "catalog/PartPage.tsx": DEAD_PART})
    (root / "contracts" / "ui").mkdir(parents=True)
    (root / "contracts" / "ui" / "T-003-frontend.json").write_text(
        json.dumps(ui_wiring.manifest("T-003-frontend", ADD_TO_CART)))
    proc = subprocess.run(["make", "-s", "frontend-wiring",
                           "MANIFEST=contracts/ui/T-003-frontend.json"],
                          cwd=root, capture_output=True, text=True,
                          env={**os.environ, "UI_WIRING_TYPESCRIPT": ts})
    assert proc.returncode != 0
    result = ui_wiring.parse_result(proc.stdout)
    assert result and result["ok"] is False and result["failed"] == 2


# protocol 2: the full request path, its base, and what cannot be resolved ────
#
# Inversion: every test below is red on 0.74.0 (e2144a7), which matched the
# path after its /api segment only and printed PASS/FAIL.

WRONG_BASE_ORDERS = API_ORDERS.replace(f'"{API}"', '"/api"')


def test_a_request_under_the_wrong_base_is_red(tmp_path):
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "api/orders.ts": WRONG_BASE_ORDERS,
                              "catalog/PartPage.tsx": WIRED_PART})
    rc, out, result = _run(tmp_path, fe, ADD_TO_CART)
    assert rc == 1, out
    assert ("FAIL C5 /parts/<id> action add-to-cart request: POST /orders/v1/cart/items/ at "
            "src/api/orders.ts:2 is under /api, not the canon /acme/api") in out
    assert "FAIL C5 /parts/<id> base: 2 API path(s) outside the canon /acme/api" in out
    assert result["failed"] == 2


def test_the_api_base_comes_from_the_command_line_too(tmp_path):
    page = WIRED_PART.replace("addToCart(1)", "void 0").replace(
        'import { addToCart } from "../api/orders.js";\n', 'import { getCart } from "../api/orders.js";\n'
        "void getCart;\n")
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "api/orders.ts": WRONG_BASE_ORDERS,
                              "catalog/PartPage.tsx": page})
    criteria = {"C5": {"pages": [{"route": "/parts/<id>", "testids": ["page-part"]}]}}
    rc, out, _ = _run(tmp_path, fe, criteria)
    assert rc == 0 and "base:" not in out, out  # no canon named anywhere: not checked
    rc, out, _ = _run(tmp_path, fe, criteria, api_base="/acme/api")
    assert rc == 1 and "FAIL C5 /parts/<id> base: 2 API path(s) outside the canon /acme/api" in out


def test_a_concatenated_path_is_evaluated(tmp_path):
    api = (f'const ROOT = "{API}";\n'
           'export const addToCart = (part: number) =>\n'
           '  fetch(ROOT + "/orders/v1/cart" + "/items/", { method: "POST", body: String(part) });\n')
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "api/orders.ts": api,
                              "catalog/PartPage.tsx": WIRED_PART})
    rc, out, _ = _run(tmp_path, fe, ADD_TO_CART)
    assert rc == 0, out
    assert "PASS C5 /parts/<id> action add-to-cart request: POST /acme/api/orders/v1/cart/items/" in out


def test_a_helper_that_prefixes_its_parameter_supplies_the_base(tmp_path):
    client = ('const BASE = import.meta.env.VITE_API_BASE ?? "/acme/api";\n'
              "export function request(path: string, init?: RequestInit) {\n"
              "  return fetch(`${BASE}${path}`, init);\n}\n")
    api = ('import { request } from "./client.js";\n'
           'export const addToCart = (part: number) =>\n'
           '  request("/orders/v1/cart/items/", { method: "POST", body: String(part) });\n')
    files = {"routes.tsx": ROUTES, "api/client.ts": client, "api/orders.ts": api,
             "catalog/PartPage.tsx": WIRED_PART}
    fe = _frontend(tmp_path, files)
    rc, out, _ = _run(tmp_path, fe, ADD_TO_CART)
    assert rc == 0, out
    assert "request: POST /acme/api/orders/v1/cart/items/ in src/api/orders.ts:3" in out
    # The env file wins over the default, as in the production build.
    (fe / ".env.production").write_text("VITE_API_BASE=/api\n")
    rc, out, _ = _run(tmp_path, fe, ADD_TO_CART)
    assert rc == 1 and "is under /api, not the canon /acme/api" in out


def test_a_client_created_with_a_base_url_supplies_the_base(tmp_path):
    api = ('import axios from "axios";\n'
           'const http = axios.create({ baseURL: "/api" });\n'
           'export const addToCart = (part: number) => http.post("/orders/v1/cart/items/", { part });\n')
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "api/orders.ts": api,
                              "catalog/PartPage.tsx": WIRED_PART})
    rc, out, _ = _run(tmp_path, fe, ADD_TO_CART)
    assert rc == 1, out
    assert "src/api/orders.ts:3 is under /api, not the canon /acme/api" in out
    (fe / "src" / "api" / "orders.ts").write_text(api.replace('"/api"', f'"{API}"'))
    rc, out, _ = _run(tmp_path, fe, ADD_TO_CART)
    assert rc == 0, out


def test_a_runtime_base_is_unverifiable_never_green(tmp_path):
    api = ("const BASE = (window as unknown as { cfg: { api: string } }).cfg.api;\n"
           "export const addToCart = (part: number) =>\n"
           '  fetch(`${BASE}/orders/v1/cart/items/`, { method: "POST", body: String(part) });\n')
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "api/orders.ts": api,
                              "catalog/PartPage.tsx": WIRED_PART})
    rc, out, result = _run(tmp_path, fe, ADD_TO_CART)
    assert rc == 3, out
    assert "UNVERIFIABLE C5 /parts/<id> action add-to-cart request: src/api/orders.ts:3 builds" in out
    assert result["ok"] is False and result["failed"] == 0 and result["unverifiable"] == 1
    assert result["criteria"][0]["unverifiable"][0].startswith("/parts/<id> action add-to-cart request")


ADD_BUTTON = """import { Button } from "antd";
export function AddButton({ onAdd }: { onAdd: () => void }) {
  return <Button data-testid="add-to-cart" onClick={onAdd}>Add</Button>;
}
"""

PROP_PART = """import { AddButton } from "./AddButton.js";
import { addToCart } from "../api/orders.js";
export function PartPage() {
  return (
    <main data-testid="page-part">
      <h1>Part</h1>
      <AddButton onAdd={() => addToCart(1)} />
    </main>
  );
}
"""


def test_a_handler_prop_is_followed_to_the_component_that_passes_it(tmp_path):
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "api/orders.ts": API_ORDERS,
                              "catalog/AddButton.tsx": ADD_BUTTON, "catalog/PartPage.tsx": PROP_PART})
    rc, out, _ = _run(tmp_path, fe, ADD_TO_CART)
    assert rc == 0, out
    assert ("PASS C5 /parts/<id> action add-to-cart click: <Button> onClick = prop onAdd, "
            "passed by <AddButton> at src/catalog/PartPage.tsx:7") in out
    assert "request: POST /acme/api/orders/v1/cart/items/ in src/api/orders.ts:2" in out


def test_a_handler_prop_nobody_passes_is_red(tmp_path):
    part = PROP_PART.replace(" onAdd={() => addToCart(1)}", "")
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "api/orders.ts": API_ORDERS,
                              "catalog/AddButton.tsx": ADD_BUTTON, "catalog/PartPage.tsx": part})
    rc, out, _ = _run(tmp_path, fe, ADD_TO_CART)
    assert rc == 1, out
    assert ("FAIL C5 /parts/<id> action add-to-cart click: <Button> at src/catalog/AddButton.tsx:3: "
            "onClick is prop onAdd of AddButton, and no <AddButton> passes it "
            "(src/catalog/PartPage.tsx:7)") in out


def test_a_handler_from_spread_props_is_unverifiable(tmp_path):
    part = """import { Button } from "antd";
export function PartPage(props: Record<string, unknown>) {
  return (
    <main data-testid="page-part">
      <h1>Part</h1>
      <Button data-testid="add-to-cart" {...props}>Add</Button>
    </main>
  );
}
"""
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "catalog/PartPage.tsx": part})
    criteria = {"C5": {"pages": [{"route": "/parts/<id>", "testids": ["page-part", "add-to-cart"],
                                  "actions": [{"testid": "add-to-cart", "handler": "click"}]}]}}
    rc, out, _ = _run(tmp_path, fe, criteria)
    assert rc == 3, out
    assert "UNVERIFIABLE C5 /parts/<id> action add-to-cart click: <Button> at src/catalog/PartPage.tsx:6" in out


LOGIN = """import { PasswordlessLogin } from "@stapel/auth-react";
export function LoginPage() {
  return (
    <main data-testid="page-login">
      <h1>Sign in</h1>
      <PasswordlessLogin>{(bag) => <button data-testid="login-send" onClick={() => bag.requestCode()}>Send</button>}</PasswordlessLogin>
    </main>
  );
}
"""

LOGIN_CRITERIA = {"C4": {"pages": [{
    "route": "/login", "testids": ["page-login", "login-send"],
    "actions": [{"testid": "login-send", "handler": "click",
                 "request": {"method": "POST", "path": "/auth/api/v1/phone/request/"}}]}]}}


def _pair(fe: Path, operations: dict | None) -> None:
    pkg = fe / "node_modules" / "@stapel" / "auth-react"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text('{"name": "@stapel/auth-react"}\n')
    if operations is not None:
        (pkg / "manifest.json").write_text(json.dumps({"operations": operations}))


def test_a_request_a_pair_declares_passes_through_its_manifest(tmp_path):
    routes = 'export const routes = [{ path: "/login", element: null }];\n'
    fe = _frontend(tmp_path, {"routes.tsx": routes, "auth/LoginPage.tsx": LOGIN})
    _pair(fe, {"auth_api_v1_phone_request_create": {"method": "POST", "path": "/auth/api/v1/phone/request/"}})
    rc, out, _ = _run(tmp_path, fe, LOGIN_CRITERIA)
    assert rc == 0, out
    assert ("PASS C4 /login action login-send request: POST /auth/api/v1/phone/request/ declared by "
            "@stapel/auth-react (operation auth_api_v1_phone_request_create), issued inside the pair") in out


def test_a_pair_that_does_not_declare_the_request_is_red(tmp_path):
    routes = 'export const routes = [{ path: "/login", element: null }];\n'
    fe = _frontend(tmp_path, {"routes.tsx": routes, "auth/LoginPage.tsx": LOGIN})
    _pair(fe, {"auth_api_v1_email_request_create": {"method": "POST", "path": "/auth/api/v1/email/request/"}})
    rc, out, _ = _run(tmp_path, fe, LOGIN_CRITERIA)
    assert rc == 1, out
    assert "not declared by @stapel/auth-react" in out


def test_a_pair_without_a_manifest_is_unverifiable(tmp_path):
    routes = 'export const routes = [{ path: "/login", element: null }];\n'
    fe = _frontend(tmp_path, {"routes.tsx": routes, "auth/LoginPage.tsx": LOGIN})
    _pair(fe, None)
    rc, out, _ = _run(tmp_path, fe, LOGIN_CRITERIA)
    assert rc == 3, out
    assert "@stapel/auth-react may issue it and ships no manifest.json" in out


def test_a_missing_action_testid_still_gets_its_request_checked_from_the_page(tmp_path):
    page = """import { checkout } from "../api/orders.js";
export function CheckoutPage() {
  return <main data-testid="page-checkout"><h1>Checkout</h1><form onSubmit={() => checkout()} /></main>;
}
"""
    api = 'export const checkout = () => fetch("/api/orders/v1/checkout/", { method: "POST" });\n'
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "api/orders.ts": api, "checkout/CheckoutPage.tsx": page})
    rc, out, _ = _run(tmp_path, fe, {"C5": {"pages": [{
        "route": "/checkout", "testids": ["page-checkout"],
        "actions": [{"testid": "checkout-submit", "handler": "click",
                     "request": {"method": "POST", "path": f"{API}/orders/v1/checkout/"}}]}]}})
    assert rc == 1
    assert "FAIL C5 /checkout action checkout-submit: testid rendered by no module under src/" in out
    assert ("FAIL C5 /checkout action checkout-submit request: POST /orders/v1/checkout/ at "
            "src/api/orders.ts:1 is under /api, not the canon /acme/api") in out


# where the script is written ────────────────────────────────────────────────


def test_the_minimal_scaffold_writes_the_script_and_says_it_has_no_frontend(tmp_path):
    from stapel_tools.create_project import create_project

    create_project(name="mini", project_type="minimal", title="Mini", url="https://mini.example",
                   company_name="Acme", company_email="hi@mini.example", modules=["core"],
                   output_dir=tmp_path, use_submodules=False, init_git=False)
    root = tmp_path / "mini"
    assert (root / ui_wiring.SCRIPT_PATH).read_text() == ui_wiring.script_source()
    make = (root / "Makefile").read_text()
    assert "\nAPI_BASE ?= /api\n" in make and "--frontend frontend" in make
    if not shutil.which("make") or not shutil.which("node"):
        pytest.skip("make or node unavailable")
    (root / "contracts" / "ui").mkdir(parents=True)
    (root / "contracts" / "ui" / "T-1.json").write_text(
        json.dumps(ui_wiring.manifest("T-1", {"C1": {"pages": [{"testids": ["x"]}]}})))
    proc = subprocess.run(["make", "-s", "frontend-wiring"], cwd=root, capture_output=True, text=True)
    assert proc.returncode != 0
    result = ui_wiring.parse_result(proc.stdout)
    assert result["checks"] == 0 and "no frontend at" in result["error"]


def test_the_public_surface_repo_gets_the_script_and_its_own_target(tmp_path):
    from stapel_tools.frontend_repo_init import write_public_surface

    repo = tmp_path / "fe-repo"
    write_public_surface(repo, name="fe-repo", title="Fe", pairs=["auth"], locale="en",
                         realtime=False, extra_prefixes=[])
    assert (repo / ui_wiring.SCRIPT_PATH).read_text() == ui_wiring.script_source()
    make = (repo / "Makefile").read_text()
    assert '\tnode scripts/ui-wiring.mjs --frontend . --api-base "$(API_BASE)" $(MANIFEST)\n' in make
    assert '"scripts/ui-wiring.mjs"' in (repo / "eslint.config.js").read_text()


# the backfill policy ────────────────────────────────────────────────────────


def test_the_stamp_identifies_a_pristine_script():
    stamp = ui_wiring.inspect_script(ui_wiring.script_source())
    assert stamp.protocol == ui_wiring.PROTOCOL == 2 and stamp.pristine
    edited = ui_wiring.script_source().replace("process.exit(failed ? 1", "process.exit(0 ? 1")
    assert ui_wiring.inspect_script(edited).pristine is False


def _v0_74_0() -> str | None:
    proc = subprocess.run(["git", "-C", str(ROOT), "show", "v0.74.0:stapel_tools/data/ui-wiring.mjs"],
                          capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def test_the_backfill_keeps_upgrades_or_refuses():
    assert ui_wiring.backfill_decision(None).action == "write"
    assert ui_wiring.backfill_decision(ui_wiring.script_source()).action == "keep"
    old = _v0_74_0()
    if old is not None:  # the tag is fetched in a full clone
        decision = ui_wiring.backfill_decision(old)
        assert decision.action == "upgrade" and decision.current.version == "0.74.0"
        assert "would answer green" in decision.reason
    edited = ui_wiring.script_source().replace("UNVERIFIABLE", "PASS")
    assert ui_wiring.backfill_decision(edited).action == "refuse"
    newer = ui_wiring.script_source().replace(" ui-wiring protocol 2 ", " ui-wiring protocol 9 ", 1)
    refused = ui_wiring.backfill_decision(newer)
    assert refused.action == "refuse" and refused.current.protocol == 9
