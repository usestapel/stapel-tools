"""Tests for the codegen backend-artifact emitter (docs/flow-system.md §0.1).

Hermetic: configures a tiny in-process Django instance (drf_spectacular + one
DRF endpoint + one registered flow) and exercises ``stapel_tools.codegen``
against it. The real all-modules run lives in stapel-example-monolith
(``make codegen``); here we prove the *mechanism*: valid artifacts + the drift
gate's core invariant — regenerating without a change yields byte-identical
output.
"""
import json

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "rest_framework",
            "drf_spectacular",
            # carries the generate_flow_docs management command
            "stapel_core.django",
        ],
        REST_FRAMEWORK={"DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema"},
        SPECTACULAR_SETTINGS={
            "TITLE": "Probe",
            "DESCRIPTION": "codegen probe",
            "VERSION": "1.0.0",
        },
        # this module doubles as the URLConf
        ROOT_URLCONF=__name__,
        USE_TZ=True,
    )
    django.setup()

from rest_framework import serializers, views  # noqa: E402
from rest_framework.response import Response  # noqa: E402
from rest_framework.urls import path  # noqa: E402  (re-exported django path)

from stapel_core.flows import Flow  # noqa: E402
from stapel_tools import codegen  # noqa: E402


class ProbeSerializer(serializers.Serializer):
    name = serializers.CharField()
    count = serializers.IntegerField()


class ProbeView(views.APIView):
    serializer_class = ProbeSerializer

    def get(self, request):  # pragma: no cover - not executed, schema only
        return Response({"name": "x", "count": 1})


urlpatterns = [path("api/probe/", ProbeView.as_view())]

# A flow registers itself on construction (registry.py). autodiscover_flows()
# only imports <app>.flows modules, so this test-module flow survives.
PROBE_FLOW = Flow("probe.login", title="Probe login", description="a probe flow")
PROBE_FLOW.human(order=0, note="user enters email")


def test_stable_json_is_byte_stable_and_newline_terminated():
    data = {"b": 1, "a": "юникод", "nested": [3, 2, 1]}
    first = codegen._stable_json(data)
    second = codegen._stable_json(data)
    assert first == second
    assert first.endswith("\n")
    # unicode stays readable (ensure_ascii=False), key order preserved
    assert "юникод" in first
    assert json.loads(first) == data


def test_emit_schema_writes_valid_openapi(tmp_path):
    out = tmp_path / "schema.json"
    path_count = codegen.emit_schema(out)

    schema = json.loads(out.read_text())
    assert schema["openapi"].startswith("3")
    assert "/api/probe/" in schema["paths"]
    assert path_count == len(schema["paths"]) >= 1
    assert out.read_text().endswith("\n")


def test_emit_schema_is_byte_stable(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    codegen.emit_schema(a)
    codegen.emit_schema(b)
    assert a.read_bytes() == b.read_bytes()


def test_emit_flows_writes_flow_list(tmp_path):
    out = tmp_path / "flows.json"
    flow_count = codegen.emit_flows(out)

    flows = json.loads(out.read_text())
    assert isinstance(flows, list)
    assert flow_count == len(flows) >= 1
    ids = {f["id"] for f in flows}
    assert "probe.login" in ids
    assert out.read_text().endswith("\n")


def test_emit_flows_is_byte_stable(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    codegen.emit_flows(a)
    codegen.emit_flows(b)
    assert a.read_bytes() == b.read_bytes()


def test_emit_errors_writes_valid_registry(tmp_path):
    out = tmp_path / "errors.json"
    error_count = codegen.emit_errors(out)

    errors = json.loads(out.read_text())
    assert isinstance(errors, list)
    assert error_count == len(errors) >= 1
    for entry in errors:
        assert set(entry) == {"code", "status", "params", "remediation", "en"}
        assert isinstance(entry["code"], str)
        assert isinstance(entry["status"], int)
        assert isinstance(entry["params"], list)
        assert isinstance(entry["remediation"], str)
        assert isinstance(entry["en"], str)
    codes = [e["code"] for e in errors]
    assert codes == sorted(codes)
    assert out.read_text().endswith("\n")


def test_emit_errors_is_byte_stable(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    codegen.emit_errors(a)
    codegen.emit_errors(b)
    assert a.read_bytes() == b.read_bytes()


def test_generate_emits_all_three_artifacts(tmp_path):
    # django.setup() already ran at import; generate() calls it again (idempotent).
    summary = codegen.generate(tmp_path)
    assert (tmp_path / "schema.json").exists()
    assert (tmp_path / "flows.json").exists()
    assert (tmp_path / "errors.json").exists()
    assert summary["paths"] >= 1
    assert summary["flows"] >= 1
    assert summary["errors"] >= 1
