"""Two contract gates every brick's test suite calls on itself.

``invalid_examples(schema)``
    Every example in an emitted OpenAPI document (``docs/schema.json``,
    ``docs/schema.v2.json``) validates against the schema it illustrates. A
    generated client and a generated test both read examples as facts: a
    native client's suite found 16 of 72 invalid in one brick (a partial
    object, ``"True"`` for a boolean, a required field missing because the
    docstring reader dropped a falsy example). Needs ``jsonschema`` (a
    dependency of drf-spectacular, so present wherever a schema is emitted).

``dead_action_declarations(resolver=None)``
    An ``@action(permission_classes=...)`` is applied only when a router
    builds the route; a viewset mounted with ``as_view({...})`` never reads
    the action's kwargs, so the declaration is text that reads like a gate.
    Walks every mounted viewset route and returns the declarations whose
    classes the mount does not run (a stricter mount is not dead).

Usage in a brick (``tests/test_contract_examples.py``)::

    from stapel_tools.contract_gates import invalid_examples
    bad, checked = invalid_examples(json.loads(path.read_text()))
    assert checked >= N and bad == {}
"""
from __future__ import annotations

import copy
from typing import Any, Iterator

__all__ = ["dead_action_declarations", "examples", "invalid_examples", "viewset_routes"]


def _openapi30_to_jsonschema(node: Any) -> Any:
    """``nullable: true`` is OpenAPI 3.0; JSON Schema spells it as a null branch."""
    if isinstance(node, list):
        return [_openapi30_to_jsonschema(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {k: _openapi30_to_jsonschema(v) for k, v in node.items() if k != "nullable"}
    if node.get("nullable"):
        return {"anyOf": [out, {"type": "null"}]}
    return out


def examples(schema: dict) -> Iterator[tuple[tuple[str, str, str, str], dict, Any]]:
    """``((method, path, where, name), media schema, value)`` for every example.

    *where* is ``"request"`` or the response status; *name* is the example's
    key, or ``"example"`` for a media-level single example.
    """
    for path, item in (schema.get("paths") or {}).items():
        for method, op in item.items():
            if not isinstance(op, dict):
                continue
            body = (op.get("requestBody") or {}).get("content") or {}
            medias = [("request", m) for m in body.values()]
            for status, response in (op.get("responses") or {}).items():
                medias += [(status, m) for m in (response.get("content") or {}).values()]
            for where, media in medias:
                if "schema" not in media:
                    continue
                if "example" in media:
                    yield (method, path, where, "example"), media["schema"], media["example"]
                for name, ex in (media.get("examples") or {}).items():
                    if isinstance(ex, dict) and "value" in ex:
                        yield (method, path, where, name), media["schema"], ex["value"]


def invalid_examples(schema: dict) -> tuple[dict[tuple[str, str, str, str], list[str]], int]:
    """``({(method, path, where, name): [error, ...]}, number of examples checked)``.

    The count is returned so a caller can assert the walk saw its subject:
    an empty result over zero examples proves nothing.
    """
    from jsonschema import Draft202012Validator

    components = _openapi30_to_jsonschema(copy.deepcopy(schema.get("components") or {}))
    bad: dict[tuple[str, str, str, str], list[str]] = {}
    checked = 0
    for key, media_schema, value in examples(schema):
        checked += 1
        root = {**_openapi30_to_jsonschema(media_schema), "components": components}
        errors = [
            f"{list(e.absolute_path)}: {e.message[:160]}"
            for e in Draft202012Validator(root).iter_errors(value)
        ]
        if errors:
            bad[key] = errors
    return bad, checked


_ACTION_ONLY_KWARGS = {
    "permission_classes": "get_permissions",
    "authentication_classes": "get_authenticators",
    "throttle_classes": "get_throttles",
}


def _viewset_routes(resolver, prefix: str = ""):
    for pattern in resolver.url_patterns:
        if hasattr(pattern, "url_patterns"):
            yield from _viewset_routes(pattern, prefix + str(pattern.pattern))
            continue
        callback = pattern.callback
        actions = getattr(callback, "actions", None)
        cls = getattr(callback, "cls", None)
        if actions and cls is not None:
            yield prefix + str(pattern.pattern), callback, cls, actions


def viewset_routes(resolver=None):
    """``(route, callback, view class, {method: action})`` per mounted viewset route."""
    if resolver is None:
        from django.urls import get_resolver

        resolver = get_resolver()
    return list(_viewset_routes(resolver))


def _effective_classes(callback, cls, name: str, kwarg: str) -> set:
    view = cls(**(getattr(callback, "initkwargs", {}) or {}))
    view.action = name
    view.action_map = getattr(callback, "actions", {})
    return {type(obj) for obj in getattr(view, _ACTION_ONLY_KWARGS[kwarg])()}


def dead_action_declarations(resolver=None) -> list[tuple[str, str, str]]:
    """``[(route, "Class.action", kwarg)]`` whose declared classes do not all run."""
    dead = []
    for route, callback, cls, actions in viewset_routes(resolver):
        for name in set(actions.values()):
            declared = getattr(getattr(cls, name, None), "kwargs", {}) or {}
            for kwarg in _ACTION_ONLY_KWARGS:
                if kwarg not in declared:
                    continue
                if not set(declared[kwarg]) <= _effective_classes(callback, cls, name, kwarg):
                    dead.append((route, f"{cls.__name__}.{name}", kwarg))
    return sorted(set(dead))
