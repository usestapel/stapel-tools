"""Templates for ``stapel-new-react-lib`` — a headless ``@stapel/<module>-react``
pair, materialized from the auth-react etalon (frontend-standard §9,
frontend-core-architecture §4 checklist).

Tokens (``{{KEY}}``) filled by ``new_react_lib.build_context``:

  MODULE         slug, single lowercase word          e.g. "notifications"
  CAMEL          Capitalized slug                      e.g. "Notifications"
  UPPER          UPPERCASE slug                        e.g. "NOTIFICATIONS"
  PKG_DIR        package directory under packages/     e.g. "notifications-react"
  PKG_NAME       npm name                              e.g. "@stapel/notifications-react"
  BACKEND        backend module repo/name              e.g. "stapel-notifications"
  PATH_PREFIX    OpenAPI path prefix for the module    e.g. "/notifications/api/"
  ERRORS_SOURCE  errors.py path relative to react root e.g. "../stapel-notifications/errors.py"
  TITLE          human title                           e.g. "Notifications"
  DESC           package.json description
  YEAR           current year

The four codegen drivers are NOT copied here (fork-free rule): the generated
package.json wires the etalon's env-parametrized ``scripts/gen-*.mjs`` at the
monorepo root via env knobs. A pair owns three per-package gates
(flows/errors/manifest); ``gen:api`` is core-owned (the shared schema.ts).
"""

# ── package.json ──────────────────────────────────────────────────────────────
PACKAGE_JSON = """{
  "name": "{{PKG_NAME}}",
  "version": "0.0.0",
  "description": "{{DESC}}",
  "license": "MIT",
  "repository": {
    "type": "git",
    "url": "https://github.com/usestapel/stapel-react.git",
    "directory": "packages/{{PKG_DIR}}"
  },
  "type": "module",
  "sideEffects": false,
  "main": "./dist/index.js",
  "types": "./dist/index.d.ts",
  "exports": {
    ".": {
      "types": "./dist/index.d.ts",
      "default": "./dist/index.js"
    },
    "./manifest": "./manifest.json",
    "./manifest.json": "./manifest.json",
    "./llms.txt": "./llms.txt",
    "./package.json": "./package.json"
  },
  "files": [
    "dist",
    "src",
    "tsconfig.json",
    "README.md",
    "MODULE.md",
    "CHANGELOG.md",
    "manifest.json",
    "llms.txt"
  ],
  "scripts": {
    "build": "tsc -p tsconfig.json",
    "test": "vitest run",
    "lint": "eslint .",
    "size": "size-limit",
    "gen:flows": "FLOW_MODULE={{MODULE}} node ../../scripts/gen-flows.mjs",
    "gen:flows:check": "FLOW_MODULE={{MODULE}} node ../../scripts/gen-flows.mjs && git diff --exit-code -- src/flows/generated",
    "gen:errors": "AUTH_ERRORS_SOURCES={{ERRORS_SOURCE}} ERRORS_OUT=src/i18n/generated ERRORS_CONST={{UPPER}}_ERRORS ERRORS_TYPE_PREFIX={{CAMEL}} node ../../scripts/gen-errors.mjs",
    "gen:errors:check": "AUTH_ERRORS_SOURCES={{ERRORS_SOURCE}} ERRORS_OUT=src/i18n/generated ERRORS_CONST={{UPPER}}_ERRORS ERRORS_TYPE_PREFIX={{CAMEL}} node ../../scripts/gen-errors.mjs && git diff --exit-code -- src/i18n/generated",
    "gen:manifest": "MANIFEST_PKG_DIR=packages/{{PKG_DIR}} MANIFEST_MODULE={{BACKEND}} MANIFEST_TAGPREFIX={{PATH_PREFIX}} node ../../scripts/gen-manifest.mjs",
    "gen:manifest:check": "MANIFEST_PKG_DIR=packages/{{PKG_DIR}} MANIFEST_MODULE={{BACKEND}} MANIFEST_TAGPREFIX={{PATH_PREFIX}} node ../../scripts/gen-manifest.mjs && git diff --exit-code -- manifest.json llms.txt",
    "gen": "pnpm gen:flows && pnpm gen:errors && pnpm gen:manifest",
    "gen:check": "pnpm gen:flows:check && pnpm gen:errors:check && pnpm gen:manifest:check"
  },
  "size-limit": [
    {
      "path": "dist/index.js",
      "limit": "12 KB"
    }
  ],
  "peerDependencies": {
    "@stapel/core": "workspace:^",
    "@tanstack/react-query": "^5.0.0",
    "react": ">=19"
  },
  "devDependencies": {
    "@size-limit/preset-small-lib": "^11.2.0",
    "@stapel/core": "workspace:^",
    "@tanstack/react-query": "^5.81.0",
    "@testing-library/react": "^16.3.0",
    "@types/react": "^19.1.0",
    "@types/react-dom": "^19.1.0",
    "jsdom": "^26.1.0",
    "msw": "^2.10.2",
    "react": "^19.1.0",
    "react-dom": "^19.1.0",
    "size-limit": "^11.2.0",
    "typescript": "^5.8.3",
    "vitest": "^3.2.4"
  },
  "engines": {
    "node": ">=22"
  },
  "publishConfig": {
    "access": "public"
  }
}
"""

