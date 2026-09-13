"""stapel-image-lint — IMG001-003 (docs/reference/base-images.md).

Every rule is exercised in both directions (violation -> reported, fix ->
silent), plus the shapes the fleet survey singled out as the traps a naive
"is the FROM line one of ours" rule falls into: a multi-stage builder whose
FIRST stage is legitimately an upstream image, a `FROM <earlier stage>` that
has to be followed back, an `ARG`-parameterised base, and a Dockerfile that is
not a Python service at all (this estate really does build a Java geocoder and
two node images next to its Django ones).
"""
import textwrap
from pathlib import Path

from stapel_tools import image_lint as il

BASE = "registry.gitlab.com/stapel-studio/stapel-images"
PINNED = "20260913-1c80c81"


def _write(tmp_path: Path, dockerfile: str, requirements: str = "", *, name="Dockerfile"):
    svc = tmp_path / "svc-x"
    svc.mkdir(parents=True, exist_ok=True)
    path = svc / name
    path.write_text(textwrap.dedent(dockerfile).lstrip(), encoding="utf-8")
    if requirements:
        (svc / "requirements.txt").write_text(
            textwrap.dedent(requirements).lstrip(), encoding="utf-8"
        )
    return path


def _rules(findings):
    return {f.rule for f in findings}


# ---------------------------------------------------------------------------
# IMG001 — not on a stapel base at all
# ---------------------------------------------------------------------------


class TestIMG001:
    def test_bare_upstream_python_is_reported(self, tmp_path):
        path = _write(tmp_path, """
            FROM python:3.12-slim
            COPY requirements.txt .
            RUN pip install --no-cache-dir -r requirements.txt
            CMD ["gunicorn", "config.wsgi:application"]
        """, "stapel-core[nats,prometheus]==0.65.0\n")
        findings = il.lint_dockerfile(path)
        # IMG004 rides along: python:3.12 is also behind the base's 3.14.
        assert _rules(findings) == {"IMG001", "IMG004"}
        assert findings[0].level == "warning", (
            "IMG001 stays a warning until the fleets are migrated — see the "
            "module docstring"
        )
        assert f"{BASE}/python-base" in findings[0].message

    def test_another_orgs_base_image_is_still_not_ours(self, tmp_path):
        # The shape most of one client fleet's services are in today.
        path = _write(tmp_path, """
            FROM registry.example.com/someone/django-base:1.0.12
            COPY requirements.txt .
            RUN pip install -r requirements.txt
        """, "stapel-core[kafka,nats,prometheus]==0.67.0\n")
        assert _rules(il.lint_dockerfile(path)) == {"IMG001"}

    def test_stapel_base_pinned_is_clean(self, tmp_path):
        path = _write(tmp_path, f"""
            FROM {BASE}/python-base:{PINNED}
            COPY requirements.txt .
            RUN pip install --no-cache-dir -r requirements.txt
        """, "stapel-core[nats,prometheus]==0.65.0\n")
        assert il.lint_dockerfile(path) == []

    def test_noqa_on_the_from_line_suppresses(self, tmp_path):
        path = _write(tmp_path, """
            FROM python:3.12-slim  # noqa: IMG001
            RUN pip install -r requirements.txt
        """, "stapel-core==0.65.0\n")
        assert il.lint_dockerfile(path) == []


# ---------------------------------------------------------------------------
# Scope — what this linter must NOT grade
# ---------------------------------------------------------------------------


