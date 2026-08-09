"""stapel-readme — README.md as an assembled artifact (tracker #257).

The properties worth defending are the ones that make a generated page more
trustworthy than a hand-written one: badges appear only when their precondition
holds, the numbers come from the artifacts, contradictory inputs stop the
render instead of being papered over, and two runs produce the same bytes so a
drift gate means something.
"""
import json

import pytest

from stapel_tools.readme import (
    EmitError,
    badges,
    load_inputs,
    main,
    python_versions,
    render,
    resolve_version,
    static_languages,
)

PYPROJECT = """\
[project]
name = "stapel-demo"
version = "1.2.3"
requires-python = ">=3.11"
dependencies = ["Django>=4.2", "stapel-core>=0.16"]
classifiers = [
    "Programming Language :: Python :: 3 :: Only",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
]
"""

CAPABILITIES = {
    "module": "stapel-demo",
    "version": "1.2.3",
    "provides": "A demo module that\n  does one thing.",
    "axes": [{"key": "DEMO_ON", "kind": "bool", "default": True}],
    "surface": [{"name": "demo", "kind": "function", "path": "stapel_demo.demo"}],
    "extension_points": [{"name": "DEMO_PROVIDER", "kind": "dotted_path"}],
    "operations_total": 4,
    "requires": [
        {"module": "stapel-core", "optional": False},
        {"module": "stapel-cdn", "optional": True},
    ],
}


def _repo(tmp_path, *, pyproject=PYPROJECT, capabilities=CAPABILITIES,
          static="## Body\n\nProse.\n", license_text="MIT License\n\nCopyright",
          ci=True, codecov=True, llms=True, extra_docs=None):
    (tmp_path / "pyproject.toml").write_text(pyproject)
    docs = tmp_path / "docs"
    docs.mkdir()
    if static is not None:
        (docs / "readme.md").write_text(static)
    if capabilities is not None:
        (docs / "capabilities.json").write_text(json.dumps(capabilities))
    if llms:
        (docs / "llms.txt").write_text("# stapel-demo\n")
    if license_text:
        (tmp_path / "LICENSE").write_text(license_text)
    if ci:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("jobs:\n  test:\n    steps:\n      - uses: codecov/codecov-action@v5\n")
    if codecov:
        (tmp_path / "codecov.yml").write_text("coverage: {}\n")
    for rel, body in (extra_docs or {}).items():
        target = docs / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    return tmp_path


# --- badges: a badge that cannot be true is not emitted ----------------------

def test_full_badge_row_in_canon_order(tmp_path):
    row = badges(load_inputs(_repo(tmp_path)))
    labels = [line.split("]", 1)[0].lstrip("[![") for line in row]
    assert labels == ["CI", "coverage", "pypi", "downloads", "python", "license", "llms.txt"]


def test_no_ci_workflow_no_ci_badge(tmp_path):
    row = badges(load_inputs(_repo(tmp_path, ci=False, codecov=False)))
    assert not any("/actions/workflow/" in line for line in row)
    # ...and no coverage badge either: the upload step lived in that workflow
    assert not any("codecov" in line for line in row)


def test_coverage_badge_needs_config_and_upload_step(tmp_path):
    """codecov.yml without an upload step renders `unknown` — so: no badge."""
    repo = _repo(tmp_path, ci=False)
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text("jobs: {test: {}}\n")
    assert not any("codecov" in line for line in badges(load_inputs(repo)))


def test_unpublished_package_gets_status_badge_not_a_broken_pypi_one(tmp_path):
    repo = _repo(tmp_path, pyproject=PYPROJECT + "\n[tool.stapel.readme]\npypi = false\n")
    row = badges(load_inputs(repo))
    assert not any("pypi.org" in line or "pepy.tech" in line for line in row)
    assert any("status-unreleased-orange" in line for line in row)
    body = render(repo, load_inputs(repo), "en", ["en"])
    assert "pip install git+https://github.com/usestapel/stapel-demo" in body
    assert "pip install stapel-demo\n" not in body


def test_license_badge_needs_the_file(tmp_path):
    repo = _repo(tmp_path, license_text="")
    assert not any("github/license" in line for line in badges(load_inputs(repo)))


def test_llms_txt_badge_needs_the_file(tmp_path):
    repo = _repo(tmp_path, llms=False)
    assert not any("llms.txt" in line for line in badges(load_inputs(repo)))


def test_python_badge_needs_version_classifiers(tmp_path):
    stripped = PYPROJECT.split("classifiers")[0]
    repo = _repo(tmp_path, pyproject=stripped)
    assert not any("pyversions" in line for line in badges(load_inputs(repo)))


def test_family_classifiers_are_not_versions():
    assert python_versions({"project": {"classifiers": [
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.11",
    ]}}) == ["3.11"]


# --- facts: read, never retyped ---------------------------------------------

