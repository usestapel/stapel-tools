"""
stapel-new-react-lib — scaffold a headless ``@stapel/<module>-react`` pair.

Materializes the Stapel frontend standard (docs/frontend-standard.md §2/§9 +
frontend-core-architecture.md §4 checklist) from the auth-react etalon: the
layer stack api → model → flows → headless → i18n, the `createFlowMachine`
primitive IMPORTED from `@stapel/core` (never copied), namespaced query keys,
generated backend error map with en fallbacks, and the self-describing
`manifest.json` / `llms.txt` — each generated surface under a drift gate.

Fork-free (workspace rule): the four codegen drivers already live parametrized
by env in the stapel-react monorepo (`scripts/gen-{flows,errors,manifest,api}.mjs`).
This scaffold does NOT copy them — the generated package.json wires them via env
knobs (FLOW_MODULE, ERRORS_* , MANIFEST_*). A pair owns three per-package gates
(flows / errors / manifest); `gen:api` is the core-owned shared schema.

The package is written under ``<react-dir>/packages/<module>-react``. The
backend supplies two artifact families:

- schema.json / flows.json — the UNIFIED all-modules codegen instance
  (stapel-example-monolith), the same file every pair reads (env-overridable);
- errors.py — the module's own error registry (``<react-dir>/../<backend>``).

Usage:
    stapel-new-react-lib notifications
    stapel-new-react-lib billing --backend stapel-billing --title "Billing"
    stapel-new-react-lib profiles --react-dir ~/Projects/stapel/stapel-react
"""

import argparse
import datetime
import re
import sys
from pathlib import Path

from . import _react_templates as T


def render(template: str, ctx: dict) -> str:
    result = template
    for key, value in ctx.items():
        result = result.replace(f"{{{{{key}}}}}", value)
    return result


def build_context(
    module: str,
    title: str,
    backend: str,
    path_prefix: str,
    desc: str | None = None,
) -> dict:
    camel = module.capitalize()
    default_desc = (
        f"Headless React flow pair for {backend}: typed API client, "
        "TanStack Query hooks, flow machines, headless components, and i18n "
        "keys. Zero visual opinion."
    )
    return {
        "MODULE": module,
        "CAMEL": camel,
        "UPPER": module.upper(),
        "PKG_DIR": f"{module}-react",
        "PKG_NAME": f"@stapel/{module}-react",
        "BACKEND": backend,
        "PATH_PREFIX": path_prefix,
        "ERRORS_SOURCE": f"../{backend}/errors.py",
        "TITLE": title,
        "DESC": desc or default_desc,
        "YEAR": str(datetime.date.today().year),
    }


def file_plan(ctx: dict) -> dict:
    """Relative path (within the package dir) -> rendered content."""
    module = ctx["MODULE"]
    plan = {
        "package.json": render(T.PACKAGE_JSON, ctx),
        "tsconfig.json": render(T.TSCONFIG, ctx),
        "vitest.config.ts": T.VITEST_CONFIG,
        "README.md": render(T.README_MD, ctx),
        "MODULE.md": render(T.MODULE_MD, ctx),
        "CHANGELOG.md": render(T.CHANGELOG_MD, ctx),
        "src/index.ts": render(T.INDEX_TS, ctx),
        "src/api/types.ts": render(T.API_TYPES_TS, ctx),
        f"src/api/{module}Api.ts": render(T.API_CLIENT_TS, ctx),
        "src/api/extensions.ts": render(T.API_EXTENSIONS_TS, ctx),
        "src/model/queryKeys.ts": render(T.QUERY_KEYS_TS, ctx),
        "src/model/runtime.ts": render(T.RUNTIME_TS, ctx),
        "src/model/context.tsx": render(T.CONTEXT_TSX, ctx),
        "src/flows/errors.ts": render(T.FLOW_ERRORS_TS, ctx),
        f"src/headless/{ctx['CAMEL']}Provider.tsx": render(T.PROVIDER_TSX, ctx),
        "src/i18n/keys.ts": render(T.I18N_KEYS_TS, ctx),
        "src/i18n/errorsMap.ts": render(T.ERRORS_MAP_TS, ctx),
        "test/pair.test.ts": render(T.TEST_TS, ctx),
    }
    return plan


def scaffold_react_lib(
    module: str,
    title: str,
    react_dir: Path,
    backend: str | None = None,
    path_prefix: str | None = None,
    desc: str | None = None,
) -> Path:
    backend = backend or f"stapel-{module}"
    path_prefix = path_prefix or f"/{module}/api/"
    ctx = build_context(module, title, backend, path_prefix, desc)

    packages_dir = react_dir / "packages"
    target = packages_dir / ctx["PKG_DIR"]
    if target.exists():
        print(f"Error: {target} already exists", file=sys.stderr)
        sys.exit(1)

    print(f"Creating pair {ctx['PKG_NAME']} in {packages_dir}/")
    for rel, content in file_plan(ctx).items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"  created {rel}")

    print(
        f"\nDone. Next steps (run from the stapel-react monorepo root):\n"
        f"  pnpm install\n"
        f"  pnpm --filter {ctx['PKG_NAME']} gen        "
        f"# flows registry + error map + manifest/llms.txt\n"
        f"  pnpm --filter {ctx['PKG_NAME']} build\n"
        f"  pnpm --filter {ctx['PKG_NAME']} lint test size\n"
        f"  # then: alias {backend} schemas in api/types.ts, add model hooks,\n"
        f"  # and scaffold flow machines once {backend} annotates @flow_step.\n"
        f"  # A changeset gates the first release: pnpm changeset\n"
    )
    return target


def _default_react_dir() -> Path:
    """Prefer CWD if it is a stapel-react monorepo, else a sibling stapel-react."""
    cwd = Path.cwd()
    if (cwd / "pnpm-workspace.yaml").exists() and (cwd / "packages").is_dir():
        return cwd
    sibling = cwd / "stapel-react"
    if sibling.is_dir():
        return sibling
    return cwd


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "module",
        help="Module slug, single lowercase word, e.g. 'notifications' "
        "-> @stapel/notifications-react",
    )
    parser.add_argument("--title", help="Display name, e.g. 'Notifications'")
    parser.add_argument(
        "--backend",
        help="Backend module repo/name (default: stapel-<module>) — source of "
        "errors.py and the manifest backend id",
    )
    parser.add_argument(
        "--path-prefix",
        help="OpenAPI path prefix for the module's operations "
        "(default: /<module>/api/)",
    )
    parser.add_argument(
        "--react-dir",
        type=Path,
        default=None,
        help="stapel-react monorepo root (default: CWD if it is one, else "
        "./stapel-react)",
    )
    args = parser.parse_args()

    module = args.module.removeprefix("@stapel/").removesuffix("-react")
    if not re.fullmatch(r"[a-z][a-z0-9]*", module):
        print(
            "Error: module must be a single lowercase word ([a-z][a-z0-9]*) — "
            "the codegen registry/type names derive from it directly, so dashes "
            "are not supported.",
            file=sys.stderr,
        )
        sys.exit(1)

    title = args.title or module.capitalize()
    react_dir = args.react_dir or _default_react_dir()
    scaffold_react_lib(
        module,
        title,
        react_dir,
        backend=args.backend,
        path_prefix=args.path_prefix,
    )


if __name__ == "__main__":
    main()
