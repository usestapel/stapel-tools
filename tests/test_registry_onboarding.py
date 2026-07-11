"""STAPEL_LIBS second-wave onboarding tests (static-scaffold-and-config.md §2).

The 2026-07-11 wave brought 14 sibling checkouts into the registry: agent,
attributes, calendar, categories, chat, currencies, geo, listings, mailtrap,
recordings, reviews, tasks, vault, video. These tests lock the registry
contract every downstream (create_project / assemble_scaffold) reads:

* each new lib resolves (dir/pin/requires present, dir matches the
  ``stapel_<mod>`` convention);
* the two headless libs (attributes, vault — no urls.py, ``django_app`` /
  ``http`` False) are mounted NOWHERE (absent from INSTALLED_APPS and urls),
  never invented a route;
* http libs land in INSTALLED_APPS AND urls, under the lib's own
  ``url_prefix`` when it declares one (calendar/chat/… bake ``api/`` into
  their own urls.py so they mount at bare ``<mod>/``), else the legacy
  ``<key>/api/`` mount;
* the ``requires`` topology is acyclic and every dependency is itself in the
  registry, and requesting a lib with a hard requires pulls its dependency in
  (categories -> attributes) — a pip-resolvable project;
* INSTALLED_APPS is emitted in registry order regardless of request order.

These are pure-generation assertions (no import of the libs themselves), so
they run in any interpreter — including a bare stapel-tools checkout.
"""
from stapel_tools.assemble_scaffold import assemble_scaffold
from stapel_tools.create_project import STAPEL_LIBS, _expand_with_requires

SECOND_WAVE = [
    "agent", "attributes", "calendar", "categories", "chat", "currencies",
    "geo", "listings", "mailtrap", "recordings", "reviews", "tasks", "vault",
    "video",
]

#: The libs with no urls.py of their own — mounted in neither INSTALLED_APPS
#: (attributes is a pure library; vault is a secrets-provider facade) nor urls.
HEADLESS = {"attributes", "vault"}


class TestRegistryResolves:
    def test_every_second_wave_lib_is_registered(self):
        for key in SECOND_WAVE:
            assert key in STAPEL_LIBS, key

    def test_dir_follows_stapel_underscore_convention(self):
        for key in SECOND_WAVE:
            assert STAPEL_LIBS[key]["dir"] == f"stapel_{key}", key

    def test_every_lib_carries_pin_requires_and_http_flag(self):
        for key in SECOND_WAVE:
            info = STAPEL_LIBS[key]
            assert "pin" in info and info["pin"], key
            assert "ahead_of_pypi" in info, key
            assert isinstance(info.get("requires", []), list), key
            # http defaults True; headless libs must declare it False.
            assert (key in HEADLESS) == (info.get("http", True) is False), key

    def test_headless_libs_have_no_url_prefix(self):
        for key in HEADLESS:
            assert STAPEL_LIBS[key].get("http", True) is False, key
            assert STAPEL_LIBS[key].get("django_app", True) is False, key
            assert "url_prefix" not in STAPEL_LIBS[key], key

    def test_http_libs_declare_a_canonical_prefix(self):
        for key in SECOND_WAVE:
            if key in HEADLESS:
                continue
            assert STAPEL_LIBS[key].get("http", True) is True, key
            # second-wave http libs bake api/ into their own urls -> bare mount
            assert STAPEL_LIBS[key]["url_prefix"] == f"{key}/", key


