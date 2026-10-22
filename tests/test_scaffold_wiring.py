"""Selection-derived wiring (stapel_tools._wiring) and the preset gate.

AUDIT-BRICKS-2 (2026-10-22), the scaffold half Brigade AC deferred:

* SO-1  a composite's preset.SETTINGS_DEFAULTS reach the generated settings;
* BK-1/2/3, SO-3  every composite preset equals the registry
  (stapel_tools.preset_gate), and the registry fixes what it flagged;
* SE-1  stapel-search is a registry lib, and every frontend composite pair
  has its backend in the selection;
* CH-8  chat brings stapel-realtime, the channels extra, CHANNEL_LAYERS, the
  signal transport and a websocket ASGI router;
* MD-1  CELERY_BEAT_SCHEDULE from the moderation and search beat helpers;
* GD-3  stapel-gdpr's closure guard in MIDDLEWARE;
* pins at the releases of the same wave.

The structural tests read generated files; the ``live`` class boots each
composite's generated minimal project in a subprocess (its own `manage.py
check` and settings), with the composites installed from the ``test`` extra.
"""
from __future__ import annotations

import os
import subprocess
import sys
import types

import pytest
from siblings import requires

from stapel_tools import _wiring
from stapel_tools.create_project import (
    FRONTEND_COMPOSITES,
    STAPEL_LIBS,
    _expand_with_requires,
    create_project,
)
from stapel_tools.preset_gate import composite_keys, frontend_drift, load_preset, preset_drift

COMPOSITES = ["shop", "classified", "booking", "social"]


def _create(tmp_path, name, project_type, modules):
    create_project(
        name=name,
        project_type=project_type,
        title=name,
        url="https://x.dev",
        company_name="X",
        company_email="x@x.dev",
        modules=["core", *modules],
        output_dir=tmp_path,
        use_submodules=False,
        init_git=False,
    )
    return tmp_path / name


def _settings_text(proj, project_type):
    if project_type == "minimal":
        return (proj / "config" / "settings.py").read_text()
    return (proj / f"svc-{proj.name}" / "config" / "settings" / "base.py").read_text()


# ─── SO-1: preset SETTINGS_DEFAULTS ─────────────────────────────────────────


class TestPresetDefaults:
    def test_every_selected_composite_preset_is_merged(self, tmp_path):
        proj = _create(tmp_path, "cls", "minimal", ["classified"])
        text = _settings_text(proj, "minimal")
        # shop is a member of classified: both presets, shop first.
        assert '_stapel_merge_preset_defaults(globals(), ["stapel_shop.preset", "stapel_classified.preset"])' in text

    def test_monolith_merges_too(self, tmp_path):
        proj = _create(tmp_path, "soc", "monolith", ["social"])
        assert '["stapel_social.preset"]' in _settings_text(proj, "monolith")

    def test_no_composite_no_block(self, tmp_path):
        proj = _create(tmp_path, "plain", "minimal", ["reviews"])
        assert "_stapel_merge_preset_defaults" not in _settings_text(proj, "minimal")

    def test_merge_semantics_explicit_wins_later_refines(self):
        """Run the rendered block itself against two fake presets."""
        a = types.ModuleType("_fake_preset_a")
        a.SETTINGS_DEFAULTS = {"STAPEL_REVIEWS": {"TARGET_TYPES": {"listing": {"v": "a"}}, "X": 1}}
        b = types.ModuleType("_fake_preset_b")
        b.SETTINGS_DEFAULTS = {"STAPEL_REVIEWS": {"TARGET_TYPES": {"listing": {"v": "b"}, "seller": {}}}}
        sys.modules.update({a.__name__: a, b.__name__: b})
        try:
            block = _wiring._PRESET_MERGE.format(
                modules='"_fake_preset_a", "_fake_preset_b"', composites_doc="a -> b",
            )
            ns = {"STAPEL_REVIEWS": {"TARGET_TYPES": {"profile": {"v": "explicit"}}, "X": 2}}
            exec(block, ns)  # noqa: S102 - the generated settings code under test
        finally:
            del sys.modules[a.__name__], sys.modules[b.__name__]
        reviews = ns["STAPEL_REVIEWS"]
        assert reviews["X"] == 2  # explicit key wins
        assert reviews["TARGET_TYPES"]["listing"] == {"v": "b"}  # later preset refines
        assert set(reviews["TARGET_TYPES"]) == {"listing", "seller", "profile"}  # registries merge


