"""``frontend/`` scaffold templates for ``stapel-create-project --type monolith``
(BACKLOG §57 — dev/prod compose + nginx canon, owner directive from the
live-run postmortem).

A minimal, real Vite + React + TypeScript app — not a placeholder. It is
wired into:

  - ``docker-compose.local.yml`` — a plain ``node:22-alpine`` container runs
    ``npm install && npm run dev`` (hot-reload, logs visible); local-nginx
    proxies everything that is not the reserved backend namespace to it.
  - ``docker-compose.yml`` (prod) — a one-shot ``frontend-build`` service
    (this dir's own ``Dockerfile``) builds the app and copies ``dist/`` into
    the ``frontend-dist`` volume; the main nginx serves it as static files
    with an SPA fallback.

Placeholders (``{{KEY}}``), filled by ``create_project._create_monolith``:
  SLUG                  project slug, e.g. "app"
  TITLE                 display name, e.g. "App"
  BACKEND_UPSTREAM_DEFAULT   compose-network default for the backend
                         (e.g. "svc-app:8000") — the *default* baked into
                         vite.config.ts's standalone dev-proxy fallback;
                         the real dev path (nginx-local) reads the same
                         default from docker-compose.local.yml's env, not
                         from this file.

Colour tokens (§68 color-token-matrix, P5): ``THEME_JSON`` is this project's
OWN ``stapel.theme.json`` — the neutral role dictionary (surface*/text*/
border*/brand*/link + success/warning/error/info x {base,-bg,-border,-on}),
seeded with a sensible bluish ``brand`` ramp and standard status colours,
light+dark. It is compiled by ``@stapel/tokens``' OWN published generator —
the ``stapel-tokens`` bin (``npm run gen:tokens`` / ``gen:tokens:check`` in
``PACKAGE_JSON`` below) — never a vendored/forked copy of the engine (the
exact forked-generator failure mode §68 closes; see
``docs/pending/color-token-matrix.md``). Editing ``stapel.theme.json``
(ramps or roles) and re-running the generator is the ONLY way to re-theme —
never hand-edit the generated ``frontend/src/stapel-tokens/`` output.
"""

import json

PACKAGE_JSON = """\
{
  "name": "{{SLUG}}-frontend",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "gen:tokens": "stapel-tokens --theme ./stapel.theme.json --out ./src/stapel-tokens --targets core",
    "gen:tokens:check": "stapel-tokens --theme ./stapel.theme.json --out ./src/stapel-tokens --targets core --check"
  },
  "dependencies": {
    "react": "^19.1.0",
    "react-dom": "^19.1.0"
  },
  "devDependencies": {
    "@stapel/eslint-plugin": "^0.3.0",
    "@stapel/tokens": "^0.5.0",
    "@types/react": "^19.1.0",
    "@types/react-dom": "^19.1.0",
    "@vitejs/plugin-react": "^4.3.0",
    "eslint": "^9.0.0",
    "typescript": "^5.8.3",
    "vite": "^6.0.0"
  }
}
"""

TSCONFIG_JSON = """\
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true
  },
  "include": ["src"]
}
"""

# Used INSTEAD of TSCONFIG_JSON whenever the scripted-nav route tree is
# active (P1) — ``src/nav.generated.ts`` does ``import stapelNavOverrides
# from "../stapel.nav.json"`` (the deep-merge-over-default override channel,
# read again at RUNTIME by the exact same ``resolveNav`` call the shipped
# app's ``<AppShell/>`` would use — see that file's own docstring), which
# needs ``resolveJsonModule`` to type-check. A project with no routing keeps
# the plain ``TSCONFIG_JSON`` above byte-for-byte (no reason to change a
# setting nothing in the generated source uses).
TSCONFIG_JSON_WITH_JSON_MODULE = """\
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "resolveJsonModule": true
  },
  "include": ["src"]
}
"""

TSCONFIG_NODE_JSON = """\
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "noEmit": true
  },
  "include": ["vite.config.ts"]
}
"""

VITE_CONFIG_TS = """\
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Dev-canon (§57 owner directive): the PRIMARY dev path is
 * docker-compose.local.yml's local-nginx, which already splits traffic between
 * this server and the Django backend (reserved namespace: /{{SLUG}}/,
 * /staticfiles/, /media/, plus each lib's own /<mod>/api|swagger|schema.json|
 * admin (never its bare root — see reserved-paths.json and the project's
 * AGENTS.md §5). The proxy
 * config below is a FALLBACK for running `npm run dev` standalone, without
 * local-nginx in front — e.g. hitting a dockerized backend from a natively
 * run Vite. Either way the backend target is an ENV VAR with a
 * compose-network default, never a hardcoded host: set VITE_BACKEND_TARGET
 * in this dir's .env to override (e.g. to http://localhost:8000 for a
 * fully native backend run).
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backendTarget =
    env.VITE_BACKEND_TARGET || "http://{{BACKEND_UPSTREAM_DEFAULT}}";

  return {
    plugins: [react()],
    server: {
      host: true,
      port: 5173,
      strictPort: true,
      proxy: {
        // GENERATED from the project's actual lib selection (STAPEL_LIBS
        // url_prefixes + service slug + admin + static/media) — adding a
        // stapel lib to the project regenerates its rule by construction.
{{VITE_PROXY_RULES}}
      },
    },
  };
});
"""

INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{{TITLE}}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""

MAIN_TSX = """\
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.js";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
"""


def render_main_tsx(*, routing_active: bool, has_modules: bool) -> str:
    """``frontend/src/main.tsx`` — the collapse rule (owner directive, P1):
    a selection with NO routing feature active (no ``--auth``, no
    ``--landing``, no selected pair with nav entries) returns ``MAIN_TSX``
    UNCHANGED, byte for byte — the exact current clean-shell output.

    Once routing is active, ``<App/>`` is retired in favour of mounting the
    generated ``router`` (``./routes.tsx``, ``RouterProvider``) — every page
    now comes from the route tree (``LandingPage``/``AuthPanel``/the
    ``/app`` subtree), so the old single-component starter has nothing left
    to do. When the project ALSO wired ``@stapel/<module>-react`` pairs
    (``has_modules`` — ``modules.tsx``'s ``ModulesProvider``), that provider
    wraps ``<RouterProvider/>`` instead of ``<App/>``'s old content — the
    runtime/session context every route (``ProtectedRoute``, ``AppShell``,
    each mounted pair's own hooks) needs has to sit ABOVE the router, not
    inside one page of it.
    """
    if not routing_active:
        return MAIN_TSX
    lines = [
        'import { StrictMode } from "react";',
        'import { createRoot } from "react-dom/client";',
        'import { RouterProvider } from "react-router";',
        'import { router } from "./routes.js";',
    ]
    if has_modules:
        lines.append('import { ModulesProvider } from "./modules.js";')
    lines.append("")
    lines.append('createRoot(document.getElementById("root")!).render(')
    lines.append("  <StrictMode>")
    if has_modules:
        lines.append("    <ModulesProvider>")
        lines.append("      <RouterProvider router={router} />")
        lines.append("    </ModulesProvider>")
    else:
        lines.append("    <RouterProvider router={router} />")
    lines.append("  </StrictMode>")
    lines.append(");")
    lines.append("")
    return "\n".join(lines)

APP_TSX = """\
import { useEffect, useState } from "react";

/**
 * Starter component — proves the dev/prod wiring end to end by calling the
 * backend's own health endpoint through the SAME path a browser uses (the
 * reserved /{{SLUG}}/ namespace, routed by nginx/local-nginx to the backend —
 * never a hardcoded backend origin from the browser's side). Replace with
 * your real app; keep hitting relative paths under /{{SLUG}}/api/, not an
 * absolute backend URL, so this keeps working unmodified behind either
 * nginx (dev or prod).
 */
export default function App() {
  const [status, setStatus] = useState<string>("checking backend...");

  useEffect(() => {
    fetch("/{{SLUG}}/api/health/")
      .then((res) => setStatus(res.ok ? "backend OK" : `backend HTTP ${res.status}`))
      .catch(() => setStatus("backend unreachable"));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem" }}>
      <h1>{{TITLE}}</h1>
      <p>Vite dev server is up. Backend check: {status}</p>
    </main>
  );
}
"""

# Used INSTEAD of APP_TSX whenever the project's lib selection includes at
# least one FRONTEND_REACT_LIBS-registered module (create_project.py). This
# template itself is STATIC — identical regardless of WHICH modules were
# selected — because the actual per-module wiring lives entirely in the
# generated src/modules.tsx (render_modules_tsx below); adding/dropping a
# module changes that data file, never this one. See that function's
# docstring for the composition it emits.
APP_TSX_WITH_MODULES = """\
import { useEffect, useState } from "react";
import { ModulesPanel, ModulesProvider } from "./modules.js";

/**
 * Starter component — proves the dev/prod wiring end to end by calling the
 * backend's own health endpoint through the SAME path a browser uses (the
 * reserved /{{SLUG}}/ namespace, routed by nginx/local-nginx to the backend —
 * never a hardcoded backend origin from the browser's side), AND mounts the
 * project's selected @stapel/<module>-react pairs via the generated
 * `./modules` registry (`ModulesProvider` wires one runtime + provider per
 * selected pair; `ModulesPanel` mounts whichever pairs shipped a genuinely
 * zero-config `/default` top-level component — see modules.tsx's own
 * comments for exactly which and why). Replace with your real app; keep
 * hitting relative paths under /{{SLUG}}/api/, not an absolute backend URL,
 * so this keeps working unmodified behind either nginx (dev or prod).
 */
export default function App() {
  const [status, setStatus] = useState<string>("checking backend...");

  useEffect(() => {
    fetch("/{{SLUG}}/api/health/")
      .then((res) => setStatus(res.ok ? "backend OK" : `backend HTTP ${res.status}`))
      .catch(() => setStatus("backend unreachable"));
  }, []);

  return (
    <ModulesProvider>
      <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem" }}>
        <h1>{{TITLE}}</h1>
        <p>Vite dev server is up. Backend check: {status}</p>
        <ModulesPanel />
      </main>
    </ModulesProvider>
  );
}
"""


def render_cdn_lib_ts(slug: str) -> str:
    """``frontend/src/lib/cdn.ts`` — stapel-cdn URL resolution, written only
    when "cdn" is among the project's selected modules (cdn auto-wiring,
    cdn-scaffold-autowire.md — generalizes the hand-applied meettoday avatar
    fix). A DOCUMENTED STOPGAP: no ``@stapel/cdn-react`` client pair exists
    yet (promoting this file's logic into one is a separate follow-up, not
    blocking here).

    ``@stapel/profiles-react/default``'s ``ProfileSettings`` stores/reads a
    raw CDN reference (``Profile.avatar``, ``"<type>/<hash>"`` —
    stapel-profiles' own ``CdnImageField``) but never resolves it to a
    displayable URL itself; that is the ``avatarUrlFor(ref)`` prop it takes
    (wired in by ``render_modules_tsx``/``render_routes_tsx`` below when cdn
    is selected). The served URL shape mirrors stapel-cdn's own server-side
    template, ``Image.get_variant_url`` (stapel_cdn/models.py):
    ``{MEDIA_URL}{type}/{hash}/{tier}{branch}.webp``. This project's
    ``MEDIA_URL`` is ``"/media/<slug>/"`` (``config/settings/base.py`` —
    namespaced per service slug), proxied/aliased through nginx exactly
    like ``/static/`` (``service-configs/nginx*/*``, both unconditionally
    generated already). 160 is the smallest preview tier (stapel-cdn's
    default ``PREVIEW_SIZES``) and the rung ``useAvatarUpload``'s own
    ``uploadedUrl`` resolves to (``variant_160_url``) — kept in sync here by
    construction, not by convention alone.
    """
    return f'''\
/**
 * GENERATED — stapel-cdn URL resolution (cdn auto-wiring). See this file's
 * generator, _frontend_templates.render_cdn_lib_ts, for the full rationale.
 */
export function avatarUrlFor(ref: string): string {{
  // `ref` is format-validated server-side (stapel-profiles'
  // validate_cdn_reference, "<type>/<hash>") before it is ever stored.
  const [type, hash] = ref.split("/", 2);
  return `/media/{slug}/${{type}}/${{hash}}/160w.webp`;
}}
'''


def _profile_settings_jsx(has_cdn: bool) -> str:
    """The JSX ``<ProfileSettings .../>`` mount — with the ``avatarUrlFor``
    stopgap prop (``render_cdn_lib_ts``) wired in when cdn is selected,
    bare otherwise (regression: no cdn -> byte-identical)."""
    return (
        "<ProfileSettings avatarUrlFor={avatarUrlFor} />" if has_cdn
        else "<ProfileSettings />"
    )


