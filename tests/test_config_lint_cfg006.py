"""CFG006 — a library offers a setting that it never reads itself.

Caught live (2026-08-08): `stapel-auth` declared
``LOGIN_NOTIFICATION_ENABLED`` with a False default, documented it in
MODULE.md — and read it in zero lines. The "suspicious login" email always
sent; deployment had no way to turn it off, while the docs claimed
otherwise. The first thing a new user saw from the product was an alert.

WHY NO GATE CAUGHT THIS. The whole CFG002-CFG005 family depends on
CONFIG.MD, and `stapel-auth` has no CONFIG.MD — so the family skipped
silently exactly where the defect lived. CFG006 therefore doesn't depend on
the registry: both halves of the question, declaration and consumption,
live entirely in code.
"""
import textwrap
from pathlib import Path

import pytest

from stapel_tools.config_lint import (
    collect_key_consumption,
    collect_offered_knobs,
    lint_project,
)


def _lib(tmp_path: Path, conf: str, extra: dict[str, str] | None = None) -> Path:
    root = tmp_path / "stapel_thing"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "stapel-thing"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "conf.py").write_text(textwrap.dedent(conf), encoding="utf-8")
    for name, body in (extra or {}).items():
        (root / name).write_text(textwrap.dedent(body), encoding="utf-8")
    return root


CONF_WITH_DEAD_KNOB = """
    from stapel_core.conf import AppSettings

    DEFAULTS = {
        'FRONTEND_URL': None,
        'LOGIN_NOTIFICATION_ENABLED': False,
    }

    thing_settings = AppSettings('STAPEL_THING', defaults=DEFAULTS)
"""


def _codes(root: Path) -> list[str]:
    return [f.rule for f in lint_project(root)]


def _cfg006_keys(root: Path) -> list[str]:
    return [
        f.message.split("'")[1] for f in lint_project(root) if f.rule == "CFG006"
    ]


class TestCatchesTheRealDefect:
    def test_declared_but_unread_knob_is_named(self, tmp_path):
        root = _lib(tmp_path, CONF_WITH_DEAD_KNOB, {
            "tasks.py": """
                from .conf import thing_settings

                def send():
                    return thing_settings.FRONTEND_URL
            """,
        })
        assert _cfg006_keys(root) == ["LOGIN_NOTIFICATION_ENABLED"]

    def test_wired_knob_is_silent(self, tmp_path):
        # The exact fix that closed the incident.
        root = _lib(tmp_path, CONF_WITH_DEAD_KNOB, {
            "tasks.py": """
                from .conf import thing_settings

                def send():
                    if not thing_settings.LOGIN_NOTIFICATION_ENABLED:
                        return
                    return thing_settings.FRONTEND_URL
            """,
        })
        assert _cfg006_keys(root) == []

    def test_declaration_does_not_count_as_a_read(self, tmp_path):
        """The subtlest part: the key is a string inside ``defaults`` itself.

        Count it as a mention and the rule would NEVER fire, while staying
        green and convincing.
        """
        root = _lib(tmp_path, CONF_WITH_DEAD_KNOB)
        assert "LOGIN_NOTIFICATION_ENABLED" in _cfg006_keys(root)


class TestDoesNotDependOnTheRegistry:
    def test_fires_without_config_md(self, tmp_path):
        # Exactly where the defect lived: no CONFIG.MD, the whole
        # CFG002-CFG005 family skips. CFG006 must still run.
        root = _lib(tmp_path, CONF_WITH_DEAD_KNOB)
        assert not (root / "CONFIG.MD").exists()
        assert "CFG006" in _codes(root)


class TestStaysQuiet:
    def test_unrelated_dicts_do_not_count_as_settings(self, tmp_path):
        """Type registries, language lists, and other DEFAULTS-shaped dicts.

        The wide net gave 82 fleet-wide "hits" against 4 from this rule —
        it would have been disabled on day one.
        """
        root = _lib(tmp_path, """
            from stapel_core.conf import AppSettings

            NOTIFICATION_DEFAULTS = {
                'new_device_login': {'channels': ['email']},
                'suspicious_login': {'channels': ['email']},
            }

            DEFAULTS = {'FRONTEND_URL': None}

            thing_settings = AppSettings('STAPEL_THING', defaults=DEFAULTS)
        """, {
            "use.py": """
                from .conf import thing_settings
                URL = thing_settings.FRONTEND_URL
            """,
        })
        assert _cfg006_keys(root) == []

    def test_nested_blocks_are_not_unrolled(self, tmp_path):
        # A block's inner keys are read through the block itself, not the
        # namespace — requiring a separate mention for them would flag
        # correct code.
        root = _lib(tmp_path, """
            from stapel_core.conf import AppSettings

            DEFAULTS = {
                'VECTOR': {'DIM': 1536, 'EF_CONSTRUCTION': 200},
            }

            thing_settings = AppSettings('STAPEL_THING', defaults=DEFAULTS)
        """, {
            "use.py": """
                from .conf import thing_settings
                CFG = thing_settings.VECTOR
            """,
        })
        assert _cfg006_keys(root) == []

    def test_read_by_string_not_attribute_still_counts(self, tmp_path):
        # A setting can also be read through a helper: `_resolve("KEY",
        # ...)`. Requiring one canonical form would produce false positives.
        root = _lib(tmp_path, CONF_WITH_DEAD_KNOB, {
            "use.py": """
                from .conf import thing_settings

                def flag():
                    return getattr(thing_settings, 'LOGIN_NOTIFICATION_ENABLED')

                URL = thing_settings.FRONTEND_URL
            """,
        })
        assert _cfg006_keys(root) == []


class TestSuppression:
    def test_noqa_on_the_key_line_clears_it(self, tmp_path):
        # A deliberate reservation for a not-yet-built path is legitimate,
        # but it must be WRITTEN, not implied.
        root = _lib(tmp_path, """
            from stapel_core.conf import AppSettings

            DEFAULTS = {
                'FRONTEND_URL': None,
                'MAX_AUDIO_SIZE': 50 * 1024 * 1024,  # noqa: CFG006
            }

            thing_settings = AppSettings('STAPEL_THING', defaults=DEFAULTS)
        """, {
            "use.py": """
                from .conf import thing_settings
                URL = thing_settings.FRONTEND_URL
            """,
        })
        assert _cfg006_keys(root) == []

    def test_bare_noqa_also_clears_it(self, tmp_path):
        root = _lib(tmp_path, """
            from stapel_core.conf import AppSettings

            DEFAULTS = {'MAX_AUDIO_SIZE': 1}  # noqa

            thing_settings = AppSettings('STAPEL_THING', defaults=DEFAULTS)
        """)
        assert _cfg006_keys(root) == []


class TestCollectors:
    def test_offered_knobs_is_only_the_defaults_passed_to_appsettings(self, tmp_path):
        root = _lib(tmp_path, """
            from stapel_core.conf import AppSettings

            UNRELATED_DEFAULTS = {'NOT_A_KNOB': 1}
            DEFAULTS = {'A': 1, 'B': 2}

            thing_settings = AppSettings('STAPEL_THING', defaults=DEFAULTS)
        """)
        assert sorted(collect_offered_knobs(root)) == ["A", "B"]

    def test_consumption_sees_attribute_name_and_string(self, tmp_path):
        root = _lib(tmp_path, "DEFAULTS = {}\n", {
            "use.py": """
                VALUE = obj.SOME_ATTR
                other = "SOME_STRING"
                call(kwarg_name=1)
            """,
        })
        consumed = collect_key_consumption(root, {})
        assert {"SOME_ATTR", "SOME_STRING", "kwarg_name"} <= consumed
