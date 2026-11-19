"""Contract profile 2 in ``stapel_tools.codegen``.

The emission runs in a subprocess against ``tests/fixtures/contract_probe`` (a
tiny Django project), so its settings never meet the ones other test modules
configure in-process. ``golden.profile1.json`` was emitted from the probe by
stapel-tools 0.80.3 on stapel-core 0.96.0 — before profile 2 existed — and
profile 1 must keep reproducing it byte for byte.

The JSON post-pass rules (discriminators, pagination, schema-less refusal)
are also tested directly on small documents, each with its inversion.
"""
import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# find_spec on the parent only: importing stapel_core.django.openapi needs
# configured Django settings, which this module must not set in-process.
_CORE_OPENAPI = importlib.util.find_spec("stapel_core.django")
if _CORE_OPENAPI is None or not (
    Path(_CORE_OPENAPI.submodule_search_locations[0]) / "openapi" / "contract.py"
).exists():
    pytest.skip("contract profile 2 needs stapel-core>=0.97.0", allow_module_level=True)

from stapel_tools import codegen  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "contract_probe" / "golden.profile1.json"

_EMIT = """
import sys
from pathlib import Path
import django
django.setup()
from stapel_tools.codegen import emit_schema
emit_schema(Path(sys.argv[1]), contract_profile=int(sys.argv[2]))
"""


def _emit(tmp_path, profile):
    out = tmp_path / f"schema.p{profile}.json"
    env = dict(os.environ)
    env["DJANGO_SETTINGS_MODULE"] = "contract_probe.settings"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(FIXTURES), str(Path(codegen.__file__).resolve().parent.parent),
         env.get("PYTHONPATH", "")]
    )
    proc = subprocess.run(
        [sys.executable, "-c", _EMIT, str(out), str(profile)],
        env=env, capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    return out


@pytest.fixture(scope="module")
def emitted(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("contract_probe")
    return {1: _emit(tmp, 1), 2: _emit(tmp, 2)}


def _ops(schema):
    return {
        (method.upper(), path): op
        for path, item in schema["paths"].items()
        for method, op in item.items()
        if isinstance(op, dict)
    }


# -- golden: profile 1 unchanged ------------------------------------------------


def test_profile1_is_byte_identical_to_the_pre_profile_golden(emitted):
    assert emitted[1].read_bytes() == GOLDEN.read_bytes()


def test_profile1_carries_no_profile_marker(emitted):
    assert "x-stapel-contract" not in json.loads(emitted[1].read_text())["info"]


# -- profile 2 end to end on the probe -------------------------------------------


def test_profile2_records_its_number(emitted):
    assert json.loads(emitted[2].read_text())["info"]["x-stapel-contract"] == 2


def test_profile2_end_to_end(emitted):
    schema = json.loads(emitted[2].read_text())
    ops = _ops(schema)
    sign_in = ops[("POST", "/auth/sign-in/")]
    account = ops[("GET", "/api/account/")]
    # (1) bearer + (2) public by the running gate
    assert sign_in["security"] == [{"JWTCookieAuth": []}, {"JWTBearerAuth": []}, {}]
    assert {} not in account["security"]
    # (3) discriminator by wire value
    assert schema["components"]["schemas"]["SignInResponse"]["discriminator"]["mapping"] == {
        "LOGGED_IN": "#/components/schemas/SignedIn",
        "REGISTERED": "#/components/schemas/SignedIn",
        "CHALLENGE": "#/components/schemas/Challenge",
    }
    # (5) codes from the handler, a service it calls, the serializer, the gate
    assert sign_in["responses"]["423"]["x-stapel-errors"] == ["error.423.probe_locked"]
    assert "error.400.probe_code" in sign_in["responses"]["400"]["x-stapel-errors"]
    assert sign_in["responses"]["403"]["x-stapel-errors"] == ["error.403.probe_closed"]
    # (6) captcha + verification + flows emitted
    assert ops[("POST", "/auth/register/")]["x-stapel-captcha"]["action"] == "register"
    assert sign_in["x-stapel-captcha"] == {"action": None, "token": {"field": "captcha_token"}}
    assert ops[("DELETE", "/api/account/")]["x-stapel-verification"]["scope"] == "probe.delete"
    assert sign_in["x-stapel-flows"] == ["probe.signin"]
    # (7) pagination named, envelope component too
    assert ops[("GET", "/api/items/")]["x-stapel-pagination"] == {
        "style": "anchor", "items": "items", "params": ["anchor", "direction", "limit"],
    }
    assert account["x-stapel-pagination"]["style"] == "page"
    assert schema["components"]["schemas"]["PaginatedItemList"]["x-stapel-pagination"] == {
        "style": "anchor", "items": "items",
    }


def test_profile1_has_none_of_it(emitted):
    text = emitted[1].read_text()
    for key in ("JWTBearerAuth", "x-stapel-errors", "x-stapel-captcha",
                "x-stapel-pagination", "x-stapel-flows", "x-stapel-verification"):
        assert key not in text


def test_profile2_is_byte_stable(tmp_path, emitted):
    again = _emit(tmp_path, 2)
    assert again.read_bytes() == emitted[2].read_bytes()


# -- the post-pass rules, directly ------------------------------------------------


def _doc(components, paths=None):
    return {"openapi": "3.0.3", "info": {}, "paths": paths or {},
            "components": {"schemas": components}}


_UNION = {
    "Union": {
        "oneOf": [{"$ref": "#/components/schemas/A"}, {"$ref": "#/components/schemas/B"}],
        "discriminator": {"propertyName": "kind", "mapping": {
            "A": "#/components/schemas/A", "None": "#/components/schemas/B"}},
    },
    "A": {"type": "object", "properties": {
        "kind": {"allOf": [{"$ref": "#/components/schemas/AKindEnum"}], "description": "x"}}},
    "AKindEnum": {"enum": ["one", "two"], "type": "string"},
    "B": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["three"]}}},
}


