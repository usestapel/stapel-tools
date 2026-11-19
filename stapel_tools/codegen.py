"""
stapel-codegen — emit the backend artifacts that drive the frontend codegen.

Design: docs/flow-system.md §0.1 + "Implementation order" item 1. The codegen source
is a single *all-modules* Django instance (e.g. stapel-example-monolith on
sqlite). From it we emit three language-agnostic artifacts:

  - schema.json  — the unified drf-spectacular OpenAPI for every installed
    module (the same document the live instance serves at ``/schema/``). It is
    produced offline via the ``spectacular`` management command rather than over
    HTTP: no running server, no port, byte-stable output — exactly what a drift
    gate needs. The runtime ``/schema/`` endpoint stays available for the
    frontend dev loop; it is the same generator, same SPECTACULAR_SETTINGS.

    ``info.version`` in that document is NOT the contract's version: the
    aggregate emits under the drf-spectacular defaults (``"0.0.0"`` with an
    empty ``info.title``), and every per-lib emitter leaves
    ``SPECTACULAR_SETTINGS`` unset so its slice stays byte-identical to this
    one. The version a consumer pins lives in ``pyproject.toml`` and in the
    pair's ``manifest.json`` (``backend.contract``). ``stapel-api-lint``'s
    SCHEMA001 holds that convention.

  - flows.json   — the ``generate_flow_docs`` machine artifact (flows +
    endpoint bindings), language-agnostic per flow-system.md §2.

  - errors.json  — the ``generate_error_keys`` machine artifact (every
    ``error.<status>.<name>`` key the instance can raise, with its HTTP
    status, ``{param}`` slots, remediation hint and canonical English text),
    the backend companion documented in stapel-core's
    ``stapel_core/django/api/errors.py``.

Plus one localized projection (flow-system.md §3):

  - features/     — the ``generate_flow_features`` Gherkin bundles: one
    directory per project language with ``.feature`` files + the
    playwright-bdd step library over the codegen typed client.

The JSON artifacts are re-normalised to a stable encoding (sorted-free, 2-space
indent, trailing newline) and the feature bundles render deterministically, so
that regenerating without a code change yields zero diff.

This module is the reusable *mechanism*. It must run inside an already-configured
Django instance: the caller sets ``DJANGO_SETTINGS_MODULE`` (and PYTHONPATH) to
point at the all-modules instance. The concrete instance + orchestration live in
stapel-example-monolith (``config.settings.codegen`` + ``codegen/generate.sh``).

Usage (from the monolith service dir):
    DJANGO_ENV=local DJANGO_SETTINGS_MODULE=config.settings.codegen \\
        python -m stapel_tools.codegen --out ../codegen/generated
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path


def _stable_json(data) -> str:
    """Byte-stable JSON encoding for drift gates.

    Preserves the generator's own key order (drf-spectacular and export_json are
    already deterministic), pins indent + separators, keeps unicode readable,
    and terminates with a single trailing newline.
    """
    return json.dumps(data, indent=2, ensure_ascii=False, separators=(",", ": ")) + "\n"


#: Contract profiles ``emit_schema`` can write. 1 is the historical emission and
#: stays byte-identical; 2 is the opt-in, client-generatable one (see
#: ``apply_contract_profile``).
CONTRACT_PROFILES = (1, 2)

#: The response statuses that legitimately carry no body.
_BODYLESS_2XX = {"204", "205"}


class ContractProfileError(RuntimeError):
    """The module's source cannot be described truthfully in the profile asked for."""


