"""API001-003 + SCHEMA001 — the HTTP surface versioning gate (api-versioning.md §3).

Two halves, deliberately separated:

* the *classifier* is pure — two OpenAPI dicts in, a list of breaking changes
  out — so the five §3 rules are exercised as fixture pairs (the corpus idiom
  upgrade-pipeline.md §3.7 asks for), with no git and no filesystem in the way;
* the *gate* is filesystem + git, so it gets real one-commit repos with a real
  tag, because the thing that has historically broken in this arsenal is the
  wiring, not the logic (that is why stapel-verify exists at all).

The additive cases matter as much as the breaking ones: a classifier that
flags a new optional field would make every release a version event and the
team would route around the gate within a week.
"""
from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from stapel_tools import api_lint, lint_profile, verify

# ---------------------------------------------------------------------------
# fixture builders — small hand-written OpenAPI documents
# ---------------------------------------------------------------------------


def doc(paths: dict, *, components: dict | None = None, version: str = "0.0.0") -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "", "version": version},
        "paths": paths,
        "components": {"schemas": components or {}},
    }


def op(*, request=None, responses=None, security=None) -> dict:
    out: dict = {"responses": responses or {"200": {"content": {
        "application/json": {"schema": {"type": "object", "properties": {}}}
    }}}}
    if request is not None:
        out["requestBody"] = {"content": {"application/json": {"schema": request}}}
    if security is not None:
        out["security"] = security
    return out


