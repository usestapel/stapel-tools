"""stapel_tools.contract_gates: the example and dead-declaration gates bricks call."""
import json
import subprocess
import sys
import textwrap

from stapel_tools.contract_gates import examples, invalid_examples

SCHEMA = {
    "openapi": "3.0.3",
    "paths": {
        "/x/": {
            "get": {
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Thing"},
                                "examples": {"Thing": {"value": {"id": 1, "on": False, "note": None}}},
                            }
                        }
                    }
                },
            },
            "post": {
                "requestBody": {"content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Thing"},
                    "example": {"id": 2, "on": True},
                }}},
                "responses": {"204": {"description": ""}},
            },
        }
    },
    "components": {"schemas": {"Thing": {
        "type": "object",
        "required": ["id", "on"],
        "properties": {
            "id": {"type": "integer"},
            "on": {"type": "boolean"},
            "note": {"type": "string", "nullable": True},
        },
    }}},
}


def test_valid_examples_pass_and_are_counted():
    assert [k for k, _s, _v in examples(SCHEMA)] == [
        ("get", "/x/", "200", "Thing"), ("post", "/x/", "request", "example"),
    ]
    assert invalid_examples(SCHEMA) == ({}, 2)


def test_inversion_a_missing_required_field_is_caught():
    schema = json.loads(json.dumps(SCHEMA))
    del schema["paths"]["/x/"]["get"]["responses"]["200"]["content"]["application/json"]["examples"]["Thing"]["value"]["on"]
    bad, checked = invalid_examples(schema)
    assert checked == 2
    assert list(bad) == [("get", "/x/", "200", "Thing")]
    assert "'on' is a required property" in bad[("get", "/x/", "200", "Thing")][0]


def test_inversion_a_string_for_a_boolean_is_caught():
    schema = json.loads(json.dumps(SCHEMA))
    schema["paths"]["/x/"]["post"]["requestBody"]["content"]["application/json"]["example"]["on"] = "True"
    bad, _ = invalid_examples(schema)
    assert list(bad) == [("post", "/x/", "request", "example")]


def test_nullable_is_read_as_a_null_branch():
    schema = json.loads(json.dumps(SCHEMA))
    schema["components"]["schemas"]["Thing"]["properties"]["note"]["nullable"] = False
    bad, _ = invalid_examples(schema)
    assert list(bad) == [("get", "/x/", "200", "Thing")]


_DEAD_PROBE = textwrap.dedent('''
    import json
    from django.conf import settings
    settings.configure(ROOT_URLCONF=__name__, INSTALLED_APPS=["rest_framework"],
                       REST_FRAMEWORK={"DEFAULT_AUTHENTICATION_CLASSES": []})
    import django
    django.setup()
    from django.urls import path
    from rest_framework import permissions, viewsets
    from rest_framework.decorators import action
    from rest_framework.response import Response

    class Things(viewsets.ViewSet):
        permission_classes = [permissions.AllowAny]

        @action(detail=False, permission_classes=[permissions.IsAuthenticated])
        def mine(self, request):
            return Response({})

        @action(detail=False, permission_classes=[permissions.IsAuthenticated])
        def guarded(self, request):
            return Response({})

        def get_permissions(self):
            if self.action == "guarded":
                return [permissions.IsAuthenticated()]
            return super().get_permissions()

        @action(detail=False)
        def plain(self, request):
            return Response({})

    urlpatterns = [
        path("mine/", Things.as_view({"get": "mine"})),
        path("guarded/", Things.as_view({"get": "guarded"})),
        path("plain/", Things.as_view({"get": "plain"})),
    ]

    from stapel_tools.contract_gates import dead_action_declarations, viewset_routes
    print(json.dumps({
        "routes": sorted(r for r, *_ in viewset_routes()),
        "dead": dead_action_declarations(),
    }))
''')


def test_a_declaration_its_mount_ignores_is_dead_and_a_honoured_one_is_not():
    out = subprocess.run([sys.executable, "-c", _DEAD_PROBE], capture_output=True, text=True, check=True)
    result = json.loads(out.stdout)
    assert result["routes"] == ["guarded/", "mine/", "plain/"]
    assert result["dead"] == [["mine/", "Things.mine", "permission_classes"]]


