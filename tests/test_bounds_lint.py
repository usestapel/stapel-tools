"""stapel-bounds-lint — BND001-BND003.

The fixtures are transcriptions, not inventions. The model is
``stapel-alerts/models.py``; the broken write is the ``Issue.objects.create(
...)`` that answered 500 on 2026-09-15 (``stapel-alerts`` at ``cb6082c``,
before ``bounds.py`` existed) — both in the ingest frame where its payload is
visible and in ``record()``, the helper it actually lived in, which is BND003's
positive control; the clean ones are the repaired forms, which call
``bounds.fit`` and ``bounds.choice_or_default``. ``fingerprint`` is the precision test case and
must stay silent in every one of them: a sha256 hex digest is exactly 64 into
a 64 column, and cutting it would merge two bugs that share a prefix.

A linter whose fixtures are written to match the linter proves only that the
author was consistent.
"""
import json
import textwrap
from pathlib import Path

from stapel_tools import bounds_lint as bl, lint_profile, verify

# ---------------------------------------------------------------------------
# the model, transcribed from stapel-alerts/models.py
# ---------------------------------------------------------------------------

MODELS = '''
from django.db import models


class Level(models.TextChoices):
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"


class Issue(models.Model):
    """One bug, as the tracker knows it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    fingerprint = models.CharField(max_length=64, unique=True, db_index=True)
    service = models.CharField(max_length=64, db_index=True)
    environment = models.CharField(max_length=32, default="production", db_index=True)
    level = models.CharField(
        max_length=16, choices=Level.choices, default=Level.ERROR, db_index=True
    )

    title = models.CharField(max_length=255)
    culprit = models.CharField(max_length=255, blank=True, default="")
    exception_class = models.CharField(max_length=128, blank=True, default="", db_index=True)
    normalised_trace = models.TextField(blank=True, default="")

    count = models.PositiveIntegerField(default=0)
'''

#: A model with nothing to overflow. The rules must be silent here — an
#: alert store's TextFields and counters are not this gate's business.
MODELS_UNBOUNDED = '''
from django.db import models


class ErrorEvent(models.Model):
    message = models.TextField(blank=True, default="")
    trace = models.TextField(blank=True, default="")
    context = models.JSONField(default=dict, blank=True)
    occurrences = models.PositiveIntegerField(default=1)
'''

MODELS_INHERITED = '''
from django.db import models


class Stamped(models.Model):
    release = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        abstract = True


class Report(Stamped):
    title = models.CharField(max_length=255)
'''


# ---------------------------------------------------------------------------
# call sites, transcribed from stapel-alerts
# ---------------------------------------------------------------------------

#: The write that broke, in the frame where the payload is visible: the report
#: endpoint's own loop (views.py builds `payload = dict(item)` per reported
#: event) with the create it ultimately performs (services.py at cb6082c).
INGEST_BROKEN = '''
def ingest(request):
    """POST /alerts/api/v1/report — one row per reported occurrence."""
    for item in (request.data or {}).get("events") or []:
        payload = dict(item)
        frames = norm.normalise_trace(payload.get("trace") or "")
        Issue.objects.create(
            fingerprint=norm.fingerprint(payload.get("trace"), service=payload.get("service")),
            service=payload.get("service") or "unknown",
            environment=payload.get("environment") or "production",
            level=payload.get("level") or "error",
            title=norm.title_for(payload.get("trace"), message=payload.get("message")),
            culprit=frames[0] if frames else "",
            normalised_trace="\\n".join(frames),
            count=1,
        )
'''

#: The repair (stapel-alerts/bounds.py + services.py). `service` is fitted
#: BEFORE the fingerprint is taken, which is why it is a local here.
INGEST_REPAIRED = '''
def ingest(request):
    for item in (request.data or {}).get("events") or []:
        payload = dict(item)
        frames = norm.normalise_trace(payload.get("trace") or "")
        service = bounds.fit(Issue, "service", payload.get("service") or "unknown")
        Issue.objects.create(
            fingerprint=norm.fingerprint(payload.get("trace"), service=service),
            service=service,
            environment=bounds.fit(Issue, "environment", payload.get("environment")),
            level=bounds.choice_or_default(Issue, "level", payload.get("level")),
            title=bounds.fit(Issue, "title", norm.title_for(payload.get("trace"))),
            culprit=bounds.fit(Issue, "culprit", frames[0] if frames else ""),
            normalised_trace="\\n".join(frames),
            count=1,
        )
'''