def emit_schema(out_path: Path, *, contract_profile: int = 1) -> int:
    """Emit the unified OpenAPI schema to ``out_path``. Returns the path count.

    ``contract_profile=1`` (default) is the historical document, unchanged.
    ``contract_profile=2`` runs drf-spectacular under stapel-core's profile-2
    AutoSchema hooks and then :func:`apply_contract_profile`.
    """
    if contract_profile not in CONTRACT_PROFILES:
        raise ValueError(
            f"unknown contract profile {contract_profile!r}; supported: {CONTRACT_PROFILES}"
        )
    from django.core.management import call_command

    buf = io.StringIO()
    if contract_profile == 1:
        # openapi-json → a single JSON document covering every mounted module.
        call_command("spectacular", "--format", "openapi-json", stdout=buf)
        schema = json.loads(buf.getvalue())
    else:
        try:
            from stapel_core.django.openapi.contract import (
                contract_profile as _profile_context,
            )
        except ImportError as exc:  # pragma: no cover - depends on the installed core
            raise ContractProfileError(
                "contract profile 2 needs stapel-core>=0.97.0 "
                "(stapel_core.django.openapi.contract)"
            ) from exc
        with _profile_context(contract_profile):
            call_command("spectacular", "--format", "openapi-json", stdout=buf)
        schema = apply_contract_profile(json.loads(buf.getvalue()), contract_profile)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_stable_json(schema))
    return len(schema.get("paths", {}))


# ---------------------------------------------------------------------------
# Contract profile 2 — the JSON post-pass
# ---------------------------------------------------------------------------


def _operations(schema: dict):
    for path, item in (schema.get("paths") or {}).items():
        for method, operation in item.items():
            if isinstance(operation, dict) and method in {
                "get", "put", "post", "delete", "options", "head", "patch", "trace"
            }:
                yield path, method, operation


def _component_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _deref(schema: dict, node: dict) -> tuple[str | None, dict]:
    """Follow ``$ref`` and the single-``$ref`` ``allOf`` wrapper drf-spectacular
    uses to attach a description/nullable to a reference."""
    components = (schema.get("components") or {}).get("schemas") or {}
    name = None
    for _ in range(8):
        if "$ref" in node:
            name = _component_name(node["$ref"])
            node = components.get(name, {})
        elif isinstance(node.get("allOf"), list) and len(node["allOf"]) == 1:
            node = node["allOf"][0]
        else:
            break
    return name, node


def _wire_values(schema: dict, prop: dict) -> list | None:
    _, resolved = _deref(schema, prop)
    values = resolved.get("enum")
    if values is None and "const" in resolved:
        values = [resolved["const"]]
    if not values:
        return None
    return [v for v in values if v is not None and v != ""]


def wire_discriminators(schema: dict) -> int:
    """Key every ``discriminator.mapping`` by the values the property takes on the wire.

    drf-spectacular keys a ``PolymorphicProxySerializer`` mapping by serializer
    class names (``"AuthResponse"``) or by whatever its field metadata yielded
    (``"None"``, ``"null"``), while the body carries ``status: "LOGGED_IN"``.
    The mapping is rebuilt from each variant's enum of the discriminator
    property. A variant whose property is not an enum, or two variants claiming
    one value, cannot be described truthfully and are refused.
    Returns the number of discriminators rewritten.
    """
    components = (schema.get("components") or {}).get("schemas") or {}
    problems: list[str] = []
    fixed = 0
    for name, component in components.items():
        discriminator = component.get("discriminator")
        variants = component.get("oneOf") or component.get("anyOf")
        if not discriminator or not variants:
            continue
        prop_name = discriminator.get("propertyName")
        mapping: dict[str, str] = {}
        for variant in variants:
            ref = variant.get("$ref")
            if not ref:
                problems.append(f"{name}: inline variant, cannot be mapped")
                continue
            _, body = _deref(schema, variant)
            prop = (body.get("properties") or {}).get(prop_name)
            values = _wire_values(schema, prop) if prop is not None else None
            if not values:
                problems.append(
                    f"{name}: variant {_component_name(ref)} declares no enum for "
                    f"discriminator {prop_name!r}"
                )
                continue
            for value in values:
                key = str(value)
                if key in mapping and mapping[key] != ref:
                    problems.append(
                        f"{name}: wire value {key!r} claimed by "
                        f"{_component_name(mapping[key])} and {_component_name(ref)}"
                    )
                mapping[key] = ref
        if discriminator.get("mapping") != mapping:
            fixed += 1
        discriminator["mapping"] = mapping
    if problems:
        raise ContractProfileError(
            "discriminators that cannot be keyed by wire value:\n  " + "\n  ".join(problems)
        )
    return fixed