# ─── preset gate (BK-1/2/3, SO-3) ───────────────────────────────────────────


class TestPresetGate:
    def test_composites_are_flagged_in_the_registry(self):
        assert composite_keys() == COMPOSITES

    @pytest.mark.parametrize("key", COMPOSITES)
    @requires("stapel_shop", "stapel_classified", "stapel_booking", "stapel_social")
    def test_installed_preset_equals_registry(self, key):
        assert preset_drift(key, load_preset(key)) == []

    def test_the_gate_sees_a_drifted_preset(self):
        """Inversion: the 0.1.5 booking preset (no categories, bare listings/)."""
        old = types.SimpleNamespace(
            INSTALLED_APPS=["stapel_calendar", "stapel_listings", "stapel_booking"],
            URL_INCLUDES=[("calendar/", "stapel_calendar.urls"), ("listings/", "stapel_listings.urls")],
            SETTINGS_DEFAULTS={},
        )
        findings = preset_drift("booking", old)
        assert any("lacks ['stapel_categories']" in f for f in findings)
        assert any("'listings/', the registry at 'listings/api/'" in f for f in findings)

    def test_the_gate_sees_an_order_drift(self):
        members = _expand_with_requires(["social"], companions=False)
        apps = [STAPEL_LIBS[m]["dir"] for m in members if STAPEL_LIBS[m].get("django_app", True)]
        drifted = types.SimpleNamespace(
            INSTALLED_APPS=list(reversed(apps)),
            URL_INCLUDES=[("chat/", "stapel_chat.urls"), ("profiles/api/", "stapel_profiles.urls"),
                          ("reviews/", "stapel_reviews.urls")],
            SETTINGS_DEFAULTS={},
        )
        assert any("order" in f for f in preset_drift("social", drifted))

    def test_listings_needs_its_category_provider(self):
        assert "categories" in _expand_with_requires(["listings"])

    def test_classified_mounts_its_own_surface(self):
        assert STAPEL_LIBS["classified"]["http"] is True
        assert STAPEL_LIBS["classified"]["url_prefix"] == "classified/api/"

    @pytest.mark.parametrize("name", sorted(FRONTEND_COMPOSITES))
    def test_every_frontend_pair_has_a_backend(self, name):
        assert frontend_drift(name) == []


# ─── SE-1: search ───────────────────────────────────────────────────────────


class TestSearch:
    def test_search_is_a_registry_lib(self):
        from stapel_tools._url_mounts import url_mount_for

        assert STAPEL_LIBS["search"]["pin"]
        assert url_mount_for("stapel_search") == "search/"

    @pytest.mark.parametrize("key", ["shop", "classified", "booking"])
    def test_storefront_composites_select_search(self, key):
        assert "search" in _expand_with_requires([key])

    def test_minimal_gets_the_sqlite_backend(self, tmp_path):
        proj = _create(tmp_path, "shp", "minimal", ["shop"])
        assert '"BACKEND": \'stapel_search.backends.naive.NaiveSearchBackend\'' in _settings_text(proj, "minimal")

    def test_listings_without_moderation_auto_approves(self, tmp_path):
        proj = _create(tmp_path, "bkg", "minimal", ["booking"])
        assert '"AUTO_APPROVE_ON_PUBLISH": True' in _settings_text(proj, "minimal")

    def test_listings_with_moderation_waits_for_the_verdict(self, tmp_path):
        proj = _create(tmp_path, "cls", "minimal", ["classified"])
        assert "AUTO_APPROVE_ON_PUBLISH" not in _settings_text(proj, "minimal")