#: The same write, in the helper it actually lived in: `record()` receives the
#: payload already unpacked into ordinary str parameters, so no taint analysis
#: can see it. BND003's positive control.
RECORD_HELPER = '''
@transaction.atomic
def record(*, trace="", message="", service="", level=Level.ERROR,
           environment="production", occurrences=1):
    frames = norm.normalise_trace(trace) if trace else [norm.normalise_line(message)]
    fingerprint = norm.fingerprint(trace or message, service=service)
    return Issue.objects.create(
        fingerprint=fingerprint,
        service=service,
        environment=environment,
        level=level,
        title=norm.title_for(trace, message=message),
        culprit=frames[0] if frames else "",
        count=occurrences,
    )
'''


#: The negative control: the same frame, repaired (stapel-alerts 0.2.2's
#: `bounds.fit` around the same helper, and `choice_or_default` for the
#: vocabulary). `fingerprint` is deliberately left unfitted.
RECORD_REPAIRED = '''
@transaction.atomic
def record(*, trace="", message="", service="", level=Level.ERROR,
           environment="production", occurrences=1):
    service = bounds.fit(Issue, "service", service)
    environment = bounds.fit(Issue, "environment", environment)
    level = bounds.choice_or_default(Issue, "level", level)
    frames = norm.normalise_trace(trace) if trace else [norm.normalise_line(message)]
    fingerprint = norm.fingerprint(trace or message, service=service)
    return Issue.objects.create(
        fingerprint=fingerprint,
        service=service,
        environment=environment,
        level=level,
        title=bounds.fit(Issue, "title", norm.title_for(trace, message=message)),
        culprit=bounds.fit(Issue, "culprit", frames[0] if frames else ""),
        count=occurrences,
    )
'''


def _module(tmp_path, source, name="views.py"):
    path = tmp_path / name
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


def _library(tmp_path, name="stapel-thing", *, models=MODELS, sources=None):
    lib = tmp_path / name
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (lib / "models.py").write_text(textwrap.dedent(models).lstrip(), encoding="utf-8")
    for fname, body in (sources or {}).items():
        (lib / fname).write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return lib


def _table(tmp_path, models=MODELS):
    (tmp_path / "models.py").write_text(textwrap.dedent(models).lstrip(), encoding="utf-8")
    return bl.build_field_table(tmp_path)


def _rules(violations):
    return {v.rule for v in violations}


def _fields(violations, rule=None):
    """The model field each finding is about, read out of its message."""
    out = set()
    for violation in violations:
        if rule and violation.rule != rule:
            continue
        out.add(violation.message.split(" ")[0])
    return out


# ---------------------------------------------------------------------------
# the field table — the limit is read off the model, never typed twice
# ---------------------------------------------------------------------------


class TestFieldTable:
    def test_limits_and_vocabularies_are_read_off_the_model(self, tmp_path):
        table = _table(tmp_path)
        assert set(table) == {"Issue"}
        fields = table["Issue"]
        assert fields["title"].max_length == 255
        assert fields["service"].max_length == 64
        assert fields["environment"].max_length == 32
        assert fields["level"].max_length == 16
        assert fields["level"].has_choices is True
        assert fields["title"].has_choices is False

    def test_unbounded_columns_are_not_in_the_table(self, tmp_path):
        """TextField, JSONField and the counters have nothing to overflow."""
        fields = _table(tmp_path)["Issue"]
        assert "normalised_trace" not in fields
        assert "count" not in fields
        assert "id" not in fields

    def test_a_model_with_no_bounded_column_carries_no_fields(self, tmp_path):
        assert _table(tmp_path, MODELS_UNBOUNDED) == {"ErrorEvent": {}}

    def test_an_abstract_base_in_the_same_module_is_merged_in(self, tmp_path):
        table = _table(tmp_path, MODELS_INHERITED)
        assert table["Report"]["release"].max_length == 64
        assert table["Report"]["title"].max_length == 255

    def test_a_textchoices_class_is_not_a_model(self, tmp_path):
        assert "Level" not in _table(tmp_path)


