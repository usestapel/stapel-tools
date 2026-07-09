"""migration-lint tests — every §3.2 rule, floor computation, base-sha check."""
import json
import subprocess

import pytest

from stapel_tools.migration_lint import (
    FLOOR_ZERO,
    app_report,
    compute_reversible_floor,
    discover_apps,
    lint_paths,
    main,
    resolve_app_label,
    scan_migration_file,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

HEADER = "from django.db import migrations, models\n\n\nclass Migration(migrations.Migration):\n"


def make_migration(app_dir, name, operations, marker=None, extra_body=""):
    mig_dir = app_dir / "migrations"
    mig_dir.mkdir(parents=True, exist_ok=True)
    (mig_dir / "__init__.py").write_text("")
    lines = []
    if marker:
        lines.append(f"# stapel: {marker}")
    lines.append(HEADER)
    lines.append("    dependencies = []\n")
    if extra_body:
        lines.append(extra_body)
    lines.append("    operations = [")
    for op in operations:
        lines.append(f"        {op},")
    lines.append("    ]")
    (mig_dir / f"{name}.py").write_text("\n".join(lines) + "\n")


def make_app(tmp_path, label="shop"):
    app_dir = tmp_path / label
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


CREATE_ORDER = (
    "migrations.CreateModel(name='Order', fields=["
    "('id', models.BigAutoField(primary_key=True)), "
    "('status', models.CharField(max_length=32, null=True)), "
    "('note', models.CharField(max_length=100))])"
)


def run_lint(tmp_path, base_sha=None):
    violations, apps = lint_paths([str(tmp_path)], base_sha=base_sha)
    return violations, apps


def rules(violations):
    return [v.rule for v in violations]


# ---------------------------------------------------------------------------
# MIG001 — destructive ops need the contract-phase marker
# ---------------------------------------------------------------------------


class TestDestructiveMarker:
    @pytest.mark.parametrize("op", [
        "migrations.RemoveField(model_name='order', name='note')",
        "migrations.DeleteModel(name='Order')",
        "migrations.RenameField(model_name='order', old_name='note', new_name='memo')",
        "migrations.RenameModel(old_name='Order', new_name='Purchase')",
    ])
    def test_destructive_without_marker_errors(self, tmp_path, op):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER])
        make_migration(app, "0002_contract", [op])
        violations, _ = run_lint(tmp_path)
        assert rules(violations) == ["MIG001"]
        assert violations[0].level == "error"

    def test_destructive_with_marker_passes(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER])
        make_migration(
            app, "0002_contract",
            ["migrations.RemoveField(model_name='order', name='note')"],
            marker="contract-phase",
        )
        violations, _ = run_lint(tmp_path)
        assert violations == []

    def test_alterfield_null_to_not_null_is_destructive(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER])
        make_migration(
            app, "0002_tighten",
            ["migrations.AlterField(model_name='order', name='status', "
             "field=models.CharField(max_length=32))"],
        )
        violations, _ = run_lint(tmp_path)
        assert rules(violations) == ["MIG001"]
        assert "null → NOT NULL" in violations[0].message

    def test_alterfield_max_length_shrink_is_destructive(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER])
        make_migration(
            app, "0002_shrink",
            ["migrations.AlterField(model_name='order', name='note', "
             "field=models.CharField(max_length=50))"],
        )
        violations, _ = run_lint(tmp_path)
        assert rules(violations) == ["MIG001"]
        assert "max_length 100 → 50" in violations[0].message

    def test_alterfield_widening_passes(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER])
        make_migration(
            app, "0002_widen",
            ["migrations.AlterField(model_name='order', name='note', "
             "field=models.CharField(max_length=200, null=True))"],
        )
        violations, _ = run_lint(tmp_path)
        assert violations == []

    def test_alterfield_unknown_previous_state_passes(self, tmp_path):
        # Field never seen in this app's migrations (e.g. created before the
        # lint's history horizon) — conservative: no false positive.
        app = make_app(tmp_path)
        make_migration(
            app, "0002_orphan_alter",
            ["migrations.AlterField(model_name='ghost', name='x', "
             "field=models.CharField(max_length=10))"],
        )
        violations, _ = run_lint(tmp_path)
        assert violations == []

    def test_state_tracks_addfield_then_narrow(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER])
        make_migration(
            app, "0002_add",
            ["migrations.AddField(model_name='order', name='tag', "
             "field=models.CharField(max_length=10, null=True))"],
        )
        make_migration(
            app, "0003_narrow",
            ["migrations.AlterField(model_name='order', name='tag', "
             "field=models.CharField(max_length=10))"],
        )
        violations, _ = run_lint(tmp_path)
        assert rules(violations) == ["MIG001"]
        assert violations[0].path.endswith("0003_narrow.py")


