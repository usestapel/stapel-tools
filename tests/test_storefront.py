"""stapel-storefront — the public page, assembled (tracker #259).

The properties that matter: the library table comes from the catalogue, the
version cells are live badges rather than typed numbers, an unpublished
package is not advertised as installable, and two renders of the same
workspace are byte-identical so a committed page can be drift-gated.
"""
import json

import pytest

from stapel_tools.storefront import (
    _first_sentence,
    _md_badges_to_html,
    main,
    module_rows,
    render_html,
    render_markdown,
)

PYPROJECT = """\
[project]
name = "{name}"
version = "1.0.0"
requires-python = ">=3.11"
classifiers = ["Programming Language :: Python :: 3.11"]
"""


def _module(workspace, name, provides, *, published=True, operations=3, axes=2):
    repo = workspace / name
    (repo / "docs").mkdir(parents=True)
    pyproject = PYPROJECT.format(name=name)
    if not published:
        pyproject += "\n[tool.stapel.readme]\npypi = false\n"
    (repo / "pyproject.toml").write_text(pyproject)
    (repo / "LICENSE").write_text("MIT License\n")
    (repo / "docs" / "capabilities.json").write_text(json.dumps({
        "module": name,
        "version": "1.0.0",
        "provides": provides,
        "operations_total": operations,
        "axes": [{"key": f"A{i}"} for i in range(axes)],
        "extension_points": [{"name": "EP"}],
    }))
    return repo


@pytest.fixture
def workspace(tmp_path):
    _module(tmp_path, "stapel-alpha", "Does alpha things for a product.")
    _module(tmp_path, "stapel-beta", "Does beta things.", published=False, operations=1, axes=1)
    (tmp_path / "not-a-stapel-repo").mkdir()
    return tmp_path


def _totals(rows):
    return {"modules": len(rows), "operations": 4, "axes": 3, "extension_points": 2}


# --- the table is sourced, not typed ----------------------------------------

def test_rows_come_from_the_catalogue_sorted(workspace):
    rows = module_rows(workspace)
    assert [row["module"] for row in rows] == ["stapel-alpha", "stapel-beta"]
    assert rows[0]["provides"] == "Does alpha things for a product."


def test_version_cells_are_live_badges_not_numbers(workspace):
    page = render_markdown(module_rows(workspace), _totals(module_rows(workspace)))
    assert "img.shields.io/pypi/v/stapel-alpha" in page
    assert "static.pepy.tech/badge/stapel-alpha/month" in page
    # the number itself appears nowhere: shields resolves it at read time, so a
    # page committed today still tells the truth about a release made tomorrow
    assert "1.0.0" not in page


def test_unpublished_module_is_not_advertised_as_installable(workspace):
    page = render_markdown(module_rows(workspace), _totals(module_rows(workspace)))
    assert "pypi/v/stapel-beta" not in page
    assert "pepy.tech/badge/stapel-beta" not in page
    assert "status-unreleased-orange" in page


def test_totals_are_reported(workspace):
    rows = module_rows(workspace)
    page = render_markdown(rows, _totals(rows))
    assert "## The libraries (2)" in page
    assert "4 HTTP operations · 3 configuration axes · 2 fork-free extension points" in page


def test_quickstart_shows_the_two_commands_that_start_a_project(workspace):
    rows = module_rows(workspace)
    page = render_markdown(rows, _totals(rows))
    assert "pip install stapel-tools" in page
    assert "stapel-create-project my-app" in page
    assert "docker compose -f docker-compose.local.yml --env-file .env.local up" in page


def test_a_repo_without_a_pyproject_is_skipped_loudly(tmp_path, capsys):
    repo = _module(tmp_path, "stapel-broken", "Broken.")
    (repo / "pyproject.toml").unlink()
    assert module_rows(tmp_path) == []
    assert "skipping stapel-broken" in capsys.readouterr().err


# --- presentation ------------------------------------------------------------

def test_long_provides_is_cut_at_a_sentence_boundary():
    text = "First sentence here. " + "x" * 300
    assert _first_sentence(text, limit=60) == "First sentence here."
    assert _first_sentence("short", limit=60) == "short"
    assert _first_sentence("word " * 40, limit=60).endswith("…")


def test_badge_markdown_becomes_html(workspace):
    row = module_rows(workspace)[0]
    rendered = _md_badges_to_html(row["badges"][:1])
    assert rendered.startswith("<a href=")
    assert "<img src=" in rendered and 'alt="CI"' not in rendered  # no ci.yml here


def test_html_is_self_contained_and_escapes_text(tmp_path):
    _module(tmp_path, "stapel-x", 'A <script>alert("x")</script> module.')
    rows = module_rows(tmp_path)
    page = render_html(rows, _totals(rows))
    assert page.startswith("<!doctype html>")
    assert "<style>" in page  # inline CSS, no external stylesheet
    assert "<script" not in page  # the provides text was escaped, not executed
    assert "&lt;script&gt;" in page


# --- determinism + the drift gate -------------------------------------------

def test_render_is_deterministic(workspace):
    rows = module_rows(workspace)
    totals = _totals(rows)
    assert render_markdown(rows, totals) == render_markdown(module_rows(workspace), totals)
    assert render_html(rows, totals) == render_html(module_rows(workspace), totals)


def test_cli_writes_both_formats_then_check_passes(workspace, tmp_path, capsys):
    out = tmp_path / "site"
    argv = ["--workspace", str(workspace), "--out-dir", str(out), "--format", "all"]
    assert main(argv) == 0
    assert (out / "index.md").is_file() and (out / "index.html").is_file()
    assert main(argv + ["--check"]) == 0

    (out / "index.md").write_text("stale\n")
    assert main(argv + ["--check"]) == 1
    assert "DRIFT" in capsys.readouterr().err


def test_cli_refuses_to_write_an_empty_storefront(tmp_path, capsys):
    assert main(["--workspace", str(tmp_path), "--out-dir", str(tmp_path)]) == 1
    assert "no modules found" in capsys.readouterr().err
    assert not (tmp_path / "index.md").exists()
