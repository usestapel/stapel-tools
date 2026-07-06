"""stapel-analytics-report tests (frontend-guardrails §3.3).

Exercised on a fixture mini-workspace (tests/fixtures/analytics_ws) with one app
package (shop-app) and one library pair (@stapel/foo-react): report.json
structure/counts, app|library slicing, cross-file (+ alias) call-site resolution,
canonical flow join with gated_by badge, the untracked/disabled sections, and
graceful degradation when a package has no generated events.json.
"""
import json
import os
import shutil
from pathlib import Path

from stapel_tools.analytics_report import (
    _load_backend_flows,
    build_report,
    discover_packages,
    main,
    render_html,
    render_markdown,
)

FIX = Path(__file__).parent / "fixtures" / "analytics_ws"
BACKEND = FIX / "backend-flows.json"


def _report(ws=FIX, backend=BACKEND):
    pkgs = discover_packages(str(ws))
    bf = _load_backend_flows(str(backend)) if backend else None
    return build_report(str(ws), pkgs, backend_flows=bf, tool_version="test")


# ── discovery + slicing ──────────────────────────────────────────────────────


def test_discovers_both_packages():
    pkgs = discover_packages(str(FIX))
    names = {os.path.basename(p) for p in pkgs}
    assert names == {"shop-app", "foo-react"}


def test_slice_split_app_vs_library():
    s = _report()["summary"]
    assert s["app"]["packages"] == ["shop-app"]
    assert s["library"]["packages"] == ["@stapel/foo-react"]
    # app owns the defined events + emit sites; library owns the flow funnel.
    assert s["app"]["defined_events"] == 2
    assert s["app"]["emit_sites"] == 4
    assert s["library"]["flow_funnels"] == 1
    assert s["library"]["defined_events"] == 0


# ── coverage summary (clickable outcomes by marker, §3.3) ─────────────────────


def test_coverage_counts_by_marker_type():
    cov = _report()["summary"]["coverage"]
    assert cov == {"tracked": 4, "flow": 1, "untracked": 1, "disabled": 1}


# ── cross-file (and alias) call-site → event resolution ───────────────────────


def test_cross_file_and_alias_resolution():
    events = {e["name"]: e for e in _report()["events"]}
    plan = events["pricing.plan.selected"]
    files = sorted((e["file"].split("/")[-1], e["wrapper"]) for e in plan["emits"])
    # tracked() in PlanCard, trackedSubmit() in PlanCard, tracked(ps) via ALIAS
    # in Upsell — all resolve to the same event across files.
    assert files == [
        ("PlanCard.tsx", "tracked"),
        ("PlanCard.tsx", "trackedSubmit"),
        ("Upsell.tsx", "tracked"),
    ]
    assert plan["flow"] == "billing.checkout"
    assert plan["props"]["plan"]["options"] == ["free", "pro"]
    # component context is captured from the nearest enclosing PascalCase fn.
    assert {e["component"] for e in plan["emits"]} == {"PlanCard", "Upsell"}
    # analytics.track(appOpened) in a third file resolves too.
    opened = events["app.opened"]
    assert [e["file"].split("/")[-1] for e in opened["emits"]] == ["app.tsx"]


# ── canonical flow report: backend prose + frontend coverage + gated badge ────


def test_flow_join_and_gated_badge():
    flows = {f["id"]: f for f in _report()["flows"]}
    # billing.checkout: no frontend funnel, but a linked app event + gated_by.
    billing = flows["billing.checkout"]
    assert billing["gated_by"] == ["BILLING_CHECKOUT"]
    assert billing["app_events"] == ["pricing.plan.selected"]
    assert billing["title"] == "Billing checkout"
    # shop.checkout: covered by the pair funnel + a name-matched machine.
    shop = flows["shop.checkout"]
    assert shop["frontend"]["funnel"] == "flow.shop.checkout.<step>"
    assert shop["frontend"]["packages"] == ["@stapel/foo-react"]
    assert shop["frontend"]["machines"] == ["createCheckoutFlow"]
    assert shop.get("gated_by") is None  # no field → always-on


# ── explicit sections ─────────────────────────────────────────────────────────


