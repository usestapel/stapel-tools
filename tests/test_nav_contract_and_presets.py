"""The nav contract's teeth and the composite frontend presets.

Two findings from the shared-layer audit (2026-08-24, Q3/G5) are pinned here,
and both were SILENT failures — the worst kind for a generator, because the
output looks complete:

* `admin.root` was declared by nobody. gdpr's `admin.privacy` and video's
  `admin.usage` are submenu entries under it, so every generated container
  dropped them as orphans, with no log line and no missing file to notice.
* `requiresAuth` was emitted into every manifest and read by NOTHING. "/app"
  and "/account" are gated as subtrees, but an absolute-path entry is their
  SIBLING: `auth.qr_confirm` (`surface: "public"`, `requiresAuth: true`)
  mounted for anonymous visitors.

Plus the preset table (architect verdict: a composite backend has no react
package — its frontend counterpart is a named set of pairs one layer up).
"""
import pytest

from stapel_tools import _frontend_templates as F
from stapel_tools.create_project import (
    FRONTEND_COMPOSITES,
    FRONTEND_REACT_LIBS,
    CompositeError,
    composite_container_pages,
    composite_nav_entries,
    composite_pairs,
    composite_report,
)


def _entries(keys):
    return [{"key": k, **FRONTEND_REACT_LIBS[k]} for k in keys]


def _public_plan(keys):
    pairs = F.public_nav_pairs(_entries(keys))
    return F.build_public_route_plan(
        F.public_nav_manifests(pairs, app_package="acme")
    )


# ── the validator ────────────────────────────────────────────────────────────

ENTRY = {
    "id": "demo.screen",
    "labelKey": "demo.nav.screen",
    "icon": "AppstoreOutlined",
    "route": {"path": "demo"},
    "component": {"export": "DemoScreen", "subpath": "default"},
    "placement": {"level": "top"},
    "menuVisibleDefault": True,
    "requiresAuth": True,
    "order": 10,
}


class TestNavValidator:
    def test_a_registered_icon_passes(self):
        F.validate_nav_entries([ENTRY])

    def test_an_unknown_icon_is_an_error_at_generation_time(self):
        """The shell's registry falls back to a generic glyph for an unknown
        name without a word, so a typo ships as a slightly wrong picture."""
        bad = {**ENTRY, "icon": "SparkleOutlined"}
        with pytest.raises(F.NavContractError) as exc:
            F.validate_nav_entries([bad])
        assert "SparkleOutlined" in str(exc.value)

    def test_a_parent_nobody_declares_is_an_error(self):
        orphan = {
            **ENTRY,
            "id": "demo.child",
            "placement": {"level": "submenu", "parentId": "nowhere.root"},
        }
        with pytest.raises(F.NavContractError) as exc:
            F.validate_nav_entries([orphan])
        assert "nowhere.root" in str(exc.value)

    def test_a_container_root_parent_is_fine(self):
        """`admin.root` is declared by the CONTAINER, not by a pair — that is
        exactly why it used to look undeclared."""
        F.validate_nav_entries([
            {**ENTRY, "id": "x.y", "placement": {"level": "submenu", "parentId": "admin.root"}}
        ])

    def test_a_parent_a_registered_pair_declares_is_fine_when_not_selected(self):
        """A submenu under a pair that is simply NOT INSTALLED is a legitimate
        drop (auth.security under profiles.settings). Only a parent nobody
        declares anywhere is a defect."""
        F.validate_nav_entries([
            {
                **ENTRY,
                "id": "x.y",
                "placement": {"level": "submenu", "parentId": "profiles.settings"},
            }
        ])

    def test_route_index_is_refused_rather_than_half_implemented(self):
        """Decision: `route.index` is DROPPED from the contract. resolveNav
        copies `route` opaque, the shell's matchesLocation ignores it, and the
        container decides its own section index — so a declaration would be a
        route that never matches."""
        with pytest.raises(F.NavContractError) as exc:
            F.validate_nav_entries([{**ENTRY, "route": {"path": "demo", "index": True}}])
        assert "route.index" in str(exc.value)

    def test_every_registered_mirror_passes_the_validator(self):
        """The live registry is itself under the gate — this is what would have
        caught `admin.root` on the day it was written."""
        F.validate_nav_entries(
            [e for pair in FRONTEND_REACT_LIBS.values() for e in pair.get("nav", ())]
        )


# ── admin.root, in both containers ───────────────────────────────────────────


