"""stapel-config-lint + config_manifest tests (static-scaffold-and-config.md §2).

CFG001 (read outside settings), CFG002 (settings read undeclared in CONFIG.MD),
CFG003 (CONFIG.MD row never read), a clean project, the noqa escape, and the
CONFIG.MD parser + lib aggregation the advisor/assembler use.
"""
from pathlib import Path

import pytest

from stapel_tools.config_lint import lint_project
from stapel_tools.config_manifest import (
    ConfigManifestError,
    aggregate_config_md,
    parse_config_md,
    render_config_md,
)

CONFIG_MD = """\
# CONFIG.MD — proj

## stapel-core
| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| SECRET_KEY | vault | Django secret | yes | |

## project
| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| LOG_LEVEL | env | Root log level | no | INFO |
"""

SETTINGS_CLEAN = """\
import os
from stapel_core.config import get_config

SECRET_KEY = get_config("SECRET_KEY")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
"""


def _make_project(tmp_path: Path, *, config_md=CONFIG_MD, settings=SETTINGS_CLEAN, extra=None):
    proj = tmp_path / "proj"
    (proj / "config").mkdir(parents=True)
    (proj / "config" / "__init__.py").write_text("")
    (proj / "config" / "settings.py").write_text(settings, encoding="utf-8")
    if config_md is not None:
        (proj / "CONFIG.MD").write_text(config_md, encoding="utf-8")
    for rel, content in (extra or {}).items():
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return proj


def _codes(findings):
    return sorted(f.rule for f in findings)


# --- clean -----------------------------------------------------------------

def test_clean_project_has_no_findings(tmp_path):
    proj = _make_project(tmp_path)
    assert lint_project(proj) == []


# --- CFG001 ----------------------------------------------------------------

def test_cfg001_read_outside_settings(tmp_path):
    proj = _make_project(
        tmp_path,
        extra={"apps/thing/views.py": 'import os\nX = os.getenv("SECRET_KEY")\n'},
    )
    findings = lint_project(proj)
    assert "CFG001" in _codes(findings)
    f = next(f for f in findings if f.rule == "CFG001")
    assert f.path.endswith("views.py")


def test_cfg001_covers_environ_subscript_and_get_secret(tmp_path):
    code = (
        "import os\n"
        "from stapel_core.secrets import get_secret\n"
        'A = os.environ["SECRET_KEY"]\n'
        'B = get_secret("LOG_LEVEL")\n'
    )
    proj = _make_project(tmp_path, extra={"apps/x/svc.py": code})
    cfg001 = [f for f in lint_project(proj) if f.rule == "CFG001"]
    assert len(cfg001) == 2


def test_cfg001_suppressed_by_noqa(tmp_path):
    proj = _make_project(
        tmp_path,
        extra={"apps/x/svc.py": 'import os\nX = os.getenv("SECRET_KEY")  # noqa: CFG001\n'},
    )
    assert "CFG001" not in _codes(lint_project(proj))


def test_cfg001_setdefault_is_not_a_read(tmp_path):
    # manage.py / wsgi set DJANGO_SETTINGS_MODULE via setdefault — a write.
    proj = _make_project(
        tmp_path,
        extra={"manage.py": 'import os\nos.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")\n'},
    )
    assert "CFG001" not in _codes(lint_project(proj))


# --- CFG002 ----------------------------------------------------------------

def test_cfg002_settings_read_not_in_config_md(tmp_path):
    settings = SETTINGS_CLEAN + 'DATA_DIR = os.environ["DATA_DIR"]\n'
    proj = _make_project(tmp_path, settings=settings)
    findings = lint_project(proj)
    assert "CFG002" in _codes(findings)
    assert any("DATA_DIR" in f.message for f in findings if f.rule == "CFG002")


def test_cfg002_reported_once_per_key(tmp_path):
    settings = SETTINGS_CLEAN + 'A = os.environ["DATA_DIR"]\nB = os.environ.get("DATA_DIR")\n'
    proj = _make_project(tmp_path, settings=settings)
    cfg002 = [f for f in lint_project(proj) if f.rule == "CFG002"]
    assert len(cfg002) == 1


# --- CFG003 ----------------------------------------------------------------

def test_cfg003_declared_but_never_read(tmp_path):
    config_md = CONFIG_MD + "| UNUSED_KEY | env | dead | no | |\n"
    proj = _make_project(tmp_path, config_md=config_md)
    findings = lint_project(proj)
    assert "CFG003" in _codes(findings)
    assert any("UNUSED_KEY" in f.message for f in findings if f.rule == "CFG003")


def test_cfg003_exempts_library_owned_keys(tmp_path):
    # SECRET_KEY is under ## stapel-core and read via get_config here; but even
    # a core key NOT read in the project must not trip CFG003.
    config_md = """\
## stapel-core
| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| JWT_SECRET_KEY | vault | never read in this project | no | |

## project
| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| LOG_LEVEL | env | log level | no | INFO |
"""
    settings = 'import os\nLOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")\n'
    proj = _make_project(tmp_path, config_md=config_md, settings=settings)
    assert "CFG003" not in _codes(lint_project(proj))


