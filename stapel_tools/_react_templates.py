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
  CORE_PEER      @stapel/core peer range (floor = core's current minor,
                 ceiling <1.0.0)                        e.g. ">=0.2.0 <1.0.0"
  YEAR           current year

The codegen drivers are NOT copied here (fork-free rule): the etalon's
env-parametrized ``scripts/gen-*.mjs`` live at the monorepo root, and — exactly
like the auth-react/tokens etalon — a pair's own package.json owns NO ``gen:*``
scripts. The scaffold instead wires this pair into the ROOT ``gen``/``gen:check``
aggregates (``new_react_lib._patch_root_gen``), appending one env-parametrized
invocation per driver (gen-flows/errors/events/demos/manifest). ``gen:api`` and
``gen:tokens`` are core/tokens-owned. Demos are first-class code (compiled by
``tsconfig.demo.json``, linted with the product ruleset, smoke-rendered by
``test/demos.test.tsx``) but never shipped (excluded from the ``files``
allowlist — proven by ``test/prodBundlePurity.test.ts``).
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
    "test": "tsc -p tsconfig.demo.json && vitest run",
    "lint": "eslint .",
    "size": "size-limit"
  },
  "size-limit": [
    {
      "path": "dist/index.js",
      "limit": "12 KB"
    }
  ],
  "peerDependencies": {
    "@stapel/core": "{{CORE_PEER}}",
    "@tanstack/react-query": "^5.0.0",
    "react": ">=19"
  },
  "devDependencies": {
    "@size-limit/preset-small-lib": "^11.2.0",
    "@stapel/core": "workspace:^",
    "@stapel/showcase": "workspace:^",
    "@stapel/tokens": "workspace:^",
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

# ── tsconfig.demo.json (demos are first-class code; §4.2) ─────────────────────
TSCONFIG_DEMO = """{
  "$schema": "https://json.schemastore.org/tsconfig",
  "_comment": "Type-checks the demos (frontend-guardrails §4.2: 'demos are first-class code, compiled'). noEmit — demos are never shipped (not in package `files`); the build tsconfig emits src only. Runs in the `test` task. Generated stories are excluded (they are drift-gated, not hand-edited).",
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
    "verbatimModuleSyntax": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "noEmit": true
  },
  "include": ["src", "demo"],
  "exclude": ["demo/generated"]
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

# ── test/pair.test.ts (query keys + drift-gated self-description) ──────────────
TEST_TS = """import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  {{MODULE}}QueryKeys,
  {{MODULE}}I18nBundleEn,
  register{{CAMEL}}I18n,
} from "../src/index.js";

describe("query keys (frontend-standard §2 — namespaced)", () => {
  it("namespaces under the module root", () => {
    expect({{MODULE}}QueryKeys.all[0]).toBe("{{MODULE}}");
  });
});

describe("i18n registration", () => {
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
  it("manifest.json describes this package + its backend contract", () => {
    // vitest runs from the package root, so a cwd-relative path is stable
    // across node/jsdom (jsdom's import.meta.url is not a file:// URL).
    const manifest = JSON.parse(readFileSync("manifest.json", "utf8"));
    expect(manifest.package).toBe("{{PKG_NAME}}");
    expect(manifest.backend.module).toBe("{{BACKEND}}");
    // backend.contract (gen:manifest ← MANIFEST_BACKEND_PYPROJECT): the semver
    // range this surface was generated against — a backend minor bump reddens
    // the drift gate (frontend-core §2.4 / §3.4.2).
    expect(manifest.backend.contract).toBeTruthy();
    expect(Array.isArray(manifest.layers)).toBe(true);
  });
});
"""

# ── test/errorsBundle.test.ts (en-fallback coverage — errors drift gate teeth) ─
ERRORS_BUNDLE_TEST = """import { describe, expect, it } from "vitest";
import { {{MODULE}}I18nBundleEn } from "../src/i18n/keys.js";
import {
  {{UPPER}}_ERROR_CODES,
  {{MODULE}}ErrorBundleEn,
  explain{{CAMEL}}Error,
} from "../src/i18n/errorsMap.js";

/**
 * The teeth of the errors drift gate (frontend-core-architecture §2.5, §4c):
 * every backend error key the pair knows about ALSO has an English fallback in
 * the i18n bundle. Combined with `pnpm gen:errors:check` (a NEW backend key = a
 * red diff), a backend key can never reach the host as a raw, untranslated key.
 * A hand-edit that drops the generated spread from `{{MODULE}}I18nBundleEn` fails
 * here.
 */
