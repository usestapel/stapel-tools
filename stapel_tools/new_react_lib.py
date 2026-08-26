"""
stapel-new-react-lib — scaffold a headless ``@stapel/<module>-react`` pair.

Materializes the Stapel frontend standard (docs/frontend-standard.md §2/§9 +
frontend-core-architecture.md §4 checklist) from the auth-react etalon: the
layer stack api → model → flows → headless → i18n, the `createFlowMachine`
primitive IMPORTED from `@stapel/core` (never copied), namespaced query keys,
generated backend error map with en fallbacks, and the self-describing
`manifest.json` / `llms.txt` — each generated surface under a drift gate.

Fork-free (workspace rule): the codegen drivers already live parametrized
by env in the stapel-react monorepo (`scripts/gen-{flows,errors,events,manifest,api}.mjs`).
This scaffold does NOT copy them — the generated package.json wires them via env
knobs (FLOW_MODULE, ERRORS_*, EVENTS_*, MANIFEST_*, API_*). `schema.ts` is
package-LOCAL per pair now (each pair's own `src/api/generated/schema.ts`,
produced from ITS backend's own `docs/schema.json` — no longer core-owned), so
`gen:api` is enumerated per pair in the root `gen:*` aggregates exactly like the
other drivers.

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

# The minor of `@stapel/core` that first re-exported the flow-machine primitive
# (`createFlowMachine`/`useFlow`) the pair re-exports from its `src/index.ts`.
# EVERY scaffolded pair re-exports it (see _react_templates INDEX_TS), so the
# peer floor can never sit below this minor no matter how old the monorepo core
# is — the pair would import a symbol that does not exist there.
FLOW_PRIMITIVE_MIN_CORE = (0, 3)  # createFlowMachine appeared in @stapel/core 0.3.0

# The minor that ships the slim-wave §21 module factories the scaffolded
# model/headless layer is a thin binding of (S2: createModuleRuntime /
# createModuleContext) plus the one-provider setup the README wires (S4:
# <StapelProvider>) — the core minor AFTER 0.3.0 (changesets pending at the
# time of the wave; the monorepo package.json still reads 0.3.0, which is why
# this floor exists instead of trusting the read version alone).
MODULE_FACTORY_MIN_CORE = (0, 4)

# The hard minimum for any scaffolded pair: the newest primitive it binds.
_PAIR_MIN_CORE = max(FLOW_PRIMITIVE_MIN_CORE, MODULE_FACTORY_MIN_CORE)

# Fallback @stapel/core peer floor when the monorepo core package.json cannot be
# read (e.g. structural unit tests without a react-dir). Mirrors the etalon's
# post-`2b1449f` policy: a fixed floor + `<1.0.0` ceiling, NOT `workspace:^`
# (which made changesets force-major the pair on an out-of-range core minor).
# The floor is the module-factory minor, not core's very first minor: a pair
# whose runtime/context/provider bind createModuleRuntime/createModuleContext
# cannot honestly claim an older core.
DEFAULT_CORE_PEER = ">=0.4.0 <1.0.0"

# ── the default skin (§54) ───────────────────────────────────────────────────
# The audit's G2: every freshly scaffolded pair used to be "headless shipped,
# feature not shipped" — no `src/default/`, no `./default` export, no antd peer,
# so a host got a bag of state and had to draw every screen itself. The skin is
# therefore ON by default and `--no-skin` is the opt-out (a genuinely headless
# pair — billing, calendar, recordings — is the exception, and an exception has
# to be asked for).
#
# `SkinTheme` (`@stapel/tokens-antd/skin`) is the ONE themed surface: it bridges
# the §68 neutral role dictionary onto an antd `ConfigProvider` and follows the
# host's mode. A pair that mounts its own `ConfigProvider` forks the bridge; a
# pair that defaults `mode="light"` ignores a dark host. Both are why the floor
# below is the minor that ships `SkinTheme`.
TOKENS_ANTD_PEER = ">=0.6.0"
ANTD_PEER = ">=5.20.0 <7"

# Icon names the shell's registry resolves (`shell-react/src/default/icons.tsx`).
# An unknown name renders a generic glyph with no error, so the scaffold picks
# from this set and `_frontend_templates.NAV_ICON_REGISTRY` refuses anything
# outside it at generation time.
SCAFFOLD_NAV_ICON = "AppstoreOutlined"


def render(template: str, ctx: dict) -> str:
    """Substitute `{{KEY}}` tokens until the text stops changing.

    A single pass is not enough since the skin fragments landed: a fragment is
    itself a template (`SKIN_I18N_EN` carries `{{MODULE}}` lines), and a
    single-pass renderer would substitute it AFTER `MODULE` had already been
    replaced, leaving `{{MODULE}}` in the generated file. Looping to a fixed
    point makes the fragments composable regardless of dict order; the bound
    stops a fragment that (wrongly) reproduced its own token from spinning."""
    result = template
    for _ in range(5):
        before = result
        for key, value in ctx.items():
            result = result.replace(f"{{{{{key}}}}}", value)
        if result == before:
            break
    return result


def core_peer_range(react_dir: Path) -> str:
    """`@stapel/core` peer range for a fresh pair: floor = **max** of the
    pair-primitive minor (`_PAIR_MIN_CORE` — the newest core primitive the
    scaffold binds: the `createFlowMachine`/`useFlow` re-export, 0.3.0, and the
    slim-wave `createModuleRuntime`/`createModuleContext`/`<StapelProvider>`
    surface, 0.4.0) and core's CURRENT minor read from the monorepo
    package.json; ceiling `<1.0.0`.

    Reading the current minor pins the compatibility the pair was built against
    (the etalon fix `2b1449f` that stopped the changeset peer-cascade force-major);
    clamping up to the pair-primitive minor stops the scaffold emitting a floor
    below the minor where a bound primitive actually appeared — a floor that
    would let the pair install against a core missing the symbol.
    The local devDep stays `workspace:^`."""
    floor = _PAIR_MIN_CORE
    core_pkg = react_dir / "packages" / "core" / "package.json"
    try:
        version = json.loads(core_pkg.read_text(encoding="utf-8"))["version"]
        major, minor = (int(part) for part in version.split(".")[:2])
        floor = max(floor, (major, minor))
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        pass  # keep the pair-primitive floor — the hard minimum for any pair
    return f">={floor[0]}.{floor[1]}.0 <1.0.0"


def module_flow_count(react_dir: Path, module: str, flows_json: Path | None = None) -> int:
    """Number of flows the module OWNS, read from the unified `flows.json` the
    pair's `gen:flows` driver reads (`stapel-example-monolith` codegen output, a
    sibling of the monorepo — the same default `scripts/gen-flows.mjs` uses).

    Flows are namespaced by an `id` prefixed `<module>.`, mirroring gen-flows'
    module filter, so this returns exactly what `pnpm gen:flows` would emit into
    the pair's registry. Returns 0 when the source is absent/unreadable (a fresh
    backend that has not annotated `@flow_step` yet) — the honest default."""
    source = flows_json or (
        react_dir / ".." / "stapel-example-monolith" / "codegen" / "generated" / "flows.json"
    )
    try:
        flows = json.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(flows, list):
        return 0
    prefix = f"{module}."
    return sum(
        1 for flow in flows
        if isinstance(flow, dict) and str(flow.get("id", "")).startswith(prefix)
    )


def analytics_marker(flow_count: int) -> dict:
    """Choose the demo `DemoButton` analytics marker HONESTLY from the module's
    flow count. A `data-analytics="flow"` marker is only true when a bag action
    steps an auto-instrumented flow machine; a pair with zero flow machines
    (every fresh scaffold, until the backend annotates `@flow_step`) must instead
    declare `data-analytics="none"` with a machine-readable reason, so the
    clickable-needs-event lint stays satisfied WITHOUT the button lying about a
    flow event it never emits. Returns the template context fragment."""
    if flow_count > 0:
        return {
            "DEMO_BUTTON_ATTRS": 'data-analytics="flow"',
            "DEMO_BUTTON_NOTE": (
                '`data-analytics="flow"` — honest, because a headless bag action '
                "STEPS a flow machine, which is auto-instrumented (`flow.<id>.<step>`)"
            ),
        }
    return {
        "DEMO_BUTTON_ATTRS": (
            'data-analytics="none" data-analytics-reason="no-flow-machines"'
        ),
        "DEMO_BUTTON_NOTE": (
            '`data-analytics="none"` with a `data-analytics-reason` — honest, '
            "because this scaffold ships no flow machines yet (only the "
            "provider), so the button steps nothing auto-instrumented. Switch to "
            '`data-analytics="flow"` once a bag action drives a real machine'
        ),
    }


def skin_context(skin: bool) -> dict:
    """The template fragments that differ between a skinned pair (the default)
    and a `--no-skin` one. Kept as data rather than as branches inside the
    templates so that BOTH shapes are readable in one place, and so a reader can
    see exactly what `--no-skin` costs: the `./default` subpath, the antd peers,
    the themed harness, the skin demo, the panel's i18n keys and the pair's one
    nav entry (an entry naming a component that does not exist is worse than
    none — it fails at the container's import)."""
    if not skin:
        return {
            "SKIN_EXPORTS": "",
            "SKIN_PEERS": "",
            "SKIN_PEERS_ANTD": "",
            "SKIN_DEVDEPS": "",
            "SKIN_DEVDEPS_ANTD": "",
            "SKIN_SIZE_LIMITS": "",
            "SKIN_I18N_KEYS": "",
            "SKIN_I18N_EN": "",
            "HARNESS_SKIN_IMPORT": "",
            "HARNESS_THEMED_CHILDREN": "{props.children}",
            "HARNESS_THEME_NOTE": "",
            "SKIN_README": "",
        }
    return {
        "SKIN_EXPORTS": (
            '    "./default": {\n'
            '      "types": "./dist/default/index.d.ts",\n'
            '      "default": "./dist/default/index.js"\n'
            "    },\n"
            '    "./i18n/ru": {\n'
            '      "types": "./dist/i18n/ru.d.ts",\n'
            '      "default": "./dist/i18n/ru.js"\n'
            "    },\n"
            '    "./i18n/es": {\n'
            '      "types": "./dist/i18n/es.d.ts",\n'
            '      "default": "./dist/i18n/es.js"\n'
            "    },\n"
        ),
        # Split so the dependency maps stay alphabetical (`antd` sorts after
        # `@tanstack/react-query`, `@stapel/tokens-antd` right after `@stapel/tokens`).
        "SKIN_PEERS": f'    "@stapel/tokens-antd": "{TOKENS_ANTD_PEER}",\n',
        "SKIN_PEERS_ANTD": f'    "antd": "{ANTD_PEER}",\n',
        "SKIN_DEVDEPS": '    "@stapel/tokens-antd": "workspace:^",\n',
        "SKIN_DEVDEPS_ANTD": '    "antd": "^5.20.0",\n',
        "SKIN_SIZE_LIMITS": (
            ",\n    {\n"
            '      "name": "default skin subpath (opt-in: antd + the token bridge)",\n'
            '      "path": "dist/default/index.js",\n'
            '      "limit": "10 KB"\n'
            "    },\n    {\n"
            '      "name": "i18n/ru subpath (opt-in locale: en floor + ru UI)",\n'
            '      "path": "dist/i18n/ru.js",\n'
            '      "limit": "8 KB"\n'
            "    },\n    {\n"
            '      "name": "i18n/es subpath (opt-in locale: en floor + es UI)",\n'
            '      "path": "dist/i18n/es.js",\n'
            '      "limit": "8 KB"\n'
            "    }"
        ),
        "SKIN_I18N_KEYS": (
            '  navOverview: "{{MODULE}}.nav.overview",\n'
            '  panelEmpty: "{{MODULE}}.panel.empty",\n'
            '  panelLoading: "{{MODULE}}.panel.loading",\n'
        ),
        "SKIN_I18N_EN": (
            "\n  // the default skin's own copy (see i18n/ru.ts, i18n/es.ts)\n"
            + "".join(f'  {line}\n' for line in _SKIN_TEXT_LINES["en"])
        ),
        "HARNESS_SKIN_IMPORT": 'import { SkinTheme } from "@stapel/tokens-antd/skin";\n',
        "HARNESS_THEMED_CHILDREN": "\n          <SkinTheme>{props.children}</SkinTheme>\n        ",
        "HARNESS_THEME_NOTE": (
            "\n * `SkinTheme` is mounted here too, so a `./default` demo is drawn\n"
            " * through the same antd bridge a host gets — a skin demo that themed\n"
            " * itself would document a screen nobody ships."
        ),
        "SKIN_README": _SKIN_README,
    }


# The scaffold's own UI copy, in the three locales the fleet ships. It is
# deliberately GENERIC — "Overview", "Nothing here yet", "Loading" — and that is
# what makes ru/es honest from commit 1 rather than a placeholder: a generator
# cannot translate a product's vocabulary, and it can translate these. Every key
# the pair adds later is held to the same rule by `test/i18nParity.test.ts`.
_SKIN_TEXT_LINES: dict[str, list[str]] = {
    "en": [
        '"{{MODULE}}.nav.overview": "Overview",',
        '"{{MODULE}}.panel.empty": "Nothing here yet.",',
        '"{{MODULE}}.panel.loading": "Loading…",',
    ],
    "ru": [
        '"{{MODULE}}.nav.overview": "Обзор",',
        '"{{MODULE}}.panel.empty": "Здесь пока ничего нет.",',
        '"{{MODULE}}.panel.loading": "Загрузка…",',
    ],
    "es": [
        '"{{MODULE}}.nav.overview": "Resumen",',
        '"{{MODULE}}.panel.empty": "Aquí todavía no hay nada.",',
        '"{{MODULE}}.panel.loading": "Cargando…",',
    ],
}

# The unknown-error fallback exists in every pair (`i18n/keys.ts`), so the two
# locale bundles carry it too — otherwise the very first refusal a ru/es host
# shows would be the one sentence still in English.
_UNKNOWN_ERROR_TEXT = {
    "ru": '"{{MODULE}}.error.unknown": "Что-то пошло не так. Попробуйте ещё раз.",',
    "es": '"{{MODULE}}.error.unknown": "Algo salió mal. Inténtalo de nuevo.",',
}

_LOCALE_NAMES = {"ru": "Russian", "es": "Spanish"}

_SKIN_README = """
`./default` is the pair's **shipped** half: `<{{CAMEL}}Panel/>`, themed through
`SkinTheme` from `@stapel/tokens-antd/skin` (one bridge for the whole fleet — a
pair never mounts its own `ConfigProvider` and never defaults a theme mode).
Importing it is the opt-in that pulls `antd`; a host with its own design system
keeps importing the root entry and draws its own screens.

```tsx
import { {{CAMEL}}Panel } from "{{PKG_NAME}}/default";
import { register{{CAMEL}}I18nRu } from "{{PKG_NAME}}/i18n/ru";
```

Locales ship as subpaths (`./i18n/ru`, `./i18n/es`) so a host carries only the
ones it registers; `test/i18nParity.test.ts` fails the build if a key exists in
en and not in ru/es. Product rules for this pair: `docs/guidelines.md`.
"""


def build_context(
    module: str,
    title: str,
    backend: str,
    path_prefix: str,
    desc: str | None = None,
    core_peer: str = DEFAULT_CORE_PEER,
    flow_count: int = 0,
    skin: bool = True,
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
        "ERRORS_SOURCE": f"${{SIBLING_ROOT:-..}}/{backend}/docs/errors.json",
        "TITLE": title,
        "DESC": desc or default_desc,
        "CORE_PEER": core_peer,
        "YEAR": str(datetime.date.today().year),
        **analytics_marker(flow_count),
        **skin_context(skin),
    }


def nav_entries(ctx: dict, *, skin: bool = True) -> list[dict]:
    """The pair's declared screens — ONE source for both `src/nav/manifest.ts`
    and `nav-manifest.json`, so the declaration and its generated projection
    cannot disagree before `pnpm gen:nav` has ever run.

    A skinned pair declares its panel; a `--no-skin` pair declares nothing,
    because an entry naming a `component.export` that does not exist passes the
    generator's structural validation and fails at the CONTAINER's import — two
    repositories away from the mistake."""
    if not skin:
        return []
    module = ctx["MODULE"]
    return [
        {
            "id": f"{module}.overview",
            "labelKey": f"{module}.nav.overview",
            "icon": SCAFFOLD_NAV_ICON,
            # Relative: a child of the member area (`/app` in a monolith,
            # `/account` in a storefront container). An absolute path would
            # claim a root-level address the container may already own.
            "route": {"path": module},
            "component": {"export": f"{ctx['CAMEL']}Panel", "subpath": "default"},
            "placement": {"level": "top"},
            "menuVisibleDefault": True,
            "requiresAuth": True,
            # DECLARED, never derived: a screen that later gains requiresAuth
            # for an unrelated reason must not silently leave a public tree.
            "surface": "member",
            "order": 50,
        }
    ]


def nav_entries_ts(entries: list[dict]) -> str:
    """The `navEntries` array literal for `src/nav/manifest.ts`. JSON is a valid
    TS object literal, so the declaration is a projection of the same dict the
    manifest is — no second hand-written copy to drift."""
    if not entries:
        return "[]"
    body = json.dumps(entries, indent=2)
    return "\n".join(
        line if i == 0 else f"  {line}" for i, line in enumerate(body.splitlines())
    )


def nav_manifest_json(ctx: dict, *, skin: bool = True) -> str:
    """The pair's own ``nav-manifest.json``, byte-identical to what
    ``scripts/gen-nav-manifest.mjs`` writes for the same (empty) declaration:
    ``JSON.stringify({package, version, entries}, null, 2)`` + a newline.

    Emitting it here rather than telling the operator to run ``pnpm gen:nav``
    is what makes the scaffold's output ALREADY GREEN under the monorepo's own
    drift gate — a scaffold whose first act is to redden `gen:nav:check` is a
    scaffold everyone learns to run with the gate switched off. ``version``
    is the scaffold's own ``0.0.0`` (PACKAGE_JSON above); the generator reads
    the same field, so the two agree by construction until the first
    changeset moves both."""
    return json.dumps(
        {
            "package": ctx["PKG_NAME"],
            "version": "0.0.0",
            "entries": nav_entries(ctx, skin=skin),
        },
        indent=2,
        ensure_ascii=False,
    ) + "\n"


def locale_bundle(ctx: dict, locale: str, *, skin: bool = True) -> str:
    """`src/i18n/<locale>.ts` — the pair's own UI copy in one locale, rendered
    from the same generic text table the en bundle uses. The bundle carries
    exactly the keys the pair DECLARES (a `--no-skin` pair has no panel copy),
    so locale parity is an equality rather than a superset."""
    lines = [_UNKNOWN_ERROR_TEXT[locale]]
    if skin:
        lines.extend(_SKIN_TEXT_LINES[locale])
    return render(
        T.I18N_LOCALE_TS,
        {
            **ctx,
            "LOCALE": locale,
            "LOCALE_CAMEL": locale.capitalize(),
            "LOCALE_NAME": _LOCALE_NAMES[locale],
            "LOCALE_TEXTS": "".join(f"  {line}\n" for line in lines),
        },
    )


def file_plan(ctx: dict, *, skin: bool | None = None) -> dict:
    """Relative path (within the package dir) -> rendered content.

    `skin` is READ OFF THE CONTEXT by default: the context already carries the
    skin fragments (`build_context(..., skin=False)` empties them), and a second
    independent flag here is a way for the two halves of one decision to
    disagree — a package.json exporting `./default` with no `src/default/` in
    the plan."""
    if skin is None:
        skin = bool(ctx.get("SKIN_EXPORTS"))
    module = ctx["MODULE"]
    plan = {
        "package.json": render(T.PACKAGE_JSON, ctx),
        "tsconfig.json": render(T.TSCONFIG, ctx),
        "tsconfig.demo.json": T.TSCONFIG_DEMO,
        "vitest.config.ts": T.VITEST_CONFIG,
        # One place for the jsdom gaps antd 6 falls into (matchMedia,
        # ResizeObserver, the pseudo-element getComputedStyle form) plus the
        # testing-library `waitFor` budget that pairs with the vitest ones.
        "test/vitest.setup.ts": T.VITEST_SETUP_TS,
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
        # zero-flow registry shim (§21/S3): gen:flows emits nothing for a
        # module without flows, so the public flow surface is hand-preserved.
        "src/flows/registry.ts": render(T.FLOWS_REGISTRY_TS, ctx),
        f"src/headless/{ctx['CAMEL']}Provider.tsx": render(T.PROVIDER_TSX, ctx),
        # Scripted-fullstack navigation (spec §3.8): the declaration and its
        # generated projection ship together, so the pair is enrolled in the
        # nav contract from its first commit instead of being retrofitted
        # into it once per pair.
        "src/nav/manifest.ts": render(
            T.NAV_MANIFEST_SKIN_TS if skin else T.NAV_MANIFEST_TS,
            {**ctx, "NAV_ENTRIES": nav_entries_ts(nav_entries(ctx, skin=skin))},
        ),
        "nav-manifest.json": nav_manifest_json(ctx, skin=skin),
        "src/i18n/keys.ts": render(T.I18N_KEYS_TS, ctx),
        "src/i18n/errorsMap.ts": render(T.ERRORS_MAP_TS, ctx),
        # Locale subpaths (i18n-shipping.md §2): ru/es ship from commit 1, so
        # parity is a gate rather than a retrofit. `--no-skin` keeps them —
        # a headless pair still has refusal copy.
        "src/i18n/ru.ts": locale_bundle(ctx, "ru", skin=skin),
        "src/i18n/es.ts": locale_bundle(ctx, "es", skin=skin),
        # The pair's own product rules, written down where the pair is.
        "docs/guidelines.md": render(T.GUIDELINES_MD, ctx),
        # demo layer (first-class code: compiled, product-linted, smoke-rendered)
        "demo/_harness.tsx": render(T.HARNESS_TSX, ctx),
        f"demo/{ctx['CAMEL']}.demo.tsx": render(T.DEMO_TSX, ctx),
        # test family mirrored from the etalon (§4.2/§5.1/§2.5); no
        # flowsContract.test.ts — vacuous for a zero-flow pair (§21/S3),
        # scaffolded per flow once the backend annotates @flow_step
        "test/pair.test.ts": render(T.TEST_TS, ctx),
        "test/errorsBundle.test.ts": render(T.ERRORS_BUNDLE_TEST, ctx),
        "test/demos.test.tsx": render(T.DEMOS_TEST_TSX, ctx),
        "test/prodBundlePurity.test.ts": render(T.PROD_BUNDLE_PURITY_TEST, ctx),
        # The parity gate the fleet lacked: a key with no ru/es text fails the
        # build instead of reaching a ru/es host in English.
        "test/i18nParity.test.ts": render(T.I18N_PARITY_TEST, ctx),
    }
    if skin:
        camel = ctx["CAMEL"]
        plan[f"src/default/{camel}Panel.tsx"] = render(T.DEFAULT_PANEL_TSX, ctx)
        plan["src/default/types.ts"] = T.DEFAULT_TYPES_TS
        plan["src/default/index.ts"] = render(T.DEFAULT_INDEX_TS, ctx)
        plan[f"demo/{camel}Skin.demo.tsx"] = render(T.DEMO_SKIN_TSX, ctx)
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
            "name": "api",
            "cmd": (
                f"API_SCHEMA=${{SIBLING_ROOT:-..}}/{ctx['BACKEND']}/docs/schema.json "
                f"API_OUT=packages/{pkg}/src/api/generated/schema.ts "
                f"node scripts/gen-api.mjs"
            ),
            "path": f"packages/{pkg}/src/api/generated/schema.ts",
            "check_inline": True,
        },
        {
            "name": "flows",
            "cmd": f"FLOW_MODULE={ctx['MODULE']} node scripts/gen-flows.mjs",
            "path": f"packages/{pkg}/src/flows/generated",
            "check_inline": True,
        },
        {
            "name": "errors",
            "cmd": (
                f"AUTH_ERRORS_JSON=${{SIBLING_ROOT:-..}}/{ctx['BACKEND']}/docs/errors.json "
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
                f"MANIFEST_BACKEND_PYPROJECT=${{SIBLING_ROOT:-..}}/{ctx['BACKEND']}/pyproject.toml "
                f"node scripts/gen-manifest.mjs"
            ),
            "path": f"packages/{pkg}/manifest.json packages/{pkg}/llms.txt",
            "check_inline": True,
        },
    ]


