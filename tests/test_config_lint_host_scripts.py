"""CFG001 scope: host scripts in tools/, e2e/, scripts/ and the file marker."""
from pathlib import Path

from stapel_tools.config_lint import lint_project

SETTINGS = 'from stapel_core.config import get_config\nSECRET_KEY = get_config("SECRET_KEY")\n'


def _project(tmp_path: Path, files: dict) -> Path:
    proj = tmp_path / "proj"
    (proj / "config").mkdir(parents=True)
    (proj / "config" / "__init__.py").write_text("")
    (proj / "config" / "settings.py").write_text(SETTINGS)
    for rel, content in files.items():
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return proj


def _cfg001(proj: Path) -> list:
    return [f for f in lint_project(proj) if f.rule == "CFG001"]


HOST = 'import os\nimport json\nBASE = os.environ["E2E_BASE_URL"]\n'


def test_host_script_in_tools_is_not_reported(tmp_path):
    proj = _project(tmp_path, {"tools/seed.py": HOST, "e2e/run.py": HOST,
                               "deploy/scripts/smoke.py": HOST})
    assert _cfg001(proj) == []


def test_tools_file_importing_django_is_reported(tmp_path):
    proj = _project(tmp_path, {"tools/seed.py": "import django\n" + HOST})
    assert len(_cfg001(proj)) == 1


def test_tools_file_importing_first_party_is_reported(tmp_path):
    proj = _project(tmp_path, {
        "apps/shop/models.py": "",
        "tools/seed.py": "from apps.shop.models import Item\n" + HOST,
    })
    assert len(_cfg001(proj)) == 1


def test_tools_file_importing_stapel_lib_is_reported(tmp_path):
    proj = _project(tmp_path, {"scripts/x.py": "from stapel_core.config import get_config\n" + HOST})
    assert len(_cfg001(proj)) == 1


def test_marker_exempts_file_anywhere(tmp_path):
    proj = _project(tmp_path, {"apps/shop/probe.py": "# stapel: host-script\nimport django\n" + HOST})
    assert _cfg001(proj) == []


def test_marker_after_line_ten_does_not_exempt(tmp_path):
    proj = _project(tmp_path, {"apps/shop/probe.py": "\n" * 10 + "# stapel: host-script\n" + HOST})
    assert len(_cfg001(proj)) == 1


def test_app_module_read_still_reported(tmp_path):
    proj = _project(tmp_path, {"apps/shop/views.py": 'import os\nX = os.getenv("SECRET_KEY")\n'})
    assert len(_cfg001(proj)) == 1
