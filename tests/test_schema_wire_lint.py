"""stapel-schema-lint — SCH001-SCH003.

The fixtures are transcriptions, not inventions. The wire test is
``stapel-alerts/tests/test_contract_wire.py``; the drift gate is the shape
every library's ``tests/test_contract.py`` has; the violating view is
``stapel-alerts`` 0.2.0's ``IssueListView.get`` and the clean one is 0.2.1's,
both taken from commit ``c9ca66a``. A linter whose fixtures are written to
match the linter proves only that the author was consistent.
"""
import json
import textwrap
from pathlib import Path

from stapel_tools import lint_profile, schema_wire_lint as swl, verify

# ---------------------------------------------------------------------------
# committed documents
# ---------------------------------------------------------------------------

SCHEMA_WITH_JSON_BODY = {
    "openapi": "3.0.3",
    "paths": {
        "/alerts/api/v1/issues": {
            "get": {
                "operationId": "alerts_api_v1_issues_list",
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/IssuePage"}
                            }
                        }
                    }
                },
            }
        },
        "/alerts/api/v1/issues/{issue_id}": {
            "patch": {
                "operationId": "alerts_api_v1_issues_partial_update",
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Issue"}
                            }
                        }
                    }
                },
            }
        },
    },
    "components": {"schemas": {"Issue": {"type": "object"}}},
}

#: A 202 that carries no body and a 404 that does — neither is a 2xx JSON body,
#: so there is nothing for a wire test to prove and SCH001 has nothing to say.
SCHEMA_WITHOUT_JSON_BODY = {
    "openapi": "3.0.3",
    "paths": {
        "/alerts/api/v1/report": {
            "post": {
                "operationId": "alerts_api_v1_report_create",
                "responses": {
                    "202": {"description": ""},
                    "404": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Error"}
                            }
                        }
                    },
                },
            }
        }
    },
    "components": {"schemas": {"Error": {"type": "object"}}},
}


# ---------------------------------------------------------------------------
# test modules, transcribed from the fleet
# ---------------------------------------------------------------------------

#: stapel-alerts/tests/test_contract_wire.py — the proof. Reads the COMMITTED
#: document, enumerates its `paths`, performs every declared operation and
#: validates the received body against the schema it was promised.
WIRE_TEST = '''
"""Every response body the contract declares is a body the views actually send."""
import copy
import json
from pathlib import Path

import jsonschema
import pytest

from stapel_alerts.models import Issue
from stapel_alerts.services import record

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "docs" / "schema.json").read_text())

WRITE_RECIPES = {
    ("PATCH", "/alerts/api/v1/issues/{issue_id}"): {"note": "reconciled"},
}


def _json_schema(node):
    if isinstance(node, list):
        return [_json_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    rebuilt = {k: _json_schema(v) for k, v in node.items() if k != "nullable"}
    if node.get("nullable"):
        return {"anyOf": [rebuilt, {"type": "null"}]}
    return rebuilt


def _validator(response_schema):
    root = copy.deepcopy(response_schema)
    root["components"] = copy.deepcopy(SCHEMA["components"])
    return jsonschema.Draft202012Validator(_json_schema(root))


def _operations():
    ops = []
    for path, methods in SCHEMA["paths"].items():
        for method, op in methods.items():
            for code, response in op.get("responses", {}).items():
                body = response.get("content", {}).get("application/json", {}).get("schema")
                if body is not None and code.startswith("2"):
                    ops.append((method.upper(), path, int(code), body))
    return ops


OPERATIONS = _operations()


def test_the_contract_declares_something_to_check():
    assert OPERATIONS, "docs/schema.json declares no JSON responses at all"


@pytest.mark.parametrize("method,path,code,body_schema", OPERATIONS)
def test_the_wire_matches_the_declared_response(staff_client, method, path, code, body_schema):
    issue = Issue.objects.get()
    url = path.replace("{issue_id}", str(issue.id))
    assert "{" not in url

    if method == "GET":
        response = staff_client.get(url)
    else:
        response = getattr(staff_client, method.lower())(
            url, WRITE_RECIPES[(method, path)], format="json"
        )

    assert response.status_code == code, response.content
    body = response.json()
    errors = sorted(_validator(body_schema).iter_errors(body), key=lambda e: list(e.path))
    assert not errors, f"{method} {path} answers a body the contract does not describe"
'''

