"""Tests for the ``surface`` section — the usage surface of capabilities.json.

Hermetic: a throwaway repo on tmp_path (pyproject + a few source files + a
curated ``docs/capabilities.meta.json``) exercises every selector, the closed
kind vocabulary, both directions of drift and — the point of the whole
section — the LOUD rule: an export a root selects and nobody explained fails
emission naming the symbol.
"""
import json

import pytest

from stapel_tools.surface import (
    FUNCTION_KINDS,
    KINDS,
    SELECTORS,
    build_static_capabilities,
    build_surface,
    main,
    patch_capabilities,
    scan_capability_fields,
    scan_functions,
    scan_permission_classes,
    scan_templates,
)

PERMISSIONS_PY = '''
from rest_framework import permissions

SOME_CONSTANT = "x"


class IsNotAnonymousUser(permissions.BasePermission):
    """doc"""


class Helper:
    pass
'''

FACTORS_PY = '''
def register_factor(f):
    pass


def strong_factors(user):
    return []


def load_configured_factors():
    pass


def _private_helper():
    pass
'''

DTO_PY = '''
from dataclasses import dataclass


@dataclass
class RegistrationCapabilities:
    """doc"""

    phone: bool
    email_mock: bool = False
'''


def _repo(tmp_path, meta: dict, *, name: str = "stapel-probe"):
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "1.2.3"\n'
    )
    (tmp_path / "perms.py").write_text(PERMISSIONS_PY)
    (tmp_path / "factors.py").write_text(FACTORS_PY)
    (tmp_path / "dto.py").write_text(DTO_PY)
    (tmp_path / "templates" / "admin").mkdir(parents=True)
    (tmp_path / "templates" / "admin" / "base_site.html").write_text("nav")
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "capabilities.meta.json").write_text(json.dumps(meta))
    return tmp_path


# ---------------------------------------------------------------------------
# scanners
# ---------------------------------------------------------------------------


class TestScanners:
    def test_permission_classes_only(self, tmp_path):
        p = tmp_path / "perms.py"
        p.write_text(PERMISSIONS_PY)
        assert scan_permission_classes(p) == ["IsNotAnonymousUser"]

    def test_functions_skip_private(self, tmp_path):
        p = tmp_path / "factors.py"
        p.write_text(FACTORS_PY)
        assert scan_functions(p) == [
            "register_factor",
            "strong_factors",
            "load_configured_factors",
        ]

    def test_capability_fields_are_class_qualified(self, tmp_path):
        p = tmp_path / "dto.py"
        p.write_text(DTO_PY)
        assert scan_capability_fields(p, "RegistrationCapabilities") == [
            "RegistrationCapabilities.phone",
            "RegistrationCapabilities.email_mock",
        ]

    def test_capability_fields_unknown_class_is_loud(self, tmp_path):
        p = tmp_path / "dto.py"
        p.write_text(DTO_PY)
        with pytest.raises(SystemExit, match="defines no class"):
            scan_capability_fields(p, "Nope")

    def test_templates_are_directory_relative(self, tmp_path):
        (tmp_path / "admin").mkdir()
        (tmp_path / "admin" / "base_site.html").write_text("x")
        assert scan_templates(tmp_path) == ["admin/base_site.html"]


# ---------------------------------------------------------------------------
# the LOUD rule — the reason the section exists
# ---------------------------------------------------------------------------


class TestLoud:
    def test_export_without_intent_fails_naming_the_symbol(self, tmp_path):
        meta = {
            "surface_roots": [{"select": "permission_classes", "path": "perms.py"}],
            "surface": {},
        }
        repo = _repo(tmp_path, meta)
        with pytest.raises(SystemExit) as exc:
            build_surface(meta, repo=repo)
        assert "IsNotAnonymousUser" in str(exc.value)
        assert "without an intent" in str(exc.value)

    def test_empty_intent_is_the_same_failure(self, tmp_path):
        meta = {
            "surface_roots": [{"select": "permission_classes", "path": "perms.py"}],
            "surface": {"IsNotAnonymousUser": {"intent": ""}},
        }
        repo = _repo(tmp_path, meta)
        with pytest.raises(SystemExit, match="IsNotAnonymousUser"):
            build_surface(meta, repo=repo)

    def test_stale_intent_for_a_gone_symbol_is_loud(self, tmp_path):
        meta = {
            "surface_roots": [{"select": "permission_classes", "path": "perms.py"}],
            "surface": {
                "IsNotAnonymousUser": {"intent": "use me"},
                "IsRemovedLastYear": {"intent": "ghost"},
            },
        }
        repo = _repo(tmp_path, meta)
        with pytest.raises(SystemExit, match="IsRemovedLastYear"):
            build_surface(meta, repo=repo)

    def test_intents_without_roots_are_unreachable_prose(self, tmp_path):
        meta = {"surface": {"Whatever": {"intent": "x"}}}
        repo = _repo(tmp_path, meta)
        with pytest.raises(SystemExit, match="no 'surface_roots'"):
            build_surface(meta, repo=repo)

    def test_no_roots_no_section(self, tmp_path):
        repo = _repo(tmp_path, {})
        assert build_surface({}, repo=repo) == []


