"""The shell template mirror (`stapel_tools._shell_templates`) against
`@stapel/shell-react`'s published `shell-templates.json` (composition spec
§2). Same convention as `scripts/check_nav_manifest_sync.py`: the sibling
checkout is `${SIBLING_ROOT:-..}/stapel-react`, and a checkout without the
file has nothing to compare against — a skip with the reason, not a failure.
"""
import json
import os
from pathlib import Path

import pytest

from stapel_tools._shell_templates import (
    MONOLITH_RENDERABLE,
    SHELL_SLOT_IDS,
    SHELL_TEMPLATES,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _templates_json() -> Path:
    env = os.environ.get("SIBLING_ROOT")
    root = Path(env).expanduser().resolve() if env else REPO_ROOT.parent
    return root / "stapel-react" / "packages" / "shell-react" / "shell-templates.json"


def test_mirror_matches_the_published_shell_templates_json():
    path = _templates_json()
    if not path.is_file():
        pytest.skip(
            f"shell-templates.json not found at {path} — nothing to compare "
            "against (set SIBLING_ROOT to the workspace that carries stapel-react)"
        )
    real = json.loads(path.read_text())
    assert real["slots"] == list(SHELL_SLOT_IDS)
    assert real["templates"] == json.loads(json.dumps(SHELL_TEMPLATES))


def test_every_template_renders_only_vocabulary_slots():
    for name, spec in SHELL_TEMPLATES.items():
        assert set(spec["slots"]) <= set(SHELL_SLOT_IDS), name
        assert set(spec["exclusive"]) <= set(spec["slots"]), name
        assert spec["status"] in ("shipped", "queued"), name


def test_monolith_renders_a_shipped_template_only():
    assert MONOLITH_RENDERABLE == {"topbar-sidenav"}
    for name in MONOLITH_RENDERABLE:
        assert SHELL_TEMPLATES[name]["status"] == "shipped"