def obj(props: dict, required: list | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


BASE = doc({
    "/auth/api/v1/login": {"post": op(
        request=obj({"email": {"type": "string"}, "password": {"type": "string"}},
                    ["email", "password"]),
        responses={
            "200": {"content": {"application/json": {"schema": obj({
                "token": {"type": "string"},
                "role": {"type": "string", "enum": ["user", "admin"]},
            })}}},
            "401": {"description": "bad credentials"},
        },
        security=[{"cookieAuth": []}],
    )},
})


# ---------------------------------------------------------------------------
# the classifier — additive changes are NOT findings
# ---------------------------------------------------------------------------


def test_identical_documents_are_not_a_change():
    assert api_lint.classify_schema_diff(BASE, json.loads(json.dumps(BASE))) == []


def test_new_endpoint_is_additive():
    after = json.loads(json.dumps(BASE))
    after["paths"]["/auth/api/v1/logout"] = {"post": op()}
    assert api_lint.classify_schema_diff(BASE, after) == []


def test_new_optional_field_is_additive():
    after = json.loads(json.dumps(BASE))
    schema = after["paths"]["/auth/api/v1/login"]["post"]["requestBody"]["content"][
        "application/json"]["schema"]
    schema["properties"]["remember_me"] = {"type": "boolean"}
    assert api_lint.classify_schema_diff(BASE, after) == []


def test_new_response_code_is_additive():
    after = json.loads(json.dumps(BASE))
    after["paths"]["/auth/api/v1/login"]["post"]["responses"]["429"] = {
        "description": "throttled"
    }
    assert api_lint.classify_schema_diff(BASE, after) == []


def test_value_added_to_an_open_enum_is_additive():
    after = json.loads(json.dumps(BASE))
    enum = after["paths"]["/auth/api/v1/login"]["post"]["responses"]["200"][
        "content"]["application/json"]["schema"]["properties"]["role"]
    enum["enum"] = ["user", "admin", "auditor"]
    assert api_lint.classify_schema_diff(BASE, after) == []


# ---------------------------------------------------------------------------
# the classifier — the five §3 breaking rules
# ---------------------------------------------------------------------------


def kinds(changes) -> list[str]:
    return sorted({c.kind for c in changes})


def test_rule1_endpoint_removed():
    after = doc({})
    changes = api_lint.classify_schema_diff(BASE, after)
    assert kinds(changes) == ["endpoint"]
    assert "POST /auth/api/v1/login" in str(changes[0])


def test_rule1_endpoint_renamed_reads_as_removal():
    after = json.loads(json.dumps(BASE))
    after["paths"]["/auth/api/v1/signin"] = after["paths"].pop("/auth/api/v1/login")
    changes = api_lint.classify_schema_diff(BASE, after)
    assert [c.kind for c in changes] == ["endpoint"]


def test_rule1_method_removed_from_a_surviving_path():
    before = doc({"/x/api/v1/a": {"get": op(), "delete": op()}})
    after = doc({"/x/api/v1/a": {"get": op()}})
    changes = api_lint.classify_schema_diff(before, after)
    assert len(changes) == 1
    assert "DELETE" in changes[0].where


def test_rule2_field_removed():
    after = json.loads(json.dumps(BASE))
    del after["paths"]["/auth/api/v1/login"]["post"]["responses"]["200"][
        "content"]["application/json"]["schema"]["properties"]["token"]
    changes = api_lint.classify_schema_diff(BASE, after)
    assert kinds(changes) == ["field"]
    assert "token" in changes[0].where


def test_rule2_field_type_changed():
    after = json.loads(json.dumps(BASE))
    after["paths"]["/auth/api/v1/login"]["post"]["responses"]["200"]["content"][
        "application/json"]["schema"]["properties"]["token"] = {"type": "integer"}
    changes = api_lint.classify_schema_diff(BASE, after)
    assert any("string -> integer" in c.detail for c in changes)


def test_rule2_optional_becomes_required_in_a_request_body():
    before = doc({"/x/api/v1/a": {"post": op(
        request=obj({"a": {"type": "string"}, "b": {"type": "string"}}, ["a"])
    )}})
    after = doc({"/x/api/v1/a": {"post": op(
        request=obj({"a": {"type": "string"}, "b": {"type": "string"}}, ["a", "b"])
    )}})
    changes = api_lint.classify_schema_diff(before, after)
    assert any("optional -> required" in c.detail for c in changes)


def test_rule2_required_becomes_optional_in_a_request_body_is_additive():
    before = doc({"/x/api/v1/a": {"post": op(
        request=obj({"a": {"type": "string"}}, ["a"])
    )}})
    after = doc({"/x/api/v1/a": {"post": op(
        request=obj({"a": {"type": "string"}}, [])
    )}})
    assert api_lint.classify_schema_diff(before, after) == []


def response_doc(required):
    return doc({"/x/api/v1/a": {"get": op(responses={"200": {"content": {
        "application/json": {"schema": obj({"a": {"type": "string"}}, required)}
    }}})}})


def test_rule2_required_becomes_optional_in_a_response_body_is_breaking():
    changes = api_lint.classify_schema_diff(response_doc(["a"]), response_doc([]))
    assert any("required -> optional" in c.detail for c in changes)


def test_rule2_optional_becomes_required_in_a_response_body_is_additive():
    """The direction that matters. The server promising a field it used to
    omit cannot break a caller — flagging it would make half the fleet's
    serializer tightenings look like version events."""
    assert api_lint.classify_schema_diff(response_doc([]), response_doc(["a"])) == []


def nullable_doc(*, request: bool, nullable: bool):
    field = {"type": "string", "nullable": nullable}
    if request:
        return doc({"/x/api/v1/a": {"post": op(request=obj({"a": field}))}})
    return doc({"/x/api/v1/a": {"get": op(responses={"200": {"content": {
        "application/json": {"schema": obj({"a": field})}
    }}})}})


def test_nullable_narrowing_breaks_a_request_not_a_response():
    breaking = api_lint.classify_schema_diff(
        nullable_doc(request=True, nullable=True),
        nullable_doc(request=True, nullable=False),
    )
    assert any("nullable -> non-nullable" in c.detail for c in breaking)
    assert api_lint.classify_schema_diff(
        nullable_doc(request=False, nullable=True),
        nullable_doc(request=False, nullable=False),
    ) == []


def test_nullable_widening_breaks_a_response_not_a_request():
    breaking = api_lint.classify_schema_diff(
        nullable_doc(request=False, nullable=False),
        nullable_doc(request=False, nullable=True),
    )
    assert any("non-nullable -> nullable" in c.detail for c in breaking)
    assert api_lint.classify_schema_diff(
        nullable_doc(request=True, nullable=False),
        nullable_doc(request=True, nullable=True),
    ) == []


def test_rule2_nested_field_removed_through_a_ref():
    components = {"Profile": obj({"name": {"type": "string"}, "bio": {"type": "string"}})}
    before = doc(
        {"/x/api/v1/me": {"get": op(responses={"200": {"content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/Profile"}}
        }}})}},
        components=components,
    )
    after = json.loads(json.dumps(before))
    del after["components"]["schemas"]["Profile"]["properties"]["bio"]
    changes = api_lint.classify_schema_diff(before, after)
    assert any(c.where.endswith(": bio") for c in changes)