# --- no CONFIG.MD ----------------------------------------------------------

def test_missing_config_md_skips_002_003_keeps_001(tmp_path):
    proj = _make_project(
        tmp_path, config_md=None,
        extra={"apps/x/svc.py": 'import os\nX = os.getenv("SECRET_KEY")\n'},
    )
    notes: list[str] = []
    findings = lint_project(proj, notes=notes)
    codes = _codes(findings)
    assert "CFG001" in codes
    assert "CFG002" not in codes and "CFG003" not in codes
    assert any("no CONFIG.MD" in n for n in notes)


def test_missing_config_md_is_a_warning_not_only_a_note(tmp_path):
    """#133: the registry law must not be opt-out by omission.

    A project with no CONFIG.MD used to get a *note* — invisible to any
    exit-code gate, and every downstream project's pre-commit config just
    recorded the fact ("CFG002/CFG003 skipped anyway") and moved on. CFG000
    is warning-level: it shows in every stapel-verify run, and still does not
    fail a build that has not done the sweep.
    """
    proj = _make_project(tmp_path, config_md=None)
    findings = lint_project(proj)
    cfg000 = [f for f in findings if f.rule == "CFG000"]
    assert len(cfg000) == 1
    assert cfg000[0].level == "warning"
    assert "CONFIG.MD" in cfg000[0].message


def test_cfg000_not_raised_for_a_unit_with_no_config_surface(tmp_path):
    """A repo that reads no configuration and is neither a Django service nor
    a stapel distribution (a TS package, a spec repo) has no registry to be
    missing — CFG000 there would be pure noise."""
    proj = tmp_path / "tsonly"
    proj.mkdir()
    (proj / "index.ts").write_text("export const x = 1;\n", encoding="utf-8")
    assert lint_project(proj) == []


def test_cfg000_present_once_a_config_md_exists(tmp_path):
    assert "CFG000" not in _codes(lint_project(_make_project(tmp_path)))


# --- CFG005 (library repos) ------------------------------------------------

LIB_PYPROJECT = '[project]\nname = "stapel-widget"\nversion = "0.1.0"\n'

LIB_CONF = '''\
from stapel_core.conf import AppSettings

DEFAULTS = {
    "WIDGET_TIMEOUT": 30,
    "NESTED": {"INNER_KNOB": True},
}

widget_settings = AppSettings("STAPEL_WIDGET", defaults=DEFAULTS)
'''


def _make_library(tmp_path, *, rows: str, conf=LIB_CONF, extra=None):
    lib = tmp_path / "stapel-widget"
    lib.mkdir(parents=True)
    (lib / "pyproject.toml").write_text(LIB_PYPROJECT, encoding="utf-8")
    (lib / "conf.py").write_text(conf, encoding="utf-8")
    (lib / "CONFIG.MD").write_text(
        "# CONFIG.MD — stapel-widget\n\n"
        "## stapel-widget\n\n"
        "| Key | Source | Purpose | Required | Default |\n"
        "|-----|--------|---------|----------|---------|\n" + rows,
        encoding="utf-8",
    )
    for rel, content in (extra or {}).items():
        p = lib / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return lib


def test_cfg005_row_backed_by_the_namespace_defaults_is_clean(tmp_path):
    lib = _make_library(tmp_path, rows="| WIDGET_TIMEOUT | env | how long | no | 30 |\n")
    assert "CFG005" not in _codes(lint_project(lib))


def test_cfg005_documented_switch_that_exists_nowhere(tmp_path):
    """The motivating defect: a switch documented as "turn it off without a
    deploy" that was never introduced in the settings namespace at all — so
    it could not be turned off. CFG001 was the wrong class for it (there was
    no read anywhere, which IS the defect)."""
    lib = _make_library(
        tmp_path,
        rows=(
            "| WIDGET_TIMEOUT | env | how long | no | 30 |\n"
            "| WIDGET_CACHE_ENABLED | env | turn the cache off without a deploy | no | true |\n"
        ),
    )
    findings = lint_project(lib)
    cfg005 = [f for f in findings if f.rule == "CFG005"]
    assert len(cfg005) == 1, [f.message for f in findings]
    assert "WIDGET_CACHE_ENABLED" in cfg005[0].message
    assert cfg005[0].level == "error"
    assert cfg005[0].path.endswith("CONFIG.MD")


def test_cfg005_sees_nested_block_keys(tmp_path):
    lib = _make_library(tmp_path, rows="| INNER_KNOB | env | nested tuning | no | true |\n")
    assert "CFG005" not in _codes(lint_project(lib))