def _with_read_only_id():
    schema = json.loads(json.dumps(SCHEMA))
    schema["components"]["schemas"]["Thing"]["properties"]["id"]["readOnly"] = True
    return schema


def test_a_read_only_field_in_a_request_example_is_caught():
    bad, checked = invalid_examples(_with_read_only_id())
    assert checked == 2
    assert list(bad) == [("post", "/x/", "request", "example")]
    assert bad[("post", "/x/", "request", "example")] == ["['id']: a readOnly field has no place in a request"]


def test_a_read_only_field_is_neither_required_nor_allowed_in_a_request():
    schema = _with_read_only_id()
    del schema["paths"]["/x/"]["post"]["requestBody"]["content"]["application/json"]["example"]["id"]
    assert invalid_examples(schema) == ({}, 2)


def test_a_write_only_field_in_a_response_example_is_caught():
    schema = json.loads(json.dumps(SCHEMA))
    schema["components"]["schemas"]["Thing"]["properties"]["note"]["writeOnly"] = True
    schema["paths"]["/x/"]["get"]["responses"]["200"]["content"]["application/json"]["examples"]["Thing"]["value"]["note"] = "x"
    bad, _ = invalid_examples(schema)
    assert list(bad) == [("get", "/x/", "200", "Thing")]


def test_response_errors_reads_the_declared_status():
    from stapel_tools.contract_gates import response_errors

    assert response_errors(SCHEMA, "GET", "/x/", 200, {"id": 1, "on": True}) == []
    assert response_errors(SCHEMA, "get", "/x/", 200, {"id": "1", "on": True}) == [
        "at [0]: '1' is not of type 'integer'".replace("[0]", "['id']")
    ]
    assert response_errors(SCHEMA, "GET", "/x/", 200, {"id": 1})[0].endswith("'on' is a required property")
    assert response_errors(SCHEMA, "GET", "/x/", 201, {})[0].startswith("status 201 is not declared")
    assert response_errors(SCHEMA, "POST", "/x/", 204, None) == []


def test_operation_template_prefers_the_literal_segment():
    from stapel_tools.contract_gates import operation_template

    schema = {"paths": {"/p/{id}": {"get": {}}, "/p/me": {"get": {}}, "/p/{id}/x": {"post": {}}}}
    assert operation_template(schema, "GET", "/p/me") == "/p/me"
    assert operation_template(schema, "GET", "/p/42") == "/p/{id}"
    assert operation_template(schema, "POST", "/p/42/x") == "/p/{id}/x"
    assert operation_template(schema, "GET", "/p/42/x") is None


