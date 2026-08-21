"""stapel-index-lint tests — the gate against "indexed silently, read by nothing".

Every rule is exercised in the shape of the incident that motivated it (the
legacy ``features_search`` / ``description_en`` / ``geohash`` fields, written
and never queried) and — just as important — in the shapes that must stay
quiet. A linter in this family has exactly one way to fail in practice:
crying wolf often enough that somebody switches it off.
"""
import json

import pytest

from stapel_tools.index_lint import lint_project, main


def _schema(**overrides):
    document = {
        "module": "stapel_search",
        "kinds": ["text", "facet", "filter", "bookkeeping"],
        "query_read_path_prefixes": ["filter:", "q"],
        "model_columns": {
            "SearchDocument": {"id": None, "title": "title", "doc_type": "doc_type"},
        },
        "fields": [
            {
                "field": "title", "kind": "text", "source": "src.title",
                "read_paths": ["q"], "test": "tests/test_index_contract.py::test_title",
                "proves": "a title word finds the document",
            },
            {
                "field": "doc_type", "kind": "filter", "source": "registry",
                "read_paths": ["filter:type"],
                "test": "tests/test_index_contract.py::test_type",
                "proves": "another type is not found",
            },
        ],
    }
    document.update(overrides)
    return document


def _project(tmp_path, *, schema=None, models="", backends=None, tests=True, dto=None,
             owns=True):
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "index.json").write_text(
        json.dumps(schema if schema is not None else _schema()), encoding="utf-8"
    )
    if owns:
        (tmp_path / "index_schema.py").write_text("INDEX_FIELDS = ()\n", encoding="utf-8")
    if models:
        (tmp_path / "models.py").write_text(models, encoding="utf-8")
    if backends:
        directory = tmp_path / "backends"
        directory.mkdir(exist_ok=True)
        for name, source in backends.items():
            (directory / name).write_text(source, encoding="utf-8")
    if tests:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test_index_contract.py").write_text(
            "def test_title():\n    pass\n\n\ndef test_type():\n    pass\n",
            encoding="utf-8",
        )
    if dto is not None:
        (tmp_path / "dto.py").write_text(dto, encoding="utf-8")
    return tmp_path


def _rules(findings):
    return sorted({f.rule for f in findings})


# ---------------------------------------------------------------------------
# silence where there is nothing to check
# ---------------------------------------------------------------------------


def test_quiet_when_the_project_declares_no_index(tmp_path):
    """A project with no search index must not be nagged about one."""
    notes = []
    assert lint_project(tmp_path, notes=notes) == []
    assert notes and "no docs/index.json" in notes[0]


def test_clean_project_is_clean(tmp_path):
    project = _project(
        tmp_path,
        models=(
            "class SearchDocument(models.Model):\n"
            "    title = models.CharField(max_length=10)\n"
            "    doc_type = models.CharField(max_length=10)\n"
        ),
        backends={
            "postgres.py": (
                'READ_PATH_IMPL = {"q": "_where", "filter:type": "_where"}\n'
                "def _where():\n    pass\n"
            )
        },
    )
    assert lint_project(project) == []


# ---------------------------------------------------------------------------
# IDX001 — indexed but undeclared
# ---------------------------------------------------------------------------


def test_idx001_a_column_the_contract_does_not_account_for(tmp_path):
    """The legacy shape: a field written into the index, read by nothing."""
    project = _project(
        tmp_path,
        models=(
            "class SearchDocument(models.Model):\n"
            "    title = models.CharField(max_length=10)\n"
            "    doc_type = models.CharField(max_length=10)\n"
            "    description_en = models.TextField()\n"
        ),
    )
    findings = lint_project(project)
    assert _rules(findings) == ["IDX001"]
    assert "description_en" in findings[0].message


def test_idx001_a_mapping_to_an_undeclared_field(tmp_path):
    schema = _schema()
    schema["model_columns"]["SearchDocument"]["ghost"] = "not_declared"
    project = _project(
        tmp_path,
        schema=schema,
        models=(
            "class SearchDocument(models.Model):\n"
            "    title = models.CharField(max_length=10)\n"
            "    doc_type = models.CharField(max_length=10)\n"
            "    ghost = models.TextField()\n"
        ),
    )
    findings = [f for f in lint_project(project) if f.rule == "IDX001"]
    assert findings and "not_declared" in findings[0].message


