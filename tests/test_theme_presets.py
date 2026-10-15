"""--theme: every preset (and the default) passes @stapel/tokens' own
contrast gate, and the embedded default is the package's theme.default.json."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from stapel_tools._theme_presets import DEFAULT_THEME, THEME_PRESETS, render_theme_json
from stapel_tools.create_project import create_project

_TOKENS = Path(__file__).resolve().parents[2] / "stapel-react" / "packages" / "tokens"
_BIN = _TOKENS / "bin" / "stapel-tokens.mjs"


def test_default_theme_is_the_packages_own():
    if not (_TOKENS / "theme.default.json").exists():
        pytest.skip("no sibling stapel-react checkout")
    shipped = json.loads((_TOKENS / "theme.default.json").read_text())
    shipped.pop("_comment", None)
    assert DEFAULT_THEME == shipped


@pytest.mark.parametrize("preset", list(THEME_PRESETS))
def test_preset_passes_the_tokens_contrast_gate(preset, tmp_path):
    if not _BIN.exists() or shutil.which("node") is None:
        pytest.skip("needs node and the sibling @stapel/tokens bin")
    theme = tmp_path / "stapel.theme.json"
    theme.write_text(render_theme_json("App", preset))
    out = subprocess.run(
        ["node", str(_BIN), "--theme", str(theme), "--out", str(tmp_path / "out"),
         "--targets", "core"],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "validation error" not in out.stdout + out.stderr


def test_preset_swaps_only_the_brand_ramp():
    default = json.loads(render_theme_json("App"))
    ocean = json.loads(render_theme_json("App", "ocean"))
    assert ocean["ramps"]["brand"] == THEME_PRESETS["ocean"]
    assert ocean["core"] == default["core"]
    assert "ocean" in ocean["_comment"]


def test_theme_path_is_copied(tmp_path):
    host = tmp_path / "mine.json"
    host.write_text(json.dumps({"ramps": {"brand": THEME_PRESETS["forest"]}}))
    assert json.loads(render_theme_json("App", str(host))) == {
        "ramps": {"brand": THEME_PRESETS["forest"]},
    }


def test_unknown_theme_refused():
    with pytest.raises(ValueError):
        render_theme_json("App", "neon")


def test_generated_project_writes_the_chosen_preset(tmp_path):
    create_project(
        name="app", project_type="monolith", title="App", url="https://app.example",
        company_name="Acme", company_email="hi@app.example", modules=["core"],
        output_dir=tmp_path, use_submodules=False, init_git=False, theme="crimson",
    )
    theme = json.loads((tmp_path / "app" / "frontend" / "stapel.theme.json").read_text())
    assert theme["ramps"]["brand"] == THEME_PRESETS["crimson"]