# ── tsconfig.json (self-contained per frontend-standard §7) ───────────────────
TSCONFIG = """{
  "$schema": "https://json.schemastore.org/tsconfig",
  "_comment": "Self-contained on purpose: standalone-buildable per frontend-standard §7. Mirrors the root tsconfig.base.json settings.",
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "noImplicitOverride": true,
    "exactOptionalPropertyTypes": true,
    "isolatedModules": true,
    "isolatedDeclarations": true,
    "verbatimModuleSyntax": true,
    "declaration": true,
    "declarationMap": true,
    "sourceMap": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "outDir": "dist",
    "rootDir": "src"
  },
  "include": ["src"]
}
"""

VITEST_CONFIG = """import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["test/**/*.test.{ts,tsx}"],
  },
});
"""

# ── src/index.ts ──────────────────────────────────────────────────────────────
INDEX_TS = """/**
 * `{{PKG_NAME}}` — the headless React flow pair for {{BACKEND}}
 * (frontend-standard §2). Business + state only, zero visual opinion. Built on
 * `@stapel/core`'s StapelClient (verification-403 interception, token refresh,
 * i18n, analytics, query layer).
 *
 * Scaffolded by `stapel-new-react-lib`. Layers: api → model → flows → headless
 * → i18n. Generated surfaces (flows registry, error map, manifest, llms.txt)
 * are produced by the monorepo `gen:*` drivers and stand under drift gates.
 */

// ── api ──────────────────────────────────────────────────────────────────────
export { create{{CAMEL}}Api } from "./api/{{MODULE}}Api.js";
export type { {{CAMEL}}Api } from "./api/{{MODULE}}Api.js";
export type { Schemas } from "./api/types.js";

// ── flows ────────────────────────────────────────────────────────────────────
// The flow-machine primitive lives in `@stapel/core` (one reviewed copy for
// every pair — frontend-core-architecture §4b). Re-exported for ergonomics.
export { createFlowMachine, useFlow, isErrorCode } from "@stapel/core";
export type {
  FlowMachine,
  FlowMachineOptions,
  FlowStateBase,
  FlowError,
} from "@stapel/core";
export { toFlowError } from "./flows/errors.js";
export { {{UPPER}}_FLOWS, flowEndpoints } from "./flows/generated/flows.gen.js";
export type {
  {{CAMEL}}FlowId,
  {{CAMEL}}FlowSpec,
  FlowEndpoint,
} from "./flows/generated/flows.gen.js";

// ── model (runtime wiring, query keys, context) ──────────────────────────────
export { create{{CAMEL}}Runtime } from "./model/runtime.js";
export type {
  {{CAMEL}}Runtime,
  Create{{CAMEL}}RuntimeOptions,
} from "./model/runtime.js";
export {
  {{CAMEL}}RuntimeContext,
  use{{CAMEL}}Runtime,
  use{{CAMEL}}Api,
  use{{CAMEL}}Analytics,
} from "./model/context.js";
export { {{MODULE}}QueryKeys } from "./model/queryKeys.js";

// ── headless (renderless components) ─────────────────────────────────────────
export { {{CAMEL}}Provider } from "./headless/{{CAMEL}}Provider.js";

// ── i18n ─────────────────────────────────────────────────────────────────────
export {
  {{UPPER}}_I18N_KEYS,
  {{MODULE}}I18nBundleEn,
  register{{CAMEL}}I18n,
} from "./i18n/keys.js";
export type { {{CAMEL}}I18nKey } from "./i18n/keys.js";

// ── errors map (code → status/params/remediation/en; generated) ──────────────
export {
  {{UPPER}}_ERRORS,
  {{UPPER}}_ERROR_CODES,
  {{MODULE}}ErrorBundleEn,
  explain{{CAMEL}}Error,
} from "./i18n/errorsMap.js";
export type {
  {{CAMEL}}ErrorCode,
  {{CAMEL}}ErrorSpec,
  Remediation,
} from "./i18n/errorsMap.js";
"""