def test_idx001_quiet_for_a_model_the_contract_does_not_claim(tmp_path):
    """A module's other models are not index models."""
    project = _project(
        tmp_path,
        models=(
            "class SearchDocument(models.Model):\n"
            "    title = models.CharField(max_length=10)\n"
            "    doc_type = models.CharField(max_length=10)\n"
            "\n\nclass AuditRow(models.Model):\n"
            "    whatever = models.TextField()\n"
        ),
    )
    assert [f for f in lint_project(project) if f.rule == "IDX001"] == []


def test_idx001_is_silenced_by_a_named_waiver(tmp_path):
    project = _project(
        tmp_path,
        models=(
            "class SearchDocument(models.Model):\n"
            "    title = models.CharField(max_length=10)\n"
            "    doc_type = models.CharField(max_length=10)\n"
            "    # stapel: index-waived scratch — populated by a migration backfill\n"
            "    scratch = models.TextField()\n"
        ),
    )
    assert [f for f in lint_project(project) if f.rule == "IDX001"] == []


# ---------------------------------------------------------------------------
# IDX002 — declared but unreachable
# ---------------------------------------------------------------------------


def test_idx002_a_backend_that_does_not_answer_a_read_path(tmp_path):
    project = _project(
        tmp_path,
        backends={"postgres.py": 'READ_PATH_IMPL = {"q": "_where"}\ndef _where():\n    pass\n'},
    )
    findings = [f for f in lint_project(project) if f.rule == "IDX002"]
    assert findings and "filter:type" in findings[0].message


def test_idx002_a_named_implementation_that_does_not_exist(tmp_path):
    project = _project(
        tmp_path,
        backends={
            "postgres.py": (
                'READ_PATH_IMPL = {"q": "_where", "filter:type": "_gone"}\n'
                "def _where():\n    pass\n"
            )
        },
    )
    findings = [f for f in lint_project(project) if f.rule == "IDX002"]
    assert findings and "_gone" in findings[0].message


def test_idx002_accepts_a_declared_native_capability(tmp_path):
    """Engines legitimately differ — declared, so the difference is reviewable."""
    project = _project(
        tmp_path,
        backends={
            "meili.py": (
                'READ_PATH_IMPL = {"q": "_search", "filter:type": "capability:native"}\n'
                "def _search():\n    pass\n"
            )
        },
    )
    assert [f for f in lint_project(project) if f.rule == "IDX002"] == []


def test_idx002_skips_a_declared_stub(tmp_path):
    """A rule that forces a stub to grow a fake implementation manufactures
    the very defect it audits."""
    project = _project(
        tmp_path,
        backends={"opensearch.py": "IS_STUB = True\nREAD_PATH_IMPL = {}\n"},
    )
    assert [f for f in lint_project(project) if f.rule == "IDX002"] == []


def test_idx002_a_backend_with_no_declaration_at_all(tmp_path):
    project = _project(tmp_path, backends={"custom.py": "class Backend:\n    pass\n"})
    findings = [f for f in lint_project(project) if f.rule == "IDX002"]
    assert findings and "READ_PATH_IMPL" in findings[0].message


# ---------------------------------------------------------------------------
# IDX003 — the declared test does not resolve
# ---------------------------------------------------------------------------


def test_idx003_a_missing_test_file(tmp_path):
    project = _project(tmp_path, tests=False)
    findings = [f for f in lint_project(project) if f.rule == "IDX003"]
    assert len(findings) == 2


def test_idx003_a_missing_test_function(tmp_path):
    project = _project(tmp_path)
    (tmp_path / "tests" / "test_index_contract.py").write_text(
        "def test_title():\n    pass\n", encoding="utf-8"
    )
    findings = [f for f in lint_project(project) if f.rule == "IDX003"]
    assert len(findings) == 1
    assert "test_type" in findings[0].message


