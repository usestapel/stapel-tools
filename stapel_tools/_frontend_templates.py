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
