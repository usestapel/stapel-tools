"""CFG009 — a library row says `env` for a key AppSettings never reads from env.

stapel-cdn's CONFIG.MD documented ASSET_TYPES, THUMBNAIL_SIZES, the extension
allowlists and the beat schedules as `env`; core refuses a bare env string
for a container-shaped key (stapel_core.conf.E002), and WATERMARK is an
import string core never reads from env (W001). An operator following the
row wrote a variable that crashed the boot or did nothing.
"""
from stapel_tools.config_lint import lint_project

PYPROJECT = '[project]\nname = "stapel-widget"\nversion = "0.1.0"\n'

CONF = '''\
from stapel_core.conf import AppSettings

DEFAULT_TYPES = ("avatar",)

DEFAULTS = {
    "MAX_SIZE": 10,
    "TYPES": DEFAULT_TYPES,
    "SIZES": (16, 32),
    "SCHEDULE": {"minute": "0"},
    "EXTS": frozenset({".jpg"}),
    "HOOK": "",
    "BACKEND": "local",
    "CLASS_PICK": "x.Y",
    "ENGINE": "short",
}

widget_settings = AppSettings(
    "STAPEL_WIDGET",
    defaults=DEFAULTS,
    import_strings=("HOOK", "CLASS_PICK", "ENGINE"),
    no_env=("BACKEND",),
    env_overridable=("CLASS_PICK",),
    env_enum={"ENGINE": ("short", "long")},
)
'''


def _lib(tmp_path, rows: str):
    lib = tmp_path / "stapel-widget"
    lib.mkdir()
    (lib / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (lib / "conf.py").write_text(CONF, encoding="utf-8")
    (lib / "CONFIG.MD").write_text(
        "# CONFIG.MD — stapel-widget\n\n## stapel-widget\n\n"
        "| Key | Source | Purpose | Required | Default |\n"
        "|-----|--------|---------|----------|---------|\n" + rows,
        encoding="utf-8",
    )
    return lib


def _cfg009_keys(lib):
    return sorted(
        f.message.split("'")[1] for f in lint_project(lib) if f.rule == "CFG009"
    )


ALL_KEYS = ["MAX_SIZE", "TYPES", "SIZES", "SCHEDULE", "EXTS", "HOOK", "BACKEND",
            "CLASS_PICK", "ENGINE"]


def test_env_rows_for_env_closed_keys_are_flagged(tmp_path):
    rows = "".join(f"| {k} | env | knob | no | |\n" for k in ALL_KEYS)
    assert _cfg009_keys(_lib(tmp_path, rows)) == [
        "BACKEND", "EXTS", "HOOK", "SCHEDULE", "SIZES", "TYPES",
    ]


def test_settings_rows_are_clean(tmp_path):
    rows = "".join(
        f"| {k} | {'env' if k in ('MAX_SIZE', 'CLASS_PICK', 'ENGINE') else 'settings'} | knob | no | |\n"
        for k in ALL_KEYS
    )
    assert _cfg009_keys(_lib(tmp_path, rows)) == []


def test_the_message_names_the_reason(tmp_path):
    lib = _lib(tmp_path, "| TYPES | env | knob | no | |\n| HOOK | env | knob | no | |\n")
    messages = {f.message.split("'")[1]: f.message for f in lint_project(lib) if f.rule == "CFG009"}
    assert "conf.E002" in messages["TYPES"]
    assert "conf.W001" in messages["HOOK"]
    assert {f.level for f in lint_project(lib) if f.rule == "CFG009"} == {"warning"}


# --- CFG010: an offered key with no CONFIG.MD row ---------------------------


def test_cfg010_warns_about_an_undocumented_offered_key(tmp_path):
    lib = _lib(tmp_path, "| MAX_SIZE | env | knob | no | |\n")
    cfg010 = sorted(
        f.message.split("'")[1] for f in lint_project(lib) if f.rule == "CFG010"
    )
    assert "MAX_SIZE" not in cfg010
    assert "TYPES" in cfg010 and "BACKEND" in cfg010
    assert all(f.level == "warning" for f in lint_project(lib) if f.rule == "CFG010")


def test_cfg010_silent_when_every_key_has_a_row(tmp_path):
    rows = "".join(f"| {k} | settings | knob | no | |\n" for k in ALL_KEYS)
    assert [f for f in lint_project(_lib(tmp_path, rows)) if f.rule == "CFG010"] == []