def render_modules_tsx(entries: list[dict], *, has_cdn: bool = False) -> str:
    """Generates ``frontend/src/modules.tsx`` — the DATA-DRIVEN registry of
    every selected ``@stapel/<module>-react`` pair (create_project.py's
    ``FRONTEND_REACT_LIBS``, filtered to the project's actual ``--modules``
    selection, in registry order). Only called when that filtered list is
    non-empty; App.tsx then switches to ``APP_TSX_WITH_MODULES`` instead of
    the plain ``APP_TSX``.

    Each ``entries[i]`` dict: ``key`` (STAPEL_LIBS/FRONTEND_REACT_LIBS key),
    ``package`` (npm name), ``provider``/``create_runtime``/
    ``register_i18n`` (the pair's own exports — see its README's "Wire the
    app once" section, reproduced here mechanically), and an optional
    ``default_component`` — the ONE ``/default`` (antd skin) export that pair
    ships with zero required props (verified by reading each pair's own
    ``src/default/*.tsx`` prop interfaces, not guessed).

    Two things this emits:

    - ``ModulesProvider`` — one shared ``<StapelProvider>`` (the first
      selected pair's client as the default, every other pair's client
      passed via the ``clients={{ "<mod>": ... }}`` per-module override —
      exactly the multi-pair composition `@stapel/core`'s own README
      documents) wrapping one ``<XProvider runtime={...}>`` per selected
      pair, nested. ``has_cdn`` (cdn auto-wiring, cdn-scaffold-autowire.md)
      additionally registers a stopgap ``cdn`` client — the primary pair's
      client reused verbatim, same as the hand-applied meettoday fix's
      ``clients: { cdn: stapelClient }`` — so core's
      ``useStapelClient("cdn")`` seam (called unconditionally by
      ``ProfileSettings``' own avatar-upload hook) never throws for want of
      a registered client.
    - ``ModulesPanel`` — mounts every selected pair's zero-config
      ``default_component`` (if any), wrapped once in antd's
      ``<ConfigProvider theme={toAntdThemeConfig("light")}>`` themed via
      ``@stapel/tokens-antd`` (§68 bridge — the same pattern
      ``@stapel/tokens-antd``'s own README shows, and the one `AuthPanel`
      itself uses internally; nesting a second ConfigProvider inside is
      harmless). Renders nothing (but stays a valid no-arg component App.tsx
      can always import) when no selected pair has a zero-config default —
      workspaces-react's `/default` components all require a `workspaceId`
      the scaffold has no way to fabricate, so it (and every headless-only
      pair — billing/calendar/recordings, which ship no `/default` subpath
      at all) is wired provider-only here, never guessed into a broken
      mount.
    """
    needs_antd = any(e.get("default_component") for e in entries)
    # cdn auto-wiring: the avatarUrlFor stopgap only matters where
    # ProfileSettings is actually mounted (this file's ModulesPanel).
    needs_cdn_avatar_helper = has_cdn and any(e["key"] == "profiles" for e in entries)
    # A pair with no `create_runtime` is L0 — no client, no provider, no
    # backend of its own (`@stapel/attributes-react`: stapel-attributes is a
    # library with no HTTP surface at all). It contributes a catalogue and
    # nothing else, and that is read off the registry's shape rather than a
    # flag someone has to remember to set.
    runtime_entries = [e for e in entries if e.get("create_runtime")]
    # The `cdn: <other>Runtime.client` stopgap existed because no
    # @stapel/cdn-react pair existed. It does now (0.2.0), so when cdn is
    # SELECTED the real runtime registers the real client and the stopgap is
    # not merely unnecessary, it would shadow it.
    cdn_pair_wired = any(e["key"] == "cdn" for e in entries)
    stopgap_cdn_client = has_cdn and not cdn_pair_wired

    lines: list[str] = [
        "/**",
        " * GENERATED — do not hand-edit the provider nesting below. This file",
        " * is the data-driven registry of this project's selected",
        " * @stapel/<module>-react pairs (stapel-create-project's",
        " * FRONTEND_REACT_LIBS, filtered to --modules). Add or drop a pair by",
        " * changing the project's module selection and re-scaffolding — never",
        " * by editing this file's shape.",
        " */",
        'import type { ReactElement, ReactNode } from "react";',
        'import { createI18n, createStapelQueryClient, StapelProvider } from "@stapel/core";',
    ]
    if needs_antd:
        lines.append('import { ConfigProvider } from "antd";')
        lines.append('import { toAntdThemeConfig } from "@stapel/tokens-antd";')
    for e in entries:
        names = [
            n
            for n in (e.get("create_runtime"), e.get("provider"), e["register_i18n"])
            if n
        ]
        lines.append(f'import {{ {", ".join(names)} }} from "{e["package"]}";')
        if e.get("default_component"):
            lines.append(f'import {{ {e["default_component"]} }} from "{e["package"]}/default";')
    if needs_cdn_avatar_helper:
        lines.append('import { avatarUrlFor } from "./lib/cdn.js";')
    lines.append("")
    lines.append('const query = createStapelQueryClient({ cacheVersion: "0.0.0" });')
    lines.append('const i18n = createI18n({ locale: "en" });')
    lines.append("")
    for e in entries:
        key = e["key"]
        if e.get("create_runtime"):
            lines.append(
                f'const {key}Runtime = {e["create_runtime"]}({{ baseUrl: "/{key}/api/v1/" }});'
            )
        lines.append(f"{e['register_i18n']}(i18n);")
    lines.append("")
    lines.append(f"export const INSTALLED_REACT_MODULES = {_ts_string_array([e['key'] for e in entries])} as const;")
    lines.append("")

    primary = runtime_entries[0] if runtime_entries else None
    rest = runtime_entries[1:]
    lines.append("/**")
    lines.append(" * One shared `<StapelProvider>` (core config + query + i18n) plus one")
    lines.append(" * `<XProvider>` per selected pair, nested — the \"Wire the app once\"")
    lines.append(" * composition every pair's own README documents, generated once per")
    lines.append(" * module selection instead of hand-edited per pair.")
    lines.append(" */")
    lines.append("export function ModulesProvider({ children }: { children: ReactNode }): ReactElement {")
    lines.append("  return (")
    if primary is None:
        # An L0-only selection has no module client at all. `<StapelProvider>`
        # needs a `baseUrl` OR a `client`; the app's own origin is the honest
        # answer, since nothing here talks to a module.
        lines.append('    <StapelProvider baseUrl="/" queryRuntime={query} i18n={i18n}>')
    elif rest or stopgap_cdn_client:
        lines.append('    <StapelProvider')
        lines.append(f'      client={{{primary["key"]}Runtime.client}}')
        lines.append("      clients={{")
        for e in rest:
            lines.append(f'        {e["key"]}: {e["key"]}Runtime.client,')
        if stopgap_cdn_client:
            # Stopgap for a project that selected the cdn BACKEND without the
            # @stapel/cdn-react pair: reuse the primary pair's client verbatim,
            # same as the hand-applied meettoday fix's
            # `clients: { cdn: stapelClient }`, so core's
            # `useStapelClient("cdn")` seam never throws for want of one.
            lines.append(f'        cdn: {primary["key"]}Runtime.client,')
        lines.append("      }}")
        lines.append("      queryRuntime={query}")
        lines.append("      i18n={i18n}")
        lines.append("    >")
    else:
        lines.append(f'    <StapelProvider client={{{primary["key"]}Runtime.client}} queryRuntime={{query}} i18n={{i18n}}>')

    indent = "      "
    for e in runtime_entries:
        lines.append(f'{indent}<{e["provider"]} runtime={{{e["key"]}Runtime}}>')
        indent += "  "
    lines.append(f"{indent}{{children}}")
    for e in reversed(runtime_entries):
        indent = indent[:-2]
        lines.append(f'{indent}</{e["provider"]}>')
    lines.append("    </StapelProvider>")
    lines.append("  );")
    lines.append("}")
    lines.append("")

    defaults = [e for e in entries if e.get("default_component")]
    lines.append("/**")
    lines.append(" * Mounts every selected pair's zero-required-prop `/default` top-level")
    lines.append(" * component (none guessed into existence — see this file's own module")
    lines.append(" * docstring for exactly which pairs qualify and why). Must render below")
    lines.append(" * `<ModulesProvider>` (needs each pair's runtime + core's i18n).")
    lines.append(" */")
    lines.append("export function ModulesPanel(): ReactElement | null {")
    if not defaults:
        lines.append("  return null;")
        lines.append("}")
    else:
        lines.append("  return (")
        if needs_antd:
            lines.append('    <ConfigProvider theme={toAntdThemeConfig("light")}>')
            for e in defaults:
                if e["key"] == "profiles" and has_cdn:
                    lines.append(f'      {_profile_settings_jsx(has_cdn)}')
                else:
                    lines.append(f'      <{e["default_component"]} />')
            lines.append("    </ConfigProvider>")
        else:
            for e in defaults:
                if e["key"] == "profiles" and has_cdn:
                    lines.append(f'    {_profile_settings_jsx(has_cdn)}')
                else:
                    lines.append(f'    <{e["default_component"]} />')
        lines.append("  );")
        lines.append("}")
    lines.append("")

    return "\n".join(lines)


def _ts_string_array(values: list[str]) -> str:
    return "[" + ", ".join(json.dumps(v) for v in values) + "]"


# ---------------------------------------------------------------------------
# Scripted-fullstack navigation (P1) — nav.generated.ts / routes.tsx /
# ProtectedRoute.tsx / stapel.nav.json / LandingPage.tsx
#
# Consumes create_project.FRONTEND_REACT_LIBS[<key>]["nav"] — the manually
# PINNED MIRROR of that pair's own `nav-manifest.json` (same discipline as
# the version pins above this file's own comments describe; see
# create_project.py's FRONTEND_REACT_LIBS docstring and
# scripts/check_nav_manifest_sync.py, the drift gate against the sibling
# stapel-react checkout's actual nav-manifest.json files).
# ---------------------------------------------------------------------------


def nav_wired_pairs(react_entries: list[dict], *, auth_wired: bool) -> list[dict]:
    """The selected react pairs (``create_project._frontend_react_entries``
    output) that actually join the scripted nav/route tree — every selected
    pair carrying a ``nav`` mirror, EXCEPT auth's when ``auth_wired`` is
    False (the ``--no-auth`` escape hatch: a project can still wire the auth
    RUNTIME via ``modules.tsx`` — e.g. to drive `AuthPanel` by hand
    somewhere of its own choosing — without any of auth's screens joining
    "/login"/the nav menu)."""
    return [e for e in react_entries if e.get("nav") and (e["key"] != "auth" or auth_wired)]


def build_nav_route_plan(nav_pairs: list[dict]) -> dict:
    """Pure, deterministic route-tree plan ``render_routes_tsx`` turns into
    react-router v7 route objects — the SCRIPTED (no-LLM) decision tree over
    the selected pairs' mirrored nav entries (registry order).

    Two kinds of entry become a route:

    - a TOP entry whose ``route.path`` is ABSOLUTE (starts with "/", e.g.
      auth.login's "/login") mounts as its own top-level sibling route —
      NEVER nested under "/app" (a sign-in screen is reachable regardless of
      session state, the opposite of what "/app" protects).
    - a TOP entry with a RELATIVE path, or a SUBMENU entry whose
      ``placement.parentId`` resolves among the selected TOP entries, nests
      as a child of "/app" (its full path is ``"<parent-path>/<own-path>"``
      for a submenu entry — e.g. auth.security under profiles.settings
      becomes "settings/security"). A submenu entry whose parent isn't
      among the selected TOP entries is DROPPED — the exact orphan-drop
      rule ``@stapel/shell-react``'s own ``resolveNav`` documents, mirrored
      here so routing and the nav menu never disagree about what "installed"
      means.

    Returns ``{"absolute_routes": [...], "app_children": [...]}``, each a
    list of ``{"path": <route path>, "entry": <mirrored NavEntry dict>}`` —
    entries carry a ``"_package"`` key (the pair's npm package name) for
    ``render_routes_tsx``'s component imports.
    """
    all_entries = [{**entry, "_package": pair["package"]} for pair in nav_pairs for entry in pair["nav"]]
    tops = {e["id"]: e for e in all_entries if e["placement"]["level"] == "top"}
    children_by_parent: dict[str, list[dict]] = {}
    for e in all_entries:
        if e["placement"]["level"] != "submenu":
            continue
        parent_id = e["placement"].get("parentId")
        if parent_id is None or parent_id not in tops:
            continue  # orphan — dropped, not thrown (mirrors resolveNav)
        children_by_parent.setdefault(parent_id, []).append(e)

    absolute_routes: list[dict] = []
    app_children: list[dict] = []
    for top in sorted(tops.values(), key=lambda e: (e["order"], e["id"])):
        path = top["route"]["path"]
        if path.startswith("/"):
            absolute_routes.append({"path": path, "entry": top})
            continue
        app_children.append({"path": path, "entry": top})
        for child in sorted(children_by_parent.get(top["id"], []), key=lambda e: (e["order"], e["id"])):
            app_children.append({"path": f'{path}/{child["route"]["path"]}', "entry": child})

    return {"absolute_routes": absolute_routes, "app_children": app_children}


