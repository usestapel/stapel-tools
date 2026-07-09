"""stapel-catalog tests (BACKLOG §33 p.1).

Exercised on fixture capabilities.json documents of different shapes
(tests/fixtures/catalog): a rich module (gated axes + a behavior-only axis +
extension points + requires), a minimal module (no axes), a broken-JSON module
and a repo with no artifact at all. Covers: aggregate totals + module sorting,
graceful skip-with-warning (never a crash) on the malformed/absent sources,
the compact markdown projection (header roll-up, axis table incl. the
`behavior` marker, extension-point names, requires with optional tag), the
curated recipes section, workspace discovery, and byte-for-byte determinism of
catalog.md across two runs.
"""
import json
from pathlib import Path

import pytest

from stapel_tools.catalog import (
    build_catalog,
    discover_workspace,
    load_documents,
    load_recipes,
    main,
    parse_recipes,
    render_markdown,
)

FIX = Path(__file__).parent / "fixtures" / "catalog"
FULL = FIX / "mod-full"
MINI = FIX / "mod-minimal"
BROKEN = FIX / "mod-broken"
ABSENT = FIX / "mod-absent"
RECIPES = FIX / "recipes.yaml"


def _collect_warnings():
    msgs: list[str] = []
    return msgs, msgs.append


# ── loading + graceful degradation ────────────────────────────────────────────


def test_loads_valid_documents():
    docs, skipped = load_documents([FULL, MINI])
    assert [d["module"] for d in docs] == ["stapel-demo", "stapel-mini"]
    assert skipped == []


def test_broken_json_is_skipped_not_fatal():
    msgs, warn = _collect_warnings()
    docs, skipped = load_documents([FULL, BROKEN], warn=warn)
    assert [d["module"] for d in docs] == ["stapel-demo"]
    assert skipped == [str(BROKEN)]
    assert any("not valid JSON" in m for m in msgs)


def test_absent_artifact_is_skipped_with_warning():
    msgs, warn = _collect_warnings()
    docs, skipped = load_documents([ABSENT], warn=warn)
    assert docs == []
    assert skipped == [str(ABSENT)]
    assert any("no capabilities.json" in m for m in msgs)


def test_direct_json_file_path_accepted():
    docs, skipped = load_documents([FULL / "docs" / "capabilities.json"])
    assert [d["module"] for d in docs] == ["stapel-demo"]
    assert skipped == []


# ── aggregate assembly ───────────────────────────────────────────────────────


def test_totals_and_module_sorting():
    # pass out of order → catalog must sort by module name
    docs, _ = load_documents([MINI, FULL])
    catalog = build_catalog(docs)
    assert [d["module"] for d in catalog["modules"]] == ["stapel-demo", "stapel-mini"]
    assert catalog["totals"] == {
        "modules": 2,
        "operations": 10,  # 7 + 3
        "axes": 3,
        "extension_points": 3,  # 2 + 1
    }
    assert catalog["schema_version"] == 1


def test_full_documents_embedded_verbatim():
    docs, _ = load_documents([FULL])
    catalog = build_catalog(docs)
    assert catalog["modules"][0]["axes"][0]["gates"]["operations"] == [
        "demo_api_z_one",
        "demo_api_z_two",
    ]


# ── markdown projection ──────────────────────────────────────────────────────


def test_markdown_header_rollup():
    docs, _ = load_documents([FULL, MINI])
    md = render_markdown(build_catalog(docs))
    assert md.startswith("# Stapel module catalog\n")
    assert "2 modules · 10 operations · 3 axes · 3 extension points." in md


def test_markdown_axis_table_and_behavior_marker():
    docs, _ = load_documents([FULL])
    md = render_markdown(build_catalog(docs))
    # axes sorted by key: ALPHA before STEPUP before ZEBRA
    assert md.index("DEMO_ALPHA") < md.index("DEMO_STEPUP") < md.index("DEMO_ZEBRA")
    assert "| DEMO_ZEBRA | true | 2 |" in md
    assert "| DEMO_ALPHA | false | 1 |" in md
    # behavior-only axis (0 ops but a behavior) is marked, not a bare 0
    assert "| DEMO_STEPUP | false | behavior |" in md


