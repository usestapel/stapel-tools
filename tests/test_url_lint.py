"""stapel-url-lint tests (docs/reference/library-standard.md §3.8).

URL001 (bare ``models.URLField()`` without explicit ``max_length``), the
explicit-max_length pass case (any width, 200 included), the noqa escape,
the DRF ``serializers.URLField`` exclusion, and unresolved-qualifier
default-to-flag behavior.
"""
from pathlib import Path

from stapel_tools.url_lint import lint_file, lint_project


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _codes(violations):
    return sorted(v.rule for v in violations)


# --- URL001: flagged --------------------------------------------------------


def test_bare_django_urlfield_is_flagged(tmp_path):
    path = _write(tmp_path, "models.py", """\
from django.db import models


class Thing(models.Model):
    avatar = models.URLField(blank=True, null=True)
""")
    violations = lint_file(path)
    assert _codes(violations) == ["URL001"]
    assert violations[0].line == 5


def test_bare_urlfield_direct_import_is_flagged(tmp_path):
    path = _write(tmp_path, "models.py", """\
from django.db.models import URLField, Model


class Thing(Model):
    webhook_url = URLField(blank=True)
""")
    violations = lint_file(path)
    assert _codes(violations) == ["URL001"]


def test_unresolved_qualifier_defaults_to_flagged(tmp_path):
    # "models" bound via plain ``import`` (not ``from ... import``) is not
    # resolvable by the import-alias scan — the rule errs toward flagging.
    path = _write(tmp_path, "models.py", """\
import django.db.models as models


class Thing(models.Model):
    provider_callback = models.URLField(blank=True)
""")
    violations = lint_file(path)
    assert _codes(violations) == ["URL001"]


# --- URL001: not flagged -----------------------------------------------------


def test_explicit_max_length_500_passes(tmp_path):
    path = _write(tmp_path, "models.py", """\
from django.db import models


class Thing(models.Model):
    avatar = models.URLField(max_length=500, blank=True, null=True)
""")
    assert lint_file(path) == []


def test_explicit_max_length_200_still_passes(tmp_path):
    # Rule is "explicit", not "wide" — an internal/short config URL that
    # deliberately keeps 200 is a pass; only the bare/implicit call fails.
    path = _write(tmp_path, "models.py", """\
from django.db import models


class Thing(models.Model):
    internal_ref = models.URLField(max_length=200, blank=True)
""")
    assert lint_file(path) == []


def test_noqa_suppresses(tmp_path):
    path = _write(tmp_path, "models.py", """\
from django.db import models


class Thing(models.Model):
    legacy = models.URLField(blank=True)  # noqa: URL001
""")
    assert lint_file(path) == []


def test_blanket_noqa_suppresses(tmp_path):
    path = _write(tmp_path, "models.py", """\
from django.db import models


class Thing(models.Model):
    legacy = models.URLField(blank=True)  # noqa
""")
    assert lint_file(path) == []


def test_noqa_other_rule_does_not_suppress(tmp_path):
    path = _write(tmp_path, "models.py", """\
from django.db import models


class Thing(models.Model):
    legacy = models.URLField(blank=True)  # noqa: SOMETHING_ELSE
""")
    assert _codes(lint_file(path)) == ["URL001"]


def test_drf_serializer_urlfield_is_excluded(tmp_path):
    path = _write(tmp_path, "serializers.py", """\
from rest_framework import serializers


class ThingSerializer(serializers.Serializer):
    variant_url = serializers.URLField(read_only=True)
""")
    assert lint_file(path) == []


def test_drf_serializer_direct_import_is_excluded(tmp_path):
    path = _write(tmp_path, "serializers.py", """\
from rest_framework.serializers import URLField, Serializer


class ThingSerializer(Serializer):
    variant_url = URLField(read_only=True)
""")
    assert lint_file(path) == []


# --- project-level scan: migrations skipped, multi-file -------------------


def test_migrations_dir_is_skipped(tmp_path):
    proj = tmp_path / "proj"
    _write(proj, "models.py", """\
from django.db import models


class Thing(models.Model):
    avatar = models.URLField(max_length=500, blank=True)
""")
    _write(proj, "migrations/0001_initial.py", """\
from django.db import migrations, models


class Migration(migrations.Migration):
    operations = [
        migrations.AddField(
            model_name="thing",
            name="avatar",
            field=models.URLField(blank=True),
        ),
    ]
""")
    violations = lint_project(proj)
    assert violations == []


def test_project_scan_finds_across_files(tmp_path):
    proj = tmp_path / "proj"
    _write(proj, "models.py", """\
from django.db import models


class Org(models.Model):
    sso_url = models.URLField(blank=True)
""")
    _write(proj, "other/models.py", """\
from django.db import models


class Widget(models.Model):
    homepage = models.URLField(blank=True)
""")
    violations = lint_project(proj)
    assert len(violations) == 2
    assert _codes(violations) == ["URL001", "URL001"]