class TestRequiresTopology:
    def test_every_dependency_is_itself_registered(self):
        for key, info in STAPEL_LIBS.items():
            for dep in info.get("requires", []):
                assert dep in STAPEL_LIBS, f"{key} -> {dep}"

    def test_requires_graph_is_acyclic(self):
        # DFS cycle detection over the requires edges.
        WHITE, GREY, BLACK = 0, 1, 2
        color = {k: WHITE for k in STAPEL_LIBS}

        def visit(node, stack):
            color[node] = GREY
            for dep in STAPEL_LIBS.get(node, {}).get("requires", []):
                assert color.get(dep) != GREY, f"cycle: {stack + [node, dep]}"
                if color.get(dep) == WHITE:
                    visit(dep, stack + [node])
            color[node] = BLACK

        for key in STAPEL_LIBS:
            if color[key] == WHITE:
                visit(key, [])

    def test_dependency_precedes_dependent_in_registry_order(self):
        # Registry (dict) order IS the emission order; a hard dependency must
        # appear before its dependent so INSTALLED_APPS/urls list it first.
        order = list(STAPEL_LIBS)
        for key, info in STAPEL_LIBS.items():
            for dep in info.get("requires", []):
                assert order.index(dep) < order.index(key), f"{dep} after {key}"

    def test_categories_and_listings_require_attributes(self):
        assert STAPEL_LIBS["categories"]["requires"] == ["attributes"]
        assert STAPEL_LIBS["listings"]["requires"] == ["attributes"]

    def test_expand_pulls_in_hard_requires(self):
        # Requesting only "categories" must yield attributes too, in
        # registry order (attributes before categories).
        assert _expand_with_requires(["categories"]) == ["attributes", "categories"]

    def test_expand_is_idempotent_and_order_invariant(self):
        a = _expand_with_requires(["listings", "categories"])
        b = _expand_with_requires(["categories", "listings"])
        assert a == b == ["attributes", "categories", "listings"]


class TestHeadlessMountingInGeneratedProject:
    def test_headless_lib_absent_from_installed_apps_and_urls(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["vault"], output_dir=tmp_path, verify=False
        )
        assert result.libs_applied == ["vault"]
        settings = (result.project_dir / "config" / "settings.py").read_text()
        urls = (result.project_dir / "config" / "urls.py").read_text()
        reqs = (result.project_dir / "requirements.txt").read_text()
        # Still a pip dependency (the facade is installed) — vault is
        # ahead-of-PyPI (unpublished), so it renders as an editable sibling
        # checkout install, not a `name @ git+...` line...
        assert "-e ../stapel-vault" in reqs
        # ...but never mounted as a Django app or a route.
        assert '"stapel_vault"' not in settings
        assert "stapel_vault.urls" not in urls

    def test_attributes_pulled_by_categories_but_mounted_headless(self, tmp_path):
        result = assemble_scaffold(
            "shop", libs=["categories"], output_dir=tmp_path, verify=False
        )
        # attributes is dragged in by categories' hard requires...
        assert set(result.libs_applied) == {"attributes", "categories"}
        settings = (result.project_dir / "config" / "settings.py").read_text()
        urls = (result.project_dir / "config" / "urls.py").read_text()
        # categories is a real app+route; attributes is neither.
        assert '"stapel_categories"' in settings
        assert 'include("stapel_categories.urls")' in urls
        assert '"stapel_attributes"' not in settings
        assert "stapel_attributes.urls" not in urls


class TestInstalledAppsOrderSecondWave:
    def test_apps_emitted_in_registry_order_not_request_order(self, tmp_path):
        # Request in reverse registry order; output must still be registry order.
        result = assemble_scaffold(
            "app", libs=["video", "calendar", "agent"],
            output_dir=tmp_path, verify=False,
        )
        settings = (result.project_dir / "config" / "settings.py").read_text()
        i_agent = settings.index('"stapel_agent"')
        i_cal = settings.index('"stapel_calendar"')
        i_video = settings.index('"stapel_video"')
        assert i_agent < i_cal < i_video

    def test_http_lib_mounts_under_its_own_prefix(self, tmp_path):
        result = assemble_scaffold(
            "app", libs=["calendar", "video"], output_dir=tmp_path, verify=False
        )
        urls = (result.project_dir / "config" / "urls.py").read_text()
        assert 'path("calendar/", include("stapel_calendar.urls"))' in urls
        assert 'path("video/", include("stapel_video.urls"))' in urls
