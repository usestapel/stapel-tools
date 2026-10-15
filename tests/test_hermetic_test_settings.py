"""The generated `config/settings/test.py` stands on its own (T14): sqlite in
memory, in-process comm, file mailtrap, and an opt-in `pg` marker that skips
by default. It used to be `from .local import *`, which inherited the compose
PostgreSQL credentials and failed every sandboxed run of the project's own
suite."""
from stapel_tools import _templates as T
from stapel_tools.create_project import create_project


def _svc(tmp_path):
    create_project(
        name="app", project_type="monolith", title="App",
        url="https://app.example", company_name="Acme",
        company_email="hi@app.example", modules=["core", "auth", "notifications"],
        output_dir=tmp_path, use_submodules=False, init_git=False,
    )
    return tmp_path / "app" / "svc-app"


def test_test_settings_no_longer_layer_over_local():
    assert "from .local import" not in T.TEST_SETTINGS
    assert "from .dev import" not in T.TEST_SETTINGS
    assert "from .base import *" in T.TEST_SETTINGS


def test_generated_test_settings_are_hermetic(tmp_path):
    svc = _svc(tmp_path)
    text = (svc / "config" / "settings" / "test.py").read_text()
    assert "from .local import" not in text
    assert '"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"' in text
    assert '"ACTION_TRANSPORT": "inprocess"' in text
    assert '"FUNCTION_TRANSPORT": "inprocess"' in text
    assert 'EMAIL_BACKEND = "tests.harness.mailtrap.FileMailtrapBackend"' in text
    assert "strict_template_variables(TEMPLATES)" in text
    assert "{{" not in text
    # The posture's prototype stage (mock OTP) applies under test too.
    assert 'public_space(stage="prototype")' in text
    compile(text, "test.py", "exec")


def test_pg_marker_is_registered_and_skipped_by_default(tmp_path):
    svc = _svc(tmp_path)
    assert "pg: needs PostgreSQL" in (svc / "pytest.ini").read_text()
    conftest = (svc / "conftest.py").read_text()
    assert "def pytest_collection_modifyitems" in conftest
    assert "STAPEL_TEST_DB" in conftest
    compile(conftest, "conftest.py", "exec")
    pg_test = (svc / "tests" / "test_pg_profile.py").read_text()
    assert "@pytest.mark.pg" in pg_test
