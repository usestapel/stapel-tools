"""``stapel-frontend-repo-init --surface public`` — the storefront container's
SOURCE half (the public-storefront spec §3.3 / §6.1).

Wave C. Every renderer has a unit test here; the whole emission has one
integration test at the bottom that installs, type-checks, builds and lints the
generated app for real (opt-in — see its own docstring for why, and for what it
costs).
"""
import json
import os
import shutil
import subprocess
import time

import pytest

from stapel_tools import _frontend_templates as F
from stapel_tools.create_project import FRONTEND_REACT_LIBS
from stapel_tools.frontend_repo_init import (
    SurfaceError,
    main,
    public_source_plan,
    write_public_surface,
)

# The seven wave-4 pairs plus the two a public surface always needs: sign-in
# is generated unconditionally, and the seller's own settings screen is a
# member route the nav manifests already declare.
SEVEN_PAIRS = [
    "attributes",
    "categories",
    "cdn",
    "chat",
    "listings",
    "reviews",
    "search",
]
STOREFRONT_PAIRS = ["auth", "profiles", *SEVEN_PAIRS]


def _plan(**kwargs):
    args = dict(
        name="acme-storefront",
        title="Acme Storefront",
        pairs=STOREFRONT_PAIRS,
        locale="en",
        realtime=False,
        extra_prefixes=[],
        doc_type="listing",
    )
    args.update(kwargs)
    return public_source_plan(**args)


class TestMountTable:
    def test_covers_every_registered_nav_entry(self):
        """A nav entry with no mount recipe falls through to a placeholder —
        a named gap, never a broken mount. That fallback is the SAFETY net,
        not the plan: every entry the fleet actually publishes today has to be
        decided here, so a new one shows up as a failing test rather than as a
        page saying "this build does not know about me"."""
        registered = {
            entry["id"]
            for info in FRONTEND_REACT_LIBS.values()
            for entry in info.get("nav", [])
        }
        missing = registered - set(F.NAV_ENTRY_MOUNTS)
        assert missing == set(), (
            f"nav entries with no mount recipe: {sorted(missing)}. Read the "
            "component's prop interface in the sibling stapel-react checkout "
            "and add a row to NAV_ENTRY_MOUNTS."
        )

    def test_the_container_roots_are_the_only_local_ones(self):
        """The two sections no module owns — and now BOTH are declared. Before
        `admin.root` existed here, gdpr's `admin.privacy` and video's
        `admin.usage` hung from a parent nobody declared and were dropped as
        orphans in every generated container, silently."""
        local = {k for k, v in F.NAV_ENTRY_MOUNTS.items() if "local" in v}
        assert local == {"account.root", "admin.root"}


class TestRoutePlan:
    def _built(self, pairs=None):
        entries = [
            {"key": k, **FRONTEND_REACT_LIBS[k]} for k in (pairs or STOREFRONT_PAIRS)
        ]
        manifests = F.public_nav_manifests(
            F.public_nav_pairs(entries), app_package="acme-storefront"
        )
        return F.build_public_route_plan(manifests)

    def test_public_and_member_routes_are_separated_by_surface(self):
        plan = self._built()
        public = {r["path"] for r in plan["public"]}
        member = {r["path"] for r in plan["member_absolute"]}
        # Every one of these is `surface: "public"` in its pair's manifest —
        # including auth.qr_confirm, which is `requiresAuth: true` AND public:
        # a signed-in phone confirming a signed-out desktop. Deriving the
        # surface from requiresAuth would have put it behind the gate.
        assert public == {"/login", "/qr-confirm", "/c", "/c/:slug", "/l/:id", "/s",
                          "/ranking-disclosure", "/u/:userId"}
        # `/new` is the one member-surface route that is not an admin screen
        # and not a `/account` child; the rest of the member-absolute set is
        # the staff-gated admin subtree auth's own admin skin hangs from
        # `admin.root` (TestAdminRoot owns its exact shape).
        assert {r["path"] for r in plan["member_absolute"]
                if r.get("gate") != "admin"} == {"/new"}
        assert "/admin" in member

    def test_member_relative_routes_hang_under_account(self):
        plan = self._built()
        paths = {r["path"] for r in plan["account_children"]}
        assert paths == {"chat", "connections", "settings", "settings/language",
                         "settings/notifications", "settings/security", "listings",
                         "favorites"}
        assert plan["account_entry"]["id"] == "account.root"

    def test_submenu_orphan_is_dropped_not_thrown(self):
        """`auth.security` nests under `profiles.settings`. Without profiles
        installed it has no parent — resolveNav drops such an entry silently
        and this plan must agree, or routing and the menu disagree about what
        "installed" means."""
        plan = self._built(pairs=["auth", "listings", "search", "categories"])
        paths = {r["path"] for r in plan["account_children"]}
        assert "settings/security" not in paths
        # listings' own submenu entries still resolve: their parent is
        # account.root, which the CONTAINER contributes.
        assert {"listings", "favorites"} <= paths

    def test_account_root_cannot_come_from_the_override_file(self):
        """`NavOverridesFile` carries `menuVisible` and `order` per EXISTING
        id and nothing else, so the container's own entry has to be a
        manifest. The override file still tunes it — that is the half of the
        channel that was ever real."""
        overrides = json.loads(F.render_public_nav_overrides_json())
        assert set(overrides["overrides"]) == {"account.root"}
        assert overrides["overrides"]["account.root"]["order"] == 100