# ---------------------------------------------------------------------------
# BND001 — the splat
# ---------------------------------------------------------------------------


class TestBND001:
    def test_create_splatting_a_payload_is_an_error(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request):
                payload = request.data
                return Issue.objects.create(**payload)
        ''')
        findings = bl.lint_source_file(path, table)
        assert _rules(findings) == {"BND001"}
        assert findings[0].level == "error"
        assert "request.data via `payload`" in findings[0].message, (
            "a finding names where the payload came INTO the frame, not the "
            "local it was parked in"
        )
        # the TIGHTEST columns first — those are the ones a payload overflows
        assert "level(16)" in findings[0].message
        assert "(+2 more)" in findings[0].message

    def test_the_constructor_is_the_same_shape(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(payload):
                issue = Issue(**payload)
                issue.save()
        ''')
        assert _rules(bl.lint_source_file(path, table)) == {"BND001"}

    def test_defaults_bound_to_a_payload_name_is_the_same_shape(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def sync(request):
                data = json.loads(request.body)
                Issue.objects.update_or_create(fingerprint=data["fp"][:64], defaults=data)
        ''')
        findings = bl.lint_source_file(path, table)
        assert _rules(findings) == {"BND001"}
        assert "json.loads" in findings[0].message

    def test_a_validated_serializer_payload_is_still_a_payload(self, tmp_path):
        """`validated_data` validates TYPES, not the column's length — a
        serializer with no max_length on the field passes a 4 kB title."""
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(serializer):
                serializer.is_valid(raise_exception=True)
                return Issue.objects.create(**serializer.validated_data)
        ''')
        assert _rules(bl.lint_source_file(path, table)) == {"BND001"}

    def test_a_plain_kwargs_factory_is_not_a_payload(self, tmp_path):
        """The boundary: `**kwargs` is the most common factory in the estate
        and almost never a door. Only a parameter NAMED like a payload is."""
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def make_issue(**kwargs):
                return Issue.objects.create(**kwargs)
        ''')
        assert bl.lint_source_file(path, table) == []

    def test_a_model_with_no_bounded_column_is_never_reported(self, tmp_path):
        table = _table(tmp_path, MODELS_UNBOUNDED)
        path = _module(tmp_path, '''
            def ingest(request):
                payload = request.data
                return ErrorEvent.objects.create(**payload)
        ''')
        assert bl.lint_source_file(path, table) == []

    def test_noqa_on_the_call_suppresses(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request):
                payload = request.data
                return Issue.objects.create(**payload)  # noqa: BND001
        ''')
        assert bl.lint_source_file(path, table) == []


# ---------------------------------------------------------------------------
# BND002 — the direct subscript
# ---------------------------------------------------------------------------