def render_nav_generated_ts(nav_pairs: list[dict]) -> str:
    """``frontend/src/nav.generated.ts`` — bakes ``INSTALLED_NAV_MANIFESTS``
    (this project's selected pairs' MIRRORED nav-manifest entries, exactly
    the shape ``PackageNavManifest[]`` from ``@stapel/core`` describes) at
    Python codegen time, then computes ``RESOLVED_NAV`` by calling
    ``@stapel/shell-react``'s own ``resolveNav`` against the committed
    ``../stapel.nav.json`` override file — the SAME pure function
    ``<AppShell/>`` itself is built on, run once at module-import time so
    editing ``stapel.nav.json`` and reloading re-resolves with no
    regeneration needed. ``reresolveNav`` re-exposes that same call for a
    host that wants to re-resolve against a DIFFERENT (e.g. freshly
    fetched) overrides object at runtime, without a rebuild.
    """
    manifests = [
        {"package": pair["package"], "version": pair["version"], "entries": pair["nav"]}
        for pair in nav_pairs
    ]
    manifests_json = json.dumps(manifests, indent=2)
    return f'''\
/**
 * GENERATED — do not hand-edit. Mirrored nav-manifest data for this
 * project's selected @stapel/<module>-react pairs (stapel-create-project's
 * FRONTEND_REACT_LIBS[<key>]["nav"] — a manually pinned mirror of each
 * pair's own nav-manifest.json, kept in sync by
 * scripts/check_nav_manifest_sync.py). Add or drop a pair's nav surface by
 * changing the project's module/--auth selection and re-scaffolding, never
 * by editing this file's shape.
 */
import type {{ PackageNavManifest }} from "@stapel/core";
import type {{ NavOverridesFile, ResolvedNavEntry }} from "@stapel/shell-react";
import {{ resolveNav }} from "@stapel/shell-react";
import stapelNavOverrides from "../stapel.nav.json";

export const INSTALLED_NAV_MANIFESTS: readonly PackageNavManifest[] = {manifests_json} as const;

/**
 * Resolved once at import time against the committed stapel.nav.json (the
 * project's deep-merge-over-default override channel) — the same call
 * @stapel/shell-react's own <AppShell/> is built on.
 */
export const RESOLVED_NAV: readonly ResolvedNavEntry[] = resolveNav(
  INSTALLED_NAV_MANIFESTS,
  stapelNavOverrides as NavOverridesFile
);

/** Re-resolve against a different (e.g. freshly-fetched) overrides object
 * at runtime — same pure function, without a rebuild. */
export function reresolveNav(overridesFile?: NavOverridesFile): readonly ResolvedNavEntry[] {{
  return resolveNav(INSTALLED_NAV_MANIFESTS, overridesFile);
}}
'''


def render_routes_tsx(
    route_plan: dict, *, auth_wired: bool, want_landing: bool, app_route_present: bool,
    has_cdn: bool = False,
) -> str:
    """``frontend/src/routes.tsx`` — react-router v7's ``createBrowserRouter``
    (v7 ships v6-future behaviour as ITS OWN default; there is no
    future-flags object to pass here, unlike v6). The decision tree (owner
    directive, P1):

    - ``"/"`` — ``<LandingPage/>`` when ``--landing``, else a redirect to
      "/app" (only reachable when routing is active at all, which this
      function assumes — ``_write_frontend_scaffold`` only calls it then).
    - one sibling route per ``route_plan["absolute_routes"]`` entry (e.g.
      "/login" -> ``<AuthPanel/>`` when auth is wired).
    - "/app" — present when ``app_route_present`` (auth wired, or at least
      one selected pair contributed a nav entry): ``<AppShell nav=
      {{RESOLVED_NAV}} mode="light"/>`` — AppShell renders its own
      ``<Outlet/>`` internally (its props carry no ``children`` slot), so
      this never re-nests one — wrapped in ``<ProtectedRoute>`` only when
      ``auth_wired`` (an unprotected "/app" is valid too: a nav-bearing
      module with no auth installed just never gates the shell). Children:
      one route per ``route_plan["app_children"]`` entry.

    ``has_cdn`` (cdn auto-wiring, cdn-scaffold-autowire.md): profiles' nav
    entry mounts ``ProfileSettings`` as a route (this is the LIVE path for
    it, not ``render_modules_tsx``'s ``ModulesPanel`` — profiles always
    carries a ``"nav"`` mirror, so ``app_route_present`` is always true once
    profiles is selected) — wired with the ``avatarUrlFor`` stopgap prop the
    same way ``render_modules_tsx`` wires its own (dead, but kept in sync)
    copy.
    """
    absolute_routes = route_plan["absolute_routes"]
    app_children = route_plan["app_children"]

    component_imports: dict[tuple[str, str], set[str]] = {}
    for r in (*absolute_routes, *app_children):
        entry = r["entry"]
        comp = entry["component"]
        component_imports.setdefault((entry["_package"], comp["subpath"]), set()).add(comp["export"])
    uses_profile_settings = any(
        "ProfileSettings" in exports for exports in component_imports.values()
    )
    needs_cdn_avatar_helper = has_cdn and uses_profile_settings

    # "/" redirects to "/app" only when there's an "/app" to redirect TO and
    # nothing already claimed "/" (LandingPage) — the only place `Navigate`
    # is used, so only import it then (an unused import would fail
    # `no-unused-vars` under strict TS/eslint).
    needs_navigate = app_route_present and not want_landing
    router_import = (
        'import { createBrowserRouter, Navigate } from "react-router";'
        if needs_navigate else
        'import { createBrowserRouter } from "react-router";'
    )
    lines: list[str] = [
        "/**",
        " * GENERATED — react-router v7 route tree (scripted-fullstack",
        " * navigation, P1 owner directive: one scripted command produces a",
        " * working navigated fullstack, no LLM in the loop). react-router v7",
        " * ships v6-future behaviour as its OWN default — there is no",
        " * future-flags object to configure here.",
        " */",
        router_import,
    ]
    if app_route_present:
        lines.append('import { AppShell } from "@stapel/shell-react/default";')
        lines.append('import { RESOLVED_NAV } from "./nav.generated.js";')
    if auth_wired:
        lines.append('import { ProtectedRoute } from "./ProtectedRoute.js";')
    if want_landing:
        lines.append('import { LandingPage } from "./LandingPage.js";')
    for (package, subpath), exports in component_imports.items():
        lines.append(f'import {{ {", ".join(sorted(exports))} }} from "{package}/{subpath}";')
    if needs_cdn_avatar_helper:
        lines.append('import { avatarUrlFor } from "./lib/cdn.js";')
    lines.append("")
    lines.append("export const router = createBrowserRouter([")

    if want_landing:
        lines.append('  { path: "/", element: <LandingPage /> },')
    elif app_route_present:
        lines.append('  { path: "/", element: <Navigate to="/app" replace /> },')

    for r in absolute_routes:
        comp = r["entry"]["component"]["export"]
        element = _profile_settings_jsx(has_cdn) if comp == "ProfileSettings" else f'<{comp} />'
        lines.append(f'  {{ path: "{r["path"]}", element: {element} }},')

    if app_route_present:
        shell_element = 'element: <AppShell nav={RESOLVED_NAV} mode="light" />,'
        lines.append("  {")
        lines.append('    path: "/app",')
        if auth_wired:
            lines.append("    element: (")
            lines.append("      <ProtectedRoute>")
            lines.append('        <AppShell nav={RESOLVED_NAV} mode="light" />')
            lines.append("      </ProtectedRoute>")
            lines.append("    ),")
        else:
            lines.append(f"    {shell_element}")
        if app_children:
            lines.append("    children: [")
            for c in app_children:
                comp = c["entry"]["component"]["export"]
                element = _profile_settings_jsx(has_cdn) if comp == "ProfileSettings" else f'<{comp} />'
                lines.append(f'      {{ path: "{c["path"]}", element: {element} }},')
            lines.append("    ],")
        lines.append("  },")

    lines.append("]);")
    lines.append("")
    return "\n".join(lines)


# ``frontend/src/ProtectedRoute.tsx`` — plain project source (like APP_TSX,
# a small template const, no per-project tokens needed). Gates "/app" behind
# an authenticated session using ONLY already-published hooks:
# `useActiveSessionReady` (@stapel/core's framework-level session ready-gate)
# and `useAuthSessionState` (@stapel/auth-react — its `status` field is
# hardened to the two-value "anonymous" | "authenticated" invariant,
# `"authenticated"` is UNREACHABLE while `user` is null). No auth-react
# change needed for this to be correct.
PROTECTED_ROUTE_TSX = """\
import type { ReactElement, ReactNode } from "react";
import { Navigate } from "react-router";
import { useActiveSessionReady } from "@stapel/core";
import { useAuthSessionState } from "@stapel/auth-react";

/**
 * Gates "/app" behind an authenticated session (scripted-fullstack
 * navigation, P1):
 *  - not ready yet (session still restoring/probing) -> render nothing, no
 *    flash of a login redirect before the real answer is known.
 *  - ready, not authenticated -> redirect to "/login".
 *  - ready, authenticated -> render children.
 */
export function ProtectedRoute({ children }: { children: ReactNode }): ReactElement | null {
  const ready = useActiveSessionReady();
  const { status } = useAuthSessionState();

  if (!ready) return null;
  if (status !== "authenticated") return <Navigate to="/login" replace />;
  return <>{children}</>;
}
"""

# ``frontend/stapel.nav.json`` — the project-root nav override file
# (deep-merge-over-default, same convention as `stapel.theme.json`): empty
# by default, the architect/advisor override channel `resolveNav` (via
# nav.generated.ts) reads at runtime to flip a menu entry's visibility/order
# without touching generated code. Schema: `NavOverridesFile` from
# `@stapel/shell-react` — `{"overrides": {"<entry-id>": {"menuVisible"?:
# bool, "order"?: number}}}`.
STAPEL_NAV_JSON = """\
{
  "overrides": {}
}
"""

# ``frontend/src/LandingPage.tsx`` — plain scaffold template (only emitted
# with --landing), a simple hero page styled entirely through §68 neutral
# colour tokens (`cssVar("<role>")` from `@stapel/tokens` — already a
# devDependency of every generated project; see PACKAGE_JSON above) — NEVER
# a raw hex/rgb (`no-raw-colors`, AGENTS.md §6). `{{CTA_HREF}}` is "/login"
# when auth is wired, else "/app" (rendered by ``_write_frontend_scaffold``
# — never guessed here).
LANDING_PAGE_TSX = """\
import type { ReactElement } from "react";
import { cssVar } from "@stapel/tokens";

/**
 * Landing page scaffold (--landing) — replace with your real marketing
 * page; keep reading colours through `cssVar("<role>")`, never a literal
 * hex/rgb (see AGENTS.md §6 "No raw colours").
 */
export function LandingPage(): ReactElement {
  return (
    <main
      style={{
        fontFamily: "system-ui, sans-serif",
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: "1rem",
        textAlign: "center",
        padding: "2rem",
        background: cssVar("surface"),
        color: cssVar("text"),
      }}
    >
      <h1 style={{ fontSize: "2.5rem", margin: 0 }}>{{TITLE}}</h1>
      <p style={{ color: cssVar("text-muted"), maxWidth: "32rem" }}>
        Welcome. This is a scaffolded landing page — replace this copy with
        your own.
      </p>
      <a
        href="{{CTA_HREF}}"
        style={{
          padding: "0.75rem 1.5rem",
          borderRadius: "0.5rem",
          textDecoration: "none",
          fontWeight: 600,
          background: cssVar("brand"),
          color: cssVar("text-on-accent"),
        }}
      >
        Get started
      </a>
    </main>
  );
}
"""


VITE_ENV_D_TS = """\
/// <reference types="vite/client" />
"""

# §68 neutral colour-role dictionary — this project's OWN copy of
# @stapel/tokens' `theme.default.json` (a host deep-merges its file OVER the
# package default; touching only e.g. `ramps.brand` still gets every other
# role). Neutral on purpose: roles are named for MEANING (surface/text/
# border/brand/link + success/warning/error/info), never for one particular
# design system's own vocabulary — antd, MUI and Tailwind bridges each
# translate the SAME roles into their own theme fields (see
# docs/pending/color-token-matrix.md). Hex is legal ONLY inside `ramps`;
# every `core` role is exactly a {light,dark} pair of `<ramp>.<step>` refs.
# `brand` is this project's default action colour (e.g. antd's
# colorPrimary / a primary button's background) — re-theme by editing the
# `ramps`/`core` entries below and regenerating (`npm run gen:tokens` in
# frontend/, or the `tokens-check` pre-commit hook on the next commit).
THEME_JSON = """\
{
  "_comment": "{{TITLE}}'s colour tokens (Sec.68 neutral colour-role dictionary). SOURCE OF TRUTH for every colour this project uses -- code never hardcodes hex/rgb (no-raw-colors). Compiled by @stapel/tokens' own `stapel-tokens` bin (never a vendored copy -- see package.json's gen:tokens scripts + the tokens-check pre-commit hook). Edit `ramps`/`core` below to re-theme; nothing else needs to change.",
  "ramps": {
    "brand": {
      "100": "#eef0fd",
      "300": "#98a5fa",
      "400": "#7c8cf8",
      "500": "#4657d9",
      "700": "#3948b8",
      "900": "#232b4d"
    }
  },
  "core": {
    "surface": { "light": "gray.25", "dark": "gray.950" },
    "surface-raised": { "light": "gray.25", "dark": "gray.850" },
    "surface-sunken": { "light": "gray.100", "dark": "gray.900" },
    "surface-overlay": { "light": "gray.25", "dark": "gray.850" },

    "text": { "light": "gray.900", "dark": "gray.100" },
    "text-muted": { "light": "gray.600", "dark": "gray.400" },
    "text-subtle": { "light": "gray.500", "dark": "gray.500" },
    "text-on-accent": { "light": "gray.25", "dark": "gray.25" },

    "border": { "light": "gray.400", "dark": "gray.700" },
    "border-subtle": { "light": "gray.300", "dark": "gray.800" },
    "focus-ring": { "light": "brand.500", "dark": "brand.300" },

    "brand": { "light": "brand.500", "dark": "brand.300" },
    "brand-hover": { "light": "brand.700", "dark": "brand.100" },
    "brand-active": { "light": "brand.900", "dark": "brand.100" },
    "brand-subtle": { "light": "brand.100", "dark": "brand.900" },

    "link": { "light": "brand.500", "dark": "brand.300" },
    "link-hover": { "light": "brand.700", "dark": "brand.100" },

    "success": { "light": "green.500", "dark": "green.300" },
    "success-bg": { "light": "green.100", "dark": "green.900" },
    "success-border": { "light": "green.300", "dark": "green.700" },
    "success-on": { "light": "gray.25", "dark": "gray.25" },

    "warning": { "light": "amber.500", "dark": "amber.300" },
    "warning-bg": { "light": "amber.100", "dark": "amber.900" },
    "warning-border": { "light": "amber.300", "dark": "amber.700" },
    "warning-on": { "light": "gray.25", "dark": "gray.25" },

    "error": { "light": "red.500", "dark": "red.300" },
    "error-bg": { "light": "red.100", "dark": "red.900" },
    "error-border": { "light": "red.300", "dark": "red.700" },
    "error-on": { "light": "gray.25", "dark": "gray.25" },

    "info": { "light": "blue.500", "dark": "blue.300" },
    "info-bg": { "light": "blue.100", "dark": "blue.900" },
    "info-border": { "light": "blue.300", "dark": "blue.700" },
    "info-on": { "light": "gray.25", "dark": "gray.25" }
  }
}
"""

