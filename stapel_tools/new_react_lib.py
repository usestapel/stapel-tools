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
import json
import re
import sys
from pathlib import Path

from . import _react_templates as T

# Fallback @stapel/core peer floor when the monorepo core package.json cannot be
# read (e.g. structural unit tests without a react-dir). Mirrors the etalon's
# post-`2b1449f` policy: a fixed floor + `<1.0.0` ceiling, NOT `workspace:^`
# (which made changesets force-major the pair on an out-of-range core minor).
DEFAULT_CORE_PEER = ">=0.2.0 <1.0.0"


def render(template: str, ctx: dict) -> str:
    result = template
    for key, value in ctx.items():
        result = result.replace(f"{{{{{key}}}}}", value)
    return result


def core_peer_range(react_dir: Path) -> str:
    """`@stapel/core` peer range for a fresh pair: floor = core's CURRENT minor
    (`>=<major>.<minor>.0`), ceiling `<1.0.0`. Read from the monorepo core
    package.json at scaffold time so a pair pins the compatibility it was built
    against — the etalon fix (`2b1449f`) that stopped the changeset peer-cascade
    from force-majoring the pair. The local devDep stays `workspace:^`."""
    core_pkg = react_dir / "packages" / "core" / "package.json"
    try:
        version = json.loads(core_pkg.read_text(encoding="utf-8"))["version"]
        major, minor = version.split(".")[:2]
        return f">={int(major)}.{int(minor)}.0 <1.0.0"
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return DEFAULT_CORE_PEER


def build_context(
    module: str,
    title: str,
    backend: str,
    path_prefix: str,
    desc: str | None = None,
    core_peer: str = DEFAULT_CORE_PEER,
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
        "ERRORS_SOURCE": f"../{backend}/docs/errors.json",
        "TITLE": title,
        "DESC": desc or default_desc,
        "CORE_PEER": core_peer,
        "YEAR": str(datetime.date.today().year),
    }


def file_plan(ctx: dict) -> dict:
    """Relative path (within the package dir) -> rendered content."""
    module = ctx["MODULE"]
    plan = {
        "package.json": render(T.PACKAGE_JSON, ctx),
        "tsconfig.json": render(T.TSCONFIG, ctx),
        "tsconfig.demo.json": T.TSCONFIG_DEMO,
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
        # demo layer (first-class code: compiled, product-linted, smoke-rendered)
        "demo/_harness.tsx": render(T.HARNESS_TSX, ctx),
        f"demo/{ctx['CAMEL']}.demo.tsx": render(T.DEMO_TSX, ctx),
        # test family mirrored from the etalon (§4.2/§5.1/§2.5 + flows contract)
        "test/pair.test.ts": render(T.TEST_TS, ctx),
        "test/errorsBundle.test.ts": render(T.ERRORS_BUNDLE_TEST, ctx),
        "test/flowsContract.test.ts": render(T.FLOWS_CONTRACT_TEST, ctx),
        "test/demos.test.tsx": render(T.DEMOS_TEST_TSX, ctx),
        "test/prodBundlePurity.test.ts": render(T.PROD_BUNDLE_PURITY_TEST, ctx),
    }
    return plan


def root_gen_invocations(ctx: dict) -> list[dict]:
    """The per-driver ROOT `gen:*` wiring for a pair (delta 7). Unlike the etalon
    package.json — which owns NO `gen:*` scripts — the codegen drivers live at the
    monorepo root and are enumerated per package. Each entry is one
    env-parametrized invocation of a shared `scripts/gen-*.mjs` driver plus the
    generated path its drift gate diffs. `check_inline` marks whether the driver's
    `:check` script re-runs the driver directly (flows/errors/events/manifest) or
    via `pnpm gen:<name>` which already picks up the append (demos)."""
    pkg = ctx["PKG_DIR"]
    return [
        {
            "name": "flows",
            "cmd": f"FLOW_MODULE={ctx['MODULE']} node scripts/gen-flows.mjs",
            "path": f"packages/{pkg}/src/flows/generated",
            "check_inline": True,
        },
        {
            "name": "errors",
            "cmd": (
                f"AUTH_ERRORS_JSON=../{ctx['BACKEND']}/docs/errors.json "
                f"ERRORS_OUT=packages/{pkg}/src/i18n/generated "
                f"ERRORS_CONST={ctx['UPPER']}_ERRORS "
                f"ERRORS_TYPE_PREFIX={ctx['CAMEL']} node scripts/gen-errors.mjs"
            ),
            "path": f"packages/{pkg}/src/i18n/generated",
            "check_inline": True,
        },
        {
            "name": "events",
            "cmd": f"EVENTS_PKG_DIR=packages/{pkg} node scripts/gen-events.mjs",
            "path": f"packages/{pkg}/src/analytics/generated/events.json",
            "check_inline": True,
        },
        {
            "name": "demos",
            "cmd": f"DEMOS_PKG_DIR=packages/{pkg} node scripts/gen-demos.mjs",
            "path": f"packages/{pkg}/demo/generated",
            # gen:demos:check runs `pnpm gen:demos`, which already includes the
            # appended invocation — so the check only needs the extra diff path.
            "check_inline": False,
        },
        {
            "name": "manifest",
            "cmd": (
                f"MANIFEST_PKG_DIR=packages/{pkg} "
                f"MANIFEST_MODULE={ctx['BACKEND']} "
                f"MANIFEST_TAGPREFIX={ctx['PATH_PREFIX']} "
                f"MANIFEST_BACKEND_PYPROJECT=../{ctx['BACKEND']}/pyproject.toml "
                f"node scripts/gen-manifest.mjs"
            ),
            "path": f"packages/{pkg}/manifest.json packages/{pkg}/llms.txt",
            "check_inline": True,
        },
    ]