def test_markdown_extension_points_and_requires():
    docs, _ = load_documents([FULL])
    md = render_markdown(build_catalog(docs))
    assert "**Extension points:** DEMO_BACKEND, DEMO_STAGES" in md
    assert "**Requires:** stapel-core, stapel-notifications (optional)" in md


def test_markdown_minimal_module_has_no_table_but_lists_ep():
    docs, _ = load_documents([MINI])
    md = render_markdown(build_catalog(docs))
    assert "| axis |" not in md
    assert "**Extension points:** MINI_SETTINGS" in md


# ── recipes ──────────────────────────────────────────────────────────────────


def test_parse_recipes_inline_and_block_lists():
    recipes = load_recipes(RECIPES)
    # sorted by name: booking before marketplace
    assert [r["name"] for r in recipes] == ["booking", "marketplace"]
    market = recipes[1]
    assert market["modules"] == ["stapel-auth", "stapel-profiles", "stapel-listings"]
    assert market["notes"].startswith("reviews live")
    booking = recipes[0]
    assert booking["modules"] == ["stapel-auth", "stapel-calendar"]
    assert booking["notes"] == ""


def test_recipes_section_rendered():
    docs, _ = load_documents([FULL])
    catalog = build_catalog(docs, recipes=load_recipes(RECIPES))
    md = render_markdown(catalog)
    assert "## Recipes" in md
    assert "### marketplace" in md
    assert "**Modules:** stapel-auth, stapel-profiles, stapel-listings" in md
    assert "**Notes:** reviews live in a separate target-generic module" in md


def test_no_recipes_section_when_absent():
    docs, _ = load_documents([FULL])
    md = render_markdown(build_catalog(docs))
    assert "## Recipes" not in md


def test_malformed_recipes_is_loud():
    with pytest.raises(SystemExit):
        parse_recipes("- name: orphan\n  modules: [a]\n")  # no top-level recipes:
    with pytest.raises(SystemExit):
        load_recipes_from_text("recipes:\n  - summary: no name here\n")


def load_recipes_from_text(text, tmp_path=None):
    # helper: parse_recipes gives raw dicts; load_recipes validates name.
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(text)
        p = Path(f.name)
    return load_recipes(p)


# ── determinism ──────────────────────────────────────────────────────────────


def test_markdown_deterministic_two_runs():
    docs, _ = load_documents([MINI, FULL])
    catalog = build_catalog(docs, recipes=load_recipes(RECIPES))
    first = render_markdown(catalog)
    # rebuild from a differently-ordered source list
    docs2, _ = load_documents([FULL, MINI])
    catalog2 = build_catalog(docs2, recipes=load_recipes(RECIPES))
    second = render_markdown(catalog2)
    assert first == second
    assert "\n\n\n" not in first  # no doubled blank lines
    assert first.endswith("\n") and not first.endswith("\n\n")


def test_markdown_no_timestamp():
    docs, _ = load_documents([FULL])
    md = render_markdown(build_catalog(docs))
    # no year-like token that would drift day to day
    assert "2026" not in md


# ── workspace discovery + CLI ────────────────────────────────────────────────


def test_discover_workspace_globs_sorted(tmp_path):
    for name in ("stapel-b", "stapel-a"):
        d = tmp_path / name / "docs"
        d.mkdir(parents=True)
        (d / "capabilities.json").write_text('{"module": "x"}')
    (tmp_path / "not-stapel" / "docs").mkdir(parents=True)
    found = discover_workspace(tmp_path)
    assert [p.parent.parent.name for p in found] == ["stapel-a", "stapel-b"]


def test_cli_writes_both_artifacts(tmp_path, capsys):
    out = tmp_path / "out"
    rc = main([str(FULL), str(MINI), str(BROKEN),
               "--recipes", str(RECIPES), "--out-dir", str(out)])
    assert rc == 0
    catalog = json.loads((out / "catalog.json").read_text())
    assert catalog["totals"]["modules"] == 2
    assert (out / "catalog.md").read_text().startswith("# Stapel module catalog")
    err = capsys.readouterr().err
    assert "2 modules covered (1 skipped)" in err


def test_cli_requires_inputs(capsys):
    with pytest.raises(SystemExit):
        main([])