# ── src/api/types.ts ──────────────────────────────────────────────────────────
API_TYPES_TS = """/**
 * Wire types for the {{BACKEND}} HTTP contract — **derived from the generated
 * OpenAPI surface** (frontend-standard §2/§3), never hand-maintained. The
 * single source of truth is `components["schemas"]` from `@stapel/core`
 * (`packages/core/src/generated/schema.ts`, produced by `pnpm gen:api` from the
 * unified all-modules OpenAPI). Alias the schemas this pair uses under local
 * names here; do NOT write parallel response bodies. Where drf-spectacular +
 * openapi-typescript under-describe the runtime, apply a small documented
 * correction (see auth-react `api/types.ts` for the three canonical patterns).
 */
import type { components } from "@stapel/core";

/** The generated schema table — the one source of truth for wire shapes. */
export type Schemas = components["schemas"];

// Alias the {{BACKEND}} schemas the pair uses, e.g.:
//   export type Device = Schemas["Device"];
//   export type NotificationFeedPage = Schemas["PaginatedNotificationList"];
"""

# ── src/api/<module>Api.ts ────────────────────────────────────────────────────
API_CLIENT_TS = """import type { StapelClient } from "@stapel/core";

/**
 * The pair's typed operation surface. Today a thin holder over the injected
 * {@link StapelClient}; the named, typed operations (`{{MODULE}}.<op>()`) will
 * be GENERATED from schema.json operationIds by gen-api v2 (task
 * `core-typed-ops`). Until then add hand-authored operations here and put
 * anything that can never be derived from the schema in `api/extensions.ts`,
 * each flagged with WHY the codegen does not cover it.
 */
export interface {{CAMEL}}Api {
  readonly client: StapelClient;
}

export function create{{CAMEL}}Api(client: StapelClient): {{CAMEL}}Api {
  return { client };
}
"""

# ── src/api/extensions.ts ─────────────────────────────────────────────────────
API_EXTENSIONS_TS = """/**
 * Hand-authored API surface the codegen does not (yet) cover — browser-redirect
 * URL builders, open-redirect guards, narrow domain type-guards, header
 * conventions. Everything that CAN be derived from schema.json belongs in the
 * generated operations (`api/{{MODULE}}Api.ts`), not here.
 */
export {};
"""

# ── src/model/queryKeys.ts ────────────────────────────────────────────────────
QUERY_KEYS_TS = """/**
 * Namespaced TanStack Query keys (frontend-standard §2 — "ключи неймспейснуты").
 * Everything under the `"{{MODULE}}"` root so a host can invalidate the whole
 * module or match a single resource. Persist scope is per-user via core's query
 * runtime (`setPersistUser`). Explicit tuple return types satisfy
 * `--isolatedDeclarations`. Add one entry per read-operation as you wire hooks.
 */
const ROOT = "{{MODULE}}" as const;

export const {{MODULE}}QueryKeys: {
  readonly all: readonly ["{{MODULE}}"];
} = {
  all: [ROOT],
};
"""