class TestRoutesRenderer:
    def _routes(self, **kwargs):
        return _plan(**kwargs)["files"]["src/routes.tsx"]

    def test_public_shell_is_the_layout_route(self):
        src = self._routes()
        assert "element: <StorefrontShell />," in src
        assert "{ index: true, element: <StorefrontHome /> }," in src

    def test_the_storefront_chrome_stops_pinning_a_theme_at_the_shell_floor(
        self, monkeypatch
    ):
        """`mode="light"` is a generator answering a question it cannot know —
        the shell follows the document's live `data-theme` through SkinTheme.
        It is gated on the PIN because the published `PublicShellProps` still
        REQUIRES `mode`: a container that dropped it there would not compile,
        which is the 0.54.0 class in a TypeScript costume."""
        from stapel_tools import create_project as C

        monkeypatch.setattr(C, "FRONTEND_SHELL_REACT_VERSION",
                            C.FRONTEND_SHELL_SELF_THEMING_FLOOR)
        assert "<PublicShell nav={nav} />" in F.render_storefront_shell_tsx()

        monkeypatch.setattr(C, "FRONTEND_SHELL_REACT_VERSION", "0.6.0")
        assert '<PublicShell nav={nav} mode="light" />' in F.render_storefront_shell_tsx()

    def test_account_subtree_sits_inside_the_member_gate(self):
        src = self._routes()
        gate = src.index("element: <MemberGate />,")
        account = src.index('path: "/account",')
        assert gate < account, "the /account subtree must be nested inside the gate"
        assert src.index('path: "/login"') < gate, "sign-in must be outside the gate"

    def test_the_cross_pair_composer_is_wired_from_the_installed_pairs(self):
        """`ListingComposerPage` needs the category schema and the photo queue,
        and an L2 pair may not import another L2 pair — so the CONTAINER
        composes them. It used to be a placeholder, which meant the scripted
        storefront had no way to list anything at all."""
        files = _plan()["files"]
        src = files["src/routes.tsx"]
        assert '{ path: "/new", element: <ListingComposePage /> },' in src
        page = files["src/pages/ListingComposePage.tsx"]
        assert "useCategoryFeatures" in page
        assert "CategoryPickerField" in page
        # ONE upload queue: the bag the composer publishes from is the bag the
        # grid draws. Two queues publish an empty images_draft while the photos
        # sit on screen.
        assert page.count("useUploadQueue(") == 1
        assert "<MediaGalleryField bag={images} />" in page
        # The slot it CANNOT fill is named on the page, not papered over.
        assert "renderLocationPicker" in page
        assert "compose-location-gap" in page

    def test_the_composer_falls_back_to_a_named_gap_without_its_member_pairs(self):
        """A composite screen is only mountable when every pair it composes is
        installed; short of that the page NAMES which prop belongs to which
        missing pair, rather than fabricating an empty feature schema (which
        does not read as "not wired yet" — it reads as "this category has no
        attributes")."""
        src = _plan(pairs=["auth", "profiles", "categories", "listings"])[
            "files"
        ]["src/routes.tsx"]
        assert 'entryId="listings.compose"' in src
        assert "gallerySlot (needs @stapel/cdn-react)" in src
        assert "<ListingComposePage" not in src

    def test_route_parameters_go_through_a_wrapper(self):
        src = self._routes()
        assert '{ path: "/l/:id", element: <ListingDetailPaneRoute /> },' in src
        assert '{ path: "/c/:slug", element: <CategoryPageRoute /> },' in src

    def test_search_page_is_a_named_gap_without_a_doc_type(self):
        """Which doc types exist is a DEPLOYMENT fact
        (`STAPEL_SEARCH["SOURCES"]`). A guessed one sends every search to a
        type the backend refuses, from a page that looks perfectly wired."""
        src = self._routes(doc_type="")
        assert 'entryId="search.results"' in src
        assert 'missing={["defaultType"]}' in src
        assert "<SearchPageRoute />" not in src