# `reserved-paths.json` lives at the PROJECT root (one level up from
# frontend/) — stapel/no-reserved-backend-route's zero-config discovery only
# walks up looking for a pnpm-workspace.yaml/pnpm-lock.yaml marker, which a
# stapel monolith isn't, so it never finds it without this override
# (AGENTS.md's `cd frontend && npx eslint .` always runs with frontend/ as
# cwd — the relative path below resolves against THAT, per the plugin's own
# readFileSync(settings.reservedPathsFile) contract, not against this file's
# location). See create_project._write_reserved_paths_json and
# stapel-react/packages/eslint-plugin's README "reserved-paths.json" section
# for the schema both sides agree on.
ESLINT_CONFIG_JS = """\
import stapel from "@stapel/eslint-plugin";

export default [
  ...stapel.configs.recommended,
  {
    settings: {
      stapel: {
        reservedPathsFile: "../reserved-paths.json",
      },
    },
  },
];
"""

GITIGNORE = """\
node_modules/
dist/
.env
.env.local
*.log
"""

# The one-shot publish step, run inside the `export` stage. Not a one-liner in
# CMD any more, because the thing it replaced was a one-liner with two real
# defects (verdict 2026-08-05, tasks/fable/frontend-delivery-split-repo.md):
#
#   rm -rf /output/* && cp -r dist/. /output/
#
#   1. The window between `rm` and the end of `cp` is seconds long, and during
#      it the site 404s. Not theoretical — it is every deploy.
#   2. It DELETES the previous build's hashed assets. Vite's chunks are
#      content-addressed, so a tab that was open before the deploy asks for a
#      chunk that no longer exists and dies with a chunk-load error. The user
#      sees a broken app, not a new one.
#
# So: each build lands in its own directory and `current` is repointed at it,
# with the previous FRONTEND_KEEP_PREVIOUS builds left in place so open tabs
# keep resolving their chunks.
#
# Honest limit: `ln -sfn` is unlink+symlink, not a rename — there IS a
# sub-millisecond window where `current` does not exist. That is not zero, and
# this comment is not going to claim it is. It replaces a multi-second window
# with a sub-millisecond one; making it truly atomic needs `mv -T`, which
# busybox (alpine) does not reliably provide.
FRONTEND_PUBLISH_SH = """\
#!/bin/sh
# Publish dist/ into the volume mounted at /output. See the Dockerfile comment.
set -eu

OUT="${OUTPUT_DIR:-/output}"
KEEP="${FRONTEND_KEEP_PREVIOUS:-2}"
BUILD_ID="${BUILD_ID:-$(date -u +%Y%m%d%H%M%S)}"

if [ ! -d dist ]; then
    echo "frontend-publish: no dist/ — the build stage produced nothing" >&2
    exit 1
fi

mkdir -p "$OUT/$BUILD_ID"
cp -R dist/. "$OUT/$BUILD_ID/"

# Repoint. Old builds stay until pruned below, so a browser tab that loaded the
# previous index.html can still fetch its chunks.
ln -sfn "$BUILD_ID" "$OUT/current"

# Prune: keep the newest $KEEP builds BESIDES the live one. Never touch
# `current` itself, and never fail the deploy over a prune error.
if [ "$KEEP" -ge 0 ] 2>/dev/null; then
    ls -1t "$OUT" 2>/dev/null \\
        | grep -v '^current$' \\
        | tail -n "+$((KEEP + 2))" \\
        | while read -r old; do
            [ -d "$OUT/$old" ] && rm -rf "$OUT/$old" || true
        done
fi

echo "frontend-publish: $OUT/current -> $BUILD_ID"
"""

# Multi-stage: `build` produces the static bundle; `export` is the one-shot
# stage docker-compose.yml (prod) runs to publish it into the frontend-dist
# volume nginx serves from. This image is never a long-lived service.
#
# In split-repo (microservice) projects this same image is what the frontend
# repo PUBLISHES to a registry under an immutable `sha-<gitsha>` tag — the
# backend's compose pulls it by that pin instead of building it. It carries the
# dist and the publish step, deliberately NOT an nginx: the project's own nginx
# stays the single boundary owning reserved paths, TLS, the proxy table and the
# cache canon.
# Without this, `COPY . .` drags node_modules (a host-built tree, wrong
# platform, hundreds of MB) and a stale local dist/ into the image — the stale
# dist being the nastier of the two, since the publish step would ship it if
# the build ever failed to overwrite it.
DOCKERIGNORE = """\
node_modules
dist
.git
.env
.env.*
*.log
coverage
.DS_Store
"""

DOCKERFILE_TEMPLATE = """\
FROM node:22-alpine AS build
WORKDIR /app
{{INSTALL}}
COPY . .
RUN {{BUILD_CMD}}

# Prod canon (§57): `docker compose run --rm frontend-build` (or the one-shot
# service in docker-compose.yml) publishes dist/ into whatever is mounted at
# /output — the frontend-dist volume the main nginx mounts read-only.
FROM build AS export
COPY frontend-publish.sh /usr/local/bin/frontend-publish
RUN chmod +x /usr/local/bin/frontend-publish
CMD ["/usr/local/bin/frontend-publish"]
"""

# Install steps per package manager. The LOCKFILE decides — writing `npm
# install` into a repo whose lockfile is pnpm's does not fail loudly, it
# silently resolves a DIFFERENT dependency tree than every developer has, and
# the image you ship stops matching the app anyone tested.
_INSTALL_STEPS = {
    "npm": "COPY package*.json ./\nRUN npm ci",
    # `dangerouslyAllowAllBuilds` looks alarming and is the correct call here:
    # pnpm 10 refuses dependency lifecycle scripts unless the repo lists them
    # in `pnpm.onlyBuiltDependencies`, and a Docker build cannot answer the
    # interactive `pnpm approve-builds` prompt — the install just exits 1
    # (ERR_PNPM_IGNORED_BUILDS; measured on ironmemo-frontend, where esbuild
    # and @tailwindcss/oxide both need theirs). Those same scripts already run
    # on every developer's machine — esbuild without its postinstall has no
    # binary and the app does not build at all — so allowing them in the build
    # container REPRODUCES the local situation rather than widening trust.
    # A repo that wants a narrower answer declares `onlyBuiltDependencies` in
    # package.json; this line then changes nothing.
    "pnpm": (
        "RUN corepack enable && pnpm config set dangerouslyAllowAllBuilds true\n"
        "COPY package.json pnpm-lock.yaml ./\n"
        "RUN pnpm install --frozen-lockfile"
    ),
    "yarn": (
        "RUN corepack enable\n"
        "COPY package.json yarn.lock ./\n"
        "RUN yarn install --immutable"
    ),
}
_BUILD_CMDS = {"npm": "npm run build", "pnpm": "pnpm build", "yarn": "yarn build"}