#: The shape every library's per-module contract gate has: it reads the
#: committed document and enumerates its `paths`, and it still proves nothing
#: about the wire, because it never performs a request.
DRIFT_TEST = '''
"""Per-module contract triad + drift gate."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"


def _emit(out_dir):
    subprocess.run(
        [sys.executable, "-m", "stapel_alerts._codegen", "--out", str(out_dir)],
        cwd=str(REPO), check=True, capture_output=True,
    )


def test_contract_has_no_drift(tmp_path):
    _emit(tmp_path)
    committed = (DOCS / "schema.json").read_bytes()
    assert committed == (tmp_path / "schema.json").read_bytes()


def test_paths_carry_canonical_prefix():
    schema = json.loads((DOCS / "schema.json").read_text())
    assert schema["paths"]
    assert all(p.startswith("/alerts/api/") for p in schema["paths"])
'''

#: The "green gate that covers three of four rows": real requests, real
#: jsonschema validation of the bodies — against a hand-picked list.
HANDPICKED_WIRE_TEST = '''
import json
from pathlib import Path

import jsonschema
import pytest

ISSUE_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "status": {"type": "string"}},
    "required": ["id", "status"],
}

CHECKED = [
    ("/alerts/api/v1/issues", ISSUE_SCHEMA),
]


@pytest.mark.parametrize("path,schema", CHECKED)
def test_the_body_matches(staff_client, path, schema):
    response = staff_client.get(path)
    body = response.json()
    jsonschema.validate(body, schema)
'''

#: The false positive the sweep found: a module that drives a client AND uses
#: jsonschema, but validates EVENT-BUS payloads against docs/events/*.json.
#: Half the fleet does this (stapel-auth, stapel-gdpr, stapel-billing), and it
#: is not a wire test — nothing it validates ever came off a response.
EVENT_PAYLOAD_TEST = '''
import json
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent


def _schema(name):
    return json.loads((ROOT / "docs" / "events" / name).read_text())


def test_publishing_emits_a_valid_event(staff_client, captured):
    response = staff_client.post("/alerts/api/v1/report", {"events": []}, format="json")
    assert response.status_code == 202
    payload = captured[0].payload
    jsonschema.validate(payload, _schema("issue.opened.json"))
'''


# ---------------------------------------------------------------------------
# views, transcribed from stapel-alerts c9ca66a
# ---------------------------------------------------------------------------

VIEW_0_2_0 = '''
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.views import APIView


class IssueListView(APIView):
    """``GET /alerts/api/v1/issues`` — the triage list, newest activity first."""

    permission_classes = [IsStaffUser]
    response_serializer_class = IssueSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter("status", str, description="new|fixed|regressed|muted"),
            OpenApiParameter("offset", int),
        ],
        responses=IssueSerializer(many=True),
    )
    def get(self, request):
        qs = Issue.objects.all()
        params = request.query_params

        if params.get("status"):
            qs = qs.filter(status=params["status"])

        total = qs.count()
        try:
            offset = max(0, int(params.get("offset", 0)))
        except (TypeError, ValueError):
            offset = 0
        rows = qs.order_by("-last_seen")[offset:offset + PAGE_SIZE]

        serializer_class = self.get_response_serializer_class() or IssueSerializer
        payload = {
            "count": total,
            "offset": offset,
            "limit": PAGE_SIZE,
            "results": serializer_class(rows, many=True).data,
        }
        return _conditional(request, payload)
'''