class TestPageWrappers:
    def test_numeric_param_refuses_nan_by_name(self):
        files = _plan()["files"]
        src = files["src/pages/ListingDetailPaneRoute.tsx"]
        assert "const id = Number(rawId);" in src
        assert "!Number.isInteger(id)" in src
        assert '<RouteParamProblem param="id" />' in src
        assert "<ListingDetailPane id={id} />" in src

    def test_string_param_refuses_the_empty_string(self):
        src = _plan()["files"]["src/pages/CategoryPageRoute.tsx"]
        assert "slug.length === 0" in src
        assert "<CategoryPage slug={slug} />" in src

    def test_search_adapter_comes_from_the_pairs_own_router_subpath(self):
        """@stapel/search-react keeps react-router out of its main entry on
        purpose, so the binding lives on `./router` and the container is what
        joins the two."""
        src = _plan()["files"]["src/pages/SearchPageRoute.tsx"]
        assert 'from "@stapel/search-react/router";' in src
        assert 'from "@stapel/search-react/default";' in src
        assert '<SearchPage adapter={adapter} defaultType="listing" />' in src


class TestMandateSource:
    def test_every_state_has_a_named_branch(self):
        src = F.render_mandate_source_ts()
        assert "mandateAsking()" in src
        assert 'mandateResolved("member")' in src
        assert 'mandateResolved("anonymous")' in src
        assert "mandateUnavailable(NO_SESSION_MODULE)" in src

    def test_guest_is_absent_with_the_reason_written_down(self):
        """A storefront has no mandate to lack, so `"guest"` is never
        produced. Written down rather than silently omitted: a reader has to
        be able to tell "impossible here" from "forgotten"."""
        src = F.render_mandate_source_ts()
        assert 'mandateResolved("guest")' not in src
        assert 'Why "guest" is never produced, said out loud' in src

    def test_no_session_manager_is_an_outage_not_anonymity(self):
        src = F.render_mandate_source_ts()
        assert "if (status === null) return { state: mandateUnavailable" in src

    def test_it_does_not_import_workspaces(self):
        src = F.render_mandate_source_ts()
        assert "workspaces" not in src.split("── Why")[0].lower()


class TestMemberGate:
    def test_all_five_match_mandate_arms_render_something_different(self):
        src = F.MEMBER_GATE_TSX
        for arm in ("member:", "anonymous:", "guest:", "asking:", "unavailable:"):
            assert arm in src, arm
        assert "<Outlet />" in src
        assert "?next=${next}" in src
        assert "<Skeleton" in src
        assert "MandateOutage" in src

    def test_an_outage_does_not_redirect_to_sign_in(self):
        """Bouncing someone to a login form because a backend hiccuped tells
        them they are logged out when they are not."""
        src = F.MEMBER_GATE_TSX
        unavailable = src[src.index("unavailable: (error)"):]
        assert "Navigate" not in unavailable