class TestScope:
    def test_a_java_service_is_not_offered_a_python_base(self, tmp_path):
        """A client fleet really does run a JRE geocoder beside its Django
        services. A linter that tells a JRE image to use python-base has
        discredited itself on the one line a reader checks."""
        path = _write(tmp_path, """
            FROM eclipse-temurin:21-jre
            COPY photon.jar /app/photon.jar
            CMD ["java", "-jar", "/app/photon.jar"]
        """)
        assert il.lint_dockerfile(path) == []

    def test_a_node_build_image_is_out_of_scope(self, tmp_path):
        path = _write(tmp_path, """
            FROM node:22-alpine AS build
            RUN npm ci
            RUN npm run build
        """)
        assert il.lint_dockerfile(path) == []

    def test_scratch_is_never_flagged(self, tmp_path):
        path = _write(tmp_path, """
            FROM scratch
            COPY --from=build /app /app
            CMD ["/app/server"]
        """)
        assert il.lint_dockerfile(path) == []

    def test_only_the_final_stage_is_graded(self, tmp_path):
        """A builder stage on a bare upstream image is the SUPPORTED pattern
        (compile a wheel, ship no compiler) — what ships is the last stage."""
        path = _write(tmp_path, f"""
            FROM python:3.12-slim AS wheels
            RUN pip wheel --wheel-dir /wheels some-sdist-only-package

            FROM {BASE}/python-base:{PINNED}
            COPY --from=wheels /wheels /wheels
            RUN pip install --no-cache-dir --find-links=/wheels -r requirements.txt
        """, "stapel-core==0.65.0\n")
        assert il.lint_dockerfile(path) == []

    def test_from_an_earlier_stage_is_followed_back(self, tmp_path):
        """`FROM build AS export` — a client storefront's shape. The graded image
        is the one `build` itself rests on, and the reported line is the one a
        reader has to edit."""
        path = _write(tmp_path, """
            FROM python:3.12-slim AS build
            RUN pip install -r requirements.txt

            FROM build AS export
            CMD ["gunicorn", "config.wsgi:application"]
        """, "stapel-core==0.65.0\n")
        findings = il.lint_dockerfile(path)
        # Followed back to python:3.12, so IMG004's interpreter half applies.
        assert _rules(findings) == {"IMG001", "IMG004"}
        assert findings[0].line == 1, "the actionable line is the real FROM, not the alias"


# ---------------------------------------------------------------------------
# IMG002 — on a stapel base, but the wrong one. An ERROR from day one,
# because it cannot fire on a project that has not migrated.
# ---------------------------------------------------------------------------


class TestIMG002:
    def test_recordings_on_python_base_is_an_error(self, tmp_path):
        path = _write(tmp_path, f"""
            FROM {BASE}/python-base:{PINNED}
            COPY requirements.txt .
            RUN pip install --no-cache-dir -r requirements.txt
        """, """
            stapel-core[kafka,nats,prometheus]==0.67.0
            stapel-recordings[s3,vector,qa]==0.24.0
        """)
        findings = il.lint_dockerfile(path)
        assert _rules(findings) == {"IMG002"}
        assert findings[0].level == "error"
        assert "media-base" in findings[0].message

    def test_recordings_on_media_base_is_clean(self, tmp_path):
        path = _write(tmp_path, f"""
            FROM {BASE}/media-base:{PINNED}
            COPY requirements.txt .
            RUN pip install --no-cache-dir -r requirements.txt
        """, "stapel-recordings[s3]==0.24.0\n")
        assert il.lint_dockerfile(path) == []

    def test_torch_on_media_base_is_an_error(self, tmp_path):
        path = _write(tmp_path, f"""
            FROM {BASE}/media-base:{PINNED}
            COPY requirements.txt .
            RUN pip install --no-cache-dir -r requirements.txt
        """, """
            --extra-index-url https://download.pytorch.org/whl/cpu
            torch==2.5.1
            pyannote.audio==3.3.2
        """)
        findings = il.lint_dockerfile(path)
        assert _rules(findings) == {"IMG002"}
        assert "ml-base" in findings[0].message

    def test_a_heavier_base_than_needed_is_not_an_error(self, tmp_path):
        """ml-base for a plain service is wasteful, not wrong — and this rule
        refuses to have an opinion it cannot ground. Weight is a size question
        and the sizes are in VERSIONS.md; correctness is what fires here."""
        path = _write(tmp_path, f"""
            FROM {BASE}/ml-base:{PINNED}
            RUN pip install -r requirements.txt
        """, "stapel-core==0.67.0\n")
        assert il.lint_dockerfile(path) == []

    def test_cdn_without_the_images_extra_needs_nothing_heavier(self, tmp_path):
        """A files-only CDN deployment genuinely does not need libvips — the
        extra is what decides, not the package name."""
        path = _write(tmp_path, f"""
            FROM {BASE}/python-base:{PINNED}
            RUN pip install -r requirements.txt
        """, "stapel-cdn[s3]==0.20.1\n")
        assert il.lint_dockerfile(path) == []

    def test_cdn_with_the_images_extra_needs_media(self, tmp_path):
        path = _write(tmp_path, f"""
            FROM {BASE}/python-base:{PINNED}
            RUN pip install -r requirements.txt
        """, "stapel-cdn[images]==0.20.1\n")
        assert _rules(il.lint_dockerfile(path)) == {"IMG002"}

    def test_the_manifest_overrides_the_inference(self, tmp_path):
        """A need the inference cannot see — a runtime that shells out to a
        binary named only in Python code — is declared, and the declaration is
        then what the rule checks against."""
        path = _write(tmp_path, f"""
            FROM {BASE}/python-base:{PINNED}
            RUN pip install -r requirements.txt
        """, "stapel-core==0.67.0\n")
        (path.parent / "stapel-service.toml").write_text(
            '[image]\nbase = "media-base"\n', encoding="utf-8"
        )
        findings = il.lint_dockerfile(path)
        assert _rules(findings) == {"IMG002"}
        assert "stapel-service.toml" in findings[0].message