def test_idx003_accepts_a_parametrized_node_id(tmp_path):
    schema = _schema()
    schema["fields"][0]["test"] = "tests/test_index_contract.py::test_title[postgres]"
    project = _project(tmp_path, schema=schema)
    assert [f for f in lint_project(project) if f.rule == "IDX003"] == []


# ---------------------------------------------------------------------------
# IDX004 — dead pull
# ---------------------------------------------------------------------------


def test_idx004_a_pulled_field_that_lands_nowhere(tmp_path):
    project = _project(
        tmp_path,
        dto=(
            "class SearchDocumentInput:\n"
            "    doc_type: str\n"
            "    title: str\n"
            "    mystery: str = ''\n"
        ),
    )
    findings = [f for f in lint_project(project) if f.rule == "IDX004"]
    assert len(findings) == 1
    assert findings[0].level == "warning"
    assert "mystery" in findings[0].message


def test_idx004_is_silenced_by_a_named_waiver(tmp_path):
    project = _project(
        tmp_path,
        dto=(
            "class SearchDocumentInput:\n"
            "    doc_type: str\n"
            "    title: str\n"
            "    # stapel: index-waived mystery — rides to the card, never a predicate\n"
            "    mystery: str = ''\n"
        ),
    )
    assert [f for f in lint_project(project) if f.rule == "IDX004"] == []


# ---------------------------------------------------------------------------
# IDX005 — kind vocabulary
# ---------------------------------------------------------------------------


def test_idx005_a_kind_outside_the_closed_vocabulary(tmp_path):
    schema = _schema()
    schema["fields"][0]["kind"] = "improvised"
    project = _project(tmp_path, schema=schema)
    findings = [f for f in lint_project(project) if f.rule == "IDX005"]
    assert findings and findings[0].level == "warning"


# ---------------------------------------------------------------------------
# level follows the reader's power to act
# ---------------------------------------------------------------------------


def test_a_consumer_project_gets_warnings_not_errors(tmp_path):
    """A project that merely installed somebody else's backend cannot fix
    its contract, and an error a reader cannot clear teaches them to ignore
    the tool (``adoption_checks.py:53-66``)."""
    project = _project(
        tmp_path,
        owns=False,
        models=(
            "class SearchDocument(models.Model):\n"
            "    title = models.CharField(max_length=10)\n"
            "    doc_type = models.CharField(max_length=10)\n"
            "    description_en = models.TextField()\n"
        ),
        backends={"custom.py": "class Backend:\n    pass\n"},
    )
    findings = lint_project(project)
    assert findings
    assert all(f.level == "warning" for f in findings)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_exit_codes_and_json(tmp_path, capsys):
    project = _project(
        tmp_path,
        models=(
            "class SearchDocument(models.Model):\n"
            "    title = models.CharField(max_length=10)\n"
            "    doc_type = models.CharField(max_length=10)\n"
        ),
        backends={
            "postgres.py": (
                'READ_PATH_IMPL = {"q": "_where", "filter:type": "_where"}\n'
                "def _where():\n    pass\n"
            )
        },
    )
    assert main([str(project), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["errors"] == 0


def test_cli_strict_fails_on_warnings(tmp_path):
    project = _project(
        tmp_path,
        dto=("class SearchDocumentInput:\n    doc_type: str\n    mystery: str = ''\n"),
    )
    assert main([str(project)]) == 0
    assert main([str(project), "--strict"]) == 1


def test_cli_rejects_a_non_directory(tmp_path):
    assert main([str(tmp_path / "nope")]) == 2


def test_it_is_composed_into_verify():
    """The point of writing it here: pre-commit picks it up on upgrade."""
    from stapel_tools import verify

    assert hasattr(verify, "run_index_lint")
    source = verify.verify_project.__code__.co_names
    assert "run_index_lint" in source


@pytest.mark.parametrize("bad", ["{not json", ""])
def test_a_broken_contract_file_is_a_loud_failure(tmp_path, bad):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.json").write_text(bad, encoding="utf-8")
    with pytest.raises(SystemExit):
        lint_project(tmp_path)
