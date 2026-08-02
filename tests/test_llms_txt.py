"""Tests for ``stapel-llms-txt`` — the generated ``docs/llms.txt``.

Hermetic: a throwaway module repo on ``tmp_path`` carrying the four contract
documents. What is asserted is the three properties the artifact is worthless
without, each of which has already failed in the field at least once:

* the file is DETERMINISTIC, so the drift gate can compare bytes;
* the budget FAILS instead of truncating (a cut context file reads exactly
  like a complete one — that is how two silent truncations survived review);
* a module with nothing to say gets an ERROR, not an empty file (an empty
  llms.txt answers "does the fleet have a mechanism for X?" with a confident
  no).
"""
import json

import pytest

from stapel_tools.llms_txt import (
    DEFAULT_TOKEN_BUDGET,
    EmitError,
    approx_tokens,
    common_prefix,
    error_codes,
    load_inputs,
    main,
    operations,
    render,
)

CAPABILITIES = {
    "module": "stapel-demo",
    "version": "1.2.3",
    "provides": "A demo module.",
    "axes": [
        {
            "key": "DEMO_B",
            "kind": "bool",
            "default": True,
            "group": "demo",
            "gates": {"operations": ["demo_list"], "co_gates": ["DEMO_A"]},
            "curated": {"summary": "Second axis.", "business_label": "B label"},
        },
        {
            "key": "DEMO_A",
            "kind": "enum",
            "default": "x",
            "group": "demo",
            "gates": {"operations": [], "co_gates": []},
            "curated": {"summary": "First axis.", "business_label": "A label"},
        },
    ],
    "extension_points": [
        {"name": "DEMO_SEAM", "kind": "dotted_path", "summary": "Replaceable."},
    ],
    "surface": [
        {
            "name": "is_ready",
            "kind": "predicate",
            "path": "stapel_demo.api.is_ready",
            "intent": "Ask this instead of poking the model.",
        },
        {
            "name": "IsDemoUser",
            "kind": "permission_class",
            "path": "stapel_demo.api.permissions.IsDemoUser",
            "intent": "The gate for demo endpoints.",
            "instead_of": ["rest_framework.permissions.IsAuthenticated"],
            "consumer": "frontend",
        },
    ],
    "requires": [
        {"module": "stapel-core", "optional": False, "reason": "comm bus"},
    ],
}

SCHEMA = {
    "openapi": "3.0.3",
    "paths": {
        "/demo/api/v1/things/": {
            "get": {"operationId": "demo_list", "tags": ["Things"]},
            "post": {"operationId": "demo_create", "tags": ["Things"]},
        },
        "/demo/api/v1/things/{id}/": {
            "delete": {"operationId": "demo_destroy", "tags": ["Things"]},
        },
        "/demo/api/v1/health": {
            "get": {"operationId": "demo_health", "tags": ["Health"]},
        },
    },
}

ERRORS = [
    {
        "code": "error.400.field.blank",
        "status": 400,
        "params": ["field"],
        "remediation": "fix_input",
        "en": "{field} may not be blank",
        "ru": "{field} не может быть пустым",
    },
    {
        "code": "error.404.not_found",
        "status": 404,
        "params": [],
        "remediation": "go_back",
        "en": "Not found",
    },
]

FLOWS = [
    {"id": "demo.second", "title": "Second flow", "steps": []},
    {"id": "demo.first", "title": "First flow", "steps": []},
]


def make_repo(tmp_path, *, capabilities=CAPABILITIES, schema=SCHEMA, errors=ERRORS,
               flows=FLOWS):
    repo = tmp_path / "stapel-demo"
    docs = repo / "docs"
    docs.mkdir(parents=True)
    if capabilities is not None:
        (docs / "capabilities.json").write_text(json.dumps(capabilities))
    if schema is not None:
        (docs / "schema.json").write_text(json.dumps(schema))
    if errors is not None:
        (docs / "errors.json").write_text(json.dumps(errors))
    if flows is not None:
        (docs / "flows.json").write_text(json.dumps(flows))
    return repo


# ── the render ───────────────────────────────────────────────────────────────

def test_sections_appear_in_the_canonical_order(tmp_path):
    text = render(load_inputs(make_repo(tmp_path)))
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert [h.split(" —")[0].split(" (")[0] for h in headings] == [
        "## Configuration axes",
        "## Usage surface",
        "## Extension points",
        "## Fits with",
        "## HTTP operations",
        "## Error codes",
        "## Documented flows",
    ]
    assert text.startswith("# stapel-demo 1.2.3\n")


def test_usage_surface_carries_path_intent_instead_of_and_consumer(tmp_path):
    """The section the artifact exists for: not just that a symbol exists, but
    what it displaces — that is the line that stops a reinvention."""
    text = render(load_inputs(make_repo(tmp_path)))
    assert "- IsDemoUser — stapel_demo.api.permissions.IsDemoUser" in text
    assert "  instead of: rest_framework.permissions.IsAuthenticated" in text
    assert "  consumer: frontend" in text
    assert "  The gate for demo endpoints." in text
    # Grouped by the closed kind vocabulary, in KINDS order — permission_class
    # before predicate, whatever order the source document happened to list.
    assert text.index("### permission_class") < text.index("### predicate")