VIEW_0_2_1 = '''
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.views import APIView


class IssueListView(APIView):
    """``GET /alerts/api/v1/issues`` — the triage list, newest activity first."""

    permission_classes = [IsStaffUser]
    response_serializer_class = IssueSerializer
    page_serializer_class = IssuePageSerializer

    @extend_schema(
        operation_id="alerts_api_v1_issues_list",
        parameters=[
            OpenApiParameter("status", str, description="new|fixed|regressed|muted"),
            OpenApiParameter("offset", int, description="Rows to skip; default 0"),
            OpenApiParameter("limit", int, description="Page size, 1..200"),
        ],
        responses=IssuePageSerializer,
    )
    def get(self, request):
        qs = Issue.objects.all()
        total = qs.count()
        offset = _bounded_int(request.query_params.get("offset", 0), default=0, floor=0)
        limit = _bounded_int(
            request.query_params.get("limit", PAGE_SIZE),
            default=PAGE_SIZE, floor=1, ceiling=MAX_PAGE_SIZE,
        )
        rows = qs.order_by("-last_seen")[offset:offset + limit]

        page = self.page_serializer_class(
            {"count": total, "offset": offset, "limit": limit, "results": rows},
            row_serializer_class=self.get_response_serializer_class() or IssueSerializer,
        )
        return _conditional(request, page.data)
'''


# ---------------------------------------------------------------------------
# fixture builder
# ---------------------------------------------------------------------------


def _library(tmp_path, name="stapel-thing", *, schema=SCHEMA_WITH_JSON_BODY,
             tests=None, sources=None):
    lib = tmp_path / name
    (lib / "docs").mkdir(parents=True, exist_ok=True)
    (lib / "docs" / "schema.json").write_text(
        json.dumps(schema, indent=2), encoding="utf-8"
    )
    if tests is not None:
        (lib / "tests").mkdir(exist_ok=True)
        for fname, body in tests.items():
            (lib / "tests" / fname).write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    for fname, body in (sources or {}).items():
        (lib / fname).write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return lib


def _view(tmp_path, source, name="views.py"):
    path = tmp_path / name
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


def _rules(violations):
    return {v.rule for v in violations}


# ---------------------------------------------------------------------------
# SCH001 — a published contract nobody proves
# ---------------------------------------------------------------------------


class TestSCH001:
    def test_a_drift_gate_alone_is_not_a_proof(self, tmp_path):
        """The green gate that closes nothing: it compares the committed
        document against a fresh emission of the same annotations."""
        lib = _library(tmp_path, tests={"test_contract.py": DRIFT_TEST})
        findings = swl.lint_library(lib)
        assert _rules(findings) == {"SCH001"}
        assert findings[0].level == "warning", (
            "SCH001 stays a warning while the sweep runs — 25 of 26 libraries "
            "trip it on the day it ships"
        )
        assert "docs/schema.json" in findings[0].path
        assert "GET /alerts/api/v1/issues" in findings[0].message

    def test_a_real_wire_test_is_a_proof(self, tmp_path):
        lib = _library(tmp_path, tests={
            "test_contract.py": DRIFT_TEST,
            "test_contract_wire.py": WIRE_TEST,
        })
        assert swl.lint_library(lib) == []

    def test_the_proof_is_recognised_by_behaviour_not_by_filename(self, tmp_path):
        """The same content under an ordinary name is the same proof."""
        lib = _library(tmp_path, tests={"test_api.py": WIRE_TEST})
        assert swl.lint_library(lib) == []

    def test_a_file_named_like_the_proof_is_not_one(self, tmp_path):
        lib = _library(tmp_path, tests={
            "test_contract_wire.py": "def test_maths():\n    assert 2 + 2 == 4\n",
        })
        assert _rules(swl.lint_library(lib)) == {"SCH001"}

    def test_no_2xx_json_body_means_nothing_to_prove(self, tmp_path):
        """A 202 with no body and a 404 that has one: neither is a 2xx JSON
        body, so the rule is silent rather than demanding a test of nothing."""
        lib = _library(tmp_path, schema=SCHEMA_WITHOUT_JSON_BODY,
                       tests={"test_contract.py": DRIFT_TEST})
        assert swl.lint_library(lib) == []

    def test_no_tests_at_all_still_reports(self, tmp_path):
        lib = _library(tmp_path)
        assert _rules(swl.lint_library(lib)) == {"SCH001"}

    def test_an_unreadable_document_says_nothing(self, tmp_path):
        lib = _library(tmp_path)
        (lib / "docs" / "schema.json").write_text("{not json", encoding="utf-8")
        assert swl.lint_library(lib) == []


