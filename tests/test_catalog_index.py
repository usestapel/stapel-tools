"""stapel-catalog --index tests (agent-knowledge-base.md §64 "Wave 1").

Exercises :func:`stapel_tools.catalog.build_index` — the full machine index
extension over :func:`build_catalog`: per module, ``flows``/``errors``
verbatim from sibling JSON, ``config_md`` table rows from CONFIG.MD,
``stapel_libs`` (STAPEL_LIBS registry projection) and ``components`` (a
matching ``-react`` package's manifest.json, when one exists). The shape
asserted here is the CONSUMER contract ``studio_cto.advisor_index`` (in
stapel-studio) already reads — see that module's docstring; do not drift
fields without checking there first.

Two fixture modules cover the honest-gap paths:

* ``mod-full`` (module ``stapel-demo``) carries a fixture flows.json/
  errors.json/CONFIG.MD and a fake ``-react`` package — every extension
  populated, so the happy path is fully exercised.
* ``mod-minimal`` (module ``stapel-mini``) carries none of them — the
  omitted-key / empty-list gap paths.

Neither ``stapel-demo`` nor ``stapel-mini`` are real STAPEL_LIBS entries, so
``stapel_libs`` is absent for both here — a real-registry hit is covered by
the workspace-siblings integration test below (skipped when the sibling
repos aren't checked out next to this one, e.g. a bare CI checkout).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from stapel_tools.catalog import build_index, load_config_lines, stapel_libs_entry

FIX = Path(__file__).parent / "fixtures" / "catalog"
FULL = FIX / "mod-full"
MINI = FIX / "mod-minimal"
REACT_ROOT = FIX / "fake-react"


def test_index_populates_every_extension_for_a_full_module():
    index, skipped = build_index([FULL], react_root=REACT_ROOT)
    assert skipped == []
    doc = index["modules"][0]
    assert doc["module"] == "stapel-demo"

    assert doc["flows"][0]["id"] == "demo.checkout"
    assert doc["errors"][0]["code"] == "error.400.demo_bad_request"
    assert doc["config_md"] == [
        "| DEMO_ZEBRA | env | fixture axis | no | true |",
        "| DEMO_ALPHA | env | fixture axis | no | false |",
    ]
    # stapel-demo is a test fixture, not a real STAPEL_LIBS entry
    assert "stapel_libs" not in doc

    components = doc["components"]
    assert components["package"] == "@stapel/demo-react"
    assert components["version"] == "0.9.0"
    assert components["hooks"] == ["useDemo", "useDemoAlpha"]
    assert components["operations"] == ["demo_api_z_one", "demo_api_z_two"]
    assert components["demos"] == [
        {
            "id": "demo.zebra-panel",
            "title": "Zebra panel",
            "component": "ZebraPanel",
            "flow": "demo.checkout",
            "source": "demo/ZebraPanel.demo.tsx",
        }
    ]


def test_index_honest_gaps_for_a_minimal_module():
    index, _ = build_index([MINI], react_root=REACT_ROOT)
    doc = index["modules"][0]
    assert doc["flows"] == []
    assert doc["errors"] == []
    assert "config_md" not in doc
    assert "components" not in doc  # no mini-react package exists
    assert "stapel_libs" not in doc


def test_index_without_react_root_never_adds_components():
    index, _ = build_index([FULL])  # react_root=None
    assert "components" not in index["modules"][0]


def test_load_config_lines_drops_header_and_separator():
    lines = load_config_lines(FULL)
    assert all(line.startswith("| DEMO_") for line in lines)
    assert not any("Key" in line for line in lines)
    assert not any(set(line.replace("|", "").strip()) <= set("- ") for line in lines if line)


def test_load_config_lines_absent_file_is_empty_list(tmp_path):
    assert load_config_lines(tmp_path) == []


def test_stapel_libs_entry_real_module_projects_url_prefix_requires_pin():
    entry = stapel_libs_entry("stapel-categories")
    assert entry == {
        # mismount fix (2026-07-20): categories' own urls.py needs the host
        # to supply "api/" (no internal segment) — see STAPEL_LIBS["categories"].
        "url_prefix": "categories/api/",
        "requires": ["attributes"],
        "pin": entry["pin"],  # pin value churns with registry bumps; shape only
    }
    assert isinstance(entry["pin"], str) and entry["pin"]


def test_stapel_libs_entry_unknown_module_is_none():
    assert stapel_libs_entry("stapel-does-not-exist") is None


def test_index_totals_and_json_serializable():
    index, _ = build_index([FULL, MINI], react_root=REACT_ROOT)
    assert index["totals"]["modules"] == 2
    # round-trips through JSON without error (no non-serializable values leaked)
    json.dumps(index)


# ── real workspace siblings (drift-check integration) ──────────────────────
# Skipped when this repo is checked out alone (e.g. bare CI checkout with no
# sibling stapel-* repos) — the same honesty discipline as stapel-studio's
# build_advisor_fixture.py: a dev-only exercise against real disk state, not
# faked with synthetic fixtures pretending to be real modules.
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
REAL_SIBLINGS = [
    WORKSPACE_ROOT / name for name in ("stapel-auth", "stapel-reviews", "stapel-billing")
    if (WORKSPACE_ROOT / name / "docs" / "capabilities.json").is_file()
]


@pytest.mark.skipif(
    len(REAL_SIBLINGS) < 2,
    reason="needs >=2 real sibling repos with docs/capabilities.json on disk",
)
def test_real_siblings_build_a_coherent_index():
    index, skipped = build_index(
        REAL_SIBLINGS,
        react_root=WORKSPACE_ROOT / "stapel-react"
        if (WORKSPACE_ROOT / "stapel-react").is_dir() else None,
    )
    assert skipped == []
    modules = {d["module"]: d for d in index["modules"]}
    assert set(modules) == {p.name for p in REAL_SIBLINGS}
    for name, doc in modules.items():
        assert isinstance(doc["flows"], list)
        assert isinstance(doc["errors"], list)
        # every real sibling here IS in STAPEL_LIBS
        assert doc.get("stapel_libs"), f"{name} missing stapel_libs projection"
    if "stapel-auth" in modules and "components" in modules["stapel-auth"]:
        auth_components = modules["stapel-auth"]["components"]
        assert auth_components["package"] == "@stapel/auth-react"
        assert isinstance(auth_components["hooks"], list) and auth_components["hooks"]


@pytest.mark.skipif(
    len(REAL_SIBLINGS) < 2,
    reason="needs >=2 real sibling repos with docs/capabilities.json on disk",
)
def test_real_siblings_index_is_deterministic_across_runs():
    from stapel_tools.catalog import _stable_json

    index1, _ = build_index(REAL_SIBLINGS)
    index2, _ = build_index(list(reversed(REAL_SIBLINGS)))
    assert _stable_json(index1) == _stable_json(index2)


# ── CLI: --index / -o / --react-root / --check ─────────────────────────────


def test_cli_index_writes_single_file(tmp_path):
    from stapel_tools.catalog import main

    out = tmp_path / "catalog.json"
    rc = main([str(FULL), str(MINI), "--index", "-o", str(out),
               "--react-root", str(REACT_ROOT)])
    assert rc == 0
    doc = json.loads(out.read_text())
    assert doc["totals"]["modules"] == 2
    modules = {m["module"]: m for m in doc["modules"]}
    assert modules["stapel-demo"]["components"]["package"] == "@stapel/demo-react"


def test_cli_index_check_passes_when_up_to_date(tmp_path):
    from stapel_tools.catalog import main

    out = tmp_path / "catalog.json"
    assert main([str(FULL), str(MINI), "--index", "-o", str(out)]) == 0
    assert main([str(FULL), str(MINI), "--index", "-o", str(out), "--check"]) == 0


def test_cli_index_check_fails_on_drift(tmp_path):
    from stapel_tools.catalog import main

    out = tmp_path / "catalog.json"
    assert main([str(FULL), "--index", "-o", str(out)]) == 0
    # a second module added → the committed artifact is now stale
    rc = main([str(FULL), str(MINI), "--index", "-o", str(out), "--check"])
    assert rc == 1


def test_cli_index_check_fails_when_missing(tmp_path):
    from stapel_tools.catalog import main

    out = tmp_path / "catalog.json"
    rc = main([str(FULL), "--index", "-o", str(out), "--check"])
    assert rc == 1
    assert not out.exists()


def test_cli_check_legacy_mode(tmp_path):
    from stapel_tools.catalog import main

    assert main([str(FULL), str(MINI), "--out-dir", str(tmp_path)]) == 0
    assert main([str(FULL), str(MINI), "--out-dir", str(tmp_path), "--check"]) == 0
    # touch nothing, add a module → drift
    rc = main([str(FULL), "--out-dir", str(tmp_path), "--check"])
    assert rc == 1


# ── --from-installed: the environment as the source of truth ────────────────
#
# A committed snapshot is a claim about a checkout; --from-installed is a
# claim about the interpreter that will actually run the product. It can only
# work because the modules now ship docs/capabilities.json (+ flows/errors +
# CONFIG.MD) as package-data, so the same layout a repo has on disk
# (<root>/docs/..., <root>/CONFIG.MD) exists inside site-packages/<pkg>/.


class _FakeDist:
    """Minimal importlib.metadata.Distribution stand-in.

    ``files``/``locate_file`` model a regular wheel install (RECORD lists the
    shipped data files); ``top_level`` models the editable fallback, where
    RECORD carries only the finder shim.
    """

    def __init__(self, name, root=None, files=None, top_level=None):
        self.metadata = {"Name": name}
        self._root = root
        self._files = [Path(f) for f in (files or [])]
        self._top_level = top_level

    @property
    def files(self):
        return self._files

    def locate_file(self, path):
        return Path(self._root) / path

    def read_text(self, filename):
        if filename == "top_level.txt" and self._top_level:
            return self._top_level + "\n"
        return None


def _fake_site_packages(tmp_path, pkg="stapel_demo"):
    """A site-packages-shaped copy of the mod-full fixture."""
    site = tmp_path / "site-packages"
    root = site / pkg
    (root / "docs").mkdir(parents=True)
    for name in ("capabilities.json", "flows.json", "errors.json"):
        (root / "docs" / name).write_text((FULL / "docs" / name).read_text())
    (root / "CONFIG.MD").write_text((FULL / "CONFIG.MD").read_text())
    return site


def test_discover_installed_reads_the_wheel_record(tmp_path, monkeypatch):
    import importlib.metadata as md

    from stapel_tools.catalog import discover_installed

    site = _fake_site_packages(tmp_path)
    dist = _FakeDist(
        "stapel-demo",
        root=site,
        files=["stapel_demo/__init__.py", "stapel_demo/docs/capabilities.json"],
    )
    monkeypatch.setattr(md, "distributions", lambda: iter([dist]))

    found = discover_installed()
    assert found == [site / "stapel_demo" / "docs" / "capabilities.json"]


def test_discover_installed_ignores_non_stapel_and_docless_dists(tmp_path, monkeypatch):
    import importlib.metadata as md

    from stapel_tools.catalog import discover_installed

    site = _fake_site_packages(tmp_path)
    stapel = _FakeDist(
        "stapel-demo", root=site,
        files=["stapel_demo/docs/capabilities.json"],
    )
    # right prefix, ships nothing readable → absent, not a crash
    docless = _FakeDist("stapel-nothing", root=site, files=["stapel_nothing/__init__.py"])
    # wrong prefix → never even probed
    foreign = _FakeDist("django", root=site, files=["django/docs/capabilities.json"])
    monkeypatch.setattr(md, "distributions", lambda: iter([foreign, docless, stapel]))

    assert discover_installed() == [site / "stapel_demo" / "docs" / "capabilities.json"]


def test_discover_installed_falls_back_to_the_import_spec(tmp_path, monkeypatch):
    """Editable install: RECORD has no docs/, the spec still finds them."""
    import importlib.metadata as md

    from stapel_tools.catalog import discover_installed

    site = _fake_site_packages(tmp_path)
    (site / "stapel_demo" / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(site))
    dist = _FakeDist(
        "stapel-demo", root=site,
        files=["__editable__.stapel_demo.finder.py"],
        top_level="stapel_demo",
    )
    monkeypatch.setattr(md, "distributions", lambda: iter([dist]))

    assert discover_installed() == [site / "stapel_demo" / "docs" / "capabilities.json"]


def test_from_installed_index_carries_the_shipped_siblings(tmp_path, monkeypatch):
    """The point of shipping more than capabilities.json: an installed-sourced
    index is not a degraded one — flows/errors/CONFIG.MD come along, so it
    says the same thing the checkout-sourced index says."""
    import importlib.metadata as md

    from stapel_tools.catalog import main

    site = _fake_site_packages(tmp_path)
    dist = _FakeDist(
        "stapel-demo", root=site,
        files=["stapel_demo/docs/capabilities.json"],
    )
    monkeypatch.setattr(md, "distributions", lambda: iter([dist]))

    out = tmp_path / "index.json"
    assert main(["--from-installed", "--index", "-o", str(out)]) == 0
    doc = json.loads(out.read_text())["modules"][0]
    assert doc["module"] == "stapel-demo"
    assert doc["flows"][0]["id"] == "demo.checkout"
    assert doc["errors"][0]["code"] == "error.400.demo_bad_request"
    assert doc["config_md"][0].startswith("| DEMO_ZEBRA |")


def test_from_installed_unions_and_dedupes_against_paths(tmp_path, monkeypatch):
    """An editable install resolves back into the checkout --workspace scans;
    the module must not land in the catalog twice."""
    import importlib.metadata as md

    from stapel_tools.catalog import main

    dist = _FakeDist(
        "stapel-demo", root=FULL.parent,
        files=["mod-full/docs/capabilities.json"],
    )
    monkeypatch.setattr(md, "distributions", lambda: iter([dist]))

    out = tmp_path / "index.json"
    assert main([str(FULL), "--from-installed", "--index", "-o", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["totals"]["modules"] == 1


def test_from_installed_alone_with_an_empty_environment_errors(tmp_path, monkeypatch):
    import importlib.metadata as md

    from stapel_tools.catalog import main

    monkeypatch.setattr(md, "distributions", lambda: iter([]))
    with pytest.raises(SystemExit):
        main(["--from-installed", "--index", "-o", str(tmp_path / "x.json")])