# ── src/model/runtime.ts ──────────────────────────────────────────────────────
RUNTIME_TS = """import { createStapelClient } from "@stapel/core";
import type { Analytics, StapelClient } from "@stapel/core";
import { create{{CAMEL}}Api } from "../api/{{MODULE}}Api.js";
import type { {{CAMEL}}Api } from "../api/{{MODULE}}Api.js";

/**
 * The wired {{MODULE}} runtime — builds a {@link StapelClient} and the pair's
 * API over it. The returned `client` is what the host injects into core's
 * `StapelConfigProvider` (as the default or the `"{{MODULE}}"` module client),
 * preserving the client-injection fork seam (frontend-standard §7.2). Auth
 * token/refresh and the verification-403 seam are supplied by the host's auth
 * runtime on the shared client — this pair does not re-implement them.
 */
export interface {{CAMEL}}Runtime {
  readonly client: StapelClient;
  readonly api: {{CAMEL}}Api;
  readonly analytics: Analytics | null;
}

export interface Create{{CAMEL}}RuntimeOptions {
  /** e.g. `{{PATH_PREFIX}}` or `https://app.example.com{{PATH_PREFIX}}`. */
  readonly baseUrl: string;
  readonly fetch?: typeof globalThis.fetch;
  readonly credentials?: RequestCredentials;
  readonly analytics?: Analytics | null;
  /** Extra headers merged into every request (e.g. a tenant id). */
  readonly defaultHeaders?: Record<string, string>;
}

export function create{{CAMEL}}Runtime(
  options: Create{{CAMEL}}RuntimeOptions
): {{CAMEL}}Runtime {
  const analytics = options.analytics ?? null;
  const client = createStapelClient({
    baseUrl: options.baseUrl,
    ...(options.fetch !== undefined ? { fetch: options.fetch } : {}),
    ...(options.credentials !== undefined
      ? { credentials: options.credentials }
      : {}),
    ...(options.defaultHeaders !== undefined
      ? { defaultHeaders: options.defaultHeaders }
      : {}),
  });
  const api = create{{CAMEL}}Api(client);
  return { client, api, analytics };
}
"""

# ── src/model/context.tsx ─────────────────────────────────────────────────────
CONTEXT_TSX = """import { createContext, useContext } from "react";
import type { Context } from "react";
import type { Analytics } from "@stapel/core";
import type { {{CAMEL}}Api } from "../api/{{MODULE}}Api.js";
import type { {{CAMEL}}Runtime } from "./runtime.js";

/**
 * The wired {{CAMEL}}Runtime shared through React context by
 * `<{{CAMEL}}Provider>`. Hooks in `model/` and `headless/` read the singletons
 * from here.
 */
export const {{CAMEL}}RuntimeContext: Context<{{CAMEL}}Runtime | null> =
  createContext<{{CAMEL}}Runtime | null>(null);

export function use{{CAMEL}}Runtime(): {{CAMEL}}Runtime {
  const runtime = useContext({{CAMEL}}RuntimeContext);
  if (runtime === null) {
    throw new Error("{{CAMEL}} hooks must be used within a <{{CAMEL}}Provider>");
  }
  return runtime;
}

export function use{{CAMEL}}Api(): {{CAMEL}}Api {
  return use{{CAMEL}}Runtime().api;
}

export function use{{CAMEL}}Analytics(): Analytics | null {
  return use{{CAMEL}}Runtime().analytics;
}
"""