def test_self_referential_component_terminates():
    components = {"Node": obj({
        "id": {"type": "string"},
        "parent": {"$ref": "#/components/schemas/Node"},
    })}
    before = doc(
        {"/x/api/v1/n": {"get": op(responses={"200": {"content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/Node"}}
        }}})}},
        components=components,
    )
    assert api_lint.classify_schema_diff(before, json.loads(json.dumps(before))) == []


def test_allof_wrapper_is_flattened_so_enums_are_visible():
    components = {"Role": {"enum": ["user", "admin"], "type": "string"}}
    before = doc(
        {"/x/api/v1/me": {"get": op(responses={"200": {"content": {
            "application/json": {"schema": obj({
                "role": {"allOf": [{"$ref": "#/components/schemas/Role"}],
                         "description": "the role"},
            })}
        }}})}},
        components=components,
    )
    after = json.loads(json.dumps(before))
    after["components"]["schemas"]["Role"]["enum"] = ["user"]
    changes = api_lint.classify_schema_diff(before, after)
    assert kinds(changes) == ["enum"]


def test_rule3_response_code_removed():
    after = json.loads(json.dumps(BASE))
    del after["paths"]["/auth/api/v1/login"]["post"]["responses"]["401"]
    changes = api_lint.classify_schema_diff(BASE, after)
    assert kinds(changes) == ["status"]
    assert "401" in changes[0].detail


def test_rule4_auth_contract_changed():
    after = json.loads(json.dumps(BASE))
    after["paths"]["/auth/api/v1/login"]["post"]["security"] = [{"bearerAuth": []}]
    changes = api_lint.classify_schema_diff(BASE, after)
    assert kinds(changes) == ["security"]
    assert "cookieAuth -> bearerAuth" in changes[0].detail


def test_rule5_enum_value_removed_is_always_breaking():
    after = json.loads(json.dumps(BASE))
    after["paths"]["/auth/api/v1/login"]["post"]["responses"]["200"]["content"][
        "application/json"]["schema"]["properties"]["role"]["enum"] = ["user"]
    changes = api_lint.classify_schema_diff(BASE, after)
    assert kinds(changes) == ["enum"]
    assert "'admin'" in changes[0].detail


def test_rule5_value_added_to_a_closed_enum_is_breaking():
    before = doc({"/x/api/v1/me": {"get": op(responses={"200": {"content": {
        "application/json": {"schema": obj({"role": {
            "type": "string", "enum": ["user", "admin"],
            api_lint.CLOSED_ENUM_KEY: True,
        }})}
    }}})}})
    after = json.loads(json.dumps(before))
    after["paths"]["/x/api/v1/me"]["get"]["responses"]["200"]["content"][
        "application/json"]["schema"]["properties"]["role"]["enum"] = [
        "user", "admin", "root"]
    changes = api_lint.classify_schema_diff(before, after)
    assert kinds(changes) == ["enum"]
    assert api_lint.CLOSED_ENUM_KEY in changes[0].detail


# ---------------------------------------------------------------------------
# version arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("before,after,ok", [
    ("0.4.2", "0.4.3", False),   # patch cannot carry a breaking change
    ("0.4.2", "0.5.0", True),    # pre-1.0: minor is the breaking bump
    ("0.4.2", "1.0.0", True),
    ("0.4.2", "0.4.2", False),   # no bump at all
    ("1.2.0", "1.3.0", False),   # post-1.0: a minor is NOT enough
    ("1.2.0", "2.0.0", True),
])
def test_bump_is_sufficient(before, after, ok):
    assert api_lint.bump_is_sufficient(before, after) is ok


def test_schema_versions_reads_only_the_segment_after_api():
    versions = api_lint.schema_versions(doc({
        "/x/api/v1/a": {"get": op()},
        "/x/api/v2/a": {"get": op()},
        "/x/api/v1/releases/v3/notes": {"get": op()},  # v3 here is a resource
    }))
    assert sorted(versions) == [1, 2]
    assert len(versions[1]) == 2


# ---------------------------------------------------------------------------
# the gate — real repos, real tags
# ---------------------------------------------------------------------------


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def make_repo(tmp_path: Path, schema: dict, version: str, *, tag="v0.4.2") -> Path:
    repo = tmp_path / "stapel-thing"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "schema.json").write_text(json.dumps(schema, indent=2))
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "stapel-thing"\nversion = "{version}"\n'
    )
    git(repo.parent, "init", "-q", str(repo))
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "tag", tag)
    return repo


