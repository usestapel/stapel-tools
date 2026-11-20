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