# ── src/flows/errors.ts ───────────────────────────────────────────────────────
FLOW_ERRORS_TS = """import { toFlowError as coreToFlowError } from "@stapel/core";
import type { FlowError } from "@stapel/core";

export type { FlowError } from "@stapel/core";
export { isErrorCode } from "@stapel/core";

/**
 * Fold any thrown value into a {@link FlowError} using this pair's own
 * module-scoped fallback key (`{{MODULE}}.error.unknown`, an en string in
 * {@link {{MODULE}}I18nBundleEn}) so a non-`StapelApiError` fault still renders
 * real copy rather than a raw key. The primitive lives in `@stapel/core`
 * (frontend-core-architecture §4b); this wrapper only pins the fallback.
 */
export function toFlowError(error: unknown): FlowError {
  return coreToFlowError(error, "{{MODULE}}.error.unknown");
}
"""

# ── src/headless/<Camel>Provider.tsx ──────────────────────────────────────────
PROVIDER_TSX = """import type { ReactElement, ReactNode } from "react";
import { {{CAMEL}}RuntimeContext } from "../model/context.js";
import type { {{CAMEL}}Runtime } from "../model/runtime.js";

/**
 * Provides the wired {@link {{CAMEL}}Runtime} to every {{MODULE}} hook and
 * headless component below it. Bring your own visual shell — this component
 * renders nothing of its own.
 *
 * ```tsx
 * const runtime = create{{CAMEL}}Runtime({ baseUrl: "{{PATH_PREFIX}}" });
 * // give runtime.client to core's <StapelConfigProvider config={{ client }}>
 * <{{CAMEL}}Provider runtime={runtime}>{app}</{{CAMEL}}Provider>
 * ```
 */
export function {{CAMEL}}Provider(props: {
  runtime: {{CAMEL}}Runtime;
  children: ReactNode;
}): ReactElement {
  return (
    <{{CAMEL}}RuntimeContext.Provider value={props.runtime}>
      {props.children}
    </{{CAMEL}}RuntimeContext.Provider>
  );
}
"""

# ── src/i18n/keys.ts ──────────────────────────────────────────────────────────
I18N_KEYS_TS = """import type { I18nDictionary, I18nEngine } from "@stapel/core";
import { {{MODULE}}ErrorBundleEn } from "./generated/errors.gen.js";

/**
 * {{MODULE}}-react's own translation KEYS (frontend-standard §4.2): headless
 * components never render literal strings — hosts resolve these via core's i18n
 * engine (`useT`). Backend error codes flow through the SAME contour: a
 * `StapelApiError.code` is already a key, so the default bundle below ships
 * English fallbacks for both the backend error codes (generated) and the
 * pair's own UI keys. Point core's `loadLocale` at stapel-translate to override
 * per locale. Add UI keys under the `{{MODULE}}.` namespace as you build flows.
 */
export const {{UPPER}}_I18N_KEYS = {
  unknownError: "{{MODULE}}.error.unknown",
} as const;

export type {{CAMEL}}I18nKey =
  (typeof {{UPPER}}_I18N_KEYS)[keyof typeof {{UPPER}}_I18N_KEYS];

/**
 * English fallback bundle for {{MODULE}}-react UI keys + backend error codes.
 * The generated `{{MODULE}}ErrorBundleEn` (from {{BACKEND}}'s error registry,
 * `pnpm gen:errors`) is spread FIRST so every backend `error.*` key has a
 * fallback — a `StapelApiError.code` never renders as a raw key. Hand-polished
 * copy below then OVERRIDES the generated English for the keys users see most.
 */
export const {{MODULE}}I18nBundleEn: I18nDictionary = {
  // Backend error codes — generated en fallbacks (coverage by construction).
  ...{{MODULE}}ErrorBundleEn,

  // {{MODULE}}-react UI
  "{{MODULE}}.error.unknown": "Something went wrong. Please try again.",
};

/**
 * Register {{MODULE}}-react's key bundle into a core i18n engine (call once at
 * startup). A later `loadLocale` from stapel-translate can layer localized
 * overrides on top.
 */
export function register{{CAMEL}}I18n(engine: I18nEngine, locale = "en"): void {
  engine.registerBundle(locale, {{MODULE}}I18nBundleEn);
}
"""