class TestNavGenerated:
    def test_both_trees_use_the_audience_named_resolvers(self):
        """`resolveNav`'s `audience` is optional and its default does not
        filter — a public container that forgot the option mounts every member
        screen and every one of them answers 403. The fix is a call that
        cannot be made wrong."""
        src = _plan()["files"]["src/nav.generated.ts"]
        assert "resolvePublicNav(" in src
        assert "resolveMemberNav(" in src
        assert "resolveNav(" not in src.replace("resolvePublicNav(", "").replace(
            "resolveMemberNav(", ""
        )

    def test_the_container_contributes_its_own_account_root(self):
        src = _plan()["files"]["src/nav.generated.ts"]
        manifests = json.loads(src[src.index("= [") + 2: src.index("] as const;") + 1])
        own = [m for m in manifests if m["package"] == "acme-storefront"]
        assert len(own) == 1
        assert own[0]["entries"][0]["id"] == "account.root"

    def test_pair_versions_come_from_the_registry(self):
        src = _plan()["files"]["src/nav.generated.ts"]
        assert f'"version": "{FRONTEND_REACT_LIBS["listings"]["version"]}"' in src


class TestModulesRegistry:
    def test_one_runtime_and_provider_per_pair_with_a_client(self):
        src = _plan()["files"]["src/modules.tsx"]
        for key in ("auth", "profiles", "categories", "cdn", "chat", "listings",
                    "reviews", "search"):
            assert f'const {key}Runtime = ' in src, key
        assert "<SearchProvider runtime={searchRuntime}>" in src

    def test_l0_pair_contributes_a_catalogue_and_nothing_else(self):
        src = _plan()["files"]["src/modules.tsx"]
        assert "registerAttributesI18n(i18n);" in src
        assert "attributesRuntime" not in src
        assert "AttributesProvider" not in src

    def test_auth_client_is_the_provider_default(self):
        """`<StapelProvider client={…}>` wants the client whose seams carry
        token refresh and the verification-403 branch."""
        src = _plan()["files"]["src/modules.tsx"]
        assert "client={authRuntime.client}" in src

    def test_mandate_provider_is_mounted_above_everything_that_renders(self):
        src = _plan()["files"]["src/modules.tsx"]
        assert "<MandateGateway>{children}</MandateGateway>" in src
        assert "useStorefrontMandateSource()" in src

    def test_the_containers_own_catalogue_is_registered_last(self):
        src = _plan()["files"]["src/modules.tsx"]
        assert src.index("registerSearchI18n(i18n);") < src.index("registerStorefrontI18n(")

    def test_a_locale_registers_each_pairs_published_catalogue(self):
        src = _plan(locale="ru")["files"]["src/modules.tsx"]
        assert 'from "@stapel/search-react/i18n/ru";' in src
        assert "registerSearchI18nRu(i18n);" in src
        assert 'registerStorefrontI18n(i18n, "ru");' in src
        # shell-react publishes no ru catalogue — its en floor goes under ru
        # so a button reads as a sentence rather than as `shell.public.sign_in`.
        assert 'registerShellI18n(i18n, "ru");' in src

    def test_an_untranslated_locale_is_refused_rather_than_faked(self):
        with pytest.raises(SurfaceError) as excinfo:
            _plan(locale="de")
        assert "not de" in str(excinfo.value) or "'de'" in str(excinfo.value)


class TestRealtimeSeam:
    def test_sockets_are_off_by_default_and_say_so(self):
        src = _plan()["files"]["src/modules.tsx"]
        assert "realtime: { socketUrl: null }," in src
        assert "WSGI" in src

    def test_realtime_flag_leaves_the_chat_pairs_own_transport_on(self):
        src = _plan(realtime=True)["files"]["src/modules.tsx"]
        assert "socketUrl: null" not in src
        assert 'createChatRuntime({ baseUrl: "/chat/api/v1/" });' in src

    def test_the_absent_realtime_package_is_documented_not_implied(self):
        """There is no `@stapel/realtime` on npm: stapel-realtime is a Python
        library and its browser half is not built. The flag must not read as
        if it had wired a fleet-wide primitive."""
        readme = _plan(realtime=True)["files"]["README.md"]
        assert "There is no `@stapel/realtime` package" in readme
        off = _plan(realtime=False)["files"]["README.md"]
        assert "no\n`@stapel/realtime` package yet" in off or "@stapel/realtime" in off