class TestBND002:
    def test_the_write_that_answered_500_is_reported(self, tmp_path):
        """The 2026-09-15 shape. Three columns take the payload directly
        (BND002) and the title arrives through a helper (BND003) — `title` is
        the one that actually overflowed, and it is the reason BND003 exists."""
        table = _table(tmp_path)
        findings = bl.lint_source_file(_module(tmp_path, INGEST_BROKEN), table)
        assert _fields(findings, "BND002") == {
            "Issue.service", "Issue.environment", "Issue.level",
        }
        assert _fields(findings, "BND003") == {"Issue.title"}
        for finding in findings:
            if finding.rule == "BND002":
                assert finding.level == "error"

    def test_the_repaired_write_is_silent(self, tmp_path):
        table = _table(tmp_path)
        assert bl.lint_source_file(_module(tmp_path, INGEST_REPAIRED), table) == []

    def test_the_fingerprint_is_never_reported(self, tmp_path):
        """Deliberately NOT fitted anywhere: a sha256 hex digest is exactly 64
        into a 64 column, so fitting it could only ever be a no-op — and a cut
        one would merge two bugs that share a prefix."""
        table = _table(tmp_path)
        for source in (INGEST_BROKEN, INGEST_REPAIRED, RECORD_HELPER):
            findings = bl.lint_source_file(_module(tmp_path, source), table)
            assert not any("fingerprint" in f.message.split(" ")[0] for f in findings)

    def test_a_get_with_a_default_is_not_a_bound(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request):
                data = request.data
                Issue.objects.create(service=data.get("service", ""), title=data["title"][:255])
        ''')
        findings = bl.lint_source_file(path, table)
        assert _fields(findings) == {"Issue.service"}
        assert "max_length=64" in findings[0].message

    def test_a_defaults_literal_is_read_entry_by_entry(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def sync(request):
                payload = request.data
                Issue.objects.update_or_create(
                    fingerprint=payload["fingerprint"][:64],
                    defaults={"title": payload["title"], "count": 1},
                )
        ''')
        findings = bl.lint_source_file(path, table)
        assert _fields(findings) == {"Issue.title"}
        assert "defaults=title=" in findings[0].message

    def test_an_attribute_assignment_on_a_known_instance(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def repair(request, issue_id):
                issue = Issue.objects.get(id=issue_id)
                issue.culprit = request.data["culprit"]
                issue.save(update_fields=["culprit"])
        ''')
        findings = bl.lint_source_file(path, table)
        assert _rules(findings) == {"BND002"}
        assert "Issue.culprit" in findings[0].message

    def test_an_attribute_assignment_on_an_unknown_receiver_is_silent(self, tmp_path):
        """The boundary: with no model for the receiver, reporting on the
        field NAME alone would grade every `self.title = ...` in the estate
        against somebody else's column."""
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def repair(request, form):
                form.title = request.data["title"]
        ''')
        assert bl.lint_source_file(path, table) == []

    def test_a_queryset_update_is_a_write(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def rename(request, issue_id):
                payload = request.data
                Issue.objects.filter(id=issue_id).update(title=payload["title"])
        ''')
        assert _rules(bl.lint_source_file(path, table)) == {"BND002"}

    def test_a_filter_is_not_a_write(self, tmp_path):
        """A query can no more truncate than it can insert."""
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def search(request):
                payload = request.data
                return Issue.objects.filter(title=payload["title"]).first()
        ''')
        assert bl.lint_source_file(path, table) == []

    def test_a_slice_is_a_bound(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(title=payload["title"][:255])
        ''')
        assert bl.lint_source_file(path, table) == []

    def test_a_fitter_is_a_bound(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(title=bounds.fit(Issue, "title", payload["title"]))
        ''')
        assert bl.lint_source_file(path, table) == []

    def test_a_project_fitter_can_be_named(self, tmp_path):
        """An unknown wrapper is BND003, not BND002 — the ROOT of
        `f(payload["title"])` is `f`, and what the call returns is the
        inverse burden's question. Naming it as a fitter answers it."""
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(title=shorten_for_column(payload["title"]))
        ''')
        assert _rules(bl.lint_source_file(path, table)) == {"BND003"}
        assert bl.lint_source_file(
            path, table, fitters=bl.FITTERS | {"shorten_for_column"}
        ) == []

    def test_a_bound_name_carries_its_bound(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request):
                payload = request.data
                title = payload["title"][:255]
                Issue.objects.create(title=title)
        ''')
        assert bl.lint_source_file(path, table) == []

    def test_a_literal_that_fits_is_not_a_payload(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(title="scheduled check failed", service="watchdog")
        ''')
        assert bl.lint_source_file(path, table) == []

    def test_an_enum_member_is_not_a_payload(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(level=Level.ERROR, service=settings.SERVICE_NAME)
        ''')
        assert bl.lint_source_file(path, table) == []

    def test_an_unbounded_column_is_out_of_scope(self, tmp_path):
        """`normalised_trace` is a TextField: there is nothing to overflow,
        and a rule that reported it would be asking for a cut that loses
        data for no reason."""
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(normalised_trace=payload["trace"], count=payload["n"])
        ''')
        assert bl.lint_source_file(path, table) == []

    def test_a_choices_field_names_the_other_remedy(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(level=payload["level"])
        ''')
        findings = bl.lint_source_file(path, table)
        assert "choice_or_default" in findings[0].message
        assert "not in the vocabulary is not a long value" in findings[0].message

    def test_a_vocabulary_guard_is_a_bound(self, tmp_path):
        """The shape a careful endpoint uses instead of `choice_or_default`:
        stapel-gdpr's DSAR state, stapel-profiles' contact policy and
        stapel-cdn's image type all test membership one line above the write,
        and reading those as unbounded reported three findings nobody could
        act on."""
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def patch(request):
                payload = request.data
                level = payload.get("level")
                if level not in {c for c, _ in Level.choices}:
                    return error(400)
                Issue.objects.create(level=level, service=payload["service"])
        ''')
        assert _fields(bl.lint_source_file(path, table)) == {"Issue.service"}

    def test_a_null_check_is_not_a_vocabulary(self, tmp_path):
        """`if doc_key in (None, "")` asks whether anything was sent. Reading
        it as a vocabulary removed a true finding from stapel-search."""
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def on_signal(payload):
                service = payload.get("service")
                if service in (None, ""):
                    return False
                Issue.objects.create(service=service)
        ''')
        assert _rules(bl.lint_source_file(path, table)) == {"BND002"}

    def test_a_lookup_map_is_not_a_vocabulary(self, tmp_path):
        """`if key not in by_key: create(key=key)` tests whether the row
        EXISTS and is usually followed by creating it with that very key —
        the opposite of a bound."""
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def sync(payload, by_service):
                service = payload["service"]
                if service not in by_service:
                    Issue.objects.create(service=service)
        ''')
        assert _rules(bl.lint_source_file(path, table)) == {"BND002"}

    def test_noqa_on_the_field_line_suppresses(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(
                    service=payload["service"],  # noqa: BND002 - the reporter caps it
                )
        ''')
        assert bl.lint_source_file(path, table) == []