_REPLAY_PROBE = textwrap.dedent('''
    import json
    from django.conf import settings
    settings.configure(
        ROOT_URLCONF=__name__,
        INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes", "rest_framework"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        # Basic answers 401 with a challenge (Session would answer 403).
        REST_FRAMEWORK={"DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.BasicAuthentication"]},
    )
    import django
    django.setup()
    from django.urls import path
    from rest_framework.decorators import api_view, permission_classes
    from rest_framework.permissions import AllowAny, IsAuthenticated
    from rest_framework.response import Response
    from rest_framework.test import APIClient

    @api_view(["GET"])
    @permission_classes([AllowAny])
    def card(request, pk):
        body = {"id": int(pk), "name": "n"}
        if request.user.is_authenticated:
            body["count"] = 3          # members only: the defect class
        return Response(body)

    @api_view(["GET"])
    @permission_classes([IsAuthenticated])
    def mine(request):
        return Response({"id": 1, "name": "me", "count": 0})

    @api_view(["GET"])
    @permission_classes([IsAuthenticated])
    def gated(request):
        return Response({"id": 1, "name": "x", "count": 1})

    @api_view(["GET"])
    @permission_classes([AllowAny])
    def token(request):
        # Open at DRF; the body accepts a session or a single-purpose header.
        if request.user.is_authenticated or request.headers.get("X-Probe-Token"):
            return Response({"id": 1, "name": "t", "count": 1})
        return Response({"detail": "no"}, status=401)

    urlpatterns = [path("c/<int:pk>", card), path("c/mine", mine), path("g", gated), path("t", token)]

    obj = {"type": "object", "required": ["id", "name", "count"], "properties": {
        "id": {"type": "integer"}, "name": {"type": "string"}, "count": {"type": "integer"}}}
    ok = {"200": {"content": {"application/json": {"schema": obj}}}}
    schema = {"paths": {
        "/c/{pk}": {"get": {"security": [{"jwt": []}, {}], "responses": ok}},
        "/c/mine": {"get": {"security": [{"jwt": []}], "responses": {**ok, "401": {}}}},
        "/g": {"get": {"security": [{"jwt": []}, {}], "responses": ok}},
        "/t": {"get": {"security": [{"jwt": []}, {"tok": []}], "responses": {**ok, "401": {}}}},
    }, "components": {"securitySchemes": {
        "tok": {"type": "apiKey", "in": "header", "name": "X-Probe-Token"}}}}

    from django.contrib.auth.models import User
    from stapel_tools.contract_gates import AuthStateReplay

    def member():
        client = APIClient()
        client.force_authenticate(User(id=7, username="m"))
        return client

    me = member()
    result = {}
    for name, url, template in [("card", "/c/5", "/c/{pk}"), ("mine", "/c/mine", "/c/mine"),
                                ("gated", "/g", "/g"), ("other", "/c/mine", "/c/{pk}")]:
        replay = AuthStateReplay({"doc": schema}, {"anonymous": APIClient, "member": member})
        with replay.watching("GET", template):
            original = me.get(url)
        result[name] = {"status": original.status_code, "replayed": replay.replayed,
                        "findings": [str(f) for f in replay.findings]}
    replay = AuthStateReplay({"doc": schema}, {"anonymous": APIClient, "member": member})
    with replay.watching("GET", "/t"):
        original = APIClient().get("/t", HTTP_X_PROBE_TOKEN="t0k")
    result["token"] = {"status": original.status_code, "replayed": replay.replayed,
                       "findings": [str(f) for f in replay.findings]}
    print(json.dumps(result))
''')


def test_replay_finds_what_a_caller_without_a_session_is_answered():
    out = subprocess.run([sys.executable, "-c", _REPLAY_PROBE], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-2000:]
    result = json.loads(out.stdout)
    # An open read that omits a required key for a caller with no session.
    assert result["card"]["replayed"] == 2
    assert result["card"]["findings"] == [
        "GET /c/{pk} as anonymous: 200 — doc: at <root>: 'count' is a required property"
    ]
    # A members-only read refuses the anonymous replay with a declared 401: clean.
    assert result["mine"] == {"status": 200, "replayed": 2, "findings": []}
    # Declared open to callers without a session, refuses them 401.
    assert result["gated"]["findings"] == [
        "GET /g as anonymous: 401 — the operation is declared open to callers without a session, and refuses them 401"
    ]
    # A request to a DIFFERENT operation is not replayed: the gate saw no subject.
    assert result["other"]["replayed"] == 0
    # A credential an apiKey scheme names is dropped from the replay like a
    # cookie: the anonymous caller holds nothing and gets the declared 401.
    assert result["token"] == {"status": 200, "replayed": 2, "findings": []}


def test_an_undiscriminated_union_is_an_alternation_and_a_discriminated_one_is_not():
    from stapel_tools.contract_gates import response_errors

    def doc(union):
        return {
            "paths": {"/u": {"get": {"responses": {"200": {"content": {"application/json": {"schema": union}}}}}}},
            "components": {"schemas": {
                "A": {"type": "object", "properties": {"t": {"type": "string"}}},
                "B": {"type": "object", "properties": {"t": {"type": "string"}, "linked": {"type": "boolean"}}},
            }},
        }

    refs = [{"$ref": "#/components/schemas/A"}, {"$ref": "#/components/schemas/B"}]
    assert response_errors(doc({"oneOf": refs}), "GET", "/u", 200, {"t": "x", "linked": True}) == []
    strict = doc({"oneOf": refs, "discriminator": {"propertyName": "t"}})
    assert response_errors(strict, "GET", "/u", 200, {"t": "x", "linked": True}) != []
