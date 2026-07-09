"""Tests for the capabilities.json emitter mechanism (capability-config.md §1-§2).

Hermetic: a fake ``conf.py``-style DEFAULTS dict, a fake gate registry (the
duck-typed ``.name/.flags/.patterns`` protocol, built with Django's settings-free
``path()``/``re_path()``) and a minimal schema.json exercise the pure
``build_capabilities()`` core plus the ``emit_capabilities()`` wrapper. The real
module run is the stapel-auth etalon shim (``stapel_auth._capabilities``); here
we prove the *mechanism*: axis introspection, gate→operationId attribution
(route, param and regex/router patterns), OR-composed ``co_gates`` derivation,
loud-fail curated-meta merge, and byte-stable output.
"""
import json

import pytest
from django.urls import path, re_path

from stapel_tools.capabilities import (
    GateEntry,
    _stable_json,
    axis_group_rules,
    axis_kind,
    build_capabilities,
    emit_capabilities,
    operations_by_entry,
    run_capabilities_cli,
)

PREFIX = "/probe/api"


def _view(request):  # pragma: no cover - never called, resolution target only
    raise NotImplementedError


def _registry() -> dict:
    """A fake gate registry mirroring the etalon's shapes.

    - ``otp``: one OR-composed entry (4 flags — the co_gates case);
    - ``anonymous``: single-flag entry (its own axis, the A1 shape);
    - ``sessions``: ungated (always-on) entry;
    - ``admin_api``: regex patterns incl. a captured param (the router shape).
    """
    return {
        "otp": GateEntry(
            "otp",
            (
                "PROBE_EMAIL_LOGIN",
                "PROBE_EMAIL_REGISTRATION",
                "PROBE_PHONE_LOGIN",
                "PROBE_PHONE_REGISTRATION",
            ),
            (
                path("email/request/", _view, name="email_request"),
                path("phone/request/", _view, name="phone_request"),
            ),
        ),
        "anonymous": GateEntry(
            "anonymous",
            ("PROBE_ANONYMOUS",),
            (path("anonymous/", _view, name="anonymous"),),
        ),
        "sessions": GateEntry(
            "sessions",
            (),
            (path("sessions/<str:session_id>/", _view, name="session"),),
        ),
        "admin_api": GateEntry(
            "admin_api",
            ("PROBE_ADMIN_API",),
            (
                re_path(r"^service-keys$", _view, name="service_keys"),
                re_path(r"^service-keys/(?P<pk>[^/.]+)$", _view, name="service_key"),
            ),
        ),
    }


def _schema() -> dict:
    def op(op_id, method="post"):
        return {method: {"operationId": op_id}}

    return {
        "paths": {
            f"{PREFIX}/email/request/": op("probe_email_request_create"),
            f"{PREFIX}/phone/request/": op("probe_phone_request_create"),
            f"{PREFIX}/anonymous/": op("probe_anonymous_create"),
            f"{PREFIX}/sessions/{{session_id}}/": {
                "delete": {"operationId": "probe_session_destroy"},
            },
            f"{PREFIX}/service-keys": op("probe_service_keys_list", "get"),
            f"{PREFIX}/service-keys/{{id}}": op("probe_service_key_retrieve", "get"),
            # Served by no entry (a co-mounted sibling module) — unattributed.
            f"{PREFIX}/gdpr/export/": op("probe_gdpr_export_create"),
        }
    }


DEFAULTS = {
    "PROBE_EMAIL_LOGIN": True,
    "PROBE_EMAIL_REGISTRATION": True,
    "PROBE_PHONE_LOGIN": True,
    "PROBE_PHONE_REGISTRATION": False,
    "PROBE_ANONYMOUS": True,
    "PROBE_ADMIN_API": True,
    "PROBE_STEP_UP": True,  # behavioral axis — gates no endpoints
    "PROBE_AVATAR_CHECK": "strict",  # enum axis
    "PROBE_PROVIDERS": ["a", "b"],  # list axis
    # NOT axes (tuning knobs) — must never leak into the artifact:
    "OTP_TTL_SECONDS": 300,
    "SOME_URL": "https://example.com",
}


def _is_axis(key: str) -> bool:
    return key.startswith("PROBE_")


AXIS_GROUP = axis_group_rules(
    exact={
        "PROBE_ANONYMOUS": "probe.anonymous",
        "PROBE_ADMIN_API": "probe.admin",
        "PROBE_AVATAR_CHECK": "probe.moderation",
        "PROBE_PROVIDERS": "probe.providers",
    },
    suffix={
        "_REGISTRATION": "probe.registration",
        "_LOGIN": "probe.login",
        "_STEP_UP": "probe.stepup",
    },
)


def _meta() -> dict:
    axes = {
        key: {"summary": f"{key} summary", "business_label": f"{key} label"}
        for key in DEFAULTS
        if _is_axis(key)
    }
    axes["PROBE_STEP_UP"]["behavior"] = "When on, a login returns a challenge."
    return {
        "provides": "Probe capabilities for the mechanism tests.",
        "requires": [{"module": "stapel-core", "optional": False, "reason": "bus"}],
        "extension_points": [
            {"name": "PROBE_PROVIDER_CLASSES", "kind": "dotted_path_list",
             "summary": "Register providers."},
        ],
        "axes": axes,
    }


