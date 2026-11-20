"""What the scaffold now does itself instead of leaving it to the project:
the regenerate target, own-page labels, the staff audience, per-destination
guest walls, an own search control, a real home page and the provenance block
of ``stapel.composition.json``."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from stapel_tools import nav_cli
from stapel_tools.composition.emitter import build_composition, emitter_for

COMMON = ["--title", "Acme Parts", "--url", "https://shop.example", "--company-name", "Acme",
          "--company-email", "a@shop.example", "--no-git", "--no-submodules"]

#: Sentences that told a developer what to do, on a page a customer sees.
DEVELOPER_COPY = (
    "scaffolded landing page", "Replace this copy", "Compose it here",
    "container's to compose", "Compose the landing here",
)


def _create(tmp_path: Path, *flags: str, name: str = "acme") -> Path:
    result = subprocess.run(
        [sys.executable, "-m", "stapel_tools.create_project", name, "--type", "monolith",
         *COMMON, "--output-dir", str(tmp_path), *flags],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return tmp_path / name


def _plan(modules, **kw):
    from stapel_tools._auth_model import AuthComposition
    from stapel_tools.create_project import _expand_with_requires

    feats = _expand_with_requires(list(modules))
    comp = build_composition(
        ctx={"slug": "acme", "title": "Acme Parts"}, project_type="monolith",
        feature_libs=feats, want_auth="auth" in feats,
        auth_comp=AuthComposition(model="public") if "auth" in feats else None, **kw)
    return emitter_for("react").plan(comp, comp.platform("react"))


def _add_page(root: Path, *, label: str | None) -> None:
    (root / "stapel.app-nav.json").write_text(json.dumps({"version": 1, "entries": [
        {"id": "app.parts", "labelKey": "app.nav.parts", "icon": "apps", "path": "/parts"}]}))
    pages = root / "frontend" / "src" / "app.pages.ts"
    pages.write_text(pages.read_text().replace(
        '  // "app.cart": CartPage,', '  "app.parts": PartsPage,').replace(
        'import type { ComponentType } from "react";',
        'import type { ComponentType } from "react";\nimport { PartsPage } from "./PartsPage.js";'))
    if label is not None:
        (root / "frontend" / "src" / "app.labels.json").write_text(
            json.dumps({"en": {"app.nav.parts": label}}))


# --- A2: make nav-regenerate ------------------------------------------------


def test_the_project_makefile_and_agents_name_the_regenerate_target(tmp_path):
    root = _create(tmp_path, "--modules", "shop")
    makefile = (root / "Makefile").read_text()
    assert ".PHONY: nav-regenerate\nnav-regenerate:\n\tstapel-nav-regenerate .\n" in makefile
    agents = (root / "AGENTS.md").read_text()
    assert "`make nav-regenerate`" in agents
    assert "frontend/src/app.labels.json" in agents
    assert "never register labels in\n  `frontend/src/modules.tsx`" in agents


# --- A3: own-page labels ----------------------------------------------------


def test_labels_are_the_projects_file_and_the_registration_is_generated(tmp_path):
    root = _create(tmp_path, "--modules", "shop")
    src = root / "frontend" / "src"
    assert json.loads((src / "app.labels.json").read_text()) == {"en": {}}
    assert 'import { registerAppLabels } from "./app.labels.generated.js";' in (
        src / "modules.tsx").read_text()
    assert "registerAppLabels(i18n);" in (src / "modules.tsx").read_text()

    _add_page(root, label=None)
    nav_cli.regenerate(root)
    generated = (src / "app.labels.generated.ts").read_text()
    assert '"app.nav.parts": "app.parts (no label in src/app.labels.json)"' in generated
    resolved = json.loads((root / "stapel.composition.json").read_text())["resolved"]["react"]
    assert resolved["labels"]["missing"] == ["app.parts"]

    (src / "app.labels.json").write_text(json.dumps({"en": {"app.nav.parts": "Parts"}}))
    written = nav_cli.regenerate(root)
    assert "frontend/src/app.labels.json" not in written
    assert json.loads((src / "app.labels.json").read_text()) == {"en": {"app.nav.parts": "Parts"}}
    assert "no label" not in (src / "app.labels.generated.ts").read_text()
    resolved = json.loads((root / "stapel.composition.json").read_text())["resolved"]["react"]
    assert resolved["labels"]["missing"] == []


def test_strict_regenerate_refuses_an_unlabelled_own_page(tmp_path):
    root = _create(tmp_path, "--modules", "shop", "--strict")
    _add_page(root, label=None)
    from stapel_tools.composition.navigation import NavigationError

    with pytest.raises(NavigationError) as exc:
        nav_cli.regenerate(root)
    assert exc.value.code == "NAV-E11"
    (root / "frontend" / "src" / "app.labels.json").write_text(
        json.dumps({"en": {"app.nav.parts": "Parts"}}))
    nav_cli.regenerate(root)


# --- A4: staff audience, guest per destination ------------------------------


def test_the_storefront_chrome_shows_the_admin_section_to_staff_only():
    plan = _plan(["shop"])
    nav = plan.files["frontend/src/nav.generated.ts"]
    assert 'const STAFF_SECTION: ReadonlySet<string> = new Set(["admin.root"]);' in nav
    assert "export const RESOLVED_NAV_STAFF" in nav
    assert "RESOLVED_NAV_STAFF.filter(\n  (entry) => !STAFF_SECTION.has(entry.id)\n)" in nav
    chrome = plan.files["frontend/src/chrome.generated.tsx"]
    assert "const staff = user?.is_staff === true;" in chrome
    assert ("nav={member ? (staff ? RESOLVED_NAV_STAFF : RESOLVED_NAV_MEMBER) : "
            "RESOLVED_NAV_GUEST}") in chrome
    assert "RESOLVED_NAV_STAFF" in plan.files["frontend/src/__tests__/nav.test.ts"]
    assert "RESOLVED_NAV_STAFF" in plan.files["frontend/src/AdminHome.tsx"]


def test_the_app_chrome_keeps_its_switched_off_admin_section():
    plan = _plan(["auth", "notifications"])
    assert "RESOLVED_NAV_STAFF" not in plan.files["frontend/src/nav.generated.ts"]


def test_a_guest_wall_per_destination_is_emitted():
    plan = _plan(["shop"], navigation={"guest": {"wall": ["profiles.settings"]}})
    nav = plan.files["frontend/src/nav.generated.ts"]
    assert 'const GUEST_WALLED: ReadonlySet<string> = new Set(["profiles.settings"]);' in nav
    assert "GUEST_WALLED.has(entry.id)" in nav
    resolved = json.loads(plan.files["frontend/stapel.composition.json"])["navigation"]["resolved"]
    assert "profiles.settings" in resolved["audiences"]["guest"]["expanded"]["visible"]


# --- A5: own search control ---------------------------------------------------


def test_an_own_search_control_is_rendered_in_the_search_slot():
    plan = _plan(["shop"], navigation={"slots": {"search": "app.search_box"}},
                 app_bindings={"react": ("app.search_box",)})
    chrome = plan.files["frontend/src/chrome.generated.tsx"]
    assert 'import { APP_PAGES } from "./app.pages.js";' in chrome
    assert 'const SearchControl = APP_PAGES["app.search_box"];' in chrome
    assert "searchSlot={SearchControl !== undefined ? <SearchControl /> : undefined}" in chrome
    resolved = json.loads(plan.files["frontend/stapel.composition.json"])["navigation"]["resolved"]
    assert resolved["reach"]["/s"] == "slot:search:app.search_box"


def test_shell_lint_accepts_a_slot_component_without_an_app_nav_entry(tmp_path):
    from stapel_tools.shell_lint import check_app_nav

    root = _create(tmp_path, "--modules", "shop")
    doc = json.loads((root / "stapel.composition.json").read_text())
    doc["navigation"] = {"slots": {"search": "app.search_box"}}
    (root / "stapel.composition.json").write_text(json.dumps(doc))
    pages = root / "frontend" / "src" / "app.pages.ts"
    pages.write_text(pages.read_text().replace(
        '  // "app.cart": CartPage,', '  "app.search_box": SearchBox,'))
    nav_cli.regenerate(root)
    assert check_app_nav(root) == []
    doc["navigation"] = {}
    (root / "stapel.composition.json").write_text(json.dumps(doc))
    nav_cli.regenerate(root)
    assert [f.code for f in check_app_nav(root)] == ["NAV003"]


# --- A7: a real home page ---------------------------------------------------


@pytest.mark.parametrize("modules,landing", [
    (["shop"], True), (["shop"], False), (["auth", "notifications"], False),
    (["auth", "notifications"], True), ([], False),
])
def test_no_page_ships_instructions_to_the_developer(modules, landing):
    plan = _plan(modules, want_landing=landing)
    for rel, content in plan.files.items():
        if not rel.startswith("frontend/src/") or not isinstance(content, str):
            continue
        for sentence in DEVELOPER_COPY:
            assert sentence not in content, (rel, sentence)


def test_the_home_page_is_built_from_the_composition():
    plan = _plan(["shop"], want_landing=True, pitch="Parts for every vintage car.")
    landing = plan.files["frontend/src/LandingPage.tsx"]
    assert 'data-testid="page-landing"' in landing
    assert "const HAS_PITCH = true;" in landing
    assert "<Typography.Title level={1}>" in landing
    assert "nav.filter(" in landing and "destination-${entry.id}" in landing
    assert 'data-testid="page-landing-empty"' in landing
    keys = plan.files["frontend/src/i18n/keys.ts"]
    assert '"storefront.landing.title": "Acme Parts"' in keys
    assert '"storefront.landing.body": "Parts for every vintage car."' in keys
    bare = _plan(["shop"], want_landing=True)
    assert "const HAS_PITCH = false;" in bare.files["frontend/src/LandingPage.tsx"]
    home = _plan(["shop"]).files["frontend/src/StorefrontHome.tsx"]
    assert 'data-testid="storefront-home"' in home
    account = _plan(["shop"]).files["frontend/src/AccountHome.tsx"]
    assert '?.children ?? []' in account and '"account.root"' in account


def test_the_pitch_is_a_cli_flag_and_a_composition_key(tmp_path):
    root = _create(tmp_path, "--modules", "shop", "--landing", "--pitch", "Parts, fast.")
    doc = json.loads((root / "stapel.composition.json").read_text())
    assert doc["input"]["pitch"] == "Parts, fast."
    nav_cli.regenerate(root)
    keys = (root / "frontend" / "src" / "i18n" / "keys.ts").read_text()
    assert '"storefront.landing.body": "Parts, fast."' in keys


def test_the_placeholder_marks_itself():
    plan = _plan(["shop"])
    assert "data-stapel-placeholder={props.entryId}" in plan.files["frontend/src/NavPlaceholder.tsx"]


# --- A8: provenance ---------------------------------------------------------


def _verify(root: Path) -> list[str]:
    """What a control plane does: sha256 of each listed file, nothing imported."""
    provenance = json.loads((root / "stapel.composition.json").read_text())["provenance"]
    assert provenance["hash"] == "sha256"
    return [rel for rel, digest in provenance["files"].items()
            if hashlib.sha256((root / rel).read_bytes()).hexdigest() != digest]


def test_provenance_names_the_generator_and_hashes_every_frontend_file(tmp_path):
    from importlib.metadata import version

    spec = tmp_path / "composition.json"
    spec.write_text(json.dumps({"modules": ["shop"], "recipe": {"name": "shop", "version": 2}}))
    root = _create(tmp_path, "--composition", str(spec))
    provenance = json.loads((root / "stapel.composition.json").read_text())["provenance"]
    assert provenance["schema"] == "stapel.provenance/1"
    assert provenance["tool"] == "stapel-tools"
    assert provenance["version"] == version("stapel-tools")
    assert provenance["recipe"] == {"name": "shop", "version": 2}
    on_disk = {p.relative_to(root).as_posix() for p in (root / "frontend").rglob("*")
               if p.is_file()}
    assert set(provenance["files"]) == on_disk
    assert "frontend/src/routes.tsx" in provenance["regenerated"]
    assert "frontend/src/app.pages.ts" not in provenance["regenerated"]
    assert _verify(root) == []
    # A hand edit is seen.
    routes = root / "frontend" / "src" / "routes.tsx"
    routes.write_text(routes.read_text() + "\n// edited\n")
    assert _verify(root) == ["frontend/src/routes.tsx"]


def test_regenerate_moves_the_hashes_it_owns_and_says_who_did(tmp_path):
    root = _create(tmp_path, "--modules", "shop")
    _add_page(root, label="Parts")
    nav_cli.regenerate(root)
    provenance = json.loads((root / "stapel.composition.json").read_text())["provenance"]
    assert provenance["regeneratedBy"]
    stale = _verify(root)
    # Only the project's own files moved: the generated ones were restamped.
    assert set(stale) <= {"frontend/src/app.pages.ts", "frontend/src/app.labels.json"}
    assert not set(stale) & set(provenance["regenerated"])


# --- the dry run plans pages that are about to be bound ----------------------


def test_nav_plan_assume_bound_plans_new_own_pages_under_strict(tmp_path, capsys):
    app_nav = tmp_path / "stapel.app-nav.json"
    app_nav.write_text(json.dumps({"version": 1, "entries": [
        {"id": "app.parts", "labelKey": "app.nav.parts", "icon": "apps", "path": "/parts"}]}))
    labels = tmp_path / "app.labels.json"
    labels.write_text(json.dumps({"en": {"app.nav.parts": "Parts"}}))
    base = ["--modules", "shop", "--strict", "--app-nav", str(app_nav)]
    assert nav_cli.plan_main(base) == 1
    assert "NAV-E09" in capsys.readouterr().err
    assert nav_cli.plan_main([*base, "--assume-bound"]) == 1
    assert "NAV-E11" in capsys.readouterr().err
    assert nav_cli.plan_main([*base, "--assume-bound", "--app-labels", str(labels)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["react"]["reach"]["/parts"] == "menu:app.parts"