describe("backend error keys all have an en fallback", () => {
  it("every {{UPPER}}_ERROR_CODE resolves in {{MODULE}}I18nBundleEn", () => {
    const missing = {{UPPER}}_ERROR_CODES.filter(
      (code) => !(code in {{MODULE}}I18nBundleEn)
    );
    expect(missing).toEqual([]);
  });

  it("the generated fallback bundle covers exactly the registry", () => {
    expect(Object.keys({{MODULE}}ErrorBundleEn).sort()).toEqual(
      [...{{UPPER}}_ERROR_CODES].sort()
    );
  });

  it("explains a remediation for every generated code", () => {
    for (const code of {{UPPER}}_ERROR_CODES) {
      expect(explain{{CAMEL}}Error(code), code).toBeDefined();
    }
  });
});
"""

# ── test/flowsContract.test.ts (flows.json → registry drift-gate teeth) ────────
FLOWS_CONTRACT_TEST = """import { describe, expect, it } from "vitest";
import { {{UPPER}}_FLOWS } from "../src/flows/generated/flows.gen.js";

/**
 * The flow CONTRACT test (docs/flow-system.md §5): proves the flows.json →
 * registry drift gate is not cosmetic. Each registry entry's key equals its
 * canonical id and carries well-formed i18n keys with ordered steps — so a
 * backend flow rename / re-endpoint (regenerated by `pnpm gen:flows`) breaks
 * these assertions rather than silently skewing the analytics funnel or the
 * client contract. Machine ↔ registry binding + HTTP-surface assertions arrive
 * per flow as you scaffold machines from flows.json (see auth-react
 * `test/flowsContract.test.ts` for the full msw-backed pattern).
 */
describe("flow registry integrity", () => {
  it("every entry's key equals its canonical id and carries well-formed i18n keys", () => {
    for (const [key, spec] of Object.entries({{UPPER}}_FLOWS)) {
      expect(spec.id).toBe(key);
      expect(spec.titleKey).toBe(`flow.${spec.id}.title`);
      expect(spec.descriptionKey).toBe(`flow.${spec.id}.description`);
      const orders = spec.steps.map((s) => s.order);
      expect(orders).toEqual([...orders].sort((a, b) => a - b));
    }
  });
});
"""

# ── test/demos.test.tsx (smoke-render every demo — demos are first-class code) ─
DEMOS_TEST_TSX = """import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { renderDemoVariant, variantIds } from "@stapel/showcase";
import type { DemoDef } from "@stapel/showcase";

/**
 * Smoke render for every {{MODULE}}-react demo (frontend-guardrails §4.2: demos
 * are first-class code — compiled, linted, RENDERED). Discovers demos by glob so
 * a new `*.demo.tsx` is covered automatically, mounts each default variant with
 * its mock harness, and asserts it renders without throwing.
 */
const modules = import.meta.glob("../demo/*.demo.tsx", { eager: true }) as Record<
  string,
  { default: DemoDef }
>;

describe("{{MODULE}}-react demos", () => {
  const entries = Object.entries(modules);

  it("discovers demos via glob", () => {
    expect(entries.length).toBeGreaterThan(0);
  });

  for (const [path, mod] of entries) {
    const demo = mod.default;
    const first = variantIds(demo)[0];
    it(`renders ${demo.id} (${path})`, () => {
      expect(first).toBeDefined();
      if (!first) return;
      const { container } = render(renderDemoVariant(demo, first));
      expect(container.firstChild).not.toBeNull();
    });
  }
});
"""

# ── test/prodBundlePurity.test.ts (§5.1 — no showcase/demo code ships) ─────────
PROD_BUNDLE_PURITY_TEST = """// @vitest-environment node
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * Introspection-gating, layer 1 (frontend-guardrails §5.1): the showcase/demo
 * tooling is OUT of the pair's production bundle *by construction*, not by a
 * runtime flag. A pair is a headless product surface; the showcase format
 * (`@stapel/showcase`) and the `*.demo.tsx` files exist only to author demos and
 * must never reach a customer's app or the published tarball. This test is the
 * teeth: it fails if a demo dependency leaks into the runtime graph or the demos
 * slip into the shipped files.
 */
const PKG_DIR = resolve(fileURLToPath(new URL(".", import.meta.url)), "..");
const pkg = JSON.parse(
  readFileSync(resolve(PKG_DIR, "package.json"), "utf8")
) as {
  dependencies?: Record<string, string>;
  peerDependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
  files?: string[];
};

