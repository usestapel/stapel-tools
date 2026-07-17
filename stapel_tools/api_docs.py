"""stapel-docs — bilingual API/flow documentation (§57-family owner
directive: "документация по api/флоу — в идеале двуязычная").

Renders ``docs/api.en.md`` + ``docs/api.ru.md`` at the project root from
artifacts every module already ships (`_docgen_scan.discover_modules`):

  - ``schema.json``   — endpoints (method/path) + DTO fields, the field
    descriptions sourced from the backend's own docstrings (R004 canon —
    drf-spectacular lifts a `@dataclass` DTO's docstring/field metadata
    straight into the OpenAPI doc, so this command duplicates nothing by
    hand);
  - ``flows.json``    — flow name, description, actors, user-story steps
    (`generate_flow_docs`'s machine artifact, flow-system.md §2);
  - ``errors.json``   — every `error.<status>.<name>` code this module can
    raise, English text (stapel-core's `generate_error_keys`).

Bilingual: modules already ship translated flows/errors (the
stapel-translate precedent — `translations/flows.ru.json` /
`translations/errors.ru.json`, keyed by the SAME `*_key`/`code` fields
flows.json/errors.json use). Where a Russian string exists, it's used
verbatim; where it does not (yet), the English text is shown with an
honest `(en)` marker — never fabricated, matching the stapel-translate
precedent for partial coverage. Endpoint summaries and DTO field
descriptions have no Russian source ANYWHERE today (they come straight from
English docstrings) — every such cell in the Russian doc is marked `(en)`
for the same reason.

``--check`` is the pre-commit drift gate: regenerate into memory, diff
against the committed files, fail (exit 1) on any divergence — the same
contract as `stapel-config-manifest`/`stapel-reserved-paths`.

A project with no discoverable schema.json (nothing generated yet) is a
graceful no-op, exit 0 — nothing to document, not an error.

Exit codes: 0 clean/no-op, 1 check-mode drift, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ._docgen_scan import ModuleDocs, discover_modules

DOCS_EN = "docs/api.en.md"
DOCS_RU = "docs/api.ru.md"

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


@dataclass(frozen=True)
class DtoField:
    name: str
    type: str
    required: bool
    description: str


@dataclass(frozen=True)
class EndpointDoc:
    method: str
    path: str
    summary: str
    description: str
    request_fields: tuple[DtoField, ...]
    response_fields: tuple[DtoField, ...]


def _load_json(path: Optional[Path]) -> Any:
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_ref(schema: dict, ref: str) -> dict:
    node: Any = schema
    for part in ref.lstrip("#/").split("/"):
        if not isinstance(node, dict):
            return {}
        node = node.get(part, {})
    return node if isinstance(node, dict) else {}


def _first_ref(node: Any) -> Optional[str]:
    if not isinstance(node, dict):
        return None
    if "$ref" in node:
        return node["$ref"]
    for key in ("allOf", "oneOf", "anyOf"):
        for item in node.get(key) or ():
            ref = _first_ref(item)
            if ref:
                return ref
    return None


def _type_name(prop: dict) -> str:
    if "$ref" in prop:
        return prop["$ref"].rsplit("/", 1)[-1]
    if prop.get("type") == "array":
        items = prop.get("items", {})
        return f"{_type_name(items)}[]"
    return str(prop.get("type", "any"))


def _dto_fields(schema: dict, content: Optional[dict]) -> tuple[DtoField, ...]:
    if not content:
        return ()
    node = content.get("application/json", {}).get("schema", {})
    ref = _first_ref(node)
    resolved = _resolve_ref(schema, ref) if ref else node
    props = resolved.get("properties", {}) if isinstance(resolved, dict) else {}
    required = set(resolved.get("required", []) if isinstance(resolved, dict) else ())
    return tuple(
        DtoField(
            name=name,
            type=_type_name(prop),
            required=name in required,
            description=str(prop.get("description", "")),
        )
        for name, prop in props.items()
    )


def endpoints_from_schema(schema: dict) -> list[EndpointDoc]:
    """One :class:`EndpointDoc` per method/path in *schema*'s ``paths`` —
    summary/description straight from the operation object (R004 docstring
    canon), DTO fields resolved from the request body / first 2xx
    response's ``application/json`` schema (one `$ref`/`allOf` hop; nested
    refs beyond that are rendered by name, not expanded — good enough for
    human docs, not a full JSON-schema resolver)."""
    out: list[EndpointDoc] = []
    for path, methods in sorted((schema.get("paths") or {}).items()):
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(op, dict):
                continue
            description = str(op.get("description") or "")
            summary = str(op.get("summary") or "")
            if not summary and description.strip():
                summary = description.strip().splitlines()[0]
            req_content = (op.get("requestBody") or {}).get("content")
            request_fields = _dto_fields(schema, req_content)
            response_fields: tuple[DtoField, ...] = ()
            for code in ("200", "201"):
                resp = (op.get("responses") or {}).get(code)
                if resp and resp.get("content"):
                    response_fields = _dto_fields(schema, resp["content"])
                    break
            out.append(EndpointDoc(
                method=method.upper(), path=path, summary=summary,
                description=description, request_fields=request_fields,
                response_fields=response_fields,
            ))
    return out


def _ru_or_en(key: Optional[str], en_text: str, ru_map: Optional[dict]) -> tuple[str, str]:
    """(text, marker) — the Russian string if *ru_map* has *key*, else the
    English text with an honest ``" (en)"`` marker (never faked)."""
    if key and ru_map and key in ru_map:
        return ru_map[key], ""
    return en_text, " (en)" if en_text else ""


def _group_by_submodule(
    mod: ModuleDocs, flows: list[dict], endpoints: list[EndpointDoc],
) -> list[tuple[str, list[dict], list[EndpointDoc]]]:
    """[(submodule_name, flows, endpoints), ...] — a single group named
    after *mod* for a per-module slice; grouped by first path/id segment
    for the monolith aggregate (one schema/flows pair spanning every
    installed module)."""
    if not mod.is_aggregate:
        return [(mod.name, flows, endpoints)]
    groups: dict[str, tuple[list[dict], list[EndpointDoc]]] = {}
    for flow in flows:
        sub = (flow.get("id") or "").split(".", 1)[0] or mod.name
        groups.setdefault(sub, ([], []))[0].append(flow)
    for ep in endpoints:
        sub = ep.path.strip("/").split("/", 1)[0] or mod.name
        groups.setdefault(sub, ([], []))[1].append(ep)
    return [(name, fs, eps) for name, (fs, eps) in sorted(groups.items())]


def _fields_table(fields: tuple[DtoField, ...], *, ru: bool) -> list[str]:
    if not fields:
        return []
    field_h = "Field" if not ru else "Поле"
    type_h = "Type" if not ru else "Тип"
    req_h = "Required" if not ru else "Обязательное"
    desc_h = "Description" if not ru else "Описание (en)"
    lines = [
        "", f"| {field_h} | {type_h} | {req_h} | {desc_h} |",
        "|---|---|---|---|",
    ]
    for f in fields:
        req = ("yes" if not ru else "да") if f.required else ("no" if not ru else "нет")
        lines.append(f"| `{f.name}` | `{f.type}` | {req} | {f.description} |")
    lines.append("")
    return lines


def render_doc(modules: list[ModuleDocs], *, lang: str, project_name: str) -> str:
    ru = lang == "ru"
    lines = [f"# API — {project_name} ({lang})", ""]
    lines.append(
        "Сгенерировано `stapel-docs` из schema.json + flows.json + errors.json "
        "— не редактировать руками; регенерировать `stapel-docs .`; "
        "`stapel-docs . --check` — гейт дрейфа в pre-commit."
        if ru else
        "Generated by `stapel-docs` from schema.json + flows.json + errors.json "
        "— do not hand-edit; regenerate with `stapel-docs .`; "
        "`stapel-docs . --check` is the pre-commit drift gate."
    )
    lines.append("")
    if ru:
        lines += [
            "Русский текст — из `translations/flows.ru.json` / "
            "`translations/errors.ru.json` модулей, где перевод уже есть; "
            "пометка `(en)` — честный пробел (перевода пока нет, показан "
            "английский текст), не выдумка.",
            "",
        ]

    if not modules:
        lines.append("*(no modules with a committed schema.json found)*" if not ru
                      else "*(ни одного модуля с committed schema.json не найдено)*")
        return "\n".join(lines).rstrip() + "\n"

    for mod in modules:
        schema = _load_json(mod.schema_json) or {}
        flows = _load_json(mod.flows_json) or []
        errors = _load_json(mod.errors_json) or []
        ru_flows = _load_json(mod.flows_ru_json)
        ru_errors = _load_json(mod.errors_ru_json)
        endpoints = endpoints_from_schema(schema)

        for sub_name, sub_flows, sub_endpoints in _group_by_submodule(mod, flows, endpoints):
            lines.append(f"## {sub_name}")
            lines.append("")

            if sub_flows:
                lines.append("### Флоу" if ru else "### Flows")
                lines.append("")
                for flow in sub_flows:
                    title, title_m = (
                        _ru_or_en(flow.get("title_key"), flow.get("title", ""), ru_flows)
                        if ru else (flow.get("title", ""), "")
                    )
                    lines.append(f"#### {title}{title_m}")
                    lines.append("")
                    desc, desc_m = (
                        _ru_or_en(flow.get("description_key"), flow.get("description", ""), ru_flows)
                        if ru else (flow.get("description", ""), "")
                    )
                    if desc:
                        lines.append(f"{desc}{desc_m}")
                        lines.append("")
                    actors = flow.get("actors") or []
                    if actors:
                        label = "Акторы" if ru else "Actors"
                        lines.append(f"**{label}:** {', '.join(actors)}")
                        lines.append("")
                    steps = flow.get("steps") or []
                    if steps:
                        label = "Шаги" if ru else "Steps"
                        lines.append(f"**{label}:**")
                        lines.append("")
                        for step in steps:
                            note, note_m = (
                                _ru_or_en(step.get("note_key"), step.get("note", ""), ru_flows)
                                if ru else (step.get("note", ""), "")
                            )
                            eps = step.get("endpoints") or []
                            ep_str = (
                                " (" + ", ".join(f"{e['method']} {e['path']}" for e in eps) + ")"
                                if eps else ""
                            )
                            order = step.get("order", 0)
                            kind = step.get("kind", "")
                            lines.append(f"{order + 1}. [{kind}] {note}{note_m}{ep_str}")
                        lines.append("")

            if sub_endpoints:
                lines.append("### Эндпоинты" if ru else "### Endpoints")
                lines.append("")
                for ep in sub_endpoints:
                    lines.append(f"#### `{ep.method} {ep.path}`")
                    lines.append("")
                    summary = ep.summary
                    if summary:
                        if ru and summary:
                            summary = f"{summary} (en)"
                        lines.append(summary)
                        lines.append("")
                    if ep.request_fields:
                        label = "Поля запроса" if ru else "Request fields"
                        lines.append(f"**{label}:**")
                        lines += _fields_table(ep.request_fields, ru=ru)
                    if ep.response_fields:
                        label = "Поля ответа" if ru else "Response fields"
                        lines.append(f"**{label}:**")
                        lines += _fields_table(ep.response_fields, ru=ru)

        if errors:
            note = (
                " *(коды не привязаны к конкретному модулю — единый агрегат)*"
                if mod.is_aggregate and ru else
                " *(codes are not attributable to one module — unified aggregate)*"
                if mod.is_aggregate else ""
            )
            lines.append(("### Ошибки" if ru else "### Errors") + note)
            lines.append("")
            code_h = "Code" if not ru else "Код"
            status_h = "Status" if not ru else "Статус"
            rem_h = "Remediation" if not ru else "Remediation"
            msg_h = "Message" if not ru else "Сообщение"
            lines.append(f"| {code_h} | {status_h} | {rem_h} | {msg_h} |")
            lines.append("|---|---|---|---|")
            for err in sorted(errors, key=lambda e: e.get("code", "")):
                code = err.get("code", "")
                msg = err.get("en", "")
                marker = ""
                if ru:
                    ru_msg = (ru_errors or {}).get(code)
                    if ru_msg:
                        msg = ru_msg
                    else:
                        marker = " (en)"
                lines.append(f"| `{code}` | {err.get('status', '')} | {err.get('remediation', '')} | {msg}{marker} |")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def run(project_dir: Path, *, check: bool = False) -> int:
    modules = discover_modules(project_dir)
    if not modules:
        print(
            "stapel-docs: no committed schema.json found (docs/schema.json, "
            "codegen/generated/schema.json, or a per-module <mod>/api/v1/"
            "schema.json) — no-op."
        )
        return 0

    project_name = project_dir.resolve().name or "project"
    rendered = {
        DOCS_EN: render_doc(modules, lang="en", project_name=project_name),
        DOCS_RU: render_doc(modules, lang="ru", project_name=project_name),
    }

    if check:
        drifted = []
        for rel, text in rendered.items():
            path = project_dir / rel
            existing = path.read_text(encoding="utf-8") if path.is_file() else None
            if existing != text:
                drifted.append(rel)
        if drifted:
            print(
                "stapel-docs --check: drift — regenerate with `stapel-docs .` "
                "and commit:\n  " + "\n  ".join(drifted),
                file=sys.stderr,
            )
            return 1
        print(f"stapel-docs --check: {len(modules)} module(s), docs up to date.")
        return 0

    for rel, text in rendered.items():
        path = project_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"stapel-docs: wrote {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-docs",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_dir", nargs="?", default=".", help="Project directory (default: .).")
    parser.add_argument(
        "--check", action="store_true",
        help="Do not write — exit 1 if regenerating would change docs/api.en.md "
             "or docs/api.ru.md (drift gate for pre-commit).",
    )
    args = parser.parse_args(argv)
    return run(Path(args.project_dir), check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
