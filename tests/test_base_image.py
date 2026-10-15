"""--base-image / $STAPEL_BASE_IMAGE: the FROM of every generated Python service."""
import re

import pytest

from stapel_tools import image_lint
from stapel_tools.create_project import apply_base_image, create_project, resolve_base_image

REF = "registry.example/python-base:2026-01-01-abcdef12"


def _gen(tmp_path, name, project_type="monolith", modules=None, **kw):
    create_project(
        name=name, project_type=project_type, title=name, url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=modules or ["core"],
        output_dir=tmp_path, use_submodules=False, init_git=False, **kw,
    )
    return tmp_path / name


def _py_dockerfiles(proj):
    return [p for p in proj.rglob("Dockerfile")
            if "node_modules" not in p.parts and re.search(r"^FROM (python:|registry\.)", p.read_text(), re.M)]


def _froms(path):
    return re.findall(r"^FROM (\S+)", path.read_text(), re.M)


def test_default_from_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("STAPEL_BASE_IMAGE", raising=False)
    proj = _gen(tmp_path, "plain")
    files = _py_dockerfiles(proj)
    assert files
    for f in files:
        assert _froms(f) == ["python:3.12-slim"]
        assert "USER" not in f.read_text()


def test_flag_sets_from_on_every_service(tmp_path, monkeypatch):
    monkeypatch.delenv("STAPEL_BASE_IMAGE", raising=False)
    proj = _gen(tmp_path, "svcs", modules=["core", "cdn"], base_image=REF)
    files = _py_dockerfiles(proj)
    assert files
    for f in files:
        assert set(_froms(f)) == {REF}
        text = f.read_text()
        assert text.index("USER root") < text.index("apt-get")
        assert text.index("USER 10001") < text.index("CMD")


def test_env_sets_from(tmp_path, monkeypatch):
    monkeypatch.setenv("STAPEL_BASE_IMAGE", REF)
    proj = _gen(tmp_path, "viaenv")
    files = _py_dockerfiles(proj)
    assert files and all(set(_froms(f)) == {REF} for f in files)


def test_flag_beats_env(monkeypatch):
    monkeypatch.setenv("STAPEL_BASE_IMAGE", "env.example/x:1")
    assert resolve_base_image(REF) == REF
    assert resolve_base_image(None) == "env.example/x:1"
    monkeypatch.delenv("STAPEL_BASE_IMAGE")
    assert resolve_base_image(None) is None


def test_whitespace_ref_refused():
    with pytest.raises(ValueError):
        resolve_base_image("a b")


def test_multistage_cdn_template_keeps_stage_names(tmp_path):
    df = tmp_path / "svc" / "Dockerfile"
    df.parent.mkdir()
    df.write_text(
        "FROM python:3.12-slim-trixie AS vips-builder\nRUN apt-get update\n\n"
        "FROM python:3.12-slim-trixie\nRUN apt-get update\n"
        "ENV DJANGO_SETTINGS_MODULE=x\nCMD [\"gunicorn\"]\n"
    )
    assert apply_base_image(tmp_path, REF) == [df]
    text = df.read_text()
    assert f"FROM {REF} AS vips-builder" in text
    assert text.count("USER root") == 2 and text.count("USER 10001") == 1


def test_image_lint_passes_on_generated_tree(tmp_path):
    proj = _gen(tmp_path, "lint", modules=["core", "cdn"], base_image=REF)
    errors = [v for v in image_lint.lint_project(proj) if v.level == "error"]
    assert errors == []