class TestProjectFiles:
    def test_package_json_pins_come_from_the_registry(self):
        """The storefront container installs the SAME substrate the generator
        pins — asserted against the generator's constants, not against a
        version typed here. A hand-typed floor (`^0.17.`) is a second table:
        it went stale the day the pairs moved to a core peer floor of 0.18.1,
        and reddened this test for the pin that FIXED the e2e."""
        from stapel_tools.create_project import (
            FRONTEND_REACT_CORE_DEPS,
            FRONTEND_ROUTER_DEPS,
            FRONTEND_SHELL_REACT_VERSION,
        )

        pkg = json.loads(_plan()["files"]["package.json"])
        deps = pkg["dependencies"]
        for key in STOREFRONT_PAIRS:
            info = FRONTEND_REACT_LIBS[key]
            assert deps[info["package"]] == f'^{info["version"]}'
        assert deps["@stapel/shell-react"] == f"^{FRONTEND_SHELL_REACT_VERSION}"
        assert deps["@stapel/core"] == f'^{FRONTEND_REACT_CORE_DEPS["@stapel/core"]}'
        assert deps["react-router"] == f'^{FRONTEND_ROUTER_DEPS["react-router"]}'

    def test_vite_proxy_never_claims_a_bare_module_root(self):
        """`location /listings` is a PREFIX match: a bare rule sends
        `/listings/12345` to the backend and a listing page answers JSON."""
        src = _plan()["files"]["vite.config.ts"]
        for key in ("listings", "search", "categories", "chat", "profiles"):
            assert f'"/{key}/": {{' not in src, key
            assert f'"/{key}/api/"' in src, key

    def test_reserved_paths_never_lists_a_bare_module_root(self):
        data = json.loads(_plan()["files"]["reserved-paths.json"])
        prefixes = data["reservedPathPrefixes"]
        assert "/listings" not in prefixes
        assert "/listings/api" in prefixes
        assert {"/admin", "/staticfiles", "/media"} <= set(prefixes)

    def test_extra_prefixes_reach_both_surfaces(self):
        plan = _plan(extra_prefixes=["gdpr", "geo"])
        assert "/gdpr/api" in json.loads(plan["files"]["reserved-paths.json"])[
            "reservedPathPrefixes"
        ]
        assert '"/geo/api/"' in plan["files"]["vite.config.ts"]

    def test_eslint_points_both_data_driven_rules_at_data(self):
        """A gate that cannot fail reads exactly like one that passes."""
        src = _plan()["files"]["eslint.config.js"]
        assert 'reservedPathsFile: "./reserved-paths.json"' in src
        assert "i18nKeys: [" in src
        assert '"storefront.gate.retry"' in src

    def test_theme_and_tokens_are_wired(self):
        files = _plan()["files"]
        theme = json.loads(files["stapel.theme.json"])
        assert "ramps" in theme and "core" in theme
        pkg = json.loads(files["package.json"])
        assert "stapel-tokens" in pkg["scripts"]["gen:tokens"]

    def test_every_file_the_contract_names_is_emitted(self):
        """Spec §6.1's list, item by item — this is the closed contract the
        wiring stage is measured against."""
        files = _plan()["files"]
        for rel in (
            "package.json", "tsconfig.json", "tsconfig.node.json", "vite.config.ts",
            "index.html", "eslint.config.js", ".gitignore",
            "src/modules.tsx", "src/mandateSource.ts", "src/nav.generated.ts",
            "src/routes.tsx", "src/MemberGate.tsx",
            "stapel.theme.json", "stapel.nav.json", "reserved-paths.json",
            "src/NavPlaceholder.tsx",
        ):
            assert rel in files, rel