def test_discriminator_mapping_is_rebuilt_from_wire_values():
    doc = _doc(copy.deepcopy(_UNION))
    assert codegen.wire_discriminators(doc) == 1
    assert doc["components"]["schemas"]["Union"]["discriminator"]["mapping"] == {
        "one": "#/components/schemas/A",
        "two": "#/components/schemas/A",
        "three": "#/components/schemas/B",
    }


def test_inversion_variant_without_enum_is_refused():
    components = copy.deepcopy(_UNION)
    components["B"]["properties"]["kind"] = {"type": "string"}
    with pytest.raises(codegen.ContractProfileError, match="declares no enum"):
        codegen.wire_discriminators(_doc(components))


def test_inversion_value_claimed_twice_is_refused():
    components = copy.deepcopy(_UNION)
    components["B"]["properties"]["kind"]["enum"] = ["one"]
    with pytest.raises(codegen.ContractProfileError, match="claimed by"):
        codegen.wire_discriminators(_doc(components))


def _page_doc(properties, params=()):
    return _doc(
        {"Env": {"type": "object", "properties": properties}},
        {"/x/": {"get": {
            "parameters": [{"name": p, "in": "query"} for p in params],
            "responses": {"200": {"content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/Env"}}}}},
        }}},
    )


@pytest.mark.parametrize("properties,params,style", [
    ({"results": {"type": "array"}, "count": {"type": "integer"},
      "limit": {"type": "integer"}, "offset": {"type": "integer"}},
     ("limit", "offset"), "offset"),
    ({"results": {"type": "array"}, "pagination": {"type": "object"},
      "revisions": {"type": "object"}}, ("page",), "revision_page"),
    ({"results": {"type": "array"}, "count": {"type": "integer"},
      "next": {"type": "integer", "nullable": True}}, ("page",), "page"),
])
def test_pagination_styles(properties, params, style):
    doc = _page_doc(properties, params)
    assert codegen.name_pagination(doc) == 1
    assert doc["paths"]["/x/"]["get"]["x-stapel-pagination"]["style"] == style


def test_inversion_half_an_envelope_is_not_paginated():
    # `results` + `count` but no position field, and a string `next` (a URL)
    doc = _page_doc({"results": {"type": "array"}, "count": {"type": "integer"},
                     "next": {"type": "string"}})
    assert codegen.name_pagination(doc) == 0
    assert "x-stapel-pagination" not in doc["paths"]["/x/"]["get"]


def test_schemaless_success_is_refused():
    doc = _doc({}, {"/x/": {"post": {"operationId": "x", "responses": {
        "200": {"description": "No response body"}}}}})
    with pytest.raises(codegen.ContractProfileError, match="POST /x/ 200"):
        codegen.refuse_schemaless_success(doc)


def test_inversion_204_needs_no_schema():
    doc = _doc({}, {"/x/": {"delete": {"responses": {"204": {"description": ""}}}}})
    codegen.refuse_schemaless_success(doc)


def test_profile1_post_pass_is_the_identity():
    doc = _doc(copy.deepcopy(_UNION))
    before = copy.deepcopy(doc)
    assert codegen.apply_contract_profile(doc, 1) == before


def test_unknown_profile_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        codegen.emit_schema(tmp_path / "x.json", contract_profile=3)