#: Envelope shapes, by the property set that identifies them (§7.1 of the
#: native-client codegen design): style -> (required properties, items
#: property, the query parameters that move the cursor).
_PAGINATION_STYLES = (
    ("anchor", {"items", "next_anchor", "prev_anchor", "has_next", "has_prev"}, "items",
     ("anchor", "direction", "limit")),
    ("revision_page", {"results", "pagination", "revisions"}, "results",
     ("page", "page_size", "min_revision", "max_revision", "include_deleted")),
    ("offset", {"results", "count", "limit", "offset"}, "results", ("limit", "offset")),
    ("page", {"results", "count", "next"}, "results", ("page", "page_size")),
)


def _pagination_style(schema: dict, body: dict) -> tuple[str, str] | None:
    props = body.get("properties") or {}
    for style, required, items, _params in _PAGINATION_STYLES:
        if not required <= set(props):
            continue
        _, items_schema = _deref(schema, props[items])
        if items_schema.get("type") != "array":
            continue
        if style == "page":
            _, nxt = _deref(schema, props["next"])
            if nxt.get("type") != "integer":
                continue
        return style, items
    return None


def name_pagination(schema: dict) -> int:
    """Name the paging style of every paginated operation (``x-stapel-pagination``).

    The source of truth is the envelope the operation answers with — the
    wire shape itself; whether a ``pagination_class`` or a hand-written page
    serializer produced it does not change what a client must do. The same
    key goes on the envelope component. Returns the number of operations named.
    """
    components = (schema.get("components") or {}).get("schemas") or {}
    named = 0
    for _path, _method, operation in _operations(schema):
        for status, response in (operation.get("responses") or {}).items():
            if not status.startswith("2"):
                continue
            media = (response.get("content") or {}).get("application/json") or {}
            if "schema" not in media:
                continue
            component, body = _deref(schema, media["schema"])
            found = _pagination_style(schema, body)
            if found is None:
                continue
            style, items = found
            expected = next(p for s, _r, _i, p in _PAGINATION_STYLES if s == style)
            present = {p.get("name") for p in operation.get("parameters") or []
                       if p.get("in") == "query"}
            operation["x-stapel-pagination"] = {
                "style": style,
                "items": items,
                "params": [p for p in expected if p in present],
            }
            if component and component in components:
                components[component]["x-stapel-pagination"] = {"style": style, "items": items}
            named += 1
            break
    return named


def refuse_schemaless_success(schema: dict) -> None:
    """Profile 2 promises a body schema on every 2xx that has a body."""
    missing = []
    for path, method, operation in _operations(schema):
        for status, response in (operation.get("responses") or {}).items():
            if not status.startswith("2") or status in _BODYLESS_2XX:
                continue
            content = response.get("content") or {}
            if not content or not all("schema" in media for media in content.values()):
                missing.append(f"{method.upper()} {path} {status} ({operation.get('operationId')})")
    if missing:
        raise ContractProfileError(
            "2xx responses without a body schema (declare one, or answer 204):\n  "
            + "\n  ".join(missing)
        )


def apply_contract_profile(schema: dict, profile: int) -> dict:
    """The profile-2 post-pass over a finished drf-spectacular document.

    In order: core's flow/verification hook (``x-stapel-flows``,
    ``x-stapel-verification`` + the documented step-up 403), discriminator
    mappings keyed by wire value, pagination styles named, the refusal of
    schema-less 2xx bodies, and ``info["x-stapel-contract"]``. Security,
    per-operation error codes and captcha were attached during generation by
    stapel-core's AutoSchema under the same profile.
    """
    if profile == 1:
        return schema
    from stapel_core.django.openapi.contract import CONTRACT_PROFILE_KEY, ERRORS_KEY
    from stapel_core.django.openapi.extensions import stapel_postprocessing_hook

    schema = stapel_postprocessing_hook(schema, None, None, True)
    for _path, _method, operation in _operations(schema):
        if "x-stapel-verification" not in operation:
            continue
        challenge = (operation.get("responses") or {}).get("403")
        if challenge is not None:
            codes = set(challenge.get(ERRORS_KEY) or ())
            codes.add("error.403.verification_required")
            challenge[ERRORS_KEY] = sorted(codes)
    wire_discriminators(schema)
    name_pagination(schema)
    refuse_schemaless_success(schema)
    schema.setdefault("info", {})[CONTRACT_PROFILE_KEY] = profile
    return schema