# ---------------------------------------------------------------------------
# MIG003 / floor — irreversible data ops
# ---------------------------------------------------------------------------


class TestIrreversible:
    def test_runpython_without_reverse_errors(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(
            app, "0001_backfill",
            ["migrations.RunPython(forward)"],
            extra_body="",
        )
        # inject the forward callable so the file stays syntactically valid
        violations, _ = run_lint(tmp_path)
        assert rules(violations) == ["MIG003"]

    def test_runpython_with_reverse_passes(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_backfill", [
            "migrations.RunPython(forward, migrations.RunPython.noop)",
        ])
        violations, _ = run_lint(tmp_path)
        assert violations == []

    def test_runpython_reverse_code_none_errors(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_backfill", [
            "migrations.RunPython(forward, reverse_code=None)",
        ])
        violations, _ = run_lint(tmp_path)
        assert rules(violations) == ["MIG003"]

    def test_runsql_without_reverse_errors_and_marker_clears(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_raw", ["migrations.RunSQL('DROP INDEX foo')"])
        violations, _ = run_lint(tmp_path)
        assert rules(violations) == ["MIG003"]

        app2 = make_app(tmp_path, "shop2")
        make_migration(
            app2, "0001_raw", ["migrations.RunSQL('DROP INDEX foo')"],
            marker="irreversible",
        )
        violations, apps = lint_paths([str(app2)])
        assert violations == []

    def test_floor_is_latest_irreversible(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER])
        make_migration(
            app, "0002_backfill", ["migrations.RunPython(forward)"],
            marker="irreversible",
        )
        make_migration(app, "0003_more", [
            "migrations.AddField(model_name='order', name='x', "
            "field=models.CharField(max_length=5, null=True))",
        ])
        _, apps = run_lint(tmp_path)
        (scan,) = apps
        assert compute_reversible_floor(scan) == "0002_backfill"
        assert scan.watermark == "0003_more"

    def test_floor_zero_when_fully_reversible(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER])
        _, apps = run_lint(tmp_path)
        assert compute_reversible_floor(apps[0]) == FLOOR_ZERO

    def test_stale_irreversible_marker_warns_but_lowers_floor(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER], marker="irreversible")
        violations, apps = run_lint(tmp_path)
        assert rules(violations) == ["MIG102"]
        assert violations[0].level == "warning"
        # the declaration is trusted for the floor (conservative for rollback)
        assert compute_reversible_floor(apps[0]) == "0001_initial"


# ---------------------------------------------------------------------------
# MIG004 — NOT NULL AddField on an existing model
# ---------------------------------------------------------------------------


