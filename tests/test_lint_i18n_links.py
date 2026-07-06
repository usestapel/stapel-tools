"""R100 — README must link both language docs when i18n artifacts exist."""
from stapel_tools.lint import check_readme_i18n_links, scan_paths


def _repo(tmp_path, readme="", *, flows=(), errors_json=False, error_mds=()):
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    for lang in flows:
        d = docs / "flows" / lang
        d.mkdir(parents=True)
        (d / "README.md").write_text("# flows", encoding="utf-8")
    if errors_json:
        (docs / "errors.json").write_text("[]", encoding="utf-8")
    for lang in error_mds:
        (docs / f"errors.{lang}.md").write_text("# errors", encoding="utf-8")
    return tmp_path


def test_no_artifacts_no_violations(tmp_path):
    (tmp_path / "README.md").write_text("nothing here", encoding="utf-8")
    assert check_readme_i18n_links(str(tmp_path)) == []


def test_flows_require_both_language_links(tmp_path):
    repo = _repo(tmp_path, readme="see [Flows](docs/flows/en/README.md)",
                flows=("en", "ru"))
    v = check_readme_i18n_links(str(repo))
    # en is linked, ru is not
    assert len(v) == 1 and "ru" in v[0].message and v[0].level == "warning"


def test_flows_both_linked_is_clean(tmp_path):
    repo = _repo(
        tmp_path,
        readme="[Flows](docs/flows/en/README.md) · [Флоу](docs/flows/ru/README.md)",
        flows=("en", "ru"),
    )
    assert check_readme_i18n_links(str(repo)) == []


def test_errors_json_requires_both_error_md_links(tmp_path):
    repo = _repo(tmp_path, readme="no error links", errors_json=True)
    v = check_readme_i18n_links(str(repo))
    assert {"docs/errors.en.md", "docs/errors.ru.md"} == {
        needle for x in v for needle in ("docs/errors.en.md", "docs/errors.ru.md")
        if needle in x.message
    }
    assert all(x.level == "warning" for x in v)


def test_errors_both_linked_clean(tmp_path):
    repo = _repo(
        tmp_path,
        readme="[Errors](docs/errors.en.md) · [Ошибки](docs/errors.ru.md)",
        errors_json=True, error_mds=("en", "ru"),
    )
    assert check_readme_i18n_links(str(repo)) == []


def test_extra_language_present_must_be_linked(tmp_path):
    repo = _repo(
        tmp_path,
        readme="[Errors](docs/errors.en.md) · [Ошибки](docs/errors.ru.md)",
        errors_json=True, error_mds=("en", "ru", "es"),
    )
    v = check_readme_i18n_links(str(repo))
    assert len(v) == 1 and "es" in v[0].message


def test_scan_paths_surfaces_warnings_but_not_as_errors(tmp_path):
    repo = _repo(tmp_path, readme="empty", errors_json=True)
    violations = scan_paths([str(repo)])
    r100 = [v for v in violations if v.rule == "R100"]
    assert r100 and all(v.level == "warning" for v in r100)
