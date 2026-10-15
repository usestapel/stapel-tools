"""CFG008: a CONFIG.MD row whose source is outside env|vault|settings.

stapel-core's config manifest reader refuses such a row at boot, so a library
shipping one fails `manage.py check` in every project that installs it."""
from pathlib import Path

from stapel_tools.config_lint import lint_project
from stapel_tools.config_manifest import parse_config_md

SETTINGS = 'from stapel_core.config import get_config\nSECRET_KEY = get_config("SECRET_KEY")\n'


def _project(tmp_path: Path, rows: str) -> Path:
    proj = tmp_path / "proj"
    (proj / "config").mkdir(parents=True)
    (proj / "config" / "__init__.py").write_text("")
    (proj / "config" / "settings.py").write_text(SETTINGS)
    (proj / "CONFIG.MD").write_text(
        "# CONFIG.MD\n\n## stapel-core\n"
        "| Key | Source | Purpose | Required | Default |\n"
        "|-----|--------|---------|----------|---------|\n"
        "| SECRET_KEY | vault | Django secret | yes | |\n\n"
        "## stapel-cdn\n"
        "| Key | Source | Purpose | Required | Default |\n"
        "|-----|--------|---------|----------|---------|\n"
        + rows
    )
    return proj


def test_unknown_source_fails_with_key_value_and_accepted_set(tmp_path):
    proj = _project(tmp_path, "| FOO | magic | A knob | no | |\n")
    findings = [f for f in lint_project(proj) if f.rule == "CFG008"]
    assert len(findings) == 1
    message = findings[0].message
    assert "'FOO'" in message and "'magic'" in message
    assert "env, vault, settings" in message
    assert findings[0].line == 11


def test_env_vault_and_settings_rows_pass(tmp_path):
    proj = _project(
        tmp_path,
        "| BAR | env | A knob | no | |\n"
        "| BAZ | vault | A knob | no | |\n"
        "| MAX_UPLOAD_MB | settings | A constant | no | 10 |\n",
    )
    assert [f for f in lint_project(proj) if f.level == "error"] == []


def test_every_bad_row_is_reported_not_only_the_first(tmp_path):
    proj = _project(tmp_path, "| FOO | magic | x | no | |\n| QUX | file | x | no | |\n")
    keys = sorted(f.message.split("'")[1] for f in lint_project(proj) if f.rule == "CFG008")
    assert keys == ["FOO", "QUX"]


def test_parser_still_raises_without_a_collector():
    import pytest

    from stapel_tools.config_manifest import ConfigManifestError

    text = "| Key | Source | Purpose |\n|---|---|---|\n| FOO | magic | x |\n"
    with pytest.raises(ConfigManifestError):
        parse_config_md(text)
    bad: list = []
    assert parse_config_md(text, bad_sources=bad) == []
    assert bad == [(3, "FOO", "magic")]