def test_axes_render_default_business_label_and_co_gates(tmp_path):
    text = render(load_inputs(make_repo(tmp_path)))
    assert "- DEMO_A [enum, default \"x\"] — A label" in text
    assert "- DEMO_B [bool, default true] — B label" in text
    assert "  gates 1 operation(s); kept mounted by DEMO_A" in text
    # sorted by key, not by document order
    assert text.index("DEMO_A [enum") < text.index("DEMO_B [bool")


def test_operations_factor_out_the_common_mount_prefix(tmp_path):
    text = render(load_inputs(make_repo(tmp_path)))
    assert "Paths are relative to `/demo/api/v1/`." in text
    assert "- GET /things/ — demo_list" in text
    assert "- DELETE /things/{id}/ — demo_destroy" in text
    assert "- GET /health — demo_health" in text
    # grouped by tag, tags in a fixed order
    assert text.index("### Health") < text.index("### Things")


def test_errors_drop_localized_text_but_keep_the_slots(tmp_path):
    text = render(load_inputs(make_repo(tmp_path)))
    assert "- error.400.field.blank [400] fix_input {field}" in text
    assert "- error.404.not_found [404] go_back" in text
    assert "may not be blank" not in text  # localized prose stays in errors.<lang>.md
    assert "не может быть пустым" not in text


def test_flows_are_an_index_not_a_transcript(tmp_path):
    text = render(load_inputs(make_repo(tmp_path)))
    assert "- demo.first — First flow" in text
    assert text.index("demo.first") < text.index("demo.second")


def test_module_without_http_surface_simply_has_no_such_sections(tmp_path):
    """stapel-core's shape: a usage surface and seams, no OpenAPI at all."""
    repo = make_repo(tmp_path, schema=None, errors=None, flows=None)
    text = render(load_inputs(repo))
    assert "## Usage surface" in text
    assert "## HTTP operations" not in text
    assert "## Error codes" not in text
    assert "## Documented flows" not in text


# ── determinism ──────────────────────────────────────────────────────────────

def test_render_is_byte_stable_across_runs_and_source_ordering(tmp_path):
    """A drift gate that compares bytes cannot tolerate any incidental order."""
    first = render(load_inputs(make_repo(tmp_path)))

    shuffled = json.loads(json.dumps(CAPABILITIES))
    shuffled["axes"].reverse()
    shuffled["surface"].reverse()
    other = make_repo(tmp_path / "other", capabilities=shuffled)
    second = render(load_inputs(other))

    assert first == second
    assert first == render(load_inputs(make_repo(tmp_path / "third")))


# ── the budget: fail, never truncate ─────────────────────────────────────────

def test_over_budget_raises_and_names_the_expensive_sections(tmp_path):
    fat = json.loads(json.dumps(CAPABILITIES))
    fat["surface"] = [
        {
            "name": f"symbol_{i:03d}",
            "kind": "factory",
            "path": f"stapel_demo.things.symbol_{i:03d}",
            "intent": "A long curated intent line. " * 20,
        }
        for i in range(60)
    ]
    with pytest.raises(EmitError) as excinfo:
        render(load_inputs(make_repo(tmp_path, capabilities=fat)))
    message = str(excinfo.value)
    assert "over the 4000-token budget" in message
    assert "per-section cost:" in message
    assert "surface" in message
    assert "surface -> axes -> extension_points" in message


def test_over_budget_writes_nothing_at_all(tmp_path, capsys):
    """The whole point: no partial file. A truncated llms.txt is worse than
    none, because at the point of use it is indistinguishable from a full one."""
    fat = json.loads(json.dumps(CAPABILITIES))
    fat["surface"] = [
        {"name": f"s{i}", "kind": "factory", "path": f"p{i}", "intent": "x " * 400}
        for i in range(40)
    ]
    repo = make_repo(tmp_path, capabilities=fat)
    assert main([str(repo)]) == 1
    assert not (repo / "docs" / "llms.txt").exists()
    assert "over the 4000-token budget" in capsys.readouterr().err


def test_budget_ceiling_is_raised_only_deliberately(tmp_path):
    fat = json.loads(json.dumps(CAPABILITIES))
    fat["surface"] = [
        {"name": f"s{i}", "kind": "factory", "path": f"p{i}", "intent": "x " * 200}
        for i in range(40)
    ]
    repo = make_repo(tmp_path, capabilities=fat)
    assert main([str(repo)]) == 1
    assert main([str(repo), "--budget", "20000"]) == 0
    assert (repo / "docs" / "llms.txt").is_file()


