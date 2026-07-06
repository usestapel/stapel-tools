# Changelog

## [Unreleased]

### Added — codegen emits the Gherkin feature bundles (flow-system.md §3)
`stapel_tools.codegen.generate()` now also runs stapel-core's
`generate_flow_features` into `<out>/features/`: one bundle per project
language (localized `.feature` files + the playwright-bdd step library over
the codegen typed client), byte-stable like the three JSON artifacts — the
same drift-gate discipline. New `emit_features()`; the CLI summary reports
the feature-file count. Tests: bundle layout per language, byte-stability,
`generate()` summary.

## [0.8.3] — 2026-07-06

### Changed — generators write the service-navigation registry as env-JSON (admin-suite AS-4)
The service list feeding the admin/Swagger "Services" menu moved out of
framework code (`stapel_core.core.config.STAPEL_SERVICES`, now removed) into a
deploy-config env-JSON. The generators own it:
- **`stapel-create-project`** seeds `STAPEL_SERVICES` in the project `.env` /
  `.env.example`: a monolith gets its single service
  (`[{"name": "<Title>", "prefix": "<slug>"}]`, "All Services" collapses); a
  microservices project gets an empty `STAPEL_SERVICES=[]` for `new-service`
  to fill.
- **`stapel-new-service`** now appends `{"name", "prefix"}` to that env-JSON
  (idempotent by prefix) — the same discipline as `STAPEL_BUS_ROUTES` —
  instead of patching a project-owned `config.py`'s `STAPEL_SERVICES` list
  (the old, largely-dormant behavior, removed).
- Tests: `TestStapelServicesEnv` (monolith seed, microservices empty-then-
  append, idempotent re-registration).

## [0.8.2] — 2026-07-06

### Fixed — three scaffold defects (generated output was dishonest / uncollectable)
- **`stapel-new-service` / `stapel-new-module` app-label collision.** A service
  or module named after a hosted Stapel app (`auth`, `profiles`, `notifications`,
  …) took the bare app label and clashed with `django.contrib.auth`
  (label `auth`) or the hosted `stapel_<x>` module (which sets `label="<x>"`),
  so `django.setup()` raised `ImproperlyConfigured: Application labels aren't
  unique` and **no test could even collect**. The scaffolded `AppConfig` now
  carries an explicit, collision-proof `label = "<module>_local"` (keeps the
  Python `name`; the `_local` suffix marks it the service's OWN app vs. the
  hosted module, and mirrors `core.settings.local`). Safe for existing users:
  the templates never shipped an explicit label and a fresh scaffold has no
  models/migrations, so the label has no `db_table` history to migrate.
- **`stapel-new-react-lib` dishonest `data-analytics="flow"` marker.** The demo
  `DemoButton` hardcoded `data-analytics="flow"` even for a pair with zero flow
  machines — a lie (the button steps no auto-instrumented flow). The scaffold now
  reads the unified `flows.json` (the same source `gen:flows` reads) and picks
  the marker HONESTLY from the module's flow count: `data-analytics="flow"` when
  it owns flows, else `data-analytics="none" data-analytics-reason="no-flow-machines"`.
- **`stapel-new-react-lib` `@stapel/core` peer floor too low.** The floor was the
  monorepo core's current minor, which could sit below `0.3.0` — the minor that
  first re-exported the `createFlowMachine`/`useFlow` primitive every pair
  re-exports. The floor is now `max(0.3.0, current-minor)`, so a pair can never
  advertise a core range (`>=0.2.0`) that lacks the symbol it imports.

## [0.8.1] — 2026-07-06

