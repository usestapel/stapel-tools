"""scripts/check_pypi_pins.py: a registry pin whose ceiling excludes a newer
PyPI release is named. Offline: the index answer is a fixture; the network
run is ``make pins-check`` and the publish workflow."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "check_pypi_pins", Path(__file__).resolve().parent.parent / "scripts" / "check_pypi_pins.py")
pins = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pins)


def test_the_0_79_1_listings_case_is_a_stale_ceiling():
    assert pins.classify("0.28.0", ["0.27.0", "0.28.0", "0.29.0"]) == ("stale", "0.29.0")


def test_a_newer_patch_is_inside_the_ceiling():
    assert pins.classify("0.9.0", ["0.9.0", "0.9.1"]) == ("patch", "0.9.1")


def test_current_and_missing():
    assert pins.classify("0.29.0", ["0.28.0", "0.29.0", "0.30.0rc1"]) == ("current", "0.29.0")
    assert pins.classify("0.2.0", ["0.1.0"]) == ("missing", "0.1.0")
    assert pins.classify("0.1.0", []) == ("missing", None)


def test_run_reports_and_fails_strict(monkeypatch, capsys):
    index = {"stapel-listings": ["0.28.0", "0.29.0"], "stapel-gdpr": ["0.9.0", "0.9.1"]}
    monkeypatch.setattr(pins, "_released_versions", lambda name: index.get(name, []))
    from stapel_tools import create_project

    monkeypatch.setattr(create_project, "STAPEL_LIBS", {
        "listings": {"repo": "https://github.com/usestapel/stapel-listings.git", "pin": "0.28.0"},
        "gdpr": {"repo": "https://github.com/usestapel/stapel-gdpr.git", "pin": "0.9.0"},
        "promotions": {"repo": "https://github.com/usestapel/stapel-promotions.git",
                       "pin": "0.1.0", "released": False},
    })
    assert pins.main([]) == 0
    assert pins.main(["--strict"]) == 1
    out = capsys.readouterr().out
    assert "PIN002 listings" in out and ">=0.28.0,<0.29" in out
    assert "promotions" not in out


def test_registry_pins_render_their_ceiling(tmp_path):
    from stapel_tools.create_project import STAPEL_LIBS, create_project

    create_project(
        name="app", project_type="monolith", title="App", url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=["shop"],
        output_dir=tmp_path, use_submodules=False, init_git=False,
    )
    reqs = (tmp_path / "app" / "svc-app" / "requirements.txt").read_text()
    pin = STAPEL_LIBS["listings"]["pin"]
    major, minor, _ = pin.split(".")
    assert f"stapel-listings>={pin},<{major}.{int(minor) + 1}" in reqs
    assert "matches last-published" not in reqs


def _registry(monkeypatch, rows, index):
    monkeypatch.setattr(pins, "_released_versions", lambda name: index.get(name, []))
    from stapel_tools import create_project

    monkeypatch.setattr(create_project, "STAPEL_LIBS", rows)


_LISTINGS = {"repo": "https://github.com/usestapel/stapel-listings.git", "pin": "0.28.0"}


def test_a_newer_upstream_at_publish_is_a_notice_with_a_follow_up(monkeypatch, capsys, tmp_path):
    """A release that appeared between the tag and the publish does not
    strand the pushed tag: exit 0, a warning annotation, a follow-up file."""
    _registry(monkeypatch, {"listings": dict(_LISTINGS)}, {"stapel-listings": ["0.28.0", "0.29.0"]})
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    report = tmp_path / "follow-up.md"
    assert pins.main(["--publish", "--report", str(report)]) == 0
    out = capsys.readouterr().out
    assert "::warning title=check-pypi-pins::PIN002 listings" in out
    assert "PIN002 listings: stapel-listings 0.29.0" in report.read_text()
    # ...and the same index before the tag is a refusal.
    assert pins.main(["--strict"]) == 1
    assert "::error title=check-pypi-pins::PIN002 listings" in capsys.readouterr().out


def test_publish_without_a_newer_upstream_writes_no_follow_up(monkeypatch, tmp_path):
    _registry(monkeypatch, {"listings": dict(_LISTINGS)}, {"stapel-listings": ["0.28.0", "0.28.1"]})
    report = tmp_path / "follow-up.md"
    assert pins.main(["--publish", "--report", str(report)]) == 0
    assert not report.exists()


def test_a_pin_that_is_not_on_pypi_blocks_the_publish_too(monkeypatch, capsys):
    _registry(monkeypatch, {"listings": {**_LISTINGS, "pin": "0.28.5"}},
              {"stapel-listings": ["0.28.0", "0.29.0"]})
    assert pins.main(["--publish"]) == 1
    assert "PIN001 listings" in capsys.readouterr().out


def test_a_pin_outside_its_own_range_blocks_in_every_mode(monkeypatch, capsys):
    """``0.28`` renders ``>=0.28,<0.29`` only by accident of parsing and
    ``0.28.0rc1`` is not a final release: both refused before the index is
    even asked."""
    for bad in ("0.28", "0.28.0rc1", "latest"):
        _registry(monkeypatch, {"listings": {**_LISTINGS, "pin": bad}},
                  {"stapel-listings": ["0.28.0"]})
        for mode in ([], ["--strict"], ["--publish"]):
            assert pins.main(mode) == 1, (bad, mode)
        assert "PIN003 listings" in capsys.readouterr().out