class TestNotNullAddField:
    def test_not_null_without_default_errors(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER])
        make_migration(app, "0002_add", [
            "migrations.AddField(model_name='order', name='kind', "
            "field=models.CharField(max_length=10))",
        ])
        violations, _ = run_lint(tmp_path)
        assert rules(violations) == ["MIG004"]

    @pytest.mark.parametrize("field", [
        "models.CharField(max_length=10, null=True)",
        "models.CharField(max_length=10, default='x')",
        "models.CharField(max_length=10, db_default='x')",
        "models.ManyToManyField(to='shop.tag')",
    ])
    def test_safe_addfield_variants_pass(self, tmp_path, field):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER])
        make_migration(app, "0002_add", [
            f"migrations.AddField(model_name='order', name='kind', field={field})",
        ])
        violations, _ = run_lint(tmp_path)
        assert violations == []

    def test_default_none_is_not_a_default(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER])
        make_migration(app, "0002_add", [
            "migrations.AddField(model_name='order', name='kind', "
            "field=models.CharField(max_length=10, default=None))",
        ])
        violations, _ = run_lint(tmp_path)
        assert rules(violations) == ["MIG004"]

    def test_same_file_create_model_is_exempt(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [
            CREATE_ORDER,
            "migrations.AddField(model_name='order', name='kind', "
            "field=models.CharField(max_length=10))",
        ])
        violations, _ = run_lint(tmp_path)
        assert violations == []

    def test_noqa_suppresses(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER])
        make_migration(app, "0002_add", [
            "migrations.AddField(model_name='order', name='kind', "
            "field=models.CharField(max_length=10)),  # noqa: MIG004",
        ])
        violations, _ = run_lint(tmp_path)
        assert violations == []


# ---------------------------------------------------------------------------
# MIG101 — dynamic operations
# ---------------------------------------------------------------------------


class TestDynamicOperations:
    def test_dynamic_operations_warn(self, tmp_path):
        app = make_app(tmp_path)
        mig_dir = app / "migrations"
        mig_dir.mkdir(parents=True)
        (mig_dir / "__init__.py").write_text("")
        (mig_dir / "0001_dyn.py").write_text(
            "from django.db import migrations\n"
            "def build(): return []\n"
            "class Migration(migrations.Migration):\n"
            "    operations = build()\n"
        )
        violations, _ = run_lint(tmp_path)
        assert rules(violations) == ["MIG101"]
        assert violations[0].level == "warning"


# ---------------------------------------------------------------------------
# MIG002 — base-sha reference check (throwaway git repo)
# ---------------------------------------------------------------------------


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
             "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo)},
    )


def _sha(repo):
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


class TestBaseShaCheck:
    def _seed(self, repo, models_body):
        app = repo / "shop"
        app.mkdir(exist_ok=True)
        (app / "models.py").write_text(models_body)
        make_migration(app, "0001_initial", [CREATE_ORDER])
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "base")
        return app, _sha(repo)

    def test_reference_at_base_errors_even_with_marker(self, git_repo):
        app, base = self._seed(
            git_repo,
            "class Order:\n    def summary(self):\n        return self.note\n",
        )
        make_migration(
            app, "0002_contract",
            ["migrations.RemoveField(model_name='order', name='note')"],
            marker="contract-phase",
        )
        violations, _ = lint_paths([str(git_repo)], base_sha=base)
        assert rules(violations) == ["MIG002"]
        assert "still referenced at base" in violations[0].message
        assert "models.py:3" in violations[0].message

    def test_no_reference_at_base_passes(self, git_repo):
        app, base = self._seed(git_repo, "class Order:\n    pass\n")
        make_migration(
            app, "0002_contract",
            ["migrations.RemoveField(model_name='order', name='note')"],
            marker="contract-phase",
        )
        violations, _ = lint_paths([str(git_repo)], base_sha=base)
        assert violations == []

    def test_migration_already_at_base_is_skipped(self, git_repo):
        # A contract migration that shipped in a PREVIOUS release is not
        # re-checked against the new base.
        app = git_repo / "shop"
        app.mkdir()
        (app / "models.py").write_text("note = 'referenced word note'\n")
        make_migration(app, "0001_initial", [CREATE_ORDER])
        make_migration(
            app, "0002_contract",
            ["migrations.RemoveField(model_name='order', name='note')"],
            marker="contract-phase",
        )
        _git(git_repo, "add", ".")
        _git(git_repo, "commit", "-q", "-m", "base with contract already shipped")
        base = _sha(git_repo)
        violations, _ = lint_paths([str(git_repo)], base_sha=base)
        assert violations == []

    def test_base_sources_exclude_migrations(self, git_repo):
        # the removed field's name obviously appears in old migrations — that
        # must not count as a code reference
        app, base = self._seed(git_repo, "class Order:\n    pass\n")
        make_migration(
            app, "0002_contract",
            ["migrations.RenameField(model_name='order', old_name='note', "
             "new_name='memo')"],
            marker="contract-phase",
        )
        violations, _ = lint_paths([str(git_repo)], base_sha=base)
        assert violations == []

    def test_bad_base_sha_is_a_clear_error(self, git_repo):
        app, _ = self._seed(git_repo, "class Order:\n    pass\n")
        make_migration(
            app, "0002_contract",
            ["migrations.RemoveField(model_name='order', name='note')"],
            marker="contract-phase",
        )
        with pytest.raises(SystemExit, match="base sha"):
            lint_paths([str(git_repo)], base_sha="deadbeef" * 5)


