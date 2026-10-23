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


def _run(tmp_path: Path, fe: Path, criteria: dict) -> tuple[int, str, dict | None]:
    ts = _needs_node()
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps(ui_wiring.manifest("T-003-frontend", criteria)))
    proc = subprocess.run(["node", str(SCRIPT), "--frontend", str(fe), str(mf)],
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
    assert ui_wiring.script_source() == SCRIPT.read_text()
    assert "UI_WIRING_RESULT" in ui_wiring.script_source()


def test_the_monolith_scaffold_writes_the_script_and_the_make_target(tmp_path):
    from stapel_tools.create_project import create_project

    create_project(name="fe", project_type="monolith", title="Fe", url="https://fe.example",
                   company_name="Acme", company_email="hi@fe.example", modules=["core"],
                   output_dir=tmp_path, use_submodules=False, init_git=False)
    root = tmp_path / "fe"
    assert (root / ui_wiring.SCRIPT_PATH).read_text() == SCRIPT.read_text()
    make = (root / "Makefile").read_text()
    assert "\nfrontend-wiring:\n\tnode scripts/ui-wiring.mjs --frontend frontend $(MANIFEST)\n" in make
    assert "MANIFEST ?= contracts/ui" in make


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
    assert "FAIL C5 /parts/<id> action add-to-cart request: no POST /orders/v1/cart/items/" in out
    assert result["ok"] is False and result["criteria"][0]["id"] == "C5"
    assert "PASS C5 /parts/<id> testid add-to-cart: src/catalog/PartPage.tsx:6" in out


def test_the_wired_button_is_green(tmp_path):
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "api/orders.ts": API_ORDERS,
                              "catalog/PartPage.tsx": WIRED_PART})
    rc, out, result = _run(tmp_path, fe, ADD_TO_CART)
    assert rc == 0, out
    assert result == {"ok": True, "checks": 6, "failed": 0, "criteria": [
        {"task": "T-003-frontend", "id": "C5", "ok": True, "failed": []}]}
    assert "POST /orders/v1/cart/items/ in src/api/orders.ts" in out


def test_a_get_path_never_stands_in_for_a_longer_one(tmp_path):
    api = f'export const getCart = () => fetch("{API}/orders/v1/cart/", {{ method: "POST" }});\n'
    part = WIRED_PART.replace("addToCart(1)", "getCart()").replace("addToCart", "getCart")
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "api/orders.ts": api,
                              "catalog/PartPage.tsx": part})
    rc, out, _ = _run(tmp_path, fe, ADD_TO_CART)
    assert rc == 1
    assert "action add-to-cart request: no POST /orders/v1/cart/items/" in out


def test_the_method_must_be_there_too(tmp_path):
    api = API_ORDERS.replace('method: "POST", ', "")
    fe = _frontend(tmp_path, {"routes.tsx": ROUTES, "api/orders.ts": api,
                              "catalog/PartPage.tsx": WIRED_PART})
    rc, out, _ = _run(tmp_path, fe, ADD_TO_CART)
    assert rc == 1 and "no POST /orders/v1/cart/items/" in out


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