def rewrite(repo: Path, *, schema: dict | None = None, version: str | None = None) -> None:
    if schema is not None:
        (repo / "docs" / "schema.json").write_text(json.dumps(schema, indent=2))
    if version is not None:
        (repo / "pyproject.toml").write_text(
            f'[project]\nname = "stapel-thing"\nversion = "{version}"\n'
        )


def rules(findings) -> list[str]:
    return sorted(f.rule for f in findings)


def test_no_schema_means_nothing_to_check(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.1.0"\n')
    notes: list[str] = []
    assert api_lint.lint_project(tmp_path, notes=notes) == []
    assert any("no docs/schema.json" in n for n in notes)


def test_no_tag_means_no_baseline_and_no_api_findings(tmp_path):
    repo = tmp_path / "r"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "schema.json").write_text(json.dumps(BASE))
    (repo / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.0.0"\n')
    git(repo.parent, "init", "-q", str(repo))
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    notes: list[str] = []
    findings = api_lint.lint_project(repo, notes=notes)
    assert rules(findings) == []  # info.version 0.0.0 == pyproject 0.0.0
    assert any("no v<semver> tag" in n for n in notes)


def test_additive_release_is_clean(tmp_path):
    repo = make_repo(tmp_path, BASE, "0.4.2")
    after = json.loads(json.dumps(BASE))
    after["paths"]["/auth/api/v1/logout"] = {"post": op()}
    rewrite(repo, schema=after, version="0.5.0")
    findings = api_lint.lint_project(repo)
    assert rules(findings) == ["SCHEMA001"]  # info.version still the placeholder


def test_api001_breaking_change_on_a_patch_bump(tmp_path):
    repo = make_repo(tmp_path, BASE, "0.4.2")
    rewrite(repo, schema=doc({}), version="0.4.3")
    findings = api_lint.lint_project(repo)
    api001 = [f for f in findings if f.rule == "API001"]
    assert len(api001) == 1
    assert "not a minor bump" in api001[0].message
    assert "UPGRADE.json" in api001[0].message
    assert "POST /auth/api/v1/login" in api001[0].message


def test_api001_satisfied_by_a_minor_bump_plus_an_upgrade_record(tmp_path):
    repo = make_repo(tmp_path, BASE, "0.4.2")
    after = doc({"/auth/api/v1/login": {"post": op()},
                 "/auth/api/v2/login": {"post": op()}})
    rewrite(repo, schema=after, version="0.5.0")
    (repo / "docs" / "UPGRADE.json").write_text(json.dumps([{
        "kind": "api_change", "version": "0.5.0",
        "old_path": "/auth/api/v1/login", "new_path": "/auth/api/v2/login",
        "version_from": "v1", "version_to": "v2",
    }]))
    findings = api_lint.lint_project(repo)
    assert "API001" not in rules(findings)


def test_api001_bump_without_an_upgrade_record_still_fires(tmp_path):
    repo = make_repo(tmp_path, BASE, "0.4.2")
    after = doc({"/auth/api/v1/login": {"post": op()},
                 "/auth/api/v2/login": {"post": op()}})
    rewrite(repo, schema=after, version="0.5.0")
    findings = [f for f in api_lint.lint_project(repo) if f.rule == "API001"]
    assert len(findings) == 1
    assert "no 'kind: api_change' record" in findings[0].message


def test_api001_record_for_the_wrong_version_is_not_a_record(tmp_path):
    repo = make_repo(tmp_path, BASE, "0.4.2")
    after = doc({"/auth/api/v1/login": {"post": op()},
                 "/auth/api/v2/login": {"post": op()}})
    rewrite(repo, schema=after, version="0.5.0")
    (repo / "docs" / "UPGRADE.json").write_text(json.dumps(
        {"entries": [{"kind": "api_change", "version": "0.3.0"}]}
    ))
    findings = [f for f in api_lint.lint_project(repo) if f.rule == "API001"]
    assert len(findings) == 1
    assert "none for 0.5.0" in findings[0].message


def test_api002_breaking_change_in_place_without_a_new_version(tmp_path):
    repo = make_repo(tmp_path, BASE, "0.4.2")
    after = json.loads(json.dumps(BASE))
    after["paths"]["/auth/api/v1/login"]["post"]["responses"]["200"]["content"][
        "application/json"]["schema"]["properties"]["token"] = {"type": "integer"}
    rewrite(repo, schema=after, version="0.5.0")
    (repo / "docs" / "UPGRADE.json").write_text(json.dumps(
        [{"kind": "api_change", "version": "0.5.0"}]
    ))
    findings = [f for f in api_lint.lint_project(repo) if f.rule == "API002"]
    assert len(findings) == 1
    assert "/api/v2/" in findings[0].message


def test_api002_clean_when_v2_is_added_beside_v1(tmp_path):
    repo = make_repo(tmp_path, BASE, "0.4.2")
    after = json.loads(json.dumps(BASE))
    after["paths"]["/auth/api/v2/login"] = {"post": op()}
    # v1 keeps its frozen shape; v2 is where the new form lives
    rewrite(repo, schema=after, version="0.5.0")
    (repo / "docs" / "UPGRADE.json").write_text(json.dumps(
        [{"kind": "api_change", "version": "0.5.0"}]
    ))
    # additive-only diff -> no breaking changes at all -> nothing to carry
    assert "API002" not in rules(api_lint.lint_project(repo))


def test_api002_new_version_but_the_old_one_is_no_longer_mounted(tmp_path):
    repo = make_repo(tmp_path, BASE, "0.4.2")
    after = json.loads(json.dumps(BASE))
    after["paths"]["/auth/api/v2/login"] = {"post": op(
        request=obj({"identifier": {"type": "string"}}, ["identifier"])
    )}
    del after["paths"]["/auth/api/v1/login"]["post"]["responses"]["401"]
    rewrite(repo, schema=after, version="0.5.0")
    (repo / "docs" / "UPGRADE.json").write_text(json.dumps(
        [{"kind": "api_change", "version": "0.5.0"}]
    ))
    (repo / "urls_v1.py").write_text("urlpatterns = []\n")
    (repo / "urls.py").write_text(
        "from django.urls import include, path\n"
        "urlpatterns = [path('api/v2/', include('x.urls_v2'))]\n"
    )
    findings = [f for f in api_lint.lint_project(repo) if f.rule == "API002"]
    assert len(findings) == 1
    assert "does not mount" in findings[0].message


def test_api002_quiet_when_urls_py_still_mounts_v1(tmp_path):
    repo = make_repo(tmp_path, BASE, "0.4.2")
    after = json.loads(json.dumps(BASE))
    after["paths"]["/auth/api/v2/login"] = {"post": op()}
    del after["paths"]["/auth/api/v1/login"]["post"]["responses"]["401"]
    rewrite(repo, schema=after, version="0.5.0")
    (repo / "docs" / "UPGRADE.json").write_text(json.dumps(
        [{"kind": "api_change", "version": "0.5.0"}]
    ))
    (repo / "urls_v1.py").write_text("urlpatterns = []\n")
    (repo / "urls.py").write_text(
        "from django.urls import include, path\n"
        "urlpatterns = [\n"
        "    path('api/v1/', include('x.urls_v1')),\n"
        "    path('api/v2/', include('x.urls_v2')),\n"
        "]\n"
    )
    assert "API002" not in rules(api_lint.lint_project(repo))


def test_api003_version_dropped_without_ever_declaring_a_sunset(tmp_path):
    before = doc({"/auth/api/v1/login": {"post": op()},
                  "/auth/api/v2/login": {"post": op()}})
    repo = make_repo(tmp_path, before, "0.4.2")
    rewrite(repo, schema=doc({"/auth/api/v2/login": {"post": op()}}), version="0.5.0")
    findings = [f for f in api_lint.lint_project(repo) if f.rule == "API003"]
    assert len(findings) == 1
    assert api_lint.SUNSET_KEY in findings[0].message


def test_api003_version_dropped_before_its_declared_sunset(tmp_path):
    v1 = op()
    v1[api_lint.SUNSET_KEY] = "2030-01-01"
    before = doc({"/auth/api/v1/login": {"post": v1},
                  "/auth/api/v2/login": {"post": op()}})
    repo = make_repo(tmp_path, before, "0.4.2")
    rewrite(repo, schema=doc({"/auth/api/v2/login": {"post": op()}}), version="0.5.0")
    findings = [f for f in api_lint.lint_project(repo, today=date(2026, 8, 24))
                if f.rule == "API003"]
    assert len(findings) == 1
    assert "2030-01-01" in findings[0].message


def test_api003_quiet_once_the_sunset_has_passed(tmp_path):
    v1 = op()
    v1[api_lint.SUNSET_KEY] = "2026-01-01"
    before = doc({"/auth/api/v1/login": {"post": v1},
                  "/auth/api/v2/login": {"post": op()}})
    repo = make_repo(tmp_path, before, "0.4.2")
    rewrite(repo, schema=doc({"/auth/api/v2/login": {"post": op()}}), version="0.6.0")
    (repo / "docs" / "UPGRADE.json").write_text(json.dumps(
        [{"kind": "api_change", "version": "0.6.0"}]
    ))
    findings = api_lint.lint_project(repo, today=date(2026, 8, 24))
    assert "API003" not in rules(findings)


def test_schema001_flags_the_placeholder_version(tmp_path):
    repo = make_repo(tmp_path, BASE, "0.4.2")
    findings = [f for f in api_lint.lint_project(repo) if f.rule == "SCHEMA001"]
    assert len(findings) == 1
    assert findings[0].level == "warning"
    assert "0.0.0" in findings[0].message


def test_schema001_quiet_when_info_version_tracks_the_package(tmp_path):
    repo = make_repo(tmp_path, doc({}, version="0.4.2"), "0.4.2")
    assert api_lint.lint_project(repo) == []


def test_explicit_base_ref_overrides_the_tag_search(tmp_path):
    repo = make_repo(tmp_path, BASE, "0.4.2", tag="v0.4.2")
    rewrite(repo, schema=doc({}), version="0.4.3")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "wip")
    # against HEAD itself there is no diff at all
    assert "API001" not in rules(api_lint.lint_project(repo, base_ref="HEAD"))
    assert "API001" in rules(api_lint.lint_project(repo, base_ref="v0.4.2"))