# ── src/i18n/errorsMap.ts ─────────────────────────────────────────────────────
ERRORS_MAP_TS = """/**
 * The pair's error map (frontend-standard §4 checklist #7, frontend-core §2.5):
 * the generated `code → { status, params, remediation, en }` catalog plus a
 * tiny `explain()` lookup. Backs the manifest `errors` block and gives hosts a
 * mechanical UX branch beside `t(code, params)`. The map itself is generated
 * from the backend registry (`pnpm gen:errors`); this file only adds the lookup
 * helper and re-exports the public surface.
 */
import { {{UPPER}}_ERRORS } from "./generated/errors.gen.js";
import type { Remediation } from "./generated/errors.gen.js";

export {
  {{UPPER}}_ERRORS,
  {{UPPER}}_ERROR_CODES,
  {{MODULE}}ErrorBundleEn,
} from "./generated/errors.gen.js";
export type {
  {{CAMEL}}ErrorCode,
  {{CAMEL}}ErrorSpec,
  Remediation,
} from "./generated/errors.gen.js";

/**
 * Resolve a backend error code to its remediation hint, or `undefined` for a
 * code this module doesn't know (e.g. a cross-cutting `stapel.http.*` fallback).
 * Zero guessing at runtime — a static lookup over the generated map.
 */
export function explain{{CAMEL}}Error(code: string): Remediation | undefined {
  return ({{UPPER}}_ERRORS as Record<string, { remediation: Remediation }>)[code]
    ?.remediation;
}
"""

# ── test/pair.test.ts ─────────────────────────────────────────────────────────
TEST_TS = """import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  {{MODULE}}QueryKeys,
  {{UPPER}}_ERROR_CODES,
  {{MODULE}}ErrorBundleEn,
  {{MODULE}}I18nBundleEn,
  explain{{CAMEL}}Error,
  register{{CAMEL}}I18n,
} from "../src/index.js";

describe("query keys", () => {
  it("namespaces under the module root", () => {
    expect({{MODULE}}QueryKeys.all[0]).toBe("{{MODULE}}");
  });
});

describe("generated error bundle (frontend-standard §9 — en fallbacks total)", () => {
  it("ships an en fallback for every generated backend code", () => {
    for (const code of {{UPPER}}_ERROR_CODES) {
      expect({{MODULE}}ErrorBundleEn[code], code).toBeTruthy();
      // and it survives the merge into the shipped i18n bundle
      expect({{MODULE}}I18nBundleEn[code], code).toBeTruthy();
    }
  });

  it("explains a remediation for every generated code", () => {
    for (const code of {{UPPER}}_ERROR_CODES) {
      expect(explain{{CAMEL}}Error(code), code).toBeDefined();
    }
  });

  it("pins the module-scoped unknown fallback", () => {
    expect({{MODULE}}I18nBundleEn["{{MODULE}}.error.unknown"]).toBeTruthy();
  });

  it("registers the bundle into a core i18n engine", () => {
    const seen: Record<string, unknown> = {};
    register{{CAMEL}}I18n({
      registerBundle: (_locale: string, dict: Record<string, unknown>) => {
        Object.assign(seen, dict);
      },
    } as never);
    expect(seen["{{MODULE}}.error.unknown"]).toBeTruthy();
  });
});

describe("self-description (frontend-core §2.4 — drift-gated manifest)", () => {
  it("manifest.json describes this package", () => {
    // vitest runs from the package root, so a cwd-relative path is stable
    // across node/jsdom (jsdom's import.meta.url is not a file:// URL).
    const manifest = JSON.parse(readFileSync("manifest.json", "utf8"));
    expect(manifest.package).toBe("{{PKG_NAME}}");
    expect(manifest.backend.module).toBe("{{BACKEND}}");
    expect(Array.isArray(manifest.layers)).toBe(true);
  });
});
"""