# ─── CH-8: realtime ─────────────────────────────────────────────────────────


class TestRealtime:
    def test_chat_selects_realtime(self):
        assert "realtime" in _expand_with_requires(["chat"])
        assert "realtime" in _expand_with_requires(["video"])

    def test_monolith_wiring(self, tmp_path):
        proj = _create(tmp_path, "soc", "monolith", ["social"])
        svc = proj / "svc-soc"
        base = _settings_text(proj, "monolith")
        assert '"stapel_realtime",' in base
        assert '"SIGNAL_TRANSPORT": "channels"' in base
        assert "channels_redis.core.RedisChannelLayer" in base
        assert 'ASGI_APPLICATION = "config.asgi.application"' in base
        assert '"URL_PREFIX": "ws"' in base
        asgi = (svc / "config" / "asgi.py").read_text()
        assert "build_websocket_application(http_application=http_application)" in asgi
        reqs = (svc / "requirements.txt").read_text()
        assert "stapel-realtime[channels,redis]>=" in reqs
        assert "daphne>=4,<5" in reqs
        assert "RUN_CMD=daphne -b 0.0.0.0 -p 8000 config.asgi:application" in (proj / ".env.example").read_text()
        dev = (svc / "config" / "settings" / "dev.py").read_text()
        assert 'INSTALLED_APPS = ["daphne", *INSTALLED_APPS]' in dev
        nginx = (proj / "service-configs" / "nginx-local" / "default.conf.template").read_text()
        ws_block = nginx.split("location ^~ /ws/chat/ {", 1)[1].split("}", 1)[0]
        assert "proxy_set_header Upgrade $http_upgrade;" in ws_block

    def test_minimal_wiring(self, tmp_path):
        proj = _create(tmp_path, "soc", "minimal", ["social"])
        text = _settings_text(proj, "minimal")
        assert "channels.layers.InMemoryChannelLayer" in text
        assert "build_websocket_application" in (proj / "config" / "asgi.py").read_text()

    def test_no_socket_module_no_realtime(self, tmp_path):
        proj = _create(tmp_path, "plain", "monolith", ["reviews"])
        svc = proj / "svc-plain"
        assert "CHANNEL_LAYERS" not in _settings_text(proj, "monolith")
        assert "build_websocket_application" not in (svc / "config" / "asgi.py").read_text()
        assert "gunicorn config.wsgi:application" in (proj / ".env.example").read_text()


# ─── MD-1: beat schedule ────────────────────────────────────────────────────


class TestBeat:
    def test_monolith_schedule(self, tmp_path):
        proj = _create(tmp_path, "cls", "monolith", ["classified"])
        text = _settings_text(proj, "monolith")
        assert "from stapel_moderation.beat import get_moderation_beat_schedule" in text
        assert "from stapel_search.tasks import get_search_beat_schedule" in text
        # Any process that runs beat owes core's Task sweep (stapel_taskstore.W001).
        assert "get_taskstore_beat_schedule()" in text
        assert "CELERY_BEAT_SCHEDULE = {" in text

    def test_minimal_schedule_and_celery(self, tmp_path):
        proj = _create(tmp_path, "mod", "minimal", ["moderation"])
        assert "**get_moderation_beat_schedule()," in _settings_text(proj, "minimal")
        assert "celery>=5.3" in (proj / "requirements.txt").read_text()

    def test_no_beat_module_no_schedule(self, tmp_path):
        proj = _create(tmp_path, "plain", "minimal", ["reviews"])
        assert "CELERY_BEAT_SCHEDULE" not in _settings_text(proj, "minimal")


# ─── GD-3: closure guard ────────────────────────────────────────────────────