def test_facts_come_from_the_artifacts(tmp_path):
    repo = _repo(tmp_path)
    page = render(repo, load_inputs(repo), "en", ["en"])
    assert "| Version | `1.2.3` |" in page
    assert "| Python | `>=3.11` (3.11, 3.12) |" in page
    assert "| Django | `Django>=4.2` |" in page
    assert "| HTTP operations | 4 |" in page
    assert "| Config axes | 1 |" in page
    assert "[`stapel-cdn`](https://github.com/usestapel/stapel-cdn) (optional)" in page
    # the provides one-liner is folded to a single line
    assert "> A demo module that does one thing." in page


def test_zero_counts_are_omitted_not_rendered_as_zero(tmp_path):
    thin = dict(CAPABILITIES, axes=[], surface=[], extension_points=[], operations_total=0)
    repo = _repo(tmp_path, capabilities=thin)
    page = render(repo, load_inputs(repo), "en", ["en"])
    assert "Config axes" not in page and "HTTP operations" not in page


def test_operation_count_prefers_the_openapi_document(tmp_path):
    repo = _repo(tmp_path)
    (repo / "docs" / "schema.json").write_text(json.dumps({
        "paths": {"/a/": {"get": {}, "post": {}}, "/b/": {"get": {}, "parameters": []}}
    }))
    assert "| HTTP operations | 3 |" in render(repo, load_inputs(repo), "en", ["en"])


# --- contradictory inputs stop the render (tracker #226) ---------------------

def test_capabilities_version_lagging_pyproject_is_an_error(tmp_path):
    stale = dict(CAPABILITIES, version="1.0.0")
    repo = _repo(tmp_path, capabilities=stale)
    with pytest.raises(EmitError, match="version drift"):
        resolve_version(load_inputs(repo))


def test_missing_static_body_names_the_migration(tmp_path):
    repo = _repo(tmp_path, static=None)
    with pytest.raises(EmitError, match="static half"):
        render(repo, load_inputs(repo), "en", ["en"])


def test_static_body_may_not_own_the_title(tmp_path):
    repo = _repo(tmp_path, static="# stapel-demo\n\nProse.\n")
    with pytest.raises(EmitError, match="level-1 heading"):
        render(repo, load_inputs(repo), "en", ["en"])


# --- documentation links: flat, per language, absolute -----------------------

def test_doc_links_are_flat_per_language_and_absolute(tmp_path):
    repo = _repo(tmp_path, extra_docs={
        "flows/en/README.md": "# Flows\n",
        "flows/ru/README.md": "# Флоу\n",
        "errors.en.md": "# Errors\n",
        "errors.ru.md": "# Ошибки\n",
    })
    (repo / "docs" / "errors.json").write_text("[]")
    page = render(repo, load_inputs(repo), "en", ["en"])
    base = "https://github.com/usestapel/stapel-demo/blob/main"
    # each language is one hop from the README — no picker page in between
    assert f"**Flows:** [English]({base}/docs/flows/en/README.md)" in page
    assert f"[Русский]({base}/docs/flows/ru/README.md)" in page
    assert f"**Errors:** [English]({base}/docs/errors.en.md)" in page
    # absolute, because this same text is the PyPI long description
    assert "](docs/" not in page


def test_language_switch_between_readmes(tmp_path):
    repo = _repo(tmp_path)
    (repo / "docs" / "readme.ru.md").write_text("## Тело\n\nПроза.\n")
    assert static_languages(repo) == ["en", "ru"]
    en = render(repo, load_inputs(repo), "en", ["en", "ru"])
    ru = render(repo, load_inputs(repo), "ru", ["en", "ru"])
    assert "Read this in: **English** · [Русский](README.ru.md)" in en
    assert "Читать на: [English](README.md) · **Русский**" in ru
    assert "## Коротко" in ru and "Проза." in ru


def test_single_language_gets_no_switch(tmp_path):
    repo = _repo(tmp_path)
    assert "Read this in" not in render(repo, load_inputs(repo), "en", ["en"])


# --- determinism + the drift gate -------------------------------------------

def test_render_is_deterministic(tmp_path):
    repo = _repo(tmp_path)
    inputs = load_inputs(repo)
    assert render(repo, inputs, "en", ["en"]) == render(repo, inputs, "en", ["en"])


def test_cli_writes_then_check_passes_and_notices_drift(tmp_path, capsys):
    repo = _repo(tmp_path)
    assert main([str(repo)]) == 0
    assert (repo / "README.md").is_file()
    assert main([str(repo), "--check"]) == 0

    (repo / "docs" / "readme.md").write_text("## Body\n\nRewritten prose.\n")
    assert main([str(repo), "--check"]) == 1
    assert "DRIFT" in capsys.readouterr().err


def test_cli_reports_a_repo_with_no_static_body(tmp_path, capsys):
    repo = _repo(tmp_path, static=None)
    assert main([str(repo)]) == 1
    assert "readme.md" in capsys.readouterr().err


def test_generated_marker_names_the_file_a_human_edits(tmp_path):
    repo = _repo(tmp_path)
    page = render(repo, load_inputs(repo), "en", ["en"])
    assert page.startswith("<!-- Generated by stapel-readme from docs/readme.md")
    assert "do not hand-edit `README.md`" in page