# ---------------------------------------------------------------------------
# Discovery, labels, CLI
# ---------------------------------------------------------------------------


class TestDiscoveryAndCli:
    def test_app_label_from_apps_py(self, tmp_path):
        app = make_app(tmp_path, "stapel_auth")
        (app / "apps.py").write_text(
            "from django.apps import AppConfig\n"
            "class AuthConfig(AppConfig):\n"
            "    name = 'stapel_auth'\n"
            "    label = 'authentication'\n"
        )
        make_migration(app, "0001_initial", [CREATE_ORDER])
        (scan,) = discover_apps(tmp_path)
        assert scan.label == "authentication"

    def test_app_label_falls_back_to_dir_name(self, tmp_path):
        app = make_app(tmp_path, "orders")
        make_migration(app, "0001_initial", [CREATE_ORDER])
        assert resolve_app_label(app) == "orders"

    def test_venv_is_skipped(self, tmp_path):
        hidden = tmp_path / ".venv" / "lib" / "pkg"
        make_migration(hidden, "0001_initial", [CREATE_ORDER])
        assert discover_apps(tmp_path) == []

    def test_app_report_shape(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER])
        make_migration(
            app, "0002_backfill", ["migrations.RunPython(forward)"],
            marker="irreversible",
        )
        _, apps = run_lint(tmp_path)
        report = app_report(apps[0])
        assert report == {
            "watermark": "0002_backfill",
            "reversible_floor": "0002_backfill",
            "migrations": ["0001_initial", "0002_backfill"],
            "irreversible": ["0002_backfill"],
        }

    def test_cli_json_and_exit_codes(self, tmp_path, capsys):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER])
        make_migration(app, "0002_bad", [
            "migrations.RemoveField(model_name='order', name='note')",
        ])
        code = main([str(tmp_path), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 1
        assert payload["ok"] is False
        assert payload["errors"] == 1
        assert payload["violations"][0]["rule"] == "MIG001"
        assert payload["apps"]["shop"]["watermark"] == "0002_bad"

    def test_cli_clean_exit_zero(self, tmp_path, capsys):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER])
        assert main([str(tmp_path)]) == 0
        assert "No violations" in capsys.readouterr().out

    def test_strict_promotes_warnings(self, tmp_path, capsys):
        app = make_app(tmp_path)
        make_migration(app, "0001_initial", [CREATE_ORDER], marker="irreversible")
        assert main([str(tmp_path)]) == 0          # MIG102 is a warning
        assert main([str(tmp_path), "--strict"]) == 1

    def test_marker_detected_by_scan(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_x", [CREATE_ORDER], marker="contract-phase")
        scan = scan_migration_file(app / "migrations" / "0001_x.py")
        assert scan.markers == {"contract-phase"}

    def test_separate_database_and_state_recurses(self, tmp_path):
        app = make_app(tmp_path)
        make_migration(app, "0001_split", [
            "migrations.SeparateDatabaseAndState(database_operations=["
            "migrations.RunSQL('DROP TABLE x')], state_operations=[])",
        ])
        violations, _ = run_lint(tmp_path)
        assert rules(violations) == ["MIG003"]