class TestClosureGuard:
    @pytest.mark.parametrize("project_type", ["monolith", "minimal"])
    def test_guard_is_appended(self, tmp_path, project_type):
        # auth: an owner library, so the derived DATA_OWNERS map is not empty.
        proj = _create(tmp_path, "g", project_type, ["auth", "gdpr"])
        text = _settings_text(proj, project_type)
        tail = text.split("MIDDLEWARE = [\n    *MIDDLEWARE,", 1)[1]
        assert '"stapel_gdpr.guards.AccountClosureGuardMiddleware",' in tail

    def test_absent_without_gdpr(self, tmp_path):
        proj = _create(tmp_path, "g", "minimal", ["reviews"])
        assert "AccountClosureGuardMiddleware" not in _settings_text(proj, "minimal")


# ─── pins ───────────────────────────────────────────────────────────────────

#: The releases of the 2026-10-22 brick-audit fixes (Brigades V, AC, AE). The
#: registry may move past them, never stay below.
WAVE_FLOORS = {
    "core": "0.93.0", "auth": "0.49.0", "cdn": "0.29.0", "notifications": "0.28.0",
    "profiles": "0.22.1", "translate": "0.8.5", "workspaces": "0.37.0", "gdpr": "0.9.0",
    "agent": "0.38.1", "chat": "0.11.0", "listings": "0.26.0", "moderation": "0.8.4",
    "reviews": "0.8.0", "vocabularies": "0.4.4", "realtime": "0.2.3", "search": "0.18.2",
    "shop": "0.2.37", "classified": "0.11.1", "booking": "0.1.6", "social": "0.1.8",
}


def _v(text):
    return tuple(int(p) for p in text.split("."))


@pytest.mark.parametrize("key", sorted(WAVE_FLOORS))
def test_pin_is_at_least_the_wave_release(key):
    assert _v(STAPEL_LIBS[key]["pin"]) >= _v(WAVE_FLOORS[key])


# ─── live: the generated composite projects boot ────────────────────────────

_PROBE = r"""
import os, sys, django
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
sys.path.insert(0, os.getcwd())
django.setup()
from django.conf import settings
from django.core.management import call_command
key = sys.argv[1]
call_command("check", fail_level="ERROR")
if key == "social":
    assert "profile" in settings.STAPEL_REVIEWS["TARGET_TYPES"], settings.STAPEL_REVIEWS
    assert settings.CHANNEL_LAYERS["default"]
    assert settings.STAPEL_COMM["SIGNAL_TRANSPORT"] == "channels"
if key == "classified":
    tasks = {e["task"] for e in settings.CELERY_BEAT_SCHEDULE.values()}
    assert "stapel_moderation.tasks.rearm_active_sanctions" in tasks, tasks
    assert "stapel_search.tasks.search_reindex_stale" in tasks, tasks
    assert settings.STAPEL_REVIEWS["TARGET_TYPES"]["listing"]["owner_key_for"]
if key == "booking":
    assert settings.STAPEL_LISTINGS["AUTO_APPROVE_ON_PUBLISH"] is True
print("ok", key)
"""