def detect_package_manager(repo) -> str:
    """npm | pnpm | yarn, from the lockfile actually committed."""
    if (repo / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (repo / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def render_dockerfile(package_manager: str = "npm") -> str:
    if package_manager not in _INSTALL_STEPS:
        raise ValueError(f"unknown package manager: {package_manager!r}")
    return DOCKERFILE_TEMPLATE.replace(
        "{{INSTALL}}", _INSTALL_STEPS[package_manager]
    ).replace("{{BUILD_CMD}}", _BUILD_CMDS[package_manager])


# The scaffold's own frontend is npm (that is what stapel-create-project
# writes), so this stays the historical constant for it.
DOCKERFILE = render_dockerfile("npm")

README_MD = """\
# {{SLUG}}-frontend

Vite + React + TypeScript, scaffolded by `stapel-create-project` (§57 dev/prod
compose + nginx canon).

## Dev

`docker compose -f docker-compose.local.yml up` starts this alongside the
backend and local-nginx — local-nginx routes the reserved backend namespace
(`/{{SLUG}}/`, `/staticfiles/`, `/media/`) to Django and everything else to
this Vite dev server (logs visible via `docker compose logs -f frontend`).

Running `npm run dev` standalone (no local-nginx) also works — see
`vite.config.ts`'s own dev-server proxy, pointed at `VITE_BACKEND_TARGET`
(env var, compose-network default, override in `.env` for a native backend).

## Prod

`docker compose build frontend-build && docker compose run --rm frontend-build`
(also wired as a normal `docker compose up` dependency) builds this app and
copies `dist/` into the `frontend-dist` volume; the project's nginx then
serves it as static files with an SPA fallback (`try_files ... /index.html`),
same container that already serves `/staticfiles/`, `/media/` and proxies the
backend's own `/{{SLUG}}/` routes — see the project root README and
`service-configs/nginx/nginx.conf`.

## Reserved namespace — do not claim these routes

The backend owns `/{{SLUG}}/*`, `/admin/*`, `/staticfiles/*`, `/media/*` AND
every selected stapel module's own prefix — the full generated list is the
proxy table in `vite.config.ts` (kept in lockstep with nginx; both are
emitted from the project's lib selection). This app's own client-side router
must not define a route under any of those prefixes.
"""


# ---------------------------------------------------------------------------
# Public surface — the storefront container's SOURCE half
# (the public-storefront spec §3.3 / §6.1; emitted by
# ``stapel-frontend-repo-init --surface public``)
#
# The monolith scaffold above puts a frontend INSIDE the project. A
# microservice fleet's frontend lives in its own repository — that is what
# makes it a microservice fleet — and until now this toolchain wrote only the
# DELIVERY half into that repo (Dockerfile + publish script + CI job) and not
# one line of React. The renderers below are the missing half, and they are
# renderers in THIS module rather than a second generator on purpose: a
# storefront and a monolith frontend disagree about chrome and about who may
# see which screen, and about nothing else. Two generators would have to agree
# about the rest by hand, forever.
# ---------------------------------------------------------------------------

# The container's own nav entry for the member area. It cannot come from a
# pair (no module owns "the account section"), and it cannot come from
# `stapel.nav.json` either: `NavOverridesFile` carries exactly `menuVisible`
# and `order` per existing id, so an override file can retune an entry and
# can never ADD one. So the container contributes its own one-entry
# `PackageNavManifest` — which is also what makes `listings.mine` /
# `listings.favorites` (both `placement.parentId === "account.root"`) resolve
# instead of being dropped as orphans. The override file still gets an
# `account.root` stanza, so the tuning channel spec §5.2 asks for is real.
ACCOUNT_ROOT_ENTRY = {
    "id": "account.root",
    "labelKey": "storefront.nav.account",
    "icon": "UserOutlined",
    "route": {"path": "/account"},
    "component": {"export": "AccountHome", "subpath": "."},
    "placement": {"level": "top"},
    "menuVisibleDefault": True,
    "requiresAuth": True,
    "surface": "member",
    "order": 100,
}

# How a nav entry's component is MOUNTED — read off each component's own prop
# interface in the sibling stapel-react checkout, never guessed. This is the
# table that keeps generation from emitting a mount that does not compile, and
# it is the reason spec §6.1 item 11 (a placeholder that NAMES the absence) is
# mechanical rather than a promise.
#
# Four shapes:
#   {}                       zero REQUIRED props — mount it directly.
#   {"route_params": {...}}  the component needs the route's own parameters
#                            (`:id`, `:slug`); generation emits a one-file page
#                            wrapper that reads them via `useParams()` and says
#                            so when the address does not parse.
#   {"adapter": (hook, sub)} the component needs a binding the pair itself
#                            publishes on another subpath (search's
#                            `useRouterSearchParams` from `./router` — the pair
#                            keeps react-router out of its main entry, so the
#                            container supplies the binding).
#   {"container": (...)}     the component needs props only the CONTAINER can
#                            supply — the cross-pair slots spec §6.2 lists as
#                            wiring. Generation emits the placeholder; it does
#                            NOT fabricate an empty value, because an empty
#                            feature schema is not "no schema yet", it is "this
#                            category has no attributes", which is a lie with a
#                            straight face.
#   {"local": "Name"}        a container-local component this generator writes.
#
# An id ABSENT from this table falls through to the placeholder, deliberately:
# a pair that adds a screen this stapel-tools does not know about gets a page
# naming that fact, not a mount that may or may not compile. The test
# `test_nav_entry_mounts_covers_every_registered_entry` keeps the gap visible.
NAV_ENTRY_MOUNTS: dict[str, dict] = {
    # auth-react 0.16.0 — AuthPanel/QrConfirmPanel/SecuritySettings: every
    # prop optional (src/default/*.tsx).
    "auth.login": {},
    "auth.qr_confirm": {},
    "auth.security": {},
    # notifications-react 0.9.1 — NotificationFeedList: `limit?` only.
    "notifications.feed": {},
    # profiles-react 0.18.2 — ProfileSettings: every prop optional.
    "profiles.settings": {},
    # categories-react 0.2.0
    "categories.catalog": {},
    "categories.category": {"route_params": {"slug": "string"}},
    # chat-react 0.2.0 — ConversationListPanel: limit/openHref/onOpen, all
    # optional.
    "chat.conversations": {},
    # listings-react 0.2.0
    "listings.detail": {"route_params": {"id": "number"}},
    "listings.compose": {"container": ("features",)},
    "listings.mine": {},
    "listings.favorites": {},
    # search-react 0.2.0 — SearchPage's `adapter` is REQUIRED and the pair
    # deliberately does not import react-router from its main entry (§13.4
    # item 6), so the binding comes from its own `./router` subpath.
    # SearchPage also requires `defaultType` — the doc type to search when the
    # URL carries none. That is a DEPLOYMENT fact (`STAPEL_SEARCH["SOURCES"]`
    # names the types this fleet indexes), so it is satisfiable from a
    # generator option and never from a guess: `--doc-type listing` mounts the
    # page, and its absence gets the placeholder naming the prop. Guessing it
    # would send every search to a type the backend refuses, on a page that
    # looks perfectly wired.
    "search.results": {
        "adapter": ("useRouterSearchParams", "router"),
        "option_props": {"defaultType": "doc_type"},
    },
    "search.ranking": {},
    # gdpr-react 0.1.0 — PrivacyPane/PrivacyAdminPane: every prop optional.
    "account.privacy": {},
    "admin.privacy": {},
    # video-react 0.1.0 — ScopeUsagePane: every prop optional.
    "admin.usage": {},
    # the container's own.
    "account.root": {"local": "AccountHome"},
}


def public_nav_pairs(entries: list[dict]) -> list[dict]:
    """The selected pairs that contribute nav entries, in registry order.
    A pair with no nav manifest (cdn, reviews, attributes — each for a reason
    recorded in its own spec section) contributes providers and i18n and no
    routes; that is not an omission to repair here."""
    return [e for e in entries if e.get("nav")]


def public_nav_manifests(nav_pairs: list[dict], *, app_package: str) -> list[dict]:
    """`INSTALLED_NAV_MANIFESTS` for the container: every selected pair's
    mirrored manifest PLUS the container's own one-entry manifest carrying
    `account.root` (see ACCOUNT_ROOT_ENTRY)."""
    manifests = [
        {"package": pair["package"], "version": pair["version"], "entries": pair["nav"]}
        for pair in nav_pairs
    ]
    manifests.append(
        {"package": app_package, "version": "0.0.0", "entries": [ACCOUNT_ROOT_ENTRY]}
    )
    return manifests


def build_public_route_plan(manifests: list[dict]) -> dict:
    """The public container's route tree, as pure data.

    Mirrors `resolveNav`'s own rules (orphan submenu entries are DROPPED, tops
    sort by `(order, id)`) and adds the one rule that is the public
    container's own: the SURFACE decides which side of the member gate a route
    lands on, and an entry's path shape decides where it hangs.

      * `surface: "public"`, absolute path  -> a sibling under the shell.
      * `surface: "member"`, absolute path  -> a sibling under the shell, but
        inside `<MemberGate>` (e.g. `/new` — composing a listing is not a
        `/account` screen, yet it is nobody's business but a member's).
      * `surface: "member"`, relative path  -> a child of `/account`.
      * `account.root` itself                -> the `/account` route, with its
        own component as that route's INDEX.
      * a submenu entry                      -> a child of its parent's path.

    Returns ``{"public": [...], "member_absolute": [...],
    "account_children": [...], "account_entry": <entry|None>}`` where each
    route is ``{"path": str, "entry": entry, "index": bool}``. `entry` carries
    a `"_package"` key for the import the renderer emits.
    """
    all_entries = [
        {**entry, "_package": m["package"]} for m in manifests for entry in m["entries"]
    ]
    tops = {e["id"]: e for e in all_entries if e["placement"]["level"] == "top"}
    children_by_parent: dict[str, list[dict]] = {}
    for e in all_entries:
        if e["placement"]["level"] != "submenu":
            continue
        parent_id = e["placement"].get("parentId")
        if parent_id is None or parent_id not in tops:
            continue  # orphan — dropped, not thrown (mirrors resolveNav)
        children_by_parent.setdefault(parent_id, []).append(e)

    def surface_of(entry: dict) -> str:
        return entry.get("surface") or ("member" if entry["requiresAuth"] else "public")

    public: list[dict] = []
    member_absolute: list[dict] = []
    account_children: list[dict] = []
    account_entry: dict | None = None

    def kids(entry_id: str) -> list[dict]:
        return sorted(children_by_parent.get(entry_id, []), key=lambda e: (e["order"], e["id"]))

    for top in sorted(tops.values(), key=lambda e: (e["order"], e["id"])):
        path = top["route"]["path"]
        surface = surface_of(top)
        if top["id"] == ACCOUNT_ROOT_ENTRY["id"]:
            account_entry = top
            for child in kids(top["id"]):
                account_children.append(
                    {"path": child["route"]["path"], "entry": child, "index": False}
                )
            continue
        if path.startswith("/"):
            bucket = public if surface == "public" else member_absolute
            bucket.append({"path": path, "entry": top, "index": False})
            for child in kids(top["id"]):
                bucket.append(
                    {
                        "path": f'{path}/{child["route"]["path"]}',
                        "entry": child,
                        "index": False,
                    }
                )
            continue
        # A relative path under a PUBLIC surface has no parent to be relative
        # to on a storefront (there is no "/app"): it becomes a root-level
        # sibling. A member one is a child of /account.
        if surface == "public":
            public.append({"path": f"/{path}", "entry": top, "index": False})
            for child in kids(top["id"]):
                public.append(
                    {
                        "path": f'/{path}/{child["route"]["path"]}',
                        "entry": child,
                        "index": False,
                    }
                )
        else:
            account_children.append({"path": path, "entry": top, "index": False})
            for child in kids(top["id"]):
                account_children.append(
                    {
                        "path": f'{path}/{child["route"]["path"]}',
                        "entry": child,
                        "index": False,
                    }
                )

    return {
        "public": public,
        "member_absolute": member_absolute,
        "account_children": account_children,
        "account_entry": account_entry,
    }


# The container's OWN i18n copy. English only, and that is a boundary
# decision rather than a shortcut: chrome copy belongs to the library (each
# pair ships `./i18n/ru`, `./i18n/es`), while a storefront's own sentences are
# PRODUCT copy — inventing translations for them here would put a product's
# voice inside a code generator. `--locale ru` therefore registers each pair's
# real catalogue AND registers this English floor under the same locale, so a
# key always resolves to a sentence rather than to a raw key.
STOREFRONT_I18N_EN: dict[str, str] = {
    "storefront.nav.account": "Account",
    "storefront.home.title": "Storefront home",
    "storefront.home.body": (
        "The home page is a composite screen — a category carousel from one "
        "pair over a newest-first result list from another — so it belongs to "
        "the container that composes them, not to either pair. Compose it "
        "here."
    ),
    "storefront.account.title": "Account",
    "storefront.account.body": (
        "The account landing is the container's to compose from the member "
        "sections in the menu."
    ),
    "storefront.placeholder.title": "This screen is declared, not wired",
    "storefront.placeholder.body": (
        "An installed pair declares this screen in its nav manifest, and the "
        "generator did not mount it: the component needs props only this "
        "container can supply. Wire them and replace this page."
    ),
    "storefront.placeholder.unknown": (
        "This build has no mount recipe for the nav entry below, so it was "
        "generated as a named gap rather than as a screen."
    ),
    "storefront.placeholder.entry": "Nav entry",
    "storefront.placeholder.component": "Component",
    "storefront.placeholder.missing": "Props the container must supply",
    "storefront.gate.asking": "Checking your access",
    "storefront.gate.unavailable.title": "We could not check your access",
    "storefront.gate.unavailable.body": (
        "This is not a refusal — the answer did not arrive. Nothing about "
        "your account has changed."
    ),
    "storefront.gate.retry": "Try again",
    "storefront.route.invalid.title": "This address does not name a record",
    "storefront.route.invalid.body": (
        "The parameter in the address could not be read, so there is nothing "
        "to open. Check the link."
    ),
}


def storefront_i18n_symbol(key: str) -> str:
    """`"storefront.gate.unavailable.title"` -> `"gateUnavailableTitle"` — the
    camel name the generated `STOREFRONT_I18N_KEYS` object uses, derived
    mechanically so a key and its symbol cannot drift."""
    parts = key.removeprefix("storefront.").split(".")
    head, *rest = parts
    return head + "".join(word.capitalize() for word in rest)


def render_storefront_i18n_ts(locale: str) -> str:
    """``src/i18n/keys.ts`` — the container's own key registry, its English
    floor, and the registrar the app calls once at startup."""
    symbols = "\n".join(
        f'  {storefront_i18n_symbol(key)}: {json.dumps(key)},'
        for key in STOREFRONT_I18N_EN
    )
    entries = "\n".join(
        f'  {json.dumps(key)}: {json.dumps(value)},'
        for key, value in STOREFRONT_I18N_EN.items()
    )
    return f'''\
/**
 * GENERATED — the CONTAINER's own i18n copy (spec §6.2 item 9: every pair's
 * catalogue is registered, and the container's bundle goes on last).
 *
 * English only. A pair's chrome copy is library copy and ships translated
 * (`<pkg>/i18n/ru`, `<pkg>/i18n/es`); these sentences are the storefront's
 * own product voice, and a code generator inventing a product's voice in
 * another language is how a stand ends up speaking two dialects.
 * `registerStorefrontI18n` therefore registers this floor into WHATEVER
 * locale the app runs in, so a key always resolves to a sentence — never to a
 * raw key on screen — and the product replaces it with its own copy when it
 * has one.
 *
 * These are also the keys `eslint.config.js` hands `stapel/i18n-key-exists`
 * as `settings.stapel.i18nKeys`: a typo in generated code fails lint here
 * rather than printing itself on a page.
 */
import type {{ I18nDictionary, I18nEngine }} from "@stapel/core";

export const STOREFRONT_I18N_KEYS = {{
{symbols}
}} as const;

export type StorefrontI18nKey =
  (typeof STOREFRONT_I18N_KEYS)[keyof typeof STOREFRONT_I18N_KEYS];

export const STOREFRONT_I18N_BUNDLE_EN: I18nDictionary = {{
{entries}
}};

/** Register the container's own copy. Call LAST, after every pair's. */
export function registerStorefrontI18n(
  engine: I18nEngine,
  locale = {json.dumps(locale)}
): void {{
  engine.registerBundle(locale, STOREFRONT_I18N_BUNDLE_EN);
}}
'''


NAV_PLACEHOLDER_TSX = '''\
/**
 * GENERATED — the page that NAMES an absence instead of impersonating a
 * screen (spec §6.1 item 11).
 *
 * Two absences reach here, and they are different sentences:
 *
 *  - a declared screen whose component needs props only this container can
 *    supply (the cross-pair slots of spec §6.2 — a listing composer needs the
 *    chosen category's feature schema, which lives in another pair). The
 *    generator will not fabricate an empty value for one: an empty feature
 *    schema does not read as "not wired yet", it reads as "this category has
 *    no attributes", which is a lie told with a straight face.
 *  - a nav entry this build has no mount recipe for at all — a pair added a
 *    screen after this generator last learned about it.
 *
 * Both print the entry id, the component and (for the first) the exact prop
 * names, because "wire it up" without saying WHAT is the same as silence.
 */
import type { ReactElement } from "react";
import { Alert, Space, Typography } from "antd";
import { useT } from "@stapel/core";
import { STOREFRONT_I18N_KEYS } from "./i18n/keys.js";

export interface NavPlaceholderProps {
  /** The nav entry id this route came from, e.g. `"listings.compose"`. */
  readonly entryId: string;
  /** The component the entry names, e.g. `"ListingComposerPage"`. */
  readonly component: string;
  /** The package that publishes it. */
  readonly pkg: string;
  /** Props the container has to supply. Empty = no recipe at all. */
  readonly missing?: readonly string[];
}

export function NavPlaceholder(props: NavPlaceholderProps): ReactElement {
  const t = useT();
  const missing = props.missing ?? [];
  return (
    <Alert
      type="info"
      showIcon
      data-testid="nav-placeholder"
      message={t(STOREFRONT_I18N_KEYS.placeholderTitle)}
      description={
        <Space direction="vertical" size="small">
          <Typography.Paragraph>
            {missing.length > 0
              ? t(STOREFRONT_I18N_KEYS.placeholderBody)
              : t(STOREFRONT_I18N_KEYS.placeholderUnknown)}
          </Typography.Paragraph>
          <Typography.Text type="secondary">
            {t(STOREFRONT_I18N_KEYS.placeholderEntry)}
            {": "}
            <Typography.Text code>{props.entryId}</Typography.Text>
          </Typography.Text>
          <Typography.Text type="secondary">
            {t(STOREFRONT_I18N_KEYS.placeholderComponent)}
            {": "}
            <Typography.Text code>
              {props.component}
              {" \\u2190 "}
              {props.pkg}
            </Typography.Text>
          </Typography.Text>
          {missing.length > 0 ? (
            <Typography.Text type="secondary">
              {t(STOREFRONT_I18N_KEYS.placeholderMissing)}
              {": "}
              <Typography.Text code>{missing.join(", ")}</Typography.Text>
            </Typography.Text>
          ) : null}
        </Space>
      }
    />
  );
}

/**
 * The other honest empty: the address itself did not parse. A `/l/:id` whose
 * `:id` is not a number has no record behind it, and rendering the pane with
 * `NaN` would ask the backend about nothing and show the answer as if it were
 * about something.
 */
export function RouteParamProblem(props: { readonly param: string }): ReactElement {
  const t = useT();
  return (
    <Alert
      type="warning"
      showIcon
      data-testid="route-param-problem"
      message={t(STOREFRONT_I18N_KEYS.routeInvalidTitle)}
      description={
        <Typography.Text type="secondary">
          {t(STOREFRONT_I18N_KEYS.routeInvalidBody)}
          {" "}
          <Typography.Text code>{props.param}</Typography.Text>
        </Typography.Text>
      }
    />
  );
}
'''


def render_mandate_source_ts() -> str:
    """``src/mandateSource.ts`` — the container's implementation of
    `@stapel/core`'s `MandateSource` seam (spec §3.2 / §6.1 item 3).

    Trivial AND complete, which is the whole requirement: every arm of
    `MandateState` is produced by a named branch, including the two that are
    not verdicts. A storefront has no workspaces, so `useMandateState()` from
    `@stapel/workspaces-react` — today's only implementation of the axis — is
    the wrong dependency, not merely a heavy one: it would mount the
    multi-tenant metaphor, its queries and its wire types inside an anonymous
    marketplace to answer one boolean.

    Written as `.ts` (not `.tsx`) deliberately: this file computes the axis
    and renders nothing. The provider that carries it is `MandateProvider`
    from core, mounted in `modules.tsx`.
    """
    return '''\
/**
 * GENERATED — this container's `MandateSource` (@stapel/core's mandate seam).
 *
 * The whole derivation, and every branch named:
 *
 *   session still bootstrapping  -> `mandateAsking()`      a WAIT
 *   signed in                    -> `mandateResolved("member")`
 *   no session / signed out      -> `mandateResolved("anonymous")`
 *   nobody tracks sessions here  -> `mandateUnavailable(...)`  an OUTAGE
 *
 * ── Why "guest" is never produced, said out loud ──────────────────────────
 *
 * `MandatePrincipal` has three values and this source emits two.
 * `"guest"` means "signed in, and holds no mandate" — a real and easy-to-miss
 * state in a tenant product, where membership is a thing you can lack. A
 * storefront has no mandate to lack: anyone signed in IS a member of the one
 * thing there is to be a member of. Emitting `"guest"` here would invent a
 * refusal nobody wrote. It is written down rather than silently omitted
 * because a reader has to be able to tell "impossible here" from "forgotten".
 * Every CONSUMER still handles it — `matchMandate`'s five arms are required —
 * and `MemberGate` sends it down the anonymous path with that reason
 * recorded.
 *
 * ── The outage arm is not paranoia ────────────────────────────────────────
 *
 * `useActiveSessionStatus()` answers `null` when no session-owning module has
 * registered a manager — a wiring fault, or an auth pair that failed to load.
 * Folding that into `"anonymous"` would render "we could not ask" as "you may
 * not", which is the exact substitution the mandate axis exists to prevent.
 */
import { mandateAsking, mandateResolved, mandateUnavailable } from "@stapel/core";
import { StapelApiError, useActiveSessionStatus } from "@stapel/core";
import type { MandateSource } from "@stapel/core";

/** The outage this container can actually hit: no session manager at all. */
const NO_SESSION_MODULE = new StapelApiError({
  code: "stapel.error.unknown",
  message:
    "No session-owning module has registered a session manager, so this app " +
    "cannot tell whether anyone is signed in. Wire @stapel/auth-react's " +
    "runtime in src/modules.tsx.",
  status: 0,
});

/**
 * The container's mandate source. A hook, because the axis is derived from a
 * store the app subscribes to — the seam takes what a host HAS in hand at
 * render time (`MandateSource.state`), not a function to call at will.
 */
export function useStorefrontMandateSource(): MandateSource {
  const status = useActiveSessionStatus();
  if (status === null) return { state: mandateUnavailable(NO_SESSION_MODULE) };
  if (status === "initializing") return { state: mandateAsking() };
  if (status === "authenticated") return { state: mandateResolved("member") };
  // "anonymous" (a real anonymous user row) and "unauthenticated" (no session
  // at all) open the same doors, which is why the principal has one name for
  // both — see @stapel/core's MandatePrincipal.
  return { state: mandateResolved("anonymous") };
}
'''


MEMBER_GATE_TSX = '''\
/**
 * GENERATED — the member gate (spec §6.1 item 6).
 *
 * `matchMandate`'s five arms are all required, and that is the mechanism
 * rather than an inconvenience: the two unresolved arms cannot be quietly
 * merged into a principal's branch, so this screen physically cannot tell
 * someone "you may not" when the truth is "we could not ask". Each arm here
 * renders a DIFFERENT thing, and none of them renders nothing:
 *
 *   member       -> the routes below (`<Outlet/>`).
 *   anonymous    -> `/login?next=<where they were going>`, so signing in
 *                   lands them where they meant to be and not on a home page.
 *   guest        -> the same redirect, WITH the reason recorded: a storefront
 *                   never produces `"guest"` (src/mandateSource.ts says why),
 *                   and if one ever arrives, the honest treatment is the one
 *                   for a person who holds no mandate.
 *   asking       -> a skeleton. A wait looks like a wait.
 *   unavailable  -> the error, explained, with a retry. NOT a redirect to
 *                   sign-in: bouncing someone to a login form because a
 *                   backend hiccuped tells them they are logged out when they
 *                   are not.
 */
import type { ReactElement } from "react";
import { Alert, Button, Skeleton, Space, Typography } from "antd";
import { Navigate, Outlet, useLocation } from "react-router";
import { matchMandate, useErrorText, useMandate, useT } from "@stapel/core";
import { STOREFRONT_I18N_KEYS } from "./i18n/keys.js";

const SIGN_IN_PATH = "/login";

function MandateOutage(props: { readonly error: unknown }): ReactElement {
  const t = useT();
  const errorText = useErrorText();
  return (
    <Alert
      type="error"
      showIcon
      data-testid="member-gate-outage"
      message={t(STOREFRONT_I18N_KEYS.gateUnavailableTitle)}
      description={
        <Space direction="vertical" size="small">
          <Typography.Paragraph>
            {t(STOREFRONT_I18N_KEYS.gateUnavailableBody)}
          </Typography.Paragraph>
          <Typography.Text type="secondary">{errorText(props.error)}</Typography.Text>
          <Button
            onClick={() => {
              // A full reload, and the comment is the honest part: the mandate
              // is derived from the session bootstrap, and @stapel/core
              // publishes no re-probe entry point for it (useActiveSessionStatus
              // reads the store, it cannot re-ask). Reloading re-runs the
              // bootstrap, which is what "try again" has to mean until core
              // grows a narrower one. UPSTREAM ASK: a `retry()` on the session
              // manager's public surface.
              window.location.reload();
            }}
            data-analytics="none"
            data-analytics-reason="reload-not-a-flow-step"
          >
            {t(STOREFRONT_I18N_KEYS.gateRetry)}
          </Button>
        </Space>
      }
    />
  );
}

export function MemberGate(): ReactElement {
  const mandate = useMandate();
  const location = useLocation();
  const t = useT();
  const next = encodeURIComponent(`${location.pathname}${location.search}`);
  const toSignIn = <Navigate to={`${SIGN_IN_PATH}?next=${next}`} replace />;

  return matchMandate(mandate, {
    member: () => <Outlet />,
    anonymous: () => toSignIn,
    // Unreachable on a storefront by construction — see src/mandateSource.ts.
    // Handled anyway, and handled the same way, because "cannot happen" is a
    // claim about today's derivation, not about this file.
    guest: () => toSignIn,
    asking: () => (
      <Skeleton
        active
        data-testid="member-gate-asking"
        title={{ width: 240 }}
        paragraph={{ rows: 4 }}
        aria-label={t(STOREFRONT_I18N_KEYS.gateAsking)}
      />
    ),
    unavailable: (error) => <MandateOutage error={error} />,
  });
}
'''


def _placeholder_jsx(entry: dict, missing: tuple = ()) -> str:
    """The element for a nav entry generation refuses to mount, carrying
    everything a reader needs to act: which entry, which component, which
    package, and exactly which props are the container's to supply."""
    comp = entry["component"]["export"]
    parts = [
        f'entryId={json.dumps(entry["id"])}',
        f"component={json.dumps(comp)}",
        f'pkg={json.dumps(entry["_package"])}',
    ]
    if missing:
        parts.append("missing={" + _ts_string_array(list(missing)) + "}")
    return "<NavPlaceholder " + " ".join(parts) + " />"


def _wrapper_name(entry: dict) -> str:
    return f'{entry["component"]["export"]}Route'


def render_nav_page_wrapper_tsx(
    entry: dict, mount: dict, options: dict | None = None
) -> str:
    """``src/pages/<Export>Route.tsx`` — the one-file binding between a route
    and a component that needs something the ROUTE knows.

    Two reasons a wrapper exists, and neither is decoration:

     * route parameters. `<ListingDetailPane id={…}/>` wants a number, the
       address carries a string, and `Number("abc")` is `NaN` — which asks the
       backend about nothing and then renders the answer as if it were about
       something. The wrapper parses, and says so when the address does not.
     * a binding the pair publishes on a second subpath. `@stapel/search-react`
       keeps react-router out of its main entry on purpose (§13.4 item 6), so
       `useRouterSearchParams` comes from `<pkg>/router` and the container is
       what joins the two.
    """
    comp = entry["component"]["export"]
    pkg = entry["_package"]
    subpath = entry["component"]["subpath"]
    name = _wrapper_name(entry)
    lines = [
        "/**",
        f" * GENERATED — route binding for `{entry['id']}`.",
        " * See _frontend_templates.render_nav_page_wrapper_tsx for why this",
        " * file exists instead of the component being mounted directly.",
        " */",
        'import type { ReactElement } from "react";',
    ]
    params = mount.get("route_params") or {}
    adapter = mount.get("adapter")
    option_props = mount.get("option_props") or {}
    options = options or {}
    if params:
        lines.append('import { useParams } from "react-router";')
    lines.append(f'import {{ {comp} }} from "{pkg}/{subpath}";')
    if adapter:
        hook, adapter_subpath = adapter
        lines.append(f'import {{ {hook} }} from "{pkg}/{adapter_subpath}";')
    if params:
        lines.append('import { RouteParamProblem } from "../NavPlaceholder.js";')
    lines.append("")
    lines.append(f"export function {name}(): ReactElement {{")
    props: list[str] = []
    if params:
        lines.append("  const params = useParams();")
        for param, kind in params.items():
            raw = f"raw{param[:1].upper()}{param[1:]}"
            lines.append(f"  const {raw} = params.{param};")
            if kind == "number":
                lines.append(f"  const {param} = Number({raw});")
                lines.append(
                    f"  if ({raw} === undefined || !Number.isInteger({param})) {{"
                )
            else:
                lines.append(f"  const {param} = {raw};")
                lines.append(f"  if ({param} === undefined || {param}.length === 0) {{")
            lines.append(f"    return <RouteParamProblem param={json.dumps(param)} />;")
            lines.append("  }")
            props.append(f"{param}={{{param}}}")
    if adapter:
        hook, _ = adapter
        lines.append(f"  const adapter = {hook}();")
        props.append("adapter={adapter}")
    for prop, option in option_props.items():
        props.append(f"{prop}={json.dumps(options[option])}")
    rendered_props = (" " + " ".join(props)) if props else ""
    lines.append(f"  return <{comp}{rendered_props} />;")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def public_mount_plan(plan: dict, options: dict | None = None) -> dict:
    """How every route in *plan* is mounted — the single pass both
    ``render_public_routes_tsx`` and the file writer read, so the element a
    route renders and the files that exist can never disagree.

    *options* carries the values a deployment knows and this generator was
    told (today: ``doc_type``). A mount whose `option_props` are not all
    supplied falls to the placeholder, naming the props — the same treatment as
    a cross-pair slot, for the same reason: an invented value renders a page
    that looks wired and answers wrongly.

    Returns ``{"elements": {id: jsx}, "imports": [line, …],
    "pages": {relpath: content}, "needs_placeholder": bool}``.
    """
    options = options or {}
    routes = [
        *plan["public"],
        *plan["member_absolute"],
        *plan["account_children"],
    ]
    if plan.get("account_entry"):
        routes = [{"entry": plan["account_entry"]}, *routes]

    elements: dict[str, str] = {}
    pages: dict[str, str] = {}
    direct: dict[tuple, set] = {}
    local_imports: set = set()
    wrapper_imports: list[str] = []
    needs_placeholder = False

    for route in routes:
        entry = route["entry"]
        mount = NAV_ENTRY_MOUNTS.get(entry["id"])
        if mount is None:
            needs_placeholder = True
            elements[entry["id"]] = _placeholder_jsx(entry)
            continue
        unmet = tuple(
            prop
            for prop, option in (mount.get("option_props") or {}).items()
            if options.get(option) in (None, "")
        )
        missing = (*tuple(mount.get("container", ())), *unmet)
        if missing:
            needs_placeholder = True
            elements[entry["id"]] = _placeholder_jsx(entry, missing)
            continue
        if "local" in mount:
            name = mount["local"]
            local_imports.add(name)
            elements[entry["id"]] = f"<{name} />"
            continue
        if mount.get("route_params") or mount.get("adapter"):
            name = _wrapper_name(entry)
            pages[f"src/pages/{name}.tsx"] = render_nav_page_wrapper_tsx(
                entry, mount, options
            )
            line = f'import {{ {name} }} from "./pages/{name}.js";'
            if line not in wrapper_imports:
                wrapper_imports.append(line)
            elements[entry["id"]] = f"<{name} />"
            continue
        comp = entry["component"]["export"]
        direct.setdefault((entry["_package"], entry["component"]["subpath"]), set()).add(comp)
        elements[entry["id"]] = f"<{comp} />"

    imports: list[str] = []
    for name in sorted(local_imports):
        imports.append(f'import {{ {name} }} from "./{name}.js";')
    if needs_placeholder:
        imports.append('import { NavPlaceholder } from "./NavPlaceholder.js";')
    for (package, subpath), exports in direct.items():
        imports.append(f'import {{ {", ".join(sorted(exports))} }} from "{package}/{subpath}";')
    imports.extend(wrapper_imports)

    return {
        "elements": elements,
        "imports": imports,
        "pages": pages,
        "needs_placeholder": needs_placeholder,
    }


def render_public_routes_tsx(plan: dict, options: dict | None = None) -> str:
    """``src/routes.tsx`` — the public storefront's route tree (spec §6.1
    item 5).

    One layout route (`<StorefrontShell/>`, which is `<PublicShell/>` with the
    nav its viewer may see) over three groups, and the grouping IS the access
    rule:

      * public routes — siblings, reachable by anyone.
      * member routes with an absolute path (`/new`) — siblings too, but
        inside `<MemberGate/>`: composing a listing is not an `/account`
        screen, and it is still nobody's business but a member's.
      * `/account` and everything under it — the same gate, one subtree.

    The gate is a LAYOUT route with no path of its own, so it wraps without
    adding a segment. `matchMandate` inside it is what keeps "we could not
    ask" out of "you may not" (see MemberGate.tsx).
    """
    mounts = public_mount_plan(plan, options)
    elements = mounts["elements"]

    lines: list[str] = [
        "/**",
        " * GENERATED — react-router v7 route tree for the PUBLIC surface.",
        " * react-router v7 ships v6-future behaviour as its own default, so",
        " * there is no future-flags object to configure here.",
        " *",
        " * Do not hand-edit the tree: it is a projection of the installed",
        " * pairs' nav manifests (src/nav.generated.ts) plus this container's",
        " * own `account.root`. Add or drop a screen by changing the pair set",
        " * and re-generating.",
        " */",
        'import { createBrowserRouter } from "react-router";',
        'import { MemberGate } from "./MemberGate.js";',
        'import { StorefrontShell } from "./StorefrontShell.js";',
        'import { StorefrontHome } from "./StorefrontHome.js";',
    ]
    lines.extend(mounts["imports"])
    lines.append("")
    lines.append("export const router = createBrowserRouter([")
    lines.append("  {")
    lines.append("    element: <StorefrontShell />,")
    lines.append("    children: [")
    lines.append("      { index: true, element: <StorefrontHome /> },")
    for route in plan["public"]:
        element = elements[route["entry"]["id"]]
        lines.append(f'      {{ path: "{route["path"]}", element: {element} }},')
    lines.append("      {")
    lines.append("        element: <MemberGate />,")
    lines.append("        children: [")
    for route in plan["member_absolute"]:
        element = elements[route["entry"]["id"]]
        lines.append(f'          {{ path: "{route["path"]}", element: {element} }},')
    account_entry = plan.get("account_entry")
    if account_entry or plan["account_children"]:
        lines.append("          {")
        account_path = (
            account_entry["route"]["path"] if account_entry else "/account"
        )
        lines.append(f'            path: "{account_path}",')
        lines.append("            children: [")
        if account_entry:
            lines.append(
                f'              {{ index: true, element: {elements[account_entry["id"]]} }},'
            )
        for route in plan["account_children"]:
            element = elements[route["entry"]["id"]]
            lines.append(f'              {{ path: "{route["path"]}", element: {element} }},')
        lines.append("            ],")
        lines.append("          },")
    lines.append("        ],")
    lines.append("      },")
    lines.append("    ],")
    lines.append("  },")
    lines.append("]);")
    lines.append("")
    return "\n".join(lines)


STOREFRONT_SHELL_TSX = '''\
/**
 * GENERATED — the storefront's chrome.
 *
 * `<PublicShell/>` reads no session, exactly like `<AppShell/>`: the mandate
 * is supplied by the container, and the container is also what decides WHICH
 * nav tree the shell was handed. That decision is here, and it is one line
 * with a reason:
 *
 *   a settled member  -> the member tree.
 *   anyone else       -> the public tree.
 *
 * "Anyone else" includes an unsettled mandate (`useMandatePrincipal()`
 * answers `null` while the session bootstraps or when the answer could not be
 * obtained), and showing the PUBLIC tree there is the safe direction: it is
 * the smaller menu, so no door is drawn that would refuse the person who
 * clicks it. The waiting and the outage themselves are rendered by
 * `<MemberGate/>` on the routes that need a verdict — a menu is not the place
 * to explain an outage.
 *
 * The slots (`brand`, `searchSlot`, `categorySlot`, `accountSlot`, `footer`)
 * are deliberately unset: they are the wiring step's closed list (spec §6.2
 * item 3), and every one of them is product knowledge — a logo, what the
 * header search field searches, which categories are "top". Left unset,
 * `PublicShell` still renders its own sign-in CTA, so the entry point is
 * never missing while this file waits to be filled in.
 */
import type { ReactElement } from "react";
import { useMandatePrincipal } from "@stapel/core";
import { PublicShell } from "@stapel/shell-react/default";
import { RESOLVED_NAV_MEMBER, RESOLVED_NAV_PUBLIC } from "./nav.generated.js";

export function StorefrontShell(): ReactElement {
  const principal = useMandatePrincipal();
  const nav = principal === "member" ? RESOLVED_NAV_MEMBER : RESOLVED_NAV_PUBLIC;
  return <PublicShell nav={nav} mode="light" />;
}
'''

STOREFRONT_HOME_TSX = '''\
/**
 * GENERATED — the storefront's `/`, and a named gap rather than a hero.
 *
 * On a marketplace the first page is the goods: a category carousel over one
 * pair and a newest-first result list over another. That makes it a COMPOSITE
 * screen, and a composite route belongs to the container that composes it —
 * no pair may claim `/`, because the second pair would then have nowhere to
 * put its half. So generation emits this page, which says what belongs here,
 * and the wiring step replaces it.
 */
import type { ReactElement } from "react";
import { Alert, Typography } from "antd";
import { useT } from "@stapel/core";
import { STOREFRONT_I18N_KEYS } from "./i18n/keys.js";

export function StorefrontHome(): ReactElement {
  const t = useT();
  return (
    <Alert
      type="info"
      showIcon
      data-testid="storefront-home"
      message={t(STOREFRONT_I18N_KEYS.homeTitle)}
      description={
        <Typography.Paragraph>
          {t(STOREFRONT_I18N_KEYS.homeBody)}
        </Typography.Paragraph>
      }
    />
  );
}
'''

ACCOUNT_HOME_TSX = '''\
/**
 * GENERATED — the index of `/account`.
 *
 * `account.root` is the container's own nav entry (no module owns "the
 * account section"), so its component is the container's too. Like `/`, the
 * landing itself is a composition of the member sections around it, and it is
 * emitted as a page that says so.
 */
import type { ReactElement } from "react";
import { Alert, Typography } from "antd";
import { useT } from "@stapel/core";
import { STOREFRONT_I18N_KEYS } from "./i18n/keys.js";

export function AccountHome(): ReactElement {
  const t = useT();
  return (
    <Alert
      type="info"
      showIcon
      data-testid="account-home"
      message={t(STOREFRONT_I18N_KEYS.accountTitle)}
      description={
        <Typography.Paragraph>
          {t(STOREFRONT_I18N_KEYS.accountBody)}
        </Typography.Paragraph>
      }
    />
  );
}
'''

PUBLIC_MAIN_TSX = '''\
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";
import { ModulesProvider } from "./modules.js";
import { router } from "./routes.js";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ModulesProvider>
      <RouterProvider router={router} />
    </ModulesProvider>
  </StrictMode>
);
'''


def render_public_nav_generated_ts(manifests: list[dict]) -> str:
    """``src/nav.generated.ts`` — the installed manifests, and the two
    resolved trees.

    Both trees come from the AUDIENCE-NAMED resolvers, never from
    `resolveNav(…, {audience})`: `audience` is optional and its default does
    not filter, so a public container that forgot the option would mount every
    member screen and every one of them would answer 403. The default has to
    stay permissive (the monolith scaffold's codegen bakes every route a
    project could mount), so the fix is a call that cannot be made wrong —
    `resolvePublicNav` / `resolveMemberNav`, with the audience in the name.
    """
    manifests_json = json.dumps(manifests, indent=2)
    return f'''\
/**
 * GENERATED — do not hand-edit. Nav-manifest data for this container's
 * installed pairs, plus this container's OWN `account.root` entry.
 *
 * `account.root` is here rather than in `stapel.nav.json` because an override
 * file cannot create an entry: `NavOverridesFile` carries `menuVisible` and
 * `order` per EXISTING id and nothing else. A container-owned
 * `PackageNavManifest` is the mechanism that exists, and it is also what makes
 * every `placement.parentId: "account.root"` entry resolve instead of being
 * dropped as an orphan. `stapel.nav.json` still tunes it, which is the part of
 * the override channel that was ever real.
 */
import type {{ PackageNavManifest }} from "@stapel/core";
import type {{ NavOverridesFile, ResolvedNavEntry }} from "@stapel/shell-react";
import {{ resolveMemberNav, resolvePublicNav }} from "@stapel/shell-react";
import stapelNavOverrides from "../stapel.nav.json";

export const INSTALLED_NAV_MANIFESTS: readonly PackageNavManifest[] = {manifests_json} as const;

const overrides = stapelNavOverrides as NavOverridesFile;

/** What an anonymous visitor may be shown. */
export const RESOLVED_NAV_PUBLIC: readonly ResolvedNavEntry[] = resolvePublicNav(
  INSTALLED_NAV_MANIFESTS,
  overrides
);

/** What a settled member may be shown — a superset of the public tree. */
export const RESOLVED_NAV_MEMBER: readonly ResolvedNavEntry[] = resolveMemberNav(
  INSTALLED_NAV_MANIFESTS,
  overrides
);
'''


def render_public_nav_overrides_json() -> str:
    """``stapel.nav.json`` — the project's override channel, pre-seeded with
    the container's own `account.root` so the one entry a reader is most
    likely to want to retune is already visible in the file."""
    return json.dumps(
        {
            "overrides": {
                ACCOUNT_ROOT_ENTRY["id"]: {
                    "menuVisible": True,
                    "order": ACCOUNT_ROOT_ENTRY["order"],
                }
            }
        },
        indent=2,
    ) + "\n"


# Locales the pairs actually publish a catalogue for (`<pkg>/i18n/<locale>`).
# `en` is every pair's inlined floor and has no subpath of its own.
PAIR_LOCALES = ("ru", "es")


def render_public_modules_tsx(
    entries: list[dict], *, locale: str = "en", realtime: bool = False
) -> str:
    """``src/modules.tsx`` — one runtime + one provider + one catalogue per
    installed pair, a single `<StapelProvider>` carrying every client, and the
    mandate seam mounted inside it (spec §6.1 item 2).

    Three things this does that the monolith's registry does not:

     * it mounts `<MandateProvider>` with this container's own source, so the
       axis is available to the shell and the gate without `workspaces-react`.
     * it registers each pair's real `<pkg>/i18n/<locale>` catalogue when the
       app runs in a locale the pairs publish, and the container's own English
       floor LAST (spec §6.2 item 9).
     * it knows an L0 pair when it sees one: `@stapel/attributes-react` has no
       client, no queries and no backend HTTP surface at all, so it contributes
       a catalogue and nothing else. That is read off the absence of
       `create_runtime` in the registry, not off a flag someone has to set.
    """
    runtime_entries = [e for e in entries if e.get("create_runtime")]
    l0_entries = [e for e in entries if not e.get("create_runtime")]
    if not runtime_entries:
        raise ValueError(
            "a public container needs at least one pair with a runtime — an "
            "L0-only selection has no client for <StapelProvider> and no "
            "screens to mount."
        )
    # auth first when present: `<StapelProvider client={…}>` wants the client
    # whose seams carry token refresh and the verification-403 branch, and
    # that is the auth runtime's. Otherwise registry order decides, as in the
    # monolith.
    primary = next(
        (e for e in runtime_entries if e["key"] == "auth"), runtime_entries[0]
    )
    others = [e for e in runtime_entries if e is not primary]
    locale_suffix = locale[:1].upper() + locale[1:]
    localized = locale in PAIR_LOCALES

    lines: list[str] = [
        "/**",
        " * GENERATED — do not hand-edit the provider nesting below. This file",
        " * is the data-driven registry of this container's installed",
        " * @stapel/<module>-react pairs. Add or drop a pair by re-running",
        " * `stapel-frontend-repo-init --pairs …`, never by editing this",
        " * file's shape.",
        " */",
        'import type { ReactElement, ReactNode } from "react";',
        'import { createI18n, createStapelQueryClient, MandateProvider, StapelProvider } from "@stapel/core";',
        'import { registerShellI18n } from "@stapel/shell-react";',
    ]
    for e in entries:
        names = [n for n in (e.get("create_runtime"), e.get("provider"), e["register_i18n"]) if n]
        lines.append(f'import {{ {", ".join(names)} }} from "{e["package"]}";')
        if localized:
            lines.append(
                f'import {{ {e["register_i18n"]}{locale_suffix} }} '
                f'from "{e["package"]}/i18n/{locale}";'
            )
    lines.append('import { registerStorefrontI18n } from "./i18n/keys.js";')
    lines.append('import { useStorefrontMandateSource } from "./mandateSource.js";')
    lines.append("")
    lines.append('const query = createStapelQueryClient({ cacheVersion: "0.0.0" });')
    lines.append(f"const i18n = createI18n({{ locale: {json.dumps(locale)} }});")
    lines.append("")
    for e in runtime_entries:
        key = e["key"]
        if key == "chat" and not realtime:
            lines.append(f"const {key}Runtime = {e['create_runtime']}({{")
            lines.append(f'  baseUrl: "/{key}/api/v1/",')
            lines.append("  // Sockets OFF, stated rather than discovered: a WSGI")
            lines.append("  // deployment mounts no websocket route, and the pair's own")
            lines.append("  // default would otherwise derive a socket base and let")
            lines.append("  // every tab fail a handshake several times before falling")
            lines.append("  // back to polling. Re-generate with --realtime on an ASGI")
            lines.append("  // fleet and this line disappears.")
            lines.append("  realtime: { socketUrl: null },")
            lines.append("});")
        else:
            lines.append(
                f'const {key}Runtime = {e["create_runtime"]}({{ baseUrl: "/{key}/api/v1/" }});'
            )
    lines.append("")
    lines.append("// The `en` floor of every catalogue first, then this locale's real")
    lines.append("// catalogue on top, then the container's own copy last.")
    for e in entries:
        lines.append(f'{e["register_i18n"]}(i18n);')
    lines.append("registerShellI18n(i18n);")
    if localized:
        for e in entries:
            lines.append(f'{e["register_i18n"]}{locale_suffix}(i18n);')
        lines.append(f"// @stapel/shell-react publishes no `{locale}` catalogue yet, so its")
        lines.append("// English floor is registered under this locale too: a shell string")
        lines.append("// in the wrong language still reads as a sentence, while a missing")
        lines.append("// key reads as `shell.public.sign_in` on a button. UPSTREAM ASK.")
        lines.append(f"registerShellI18n(i18n, {json.dumps(locale)});")
    lines.append(f"registerStorefrontI18n(i18n, {json.dumps(locale)});")
    lines.append("")
    lines.append(
        f"export const INSTALLED_REACT_MODULES = {_ts_string_array([e['key'] for e in entries])} as const;"
    )
    lines.append("")
    if l0_entries:
        keys = ", ".join(e["key"] for e in l0_entries)
        lines.append("// L0 pairs (no client, no provider, no backend of their own):")
        lines.append(f"//   {keys}")
        lines.append("// They contribute a catalogue above and are used directly by the")
        lines.append("// pairs that render their editors — nothing to mount here.")
        lines.append("")
    lines.append("/**")
    lines.append(" * The mandate seam. Inside `<StapelProvider>` because the derivation")
    lines.append(" * reads the active session manager the auth runtime registers, and")
    lines.append(" * above everything that renders, because the shell's menu and the")
    lines.append(" * member gate must read ONE answer.")
    lines.append(" */")
    lines.append("function MandateGateway({ children }: { children: ReactNode }): ReactElement {")
    lines.append("  const source = useStorefrontMandateSource();")
    lines.append("  return <MandateProvider source={source}>{children}</MandateProvider>;")
    lines.append("}")
    lines.append("")
    lines.append("export function ModulesProvider({ children }: { children: ReactNode }): ReactElement {")
    lines.append("  return (")
    lines.append("    <StapelProvider")
    lines.append(f'      client={{{primary["key"]}Runtime.client}}')
    if others:
        lines.append("      clients={{")
        for e in others:
            lines.append(f'        {e["key"]}: {e["key"]}Runtime.client,')
        lines.append("      }}")
    lines.append("      queryRuntime={query}")
    lines.append("      i18n={i18n}")
    lines.append("    >")
    indent = "      "
    for e in runtime_entries:
        lines.append(f'{indent}<{e["provider"]} runtime={{{e["key"]}Runtime}}>')
        indent += "  "
    lines.append(f"{indent}<MandateGateway>{{children}}</MandateGateway>")
    for e in reversed(runtime_entries):
        indent = indent[:-2]
        lines.append(f'{indent}</{e["provider"]}>')
    lines.append("    </StapelProvider>")
    lines.append("  );")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


# The container's own toolchain — the SAME pins the stapel-react monorepo
# builds its pairs with (its root package.json), not the newest majors on the
# registry. A generated app that type-checks under a different major than the
# packages it installs is a support question waiting to happen, and the day
# the fleet moves, both move together.
PUBLIC_DEV_DEPS = {
    "@eslint/js": "^9.30.0",
    "@stapel/eslint-plugin": "^0.10.0",
    "@stapel/tokens": "^0.5.0",
    "@types/react": "^19.1.0",
    "@types/react-dom": "^19.1.0",
    "@vitejs/plugin-react": "^4.3.0",
    "eslint": "^9.30.0",
    "typescript": "^5.8.3",
    "typescript-eslint": "^8.35.0",
    "vite": "^6.0.0",
}


def render_public_package_json(
    entries: list[dict],
    *,
    name: str,
    core_version: str,
    query_version: str,
    antd_version: str,
    tokens_antd_version: str,
    shell_version: str,
    router_version: str,
) -> str:
    """``package.json`` for the container: every installed pair at its pinned
    minor, plus the support set a public surface always needs (core, query,
    antd + the token bridge, shell-react for `PublicShell`, react-router)."""
    deps = {
        "@stapel/core": f"^{core_version}",
        "@stapel/shell-react": f"^{shell_version}",
        "@tanstack/react-query": f"^{query_version}",
        "@stapel/tokens-antd": f"^{tokens_antd_version}",
        "antd": f"^{antd_version}",
        "react": "^19.1.0",
        "react-dom": "^19.1.0",
        "react-router": f"^{router_version}",
    }
    for entry in entries:
        deps[entry["package"]] = f'^{entry["version"]}'
    pkg = {
        "name": name,
        "private": True,
        "version": "0.0.0",
        "type": "module",
        "scripts": {
            "dev": "vite",
            "build": "tsc --noEmit && vite build",
            "preview": "vite preview",
            "lint": "eslint .",
            "gen:tokens": (
                "stapel-tokens --theme ./stapel.theme.json "
                "--out ./src/stapel-tokens --targets core"
            ),
            "gen:tokens:check": (
                "stapel-tokens --theme ./stapel.theme.json "
                "--out ./src/stapel-tokens --targets core --check"
            ),
        },
        "dependencies": dict(sorted(deps.items())),
        "devDependencies": dict(sorted(PUBLIC_DEV_DEPS.items())),
    }
    return json.dumps(pkg, indent=2) + "\n"


def render_public_vite_config_ts(prefixes: list[str]) -> str:
    """``vite.config.ts`` — the dev proxy table for the fleet's backend
    prefixes.

    Every rule is a NAMED SUB-SURFACE (`/<mod>/api/`, `/<mod>/swagger/`,
    `/<mod>/schema.json`, `/<mod>/admin/`), never a bare module root. That is
    not tidiness: `location /listings` is a PREFIX match, so a bare rule sends
    `/l/…`-style SPA routes — and `/listings/12345` most of all — to the
    backend, and a listing page answers JSON. The bare root belongs to the
    router; the same list is written to `reserved-paths.json`, where
    `stapel/no-reserved-backend-route` checks the routes this app defines
    against it statically.
    """
    rules = []
    for prefix in prefixes:
        key = prefix if prefix.endswith(".json") else f"{prefix}/"
        rules.append(f'        "{key}": {{ target: backendTarget, changeOrigin: true }},')
    proxy = "\n".join(rules)
    return f'''\
import {{ defineConfig, loadEnv }} from "vite";
import react from "@vitejs/plugin-react";

/**
 * GENERATED. In production this app is a dist-carrier image and the fleet's
 * OWN nginx is the single boundary that owns reserved paths, TLS, the proxy
 * table and the cache canon — this file never runs there. It is the DEV path:
 * `npm run dev` in front of a live backend, with the target an env var
 * (VITE_BACKEND_TARGET) and never a hardcoded host.
 *
 * The table below reserves each module's named sub-surfaces and NEVER a bare
 * module root — see render_public_vite_config_ts for why a bare root would
 * make a listing page answer JSON.
 */
export default defineConfig(({{ mode }}) => {{
  const env = loadEnv(mode, process.cwd(), "");
  const backendTarget = env.VITE_BACKEND_TARGET || "http://localhost:8000";

  return {{
    plugins: [react()],
    server: {{
      host: true,
      port: 5173,
      strictPort: true,
      proxy: {{
{proxy}
      }},
    }},
  }};
}});
'''


def render_public_index_html(title: str) -> str:
    return f'''\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{title}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
'''


def render_public_eslint_config_js() -> str:
    """``eslint.config.js`` — the guardrails, with both data-driven rules
    actually POINTED AT DATA.

    `stapel/no-reserved-backend-route` and `stapel/i18n-key-exists` are both
    no-ops without a catalogue, and a gate that cannot fail reads exactly like
    one that passes. So the config hands them this repo's own
    `reserved-paths.json` and this container's own key list — the keys
    generated code actually calls `t()` with. A pair's keys are deliberately
    NOT listed: an unknown key under a namespace no catalogue owns is treated
    as app-local by the rule, which is the false-positive policy that keeps it
    switched on.
    """
    keys = ",\n".join(f"          {json.dumps(k)}" for k in STOREFRONT_I18N_EN)
    return f'''\
// GENERATED — the frontend guardrails (@stapel/eslint-plugin) plus the
// TypeScript parser they need. Regenerating this repo rewrites this file;
// project-specific rules belong in a sibling config entry you add below.
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import stapel from "@stapel/eslint-plugin";

export default tseslint.config(
  // JSON is ignored explicitly, and the reason is not obvious: the guardrail
  // preset's raw-token carve-out lists `**/stapel.theme.*` and
  // `**/*.theme.{{…,json}}`, which PULLS those JSON files into the lint set —
  // and then the TypeScript rules try to read `{{ … }}` as a block with a
  // dangling expression. Ignoring them here is the local fix; the preset
  // narrowing its carve-out to source files is the upstream one.
  {{ ignores: ["dist/**", "node_modules/**", "src/stapel-tokens/**", "**/*.json"] }},
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...stapel.configs.recommended,
  {{
    settings: {{
      stapel: {{
        // The SPA must not claim a path the backend reserves. Without this
        // the rule silently checks nothing.
        reservedPathsFile: "./reserved-paths.json",
        // The container's own keys. An unknown key under a namespace no
        // catalogue owns is assumed app-local by the rule, so listing these
        // gates generated code without false-positiving on the pairs'.
        i18nKeys: [
{keys}
        ],
      }},
    }},
  }}
);
'''