def _build(meta=None, defaults=None, registry=None, schema=None) -> dict:
    return build_capabilities(
        module="stapel-probe",
        version="0.1.0",
        defaults=defaults or DEFAULTS,
        registry=registry if registry is not None else _registry(),
        schema=schema or _schema(),
        meta=meta or _meta(),
        is_axis=_is_axis,
        axis_group=AXIS_GROUP,
        canonical_prefix=PREFIX,
    )


def _axis(doc, key):
    return next(a for a in doc["axes"] if a["key"] == key)


class TestOperationsByEntry:
    def test_attributes_route_param_and_regex_paths(self):
        ops = operations_by_entry(_schema(), _registry(), canonical_prefix=PREFIX)
        assert ops["otp"] == [
            "probe_email_request_create", "probe_phone_request_create",
        ]
        assert ops["anonymous"] == ["probe_anonymous_create"]
        # {param} substitution resolves the path-converter pattern
        assert ops["sessions"] == ["probe_session_destroy"]
        # regex (router-style) patterns, incl. a captured param
        assert ops["admin_api"] == [
            "probe_service_key_retrieve", "probe_service_keys_list",
        ]

    def test_unprefixed_schema_path_fails_loudly(self):
        schema = _schema()
        schema["paths"]["/bare/path/"] = {"get": {"operationId": "x"}}
        with pytest.raises(SystemExit, match="canonical"):
            operations_by_entry(schema, _registry(), canonical_prefix=PREFIX)


class TestBuildCapabilities:
    def test_envelope_and_axis_inventory(self):
        doc = _build()
        assert doc["module"] == "stapel-probe"
        assert doc["version"] == "0.1.0"
        assert doc["provides"]
        assert doc["requires"] and doc["extension_points"]
        # only is_axis keys, in DEFAULTS insertion order
        assert [a["key"] for a in doc["axes"]] == [
            k for k in DEFAULTS if _is_axis(k)
        ]
        # the tuning knobs never leak
        assert "OTP_TTL_SECONDS" not in json.dumps(doc)

    def test_kinds_defaults_and_groups(self):
        doc = _build()
        assert _axis(doc, "PROBE_EMAIL_LOGIN")["kind"] == "bool"
        assert _axis(doc, "PROBE_AVATAR_CHECK")["kind"] == "enum"
        assert _axis(doc, "PROBE_PROVIDERS")["kind"] == "list"
        assert _axis(doc, "PROBE_PHONE_REGISTRATION")["default"] is False
        assert _axis(doc, "PROBE_EMAIL_LOGIN")["group"] == "probe.login"
        assert _axis(doc, "PROBE_ANONYMOUS")["group"] == "probe.anonymous"
        assert _axis(doc, "PROBE_STEP_UP")["group"] == "probe.stepup"

    def test_or_composed_co_gates(self):
        """The otp entry is gated by 4 flags with OR semantics: each of those
        axes carries the entry's operations plus the other 3 flags as co_gates
        — the aggregate-index contract from the design's auth-etalon note."""
        doc = _build()
        axis = _axis(doc, "PROBE_EMAIL_LOGIN")
        assert axis["gates"]["operations"] == [
            "probe_email_request_create", "probe_phone_request_create",
        ]
        assert axis["gates"]["co_gates"] == [
            "PROBE_EMAIL_REGISTRATION",
            "PROBE_PHONE_LOGIN",
            "PROBE_PHONE_REGISTRATION",
        ]
        # single-flag entry — no co_gates (its own factory, the A1 shape)
        anon = _axis(doc, "PROBE_ANONYMOUS")
        assert anon["gates"]["operations"] == ["probe_anonymous_create"]
        assert anon["gates"]["co_gates"] == []

    def test_behavioral_axis_gates_no_operations(self):
        axis = _axis(_build(), "PROBE_STEP_UP")
        assert axis["gates"]["operations"] == []
        assert axis["gates"]["behavior"] == "When on, a login returns a challenge."

    def test_operations_total_counts_all_methods(self):
        # 7 paths, one operation each (the gdpr path counts too — it is real
        # surface of the harness even if no gate of this module serves it).
        assert _build()["operations_total"] == 7

    def test_curated_semantics_merged(self):
        axis = _axis(_build(), "PROBE_EMAIL_LOGIN")
        assert axis["curated"] == {
            "summary": "PROBE_EMAIL_LOGIN summary",
            "business_label": "PROBE_EMAIL_LOGIN label",
        }

    def test_deterministic_output(self):
        assert _stable_json(_build()) == _stable_json(_build())