# ---------------------------------------------------------------------------
# IMG003 — a stapel base pinned by its moving tag
# ---------------------------------------------------------------------------


class TestIMG003:
    def test_moving_major_tag_is_reported(self, tmp_path):
        path = _write(tmp_path, f"""
            FROM {BASE}/python-base:1
            RUN pip install -r requirements.txt
        """, "stapel-core==0.67.0\n")
        findings = il.lint_dockerfile(path)
        assert _rules(findings) == {"IMG003"}
        assert findings[0].level == "warning"

    def test_dated_sha_tag_is_clean(self, tmp_path):
        path = _write(tmp_path, f"""
            FROM {BASE}/python-base:{PINNED}
            RUN pip install -r requirements.txt
        """, "stapel-core==0.67.0\n")
        assert il.lint_dockerfile(path) == []

    def test_digest_is_clean(self, tmp_path):
        digest = "sha256:" + "a" * 64
        path = _write(tmp_path, f"""
            FROM {BASE}/python-base@{digest}
            RUN pip install -r requirements.txt
        """, "stapel-core==0.67.0\n")
        assert il.lint_dockerfile(path) == []

    def test_no_tag_at_all_is_reported(self, tmp_path):
        path = _write(tmp_path, f"""
            FROM {BASE}/python-base
            RUN pip install -r requirements.txt
        """, "stapel-core==0.67.0\n")
        assert _rules(il.lint_dockerfile(path)) == {"IMG003"}


# ---------------------------------------------------------------------------
# ARG-parameterised bases — how every derived image in stapel-images itself,
# and a client fleet's benchmark harness, actually writes its FROM
# ---------------------------------------------------------------------------


class TestArgSubstitution:
    def test_arg_default_is_substituted(self, tmp_path):
        path = _write(tmp_path, f"""
            ARG PYTHON_BASE={BASE}/python-base:{PINNED}
            FROM ${{PYTHON_BASE}}
            RUN pip install -r requirements.txt
        """, "stapel-core==0.67.0\n")
        assert il.lint_dockerfile(path) == []

    def test_arg_default_pointing_upstream_is_reported(self, tmp_path):
        path = _write(tmp_path, """
            ARG PYTHON_BASE=python:3.12-slim-bookworm
            FROM ${PYTHON_BASE}
            RUN pip install -r requirements.txt
        """, "stapel-core==0.67.0\n")
        # The ARG default substitutes to python:3.12, which IMG004 also grades.
        assert _rules(il.lint_dockerfile(path)) == {"IMG001", "IMG004"}

    def test_arg_with_no_default_is_unresolvable_not_guessed(self, tmp_path):
        """A linter that guesses here is a linter that certifies a base image
        nobody has."""
        path = _write(tmp_path, """
            ARG PYTHON_BASE
            FROM ${PYTHON_BASE}
            RUN pip install -r requirements.txt
        """, "stapel-core==0.67.0\n")
        findings = il.lint_dockerfile(path)
        assert _rules(findings) == {"IMG001"}
        assert "resolves to nothing" in findings[0].message


# ---------------------------------------------------------------------------
# Requirements discovery — the two fleet layouts
# ---------------------------------------------------------------------------