def test_untracked_and_disabled_sections():
    r = _report()
    assert len(r["untracked"]) == 1
    assert r["untracked"][0]["reason"] == "visual accordion toggle, not a funnel step"
    assert r["untracked"][0]["slice"] == "app"
    assert len(r["disabled"]) == 1
    assert r["disabled"][0]["rule"] == "stapel/clickable-needs-event"
    assert r["disabled"][0]["description"] == "legacy widget, tracked upstream"


# ── rendering ─────────────────────────────────────────────────────────────────


def test_markdown_has_all_sections():
    md = render_markdown(_report())
    assert "# Analytics report" in md
    assert "## Events — app" in md
    assert "pricing.plan.selected" in md
    assert "## Flows (canonical backend + frontend coverage)" in md
    assert "[gated: BILLING_CHECKOUT]" in md
    assert '## Explicitly untracked (data-analytics="none")' in md
    assert "## Explicitly disabled (eslint-disable with description)" in md
    assert "visual accordion toggle" in md


def test_html_is_self_contained():
    html = render_html(_report())
    assert html.startswith("<!doctype html>")
    assert "<style>" in html
    assert "gated: BILLING_CHECKOUT" in html
    # no external resource references — CSP-safe, embeddable in Studio passport.
    assert "http://" not in html and "https://" not in html
    assert "src=" not in html


# ── snapshot of the machine-readable report (stable keys) ─────────────────────


def test_report_json_snapshot_shape():
    r = _report()
    assert r["$schema"] == "stapel-analytics-report/v1"
    assert set(r.keys()) == {
        "$schema",
        "generated_by",
        "workspace",
        "summary",
        "events",
        "flow_funnels",
        "flows",
        "untracked",
        "disabled",
    }
    # events sorted by (slice, name); app before library, stable order.
    assert [(e["slice"], e["name"]) for e in r["events"]] == [
        ("app", "app.opened"),
        ("app", "pricing.plan.selected"),
    ]
    assert [f["flow"] for f in r["flow_funnels"]] == ["shop.checkout"]
    assert [f["id"] for f in r["flows"]] == ["billing.checkout", "shop.checkout"]


# ── graceful degradation: no events.json (must not crash) ─────────────────────


def test_degrades_without_events_json(tmp_path):
    dst = tmp_path / "ws"
    shutil.copytree(FIX, dst)
    # remove BOTH generated events.json files
    (dst / "packages/shop-app/src/analytics/generated/events.json").unlink()
    (dst / "packages/foo-react/src/analytics/generated/events.json").unlink()

    r = _report(ws=dst)  # must not raise
    s = r["summary"]
    # shop-app has no other catalog → flagged; foo-react degrades to its
    # manifest.events section, so it is NOT flagged (fallback still covers it).
    assert s["packages_missing_events_json"] == ["shop-app"]
    # ...yet call sites still resolve from source-derived bindings (no meta).
    events = {e["name"]: e for e in r["events"]}
    assert "pricing.plan.selected" in events
    assert len(events["pricing.plan.selected"]["emits"]) == 3
    assert events["pricing.plan.selected"]["description"] == ""  # no meta available
    # foo-react funnel now comes from manifest.events (events.json gone).
    assert [f["flow"] for f in r["flow_funnels"]] == ["shop.checkout"]
    # markers survive regardless of generated artifacts.
    assert r["summary"]["coverage"]["untracked"] == 1
    assert r["summary"]["coverage"]["disabled"] == 1


def test_no_backend_flows_still_reports_pair_flows():
    # without a backend flows.json, the pair's own flows.json is the canonical
    # source (i18n-key titles, no prose) — still joined with frontend coverage.
    flows = {f["id"]: f for f in _report(backend=None)["flows"]}
    assert set(flows) == {"shop.checkout"}
    assert flows["shop.checkout"]["frontend"]["machines"] == ["createCheckoutFlow"]


# ── CLI integration ───────────────────────────────────────────────────────────


def test_cli_writes_three_artifacts(tmp_path):
    out = tmp_path / "out"
    rc = main([str(FIX), "--backend-flows", str(BACKEND), "--out", str(out)])
    assert rc == 0
    for fname in ("report.json", "report.md", "report.html"):
        assert (out / fname).is_file()
    data = json.loads((out / "report.json").read_text())
    assert data["$schema"] == "stapel-analytics-report/v1"
