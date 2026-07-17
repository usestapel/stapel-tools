"""stapel-reserved-paths — regenerate/verify a generated project's
``reserved-paths.json``.

Schema (agreed with ``@stapel/eslint-plugin``'s ``no-reserved-backend-route``
rule — stapel-react/packages/eslint-plugin/lib/data.js
``loadReservedPathCatalog`` + that package's own README "reserved-paths.json"
section; do not change without updating both sides)::

    {"reservedPathPrefixes": ["/admin", "/staticfiles", "/media",
                              "/<mod>/api", "/<mod>/swagger", ...]}

This is the SAME projection ``create_project._write_reserved_paths_json``
writes at generation time (nginx-local/prod-nginx/Vite all render their
backend location blocks from the sibling, richer
``create_project._reserved_paths_manifest`` — this file is the flat subset
of that manifest the frontend lint rule reads). A bare module root
(``/<mod>``) must never appear — roots belong to the frontend SPA by canon
(the "/calendar page vs backend" collision this whole mechanism exists to
prevent).

``--check`` is the pre-commit drift gate (``reserved-paths-check``, wired
into a monolith's ``.pre-commit-config.yaml`` next to
``config-manifest-check``/``presenter-catalog-check``): it recovers the
module set already committed in the file (its ``admin``/``staticfiles``/
``media`` fixed entries are left alone), then re-renders each module's
sub-surfaces from stapel-tools' CURRENT definition. Drift here means
stapel-tools' own sub-surface list changed since this project was generated
or last regenerated — NOT a full lib-selection re-derivation (that is
``stapel-assemble``'s re-stamp scope; the project's actual INSTALLED_APPS
selection is not reliably recoverable from a generated project's files
across every install mode (pip vs git submodule), so this tool deliberately
does not attempt it — the same "recover from what's already committed"
discipline ``config_manifest.libs_from_existing_config_md`` uses).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RESERVED_PATHS_JSON = "reserved-paths.json"

# Framework-wide, independent of any lib selection — never touched by
# regeneration (see create_project._reserved_paths_manifest's "fixed" list).
_FIXED = ("/admin", "/staticfiles", "/media")

# Sub-surfaces our canon's generic per-service URLconf (URLS_PY) mounts under
# a lib's own prefix — see create_project._MODULE_SUB_SURFACES (the two
# constants are kept in sync by hand today; a future refactor could import
# one from the other, but stapel-tools' own create_project module carries
# import-time side effects this standalone CLI doesn't want to pull in).
_MODULE_SUB_SURFACES = ("api", "swagger", "schema.json", "admin")


def modules_from_existing(prefixes: list[str]) -> list[str]:
    """The module first-segments already reserved in a committed
    ``reservedPathPrefixes`` list (fixed entries excluded), in first-seen
    order. This is the recovery step — see the module docstring for why it
    reads the committed file rather than re-deriving a project's lib
    selection from scratch."""
    mods: list[str] = []
    for p in prefixes:
        if p in _FIXED:
            continue
        mod = p.lstrip("/").split("/", 1)[0]
        if mod and mod not in mods:
            mods.append(mod)
    return mods


def regenerate(prefixes: list[str]) -> list[str]:
    """Re-render ``reservedPathPrefixes`` from the module set already
    committed in *prefixes*, against stapel-tools' CURRENT sub-surface
    definition."""
    out = list(_FIXED)
    for mod in modules_from_existing(prefixes):
        for sub in _MODULE_SUB_SURFACES:
            entry = f"/{mod}/{sub}"
            if entry not in out:
                out.append(entry)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-reserved-paths",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "project_dir", nargs="?", default=".",
        help="Project directory (default: .) — must already have a "
             "reserved-paths.json (minimal/microservices projects don't "
             "ship one; nothing to do there).",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Do not write — exit 1 if regenerating would change the file "
             "(drift: stapel-tools' sub-surface definition changed since "
             "this project was generated/last regenerated). Exit 0 when "
             "already up to date, or when there is no reserved-paths.json.",
    )
    args = parser.parse_args(argv)

    path = Path(args.project_dir) / RESERVED_PATHS_JSON
    if not path.exists():
        print(
            "stapel-reserved-paths: no reserved-paths.json here — nothing "
            "to do (not a monolith-with-frontend project?)"
        )
        return 0

    try:
        current = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        print(f"stapel-reserved-paths: {path} is not valid JSON: {exc}", file=sys.stderr)
        return 1

    existing = current.get("reservedPathPrefixes")
    if not isinstance(existing, list):
        print(
            f"stapel-reserved-paths: {path} has no 'reservedPathPrefixes' "
            "array — malformed, refusing to guess",
            file=sys.stderr,
        )
        return 1

    rendered = json.dumps({"reservedPathPrefixes": regenerate(existing)}, indent=2) + "\n"

    if args.check:
        if path.read_text() != rendered:
            print(
                "stapel-reserved-paths: reserved-paths.json is stale — run "
                "`stapel-reserved-paths .` (no --check) and commit the result.",
                file=sys.stderr,
            )
            return 1
        print("stapel-reserved-paths: reserved-paths.json is up to date")
        return 0

    path.write_text(rendered)
    print("stapel-reserved-paths: wrote reserved-paths.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