class TestLoudMetaFailures:
    """A curated-layer gap must be an emission ERROR, never a silent skip."""

    def test_missing_axis_entry(self):
        meta = _meta()
        del meta["axes"]["PROBE_ANONYMOUS"]
        with pytest.raises(SystemExit, match="PROBE_ANONYMOUS"):
            _build(meta=meta)

    def test_stale_axis_entry(self):
        meta = _meta()
        meta["axes"]["PROBE_NO_SUCH_AXIS"] = {"summary": "x", "business_label": "x"}
        with pytest.raises(SystemExit, match="PROBE_NO_SUCH_AXIS"):
            _build(meta=meta)

    def test_empty_business_label(self):
        meta = _meta()
        meta["axes"]["PROBE_EMAIL_LOGIN"]["business_label"] = ""
        with pytest.raises(SystemExit, match="business_label"):
            _build(meta=meta)

    def test_empty_summary(self):
        meta = _meta()
        del meta["axes"]["PROBE_STEP_UP"]["summary"]
        with pytest.raises(SystemExit, match="summary"):
            _build(meta=meta)

    def test_missing_provides(self):
        meta = _meta()
        meta["provides"] = ""
        with pytest.raises(SystemExit, match="provides"):
            _build(meta=meta)

    def test_extension_points_and_requires_must_be_lists(self):
        for field in ("extension_points", "requires"):
            meta = _meta()
            meta[field] = None
            with pytest.raises(SystemExit, match=field):
                _build(meta=meta)


class TestAxisGroupRules:
    def test_exact_wins_over_suffix(self):
        group = axis_group_rules(
            exact={"X_LOGIN": "special"}, suffix={"_LOGIN": "login"}
        )
        assert group("X_LOGIN") == "special"
        assert group("Y_LOGIN") == "login"

    def test_unmatched_key_fails_loudly(self):
        group = axis_group_rules(suffix={"_LOGIN": "login"})
        with pytest.raises(SystemExit, match="UNMATCHED"):
            group("UNMATCHED")


class TestAxisKind:
    def test_shapes(self):
        assert axis_kind(True) == "bool"
        assert axis_kind([1]) == "list"
        assert axis_kind(("a",)) == "list"
        assert axis_kind("strict") == "enum"
        assert axis_kind(3) == "enum"


# ── emit_capabilities: the repo-facing wrapper ─────────────────────────────────


def _repo(tmp_path, *, meta=True, schema=True):
    repo = tmp_path / "stapel-probe"
    (repo / "docs").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "stapel-probe"\nversion = "0.1.0"\n'
    )
    if meta:
        (repo / "docs" / "capabilities.meta.json").write_text(json.dumps(_meta()))
    if schema:
        (repo / "docs" / "schema.json").write_text(json.dumps(_schema()))
    return repo


def _emit(repo, out_dir=None, registry=None):
    return emit_capabilities(
        out_dir if out_dir is not None else repo / "docs",
        repo=repo,
        canonical_prefix=PREFIX,
        defaults=DEFAULTS,
        registry=registry if registry is not None else _registry(),
        is_axis=_is_axis,
        axis_group=AXIS_GROUP,
    )


class TestEmitCapabilities:
    def test_writes_artifact_with_pyproject_identity(self, tmp_path):
        repo = _repo(tmp_path)
        doc = _emit(repo)
        artifact = repo / "docs" / "capabilities.json"
        assert artifact.is_file()
        on_disk = json.loads(artifact.read_text())
        assert on_disk == doc
        assert on_disk["module"] == "stapel-probe"
        assert on_disk["version"] == "0.1.0"
        assert artifact.read_text().endswith("\n")

    def test_emission_is_byte_stable(self, tmp_path):
        repo = _repo(tmp_path)
        _emit(repo)
        first = (repo / "docs" / "capabilities.json").read_bytes()
        _emit(repo)
        assert (repo / "docs" / "capabilities.json").read_bytes() == first

    def test_missing_meta_fails_loudly(self, tmp_path):
        repo = _repo(tmp_path, meta=False)
        with pytest.raises(SystemExit, match="capabilities.meta.json"):
            _emit(repo)

    def test_missing_schema_fails_loudly(self, tmp_path):
        repo = _repo(tmp_path, schema=False)
        with pytest.raises(SystemExit, match="schema.json"):
            _emit(repo)

    def test_empty_registry_fails_closed(self, tmp_path):
        repo = _repo(tmp_path)
        with pytest.raises(SystemExit, match="registry"):
            _emit(repo, registry={})


class TestCli:
    def test_out_flag_and_summary(self, tmp_path, capsys):
        repo = _repo(tmp_path)
        out = tmp_path / "out"
        out.mkdir()
        (out / "schema.json").write_text(json.dumps(_schema()))
        rc = run_capabilities_cli(
            ["--out", str(out)],
            repo=repo,
            canonical_prefix=PREFIX,
            defaults=DEFAULTS,
            registry=_registry(),
            is_axis=_is_axis,
            axis_group=AXIS_GROUP,
        )
        assert rc == 0
        assert (out / "capabilities.json").is_file()
        summary = capsys.readouterr().err
        assert "stapel-probe capabilities:" in summary
        assert "9 axes" in summary