def emit_flows(flows_json_path: Path) -> int:
    """Emit flows.json to ``flows_json_path``. Returns the flow count.

    ``generate_flow_docs`` writes flows.json plus per-flow markdown into a
    directory; we run it against a temp dir and lift out just flows.json,
    re-normalised. The SA-doc markdown rendering is a later roadmap step
    (flow-system.md §4) and is intentionally not part of the codegen artifact.
    """
    import tempfile

    from django.core.management import call_command

    with tempfile.TemporaryDirectory() as tmp:
        call_command("generate_flow_docs", "--out", tmp, verbosity=0)
        src = Path(tmp) / "flows.json"
        flows = json.loads(src.read_text()) if src.exists() else []
    flows_json_path.parent.mkdir(parents=True, exist_ok=True)
    flows_json_path.write_text(_stable_json(flows))
    return len(flows)


def emit_errors(errors_json_path: Path) -> int:
    """Emit errors.json to ``errors_json_path``. Returns the error-key count.

    ``generate_error_keys`` (stapel-core) writes a single errors.json file
    straight to the path we give it — no lifting from a directory needed, as
    with ``emit_flows``. Force-imports every app's ``errors`` module (plus
    the cross-cutting core mechanisms) before projecting the global registry,
    so the artifact does not depend on which view/serializer happened to be
    imported already.
    """
    from django.core.management import call_command

    errors_json_path.parent.mkdir(parents=True, exist_ok=True)
    call_command("generate_error_keys", "--out", str(errors_json_path), verbosity=0)
    errors = json.loads(errors_json_path.read_text())
    # Re-normalise through our own _stable_json for the same guarantee the
    # other two artifacts get, even though the command already writes a
    # stable encoding — one seam, one source of truth for "stable" here.
    errors_json_path.write_text(_stable_json(errors))
    return len(errors)


def emit_features(features_dir: Path) -> int:
    """Emit the Gherkin bundles into ``features_dir``. Returns the .feature count.

    ``generate_flow_features`` (stapel-core, flow-system.md §3) writes one
    self-consistent bundle per project language: localized ``.feature`` files
    plus the playwright-bdd step library over the codegen typed client.
    Byte-stable like the JSON artifacts — same drift-gate discipline.
    """
    from django.core.management import call_command

    call_command("generate_flow_features", "--out", str(features_dir), verbosity=0)
    return len(list(features_dir.rglob("*.feature"))) if features_dir.exists() else 0


def generate(out_dir: Path, *, contract_profile: int = 1) -> dict:
    """Emit all four artifacts into ``out_dir``. Returns a summary dict.

    With ``contract_profile=2`` a ``schema.v2.json`` is written next to the
    profile-1 ``schema.json``, which stays what existing consumers pin.
    """
    import django

    django.setup()
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = emit_schema(out_dir / "schema.json")
    if contract_profile != 1:
        emit_schema(out_dir / f"schema.v{contract_profile}.json",
                    contract_profile=contract_profile)
    flows = emit_flows(out_dir / "flows.json")
    errors = emit_errors(out_dir / "errors.json")
    features = emit_features(out_dir / "features")
    return {
        "paths": paths,
        "flows": flows,
        "errors": errors,
        "features": features,
        "out_dir": str(out_dir),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-codegen",
        description="Emit unified OpenAPI schema.json + flows.json + errors.json "
        "from an all-modules Django instance (flow-system.md §0.1).",
    )
    parser.add_argument(
        "--out",
        default="codegen/generated",
        help="Output directory for schema.json + flows.json + errors.json "
        "(default: codegen/generated)",
    )
    parser.add_argument(
        "--contract-profile",
        type=int,
        choices=CONTRACT_PROFILES,
        default=1,
        help="Also emit schema.v<N>.json in contract profile N (default: 1, "
        "schema.json only).",
    )
    args = parser.parse_args(argv)

    summary = generate(Path(args.out), contract_profile=args.contract_profile)
    print(
        f"stapel-codegen: {summary['paths']} paths, {summary['flows']} flows, "
        f"{summary['errors']} error keys, {summary['features']} feature file(s) "
        f"→ {summary['out_dir']}/",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
