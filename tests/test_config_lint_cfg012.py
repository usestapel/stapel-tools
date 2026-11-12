"""CFG012 — moderation screens photos with no origin to resolve them against.

A generated classified project installed stapel-moderation and stapel-cdn and
left ``STAPEL_MODERATION["MEDIA_BASE_URL"]`` at its empty default.
``cdn.describe`` answers relative variant URLs, so screening parked every ad
with photos as ``MediaUnavailable`` and the seller waited on a review that
never came. The scaffold now derives the origin from ``SITE_URL``.
"""
import pytest

from stapel_tools.config_lint import lint_project
from stapel_tools.create_project import create_project

APPS = 'INSTALLED_APPS = ["stapel_cdn", "stapel_moderation"]\n'


def _project(tmp_path, settings: str, env: str = ""):
    pkg = tmp_path / "svc" / "config" / "settings"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "base.py").write_text("import os\n" + settings)
    if env:
        (tmp_path / ".env.example").write_text(env)
    return tmp_path


def _cfg012(project):
    return [f for f in lint_project(project, notes=[]) if f.rule == "CFG012"]


def test_fires_when_no_origin_is_stated(tmp_path):
    [finding] = _cfg012(_project(tmp_path, APPS))
    assert finding.level == "error"
    assert "never state it" in finding.message
    assert finding.path.endswith("base.py") and finding.line == 2


@pytest.mark.parametrize("statement", [
    'STAPEL_MODERATION = {"MEDIA_BASE_URL": ""}\n',
    'STAPEL_MODERATION = {}\nSTAPEL_MODERATION["MEDIA_BASE_URL"] = None\n',
    'MEDIA_BASE_URL = ""\n',
])
def test_fires_on_an_empty_literal(tmp_path, statement):
    [finding] = _cfg012(_project(tmp_path, APPS + statement))
    assert "state it as empty" in finding.message


@pytest.mark.parametrize("statement", [
    'STAPEL_MODERATION = {"MEDIA_BASE_URL": os.getenv("SITE_URL", ""), **{}}\n',
    'STAPEL_MODERATION = {}\nSTAPEL_MODERATION["MEDIA_BASE_URL"] = "https://x.dev"\n',
    'MEDIA_BASE_URL = "https://cdn.x.dev"\n',
])
def test_silent_when_an_origin_is_stated(tmp_path, statement):
    assert _cfg012(_project(tmp_path, APPS + statement)) == []


def test_env_file_counts(tmp_path):
    assert _cfg012(_project(tmp_path / "a", APPS, env="MEDIA_BASE_URL=https://x.dev\n")) == []
    assert len(_cfg012(_project(tmp_path / "b", APPS, env="MEDIA_BASE_URL=\n"))) == 1


@pytest.mark.parametrize("apps", ['INSTALLED_APPS = ["stapel_moderation"]\n',
                                  'INSTALLED_APPS = ["stapel_cdn"]\n'])
def test_silent_without_both_modules(tmp_path, apps):
    assert _cfg012(_project(tmp_path, apps)) == []


def _generate(tmp_path, modules):
    create_project(
        name="app", project_type="monolith", title="App", url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=modules,
        output_dir=tmp_path, use_submodules=False, init_git=False,
    )
    return tmp_path / "app"


def test_generated_moderation_project_states_the_origin(tmp_path):
    proj = _generate(tmp_path, ["core", "cdn", "moderation"])
    base = (proj / "svc-app" / "config" / "settings" / "base.py").read_text()
    assert '"MEDIA_BASE_URL": os.getenv("SITE_URL", "")' in base
    assert "SITE_URL=https://x.dev" in (proj / ".env.example").read_text()
    assert _cfg012(proj) == []


def test_generated_project_without_moderation_gets_no_fragment(tmp_path):
    proj = _generate(tmp_path, ["core", "cdn"])
    base = (proj / "svc-app" / "config" / "settings" / "base.py").read_text()
    assert "MEDIA_BASE_URL" not in base