class TestRequirementsDiscovery:
    def test_context_is_repo_root_layout(self, tmp_path):
        """Both fleets build with the REPO ROOT as context, so the Dockerfile
        says `COPY svc-x/requirements.txt .` while living in `svc-x/`.
        Resolving "the requirements.txt next to the Dockerfile" would find the
        right file in one layout and nothing in the other."""
        svc = tmp_path / "svc-x"
        svc.mkdir()
        (svc / "requirements.txt").write_text("stapel-recordings==0.24.0\n", encoding="utf-8")
        path = svc / "Dockerfile"
        path.write_text(
            f"FROM {BASE}/python-base:{PINNED}\n"
            "COPY svc-x/requirements.txt .\n"
            "RUN pip install --no-cache-dir -r requirements.txt\n",
            encoding="utf-8",
        )
        assert _rules(il.lint_dockerfile(path)) == {"IMG002"}


# ---------------------------------------------------------------------------
# CLI / project driver
# ---------------------------------------------------------------------------


class TestDriver:
    def test_clean_tree_exits_zero(self, tmp_path, capsys):
        _write(tmp_path, f"""
            FROM {BASE}/python-base:{PINNED}
            RUN pip install -r requirements.txt
        """, "stapel-core==0.67.0\n")
        assert il.main([str(tmp_path)]) == 0

    def test_warnings_alone_exit_zero(self, tmp_path):
        _write(tmp_path, """
            FROM python:3.12-slim
            RUN pip install -r requirements.txt
        """, "stapel-core==0.67.0\n")
        assert il.main([str(tmp_path)]) == 0

    def test_strict_promotes_warnings(self, tmp_path):
        """How a fleet that HAS migrated keeps IMG001 from regressing before
        the default level flips."""
        _write(tmp_path, """
            FROM python:3.12-slim
            RUN pip install -r requirements.txt
        """, "stapel-core==0.67.0\n")
        assert il.main([str(tmp_path), "--strict"]) == 1

    def test_errors_exit_one(self, tmp_path):
        _write(tmp_path, f"""
            FROM {BASE}/python-base:{PINNED}
            RUN pip install -r requirements.txt
        """, "stapel-recordings==0.24.0\n")
        assert il.main([str(tmp_path)]) == 1

    def test_a_tree_with_no_dockerfile_is_silent(self, tmp_path):
        notes: list = []
        assert il.lint_project(tmp_path, notes=notes) == []
        assert notes == ["no Dockerfile in this tree — image rules are silent"]

    def test_json_output_shape(self, tmp_path, capsys):
        _write(tmp_path, f"""
            FROM {BASE}/python-base:{PINNED}
            RUN pip install -r requirements.txt
        """, "stapel-recordings==0.24.0\n")
        il.main([str(tmp_path), "--json"])
        import json as _json
        payload = _json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["errors"] == 1
        assert payload["violations"][0]["rule"] == "IMG002"


# ---------------------------------------------------------------------------
# IMG004 — a declaration older than the base it will be installed on
# ---------------------------------------------------------------------------


class TestImg004Django:
    """The requirements half: a cap that EXCLUDES the base's Django.

    This is the shape that does not fail. The service builds, pip quietly
    downgrades the framework inside the image, and the estate believes it runs
    something it does not — which is why the finding has to come from a
    linter rather than from a build.
    """

    def _rules(self, violations):
        return [(v.rule, v.level) for v in violations]

    def test_cap_below_the_base_is_reported(self, tmp_path):
        path = _write(tmp_path, f"""
            FROM {BASE}/python-base:{PINNED}
            RUN pip install -r requirements.txt
        """, "stapel-core==0.65.0\nDjango>=5.1,<6.0\n")
        found = [v for v in il.lint_dockerfile(path) if v.rule == "IMG004"]
        assert len(found) == 1
        assert found[0].level == "warning"
        assert "Django>=5.1,<6.0" in found[0].message

    def test_a_cap_that_admits_the_base_is_silent(self, tmp_path):
        path = _write(tmp_path, f"""
            FROM {BASE}/python-base:{PINNED}
            RUN pip install -r requirements.txt
        """, "stapel-core==0.68.0\nDjango>=6.0,<7\n")
        assert [v for v in il.lint_dockerfile(path) if v.rule == "IMG004"] == []

    def test_the_retired_core_cap_admits_the_base(self, tmp_path):
        """`<6.1` was stapel-core's bound until 0.68.0. It contains 6.0.x, so
        it was never what kept the estate off Django 6 — the fleet's own
        `<6.0` was. The rule has to tell those two apart."""
        path = _write(tmp_path, f"""
            FROM {BASE}/python-base:{PINNED}
            RUN pip install -r requirements.txt
        """, "Django>=5.1,<6.1\n")
        assert [v for v in il.lint_dockerfile(path) if v.rule == "IMG004"] == []

    def test_an_exact_old_pin_is_reported(self, tmp_path):
        path = _write(tmp_path, f"""
            FROM {BASE}/python-base:{PINNED}
            RUN pip install -r requirements.txt
        """, "Django==5.2.17\n")
        found = [v for v in il.lint_dockerfile(path) if v.rule == "IMG004"]
        assert len(found) == 1

    def test_no_django_declared_is_silent(self, tmp_path):
        """Most services let stapel-core carry Django. Nothing to grade."""
        path = _write(tmp_path, f"""
            FROM {BASE}/python-base:{PINNED}
            RUN pip install -r requirements.txt
        """, "stapel-core==0.68.0\n")
        assert [v for v in il.lint_dockerfile(path) if v.rule == "IMG004"] == []

    def test_noqa_suppresses_it(self, tmp_path):
        path = _write(tmp_path, f"""
            FROM {BASE}/python-base:{PINNED}  # noqa: IMG004
            RUN pip install -r requirements.txt
        """, "Django>=5.1,<6.0\n")
        assert [v for v in il.lint_dockerfile(path) if v.rule == "IMG004"] == []

    def test_it_is_a_warning_so_a_clean_tree_still_exits_zero(self, tmp_path):
        _write(tmp_path, f"""
            FROM {BASE}/python-base:{PINNED}
            RUN pip install -r requirements.txt
        """, "Django>=5.1,<6.0\n")
        assert il.main([str(tmp_path)]) == 0
        assert il.main([str(tmp_path), "--strict"]) == 1