class TestAdminRoot:
    def test_the_storefront_declares_it_when_a_pair_hangs_from_it(self):
        plan = _public_plan(["auth", "profiles", "gdpr", "video"])
        admin = {r["path"] for r in plan["member_absolute"] if r.get("gate") == "admin"}
        assert admin == {"/admin", "/admin/privacy", "/admin/usage"}

    def test_no_admin_tab_without_an_admin_screen(self):
        """An empty Admin section is its own defect."""
        plan = _public_plan(["auth", "profiles"])
        assert not [r for r in plan["member_absolute"] if r.get("gate") == "admin"]
        manifests = F.public_nav_manifests(
            F.public_nav_pairs(_entries(["auth", "profiles"])), app_package="acme"
        )
        assert not any(
            e["id"] == "admin.root" for m in manifests for e in m["entries"]
        )

    def test_the_monolith_declares_it_too(self):
        pairs = F.nav_wired_pairs(_entries(["auth", "profiles", "gdpr", "video"]),
                                  auth_wired=True)
        plan = F.build_nav_route_plan(pairs)
        paths = {c["path"] for c in plan["app_children"]}
        assert "admin" in paths
        assert "admin/privacy" in paths
        assert "admin/usage" in paths
        assert plan["container_roots"] == ["account.root", "admin.root"]

    def test_the_storefront_admin_subtree_is_staff_gated_and_listed(self):
        keys = ["auth", "profiles", "gdpr", "video"]
        src = F.render_public_routes_tsx(_public_plan(keys), {}, pairs=tuple(keys))
        assert "element: <AdminGate />," in src
        # listed, not hidden: the entry stays menuVisible and the gate refuses
        # by name (the fleet rule for a staff screen).
        assert F.ADMIN_ROOT_ENTRY["menuVisibleDefault"] is True
        assert "admin-gate-refusal" in F.ADMIN_GATE_TSX

    def test_the_monolith_admin_screens_are_staff_gated(self):
        pairs = F.nav_wired_pairs(_entries(["auth", "gdpr"]), auth_wired=True)
        plan = F.build_nav_route_plan(pairs)
        src = F.render_routes_tsx(
            plan, auth_wired=True, want_landing=False, app_route_present=True
        )
        assert "<AdminGate><PrivacyAdminPane /></AdminGate>" in src

    def test_no_staff_gate_without_auth_installed(self):
        """There is no staff fact to read without the auth session, and a gate
        that only pretends to check is worse than the member gate alone."""
        keys = ["profiles", "gdpr", "video"]
        src = F.render_public_routes_tsx(_public_plan(keys), {}, pairs=tuple(keys))
        assert "AdminGate" not in src
        assert '{ path: "/admin/privacy"' in src


# ── requiresAuth, per route ──────────────────────────────────────────────────


class TestPerRouteAuth:
    def test_a_public_surface_route_that_needs_a_session_sits_inside_the_gate(self):
        keys = ["auth", "profiles"]
        src = F.render_public_routes_tsx(_public_plan(keys), {}, pairs=tuple(keys))
        gate = src.index("element: <MemberGate />,")
        assert src.index('path: "/qr-confirm"') > gate
        # and sign-in stays outside it, whatever else moves
        assert src.index('path: "/login"') < gate

    def test_the_monolith_gates_an_absolute_route_that_declares_it(self):
        pairs = F.nav_wired_pairs(_entries(["auth"]), auth_wired=True)
        plan = F.build_nav_route_plan(pairs)
        src = F.render_routes_tsx(
            plan, auth_wired=True, want_landing=False, app_route_present=True
        )
        assert '{ path: "/qr-confirm", element: <ProtectedRoute><QrConfirmPanel /></ProtectedRoute> },' in src
        # never the sign-in screen: ProtectedRoute redirects THERE.
        assert '{ path: "/login", element: <AuthPanel /> },' in src


# ── audience, in the member container ────────────────────────────────────────


class TestMemberAudience:
    def test_the_gated_app_resolves_the_member_tree(self):
        pairs = F.nav_wired_pairs(_entries(["auth", "profiles"]), auth_wired=True)
        src = F.render_nav_generated_ts(pairs, auth_wired=True)
        assert "resolveMemberNav(" in src
        # never the bare, audience-optional call (`reresolveNav` is this
        # module's own exported helper, and it delegates to the named one)
        assert 'import { resolveMemberNav } from "@stapel/shell-react";' in src
        assert " resolveNav(INSTALLED_NAV_MANIFESTS" not in src

    def test_an_ungated_app_resolves_the_public_tree(self):
        """The bare `resolveNav`'s audience argument is optional and its default
        does not filter — which is how "/app" handed every member screen to
        whoever was standing in front of it."""
        pairs = F.nav_wired_pairs(_entries(["profiles"]), auth_wired=False)
        src = F.render_nav_generated_ts(pairs, auth_wired=False)
        assert "resolvePublicNav(" in src


# ── composite presets ────────────────────────────────────────────────────────


class TestComposites:
    def test_every_preset_expands_to_registered_pairs(self):
        for name in FRONTEND_COMPOSITES:
            for key in composite_pairs(name):
                assert key in FRONTEND_REACT_LIBS, (name, key)

    def test_classified_carries_the_verdict_s_member_set(self):
        """Architect verdict: classified = the shop shape + place + messaging.
        The three members with no react pair yet are NAMED as pending, not
        quietly dropped."""
        pairs = composite_pairs("classified")
        for key in ("auth", "profiles", "categories", "listings", "search",
                    "reviews", "chat", "cdn"):
            assert key in pairs
        assert set(FRONTEND_COMPOSITES["classified"]["pending"]) == {
            "geo", "moderation", "currencies"
        }

    def test_a_preset_bakes_the_container_roots_into_its_nav_bundle(self):
        ids = {e["id"] for e in composite_nav_entries("classified")}
        assert "account.root" in ids
        assert "listings.compose" in ids
        assert "chat.conversations" in ids

    def test_a_preset_names_its_container_pages(self):
        pages = composite_container_pages("classified")
        assert "src/StorefrontHome.tsx" in pages
        assert "src/AccountHome.tsx" in pages
        # the cross-pair page its member set makes mountable
        assert "src/pages/ListingComposePage.tsx" in pages

    def test_social_has_no_catalogue_screens(self):
        ids = {e["id"] for e in composite_nav_entries("social")}
        assert "listings.compose" not in ids
        assert "chat.conversations" in ids

    def test_an_unknown_preset_names_the_roster(self):
        with pytest.raises(CompositeError) as exc:
            composite_pairs("marketplace")
        assert "classified" in str(exc.value)

    def test_the_report_prints_the_pending_members(self):
        text = "\n".join(composite_report("classified"))
        assert "NOT INSTALLED — moderation" in text
        assert "NOT INSTALLED — geo" in text
