"""An unknown address renders the product's page, not the router's error.

The generated router had no catch-all, so ``/cart`` or a mistyped link showed
react-router's developer screen ("Unexpected Application Error! 404 ... Hey
developer") to visitors.
"""
import re

import pytest

from stapel_tools.create_project import create_project


def _front(tmp_path, modules):
    create_project(
        name="app", project_type="monolith", title="App", url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=modules,
        output_dir=tmp_path, use_submodules=False, init_git=False, want_landing=True,
    )
    return tmp_path / "app" / "frontend" / "src"


@pytest.mark.parametrize("composite", ["shop", "social"])
def test_router_ends_with_a_catch_all(tmp_path, composite):
    src = _front(tmp_path, [composite])
    routes = (src / "routes.tsx").read_text()
    assert 'import { NotFoundPage, RouteErrorPage } from "./NotFoundPage.js";' in routes
    body = routes.split("createBrowserRouter([", 1)[1]
    # One pathless root with the errorElement; the routes are its children.
    assert body.startswith("\n  {\n    errorElement: <RouteErrorPage />,\n    children: [\n")
    top_level = re.findall(r'^      \{ path: "([^"]+)"', body, re.M)
    assert top_level[-1] == "*", top_level
    assert '      { path: "*", element: <NotFoundPage /> },' in body
    page = (src / "NotFoundPage.tsx").read_text()
    keys = (src / "i18n" / "keys.ts").read_text()
    for symbol in re.findall(r"STOREFRONT_I18N_KEYS\.(\w+)", page):
        assert f"  {symbol}: " in keys, symbol
    assert '"storefront.notfound.title": "There is no page at this address"' in keys
    # Technical detail only in dev builds.
    assert page.count("import.meta.env.DEV") == 1