const INTROSPECTION_ONLY = ["@stapel/showcase", "@stapel/showcase-viewer"];

describe("prod bundle carries no showcase/demo code (§5.1)", () => {
  it("no showcase package is a runtime (deps) or peer dependency", () => {
    const runtime = {
      ...(pkg.dependencies ?? {}),
      ...(pkg.peerDependencies ?? {}),
    };
    const leaked = INTROSPECTION_ONLY.filter((name) => name in runtime);
    expect(leaked).toEqual([]);
  });

  it("@stapel/showcase is present, but only as a devDependency", () => {
    // It IS used (to author demos) — so assert the intended location, not just
    // absence, to catch an accidental promotion to dependencies.
    expect(pkg.devDependencies ?? {}).toHaveProperty("@stapel/showcase");
  });

  it("the published `files` allowlist excludes demo/", () => {
    const files = pkg.files ?? [];
    expect(files).not.toContain("demo");
    expect(files.some((f) => /(^|\\/)demo(\\/|$)/.test(f))).toBe(false);
  });

  it("the packed tarball contains no demo or showcase files", () => {
    // `npm pack --dry-run --json` reports exactly what would publish, honoring
    // the files allowlist + .npmignore — the ground truth, not just config.
    const out = execFileSync("npm", ["pack", "--dry-run", "--json"], {
      cwd: PKG_DIR,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    });
    const paths: string[] = JSON.parse(out)[0].files.map(
      (f: { path: string }) => f.path
    );
    expect(paths.filter((p) => /(^|\\/)demo(\\/|\\.)/i.test(p))).toEqual([]);
    expect(paths.filter((p) => /showcase/i.test(p))).toEqual([]);
  }, 30_000); // `npm pack` cold-starts slowly — beyond vitest's 5s default
});
"""

# ── demo/_harness.tsx (shared mock harness; product-linted first-class code) ───
HARNESS_TSX = """/**
 * Shared harness for the {{MODULE}}-react demos (frontend-guardrails §4.2). Demos
 * are first-class code — compiled, linted with the PRODUCT ruleset, smoke-rendered
 * — so this file obeys the same guardrails as `src/`:
 *
 *  - no raw colours: every colour is a token via `cssVar()`.
 *  - no hardcoded text: every label is an i18n key rendered with `t()`.
 *  - clickable-needs-event: {@link DemoButton} carries `data-analytics=\"flow\"` —
 *    honest, because a headless bag action STEPS a flow machine, which is
 *    auto-instrumented (`flow.<id>.<step>`). The action prop is named `run` (not
 *    `onClick`) so the CALL site is not itself an untracked clickable — the
 *    tracked point is the real `<button>` in here.
 *
 * The mock runtime injects a canned `fetch` (no MSW worker needed) so a demo
 * renders identically in Ladle (interactive) and in vitest (smoke). Themes are
 * the viewer's job (data-theme + tokens.css); this only wires the providers a
 * headless component needs: query client, i18n, and the {{MODULE}} runtime.
 */
import { useMemo } from "react";
import type { CSSProperties, ReactElement, ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nProvider, createI18n, useT } from "@stapel/core";
import { cssVar, radii, spacing, fontSize } from "@stapel/tokens";
import { create{{CAMEL}}Runtime } from "../src/index.js";
import { {{CAMEL}}Provider, register{{CAMEL}}I18n } from "../src/index.js";

/** The base every mock handler mounts on (mirrors {{BACKEND}} `{{PATH_PREFIX}}`). */
export const DEMO_BASE = "https://{{MODULE}}.demo.stapel.dev{{PATH_PREFIX}}";

/**
 * A handler map: path suffix → response. A plain value is a 200 JSON body; a
 * `[status, body]` tuple sets the HTTP status (so a demo can reach an error
 * step).
 */
export type DemoResponse = unknown | readonly [number, unknown];
export type DemoHandlers = Readonly<Record<string, DemoResponse>>;

function statusAndBody(value: DemoResponse): [number, unknown] {
  if (
    Array.isArray(value) &&
    value.length === 2 &&
    typeof value[0] === "number"
  ) {
    return [value[0], value[1]];
  }
  return [200, value];
}