# ---------------------------------------------------------------------------
# SCH002 — a wire-shaped test working from a hand-picked list
# ---------------------------------------------------------------------------


class TestSCH002:
    def test_a_hardcoded_endpoint_list_is_an_error(self, tmp_path):
        lib = _library(tmp_path, tests={"test_wire.py": HANDPICKED_WIRE_TEST})
        findings = swl.lint_library(lib)
        assert _rules(findings) == {"SCH001", "SCH002"}
        sch002 = next(f for f in findings if f.rule == "SCH002")
        assert sch002.level == "error"
        assert sch002.path.endswith("test_wire.py")
        assert "never reads the committed docs/schema.json" in sch002.message

    def test_the_real_wire_test_is_not_reported(self, tmp_path):
        lib = _library(tmp_path, tests={"test_contract_wire.py": WIRE_TEST})
        assert swl.lint_library(lib) == []

    def test_event_payload_validation_is_not_a_wire_test(self, tmp_path):
        """The false positive the fleet sweep found. stapel-auth, stapel-gdpr
        and stapel-billing all validate event-bus payloads against
        docs/events/*.json in a module that also drives a client. Nothing it
        validates ever came off a response, so it is neither a wire test
        (SCH001 still fires) nor a broken one (SCH002 must not)."""
        lib = _library(tmp_path, tests={"test_events.py": EVENT_PAYLOAD_TEST})
        assert _rules(swl.lint_library(lib)) == {"SCH001"}

    def test_a_module_that_only_makes_requests_is_an_ordinary_api_test(self, tmp_path):
        lib = _library(tmp_path, tests={"test_api.py": (
            "def test_list(staff_client):\n"
            "    response = staff_client.get('/alerts/api/v1/issues')\n"
            "    assert response.status_code == 200\n"
        )})
        assert _rules(swl.lint_library(lib)) == {"SCH001"}


# ---------------------------------------------------------------------------
# SCH003 — declared array, built object
# ---------------------------------------------------------------------------


