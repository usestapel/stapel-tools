"""A library that requires a setting must say so, and the scaffold must read it.

``stapel_gdpr`` raises the boot-fatal ``gdpr.E001`` when
``STAPEL_GDPR["DATA_OWNERS"]`` is empty. Both example apps in this workspace
were generated with the app installed and the setting emitted nowhere, so both
were dead on arrival — and the scaffold could not have fixed it even if it had
tried: ``validate_module_config`` HARD-REJECTED unknown keys, and
``DATA_OWNERS`` is not an axis in stapel-gdpr's ``capabilities.json``, so a
caller supplying it was refused.

Two halves, both pinned here:

* the DECLARATION — ``capabilities.json`` grows ``required_settings``, with
  enough shape (``kind``/``example``) for a generator to emit a correct
  placeholder and enough prose (``why``/``unset_check``) for a human;
* the GATE — generating a project with such a library and no configuration
  fails at GENERATION time, naming the keys and handing back the block that
  fixes it.

Every test here builds its own fixture workspace, so the gate is exercised in
any checkout. That is deliberate: the pre-existing four-lib proof is
``skipif``-guarded on the libs being importable, which is how a gate can exist
and be skipped in the environment that matters.
"""
import json

import pytest

import stapel_tools._module_config as _module_config
from stapel_tools._module_config import (
    check_required_settings,
    known_config_keys,
    render_required_placeholder_block,
    required_settings,
)
from stapel_tools.assemble_scaffold import assemble_scaffold

GDPR_REQUIRED = [
    {
        "key": "DATA_OWNERS",
        "kind": "list",
        "example": ["auth", "profiles"],
        "why": "Every store that holds personal data; an empty list certifies nothing.",
        "unset_check": "gdpr.E001",
    },
    {
        "key": "DATA_OWNERS_VERSION",
        "kind": "str",
        "example": "2026-01-01.1",
        "why": "Which inventory certified a given erasure.",
        "unset_check": "gdpr.W003",
    },
]


@pytest.fixture
def workspace(tmp_path_factory, monkeypatch):
    """A mini registry: gdpr declares required settings, auth declares none."""
    root = tmp_path_factory.mktemp("required_settings_workspace")
    gdpr = root / "stapel-gdpr" / "docs"
    gdpr.mkdir(parents=True)
    (gdpr / "capabilities.json").write_text(json.dumps({
        "module": "stapel-gdpr",
        "axes": [{"key": "REMOTE_DELETION_SERVICES"}],
        "required_settings": GDPR_REQUIRED,
        "extension_points": [{"name": "GDPR_PROVIDERS"}],
    }))
    auth = root / "stapel-auth" / "docs"
    auth.mkdir(parents=True)
    (auth / "capabilities.json").write_text(json.dumps({
        "module": "stapel-auth",
        "axes": [{"key": "AUTH_PASSWORD_LOGIN"}],
        "extension_points": [],
    }))
    monkeypatch.setattr(_module_config, "_default_workspace_root", lambda: root)
    return root


# ---------------------------------------------------------------------------
# the declaration
# ---------------------------------------------------------------------------


def test_a_module_declares_what_installing_it_makes_mandatory(workspace):
    entries = required_settings("gdpr")
    assert [e["key"] for e in entries] == ["DATA_OWNERS", "DATA_OWNERS_VERSION"]


def test_a_module_that_declares_nothing_requires_nothing(workspace):
    assert required_settings("auth") == []


def test_an_unswept_module_requires_nothing(workspace):
    """Silence is 'no mandatory settings', not 'unknown' — most libraries
    genuinely have none, and inventing a requirement would block every
    project."""
    assert required_settings("not-a-real-module") == []


def test_required_keys_are_accepted_by_the_validator(workspace):
    """The gap that made the scaffold unable to fix this: DATA_OWNERS is not
    an axis, so a caller supplying it was hard-rejected."""
    keys = known_config_keys("gdpr")
    assert "DATA_OWNERS" in keys
    assert "REMOTE_DELETION_SERVICES" in keys  # axes still there
    assert "GDPR_PROVIDERS" in keys  # extension points still there


def test_the_declaration_carries_shape_for_a_placeholder(workspace):
    block = render_required_placeholder_block("gdpr", required_settings("gdpr"))
    assert "STAPEL_GDPR = {" in block
    assert '"DATA_OWNERS": [\'auth\', \'profiles\'],' in block
    assert '"DATA_OWNERS_VERSION": \'2026-01-01.1\',' in block
    assert "# Every store that holds personal data" in block


def test_a_declaration_without_an_example_still_gets_the_right_shape():
    """A generator must never emit a placeholder of the wrong type."""
    block = render_required_placeholder_block(
        "mod", [{"key": "OWNERS", "kind": "list"}, {"key": "NAME", "kind": "str"}]
    )
    assert '"OWNERS": [],' in block
    assert '"NAME": \'\',' in block


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


def test_installing_the_library_with_no_configuration_is_refused(workspace):
    with pytest.raises(SystemExit) as exc:
        check_required_settings(["gdpr"], None)
    message = str(exc.value)
    assert "DATA_OWNERS" in message
    assert "gdpr.E001" in message, "the refusal names the check it prevents"
    assert "STAPEL_GDPR = {" in message, "the refusal hands back the fix"


def test_a_partial_configuration_is_refused_naming_only_what_is_missing(workspace):
    with pytest.raises(SystemExit) as exc:
        check_required_settings(["gdpr"], {"gdpr": {"DATA_OWNERS": ["auth"]}})
    message = str(exc.value)
    assert "DATA_OWNERS_VERSION" in message
    assert '"DATA_OWNERS":' not in message


def test_an_empty_value_is_not_a_value(workspace):
    """``DATA_OWNERS: []`` is exactly the state gdpr.E001 fires on — accepting
    it would let the gate be satisfied by the defect it guards against."""
    with pytest.raises(SystemExit):
        check_required_settings(
            ["gdpr"], {"gdpr": {"DATA_OWNERS": [], "DATA_OWNERS_VERSION": "1"}}
        )


def test_a_complete_configuration_passes(workspace):
    check_required_settings(
        ["gdpr"],
        {"gdpr": {"DATA_OWNERS": ["auth"], "DATA_OWNERS_VERSION": "2026-01-01.1"}},
    )


def test_a_module_that_declares_nothing_never_blocks(workspace):
    check_required_settings(["auth"], None)


# ---------------------------------------------------------------------------
# ...and it is wired into generation, not merely importable
# ---------------------------------------------------------------------------


def test_generating_a_project_with_gdpr_and_no_config_fails_at_generation_time(
    workspace, tmp_path
):
    with pytest.raises(SystemExit) as exc:
        assemble_scaffold(
            "deadonarrival", libs=["gdpr"], output_dir=tmp_path, verify=False
        )
    assert "DATA_OWNERS" in str(exc.value)
    assert not (tmp_path / "deadonarrival").exists(), (
        "the refusal must land before any file is written"
    )


def test_generating_with_the_config_supplied_succeeds(workspace, tmp_path):
    result = assemble_scaffold(
        "alive", libs=["gdpr"],
        config={"gdpr": {
            "DATA_OWNERS": ["auth", "profiles"],
            "DATA_OWNERS_VERSION": "2026-01-01.1",
        }},
        output_dir=tmp_path, verify=False,
    )
    settings = (result.project_dir / "config" / "settings.py").read_text()
    assert "STAPEL_GDPR = {" in settings
    assert '"DATA_OWNERS": [\'auth\', \'profiles\'],' in settings
