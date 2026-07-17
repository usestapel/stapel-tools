"""`stapel-docs` — bilingual docs/api.en.md + docs/api.ru.md generation and
its pre-commit drift gate (owner directive: "документация по api/флоу — в
идеале двуязычная")."""
import shutil
from pathlib import Path

from stapel_tools.api_docs import DOCS_EN, DOCS_RU, endpoints_from_schema, main, run

FIXTURE = Path(__file__).parent / "fixtures" / "docgen"


def _plant_root_docs(tmp_path: Path) -> Path:
    """A project with the per-module `docs/` layout at its own root, plus
    its translations/ sibling (mirrors a single-lib-repo checkout)."""
    proj = tmp_path / "widgetco"
    docs_dir = proj / "docs"
    shutil.copytree(FIXTURE, docs_dir)
    shutil.move(str(docs_dir / "translations"), str(proj / "translations"))
    return proj


class TestEndpointsFromSchema:
    def test_extracts_method_path_summary_and_dto_fields(self):
        import json

        schema = json.loads((FIXTURE / "schema.json").read_text())
        eps = endpoints_from_schema(schema)
        by_path = {(e.method, e.path): e for e in eps}
        create = by_path[("POST", "/demo/api/v1/widgets/")]
        assert create.summary == "Create a widget"
        assert {f.name for f in create.request_fields} == {"name"}
        assert create.request_fields[0].required is True
        assert create.request_fields[0].description == "Widget display name."
        assert {f.name for f in create.response_fields} == {"id", "name"}

        get = by_path[("GET", "/demo/api/v1/widgets/{id}/")]
        assert get.request_fields == ()
        assert {f.name for f in get.response_fields} == {"id", "name"}


class TestRenderAndWrite:
    def test_writes_both_bilingual_docs(self, tmp_path):
        proj = _plant_root_docs(tmp_path)
        assert run(proj) == 0
        en = (proj / DOCS_EN).read_text()
        ru = (proj / DOCS_RU).read_text()
        assert "# API — widgetco (en)" in en
        assert "# API — widgetco (ru)" in ru

    def test_en_doc_has_flows_endpoints_errors_sections(self, tmp_path):
        proj = _plant_root_docs(tmp_path)
        run(proj)
        en = (proj / DOCS_EN).read_text()
        assert "### Flows" in en
        assert "#### Create a widget" in en
        assert "### Endpoints" in en
        assert "`POST /demo/api/v1/widgets/`" in en
        assert "### Errors" in en
        assert "error.400.widget_name_taken" in en
        assert "Widget name already taken." in en

    def test_ru_doc_uses_translated_strings_where_available(self, tmp_path):
        proj = _plant_root_docs(tmp_path)
        run(proj)
        ru = (proj / DOCS_RU).read_text()
        assert "Создание виджета" in ru  # translated title, no (en) marker on it
        assert "Пользователь заполняет форму виджета." in ru  # translated step 0
        assert "Такое имя виджета уже занято." in ru  # translated error

    def test_ru_doc_falls_back_to_en_with_honest_marker_when_untranslated(self, tmp_path):
        proj = _plant_root_docs(tmp_path)
        run(proj)
        ru = (proj / DOCS_RU).read_text()
        # step 1's note has no ru translation in the fixture
        assert "Create the widget. (en)" in ru
        # error #2 has no ru translation in the fixture
        assert "Widget not found. (en)" in ru
        # endpoint summaries/DTO descriptions have no ru source anywhere
        assert "Create a widget (en)" in ru

    def test_endpoint_dto_field_tables_present_in_both_langs(self, tmp_path):
        proj = _plant_root_docs(tmp_path)
        run(proj)
        en = (proj / DOCS_EN).read_text()
        ru = (proj / DOCS_RU).read_text()
        assert "`name`" in en and "`name`" in ru
        assert "Widget display name." in en
        assert "Widget display name." in ru  # (en) marked in ru, but same text


class TestCheckDriftGate:
    def test_check_passes_right_after_generation(self, tmp_path):
        proj = _plant_root_docs(tmp_path)
        run(proj)
        assert run(proj, check=True) == 0

    def test_check_fails_when_docs_missing(self, tmp_path):
        proj = _plant_root_docs(tmp_path)
        assert run(proj, check=True) == 1

    def test_check_fails_on_hand_edit_drift(self, tmp_path):
        proj = _plant_root_docs(tmp_path)
        run(proj)
        (proj / DOCS_EN).write_text("hand-edited\n")
        assert run(proj, check=True) == 1
        # regenerating without --check fixes it
        assert run(proj) == 0
        assert run(proj, check=True) == 0


class TestNoOpGate:
    def test_no_schema_json_anywhere_is_a_graceful_no_op(self, tmp_path):
        proj = tmp_path / "empty"
        proj.mkdir()
        assert run(proj) == 0
        assert not (proj / "docs" / "api.en.md").exists()
        assert run(proj, check=True) == 0


class TestCli:
    def test_main_entrypoint(self, tmp_path):
        proj = _plant_root_docs(tmp_path)
        assert main([str(proj)]) == 0
        assert main([str(proj), "--check"]) == 0