# ---------------------------------------------------------------------------
# the closed vocabularies
# ---------------------------------------------------------------------------


class TestVocabulary:
    def test_six_kinds_exactly(self):
        assert set(KINDS) == {
            "permission_class",
            "gate_function",
            "template",
            "predicate",
            "capability_field",
            "factory",
        }

    def test_function_kinds_are_a_subset(self):
        assert set(FUNCTION_KINDS) < set(KINDS)

    def test_selector_set_is_closed(self, tmp_path):
        meta = {
            "surface_roots": [{"select": "everything", "path": "perms.py"}],
            "surface": {},
        }
        repo = _repo(tmp_path, meta)
        with pytest.raises(SystemExit, match="selector set is closed"):
            build_surface(meta, repo=repo)
        assert set(SELECTORS) == {
            "permission_classes",
            "functions",
            "capability_fields",
            "templates",
        }

    def test_function_kind_outside_the_three_roles_is_loud(self, tmp_path):
        meta = {
            "surface_roots": [{"select": "functions", "path": "factors.py"}],
            "surface": {
                "register_factor": {"intent": "x", "kind": "template"},
                "strong_factors": {"intent": "x", "kind": "predicate"},
                "load_configured_factors": {"intent": "x", "kind": "factory"},
            },
        }
        repo = _repo(tmp_path, meta)
        with pytest.raises(SystemExit, match="must be one of"):
            build_surface(meta, repo=repo)

    def test_function_without_a_kind_is_loud(self, tmp_path):
        meta = {
            "surface_roots": [{"select": "functions", "path": "factors.py"}],
            "surface": {
                "register_factor": {"intent": "x"},
                "strong_factors": {"intent": "x", "kind": "predicate"},
                "load_configured_factors": {"intent": "x", "kind": "factory"},
            },
        }
        repo = _repo(tmp_path, meta)
        with pytest.raises(SystemExit, match="needs an explicit 'kind'"):
            build_surface(meta, repo=repo)

    def test_derived_kind_may_not_be_overridden(self, tmp_path):
        meta = {
            "surface_roots": [{"select": "permission_classes", "path": "perms.py"}],
            "surface": {
                "IsNotAnonymousUser": {"intent": "x", "kind": "predicate"}
            },
        }
        repo = _repo(tmp_path, meta)
        with pytest.raises(SystemExit, match="machine fact"):
            build_surface(meta, repo=repo)


# ---------------------------------------------------------------------------
# entry shape
# ---------------------------------------------------------------------------


def _full_meta() -> dict:
    return {
        "provides": "probe",
        "extension_points": [],
        "requires": [],
        "surface_roots": [
            {"select": "permission_classes", "path": "perms.py"},
            {"select": "functions", "path": "factors.py"},
            {
                "select": "capability_fields",
                "path": "dto.py",
                "class": "RegistrationCapabilities",
            },
            {"select": "templates", "path": "templates"},
        ],
        "surface": {
            "IsNotAnonymousUser": {
                "intent": "write-gate for user-facing endpoints",
                "instead_of": ["rest_framework.permissions.IsAuthenticated"],
            },
            "register_factor": {"intent": "register a factor", "kind": "factory"},
            "strong_factors": {"intent": "does the user have 2FA", "kind": "predicate"},
            "load_configured_factors": {
                "intent": "wire EXTRA_FACTORS at boot",
                "kind": "factory",
            },
            "RegistrationCapabilities.phone": {"intent": "phone OTP is on"},
            "RegistrationCapabilities.email_mock": {
                "intent": "delivery is mocked; render a dev badge",
                "consumer": "frontend",
            },
            "admin/base_site.html": {"intent": "renders cross-service nav"},
        },
    }


