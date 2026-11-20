"""``stapel-shell-lint`` (NAV001-NAV005): silent on what the scaffold
generates, red on each inversion of the navigation spec §6.4 it owns."""
import json
import shutil
from pathlib import Path

import pytest

from stapel_tools import shell_lint


@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> Path:
    from stapel_tools.frontend_repo_init import main

    root = tmp_path_factory.mktemp("web")
    assert main(["--surface", "public", "--preset", "shop", "--name", "web", "--title", "Web",
                 str(root / "web")]) in (0, None)
    return root / "web"


@pytest.fixture
def project(generated, tmp_path) -> Path:
    dest = tmp_path / "web"
    shutil.copytree(generated, dest)
    return dest


def _codes(project: Path) -> list[str]:
    return [f.code for f in shell_lint.lint(project) if not f.baseline]


def test_the_generated_project_is_clean_and_was_seen(project, capsys):
    assert _codes(project) == []
    assert shell_lint.main([str(project)]) == 0
    out = capsys.readouterr().out
    assert "1 route file(s)" in out and "0 recorded hash(es)" not in out


def test_i1_a_route_beside_the_shell_layout_is_nav001(project):
    routes = project / "src" / "routes.tsx"
    text = routes.read_text()
    marker = "export const routes: RouteObject[] = ["
    assert marker in text
    routes.write_text(text.replace(marker, marker + '\n  { path: "/promo", element: <div /> },', 1))
    assert "NAV001" in _codes(project)


def test_a_route_file_without_the_shell_layout_is_nav001(project):
    (project / "src" / "routes.extra.tsx").write_text(
        'export const more = [{ path: "/x", element: null }];\n')
    findings = [f for f in shell_lint.lint(project) if f.code == "NAV001"]
    assert findings and findings[0].path.endswith("routes.extra.tsx")


def test_i2_a_page_module_nobody_binds_is_nav002(project):
    pages = project / "src" / "pages"
    pages.mkdir(exist_ok=True)
    (pages / "PromoPage.tsx").write_text("export function PromoPage() { return null; }\n")
    assert _codes(project) == ["NAV002"]


def test_an_entry_without_a_binding_and_a_binding_without_an_entry_are_nav003(project):
    app_nav = project / "stapel.app-nav.json"
    doc = json.loads(app_nav.read_text()) if app_nav.exists() else {"entries": []}
    doc.setdefault("entries", []).append({"id": "app.cart", "path": "cart"})
    app_nav.write_text(json.dumps(doc))
    pages_ts = project / "src" / "app.pages.ts"
    pages_ts.write_text(pages_ts.read_text().replace(
        "= {", '= {\n  "app.wishlist": Wishlist,', 1))
    messages = [f.message for f in shell_lint.lint(project) if f.code == "NAV003"]
    assert any(m.startswith("app.cart: an entry with no binding") for m in messages)
    assert any(m.startswith("app.wishlist: a binding with no entry") for m in messages)


def test_i6_a_hand_edit_of_a_generated_file_is_nav004(project):
    routes = project / "src" / "routes.tsx"
    routes.write_text(routes.read_text() + "\n// tweak\n")
    assert _codes(project) == ["NAV004"]


def test_no_resolved_navigation_is_nav005_and_a_baseline_makes_it_a_warning(project, tmp_path):
    comp = project / "stapel.composition.json"
    doc = json.loads(comp.read_text())
    doc.pop("navigation", None)
    doc.pop("resolved", None)
    comp.write_text(json.dumps(doc))
    assert "NAV005" in _codes(project)
    baseline = tmp_path / "baseline.json"
    assert shell_lint.main([str(project), "--write-baseline", str(baseline)]) == 1
    assert shell_lint.main([str(project), "--baseline", str(baseline)]) == 0


def test_sign_in_without_an_account_slot_is_nav005(project):
    comp = project / "stapel.composition.json"
    doc = json.loads(comp.read_text())
    resolved, _ = shell_lint._resolved(project)
    assert resolved is not None
    resolved["signIn"] = "auth.login"
    resolved["slots"]["account"] = None
    if "navigation" in doc and doc["navigation"].get("resolved"):
        doc["navigation"]["resolved"] = resolved
    else:
        doc["resolved"]["react"] = resolved
    comp.write_text(json.dumps(doc))
    assert "NAV005" in _codes(project)


def test_a_directory_that_is_not_a_project_is_refused(tmp_path, capsys):
    assert shell_lint.main([str(tmp_path)]) == 1
    assert "nothing checked" in capsys.readouterr().err


def test_a_generated_monolith_is_clean(tmp_path):
    from stapel_tools.create_project import create_project

    create_project(name="mono", project_type="monolith", title="Mono", url="https://shop.example",
                   company_name="Acme", company_email="a@shop.example", modules=["shop"],
                   output_dir=tmp_path, use_submodules=False, init_git=False)
    project = tmp_path / "mono"
    assert (project / "frontend" / "src" / "pages").is_dir()
    assert _codes(project) == []
    (project / "frontend" / "src" / "routes.tsx").write_text("// emptied\n")
    assert set(_codes(project)) == {"NAV001", "NAV002", "NAV004"}
