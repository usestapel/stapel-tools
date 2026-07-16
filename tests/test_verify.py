"""stapel-verify tests — the aggregator gate that runs the whole lint arsenal.

Builds one fixture project with a deliberate violation for each composed
linter (stapel-lint R006, stapel-adoption-lint ADO001/ADO002/ADO004,
stapel-url-lint URL001, stapel-config-lint CFG001, stapel-migration-lint
MIG001) and asserts every linter contributes to the summed report, the exit
codes, and the ``--json`` shape.
"""
import json

from stapel_tools.verify import LinterReport, main, verify_project

# ---------------------------------------------------------------------------
# fixture builder — one project with a violation for every composed linter
# ---------------------------------------------------------------------------


def make_dirty_project(tmp_path):
    """A project + sibling ``stapel-verifyfixture`` module repo with:

    - R006 (stapel-lint): ``StapelResponse({...})`` raw dict in a views file.
    - ADO001 (adoption-lint): ``stapel_verifyfixture`` installed but never mounted.
    - ADO002 (adoption-lint): a hand-written route shadowing stapel_verifyfixture's
      ``/verifyfixture/ping`` operation.
    - ADO004 (adoption-lint, warning): a dead ``PyJWT`` pin never imported.
    - URL001 (url-lint): a bare ``models.URLField()`` with no max_length.
    - CFG001 (config-lint): an ``os.environ.get`` read outside settings.
    - MIG001 (migration-lint): a destructive ``RemoveField`` without the
      contract-phase marker.
    """
    workspace = tmp_path
    module = workspace / "stapel-verifyfixture"
    (module / "docs").mkdir(parents=True)
    (module / "urls.py").write_text("urlpatterns = []\n")
    (module / "docs" / "schema.json").write_text(json.dumps({
        "openapi": "3.0.3",
        "paths": {"/verifyfixture/ping": {"post": {"operationId": "verifyfixture_op"}}},
    }))

    proj = workspace / "proj"
    (proj / "config").mkdir(parents=True)
    (proj / "config" / "__init__.py").write_text("")
    (proj / "config" / "settings.py").write_text(
        "import os\n\n"
        "ROOT_URLCONF = \"config.urls\"\n"
        "INSTALLED_APPS = [\n"
        "    \"stapel_verifyfixture\",\n"
        "]\n"
        "SECRET_KEY = os.environ.get(\"SECRET_KEY\")\n"
    )
    (proj / "config" / "urls.py").write_text(
        "from django.urls import path\n\n"
        "def login_view(request):\n"
        "    pass\n\n"
        "urlpatterns = [\n"
        "    path(\"verifyfixture/ping\", login_view),\n"
        "]\n"
    )
    (proj / "requirements.txt").write_text("stapel_verifyfixture\nPyJWT\n")

    (proj / "app").mkdir()
    (proj / "app" / "__init__.py").write_text("")
    (proj / "app" / "utils.py").write_text(
        "import os\n\n"
        "def get_flag():\n"
        "    return os.environ.get(\"SOME_FLAG\")\n"
    )
    (proj / "app" / "models.py").write_text(
        "from django.db import models\n\n\n"
        "class Thing(models.Model):\n"
        "    avatar = models.URLField(blank=True, null=True)\n"
    )
    (proj / "app" / "views.py").write_text(
        "from stapel_core.http import StapelResponse\n\n\n"
        "def thing_view(request):\n"
        "    return StapelResponse({\"ok\": True})\n"
    )

    migrations = proj / "app" / "migrations"
    migrations.mkdir()
    (migrations / "__init__.py").write_text("")
    (migrations / "0001_initial.py").write_text(
        "from django.db import migrations, models\n\n\n"
        "class Migration(migrations.Migration):\n\n"
        "    dependencies = []\n\n"
        "    operations = [\n"
        "        migrations.RemoveField(model_name='thing', name='legacy_field'),\n"
        "    ]\n"
    )
    return proj


def make_clean_project(tmp_path):
    proj = tmp_path / "clean"
    (proj / "config").mkdir(parents=True)
    (proj / "config" / "__init__.py").write_text("")
    (proj / "config" / "settings.py").write_text(
        "ROOT_URLCONF = \"config.urls\"\nINSTALLED_APPS = []\n"
    )
    (proj / "config" / "urls.py").write_text("urlpatterns = []\n")
    return proj


# ---------------------------------------------------------------------------
# verify_project — every linter contributes
# ---------------------------------------------------------------------------