class TestSCH003:
    def test_the_alerts_0_2_0_view_is_reported(self, tmp_path):
        """The literal shape that shipped: `responses=IssueSerializer(
        many=True)` over a body that builds `{count, offset, limit,
        results}`."""
        path = _view(tmp_path, VIEW_0_2_0)
        findings = swl.lint_source_file(path)
        assert _rules(findings) == {"SCH003"}
        assert findings[0].level == "error"
        assert "responses=IssueSerializer(many=True)" in findings[0].message
        assert "'count': total" in findings[0].message

    def test_the_alerts_0_2_1_fix_is_clean(self, tmp_path):
        path = _view(tmp_path, VIEW_0_2_1)
        assert swl.lint_source_file(path) == []

    def test_a_dict_keyed_by_status_code_is_read_per_code(self, tmp_path):
        path = _view(tmp_path, '''
            class AuditView(APIView):
                @extend_schema(responses={200: AuditSerializer(many=True)})
                def get(self, request):
                    return Response({"items": [], "count": 0})
        ''')
        assert _rules(swl.lint_source_file(path)) == {"SCH003"}

    def test_a_non_2xx_array_declaration_is_out_of_scope(self, tmp_path):
        path = _view(tmp_path, '''
            class AuditView(APIView):
                @extend_schema(responses={404: ErrorSerializer(many=True)})
                def get(self, request):
                    return Response({"items": []})
        ''')
        assert swl.lint_source_file(path) == []

    def test_a_list_serializer_declaration_counts(self, tmp_path):
        path = _view(tmp_path, '''
            class AuditView(APIView):
                @extend_schema(responses=serializers.ListSerializer(child=IssueSerializer()))
                def get(self, request):
                    payload = {"results": []}
                    return _conditional(request, payload)
        ''')
        assert _rules(swl.lint_source_file(path)) == {"SCH003"}

    def test_a_keyword_dict_is_not_the_body(self, tmp_path):
        """`headers={...}` is metadata. Only POSITIONAL arguments carry the
        body, which is why the rule reads nothing else."""
        path = _view(tmp_path, '''
            class IssueListView(APIView):
                @extend_schema(responses=IssueSerializer(many=True))
                def get(self, request):
                    return Response(
                        IssueSerializer(self.rows(), many=True).data,
                        headers={"X-Total-Count": "12"},
                    )
        ''')
        assert swl.lint_source_file(path) == []

    def test_an_error_branch_envelope_is_not_the_declared_body(self, tmp_path):
        """A hand-built dict on the 403 branch says nothing about the 200 the
        annotation describes."""
        path = _view(tmp_path, '''
            class IssueListView(APIView):
                @extend_schema(responses=IssueSerializer(many=True))
                def get(self, request):
                    if not request.user.is_staff:
                        return StapelErrorResponse(403, ERR_403_FORBIDDEN, {"detail": "no"})
                    if request.query_params.get("bad"):
                        return Response({"detail": "bad"}, status=http_status.HTTP_400_BAD_REQUEST)
                    return Response(IssueSerializer(self.rows(), many=True).data)
        ''')
        assert swl.lint_source_file(path) == []

    def test_a_paginated_view_declares_an_envelope_not_an_array(self, tmp_path):
        """stapel-workspaces' audit endpoint, verbatim in shape. drf-spectacular
        wraps `many=True` in the paginator's envelope before it reaches the
        document (`PaginatedAuditEventResponseList`), so the hand-assembled
        envelope in the body is the MATCHING body, not a contradiction."""
        path = _view(tmp_path, '''
            class WorkspaceAuditView(APIView):
                pagination_class = AuditPagination
                response_serializer_class = AuditEventResponseSerializer

                @extend_schema(responses={200: AuditEventResponseSerializer(many=True)})
                def get(self, request, workspace_id):
                    items = self.get_response_serializer_class()(dtos, many=True).data
                    return Response(
                        {
                            "items": items,
                            "next_anchor": page.next_anchor if page else None,
                            "has_next": page.has_next if page else False,
                            "count": len(items),
                        }
                    )
        ''')
        assert swl.lint_source_file(path) == []

    def test_pagination_declared_on_a_base_class_in_the_same_module(self, tmp_path):
        path = _view(tmp_path, '''
            class _PagedView(APIView):
                pagination_class = AuditPagination

            class WorkspaceAuditView(_PagedView):
                @extend_schema(responses={200: AuditEventResponseSerializer(many=True)})
                def get(self, request):
                    return Response({"items": [], "count": 0})
        ''')
        assert swl.lint_source_file(path) == []

    def test_pagination_class_none_does_not_exempt(self, tmp_path):
        path = _view(tmp_path, '''
            class IssueListView(APIView):
                pagination_class = None

                @extend_schema(responses=IssueSerializer(many=True))
                def get(self, request):
                    return Response({"results": []})
        ''')
        assert _rules(swl.lint_source_file(path)) == {"SCH003"}

    def test_a_nested_helpers_return_is_not_the_methods(self, tmp_path):
        path = _view(tmp_path, '''
            class IssueListView(APIView):
                @extend_schema(responses=IssueSerializer(many=True))
                def get(self, request):
                    def _debug():
                        return Response({"trace": "x"})

                    return Response(IssueSerializer(self.rows(), many=True).data)
        ''')
        assert swl.lint_source_file(path) == []

    def test_noqa_on_the_decorator_suppresses(self, tmp_path):
        path = _view(tmp_path, '''
            class IssueListView(APIView):
                @extend_schema(responses=IssueSerializer(many=True))  # noqa: SCH003
                def get(self, request):
                    return Response({"results": []})
        ''')
        assert swl.lint_source_file(path) == []

    def test_an_object_declaration_over_a_built_object_says_nothing(self, tmp_path):
        """The boundary: field-level truth needs the serializer, which needs
        the application. SCH003 decides types, never fields."""
        path = _view(tmp_path, '''
            class IssueDetailView(APIView):
                @extend_schema(responses=IssueDetailSerializer)
                def get(self, request, issue_id):
                    payload = {"id": str(issue_id), "events": []}
                    return _conditional(request, payload)
        ''')
        assert swl.lint_source_file(path) == []

    def test_a_file_that_does_not_parse_is_skipped(self, tmp_path):
        path = _view(tmp_path, "def broken(:\n    pass\n")
        assert swl.lint_source_file(path) == []