class TestImg004Python:
    """The Dockerfile half: an interpreter older than the base's."""

    def test_an_older_python_image_is_reported(self, tmp_path):
        path = _write(tmp_path, """
            FROM python:3.12-slim
            RUN pip install -r requirements.txt
        """, "stapel-core==0.68.0\n")
        found = [v for v in il.lint_dockerfile(path) if v.rule == "IMG004"]
        assert len(found) == 1
        assert "python:3.12" in found[0].message

    def test_the_bases_own_python_is_silent(self, tmp_path):
        path = _write(tmp_path, """
            FROM python:3.14-slim-trixie
            RUN pip install -r requirements.txt
        """, "stapel-core==0.68.0\n")
        assert [v for v in il.lint_dockerfile(path) if v.rule == "IMG004"] == []

    def test_a_newer_python_is_not_reported(self, tmp_path):
        """The rule is about falling BEHIND the base, not about differing."""
        path = _write(tmp_path, """
            FROM python:3.15-slim
            RUN pip install -r requirements.txt
        """, "stapel-core==0.68.0\n")
        assert [v for v in il.lint_dockerfile(path) if v.rule == "IMG004"] == []

    def test_a_builder_stage_on_an_old_python_is_not_graded(self, tmp_path):
        """Only the final stage ships. Compiling a wheel on 3.12 is fine."""
        path = _write(tmp_path, f"""
            FROM python:3.12-slim AS builder
            RUN pip wheel --wheel-dir /wheels pyvips

            FROM {BASE}/media-base:{PINNED}
            COPY --from=builder /wheels /wheels
            RUN pip install -r requirements.txt
        """, "stapel-core==0.68.0\nDjango>=6.0,<7\n")
        assert [v for v in il.lint_dockerfile(path) if v.rule == "IMG004"] == []


class TestAllowsSpecifier:
    """The PEP 440 subset IMG004 leans on, at the majors it actually sees."""

    def test_the_shapes_the_estate_declares(self):
        base = il.BASE_DJANGO
        assert il._allows(">=6.0,<7", base) is True
        assert il._allows(">=5.2,<7.0", base) is True
        assert il._allows(">=5.1,<6.1", base) is True
        assert il._allows(">=5.1,<6.0", base) is False
        assert il._allows("==5.2.17", base) is False
        assert il._allows("==6.0.8", base) is True
        assert il._allows(">=5.1", base) is True
        assert il._allows("~=5.2", base) is False
        assert il._allows("==6.*", base) is True

    def test_an_unparseable_specifier_is_permissive(self):
        """A linter that guesses a cap into existence is worse than one that
        misses it."""
        assert il._allows("@ https://example.invalid/django.whl", il.BASE_DJANGO) is True