def test_every_linter_contributes_a_finding(tmp_path):
    proj = make_dirty_project(tmp_path)
    reports = verify_project(proj)

    by_name = {r.name: r for r in reports}
    assert set(by_name) == {
        "stapel-lint",
        "stapel-adoption-lint",
        "stapel-url-lint",
        "stapel-config-lint",
        "stapel-migration-lint",
        "stapel-swap-lint",
        "stapel-doc-lint",
    }

    assert by_name["stapel-lint"].errors >= 1
    rules = {f["rule"] for f in by_name["stapel-lint"].findings}
    assert "R006" in rules

    ado_rules = {f["rule"] for f in by_name["stapel-adoption-lint"].findings}
    assert {"ADO001", "ADO002", "ADO004"} <= ado_rules
    assert by_name["stapel-adoption-lint"].errors == 2   # ADO001 + ADO002
    assert by_name["stapel-adoption-lint"].warnings == 1  # ADO004

    url_rules = {f["rule"] for f in by_name["stapel-url-lint"].findings}
    assert "URL001" in url_rules

    cfg_rules = {f["rule"] for f in by_name["stapel-config-lint"].findings}
    assert "CFG001" in cfg_rules

    mig_rules = {f["rule"] for f in by_name["stapel-migration-lint"].findings}
    assert "MIG001" in mig_rules

    # DOC001 (warning): app/models.py's Thing.avatar has no help_text and no
    # preceding comment.
    doc_rules = {f["rule"] for f in by_name["stapel-doc-lint"].findings}
    assert "DOC001" in doc_rules
    assert by_name["stapel-doc-lint"].errors == 0
    assert by_name["stapel-doc-lint"].warnings >= 1

    # no get_model/get_presenter or dto.py in this fixture -> swap-lint is clean
    assert by_name["stapel-swap-lint"].errors == 0
    assert by_name["stapel-swap-lint"].warnings == 0

    total_errors = sum(r.errors for r in reports)
    assert total_errors == 6  # R006, ADO001, ADO002, URL001, CFG001, MIG001


def test_clean_project_reports_all_zero(tmp_path):
    proj = make_clean_project(tmp_path)
    reports = verify_project(proj)
    for r in reports:
        assert isinstance(r, LinterReport)
        assert r.errors == 0
        assert r.findings == []


# ---------------------------------------------------------------------------
# CLI — exit codes and --json
# ---------------------------------------------------------------------------


def test_cli_exit_code_1_on_dirty_project(tmp_path, capsys):
    proj = make_dirty_project(tmp_path)
    code = main([str(proj)])
    out = capsys.readouterr().out
    assert code == 1
    assert "stapel-lint" in out
    assert "stapel-migration-lint" in out
    assert "errors" in out


def test_cli_exit_code_0_on_clean_project(tmp_path, capsys):
    proj = make_clean_project(tmp_path)
    code = main([str(proj)])
    out = capsys.readouterr().out
    assert code == 0
    assert "All clean across 7 linters." in out


def test_cli_json_shape_and_exit_code(tmp_path, capsys):
    proj = make_dirty_project(tmp_path)
    code = main([str(proj), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["errors"] == 6
    assert len(payload["linters"]) == 7
    names = {entry["name"] for entry in payload["linters"]}
    assert names == {
        "stapel-lint",
        "stapel-adoption-lint",
        "stapel-url-lint",
        "stapel-config-lint",
        "stapel-migration-lint",
        "stapel-swap-lint",
        "stapel-doc-lint",
    }
    for entry in payload["linters"]:
        assert "errors" in entry
        assert "warnings" in entry
        assert "findings" in entry


def test_cli_json_clean_ok_true(tmp_path, capsys):
    proj = make_clean_project(tmp_path)
    code = main([str(proj), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["errors"] == 0


def test_cli_errors_on_missing_directory(tmp_path, capsys):
    missing = tmp_path / "does-not-exist"
    code = main([str(missing)])
    err = capsys.readouterr().err
    assert code == 2
    assert "not a directory" in err


def test_cli_forwards_workspace_flag_to_adoption_lint(tmp_path, capsys):
    # Project lives alone (no sibling stapel-verifyfixture repo in its parent, so the
    # default search root finds nothing); the module repo sits in a separate
    # "elsewhere" directory only discoverable via an explicit --workspace.
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    proj = lonely / "proj"
    (proj / "config").mkdir(parents=True)
    (proj / "config" / "__init__.py").write_text("")
    (proj / "config" / "settings.py").write_text(
        "ROOT_URLCONF = \"config.urls\"\nINSTALLED_APPS = [\"stapel_verifyfixture\"]\n"
    )
    (proj / "config" / "urls.py").write_text("urlpatterns = []\n")

    elsewhere = tmp_path / "elsewhere"
    module = elsewhere / "stapel-verifyfixture"
    (module / "docs").mkdir(parents=True)
    (module / "urls.py").write_text("urlpatterns = []\n")
    (module / "docs" / "schema.json").write_text(json.dumps({"paths": {}}))

    # without --workspace, the module isn't discoverable -> no ADO001
    code_without = main([str(proj)])
    out_without = capsys.readouterr().out
    assert code_without == 0
    assert "ADO001" not in out_without

    # with --workspace pointing at "elsewhere", adoption-lint finds the
    # module's urls.py and flags it as installed-but-unmounted
    code_with = main([str(proj), "--workspace", str(elsewhere)])
    out_with = capsys.readouterr().out
    assert code_with == 1
    assert "ADO001" in out_with