# ── the nav aggregate (spec §3.8) ────────────────────────────────────────────
# `gen:nav` is the ONE driver that cannot be enumerated by appending: every
# invocation carries the FULL package set in `NAV_PACKAGES`, because the
# driver rebuilds the monorepo's ROOT aggregate on each run (that is what
# removes the bootstrap ordering problem — see gen-nav-manifest.mjs's own
# header). Appending a new invocation while leaving the existing ones' lists
# alone would make the aggregate depend on WHICH invocation ran last, i.e. on
# nothing anyone can read. So this pair of helpers rebuilds the whole script
# from the package list instead.
_NAV_DRIVER = "node --experimental-strip-types scripts/gen-nav-manifest.mjs"
_NAV_DIFF_SEP = " && git diff --exit-code -- nav-manifest.json "


def nav_gen_script(packages: list[str]) -> str:
    """The root `gen:nav` script for *packages* (dirs, e.g.
    ``packages/auth-react``), in the given order."""
    csv = ",".join(packages)
    return " && ".join(
        f"NAV_PACKAGES={csv} NAV_PKG_DIR={pkg} {_NAV_DRIVER}" for pkg in packages
    )


def nav_check_script(packages: list[str]) -> str:
    """The root `gen:nav:check` script: regenerate, then diff the root
    aggregate AND every package's own projection — a pair whose manifest was
    hand-edited has to fail here, not at a container's build."""
    diffed = " ".join(f"{pkg}/nav-manifest.json" for pkg in packages)
    return f"{nav_gen_script(packages)}{_NAV_DIFF_SEP}{diffed}"