class TestEntries:
    def test_shape_and_derived_kinds(self, tmp_path):
        meta = _full_meta()
        repo = _repo(tmp_path, meta)
        by_name = {e["name"]: e for e in build_surface(meta, repo=repo)}

        perm = by_name["IsNotAnonymousUser"]
        assert perm["kind"] == "permission_class"
        assert perm["path"] == "stapel_probe.perms.IsNotAnonymousUser"
        assert perm["instead_of"] == ["rest_framework.permissions.IsAuthenticated"]
        assert "consumer" not in perm

        assert by_name["load_configured_factors"]["kind"] == "factory"
        assert by_name["strong_factors"]["kind"] == "predicate"

        mock = by_name["email_mock"]
        assert mock["kind"] == "capability_field"
        assert mock["path"] == (
            "stapel_probe.dto.RegistrationCapabilities.email_mock"
        )
        assert mock["consumer"] == "frontend"

        tpl = by_name["admin/base_site.html"]
        assert tpl["kind"] == "template"
        assert tpl["path"] == "templates/admin/base_site.html"

    def test_deterministic_order(self, tmp_path):
        meta = _full_meta()
        repo = _repo(tmp_path, meta)
        first = build_surface(meta, repo=repo)
        assert first == build_surface(meta, repo=repo)
        assert [e["path"] for e in first] == sorted(e["path"] for e in first)

    def test_duplicate_name_across_roots_is_loud(self, tmp_path):
        meta = _full_meta()
        meta["surface_roots"].append(
            {"select": "permission_classes", "path": "perms.py"}
        )
        repo = _repo(tmp_path, meta)
        with pytest.raises(SystemExit, match="must be unique"):
            build_surface(meta, repo=repo)

    def test_missing_root_path_is_loud(self, tmp_path):
        meta = {
            "surface_roots": [{"select": "functions", "path": "nope.py"}],
            "surface": {},
        }
        repo = _repo(tmp_path, meta)
        with pytest.raises(SystemExit, match="does not exist"):
            build_surface(meta, repo=repo)


# ---------------------------------------------------------------------------
# the standalone document (stapel-core's shape) and the patch mode
# ---------------------------------------------------------------------------


class TestStaticDocument:
    def test_no_schema_no_operations_total(self, tmp_path):
        meta = _full_meta()
        repo = _repo(tmp_path, meta)
        doc = build_static_capabilities(repo, meta)
        assert "operations_total" not in doc
        assert doc["module"] == "stapel-probe"
        assert doc["version"] == "1.2.3"
        assert doc["axes"] == []
        assert len(doc["surface"]) == 7

    def test_schema_present_counts_operations(self, tmp_path):
        meta = _full_meta()
        repo = _repo(tmp_path, meta)
        (repo / "docs" / "schema.json").write_text(
            json.dumps({"paths": {"/x/": {"get": {}, "post": {}, "parameters": []}}})
        )
        assert build_static_capabilities(repo, meta)["operations_total"] == 2

    def test_provides_is_required(self, tmp_path):
        meta = _full_meta()
        meta["provides"] = ""
        repo = _repo(tmp_path, meta)
        with pytest.raises(SystemExit, match="provides"):
            build_static_capabilities(repo, meta)

    def test_cli_emit_then_check_roundtrip(self, tmp_path, capsys):
        meta = _full_meta()
        repo = _repo(tmp_path, meta)
        assert main([str(repo)]) == 0
        assert main([str(repo), "--check"]) == 0
        doc = json.loads((repo / "docs" / "capabilities.json").read_text())
        assert [e["name"] for e in doc["surface"] if e["kind"] == "capability_field"] == [
            "email_mock",
            "phone",
        ]

    def test_cli_check_detects_drift(self, tmp_path):
        meta = _full_meta()
        repo = _repo(tmp_path, meta)
        assert main([str(repo)]) == 0
        (repo / "perms.py").write_text(
            PERMISSIONS_PY.replace("IsNotAnonymousUser", "IsNotAnonymousUser2")
        )
        # the renamed class has no intent -> loud before it can even diff
        with pytest.raises(SystemExit, match="IsNotAnonymousUser2"):
            main([str(repo), "--check"])


class TestPatchMode:
    def test_patch_inserts_surface_after_extension_points(self, tmp_path):
        meta = _full_meta()
        repo = _repo(tmp_path, meta)
        (repo / "docs" / "capabilities.json").write_text(
            json.dumps(
                {
                    "module": "stapel-probe",
                    "version": "0.0.1",
                    "provides": "hand-written",
                    "axes": [{"key": "A"}],
                    "extension_points": [{"name": "EP"}],
                    "operations_total": 5,
                    "requires": [],
                }
            )
        )
        doc = patch_capabilities(repo, meta)
        assert list(doc) == [
            "module",
            "version",
            "provides",
            "axes",
            "extension_points",
            "surface",
            "operations_total",
            "requires",
        ]
        # derivable facts refreshed from pyproject; the rest kept verbatim
        assert doc["version"] == "1.2.3"
        assert doc["provides"] == "hand-written"
        assert doc["operations_total"] == 5

    def test_patch_without_a_document_is_loud(self, tmp_path):
        meta = _full_meta()
        repo = _repo(tmp_path, meta)
        with pytest.raises(SystemExit, match="needs an existing"):
            patch_capabilities(repo, meta)
