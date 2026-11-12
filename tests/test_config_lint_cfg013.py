"""CFG013: an environment variable parsed as a boolean by hand.

``os.getenv("K", "True").lower() == "true"`` turns a default-on gate off for a
blank ``K=`` and for ``1``; ``not in ("0", "false", ...)`` opens a default-off
hatch for a blank value or a typo. ``stapel_core.conf.env_bool`` is the one
parser with one answer.
"""
from pathlib import Path

import pytest

from stapel_tools.config_lint import find_hand_rolled_env_bools, lint_project

HAND_ROLLED = [
    'X = os.getenv("GATE", "True").lower() == "true"',
    "X = os.getenv('GATE', 'False').lower() == 'true'",
    'X = os.environ.get("GATE", "").strip().lower() in ("1", "true", "yes", "on")',
    'X = os.getenv("GATE", "true").strip().lower() not in ("", "0", "false", "no", "off")',
    'X = os.environ["GATE"].lower() not in {"0", "false"}',
    'X = os.environ.get("GATE") == "1"',
    'X = "true" == os.getenv("GATE")',
    'X = bool(os.environ.get("GATE"))',
    'X = (os.getenv("GATE") or "true").lower() != "false"',
    'X = os.getenv(GATE_FLAG_NAME, "").lower() in ("", "0", "false")',
]

NOT_A_BOOL = [
    'X = env_bool("GATE", True)',
    'X = os.getenv("STAGE", "dev") == "prod"',
    'X = os.getenv("MODE", "auto").lower() in ("auto", "on", "off")',
    'X = os.getenv("GATE", "True")',
    'X = os.getenv("GATE", "True").lower() == "true"  # noqa: CFG013',
    'X = value.lower() == "true"',
    'X = os.getenv("GATE", "").lower() not in (\n    "0", "false")  # noqa: CFG013',
]


def _project(tmp_path: Path, line: str) -> Path:
    proj = tmp_path / "proj"
    (proj / "config").mkdir(parents=True)
    (proj / "config" / "__init__.py").write_text("")
    (proj / "config" / "settings.py").write_text(
        "import os\nfrom stapel_core.conf import env_bool\n" + line + "\n"
    )
    return proj


@pytest.mark.parametrize("line", HAND_ROLLED)
def test_a_hand_rolled_env_bool_is_reported(tmp_path, line):
    proj = _project(tmp_path, line)
    found = [f for f in lint_project(proj) if f.rule == "CFG013"]
    assert len(found) == 1, line
    assert found[0].line == 3
    assert found[0].level == "warning"
    assert "env_bool" in found[0].message and "GATE" in found[0].message


@pytest.mark.parametrize("line", NOT_A_BOOL)
def test_env_bool_and_non_boolean_reads_are_silent(tmp_path, line):
    proj = _project(tmp_path, line)
    assert find_hand_rolled_env_bools(proj) == [], line


def test_reported_outside_settings_too(tmp_path):
    proj = _project(tmp_path, "")
    (proj / "app.py").write_text(
        'import os\nDEBUGGING = os.getenv("DEBUGGING", "").lower() == "true"\n'
    )
    sites = find_hand_rolled_env_bools(proj)
    assert [(Path(p).name, line, key) for p, line, key in sites] == [
        ("app.py", 2, "DEBUGGING"),
    ]


def test_env_bool_is_a_config_read(tmp_path):
    # Moving to env_bool must not hide the key from the CONFIG.MD rules.
    from stapel_tools.config_lint import collect_reads

    proj = _project(tmp_path, 'X = env_bool("GATE", True)')
    assert [r.key for r in collect_reads(proj)] == ["GATE"]