def test_cfg005_accepts_a_key_wired_without_appsettings(tmp_path):
    """Real libs wire keys the AST read-matcher cannot follow — a helper
    (``_resolve("MOUNT", "VAULT_KV_MOUNT", ...)``), a module constant, a flat
    Django setting. Those are wiring, so they must not be reported."""
    lib = _make_library(
        tmp_path,
        rows=(
            "| WIDGET_TIMEOUT | env | how long | no | 30 |\n"
            "| WIDGET_ADDR | env | endpoint | no | |\n"
        ),
        extra={"client.py": 'def build():\n    return _resolve("addr", "WIDGET_ADDR", "")\n'},
    )
    assert "CFG005" not in _codes(lint_project(lib))


def test_cfg005_ignores_a_key_named_only_in_prose(tmp_path):
    """A docstring mention is documentation, which is the thing in question."""
    lib = _make_library(
        tmp_path,
        rows=(
            "| WIDGET_TIMEOUT | env | how long | no | 30 |\n"
            "| WIDGET_ADDR | env | endpoint | no | |\n"
        ),
        extra={"client.py": '"""Set WIDGET_ADDR to point at the service."""\n'},
    )
    cfg005 = [f for f in lint_project(lib) if f.rule == "CFG005"]
    assert len(cfg005) == 1 and "WIDGET_ADDR" in cfg005[0].message


def test_cfg005_only_checks_rows_this_library_owns(tmp_path):
    """A lib re-documenting another owner's key (## stapel-core) is not the
    owner of that row and must not be charged for it."""
    lib = _make_library(tmp_path, rows="| WIDGET_TIMEOUT | env | how long | no | 30 |\n")
    (lib / "CONFIG.MD").write_text(
        (lib / "CONFIG.MD").read_text(encoding="utf-8")
        + "\n## stapel-core\n\n"
        "| Key | Source | Purpose | Required | Default |\n"
        "|-----|--------|---------|----------|---------|\n"
        "| SECRET_KEY | vault | Django secret | yes | |\n",
        encoding="utf-8",
    )
    assert "CFG005" not in _codes(lint_project(lib))


def test_cfg005_does_not_run_in_a_consuming_service(tmp_path):
    """CFG005 is the library-side mirror of CFG003. In a service (manage.py),
    library-owned rows stay exempt — the service does not ship the lib."""
    proj = _make_project(tmp_path, extra={"manage.py": "import os\n"})
    (proj / "pyproject.toml").write_text('[project]\nname = "stapel-thing"\n', encoding="utf-8")
    assert "CFG005" not in _codes(lint_project(proj))


# --- tests dir is skipped --------------------------------------------------

def test_reads_in_tests_dir_not_flagged(tmp_path):
    proj = _make_project(
        tmp_path,
        extra={"tests/test_x.py": 'import os\nX = os.getenv("SECRET_KEY")\n'},
    )
    assert "CFG001" not in _codes(lint_project(proj))


# --- manifest parsing ------------------------------------------------------

def test_parse_owner_and_fields():
    entries = parse_config_md(CONFIG_MD)
    by_key = {e.key: e for e in entries}
    assert by_key["SECRET_KEY"].source == "vault"
    assert by_key["SECRET_KEY"].required is True
    assert by_key["SECRET_KEY"].owner == "stapel-core"
    assert by_key["SECRET_KEY"].library_owned is True
    assert by_key["LOG_LEVEL"].owner == "project"
    assert by_key["LOG_LEVEL"].library_owned is False
    assert by_key["LOG_LEVEL"].default == "INFO"


def test_parse_bad_source_raises():
    with pytest.raises(ConfigManifestError):
        parse_config_md("| Key | Source |\n|--|--|\n| X | redis |\n")


def test_render_round_trips_and_escapes_pipes():
    entries = parse_config_md(CONFIG_MD)
    entries[0].purpose = "a | b"  # pipe must be escaped, not corrupt the table
    text = render_config_md(entries, title="T")
    reparsed = {e.key: e for e in parse_config_md(text)}
    assert reparsed["SECRET_KEY"].purpose == "a | b"
    assert set(reparsed) == {"SECRET_KEY", "LOG_LEVEL"}


# --- aggregation -----------------------------------------------------------

def test_aggregate_core_present_others_missing():
    text, missing = aggregate_config_md(["core", "auth"], title="CONFIG.MD — demo")
    assert "auth" in missing  # no CONFIG.MD shipped yet
    assert "## stapel-core" in text
    entries = {e.key: e for e in parse_config_md(text)}
    assert entries["SECRET_KEY"].source == "vault"
    assert entries["SECRET_KEY"].owner == "stapel-core"


def test_aggregate_extra_project_entries():
    from stapel_tools.config_manifest import ConfigEntry

    extra = [ConfigEntry(key="MY_KEY", source="env", purpose="p", owner="project")]
    text, _ = aggregate_config_md(["core"], extra_entries=extra)
    entries = {e.key: e for e in parse_config_md(text)}
    assert "MY_KEY" in entries
    assert entries["MY_KEY"].owner == "project"
