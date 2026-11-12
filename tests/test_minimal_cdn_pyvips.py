"""A minimal project with stapel-cdn migrates on a fresh venv.

``--type minimal --modules auth orders shop`` enabled cdn's ``images``
submodule and listed no pyvips, so the first ``manage.py migrate`` after
``pip install -r requirements.txt`` failed on ``stapel_cdn.images.E001``. The
service tier gets libvips from its Dockerfile; the minimal preset has none, so
it takes the binary wheel. (The live half of this gate: install the generated
requirements into a fresh venv and migrate.)
"""
from stapel_tools.create_project import create_project


def _reqs(tmp_path, project_type, modules):
    create_project(
        name="app", project_type=project_type, title="App", url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=modules,
        output_dir=tmp_path, use_submodules=False, init_git=False,
    )
    root = tmp_path / "app"
    path = root / ("svc-app" if project_type == "monolith" else ".") / "requirements.txt"
    return [ln for ln in path.read_text().splitlines() if ln and not ln.startswith("#")]


def test_minimal_with_cdn_bundles_libvips(tmp_path):
    lines = _reqs(tmp_path, "minimal", ["auth", "orders", "shop"])
    assert "pyvips[binary]>=3.1,<4" in lines
    assert any(ln.startswith("stapel-cdn") for ln in lines)


def test_minimal_without_cdn_has_no_pyvips(tmp_path):
    assert not any("pyvips" in ln for ln in _reqs(tmp_path, "minimal", ["auth"]))


def test_service_tier_keeps_the_system_libvips(tmp_path):
    lines = _reqs(tmp_path, "monolith", ["core", "cdn"])
    assert not any("pyvips" in ln for ln in lines)
    assert any(ln.startswith("stapel-cdn[images]") for ln in lines)