def test_a_fitting_module_reports_its_cost_against_the_budget(tmp_path, capsys):
    repo = make_repo(tmp_path)
    assert main([str(repo)]) == 0
    text = (repo / "docs" / "llms.txt").read_text()
    assert approx_tokens(text) <= DEFAULT_TOKEN_BUDGET
    assert f"/{DEFAULT_TOKEN_BUDGET} tokens" in capsys.readouterr().err


# ── nothing to say → loud, never an empty file ───────────────────────────────

def test_missing_capabilities_is_an_error_naming_the_module(tmp_path):
    repo = make_repo(tmp_path, capabilities=None)
    with pytest.raises(EmitError) as excinfo:
        load_inputs(repo)
    assert "has no contract document" in str(excinfo.value)


def test_missing_capabilities_via_cli_fails_and_leaves_no_file(tmp_path, capsys):
    repo = make_repo(tmp_path, capabilities=None)
    assert main([str(repo)]) == 1
    assert not (repo / "docs" / "llms.txt").exists()
    err = capsys.readouterr().err
    assert "no contract document" in err
    assert "--skip-missing" in err


def test_skip_missing_is_a_loud_no_op_for_fleet_loops(tmp_path, capsys):
    repo = make_repo(tmp_path, capabilities=None)
    assert main([str(repo), "--skip-missing"]) == 0
    assert not (repo / "docs" / "llms.txt").exists()
    assert "skipping stapel-demo" in capsys.readouterr().err


# ── the drift gate ───────────────────────────────────────────────────────────

def test_check_is_green_right_after_emission(tmp_path, capsys):
    repo = make_repo(tmp_path)
    assert main([str(repo)]) == 0
    assert main([str(repo), "--check"]) == 0
    assert "up to date" in capsys.readouterr().err


def test_check_reddens_when_the_source_moves_under_it(tmp_path, capsys):
    repo = make_repo(tmp_path)
    assert main([str(repo)]) == 0
    capabilities = repo / "docs" / "capabilities.json"
    moved = json.loads(capabilities.read_text())
    moved["surface"][1]["intent"] = "A different intent line."
    capabilities.write_text(json.dumps(moved))

    assert main([str(repo), "--check"]) == 1
    assert "DRIFT" in capsys.readouterr().err
    # ...and the gate goes green again once the artifact is re-emitted.
    assert main([str(repo)]) == 0
    assert main([str(repo), "--check"]) == 0


def test_check_reddens_on_a_version_bump(tmp_path):
    """The studio-index failure in one assertion: the version in the artifact
    lagged eleven releases and nothing noticed."""
    repo = make_repo(tmp_path)
    assert main([str(repo)]) == 0
    capabilities = repo / "docs" / "capabilities.json"
    bumped = json.loads(capabilities.read_text())
    bumped["version"] = "1.3.0"
    capabilities.write_text(json.dumps(bumped))
    assert main([str(repo), "--check"]) == 1


def test_check_without_a_committed_artifact_is_a_failure_not_a_write(tmp_path, capsys):
    repo = make_repo(tmp_path)
    assert main([str(repo), "--check"]) == 1
    assert not (repo / "docs" / "llms.txt").exists()
    assert "does not exist" in capsys.readouterr().err


# ── writing elsewhere / stdout ───────────────────────────────────────────────

def test_out_dir_renders_a_checkout_that_must_not_be_written_to(tmp_path):
    repo = make_repo(tmp_path)
    out = tmp_path / "elsewhere"
    assert main([str(repo), "--out", str(out)]) == 0
    assert (out / "llms.txt").is_file()
    assert not (repo / "docs" / "llms.txt").exists()


def test_stdout_mode(tmp_path, capsys):
    repo = make_repo(tmp_path)
    assert main([str(repo), "--stdout"]) == 0
    assert capsys.readouterr().out.startswith("# stapel-demo 1.2.3")
    assert not (repo / "docs" / "llms.txt").exists()


# ── projection helpers ───────────────────────────────────────────────────────

def test_common_prefix_only_cuts_at_a_segment_boundary():
    assert common_prefix(["/a/bb/x", "/a/bc/y"]) == "/a/"
    assert common_prefix(["/a/b/x", "/a/b/y"]) == "/a/b/"
    assert common_prefix(["/a/x", "/b/y"]) == ""
    assert common_prefix(["/only/one"]) == ""


def test_operations_sort_by_tag_then_operation_id():
    ops = operations(
        {
            "paths": {
                "/z": {"get": {"operationId": "z_get", "tags": ["Alpha"]}},
                "/a": {"get": {"operationId": "a_get", "tags": ["Beta"]}},
            }
        }
    )
    assert [o["id"] for o in ops] == ["z_get", "a_get"]
    assert operations(None) == []


def test_error_codes_are_sorted_and_stripped():
    codes = error_codes(ERRORS)
    assert [c["code"] for c in codes] == [
        "error.400.field.blank",
        "error.404.not_found",
    ]
    assert set(codes[0]) == {"code", "status", "remediation", "params"}