class TestLiveComposites:
    @pytest.mark.parametrize("key", COMPOSITES)
    @requires(
        "stapel_shop", "stapel_classified", "stapel_booking", "stapel_social",
        "stapel_search", "stapel_moderation", "stapel_realtime", "channels",
    )
    def test_generated_minimal_project_boots(self, tmp_path, key):
        proj = _create(tmp_path, f"live{key}", "minimal", [key])
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
        proc = subprocess.run(
            [sys.executable, "-c", _PROBE, key],
            cwd=proj, env=env, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
        assert f"ok {key}" in proc.stdout


def test_gdpr_inventory_covers_composite_members(tmp_path):
    """The derived DATA_OWNERS map is read over the expanded selection: social's
    members own chat, profile, reviews and media stores (gdpr.E002/E010)."""
    proj = _create(tmp_path, "sg", "minimal", ["social", "gdpr"])
    text = _settings_text(proj, "minimal")
    block = text.split("STAPEL_GDPR = {", 1)[1].split("\n}", 1)[0]
    for owner in ("'chat'", "'profile'", "'reviews'", "'media'"):
        assert owner in block, block


# ─── E2E-TIER0 D9: the local compose DB race ────────────────────────────────


def test_databases_are_ensured_against_the_real_server(tmp_path):
    """The initdb temp server answers pg_isready on the socket and is killed
    mid-script; the real one listens on TCP. Wait for that, and retry."""
    proj = _create(tmp_path, "db", "monolith", ["reviews"])
    for name in ("docker-compose.base.yml", "docker-compose.local.yml"):
        text = (proj / name).read_text()
        if "ensure-databases.sh" not in text:
            continue
        assert "until pg_isready -h 127.0.0.1 -U" in text, name
        assert "until /usr/local/bin/ensure-databases.sh; do sleep 1; done" in text, name


# ─── E2E-TIER0 D14: reference data at bootstrap ─────────────────────────────


def test_bootstrap_seeds_currencies_and_a_starter_catalog(tmp_path):
    import json

    proj = _create(tmp_path, "sd", "monolith", ["shop"])
    svc = proj / "svc-sd"
    boot = (svc / "bootstrap.sh").read_text()
    assert 'optional "seed-load-catalog" python manage.py load_catalog --seed-if-empty' in boot
    assert 'optional "seed-load-default-currencies" python manage.py load_default_currencies' in boot
    assert boot.index("seed-load-catalog") < boot.rindex("bootstrap_done")
    cats = json.loads((svc / "fixtures" / "catalog" / "categories.json").read_text())
    assert {c["slug"] for c in cats} >= {"electronics", "phones"}
    assert json.loads((svc / "fixtures" / "catalog" / "features.json").read_text()) == []


def test_no_seed_without_the_module(tmp_path):
    proj = _create(tmp_path, "ns", "monolith", ["reviews"])
    assert "seed-" not in (proj / "svc-ns" / "bootstrap.sh").read_text()
    assert not (proj / "svc-ns" / "fixtures").exists()


# ─── E2E-TIER0 D1/D4: mandate seam and listing photos in modules.tsx ───────


@pytest.mark.parametrize("key", ["shop", "classified", "booking"])
def test_modules_tsx_mounts_the_mandate_and_resolves_photos(tmp_path, key):
    proj = _create(tmp_path, f"fe{key}", "monolith", [key])
    src = proj / "frontend" / "src"
    modules = (src / "modules.tsx").read_text()
    assert "<MandateGateway>{children}</MandateGateway>" in modules
    assert "<MandateProvider source={source}>" in modules
    assert "resolveImage: listingImageFor" in modules
    assert "export function useStorefrontMandateSource" in (src / "mandateSource.ts").read_text()
    assert "export function listingImageFor" in (src / "lib" / "cdn.ts").read_text()


def test_workspaces_keeps_its_own_mandate(tmp_path):
    proj = _create(tmp_path, "fews", "monolith", ["auth", "workspaces"])
    assert "MandateGateway" not in (proj / "frontend" / "src" / "modules.tsx").read_text()


def test_category_page_fills_the_listing_grid(tmp_path):
    proj = _create(tmp_path, "cg", "monolith", ["shop"])
    page = (proj / "frontend" / "src" / "pages" / "CategoryPageRoute.tsx").read_text()
    assert 'import { ListingBoardPane } from "@stapel/listings-react/default";' in page
    assert "renderListings={(category) =>" in page
    assert "categoryIds={[String(category.id)]}" in page


def test_calendar_gets_the_viewer_and_a_people_provider(tmp_path):
    proj = _create(tmp_path, "cv", "monolith", ["booking"])
    page = (proj / "frontend" / "src" / "pages" / "CalendarRoute.tsx").read_text()
    assert "useAuthSessionState()" in page
    assert "<CalendarPeopleProvider" in page
    assert "{...(viewerId !== undefined ? { viewerId } : {})}" in page