# ── README.md ─────────────────────────────────────────────────────────────────
README_MD = """# {{PKG_NAME}}

{{DESC}}

Headless React flow pair for **{{BACKEND}}** (frontend-standard §2). Business +
state only, zero visual opinion — any design layers on top. Built on
`@stapel/core` (typed client + `StapelApiError` envelope, token refresh,
verification-403 interception, i18n engine, analytics facade, TanStack Query).

Scaffolded by `stapel-new-react-lib`. See `MODULE.md` for the layer map, machine
table, extension seams, and persist policy.

## Layers

```
src/
  api/        typed client — thin adapter over @stapel/core `components`
  model/      query keys, runtime wiring, context/hooks
  flows/      createFlowMachine flow machines (+ generated registry)
  headless/   renderless components ({{CAMEL}}Provider, flow render-props)
  i18n/       translation keys + generated backend error map
```

## Generated surfaces (drift-gated)

| Surface | Command | Gate |
|---|---|---|
| Flow registry | `pnpm gen:flows` | `pnpm gen:flows:check` |
| Backend error map + en bundle | `pnpm gen:errors` | `pnpm gen:errors:check` |
| `manifest.json` + `llms.txt` | `pnpm gen:manifest` | `pnpm gen:manifest:check` |

All three (`pnpm gen` / `pnpm gen:check`) drive the monorepo `scripts/gen-*.mjs`
with this package's env knobs — the drivers are shared, not forked. The typed
`schema.ts` is core-owned (`pnpm gen:api` at the root).

## License

MIT
"""

# ── MODULE.md ─────────────────────────────────────────────────────────────────
MODULE_MD = """# {{PKG_NAME}} — module guide

Headless React flow pair for **{{BACKEND}}**. This is the human companion to the
generated `llms.txt` (agent context) and `manifest.json` (machine catalog).

## Layers

- **api/** — `create{{CAMEL}}Api(client)`; types are aliases over the generated
  `components["schemas"]` from `@stapel/core` (never parallel hand-written
  bodies). Named typed operations arrive with gen-api v2 (`core-typed-ops`);
  hand-authored, un-generatable surface lives in `api/extensions.ts`.
- **model/** — `{{MODULE}}QueryKeys` (single key factory, `["{{MODULE}}"]`
  namespace), `create{{CAMEL}}Runtime`, React context/hooks. Declare the
  persist/optimistic policy here as you add read hooks and mutations.
- **flows/** — `createFlowMachine`-based machines (primitive imported from
  `@stapel/core`), bound to the generated `{{UPPER}}_FLOWS` registry. Scaffold
  new machines from flows.json; keep them under `gen:flows:check`.
- **headless/** — render-prop components; `<{{CAMEL}}Provider>` wires the
  runtime into context. shadcn-copyable (frontend-standard §7).
- **i18n/** — `{{UPPER}}_I18N_KEYS` + en bundle; the generated backend error
  bundle is merged in so every `error.*` code has a fallback.

## Extension seams (frontend-standard §7)

- Client is injected via `<{{CAMEL}}Provider>` / core's `StapelConfigProvider`
  (per-module override) — pairs never hard-import a client.
- Flow deps are injected through `create<X>Flow(deps)` factories.
- The headless layer is fully replaceable (copy-and-own).

## TODO after scaffold

1. `pnpm install && pnpm gen` — materialize the generated surfaces.
2. Alias the {{BACKEND}} schemas you use in `api/types.ts`.
3. Add read hooks + mutations in `model/` and a persist/optimistic policy.
4. Once {{BACKEND}} annotates `@flow_step`, scaffold flow machines from
   flows.json and put them under `gen:flows:check`.
5. Fill `MODULE.md`'s machine table and link the SA-doc flows.
"""

# ── CHANGELOG.md ──────────────────────────────────────────────────────────────
CHANGELOG_MD = """# {{PKG_NAME}}

## 0.0.0

- Scaffolded by `stapel-new-react-lib` from the auth-react etalon
  (frontend-standard §9, frontend-core-architecture §4 checklist). Layers
  api → model → flows → headless → i18n; drift-gated generated surfaces
  (flows registry, backend error map, manifest + llms.txt) via the shared
  monorepo `gen:*` drivers.
"""