def nav_packages_from(script: str) -> list[str]:
    """The package list a `gen:nav` script already carries. Read from the
    FIRST `NAV_PACKAGES=` assignment — every invocation carries the same list
    by construction (and if they ever disagree, the first one is the one this
    function's caller is about to overwrite anyway)."""
    marker = "NAV_PACKAGES="
    if marker not in script:
        return []
    csv = script.split(marker, 1)[1].split(" ", 1)[0]
    return [p for p in (part.strip() for part in csv.split(",")) if p]


def patch_root_nav(react_dir: Path, ctx: dict) -> tuple[bool, list[str]]:
    """Enroll this pair in the root `gen:nav`/`gen:nav:check` scripts and, by
    the same edit, in `NAV_PACKAGES` (the two are the same list — the aggregate
    IS the enrollment). Idempotent: a pair already in the list is a no-op.

    Returns ``(patched_ok, changed_script_keys)``; ``(False, [])`` on any
    unexpected shape, so the caller prints exact instructions rather than
    half-editing the monorepo's root."""
    root_pkg = react_dir / "package.json"
    try:
        data = json.loads(root_pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, []
    scripts = data.get("scripts")
    if not isinstance(scripts, dict) or "gen:nav" not in scripts or "gen:nav:check" not in scripts:
        return False, []

    packages = nav_packages_from(scripts["gen:nav"])
    if not packages:
        return False, []
    pkg_dir = f"packages/{ctx['PKG_DIR']}"
    if pkg_dir in packages:
        return True, []
    packages.append(pkg_dir)
    scripts["gen:nav"] = nav_gen_script(packages)
    scripts["gen:nav:check"] = nav_check_script(packages)
    root_pkg.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return True, ["gen:nav", "gen:nav:check"]


def _root_nav_instructions(ctx: dict) -> str:
    """Human-readable fallback for the nav aggregate, printed when the root
    package.json cannot be patched safely."""
    pkg_dir = f"packages/{ctx['PKG_DIR']}"
    return (
        "Enroll this pair in the ROOT package.json nav aggregate by hand:\n"
        f"  gen:nav / gen:nav:check  — add `{pkg_dir}` to EVERY invocation's "
        "NAV_PACKAGES list, add one more invocation with "
        f"NAV_PKG_DIR={pkg_dir}, and add `{pkg_dir}/nav-manifest.json` to the "
        "check script's `git diff` paths. Every invocation carries the full "
        "list on purpose — the driver rebuilds the root aggregate each run."
    )


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
    skin: bool = True,
) -> Path:
    backend = backend or f"stapel-{module}"
    path_prefix = path_prefix or f"/{module}/api/v1/"  # v1 canon (api-versioning.md §2)
    ctx = build_context(
        module, title, backend, path_prefix, desc,
        core_peer=core_peer_range(react_dir),
        flow_count=module_flow_count(react_dir, module),
        skin=skin,
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
    # Spec §3.8: and in the nav aggregate, which is enumerated differently
    # (the full package list per invocation) — see patch_root_nav.
    nav_patched, nav_changed = patch_root_nav(react_dir, ctx)
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
    if nav_patched:
        if nav_changed:
            print("  gen:nav\n  gen:nav:check   (NAV_PACKAGES rebuilt with this pair)")
        else:
            print("Root package.json already enrolls this pair in NAV_PACKAGES.")
    else:
        print("\nCould not patch the nav aggregate automatically.\n" + _root_nav_instructions(ctx))

    print(
        f"\nDone. Next steps (run from the stapel-react monorepo root):\n"
        f"  pnpm install\n"
        f"  pnpm gen                                    "
        f"# flows + errors + events + demos + manifest/llms.txt (all pairs)\n"
        f"  pnpm --filter {ctx['PKG_NAME']} build\n"
        f"  pnpm --filter {ctx['PKG_NAME']} lint test size\n"
        # `test` deliberately excludes the pack purity test (npm pack --dry-run
        # is tens of seconds cold and times the package out inside a parallel
        # turbo graph); it runs on its own task instead.
        f"  pnpm --filter {ctx['PKG_NAME']} test:pack\n"
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
        "--no-skin",
        dest="skin",
        action="store_false",
        help="scaffold the headless layers ONLY — no src/default/ skin, no "
        "./default export, no antd peers, no nav entry. The skin is on by "
        "default (§54: a pair ships a feature, not only a bag); pass this for "
        "a pair that is genuinely headless by design (billing, calendar, "
        "recordings) and say so in its MODULE.md.",
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
        skin=args.skin,
    )


if __name__ == "__main__":
    main()
