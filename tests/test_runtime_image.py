"""--image-base fleet: the stapel-runtime backend, the node-build frontend with a
static export stage, IMG005 and the runtime-closure subset gates."""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

from stapel_tools import image_lint, runtime_image
from stapel_tools.create_project import create_project

TAG = "20261105-abcdef12"
REG = runtime_image.FLEET_REGISTRY


def _gen(tmp_path, name, modules=None, **kw):
    create_project(
        name=name, project_type="monolith", title=name, url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=modules or ["core"],
        output_dir=tmp_path, use_submodules=False, init_git=False, **kw,
    )
    return tmp_path / name


def _froms(path):
    return re.findall(r"^FROM (\S+)", path.read_text(), re.M)


def _dockerfiles(proj):
    return sorted(p for p in proj.rglob("Dockerfile") if "node_modules" not in p.parts)


# --- the switch --------------------------------------------------------------


def test_upstream_is_the_default_and_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv(runtime_image.FLEET_TAG_ENV, raising=False)
    proj = _gen(tmp_path, "plain", modules=["core", "cdn"])
    froms = {str(p.relative_to(proj)): _froms(p) for p in _dockerfiles(proj)}
    assert froms["frontend/Dockerfile"] == ["node:22-alpine", "build"]
    assert froms["svc-plain/Dockerfile"] == ["python:3.12-slim-trixie", "python:3.12-slim-trixie"]


@pytest.mark.parametrize("modules", [["core"], ["core", "cdn"]])
def test_fleet_builds_every_image_on_one_tag(tmp_path, modules):
    proj = _gen(tmp_path, "fl", modules=modules, image_base="fleet", fleet_tag=TAG)
    backend = (proj / "svc-fl" / "Dockerfile").read_text()
    frontend = (proj / "frontend" / "Dockerfile").read_text()
    assert _froms(proj / "svc-fl" / "Dockerfile") == [f"{REG}/stapel-runtime:{TAG}"]
    assert _froms(proj / "frontend" / "Dockerfile") == [
        f"{REG}/node-build:{TAG}", runtime_image.STATIC_EXPORT_BASE]
    # The closure and the decoders come from the runtime: no apt, no compiler.
    for absent in ("apt-get", "vips-builder", "build-essential", "--find-links"):
        assert absent not in backend
    assert "RUN pip install --no-cache-dir -r requirements.txt" in backend
    assert "COPY --chown=10001:10001 svc-fl ." in backend
    # Only dist/ leaves the build stage.
    export = frontend.split(" AS export", 1)[1]
    assert "COPY --from=build /app/dist ./dist" in export
    assert "node_modules" not in export and "pnpm" not in export
    assert image_lint.lint_preview_project(proj) == []
    assert image_lint.lint_preview_project(proj, base_tag=TAG) == []
    assert [v for v in image_lint.lint_project(proj) if v.level == "error"] == []
    assert not [v for v in image_lint.lint_project(proj) if v.rule in ("IMG001", "IMG003")]


def test_fleet_tag_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv(runtime_image.FLEET_TAG_ENV, TAG)
    proj = _gen(tmp_path, "viaenv", image_base="fleet")
    assert _froms(proj / "svc-viaenv" / "Dockerfile") == [f"{REG}/stapel-runtime:{TAG}"]


def test_fleet_without_a_tag_refuses(tmp_path, monkeypatch):
    monkeypatch.delenv(runtime_image.FLEET_TAG_ENV, raising=False)
    monkeypatch.setattr(runtime_image, "FLEET_IMAGE_TAG", None)
    with pytest.raises(SystemExit):
        _gen(tmp_path, "notag", image_base="fleet")


@pytest.mark.parametrize("bad", ["1", "latest", "2026-11-05"])
def test_fleet_refuses_a_moving_tag(tmp_path, bad):
    with pytest.raises(SystemExit):
        _gen(tmp_path, "moving", image_base="fleet", fleet_tag=bad)


def test_unknown_image_base_refuses(tmp_path):
    with pytest.raises(SystemExit):
        _gen(tmp_path, "unknown", image_base="slim")