# ---------------------------------------------------------------------------
# BND003 — the inverse burden
# ---------------------------------------------------------------------------


class TestBND003:
    def test_a_helper_call_with_a_payload_in_scope_is_a_warning(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(title=norm.title_for(payload.get("trace")))
        ''')
        findings = bl.lint_source_file(path, table)
        assert _rules(findings) == {"BND003"}
        assert findings[0].level == "warning"

    def test_an_f_string_is_as_knowable_as_its_pieces(self, tmp_path):
        """`f"{exc_class}: {message}"` is two bare names, which are never
        reported; `f"{exc_class}: {describe(trace)}"` carries a call result,
        which is. The fleet sweep's `f"totp:{uuid4().hex}"` and
        `f"{namespace}/{PVC_NAME}"` are the first shape, and reading them as
        findings was five of the rule's false positives."""
        table = _table(tmp_path)
        safe = _module(tmp_path, '''
            def ingest(request):
                Issue.objects.create(title=f"{exc_class}: {message}")
        ''')
        assert bl.lint_source_file(safe, table) == []
        reported = _module(tmp_path, '''
            def ingest(request):
                Issue.objects.create(title=f"{exc_class}: {describe(trace)}")
        ''', name="other_views.py")
        assert _rules(bl.lint_source_file(reported, table)) == {"BND003"}

    def test_the_write_that_answered_500_is_reported_in_its_own_frame(self, tmp_path):
        """The positive control, and the reason BND003 does not require a
        payload in scope.

        `record()` is where the 2026-09-15 create actually lived: the view
        unpacked the payload into keyword arguments, so by the time the values
        reach here they are ordinary `str` parameters with no mark on them and
        no taint analysis can see them. The burden therefore sits on the WRITE
        — `title=norm.title_for(...)` is a call result going into a 255
        column, and nothing here says it fits.

        `fingerprint` and `service` in the same call must stay silent: a
        digest is fixed-length by construction and a bare name is never
        reported. Pinned by behaviour, never by asserting the flag's value."""
        table = _table(tmp_path)
        findings = bl.lint_source_file(_module(tmp_path, RECORD_HELPER), table)
        assert _rules(findings) == {"BND003"}
        assert _fields(findings) == {"Issue.title"}

    def test_the_repaired_helper_is_the_negative_control(self, tmp_path):
        """The same frame with `bounds.fit` around the same helper. A rule
        that reported here would be a rule nobody can satisfy."""
        table = _table(tmp_path)
        assert bl.lint_source_file(_module(tmp_path, RECORD_REPAIRED), table) == []

    def test_a_bound_on_an_ARGUMENT_is_not_a_bound_on_the_value(self, tmp_path):
        """`record()` opens with `trace = (trace or "")[:MAX_TRACE]` — 60000
        characters — and passes `trace` to the helper whose result lands in a
        255 column. Reading that slice as the title's bound is exactly how a
        rule comes to be green on the write it was written for."""
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def record(trace="", message=""):
                trace = (trace or "")[:60000]
                Issue.objects.create(title=norm.title_for(trace, message=message))
        ''')
        assert _rules(bl.lint_source_file(path, table)) == {"BND003"}

    def test_a_workspace_finds_the_same_libraries_as_naming_each_child(self, tmp_path):
        """A model-bearing tree with no pyproject.toml must be found from the
        WORKSPACE, not only when it is named on its own.

        The walk only ever appends a distribution, so such a tree was appended
        by nobody, and the "nothing found" fallback cannot rescue it once any
        sibling has matched. It cost five findings on the first fleet sweep —
        three of them the best BND003 had — in the mode the README advertises
        for exactly this job.
        """
        workspace = tmp_path / "workspace"
        # a sibling WITH a pyproject.toml, so `found` is non-empty and the
        # fallback is unreachable: that is the shape that hid the bug
        packaged = workspace / "lib-packaged"
        packaged.mkdir(parents=True)
        (packaged / "pyproject.toml").write_text(
            '[project]\nname = "lib-packaged"\n', encoding="utf-8"
        )
        (packaged / "models.py").write_text(MODELS, encoding="utf-8")

        # …and one with models and no distribution metadata at all
        loose = workspace / "svc-loose" / "app"
        loose.mkdir(parents=True)
        (loose / "models.py").write_text(MODELS, encoding="utf-8")
        (loose / "writes.py").write_text(
            "def ingest(payload):\n"
            "    Issue.objects.create(title=payload['title'])\n",
            encoding="utf-8",
        )

        from_workspace = bl.lint_project(workspace)
        from_child = bl.lint_project(workspace / "svc-loose")
        assert from_child, "the loose tree reports nothing even when named"
        missed = {(v.path, v.line, v.rule) for v in from_child} - {
            (v.path, v.line, v.rule) for v in from_workspace
        }
        assert missed == set(), f"the workspace mode missed {sorted(missed)}"

    def test_a_test_module_is_out_of_scope(self, tmp_path):
        """A suite fabricates columns by the thousand and every one of them
        was written to fit. On the day the rule shipped they were 140 of its
        196 fleet findings."""
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def test_an_issue_is_recorded(db):
                Issue.objects.create(title=describe(1), fingerprint="a" * 64)
        ''', name="test_ingest.py")
        assert bl.lint_source_file(path, table) == []

    def test_a_same_named_field_read_cannot_overflow(self, tmp_path):
        """`title=series.title` — stapel-calendar materialises an occurrence
        out of its series. A column cannot overflow the column it came from."""
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def materialise(series, occurrence_start):
                Issue.objects.create(title=series.title, service=describe(series))
        ''')
        findings = bl.lint_source_file(path, table)
        assert _fields(findings) == {"Issue.service"}

    def test_a_constant_that_folds_is_a_constant(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def seed():
                Issue.objects.create(fingerprint="a" * 64, culprit="frame/" + "x" * 8)
        ''')
        assert bl.lint_source_file(path, table) == []

    def test_a_folded_constant_that_does_not_fit_is_still_reported(self, tmp_path):
        """65 characters into a 64 column. The fold is what makes the length
        knowable, and knowing it is what makes this a finding."""
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def seed():
                Issue.objects.create(fingerprint="a" * 65)
        ''')
        assert _rules(bl.lint_source_file(path, table)) == {"BND003"}

    def test_a_bare_name_is_never_reported(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request, title):
                payload = request.data
                Issue.objects.create(title=title)
        ''')
        assert bl.lint_source_file(path, table) == []

    def test_a_digest_is_bounded_by_construction(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(
                    fingerprint=hashlib.sha256(payload["trace"].encode()).hexdigest(),
                )
        ''')
        assert bl.lint_source_file(path, table) == []

    def test_a_fitted_helper_result_is_silent(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(title=bounds.fit(Issue, "title", norm.title_for(payload)))
        ''')
        assert bl.lint_source_file(path, table) == []

    def test_noqa_suppresses(self, tmp_path):
        table = _table(tmp_path)
        path = _module(tmp_path, '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(title=norm.title_for(payload))  # noqa: BND003
        ''')
        assert bl.lint_source_file(path, table) == []