/** Build a canned `fetch` from a suffix→response map; unmatched paths return `{}`. */
export function mockFetch(handlers: DemoHandlers): typeof globalThis.fetch {
  return ((input: RequestInfo | URL): Promise<Response> => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    let matched: DemoResponse = {};
    for (const [suffix, value] of Object.entries(handlers)) {
      if (url.includes(suffix)) {
        matched = value;
        break;
      }
    }
    const [status, body] = statusAndBody(matched);
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { \"content-type\": \"application/json\" },
      })
    );
  }) as typeof globalThis.fetch;
}

/** i18n copy for the demo chrome — a `demo.*` (unmanaged) namespace, so the
 * i18n-key-exists lint treats it as app-local and never false-positives. */
const demoBundleEn: Record<string, string> = {
  \"demo.action.start\": \"Start\",
  \"demo.action.submit\": \"Submit\",
  \"demo.action.reset\": \"Reset\",
  \"demo.label.step\": \"state.step\",
};

/**
 * Provider frame every {{MODULE}} demo variant renders inside. Builds a fresh mock
 * runtime + query client per mount so variants stay isolated.
 */
export function {{CAMEL}}DemoHarness(props: {
  handlers?: DemoHandlers;
  children: ReactNode;
}): ReactElement {
  const { handlers } = props;
  const { runtime, queryClient, i18n } = useMemo(() => {
    const rt = create{{CAMEL}}Runtime({
      baseUrl: DEMO_BASE,
      fetch: mockFetch(handlers ?? {}),
    });
    const engine = createI18n({ locale: \"en\" });
    register{{CAMEL}}I18n(engine);
    engine.registerBundle(\"en\", demoBundleEn);
    return {
      runtime: rt,
      queryClient: new QueryClient({
        defaultOptions: { queries: { retry: false } },
      }),
      i18n: engine,
    };
  }, [handlers]);
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider i18n={i18n}>
        <{{CAMEL}}Provider runtime={runtime}>{props.children}</{{CAMEL}}Provider>
      </I18nProvider>
    </QueryClientProvider>
  );
}

// ── shared demo UI (token-driven; no raw colours, no literal prose) ───────────

const cardStyle: CSSProperties = {
  background: cssVar(\"card-bg\"),
  color: cssVar(\"color-text-primary\"),
  border: `1px solid ${cssVar(\"card-border\")}`,
  borderRadius: radii.lg,
  padding: spacing[\"5\"],
  display: \"flex\",
  flexDirection: \"column\",
  gap: spacing[\"3\"],
  maxWidth: \"24rem\",
  fontSize: fontSize.md.fontSize,
};

/** A titled card wrapper for a demo body. `heading` (not `title`) keeps the
 * no-hardcoded-text rule from treating a technical component name as prose. */
export function DemoCard(props: {
  heading: ReactNode;
  children: ReactNode;
}): ReactElement {
  return (
    <div style={cardStyle} data-theme-surface>
      <strong style={{ fontSize: fontSize.lg.fontSize }}>{props.heading}</strong>
      {props.children}
    </div>
  );
}

/** Renders the current flow step (a technical token, never user prose). */
export function StepBadge(props: { step: string }): ReactElement {
  const t = useT();
  return (
    <div style={{ display: \"flex\", gap: spacing[\"2\"], alignItems: \"center\" }}>
      <span style={{ color: cssVar(\"color-text-secondary\") }}>
        {t(\"demo.label.step\")}
      </span>
      <code
        style={{
          background: cssVar(\"color-background-secondary\"),
          color: cssVar(\"color-text-brand\"),
          borderRadius: radii.sm,
          // Size tokens are unitless numbers; React only auto-appends `px` to
          // single numeric values, so multi-value shorthands spell the unit.
          padding: `${spacing[\"1\"]}px ${spacing[\"2\"]}px`,
        }}
      >
        {props.step}
      </code>
    </div>
  );
}

const buttonStyle: CSSProperties = {
  background: cssVar(\"button-primary-bg\"),
  color: cssVar(\"button-primary-text\"),
  border: \"none\",
  borderRadius: radii.md,
  // See StepBadge: unitless tokens need an explicit unit in shorthands.
  padding: `${spacing[\"2\"]}px ${spacing[\"4\"]}px`,
  cursor: \"pointer\",
  fontSize: fontSize.sm.fontSize,
};

/**
 * A demo action button. The interactive prop is `run` (not `onClick`) so the
 * call site is not an untracked clickable; the real `<button>` here declares
 * `data-analytics=\"flow\"` — the bag action it triggers steps an
 * auto-instrumented flow machine.
 */
export function DemoButton(props: {
  run: () => void;
  labelKey: string;
}): ReactElement {
  const t = useT();
  return (
    <button style={buttonStyle} data-analytics=\"flow\" onClick={props.run}>
      {t(props.labelKey)}
    </button>
  );
}

/** A row of demo action buttons. */
export function DemoActions(props: { children: ReactNode }): ReactElement {
  return (
    <div style={{ display: \"flex\", gap: spacing[\"2\"], flexWrap: \"wrap\" }}>
      {props.children}
    </div>
  );
}
"""

# ── demo/<Camel>.demo.tsx (starter demo — covers the starter headless export) ──
DEMO_TSX = """/** {{TITLE}} provider — the pair's headless root (starter demo). */
import type { ReactElement } from "react";
import { defineDemo } from "@stapel/showcase";
import { {{CAMEL}}Provider } from "../src/index.js";
import { {{CAMEL}}DemoHarness, DemoCard, StepBadge } from "./_harness.js";

function {{CAMEL}}ProviderDemo(): ReactElement {
  return (
    <{{CAMEL}}DemoHarness>
      <DemoCard heading="{{CAMEL}}Provider">
        <StepBadge step="ready" />
      </DemoCard>
    </{{CAMEL}}DemoHarness>
  );
}

/**
 * The completeness gate (gen:demos) requires every exported headless component
 * to have ≥1 demo. This starter demo covers `{{CAMEL}}Provider` — the pair's only
 * headless export at scaffold time. Add one `<Name>.demo.tsx` per headless flow
 * component (with `defineDemo({ component: <X>, flow: \"{{MODULE}}.<id>\", … })`)
 * as you build them; each becomes a smoke test AND a Ladle story automatically.
 */
export default defineDemo({
  id: "{{MODULE}}.provider",
  title: "{{TITLE}} provider",
  description:
    "The headless {{MODULE}} root wires the runtime, i18n engine, and query client into React context. Replace with per-flow demos as you add headless components.",
  component: {{CAMEL}}Provider,
  tokens: ["card-bg"],
  variants: {
    default: { render: () => <{{CAMEL}}ProviderDemo /> },
  },
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
  analytics/  generated typed-event registry (events.json)
demo/         first-class demos (compiled, product-linted, smoke-rendered)
```

## Generated surfaces (drift-gated)

| Surface | Path | Gate |
|---|---|---|
| Flow registry | `src/flows/generated/` | `pnpm gen:flows:check` |
| Backend error map + en bundle | `src/i18n/generated/` | `pnpm gen:errors:check` |
| Typed-event registry | `src/analytics/generated/events.json` | `pnpm gen:events:check` |
| Demos → Ladle stories | `demo/generated/` | `pnpm gen:demos:check` |
| `manifest.json` + `llms.txt` | package root | `pnpm gen:manifest:check` |

These drift gates run at the **monorepo root** (`pnpm gen` / `pnpm gen:check`) —
the etalon's env-parametrized `scripts/gen-*.mjs` drivers are shared, not forked.
`stapel-new-react-lib` wired this pair into the root `gen`/`gen:check` aggregates
at scaffold time (one env-parametrized invocation per driver). The typed
`schema.ts` is core-owned (`pnpm gen:api`); design tokens are tokens-owned
(`pnpm gen:tokens`).

## Guardrails

Linted by the shared `@stapel/eslint-plugin` flat config (no raw colours, no raw
token imports, no raw fetch, i18n-key existence, typed analytics, headless-only)
and the shared **stylelint** preset — `pnpm lint` per package plus `pnpm lint:css`
at the root (colours only ever `var(--stapel-*)`). Demos are first-class code:
compiled by `tsconfig.demo.json`, linted with the product ruleset, and
smoke-rendered by `test/demos.test.tsx` — but never shipped (excluded from the
`files` allowlist; proven by `test/prodBundlePurity.test.ts`).

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
- **analytics/** — `generated/events.json`, the typed-event registry projected
  from `defineEvent` call sites + flow funnels (`pnpm gen:events`). Read by the
  analytics lint and embedded into `manifest.json`; nothing to hand-edit.
- **demo/** — first-class demos (`defineDemo`, `@stapel/showcase`): `_harness.tsx`
  wires a mock runtime + i18n + query client; each `<Name>.demo.tsx` is compiled,
  product-linted, smoke-rendered, and projected to a Ladle story (`pnpm gen:demos`).
  The completeness gate requires ≥1 demo per exported headless component; the
  starter `{{CAMEL}}.demo.tsx` covers `{{CAMEL}}Provider`. Demos never ship.

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