def _root_gen_instructions(ctx: dict) -> str:
    """Human-readable fallback: the exact root package.json edits, printed when
    the scaffold cannot patch safely (missing/renamed root, unexpected shape)."""
    lines = [
        "Wire this pair into the ROOT package.json `gen:*` scripts by hand "
        "(append to each existing script):",
    ]
    for d in root_gen_invocations(ctx):
        lines.append(f"  gen:{d['name']}        += ` && {d['cmd']}`")
        if d["check_inline"]:
            lines.append(
                f"  gen:{d['name']}:check  += ` && {d['cmd']}` before"
                f" the `git diff`, and ` {d['path']}` to its diff paths"
            )
        else:
            lines.append(
                f"  gen:{d['name']}:check  += ` {d['path']}` to its diff paths"
            )
    return "\n".join(lines)


_DIFF_SEP = " && git diff --exit-code -- "


def patch_root_gen(react_dir: Path, ctx: dict) -> tuple[bool, list[str]]:
    """Idempotently enumerate this pair in the root package.json `gen:*` scripts
    (delta 7). Append-only + guarded by a `packages/<pkg>` substring, so re-running
    is a no-op. Returns (patched_ok, changed_script_keys). On any unexpected shape
    (missing root, missing script, missing diff separator) returns (False, []) so
    the caller can fall back to printing exact instructions — never a partial edit."""
    root_pkg = react_dir / "package.json"
    try:
        data = json.loads(root_pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, []
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return False, []

    invocations = root_gen_invocations(ctx)

    # Validate every script we intend to touch exists and is well-shaped BEFORE
    # mutating anything (all-or-nothing).
    for d in invocations:
        gen_key, check_key = f"gen:{d['name']}", f"gen:{d['name']}:check"
        if gen_key not in scripts or check_key not in scripts:
            return False, []
        if _DIFF_SEP not in scripts[check_key]:
            return False, []

    # Idempotency is per invocation: the exact `cmd` guards the gen script and
    # the exact generated `path` guards the check script (a generic `packages/<pkg>`
    # substring would miss flows, whose invocation names FLOW_MODULE, not a path).
    changed: list[str] = []
    for d in invocations:
        gen_key, check_key = f"gen:{d['name']}", f"gen:{d['name']}:check"
        if d["cmd"] not in scripts[gen_key]:
            scripts[gen_key] = f"{scripts[gen_key]} && {d['cmd']}"
            changed.append(gen_key)
        if d["path"] not in scripts[check_key]:
            left, paths = scripts[check_key].split(_DIFF_SEP, 1)
            if d["check_inline"]:
                left = f"{left} && {d['cmd']}"
            scripts[check_key] = f"{left}{_DIFF_SEP}{paths} {d['path']}"
            changed.append(check_key)

    if changed:
        root_pkg.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return True, changed


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
    ctx = build_context(
        module, title, backend, path_prefix, desc,
        core_peer=core_peer_range(react_dir),
    )

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

    # Delta 7: enumerate the pair in the root gen/gen:check aggregates.
    patched, changed = patch_root_gen(react_dir, ctx)
    if patched:
        if changed:
            print(
                "\nPatched root package.json (idempotent) — enumerated the pair "
                "in the codegen drift gates:"
            )
            for key in changed:
                print(f"  {key}")
        else:
            print("\nRoot package.json already enumerates this pair (no change).")
    else:
        print(
            "\nCould not patch the root package.json automatically.\n"
            + _root_gen_instructions(ctx)
        )

    print(
        f"\nDone. Next steps (run from the stapel-react monorepo root):\n"
        f"  pnpm install\n"
        f"  pnpm gen                                    "
        f"# flows + errors + events + demos + manifest/llms.txt (all pairs)\n"
        f"  pnpm --filter {ctx['PKG_NAME']} build\n"
        f"  pnpm --filter {ctx['PKG_NAME']} lint test size\n"
        f"  pnpm lint:css                               # shared stylelint preset\n"
        f"  # then: alias {backend} schemas in api/types.ts, add model hooks,\n"
        f"  # and scaffold flow machines once {backend} annotates @flow_step.\n"
        f"  # Each new headless component needs a demo/<Name>.demo.tsx (the\n"
        f"  # completeness gate fails without one). A changeset gates the first\n"
        f"  # release: pnpm changeset\n"
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
