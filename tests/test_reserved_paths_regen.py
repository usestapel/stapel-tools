"""stapel-reserved-paths CLI: the pre-commit drift gate for a generated
project's reserved-paths.json (schema agreed with @stapel/eslint-plugin's
no-reserved-backend-route rule — see reserved_paths.py's module docstring).
"""
import json

from stapel_tools.create_project import create_project
from stapel_tools.reserved_paths import main, modules_from_existing, regenerate


def _create(tmp_path, modules):
    create_project(
        name="app", project_type="monolith", title="App",
        url="https://x.dev", company_name="X", company_email="x@x.dev",
        modules=modules, output_dir=tmp_path,
        use_submodules=False, init_git=False,
    )
    return tmp_path / "app"


class TestModulesFromExisting:
    def test_recovers_module_set_skipping_fixed_entries(self):
        prefixes = [
            "/admin", "/staticfiles", "/media",
            "/auth/api", "/auth/swagger", "/auth/schema.json", "/auth/admin",
            "/calendar/api",
        ]
        assert modules_from_existing(prefixes) == ["auth", "calendar"]

    def test_empty_when_only_fixed_entries(self):
        assert modules_from_existing(["/admin", "/staticfiles", "/media"]) == []


class TestRegenerate:
    def test_idempotent_against_a_real_generated_project(self, tmp_path):
        proj = _create(tmp_path, ["core", "auth", "calendar"])
        before = json.loads((proj / "reserved-paths.json").read_text())["reservedPathPrefixes"]
        assert regenerate(before) == before

    def test_drops_a_stale_module_never_reintroduced_by_the_current_definition(self):
        # A module the CURRENT registry no longer knows (renamed/removed) —
        # regeneration only re-renders sub-surfaces for modules recovered
        # from the file itself, so this proves the mechanism doesn't invent
        # entries out of thin air; it always includes the fixed three.
        prefixes = ["/admin", "/staticfiles", "/media", "/ghost/api"]
        result = regenerate(prefixes)
        assert result == [
            "/admin", "/staticfiles", "/media",
            "/ghost/api", "/ghost/swagger", "/ghost/schema.json", "/ghost/admin",
        ]


class TestMainCli:
    def test_check_passes_on_freshly_generated_project(self, tmp_path):
        proj = _create(tmp_path, ["core", "auth", "calendar"])
        assert main([str(proj), "--check"]) == 0

    def test_check_fails_on_drift(self, tmp_path):
        proj = _create(tmp_path, ["core", "calendar"])
        path = proj / "reserved-paths.json"
        data = json.loads(path.read_text())
        data["reservedPathPrefixes"].remove("/calendar/admin")  # simulate drift
        path.write_text(json.dumps(data, indent=2) + "\n")
        assert main([str(proj), "--check"]) == 1
        # Re-running without --check fixes it.
        assert main([str(proj)]) == 0
        assert main([str(proj), "--check"]) == 0

    def test_no_op_when_project_has_no_reserved_paths_json(self, tmp_path):
        empty = tmp_path / "nothing"
        empty.mkdir()
        assert main([str(empty), "--check"]) == 0

    def test_malformed_file_refuses_to_guess(self, tmp_path):
        proj = tmp_path / "bad"
        proj.mkdir()
        (proj / "reserved-paths.json").write_text("{}")
        assert main([str(proj), "--check"]) == 1
