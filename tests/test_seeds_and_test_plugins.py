"""The generated service declares its seeds and registers the bricks' test helpers.

* ``config/settings/base.py`` carries ``STAPEL_SEEDS: list[str] = []``, the
  list ``manage.py run_seeds`` (stapel-core 0.95.0) reads; ``bootstrap.sh``
  runs it (``test_boot_contract.py`` holds the step and its lint);
* the service ``conftest.py`` lists ``pytest_plugins`` for every selected
  module that ships a ``testing`` plugin (stapel-auth 0.50.0), and nothing
  when none is selected — explicit registration, no ``pytest11`` entry point.
"""
import ast

import pytest

from stapel_tools import _wiring
from stapel_tools.create_project import STAPEL_LIBS, create_project


def _create(out_dir, modules):
    create_project(
        name="app", project_type="monolith", title="App",
        url="https://x.dev", company_name="X", company_email="x@x.dev",
        modules=modules, output_dir=out_dir,
        use_submodules=False, init_git=False,
    )
    return out_dir / "app" / "svc-app"


@pytest.fixture(scope="module")
def with_auth(tmp_path_factory):
    return _create(tmp_path_factory.mktemp("auth"), ["core", "auth"])


@pytest.fixture(scope="module")
def core_only(tmp_path_factory):
    return _create(tmp_path_factory.mktemp("core"), ["core"])


def _module_value(path, name):
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == name:
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not assigned at module level in {path}")


def test_base_settings_declare_an_empty_seed_list(core_only):
    assert _module_value(core_only / "config" / "settings" / "base.py", "STAPEL_SEEDS") == []


def test_bootstrap_runs_the_declared_seeds(core_only):
    boot = (core_only / "bootstrap.sh").read_text()
    assert 'optional "seeds" python manage.py run_seeds' in boot
    assert boot.index("run_seeds") < boot.index("\nbootstrap_done")


def test_auth_project_registers_the_auth_test_plugin(with_auth):
    conftest = with_auth / "conftest.py"
    assert _module_value(conftest, "pytest_plugins") == ["stapel_auth.testing"]


def test_no_plugin_line_without_a_brick_that_ships_one(core_only):
    assert "pytest_plugins" not in (core_only / "conftest.py").read_text()


def test_plugin_line_comes_after_every_import(with_auth):
    tree = ast.parse((with_auth / "conftest.py").read_text())
    names = [type(n).__name__ for n in tree.body]
    last_import = max(i for i, n in enumerate(names) if n in ("Import", "ImportFrom"))
    plugins_at = next(
        i for i, n in enumerate(tree.body)
        if isinstance(n, ast.Assign) and n.targets[0].id == "pytest_plugins"
    )
    assert plugins_at > last_import


def test_registry_drives_the_plugin_list():
    assert STAPEL_LIBS["auth"]["pytest_plugin"] == "stapel_auth.testing"
    assert _wiring.testing_plugins(["core", "auth"]) == ["stapel_auth.testing"]
    assert _wiring.render_pytest_plugins(["core"]) == ""


def _v(text):
    return tuple(int(p) for p in text.split("."))


@pytest.mark.parametrize("key,floor", [("core", "0.95.0"), ("auth", "0.50.0")])
def test_pins_carry_what_the_scaffold_emits(key, floor):
    """bootstrap.sh calls run_seeds (core 0.95.0); the conftest lists
    stapel_auth.testing (auth 0.50.0). A lower pin emits a dead step."""
    assert _v(STAPEL_LIBS[key]["pin"]) >= _v(floor)
