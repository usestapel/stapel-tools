"""EXP001/EXP002 — a private client name must not reach a public tree.

Pinned after the 2026-08-22 sweep: ten public repos carried a client's name
in changelogs, docstrings, tests and one published schema.json, plus sixteen
commit messages that cannot be scrubbed without rewriting history. The
previous scrub (a legacy product name, 2026-07-09) had already rewritten
history once. A gate, not a reminder.
"""
import json
import subprocess
from pathlib import Path

from stapel_tools.exposure_lint import (
    LIST_ENV,
    is_public_project,
    lint_project,
    load_private_names,
    main,
)


def _names_file(tmp_path, *names) -> Path:
    p = tmp_path / "private-names"
    p.write_text("# clients\n" + "\n".join(names) + "\n", encoding="utf-8")
    return p


def _lib(tmp_path, name="stapel-thing", body="x = 1\n") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "thing.py").write_text(body, encoding="utf-8")
    return root


class TestTheList:
    def test_missing_list_is_a_note_not_a_pass(self, tmp_path, monkeypatch):
        monkeypatch.setenv(LIST_ENV, str(tmp_path / "absent"))
        notes = []
        assert lint_project(_lib(tmp_path, body="acme\n"), notes=notes) == []
        assert any("no private-names list" in n for n in notes)

    def test_list_is_lowercased_commented_deduped(self, tmp_path):
        p = _names_file(tmp_path, "Acme", "acme  # again", "acme.example")
        assert load_private_names(p) == ["acme", "acme.example"]


class TestScope:
    def test_stapel_distribution_is_public(self, tmp_path):
        assert is_public_project(_lib(tmp_path))

    def test_other_distribution_is_private(self, tmp_path):
        assert not is_public_project(_lib(tmp_path, name="acme-fleet"))

    def test_public_npm_package_and_monorepo(self, tmp_path):
        root = tmp_path / "mono"
        (root / "packages" / "x").mkdir(parents=True)
        (root / "packages" / "x" / "package.json").write_text(
            json.dumps({"name": "@stapel/x-react"}), encoding="utf-8"
        )
        assert is_public_project(root)
        (root / "packages" / "x" / "package.json").write_text(
            json.dumps({"name": "@stapel/x-react", "private": True}), encoding="utf-8"
        )
        assert not is_public_project(root)

    def test_private_project_is_not_applicable(self, tmp_path, monkeypatch):
        monkeypatch.setenv(LIST_ENV, str(_names_file(tmp_path, "acme")))
        notes = []
        root = _lib(tmp_path, name="acme-fleet", body="# acme lives here\n")
        assert lint_project(root, notes=notes) == []
        assert any("not applicable" in n for n in notes)


class TestExp001:
    def test_hit_in_tree_is_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv(LIST_ENV, str(_names_file(tmp_path, "acme")))
        root = _lib(tmp_path, body="# found on the Acme.example NATS deploy\n")
        found = lint_project(root)
        assert [f.rule for f in found] == ["EXP001"]
        assert found[0].path.endswith("thing.py") and found[0].line == 1

    def test_excepted_token_is_not_a_hit(self, tmp_path, monkeypatch):
        monkeypatch.setenv(LIST_ENV, str(_names_file(tmp_path, "acme", "!acme-widget")))
        root = _lib(tmp_path, body='OPTION = "acme-widget"  # a public dataset code\n')
        assert lint_project(root) == []

    def test_exception_covers_only_its_own_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv(LIST_ENV, str(_names_file(tmp_path, "acme", "!acme-widget")))
        root = _lib(tmp_path, body='X = "acme-widget"\nY = "acme"\nZ = "acme-widgets"\n')
        found = lint_project(root)
        assert [f.line for f in found] == [2, 3]

    def test_generated_schema_is_not_skipped(self, tmp_path, monkeypatch):
        """docs/schema.json ships in the wheel — the hit that matters most."""
        monkeypatch.setenv(LIST_ENV, str(_names_file(tmp_path, "acme")))
        root = _lib(tmp_path)
        (root / "docs").mkdir()
        (root / "docs" / "schema.json").write_text(
            '{"description": "per acme-storefront-design.md"}', encoding="utf-8"
        )
        assert [f.rule for f in lint_project(root)] == ["EXP001"]

    def test_node_modules_and_binaries_are_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setenv(LIST_ENV, str(_names_file(tmp_path, "acme")))
        root = _lib(tmp_path)
        (root / "node_modules" / "dep").mkdir(parents=True)
        (root / "node_modules" / "dep" / "index.js").write_text("acme", encoding="utf-8")
        (root / "logo.png").write_bytes(b"acme")
        assert lint_project(root) == []


class TestExp002:
    def _git(self, root, *args):
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout

    def test_unpushed_commit_message_is_caught_only_with_commits(self, tmp_path, monkeypatch):
        monkeypatch.setenv(LIST_ENV, str(_names_file(tmp_path, "acme")))
        root = _lib(tmp_path)
        self._git(root, "init", "-q")
        self._git(root, "-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-q", "--allow-empty", "-m", "fix: found on the acme fleet")
        assert lint_project(root) == []
        found = lint_project(root, commits=True)
        assert [f.rule for f in found] == ["EXP002"]
        assert found[0].path.startswith("commit ")

    def test_cli_exit_codes(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv(LIST_ENV, str(_names_file(tmp_path, "acme")))
        root = _lib(tmp_path, body="acme\n")
        assert main([str(root)]) == 1
        assert main([str(_lib(tmp_path, name="stapel-clean"))]) == 0
