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

Colour tokens (§68 color-token-matrix, Ф5): ``THEME_JSON`` is this project's
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
# active (Ф1) — ``src/nav.generated.ts`` does ``import stapelNavOverrides
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
    """``frontend/src/main.tsx`` — the collapse rule (owner directive, Ф1):
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
        lines.append(
            f'import {{ {e["create_runtime"]}, {e["provider"]}, {e["register_i18n"]} }} '
            f'from "{e["package"]}";'
        )
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
        lines.append(
            f'const {key}Runtime = {e["create_runtime"]}({{ baseUrl: "/{key}/api/v1/" }});'
        )
        lines.append(f"{e['register_i18n']}(i18n);")
    lines.append("")
    lines.append(f"export const INSTALLED_REACT_MODULES = {_ts_string_array([e['key'] for e in entries])} as const;")
    lines.append("")

    primary = entries[0]
    rest = entries[1:]
    lines.append("/**")
    lines.append(" * One shared `<StapelProvider>` (core config + query + i18n) plus one")
    lines.append(" * `<XProvider>` per selected pair, nested — the \"Wire the app once\"")
    lines.append(" * composition every pair's own README documents, generated once per")
    lines.append(" * module selection instead of hand-edited per pair.")
    lines.append(" */")
    lines.append("export function ModulesProvider({ children }: { children: ReactNode }): ReactElement {")
    lines.append("  return (")
    if rest or has_cdn:
        lines.append('    <StapelProvider')
        lines.append(f'      client={{{primary["key"]}Runtime.client}}')
        lines.append("      clients={{")
        for e in rest:
            lines.append(f'        {e["key"]}: {e["key"]}Runtime.client,')
        if has_cdn:
            # Stopgap (no @stapel/cdn-react pair exists yet — a documented
            # follow-up, cdn-scaffold-autowire.md): reuse the primary pair's
            # client verbatim, same as the hand-applied meettoday fix's
            # `clients: { cdn: stapelClient }`.
            lines.append(f'        cdn: {primary["key"]}Runtime.client,')
        lines.append("      }}")
        lines.append("      queryRuntime={query}")
        lines.append("      i18n={i18n}")
        lines.append("    >")
    else:
        lines.append(f'    <StapelProvider client={{{primary["key"]}Runtime.client}} queryRuntime={{query}} i18n={{i18n}}>')

    indent = "      "
    for e in entries:
        lines.append(f'{indent}<{e["provider"]} runtime={{{e["key"]}Runtime}}>')
        indent += "  "
    lines.append(f"{indent}{{children}}")
    for e in reversed(entries):
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
# Scripted-fullstack navigation (Ф1) — nav.generated.ts / routes.tsx /
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
    directive, Ф1):

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
        " * navigation, Ф1 owner directive: one scripted command produces a",
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
 * navigation, Ф1):
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

# Multi-stage: `build` produces the static bundle; `export` is the one-shot
# stage docker-compose.yml (prod) runs to copy it into the frontend-dist
# volume nginx serves from. This image is never a long-lived service.
DOCKERFILE = """\
FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

# Prod canon (§57): `docker compose run --rm frontend-build` (or the
# one-shot service in docker-compose.yml) copies dist/ into whatever host
# path is mounted at /output — the frontend-dist volume the main nginx
# mounts read-only at /usr/share/nginx/html.
FROM build AS export
CMD ["sh", "-c", "rm -rf /output/* && cp -r dist/. /output/"]
"""

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
