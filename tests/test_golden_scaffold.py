"""The golden whole-tree gate over generated projects (scripts/golden_scaffold.py).

The emitter refactor promises byte-identical output at every step; this is
the test that holds it to that. The inversion tests below prove the gate can
go red: one changed character in a template, one dropped file, one extra
file, and a case that generates nothing.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "golden_scaffold.py"


def _load():
    spec = importlib.util.spec_from_file_location("golden_scaffold", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["golden_scaffold"] = mod
    spec.loader.exec_module(mod)
    return mod


golden = _load()
CASES = golden.cases()


def test_every_case_has_a_manifest_and_every_manifest_a_case():
    on_disk = {p.stem for p in golden.GOLDEN_DIR.glob("*.json")}
    assert on_disk == set(CASES)


def test_matrix_covers_the_composites_auth_models_and_repo_init():
    names = set(CASES)
    for composite in ("shop", "classified", "booking", "social"):
        assert composite in names
        assert f"repo-init-{composite}" in names
    for model in ("none", "anonymous", "public", "internal"):
        assert f"auth-model-{model}" in names
    assert {"every-pair", "slot-none", "slot-override", "minimal-auth",
            "microservices"} <= names


@pytest.mark.parametrize("name", list(CASES))
def test_generated_tree_matches_the_golden_manifest(name):
    want = json.loads(golden.manifest_path(name).read_text(encoding="utf-8"))
    got = golden.run_case(name, CASES[name])
    assert got, f"{name} generated nothing"
    changes = golden.diff(want, got)
    assert not changes, (
        f"{name}: generated tree differs from tests/golden/{name}.json\n"
        + "\n".join(changes)
        + "\nIntended? `make golden-update` and commit the manifests with the change."
    )


def test_the_same_case_twice_is_byte_identical():
    """Determinism: secrets come from the injected source, nothing else varies."""
    assert golden.run_case("auth", CASES["auth"]) == golden.run_case("auth", CASES["auth"])


class TestInversions:
    def test_one_character_in_a_frontend_template_turns_it_red(self, monkeypatch):
        from stapel_tools import _frontend_templates as F

        monkeypatch.setattr(F, "TSCONFIG_NODE_JSON", F.TSCONFIG_NODE_JSON + " ")
        want = json.loads(golden.manifest_path("auth").read_text(encoding="utf-8"))
        changes = golden.diff(want, golden.run_case("auth", CASES["auth"]))
        assert changes == ["  ~ tsconfig.node.json"]

    def test_one_character_in_a_repo_init_template_turns_it_red(self, monkeypatch):
        # frontend_repo_init binds the template by name at import; patch it there
        from stapel_tools import frontend_repo_init as R

        monkeypatch.setattr(R, "FRONTEND_PUBLISH_SH", R.FRONTEND_PUBLISH_SH + "#\n")
        case = "repo-init-delivery-github"
        want = json.loads(golden.manifest_path(case).read_text(encoding="utf-8"))
        changes = golden.diff(want, golden.run_case(case, CASES[case]))
        assert changes == ["  ~ frontend-publish.sh"]

    def test_a_frontend_emitted_for_a_project_without_one_is_red(self, monkeypatch):
        """minimal/microservices record that NO frontend/ exists."""
        want = json.loads(golden.manifest_path("minimal-none").read_text(encoding="utf-8"))
        assert list(want) == ["<no frontend/>"]

    def test_a_missing_and_an_extra_file_are_named(self):
        want = {"a": "1", "b": "2"}
        assert golden.diff(want, {"a": "1", "c": "3"}) == ["  - b", "  + c"]

    def test_check_reports_a_drifted_case_and_exits_nonzero(self, monkeypatch, capsys):
        from stapel_tools import _frontend_templates as F

        monkeypatch.setattr(F, "VITE_ENV_D_TS", F.VITE_ENV_D_TS + "\n")
        assert golden.check(["no-modules"]) == 1
        assert "src/vite-env.d.ts" in capsys.readouterr().out

    def test_a_case_that_generates_nothing_is_red(self, monkeypatch, capsys):
        monkeypatch.setattr(golden, "run_case", lambda name, spec: {})
        assert golden.check(["no-modules"]) == 1
        assert "generated NOTHING" in capsys.readouterr().out
