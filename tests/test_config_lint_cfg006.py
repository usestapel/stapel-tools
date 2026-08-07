"""CFG006 — библиотека предлагает настройку, которую сама не читает.

ПРИШПИЛЕНО ЖИВЫМ ДЕФЕКТОМ (08.08.2026, миттудей). `stapel-auth` объявлял
``LOGIN_NOTIFICATION_ENABLED`` с дефолтом False, описывал его в MODULE.md — и
не читал ни одной строкой. Письма «обнаружен подозрительный вход» уходили
безусловно; развёртывание не могло погасить их вообще никак, а документация
обещала обратное. Первым, что видел новый человек от продукта, была тревога.

ПОЧЕМУ ЭТОГО НЕ ЛОВИЛ НИ ОДИН ГЕЙТ. Вся семья CFG002-CFG005 висит на
CONFIG.MD, а у `stapel-auth` CONFIG.MD нет вовсе — и семья молча скипалась
ровно там, где дефект и жил. CFG006 поэтому не зависит от реестра: обе
половины вопроса, объявление и потребление, целиком внутри кода.
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


class TestЛовитНастоящийДефект:
    def test_объявленная_но_нечитаемая_ручка_названа(self, tmp_path):
        root = _lib(tmp_path, CONF_WITH_DEAD_KNOB, {
            "tasks.py": """
                from .conf import thing_settings

                def send():
                    return thing_settings.FRONTEND_URL
            """,
        })
        assert _cfg006_keys(root) == ["LOGIN_NOTIFICATION_ENABLED"]

    def test_подключённая_ручка_молчит(self, tmp_path):
        # Ровно та правка, что закрыла инцидент.
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

    def test_объявление_не_засчитывается_за_чтение(self, tmp_path):
        """Самая тонкая часть: ключ — строка внутри самого ``defaults``.

        Засчитай её за упоминание — и правило не сработает НИКОГДА, оставаясь
        при этом зелёным и убедительным.
        """
        root = _lib(tmp_path, CONF_WITH_DEAD_KNOB)
        assert "LOGIN_NOTIFICATION_ENABLED" in _cfg006_keys(root)


class TestНеЗависитОтРеестра:
    def test_срабатывает_без_config_md(self, tmp_path):
        # Место, где дефект и жил: CONFIG.MD нет, вся семья CFG002-CFG005
        # скипается. CFG006 обязан отработать.
        root = _lib(tmp_path, CONF_WITH_DEAD_KNOB)
        assert not (root / "CONFIG.MD").exists()
        assert "CFG006" in _codes(root)


class TestНеШумит:
    def test_чужие_словари_не_считаются_настройками(self, tmp_path):
        """Реестры типов, списки языков и прочие DEFAULTS-подобные словари.

        Широкий невод давал 82 «находки» по флоту против 4 у этого правила —
        и был бы отключён в первый же день.
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

    def test_вложенные_блоки_не_разворачиваются(self, tmp_path):
        # Внутренние ключи блока читаются самим блоком, а не через namespace —
        # требовать для них отдельного упоминания значит ругаться на исправное.
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

    def test_чтение_строкой_а_не_атрибутом_засчитывается(self, tmp_path):
        # Настройку читают и через хелперы: `_resolve("KEY", ...)`. Требовать
        # одну каноническую форму — значит получить ложные срабатывания.
        root = _lib(tmp_path, CONF_WITH_DEAD_KNOB, {
            "use.py": """
                from .conf import thing_settings

                def flag():
                    return getattr(thing_settings, 'LOGIN_NOTIFICATION_ENABLED')

                URL = thing_settings.FRONTEND_URL
            """,
        })
        assert _cfg006_keys(root) == []


class TestГлушилка:
    def test_noqa_на_строке_ключа_снимает(self, tmp_path):
        # Осознанный резерв под ещё не построенный путь — законный случай,
        # но он обязан быть НАПИСАН, а не подразумеваться.
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

    def test_голый_noqa_тоже_снимает(self, tmp_path):
        root = _lib(tmp_path, """
            from stapel_core.conf import AppSettings

            DEFAULTS = {'MAX_AUDIO_SIZE': 1}  # noqa

            thing_settings = AppSettings('STAPEL_THING', defaults=DEFAULTS)
        """)
        assert _cfg006_keys(root) == []


class TestСборщики:
    def test_предлагаемые_ручки_это_только_defaults_переданный_в_appsettings(self, tmp_path):
        root = _lib(tmp_path, """
            from stapel_core.conf import AppSettings

            UNRELATED_DEFAULTS = {'NOT_A_KNOB': 1}
            DEFAULTS = {'A': 1, 'B': 2}

            thing_settings = AppSettings('STAPEL_THING', defaults=DEFAULTS)
        """)
        assert sorted(collect_offered_knobs(root)) == ["A", "B"]

    def test_потребление_видит_атрибут_имя_и_строку(self, tmp_path):
        root = _lib(tmp_path, "DEFAULTS = {}\n", {
            "use.py": """
                VALUE = obj.SOME_ATTR
                other = "SOME_STRING"
                call(kwarg_name=1)
            """,
        })
        consumed = collect_key_consumption(root, {})
        assert {"SOME_ATTR", "SOME_STRING", "kwarg_name"} <= consumed