class TestRefusals:
    def test_unknown_pair_is_named(self):
        with pytest.raises(SurfaceError) as excinfo:
            _plan(pairs=["search", "nosuchthing"])
        assert "nosuchthing" in str(excinfo.value)

    def test_public_surface_without_pairs_is_refused(self, tmp_path):
        with pytest.raises(SurfaceError):
            main([str(tmp_path), "--surface", "public"])

    def test_pair_flags_are_refused_on_the_delivery_half(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        with pytest.raises(SurfaceError):
            main([str(tmp_path), "--pairs", "search"])


class TestWriter:
    def test_it_creates_the_repository_and_then_the_delivery_half(self, tmp_path):
        repo = tmp_path / "acme-storefront"
        rc = main([
            str(repo), "--surface", "public", "--pairs", ",".join(STOREFRONT_PAIRS),
            "--doc-type", "listing", "--ci", "github",
        ])
        assert rc == 0
        # source half
        assert (repo / "src" / "routes.tsx").is_file()
        # delivery half — the dist-carrier, not an nginx image
        dockerfile = (repo / "Dockerfile").read_text()
        assert "AS export" in dockerfile
        # a dist CARRIER: the fleet's own nginx stays the single boundary
        # owning reserved paths, TLS, the proxy table and the cache canon.
        assert "FROM nginx" not in dockerfile
        workflow = (repo / ".github" / "workflows" / "publish-frontend.yml").read_text()
        assert "sha-$(echo" in workflow
        assert ":latest" not in workflow

    def test_named_gaps_are_reported_to_the_operator(self, tmp_path, capsys):
        """With the composer's member pairs missing, the operator is TOLD which
        screen was emitted as a gap — the full storefront selection now mounts
        every declared screen, so the gap is provoked by leaving cdn out."""
        out = write_public_surface(
            tmp_path / "repo",
            name="acme-storefront",
            title="Acme",
            pairs=["auth", "profiles", "categories", "listings"],
            locale="en",
            realtime=False,
            extra_prefixes=[],
            doc_type="listing",
        )
        assert any("NAMED GAPS" in line and "listings.compose" in line for line in out)

    def test_the_full_selection_leaves_no_named_gap(self, tmp_path):
        """The scripted storefront mounts every screen its pairs declare. This
        is the assertion that would have caught "the app is a library": before
        the composer was wired, the one screen that lists something was a
        placeholder."""
        out = write_public_surface(
            tmp_path / "repo",
            name="acme-storefront",
            title="Acme",
            pairs=STOREFRONT_PAIRS,
            locale="en",
            realtime=False,
            extra_prefixes=[],
            doc_type="listing",
        )
        assert not any("NAMED GAPS" in line for line in out)


# ── the integration gate ─────────────────────────────────────────────────────

SLOW = os.environ.get("STAPEL_SLOW_TESTS") == "1"


@pytest.mark.slow
@pytest.mark.skipif(
    not SLOW,
    reason=(
        "installs the real npm tree for nine published pairs and runs tsc + "
        "vite + eslint over the result. It is the only test that proves the "
        "generated container BUILDS rather than merely looks right, and it is "
        "opt-in because it costs a full npm install (measured: ~16 min cold, "
        "~50s warm with a populated cache) and needs network. Run it with "
        "STAPEL_SLOW_TESTS=1."
    ),
)
def test_generated_storefront_installs_typechecks_builds_and_lints(tmp_path):
    repo = tmp_path / "acme-storefront"
    assert main([
        str(repo), "--surface", "public", "--pairs", ",".join(STOREFRONT_PAIRS),
        "--doc-type", "listing", "--ci", "github", "--name", "acme-storefront",
    ]) == 0

    npm = shutil.which("npm")
    assert npm, "npm is required for this test"

    def run(*args, timeout=1800):
        started = time.monotonic()
        proc = subprocess.run(
            args, cwd=repo, capture_output=True, text=True, timeout=timeout
        )
        elapsed = time.monotonic() - started
        assert proc.returncode == 0, (
            f"{' '.join(args)} failed in {elapsed:.0f}s\n"
            f"--- stdout ---\n{proc.stdout[-6000:]}\n"
            f"--- stderr ---\n{proc.stderr[-6000:]}"
        )
        return proc

    run(npm, "install", "--no-audit", "--no-fund")
    run(npm, "exec", "--", "tsc", "--noEmit")
    run(npm, "exec", "--", "vite", "build")
    run(npm, "exec", "--", "eslint", ".")
    assert (repo / "dist" / "index.html").is_file()