# ---------------------------------------------------------------------------
# CLI + wiring
# ---------------------------------------------------------------------------


def test_cli_json_output_and_exit_code(tmp_path, capsys):
    repo = make_repo(tmp_path, BASE, "0.4.2")
    rewrite(repo, schema=doc({}), version="0.4.3")
    code = api_lint.main([str(repo), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert "API001" in {f["rule"] for f in payload["findings"]}


def test_cli_strict_turns_a_warning_into_a_failure(tmp_path, capsys):
    repo = make_repo(tmp_path, BASE, "0.4.2")
    assert api_lint.main([str(repo)]) == 0
    capsys.readouterr()
    assert api_lint.main([str(repo), "--strict"]) == 1


def test_cli_rejects_a_missing_directory(capsys):
    assert api_lint.main(["/nope/not/here"]) == 2


def test_api_lint_is_composed_into_stapel_verify():
    assert "stapel-api-lint" in verify.COMPOSED_LINTERS
    assert lint_profile.LINTER_SURFACES["stapel-api-lint"] == "python"


def test_verify_runs_api_lint_and_reports_it(tmp_path):
    repo = make_repo(tmp_path, BASE, "0.4.2")
    rewrite(repo, schema=doc({}), version="0.4.3")
    reports = {r.name: r for r in verify.verify_project(repo)}
    assert "stapel-api-lint" in reports
    assert reports["stapel-api-lint"].errors >= 1
    assert {f["rule"] for f in reports["stapel-api-lint"].findings} >= {"API001"}


def test_verify_forwards_base_sha_as_the_api_baseline(tmp_path):
    repo = make_repo(tmp_path, BASE, "0.4.2")
    rewrite(repo, schema=doc({}), version="0.4.3")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "wip")
    reports = {r.name: r for r in verify.verify_project(repo, base_sha="HEAD")}
    assert reports["stapel-api-lint"].errors == 0


def test_python_surface_off_skips_the_api_gate(tmp_path):
    repo = make_repo(tmp_path, BASE, "0.4.2")
    rewrite(repo, schema=doc({}), version="0.4.3")
    (repo / "stapel-lint.toml").write_text(
        '[surface.python]\nmode = "off"\nreason = "legacy"\n'
    )
    reports = {r.name: r for r in verify.verify_project(repo)}
    assert reports["stapel-api-lint"].skipped is True
    assert reports["stapel-api-lint"].errors == 0