# ---------------------------------------------------------------------------
# discovery — one library, or a workspace holding many
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_a_library_root_is_its_own_library(self, tmp_path):
        lib = _library(tmp_path)
        assert bl.find_libraries(lib) == [lib.resolve()]

    def test_a_workspace_finds_every_library_under_it(self, tmp_path):
        a = _library(tmp_path, "stapel-a")
        b = _library(tmp_path, "stapel-b")
        assert bl.find_libraries(tmp_path) == sorted([a.resolve(), b.resolve()])

    def test_two_libraries_do_not_share_a_field_table(self, tmp_path):
        """Both declare an `Issue`; one bounds its title at 255 and the other
        does not bound anything. A merged table would grade one library's
        call sites against the other's columns."""
        _library(tmp_path, "stapel-a", sources={"views.py": '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(**payload)
        '''})
        _library(tmp_path, "stapel-b", models=MODELS_UNBOUNDED, sources={"views.py": '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(**payload)
        '''})
        findings = bl.lint_project(tmp_path)
        assert len(findings) == 1
        assert "stapel-a" in findings[0].path

    def test_a_models_package_is_read(self, tmp_path):
        lib = tmp_path / "stapel-thing"
        (lib / "models").mkdir(parents=True)
        (lib / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
        (lib / "models" / "issue.py").write_text(
            textwrap.dedent(MODELS).lstrip(), encoding="utf-8"
        )
        (lib / "views.py").write_text(
            "def ingest(request):\n"
            "    payload = request.data\n"
            "    Issue.objects.create(**payload)\n",
            encoding="utf-8",
        )
        assert _rules(bl.lint_library(lib)) == {"BND001"}

    def test_migrations_are_not_the_model(self, tmp_path):
        """A migration describes history. The current model is the contract,
        and a field the migrations still carry but the model has dropped is
        not a column anything writes."""
        lib = _library(tmp_path, models=MODELS_UNBOUNDED)
        migrations = lib / "migrations"
        migrations.mkdir()
        (migrations / "0001_initial.py").write_text(
            textwrap.dedent(MODELS).lstrip(), encoding="utf-8"
        )
        (lib / "views.py").write_text(
            "def ingest(request):\n"
            "    payload = request.data\n"
            "    ErrorEvent.objects.create(**payload)\n",
            encoding="utf-8",
        )
        assert bl.lint_library(lib) == []

    def test_build_and_vendor_copies_are_skipped(self, tmp_path):
        _library(tmp_path, "stapel-a")
        _library(tmp_path / "stapel-a" / "build" / "lib", "stapel_a")
        _library(tmp_path / "stapel-studio" / ".vendor", "stapel-a")
        assert len(bl.find_libraries(tmp_path)) == 1

    def test_a_tree_with_no_models_is_silent_with_a_note(self, tmp_path):
        notes: list = []
        assert bl.lint_project(tmp_path, notes=notes) == []
        assert notes == ["no models.py in this tree — bounds rules are silent"]

    def test_findings_are_sorted(self, tmp_path):
        _library(tmp_path, "stapel-b", sources={"views.py": INGEST_BROKEN})
        _library(tmp_path, "stapel-a", sources={"views.py": INGEST_BROKEN})
        findings = bl.lint_project(tmp_path)
        assert [(f.path, f.line, f.rule) for f in findings] == sorted(
            (f.path, f.line, f.rule) for f in findings
        )

    def test_a_file_that_does_not_parse_is_skipped(self, tmp_path):
        lib = _library(tmp_path, sources={"views.py": "def broken(:\n    pass\n"})
        assert bl.lint_library(lib) == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestDriver:
    def test_clean_tree_exits_zero(self, tmp_path):
        _library(tmp_path, sources={"views.py": INGEST_REPAIRED})
        assert bl.main([str(tmp_path)]) == 0

    def test_warnings_alone_exit_zero(self, tmp_path):
        _library(tmp_path, sources={"views.py": '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(title=norm.title_for(payload))
        '''})
        assert bl.main([str(tmp_path)]) == 0

    def test_strict_promotes_warnings(self, tmp_path):
        _library(tmp_path, sources={"views.py": '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(title=norm.title_for(payload))
        '''})
        assert bl.main([str(tmp_path), "--strict"]) == 1

    def test_errors_exit_one(self, tmp_path):
        _library(tmp_path, sources={"views.py": INGEST_BROKEN})
        assert bl.main([str(tmp_path)]) == 1

    def test_json_output_shape(self, tmp_path, capsys):
        _library(tmp_path, sources={"views.py": INGEST_BROKEN})
        bl.main([str(tmp_path), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["errors"] == 3
        assert {v["rule"] for v in payload["violations"]} == {"BND002", "BND003"}
        for violation in payload["violations"]:
            assert set(violation) == {"path", "line", "rule", "message", "level"}

    def test_json_output_of_a_clean_tree(self, tmp_path, capsys):
        _library(tmp_path, sources={"views.py": INGEST_REPAIRED})
        assert bl.main([str(tmp_path), "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == {
            "ok": True, "errors": 0, "violations": [],
        }

    def test_human_summary_line(self, tmp_path, capsys):
        _library(tmp_path, sources={"views.py": INGEST_BROKEN})
        bl.main([str(tmp_path)])
        assert "3 error(s), 1 warning(s) found." in capsys.readouterr().out

    def test_clean_summary_line(self, tmp_path, capsys):
        _library(tmp_path, sources={"views.py": INGEST_REPAIRED})
        bl.main([str(tmp_path)])
        assert "No violations found." in capsys.readouterr().out

    def test_the_fitter_option_reaches_the_rules(self, tmp_path):
        _library(tmp_path, sources={"views.py": '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(title=shorten_for_column(payload["title"]))
        '''})
        assert bl.main([str(tmp_path), "--strict"]) == 1
        assert bl.main(
            [str(tmp_path), "--strict", "--fitter", "shorten_for_column"]
        ) == 0

    def test_a_missing_path_is_a_usage_error(self, tmp_path):
        try:
            bl.lint_paths([str(tmp_path / "nope")])
        except SystemExit as exc:
            assert "does not exist" in str(exc)
        else:  # pragma: no cover - the call above must raise
            raise AssertionError("a missing path must not pass silently")

    def test_violation_str_marks_the_level(self, tmp_path):
        _library(tmp_path, sources={"views.py": '''
            def ingest(request):
                payload = request.data
                Issue.objects.create(title=norm.title_for(payload))
        '''})
        findings = bl.lint_project(tmp_path)
        assert "[BND003 warning]" in str(findings[0])


# ---------------------------------------------------------------------------
# wiring — the gate is only a gate if the composition runs it
# ---------------------------------------------------------------------------


class TestWiring:
    def test_composed_into_stapel_verify(self):
        assert "stapel-bounds-lint" in verify.COMPOSED_LINTERS

    def test_lives_on_the_python_surface(self):
        assert lint_profile.LINTER_SURFACES["stapel-bounds-lint"] == "python"

    def test_the_runner_reports_under_its_own_name(self, tmp_path):
        lib = _library(tmp_path, sources={"views.py": INGEST_BROKEN})
        report = verify.run_bounds_lint(lib)
        assert report.name == "stapel-bounds-lint"
        assert report.errors == 3
        assert report.warnings == 1
        assert {f["rule"] for f in report.findings} == {"BND002", "BND003"}

    def test_the_runner_carries_the_silence_note(self, tmp_path):
        report = verify.run_bounds_lint(tmp_path)
        assert report.errors == 0
        assert report.notes == ["no models.py in this tree — bounds rules are silent"]

    def test_the_console_script_is_declared(self):
        import tomllib

        root = Path(__file__).resolve().parent.parent
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        assert pyproject["project"]["scripts"]["stapel-bounds-lint"] == (
            "stapel_tools.bounds_lint:main"
        )

    def test_the_rules_are_in_the_escape_registry(self):
        from stapel_tools import escape

        for rule in ("BND001", "BND002", "BND003"):
            assert escape.RULE_REGISTRY[rule].linter == "stapel-bounds-lint"
            assert escape.RULE_REGISTRY[rule].honors_noqa is True