def test_base_image_and_fleet_conflict(tmp_path):
    with pytest.raises(SystemExit):
        _gen(tmp_path, "both", image_base="fleet", fleet_tag=TAG,
             base_image="registry.example/python-base:20260101-abcdef12")


def test_released_default_tag_is_dated():
    if runtime_image.FLEET_IMAGE_TAG is not None:
        assert re.fullmatch(r"\d{8}-[0-9a-f]{8}", runtime_image.FLEET_IMAGE_TAG)


def test_new_service_joins_the_projects_fleet_tag(tmp_path):
    proj = _gen(tmp_path, "grow", image_base="fleet", fleet_tag=TAG)
    proc = subprocess.run(
        [sys.executable, "-m", "stapel_tools.new_service", "extra", "--prefix", "svc-",
         "--project-root", str(proj)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert _froms(proj / "svc-extra" / "Dockerfile") == [f"{REG}/stapel-runtime:{TAG}"]
    assert image_lint.lint_preview_project(proj) == []


# --- IMG005 --------------------------------------------------------------------


def _img005(text, **kw):
    return [v.message for v in image_lint.lint_preview_dockerfile(text, **kw)]


def test_img005_accepts_the_fleet_templates():
    assert _img005(runtime_image.render_backend("svc-x", TAG, vendored_core=False)) == []
    assert _img005(runtime_image.render_frontend(TAG)) == []


@pytest.mark.parametrize("text, fragment", [
    ("FROM python:3.12-slim\nRUN pip install x\n", "not a fleet tag"),
    ("FROM node:22-alpine AS build\nFROM build AS export\n", "not a fleet tag"),
    (f"FROM {REG}/stapel-runtime:1\n", "moving tag"),
    ("ARG BASE\nFROM ${BASE}\n", "resolves to nothing"),
    (f"FROM {REG}/node-build:{TAG} AS build\nFROM alpine:3.24.3\nRUN apk add python3\n",
     "installs or fetches"),
    ("FROM alpine:3.24\n", "not a fleet tag"),
    ("FROM alpine:latest\n", "not a fleet tag"),
    (f"FROM {REG}/node-build:{TAG} AS build\nFROM builder\n", "not a fleet tag"),
    (f"FROM {REG}/node-build:{TAG} AS build\nFROM {REG}/stapel-runtime:20261101-12345678\n",
     "more than one tag"),
    ("FROM registry.example/stapel-runtime:20261105-abcdef12\n", "not a fleet tag"),
])
def test_img005_refuses(text, fragment):
    found = _img005(text)
    assert found and any(fragment in m for m in found), found


def test_img005_ignores_noqa():
    assert _img005("FROM python:3.12-slim  # noqa: IMG005\n")


def test_img005_one_base_date_per_host():
    text = runtime_image.render_backend("svc-x", TAG, vendored_core=False)
    assert _img005(text, base_tag=TAG) == []
    assert any("one base date per host" in m for m in _img005(text, base_tag="20261101-12345678"))


def test_img005_across_a_project(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "Dockerfile").write_text(f"FROM {REG}/stapel-runtime:{TAG}\n")
    (tmp_path / "b" / "Dockerfile").write_text(f"FROM {REG}/stapel-runtime:20261101-12345678\n")
    found = image_lint.lint_preview_project(tmp_path)
    assert any("more than one fleet tag" in v.message for v in found)


def test_img005_cli(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    assert image_lint.main(["--preview", str(tmp_path)]) == 1
    (tmp_path / "Dockerfile").write_text(f"FROM {REG}/stapel-runtime:{TAG}\n")
    assert image_lint.main(["--preview", str(tmp_path)]) == 0
    assert image_lint.main(["--preview", "--base-tag", "20261101-12345678", str(tmp_path)]) == 1


def test_stapel_runtime_satisfies_a_media_need_without_becoming_a_tier():
    assert image_lint.BASE_RANK["stapel-runtime"] == image_lint.BASE_RANK["media-base"]
    assert image_lint.RANK_BASE[1] == "media-base"


# --- the runtime closure ---------------------------------------------------------


def test_closure_pins_stapel_exactly_and_merges_extras():
    closure = runtime_image.runtime_closure({
        "a": ["stapel-cdn>=0.30.0,<0.31", "Django>=5.1", "# comment"],
        "b": ["stapel-cdn[images]>=0.30.0,<0.31", "django>=5.1", "daphne>=4,<5"],
    })
    assert closure == ["stapel-cdn[images]==0.30.0", "daphne>=4,<5", "django>=5.1"]


def test_closure_refuses_disagreeing_pins_and_editables():
    with pytest.raises(runtime_image.RuntimeImageError):
        runtime_image.runtime_closure({"a": ["stapel-core>=0.95.0,<0.96"],
                                       "b": ["stapel-core>=0.94.0,<0.95"]})
    with pytest.raises(runtime_image.RuntimeImageError):
        runtime_image.runtime_closure({"a": ["-e ../stapel-auth"]})


def test_emitted_closure_covers_every_composite(tmp_path):
    info = runtime_image.emit(tmp_path)
    assert info["composites"] == ["booking", "classified", "shop", "social"]
    pinned = {}
    for line in info["closure"]:
        name, _extras, spec = runtime_image.parse_requirement(line)
        if spec.startswith("=="):
            pinned[name] = spec[2:]
    manifest = {"distributions": pinned}
    for name in info["composites"]:
        lines = (tmp_path / "composites" / f"{name}.txt").read_text().splitlines()
        stapel = [ln for ln in lines if ln.startswith("stapel-")]
        assert stapel, name
        assert runtime_image.check_against_manifest(stapel, manifest) == [], name
    assert (tmp_path / "requirements-runtime.txt").read_text().startswith("# stapel-runtime closure")


@pytest.mark.parametrize("version, spec, ok", [
    ("0.30.0", ">=0.30.0,<0.31", True),
    ("0.31.0", ">=0.30.0,<0.31", False),
    ("6.0.8", ">=5.1", True),
    ("4.1", ">=4,<5", True),
    ("5.0", ">=4,<5", False),
    ("3.3.5", "==3.3.*", True),
    ("2.2", "~=2.1", True),
    ("3.0", "~=2.1", False),
    ("1.0", "!=1.0", False),
])
def test_satisfies(version, spec, ok):
    assert runtime_image.satisfies(version, spec) is ok


def test_manifest_check_names_what_is_missing_or_behind():
    manifest = {"distributions": {"stapel-core": "0.94.0", "Django": "6.0.8"}}
    problems = runtime_image.check_against_manifest(
        ["stapel-core>=0.95.0,<0.96", "django>=5.1", "stapel-orders>=0.1.0,<0.2"], manifest)
    assert len(problems) == 2
    assert "runtime has stapel-core 0.94.0" in problems[0]
    assert "not in the runtime" in problems[1]


def test_dry_run_report_parsing():
    assert runtime_image.dry_run_installs(json.dumps({"install": []})) == []
    report = {"install": [{"metadata": {"name": "stapel-orders", "version": "0.1.2"}}]}
    assert runtime_image.dry_run_installs(json.dumps(report)) == ["stapel-orders==0.1.2"]


# --- the built images: CI / a stand's runner only ----------------------------------

_BUILD = os.environ.get("STAPEL_IMAGE_BUILD_TESTS") == "1" and shutil.which("docker")


@pytest.mark.skipif(not _BUILD, reason="builds images: STAPEL_IMAGE_BUILD_TESTS=1 on CI or a stand runner")
@pytest.mark.parametrize("composite", ["shop", "booking", "classified", "social"])
def test_preview_image_is_thin(composite):
    report = runtime_image.thin_check(composite, "fleet")
    assert report["ok"], report["failures"]
    assert report["backend_unique_mb"] <= runtime_image.BACKEND_UNIQUE_BUDGET_MB
    assert report["dry_run_installs"] == []
    assert report["frontend_node_modules"] == []


@pytest.mark.skipif(not _BUILD, reason="builds images: STAPEL_IMAGE_BUILD_TESTS=1 on CI or a stand runner")
def test_preview_image_upstream_still_builds():
    assert runtime_image.thin_check("shop", "upstream")["ok"]