### Fixed
- `stapel-new-service` / `stapel-create-project` (monolith & microservices)
  generated `LOGIN_REDIRECT_URL = "/{{SLUG}}/admin/"` — a root-relative path
  that 404s once the service is mounted under a prefix. Now emits the URL
  *name* `"admin:index"` (house convention, stapel-core MODULE.md → "URL
  mounting"), same as the example-monolith etalon (`ca64fa7`).
- The scaffolded `AUTH_SERVICE_PREFIX` setting didn't match the name
  `stapel_core.django.mounts` / `AdminLoginRedirectMiddleware` actually read
  (`STAPEL_AUTH_SERVICE_PREFIX`) — every generated service silently had two
  disconnected "is there a dedicated auth service" toggles. Renamed to the
  canonical `STAPEL_AUTH_SERVICE_PREFIX` in both `core/urls.py` and
  `core/settings/base.py` templates. Added `TestMountConventions` regression
  coverage in `tests/test_create_project.py`.


## [0.8.0] — 2026-07-06

### Changed — `stapel-new-react-lib` re-etalon (auth-react after `ebc8f6c`/`4524a53`/`2b1449f`/`8f6b999`)
The React-pair scaffold predated the G1–G8 guardrails contract; this brings it
back to the confirmed etalon. Eight deltas closed:

1. **Typed-event registry** — the pair is wired into `gen:events` /
   `gen:events:check` (root), and `src/analytics/generated/events.json` is a
   generated, drift-gated surface (documented in README/MODULE.md).
2. **Demo layer** — `demo/_harness.tsx` (mock-`fetch`, token chrome via
   `cssVar()`, `demo.*` i18n keys, `data-analytics="flow"`, `run` prop) plus a
   starter `<Camel>.demo.tsx` that covers the starter headless export
   (`<Camel>Provider`), so the `gen:demos` completeness gate passes on a fresh
   scaffold; `tsconfig.demo.json` compiles demos as first-class code.
3. **`@stapel/showcase`** (and `@stapel/tokens`) added as **devDependencies**
   only — never runtime/peer.
4. **`manifest.backend.contract`** — `gen:manifest` wired with
   `MANIFEST_BACKEND_PYPROJECT` so the manifest states the backend semver range
   it was generated against (a backend minor bump reddens the gate).
5. **Etalon test family** — `demos.test.tsx` (glob smoke-render),
   `prodBundlePurity.test.ts` (real `npm pack --dry-run` ground truth),
   `errorsBundle.test.ts` (en-fallback coverage), `flowsContract.test.ts`
   (registry integrity) — replacing the single `pair.test.ts` (a slim residual
   retains query-key + drift-gated manifest self-description).
6. **Peer policy** — `@stapel/core` peer is a pinned floor
   `>=<current-core-minor>.0 <1.0.0` (read from the monorepo core package.json at
   scaffold time), not `workspace:^` — stops changesets force-majoring the pair
   on an out-of-range core minor. The local link stays `workspace:^` (devDep).
7. **Root `gen`/`gen:check` enumeration** — matching the etalon (pairs own NO
   `gen:*` scripts; the drivers live at the root and are listed per package), the
   scaffold now **idempotently patches** the monorepo root package.json,
   appending one env-parametrized invocation per driver
   (flows/errors/events/demos/manifest) to each `gen:*` and `gen:*:check`. Falls
   back to printing the exact edits when the root shape is unexpected.
8. **CSS guardrail** — README documents `lint:css`/stylelint alongside the
   ESLint plugin.

Fork-free preserved: the scaffold WIRES the etalon's env-parametrized
`scripts/gen-*.mjs` drivers (via `FLOW_MODULE`/`ERRORS_*`/`EVENTS_PKG_DIR`/
`DEMOS_PKG_DIR`/`MANIFEST_*`), never copies driver logic. Also fixed a latent
pre-contract bug: the old per-pair `gen:errors` used a nonexistent env knob
(`AUTH_ERRORS_SOURCES`) pointed at `errors.py`; the driver reads
`AUTH_ERRORS_JSON` (a backend `docs/errors.json`).

Smoke-validated end-to-end on a throwaway `notifications-react` in stapel-react
(install + all 5 gens + build + lint + lint:css + test 14/14 + size 720 B; the
completeness gate fails closed when the starter demo is removed).

## [0.7.0] — 2026-07-06

### Added — i18n doc-link lint rule + seed export (i18n-shipping wave 0)
- **`stapel-lint` R100** (WARNING) — when a repo carries i18n artifacts, its
  README must link the docs in *each* language (i18n-shipping.md §4): if
  `docs/flows/` exists, a link per flow-doc language (en + ru at minimum); if
  `docs/errors.json` or any `docs/errors.<lang>.md` exists, a link per error
  language. Emitted at warning level — the convention is rolling out (W→E after
  the sweep), so `stapel-lint` now exits non-zero only on *error*-level
  violations; warnings are printed but non-blocking. `Violation` gained a
  `level` field (`"error"` default). Repo-level checks (README ↔ artifacts) run
  once per directory root, alongside the existing per-file AST rules.
- **`stapel-i18n-seed`** — one-shot export of a `translate_catalogs` seed from
  the curated `stapel-translate` builtin fixtures
  (`fixtures/builtin/<lang>.json`): `--fixtures DIR --domain {errors,notifications}
  --lang X [--out FILE]` projects the flat corpus, filtered to the domain's key
  prefix, into a byte-stable seed file (sorted keys, matches
  `stapel_core.i18n.dump_catalog`). This is how the first ru of a module's
  errors is *copied* from the paid-for corpus rather than re-translated
  (i18n-shipping.md §5, requirement "clients don't spend tokens").

## [0.6.1] — 2026-07-06

### Fixed
- `stapel-codegen`'s `generate()` now also emits `errors.json` alongside
  `schema.json`/`flows.json`, wired onto stapel-core's `generate_error_keys`
  management command (the mechanism landed in stapel-core `08b6c40` but was
  never plumbed into the orchestrator). Same byte-stable re-normalisation and
  drift-gate invariant as the other two artifacts; format matches
  stapel-auth's `docs/errors.json` (`code`/`status`/`params`/`remediation`/`en`).

## [0.6.0] — 2026-07-06

### Added — `stapel-analytics-report` (frontend-guardrails §3.3, task G5)
- New CLI `stapel-analytics-report <workspace-dir>` (+ `--package DIR`,
  repeatable, for a single package/app). Generates the typed-analytics summary
  report across a pnpm workspace of `@stapel/*-react` pairs and/or a customer
  app, from STATIC generated artifacts only (§3.3): `events.json` /
  `manifest.events` (defineEvent catalog + auto-instrumented flow funnels),
  `flows.json` (canonical backend flows, prose + endpoints), `manifest.machines`,
  and a syntactic scan of TS/TSX for call sites (`tracked()`/`trackedSubmit()`/
  `track()` emit points, `data-analytics="flow"/"none"` markers with reasons,
  `eslint-disable … -- description` escape hatches).
- Two always-separate slices — **app** (customer code) and **library**
  (`@stapel/*` pairs) — reported summarily and split, classified by package name.
- Outputs: machine-readable `report.json` (for the Studio project passport,
  user decision Q13) plus presentable human-readable `report.md` and self-
  contained (CSP-safe, theme-aware) `report.html`. `--out DIR` writes all three;
  otherwise `--format {json,md,html,all}` prints to stdout.
- Per event: description, typed props (types + options + descriptions), emit
  sites (`file:line` + enclosing component), and the linked backend flow when
  declared. Flow funnels list their documented steps. The canonical flow report
  joins backend `flows.json` (prose title/description, actors, endpoints) with
  frontend coverage (covering pairs, funnel event, name-matched machine, linked
  app events) and renders a `[gated: <ENV>]` badge from the `gated_by` field
  (placeholder for task G6 — absent field means always-on). Coverage summary
  counts clickable outcomes by static marker (tracked / flow / untracked /
  disabled).
- Cross-file (and `import { X as Y }` alias) call-site → event resolution reuses
  the events.json TS-AST catalog (produced by `scripts/events-lib.mjs`) as the
  authoritative event set — not the intentionally conservative in-file lint
  resolver. Missing `events.json` degrades to source-derived bindings and the
  `manifest.events` fallback; a package with no catalog at all is flagged, never
  crashes. `--capabilities` is reserved for the §3.4 env-aware mode (ignored).
- Pure Python, zero new dependencies (no Node runtime): the heavy TS-AST work is
  already done by the workspace's drift-gated `gen:events` and consumed here.

## [0.5.0]

### Added
- `stapel-new-react-lib <module>` — scaffold a headless `@stapel/<module>-react`
  pair into a stapel-react monorepo, from the auth-react etalon (frontend-standard
  §9, frontend-core-architecture §4 checklist). Emits the full layer stack
  (`api → model → flows → headless → i18n`), namespaced query keys, the
  `create<Module>Runtime`/`<Module>Provider` wiring, the module-scoped
  `toFlowError`/i18n bundle, an errors map with `explain<Module>Error`, a vitest
  smoke suite, and package hygiene (ESM, `sideEffects:false`, `isolatedDeclarations`,
  src-in-tarball, size-limit, exports for `manifest`/`llms.txt`). The
  `createFlowMachine` primitive is IMPORTED from `@stapel/core`, never copied.
  Fork-free: the generated `package.json` wires the etalon's env-parametrized
  monorepo drivers (`scripts/gen-{flows,errors,manifest}.mjs`) via env knobs
  (`FLOW_MODULE`, `ERRORS_*`, `MANIFEST_*`) rather than duplicating codegen — a
  pair owns three per-package drift gates (`gen:{flows,errors,manifest}:check`);
  `gen:api` stays core-owned. Usage: `stapel-new-react-lib notifications
  [--backend stapel-notifications] [--path-prefix /notifications/api/]
  [--react-dir <stapel-react>]`.

## [0.5.1] — 2026-07-06

### Added — settings hardening + generated secrets (SEC-4/SEC-6)
- **prod settings tier (monolith/microservices `core/settings/prod.py`):**
  `SECURE_SSL_REDIRECT=True`, a conservative `SECURE_HSTS_SECONDS=86400`
  (no `include_subdomains`, no `preload` — both one-way doors, left for the
  deploying team to decide; ramp to `31536000` once HTTPS is verified
  stable), `SECURE_CONTENT_TYPE_NOSNIFF=True`, and `JWT_COOKIE_SECURE=True`
  alongside the existing `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE`
  (security-programme.md gaps B1/B3). Also ships a report-only
  Content-Security-Policy (Django's native CSP middleware, Django>=6; a
  `default-src 'self'`-based policy) — report-only because a strict enforced
  policy can break django-admin/Vite inline scripts without per-project
  tuning (gap B7, §8.4 open question); the CSP block is skipped gracefully
  (`try`/`except ImportError`) on Django<6.
- **`stapel_core.django.prodguard` (new in stapel-core 0.8.1-unreleased)**
  wired into prod settings: `guard_secret` rejects an empty, `change_me*`-
  or `django-insecure-`-prefixed, or under-50-character `SECRET_KEY`/
  `JWT_SECRET_KEY`; `guard_db_password` rejects the shipped
  `POSTGRES_PASSWORD` placeholder/dev-default (`change_me`/`stapel`). The
  previous inline guard only caught an empty or `django-insecure-`-prefixed
  `SECRET_KEY` — the actual `.env.example` placeholder
  (`change_me_to_a_long_random_string`) sailed straight through (gap B2/B6).
- **Minimal preset now has a prod profile** (previously none at all, gap
  B8): `core/settings.py` gains a `DJANGO_ENV` switch (default `local`,
  unaffected DX — `DEBUG`/`ALLOWED_HOSTS` behave exactly as before) and a
  `DJANGO_ENV=prod` branch applying the same `SECURE_*`/HSTS/CSP-report-only
  hardening and `guard_secret` check as the monolith/microservices tiers.
  `.env.example`/README now carry a "NOT FOR PRODUCTION by default" banner.
  The hardcoded insecure `SECRET_KEY` fallback is gone — it only applies
  outside `DJANGO_ENV=prod`, same shape as the monolith/microservices dev
  fallback.
- **`stapel-create-project` now generates `.env` (with a fresh random
  `SECRET_KEY`) for the minimal preset too** (SEC-6) — previously only
  monolith/microservices got generated secrets; minimal fell through to the
  hardcoded dev-only fallback with no `.env` at all. `.env.example` keeps
  the placeholder (safe to commit); `.env` is gitignored, as before.
- Fixed the post-generation guidance for monolith/microservices: it used to
  print `cp .env.example .env  # fill in secrets`, which — followed
  literally — would overwrite the already-generated random secrets with the
  committed placeholders right back. Now states `.env` was already created
  with generated secrets.

### Changed
- `stapel-new-library` artifact hygiene (top-tier packaging): the generated
  `tests` package is no longer listed in `[tool.setuptools] packages`, so test
  files and `conftest.py` no longer ship inside the built wheel/sdist (the
  flat-layout editable install still resolves `<pkg>.tests.urls` for the test
  `ROOT_URLCONF`, so the scaffold suite stays green). Generated `pyproject.toml`
  now carries a full `[project.urls]` block, completed trove classifiers
  (`License :: OSI Approved :: MIT License`, Python 3.13, `Typing :: Typed`,
  `Intended Audience`, `Operating System`, `Development Status`,
  `Python :: 3 :: Only`) matching the CI matrix, and a `[tool.ruff]` section
  single-sourcing the lint config the git hooks/CI pass on the CLI. Generated
  `.gitignore` now also covers `.ruff_cache/`, `.mypy_cache/`, `coverage.xml`,
  `junit.xml`, `.DS_Store` and `*.err`.
- Dropped an unused `pytest` import in `tests/test_codegen.py` (ruff F401).

### Fixed
- `stapel-create-project --modules <mod>` on **minimal** projects now wires each
  chosen module into `INSTALLED_APPS` and mounts its urls under `/<mod>/api/`,
  not just into `requirements.txt` (G10). A module installed but absent from
  `INSTALLED_APPS` was dead weight; url includes mirror how
  stapel-example-monolith mounts per-module urls. (Monolith already wired
  modules via the service scaffold — covered by new regression tests.)
- Generated **minimal** `requirements.txt` now pins framework ranges
  (`django>=6,<7`, `djangorestframework>=3.14,<4`) — the Django line every
  stapel suite is actually validated on (the source codebases and the
  workspace venv run Django 6; 5.1/5.2 has never been tested) — instead of
  the stale `django>=4.2,<5.0` floor that let a fresh project ride an
  untested Django (G11, version skew). Note: stapel-core still declares
  `Django>=5.1`, an untested claim tracked in
  docs/module-extension-gaps.md.

## 0.3.1 — 2026-07-04
### Added
- `stapel-new-library` — scaffolds a standalone `stapel-*` package repo
  implementing the library standard (workspace `docs/library-standard.md`):
  flat-layout packaging, `STAPEL_<NAME>` conf namespace, comm surface with
  JSON schemas (ping example), serializer seams, MODULE.md skeleton,
  community files, codecov ratchet/floor policy, CI/publish workflows,
  ruff git hooks. Two kinds: `module` (service-capable Django app) and
  `library` (importable L1 package). Generated repo's own tests pass
  out of the box.

## 0.3.0 — 2026-07-03

### Added
- Generator test suite; template/generator refinements.

### Fixed
- Committed bytecode removed from tracking and ignored.


## 0.2.0 — 2026-07-02

- (see git log — changelog discipline starts here; add entries with each PR)
