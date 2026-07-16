"""``frontend/`` scaffold templates for ``stapel-create-project --type monolith``
(BACKLOG §57 — dev/prod compose + nginx canon, owner directive from the
live-run postmortem).

A minimal, real Vite + React + TypeScript app — not a placeholder. It is
wired into:

  - ``docker-compose.dev.yml`` — a plain ``node:22-alpine`` container runs
    ``npm install && npm run dev`` (hot-reload, logs visible); dev-nginx
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
                         the real dev path (nginx-dev) reads the same
                         default from docker-compose.dev.yml's env, not
                         from this file.
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
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.1.0",
    "react-dom": "^19.1.0"
  },
  "devDependencies": {
    "@types/react": "^19.1.0",
    "@types/react-dom": "^19.1.0",
    "@vitejs/plugin-react": "^4.3.0",
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
 * docker-compose.dev.yml's dev-nginx, which already splits traffic between
 * this server and the Django backend (reserved namespace: /{{SLUG}}/,
 * /staticfiles/, /media/ — see the project's AGENTS.md §3). The proxy
 * config below is a FALLBACK for running `npm run dev` standalone, without
 * dev-nginx in front — e.g. hitting a dockerized backend from a natively
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
        "/{{SLUG}}/": { target: backendTarget, changeOrigin: true },
        "/staticfiles/": { target: backendTarget, changeOrigin: true },
        "/media/": { target: backendTarget, changeOrigin: true },
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
 * reserved /{{SLUG}}/ namespace, routed by nginx/dev-nginx to the backend —
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

`docker compose -f docker-compose.dev.yml up` starts this alongside the
backend and dev-nginx — dev-nginx routes the reserved backend namespace
(`/{{SLUG}}/`, `/staticfiles/`, `/media/`) to Django and everything else to
this Vite dev server (logs visible via `docker compose logs -f frontend`).

Running `npm run dev` standalone (no dev-nginx) also works — see
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

The backend owns `/{{SLUG}}/*`, `/staticfiles/*`, `/media/*` (see the project
root `AGENTS.md` §3). This app's own client-side router must not define a
route under any of those prefixes.
"""
