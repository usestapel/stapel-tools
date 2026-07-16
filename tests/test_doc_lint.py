"""stapel-doc-lint tests (§55 extensibility-presenters.md §3 — DOC001,
the "DOC-FIELD" rule). Warning-level: a Django model field with neither
``help_text=`` nor a preceding ``#`` comment.
"""
from pathlib import Path

from stapel_tools.doc_lint import lint_file, lint_project


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _codes(violations):
    return sorted(v.rule for v in violations)


# --- DOC001: flagged ---------------------------------------------------------


def test_field_without_help_text_or_comment_is_flagged(tmp_path):
    path = _write(tmp_path, "models.py", """\
from django.db import models


class Thing(models.Model):
    name = models.CharField(max_length=100)
""")
    violations = lint_file(path)
    assert _codes(violations) == ["DOC001"]
    assert violations[0].level == "warning"
    assert violations[0].line == 5


def test_foreign_key_without_docs_is_flagged(tmp_path):
    path = _write(tmp_path, "models.py", """\
from django.db import models


class Order(models.Model):
    customer = models.ForeignKey("app.Customer", on_delete=models.CASCADE)
""")
    violations = lint_file(path)
    assert _codes(violations) == ["DOC001"]


def test_multiple_undocumented_fields_each_flagged(tmp_path):
    path = _write(tmp_path, "models.py", """\
from django.db import models


class Thing(models.Model):
    name = models.CharField(max_length=100)
    age = models.IntegerField()
""")
    violations = lint_file(path)
    assert len(violations) == 2


# --- DOC001: not flagged ------------------------------------------------------


def test_help_text_keyword_passes(tmp_path):
    path = _write(tmp_path, "models.py", """\
from django.db import models


class Thing(models.Model):
    name = models.CharField(max_length=100, help_text="Display name.")
""")
    assert lint_file(path) == []


def test_preceding_comment_passes(tmp_path):
    path = _write(tmp_path, "models.py", """\
from django.db import models


class Thing(models.Model):
    # Display name shown on the public profile.
    name = models.CharField(max_length=100)
""")
    assert lint_file(path) == []


def test_noqa_suppresses(tmp_path):
    path = _write(tmp_path, "models.py", """\
from django.db import models


class Thing(models.Model):
    name = models.CharField(max_length=100)  # noqa: DOC001
""")
    assert lint_file(path) == []


def test_blanket_noqa_suppresses(tmp_path):
    path = _write(tmp_path, "models.py", """\
from django.db import models


class Thing(models.Model):
    name = models.CharField(max_length=100)  # noqa
""")
    assert lint_file(path) == []


def test_noqa_other_rule_does_not_suppress(tmp_path):
    path = _write(tmp_path, "models.py", """\
from django.db import models


class Thing(models.Model):
    name = models.CharField(max_length=100)  # noqa: SOMETHING_ELSE
""")
    assert _codes(lint_file(path)) == ["DOC001"]


def test_non_model_class_is_ignored(tmp_path):
    path = _write(tmp_path, "models.py", """\
class PlainHelper:
    name = SomeField(max_length=100)
""")
    assert lint_file(path) == []


def test_non_field_class_attribute_is_ignored(tmp_path):
    path = _write(tmp_path, "models.py", """\
from django.db import models


class Thing(models.Model):
    objects = models.Manager()
    STATUS_ACTIVE = "active"
""")
    assert lint_file(path) == []


def test_dto_py_is_not_scanned(tmp_path):
    # @dataclass docstring gate is R004 in lint.py; DOC001 never touches dto.py.
    _write(tmp_path, "dto.py", """\
from django.db import models


class Thing(models.Model):
    name = models.CharField(max_length=100)
""")
    assert lint_project(tmp_path) == []


# --- project-level scan -------------------------------------------------------


def test_migrations_dir_is_skipped(tmp_path):
    proj = tmp_path / "proj"
    _write(proj, "models.py", """\
from django.db import models


class Thing(models.Model):
    name = models.CharField(max_length=100, help_text="ok")
""")
    _write(proj, "migrations/0001_initial.py", """\
from django.db import migrations, models


class Migration(migrations.Migration):
    operations = [
        migrations.AddField(
            model_name="thing",
            name="name",
            field=models.CharField(max_length=100),
        ),
    ]
""")
    assert lint_project(proj) == []


def test_project_scan_finds_across_files(tmp_path):
    proj = tmp_path / "proj"
    _write(proj, "models.py", """\
from django.db import models


class Org(models.Model):
    slug = models.CharField(max_length=50)
""")
    _write(proj, "other/models.py", """\
from django.db import models


class Widget(models.Model):
    title = models.CharField(max_length=50)
""")
    violations = lint_project(proj)
    assert len(violations) == 2
    assert _codes(violations) == ["DOC001", "DOC001"]