# ---------------------------------------------------------------------------
# discovery — one library, or a workspace holding many
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_a_library_root_is_its_own_library(self, tmp_path):
        lib = _library(tmp_path)
        assert swl.find_libraries(lib) == [lib.resolve()]

    def test_a_workspace_finds_every_library_under_it(self, tmp_path):
        a = _library(tmp_path, "stapel-a")
        b = _library(tmp_path, "stapel-b")
        assert swl.find_libraries(tmp_path) == sorted([a.resolve(), b.resolve()])

    def test_build_and_vendor_copies_are_skipped(self, tmp_path):
        """A `build/lib/...` copy and a `.vendor/` checkout are the same
        library twice, and would double every finding in a workspace sweep."""
        _library(tmp_path, "stapel-a")
        _library(tmp_path / "stapel-a" / "build" / "lib", "stapel_a")
        _library(tmp_path / "stapel-studio" / ".vendor", "stapel-a")
        assert len(swl.find_libraries(tmp_path)) == 1

    def test_a_workspace_reports_every_library(self, tmp_path):
        _library(tmp_path, "stapel-a")
        _library(tmp_path, "stapel-b", tests={"test_contract_wire.py": WIRE_TEST})
        findings = swl.lint_project(tmp_path)
        assert _rules(findings) == {"SCH001"}
        assert len(findings) == 1
        assert "stapel-a" in findings[0].path

    def test_a_tree_with_no_schema_is_silent_with_a_note(self, tmp_path):
        notes: list = []
        assert swl.lint_project(tmp_path, notes=notes) == []
        assert notes == [
            "no docs/schema.json in this tree — schema-wire rules are silent"
        ]

    def test_findings_are_sorted(self, tmp_path):
        _library(tmp_path, "stapel-b")
        _library(tmp_path, "stapel-a", tests={"test_wire.py": HANDPICKED_WIRE_TEST})
        findings = swl.lint_project(tmp_path)
        assert [(f.path, f.line, f.rule) for f in findings] == sorted(
            (f.path, f.line, f.rule) for f in findings
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestDriver:
    def test_clean_tree_exits_zero(self, tmp_path):
        _library(tmp_path, tests={"test_contract_wire.py": WIRE_TEST})
        assert swl.main([str(tmp_path)]) == 0

    def test_warnings_alone_exit_zero(self, tmp_path):
        _library(tmp_path)
        assert swl.main([str(tmp_path)]) == 0

    def test_strict_promotes_warnings(self, tmp_path):
        """How a library that HAS written its wire test keeps SCH001 closed
        before the default level flips."""
        _library(tmp_path)
        assert swl.main([str(tmp_path), "--strict"]) == 1

    def test_errors_exit_one(self, tmp_path):
        _library(tmp_path, tests={"test_wire.py": HANDPICKED_WIRE_TEST})
        assert swl.main([str(tmp_path)]) == 1

    def test_json_output_shape(self, tmp_path, capsys):
        _library(tmp_path, tests={"test_wire.py": HANDPICKED_WIRE_TEST})
        swl.main([str(tmp_path), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["errors"] == 1
        assert {v["rule"] for v in payload["violations"]} == {"SCH001", "SCH002"}
        for violation in payload["violations"]:
            assert set(violation) == {"path", "line", "rule", "message", "level"}

    def test_json_output_of_a_clean_tree(self, tmp_path, capsys):
        _library(tmp_path, tests={"test_contract_wire.py": WIRE_TEST})
        assert swl.main([str(tmp_path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload == {"ok": True, "errors": 0, "violations": []}

    def test_human_summary_line(self, tmp_path, capsys):
        _library(tmp_path, tests={"test_wire.py": HANDPICKED_WIRE_TEST})
        swl.main([str(tmp_path)])
        assert "1 error(s), 1 warning(s) found." in capsys.readouterr().out

    def test_clean_summary_line(self, tmp_path, capsys):
        _library(tmp_path, tests={"test_contract_wire.py": WIRE_TEST})
        swl.main([str(tmp_path)])
        assert "No violations found." in capsys.readouterr().out

    def test_a_missing_path_is_a_usage_error(self, tmp_path):
        try:
            swl.lint_paths([str(tmp_path / "nope")])
        except SystemExit as exc:
            assert "does not exist" in str(exc)
        else:  # pragma: no cover - the call above must raise
            raise AssertionError("a missing path must not pass silently")

    def test_violation_str_marks_the_level(self, tmp_path):
        _library(tmp_path)
        findings = swl.lint_project(tmp_path)
        assert "[SCH001 warning]" in str(findings[0])


# ---------------------------------------------------------------------------
# wiring — the gate is only a gate if the composition runs it
# ---------------------------------------------------------------------------


class TestWiring:
    def test_composed_into_stapel_verify(self):
        assert "stapel-schema-lint" in verify.COMPOSED_LINTERS

    def test_lives_on_the_python_surface(self):
        """A library-level contract gate, beside api-lint and surface-lint —
        not `deploy`, which is for deployment-class files (nginx, Dockerfiles,
        environment addresses) rather than a library's published API."""
        assert lint_profile.LINTER_SURFACES["stapel-schema-lint"] == "python"

    def test_the_runner_reports_under_its_own_name(self, tmp_path):
        lib = _library(tmp_path, tests={"test_wire.py": HANDPICKED_WIRE_TEST})
        report = verify.run_schema_wire_lint(lib)
        assert report.name == "stapel-schema-lint"
        assert report.errors == 1
        assert report.warnings == 1
        assert {f["rule"] for f in report.findings} == {"SCH001", "SCH002"}

    def test_the_runner_carries_the_silence_note(self, tmp_path):
        report = verify.run_schema_wire_lint(tmp_path)
        assert report.errors == 0
        assert report.notes == [
            "no docs/schema.json in this tree — schema-wire rules are silent"
        ]

    def test_the_console_script_is_declared(self):
        import tomllib

        root = Path(__file__).resolve().parent.parent
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        assert pyproject["project"]["scripts"]["stapel-schema-lint"] == (
            "stapel_tools.schema_wire_lint:main"
        )

    def test_the_rules_are_in_the_escape_registry(self):
        from stapel_tools import escape

        for rule in ("SCH001", "SCH002", "SCH003"):
            assert escape.RULE_REGISTRY[rule].linter == "stapel-schema-lint"
        assert escape.RULE_REGISTRY["SCH003"].honors_noqa is True
        assert escape.RULE_REGISTRY["SCH001"].honors_noqa is False
