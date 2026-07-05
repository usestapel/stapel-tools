"""
stapel-codegen — emit the backend artifacts that drive the frontend codegen.

Design: docs/flow-system.md §0.1 + "Порядок реализации" п.1. The codegen source
is a single *all-modules* Django instance (e.g. stapel-example-monolith on
sqlite). From it we emit two language-agnostic artifacts:

  - schema.json  — the unified drf-spectacular OpenAPI for every installed
    module (the same document the live instance serves at ``/schema/``). It is
    produced offline via the ``spectacular`` management command rather than over
    HTTP: no running server, no port, byte-stable output — exactly what a drift
    gate needs. The runtime ``/schema/`` endpoint stays available for the
    frontend dev loop; it is the same generator, same SPECTACULAR_SETTINGS.

  - flows.json   — the ``generate_flow_docs`` machine artifact (flows +
    endpoint bindings), language-agnostic per flow-system.md §2.

Both are re-normalised to a stable JSON encoding (sorted-free, 2-space indent,
trailing newline) so that regenerating without a code change yields zero diff.

This module is the reusable *mechanism*. It must run inside an already-configured
Django instance: the caller sets ``DJANGO_SETTINGS_MODULE`` (and PYTHONPATH) to
point at the all-modules instance. The concrete instance + orchestration live in
stapel-example-monolith (``core.settings.codegen`` + ``codegen/generate.sh``).

Usage (from the monolith service dir):
    DJANGO_ENV=local DJANGO_SETTINGS_MODULE=core.settings.codegen \\
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


def emit_schema(out_path: Path) -> int:
    """Emit the unified OpenAPI schema to ``out_path``. Returns the path count."""
    from django.core.management import call_command

    buf = io.StringIO()
    # openapi-json → a single JSON document covering every mounted module.
    call_command("spectacular", "--format", "openapi-json", stdout=buf)
    schema = json.loads(buf.getvalue())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_stable_json(schema))
    return len(schema.get("paths", {}))


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


def generate(out_dir: Path) -> dict:
    """Emit both artifacts into ``out_dir``. Returns a summary dict."""
    import django

    django.setup()
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = emit_schema(out_dir / "schema.json")
    flows = emit_flows(out_dir / "flows.json")
    return {"paths": paths, "flows": flows, "out_dir": str(out_dir)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-codegen",
        description="Emit unified OpenAPI schema.json + flows.json from an "
        "all-modules Django instance (flow-system.md §0.1).",
    )
    parser.add_argument(
        "--out",
        default="codegen/generated",
        help="Output directory for schema.json + flows.json "
        "(default: codegen/generated)",
    )
    args = parser.parse_args(argv)

    summary = generate(Path(args.out))
    print(
        f"stapel-codegen: {summary['paths']} paths, {summary['flows']} flows "
        f"→ {summary['out_dir']}/",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
