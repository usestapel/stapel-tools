# Changelog

## [Unreleased]

## [0.18.0] — 2026-07-20

### Fixed — generated monolith mismounted every feature lib (high-priority)

`new_service.py`'s `_url_include` (the function that renders each selected
Stapel feature lib's `path(..., include(<app>.urls))` row into a generated
service's `config/urls.py`) mounted EVERY lib under the hosting SERVICE's own
shared `{url_prefix}api/` (its slug, e.g. `"app/api/"` for a monolith) —
except `stapel_cdn`, hand-special-cased to its real `"cdn/api/"` mount. A
monolith combining more than one feature lib collided every one of them onto
the identical Django path; the generated frontend's api clients (`/<lib>/api/
v1/...`), the generated nginx proxy (per-lib `/<lib>/api/`) and the §57
reserved-paths canon all expected each lib at its OWN prefix, so a generated
fullstack project 404'd on every lib's API. The bug was invisible for a
dedicated single-lib microservice whenever the service's own slug happened to
equal the lib's key (the common case) — it only manifested the moment a
monolith combined libs, or a microservice's slug diverged from its lib's key.

- New `stapel_tools/_url_mounts.py` — the single source of truth for a
  lib's Django mount prefix, derived from `create_project.STAPEL_LIBS`
  (cross-checked lib-by-lib against each sibling checkout's actual urls.py/
  urls_v1.py, not merely trusted from the registry, and against the one
  hand-wired working reference, meettoday's own `config/urls.py`, for auth/
  workspaces/profiles/notifications/calendar/recordings/cdn). One documented
  outlier override (`stapel_translate`, whose own urls_v1.py hardcodes its
  full `"translate/api/v1/..."` prefix internally — mounts at the bare
  project root instead of doubling the segment).
- `new_service.make_context`'s `_url_include` now consults this map for every
  registered Stapel lib — `stapel_cdn` is no longer a hand-special-case, just
  one entry in the same general mechanism. A project-local/custom app not in
  the registry keeps the old shared-service-prefix fallback (no data to do
  better for it).
- `create_project._create_minimal`'s url-include rendering now goes through
  the same helper instead of a second, slightly different ad hoc default —
  one mechanism for monolith, microservices and minimal generation.
- Found and fixed while building the per-lib map: `STAPEL_LIBS["categories"]`
  and `STAPEL_LIBS["listings"]` declared a bare `"<mod>/"` mount (assumed to
  match calendar/video's "bakes api/ into its own urls" shape at onboarding
  time) but their own urls.py docstrings actually read like auth/cdn's — no
  internal `api/` segment, host must supply `"<mod>/api/"`. Fixed to
  `"categories/api/"` / `"listings/api/"` (this also fixed a latent,
  independent mismount in the `minimal` preset for these two libs).

### Verification

- `tests/test_create_project.py::TestMultiLibMonolithMountsEachLibUnderItsOwnPrefix`
  — a monolith with auth+profiles+calendar+cdn mounts each under its own
  prefix (not the shared service prefix), a real `django.urls.resolve()`
  reaches a view for a real operation path per lib (CI-safe auth+gdpr
  variant always runs; the fuller auth/profiles/calendar/cdn variant skips
  cleanly when those sibling packages aren't importable), and nginx-local/
  prod-nginx/Vite/`reserved-paths.json` all agree with the Django mounts.
- `tests/test_create_project.py::TestSingleAndNoLibScaffoldUnaffected` — a
  monolith with zero or one feature lib, and a standalone
  `stapel-new-service` microservice, still mount correctly (the shapes the
  bug was invisible for).
- `tests/test_registry_onboarding.py` / `tests/test_catalog_index.py` —
  updated for the categories/listings mount correction.

### Bump rationale

Minor (0.17.0 → 0.18.0): a correctness fix, but it changes generated output
(every non-cdn feature lib's url mount in a monolith with 2+ libs, plus
categories'/listings' mount in every preset).

## [0.17.0] — 2026-07-20

### Added — cdn auto-wiring

Generalizes the hand-applied meettoday avatar fix (11 hand-edited files) into
`stapel-create-project`'s monolith scaffold: selecting `cdn` in `--modules`
now auto-wires the FULL stack instead of only installing `stapel_cdn` as a
dependency — closing the "the cdn module exists, nothing serves it" gap
(every generated project would otherwise 404 on `/cdn/api/...` and 413 on
real uploads). Everything below is conditional on `cdn` being selected; a
project without it is byte-identical to the pre-fix scaffold.

- `new_service.make_context` — `stapel_cdn`'s url mount is now the literal
  `path("cdn/api/", include("stapel_cdn.urls"))`, not the generic
  `{url_prefix}api/` pattern every other feature lib shares in a monolith.
  Matches nginx's own GENERATED `^~ /cdn/api/` proxy (already built from
  `STAPEL_LIBS["cdn"]`'s default url_prefix) — without this, nginx forwarded
  `/cdn/api/...` to a Django that only knew `{url_prefix}api/...` for it, a
  guaranteed 404.
- `create_project.create_project` — auto-injects a self-documenting
  `STAPEL_CDN = {"ASSET_TYPES": ("avatar",), "ENABLED_SUBMODULES":
  ("images",)}` block (both are stapel-cdn's own library defaults; rendered
  explicitly so the generated settings state intent instead of silently
  relying on upstream defaults) whenever cdn is selected, plus
  `STAPEL_PROFILES = {"PROFILES_AVATAR_CHECK": "comm"}` when profiles is
  ALSO selected. Never overrides an explicit `--module-config` entry.
- `create_project._append_cdn_pip_requirement` — appends
  `stapel-cdn[images]>=<pin>,<<ceiling>` to the generated service's
  `requirements.txt`, independent of whether `stapel_cdn` itself lands via
  git submodule or pip: the `[images]` extra's native dependency (pyvips)
  is never satisfied by vendoring stapel_cdn's source alone.
- `_templates.DOCKERFILE_CDN` — a multi-stage `vips-builder` → runtime
  Dockerfile (mirrors `svc-stapel-studio/Dockerfile`, the verified libvips
  container precedent) selected instead of the plain single-stage
  `DOCKERFILE` whenever the service installs `stapel_cdn`, so `import
  pyvips` resolves at runtime without a compiler in the final image.
- `_frontend_templates.render_cdn_lib_ts` — writes
  `frontend/src/lib/cdn.ts` (a documented STOPGAP — no dedicated
  `@stapel/cdn-react` client pair exists yet) exporting `avatarUrlFor(ref)`,
  wired into `ProfileSettings` (`render_routes_tsx` — the LIVE mount path,
  since profiles always carries a nav mirror — and defensively into
  `render_modules_tsx`'s `ModulesPanel`) whenever profiles-react is also
  wired. `render_modules_tsx` additionally registers a stopgap `cdn`-keyed
  client in the generated `<StapelProvider clients={{...}}>`, reusing the
  primary pair's client — mirrors the hand-applied meettoday fix's
  `clients: { cdn: stapelClient }` — so core's `useStapelClient("cdn")` seam
  (called unconditionally by `ProfileSettings`' avatar-upload hook) never
  throws for want of a registered client.
- nginx's `client_max_body_size 50m;`/`location /media/` and the Vite dev
  proxy's `/media/` rule were ALREADY unconditional/generic (no code
  change needed) — the `/cdn/api/`+`/cdn/swagger/`+`/cdn/admin/` proxy rows
  were already GENERATED per-lib too (`_reserved_backend_prefixes`); this
  release adds explicit regression tests locking both in as this feature's
  own numeric gate.
- New tests: `tests/test_create_project.py::TestCdnAutoWiring` (8 cases —
  INSTALLED_APPS+url mount, settings block, module_config override, pip
  requirement, Dockerfile, ADO001 lint, byte-identical-without-cdn
  regression) and `tests/test_frontend_scaffold.py::TestCdnFrontendAutoWiring`
  (5 cases — client registration, avatarUrlFor wiring, cdn-without-profiles,
  nginx/vite proxy, byte-identical-without-cdn regression).
- Known follow-up (not built here): promoting `frontend/src/lib/cdn.ts`'s
  stopgap logic into a real `@stapel/cdn-react` client pair, which would
  drop the host-registered `cdn` client override entirely.

## [0.16.0] — 2026-07-20

### Added — scripted-fullstack navigation, scaffold half (Ф1)

The lib-side nav foundation (`@stapel/shell-react`'s `resolveNav`/
`<AppShell/>`, `@stapel/core`'s `NavEntry`/`PackageNavManifest` types, and
`nav-manifest.json` on auth-react/profiles-react/notifications-react)
shipped to stapel-react main but isn't published to npm yet. This release
is the SCAFFOLD half: `stapel-create-project` now generates a real
react-router v7 navigated app scriptedly (no LLM) instead of a single
unrouted `<App/>`, whenever `--auth`, `--landing`, or a selected module
with a mirrored nav surface is in play.

- `FRONTEND_ROUTER_DEPS` (`create_project.py`) — `react-router` pinned to
  the latest v7 release (`7.18.1`, verified via `npm view "react-router@^7"
  version` — the PLAIN `npm view react-router version` dist-tag is now a
  v8 major and would silently pull an incompatible one). Added to the
  generated `frontend/package.json` whenever routing is active.
  `FRONTEND_SHELL_REACT_PACKAGE`/`_VERSION` pin `@stapel/shell-react`
  ahead-of-npm (not published yet — 404s on npm today; pinned from the
  sibling stapel-react checkout's own package.json, same discipline as
  `STAPEL_LIBS`' `ahead_of_pypi` flag), added whenever a selected pair
  contributes nav entries.
- `FRONTEND_REACT_LIBS["auth"|"profiles"|"notifications"]["nav"]` — a
  manually PINNED MIRROR of each pair's own `nav-manifest.json` (auth.login
  + auth.security, profiles.settings, notifications.feed). New
  `scripts/check_nav_manifest_sync.py` — a drift gate (peer of the
  pin-verification comments) diffing the Python mirror against the sibling
  stapel-react checkout's real `nav-manifest.json` files; skips cleanly
  when that checkout isn't present.
- New generated files (`_frontend_templates.py`): `src/nav.generated.ts`
  (bakes `INSTALLED_NAV_MANIFESTS` at codegen time, computes `RESOLVED_NAV`
  by calling the real `resolveNav` against the committed
  `stapel.nav.json` at import time — the same call `<AppShell/>` itself is
  built on), `src/routes.tsx` (`createBrowserRouter` — react-router v7
  ships v6-future behaviour as its own default, no future-flags object to
  emit), `src/ProtectedRoute.tsx` (gates `/app` on
  `useActiveSessionReady`/`useAuthSessionState`, both already-published
  hooks — no auth-react change needed), `stapel.nav.json` (empty
  `{"overrides": {}}` override channel, deep-merge-over-default like
  `stapel.theme.json`), and `src/LandingPage.tsx` (`--landing` only, styled
  entirely through `cssVar("<role>")` §68 tokens, no raw hex).
- `main.tsx` mounts `<RouterProvider router={router}/>` (wrapped in
  `<ModulesProvider>` when any `@stapel/<module>-react` pair is also wired)
  once routing is active; a selection with none of `--auth`/`--landing`/a
  nav-bearing module collapses to the EXACT prior `<App/>` output, byte for
  byte (regression-tested).
- New CLI flags: `--landing` and `--auth`/`--no-auth` (default: derived
  from whether the `auth` module is selected).
- New `TestFrontendNavWiring` (`tests/test_frontend_scaffold.py`, 7 tests)
  plus 2 pre-existing `TestFrontendReactWiring` tests updated to select a
  non-nav-bearing module combo (their App.tsx/modules.tsx assertions no
  longer apply to a nav-bearing selection, which now routes instead).

Deferred to post-publish: an actual `npm ci && npm run build` against a
generated project — `@stapel/shell-react` isn't on npm yet, and
auth-react/profiles-react/notifications-react's last PUBLISHED releases
predate their `nav-manifest.json`/`NavEntry` core types. The import-graph
gate (every non-relative import resolves to a declared `package.json` dep)
covers everything short of an actual install today.

## [0.15.0] — 2026-07-19

### Fixed — monolith preset shipped no root controls surface (studio e2e-3f018cc3, R3/§44 follow-up)

Live studio runs run `make -C <assembled_root> lint/controls/test/boot-smoke`
at the generated project's ROOT regardless of preset. The minimal preset
always wrote a root `Makefile` with those targets; the monolith preset's
Django backend lives inside `svc-<slug>/` instead and never got a root
`Makefile` at all — every live monolith run failed the architect's lint gate
unfixably (the architect can't create root build files), blocking the
fullstack pipeline entirely. Closed, deletion-driven:

- New root `Makefile` for monolith (`MONOLITH_MAKEFILE`,
  `_compose_templates.py`) — `.PHONY: controls lint test boot-smoke`,
  `controls: lint boot-smoke test`, each target delegating into
  `svc-<slug>/`. Target names and `controls` semantics match the minimal
  preset's own Makefile 1:1, so the studio contract is preset-agnostic.
  Backend-only for now (a comment says so); `frontend/`'s own `npx eslint .`
  is a separate stage, not silently dropped.
- New `svc-<slug>/Makefile` (`SVC_MAKEFILE`, written by `scaffold_service` —
  also reaches every `stapel-new-service`-created service, monolith or
  microservices) exposing the same four targets standalone: `lint` runs
  `ruff check .`, `test` runs `pytest -q`, `boot-smoke` runs `manage.py
  check` under a new `config/settings/boot_smoke.py` tier.
- New `svc-<slug>/pyproject.toml` (`SVC_PYPROJECT`) — the service had NO
  ruff config before, so `ruff check .` (once the Makefile existed) would
  have run under bare defaults and flagged the Django settings tiers'
  intentional star-imports (`from .base import *`) as 25 false-positive
  F405/F403/E402/I001 errors. Selects the same rule set as the minimal
  preset (`E,F,W,I,B,UP`) with one addition: `config/settings/*.py` is
  exempted from F403/F405 (the star-import pattern is Django's own
  convention across settings tiers, not a bug).
- New `svc-<slug>/config/settings/boot_smoke.py` (`BOOT_SMOKE_SETTINGS`) —
  the monolith counterpart of the minimal preset's
  `config/settings_boot_smoke.py` (R3/§44 was minimal-only until now).
  Layers over `.base`, not `.dev`/`.local`: the dev tier adds
  django-debug-toolbar to `INSTALLED_APPS`, whose SQL panel unconditionally
  probes `django.contrib.gis` at `AppConfig.ready()` time — an environment
  fragility (observed as a raw `OSError` crash on a host with a
  broken/partial GDAL native lib, uncaught by the toolbar's own
  `except ImportError` guard) this gate must not inherit. Also seeds
  `os.environ["SECRET_KEY"]` with an insecure dev-only fallback when unset:
  `base.py` carries no fallback of its own (only `dev.py`'s does), and
  stapel_core's `config.E001` system check resolves required keys via
  `os.environ` directly — independent of any `django.conf.settings` value —
  so this gate must run standalone (no shell-sourced `.env`, no docker)
  straight after generation.
- `ruff>=0.4` added to the service's `requirements.txt` dev/test section
  (it was entirely absent — `make lint` had nothing to run against).
- Second generator defect found and fixed while getting the assembled
  monolith controls-green from birth: `BASE_SETTINGS`/`DEV_SETTINGS`
  (`_templates.py`) carried 4 extraneous f-string prefixes (F541) and 2
  import-order violations (E402); `ASGI_PY`/`WSGI_PY`/`URLS_PY`/
  `PROD_SETTINGS` carried unsorted import blocks (I001); `MODELS_PY`/
  `ADMIN_PY`/`MODULE_MODELS`/`MODULE_ADMIN` carried an always-unused import
  on a fresh scaffold (F401, silenced with an explanatory `# noqa`). All
  fixed at the template source, verified via a real `ruff check .` run
  (`All checks passed!`) against a freshly assembled monolith.
- Known residual gap: `make test`/`make controls` need a live Postgres
  (`docker compose -f docker-compose.local.yml up db` — same as any
  monolith dev workflow, not new) — not exercised by this fix's own test
  suite for that reason. microservices shares the missing-root-Makefile gap
  (confirmed: a fresh `stapel-example-microservices`-shaped assembly has no
  root Makefile either) but starts with zero services to lint until
  `stapel-new-service` is run, so it is not closed here — follow-up.

## [0.14.0] — 2026-07-19

### Added — frontend wiring: scaffold the selected `@stapel/<module>-react` pairs (owner directive, frontend-wiring gap)

The generated `frontend/` used to be a generic Vite+React shell that never
wired a project's selected feature libs' React counterparts, even when a
published `@stapel/<module>-react` pair existed for one. Closed, data-driven:

- New `FRONTEND_REACT_LIBS` registry (`create_project.py`) maps each
  `STAPEL_LIBS` key with a published pair (`auth`, `billing`, `calendar`,
  `notifications`, `profiles`, `recordings`, `workspaces` — versions pinned
  against both the sibling `stapel-react` checkout AND live
  `npm view @stapel/<name>-react version`, identical for all seven) to that
  pair's `create<Module>Runtime`/`<Module>Provider`/`register<Module>I18n`
  exports and, where genuinely zero-required-prop, its `/default` antd-skin
  top-level component (`AuthPanel`, `NotificationFeedList`,
  `ProfileSettings` — read off each pair's own prop interfaces, not
  guessed; `workspaces`' `/default` components all require a `workspaceId`
  the scaffold can't fabricate, so it stays provider-only).
- `frontend/package.json` gains the selected pairs' deps + `@stapel/core` +
  `@tanstack/react-query`, plus `antd`/`@stapel/tokens-antd` IFF a selected
  pair mounts a `/default` skin — never for a headless-only selection
  (billing/calendar/recordings ship no antd peer dep at all).
- New generated `frontend/src/modules.tsx` — the data-driven registry: one
  shared `<StapelProvider>` (first selected pair as the default client,
  every other pair via `clients={{ "<mod>": ... }}`, the exact multi-pair
  composition `@stapel/core`'s own README documents) wrapping one
  `<XProvider>` per selected pair (`ModulesProvider`), plus `ModulesPanel`
  mounting every selected pair's zero-config default component wrapped once
  in antd's `<ConfigProvider theme={toAntdThemeConfig("light")}>` (§68
  bridge). Regenerating this file is "change the module selection", never a
  hand-edit — adding a pair later is data, not code.
- `frontend/src/App.tsx` switches to a second, still-STATIC template
  (`APP_TSX_WITH_MODULES`) that imports `ModulesProvider`/`ModulesPanel`
  from `./modules.js` whenever the selection has >=1 react-paired module;
  a selection with none gets the byte-identical prior clean shell (no
  `modules.tsx`, no package.json churn — regression-tested).
- `tests/test_frontend_scaffold.py::TestFrontendReactWiring` — exact
  dependency-set assertions, version pins, provider/runtime wiring per
  selected pair, the zero-config-vs-provider-only default-component split,
  the import-resolves-to-a-declared-dep "compiles conceptually" gate, and
  the clean-shell regression for a selection with no react-paired module.

### Added — scaffold `stapel.theme.json` + `stapel-tokens`-bin pre-commit hook (§68 Ф5, color-token-matrix)

§68's neutral colour-role dictionary now reaches the `stapel-create-project`
monolith scaffold, so a freshly generated frontend starts with a real,
themeable colour source instead of hardcodes — and the generator is called
through `@stapel/tokens`' own published bin, never vendored:

- `frontend/stapel.theme.json` — the neutral role dictionary (`surface*`/
  `text*`/`border*`/`brand*`/`link*` + `success`/`warning`/`error`/`info` ×
  `{base, -bg, -border, -on}`), seeded with a sensible bluish `brand` and
  standard status colours, light+dark, in `_frontend_templates.THEME_JSON`.
- `frontend/package.json` gains the `@stapel/tokens` devDependency and
  `gen:tokens`/`gen:tokens:check` scripts calling the published
  `stapel-tokens` bin directly (`--targets core` by default — the default
  studio delivery is antd, self-sufficient; Tailwind stays opt-in, and if a
  project does add it the bin's `tailwind@4` `@theme` adapter is the target,
  never the legacy v3 RGB-triplet one).
- New `.pre-commit-config.yaml` hook `tokens-check` (frontend projects
  only) — `npm run gen:tokens:check` in `frontend/`, same
  regenerator-of-everything-that-can-be-regenerated shape as
  `config-manifest-check`/`reserved-paths-check`/`gen-client-check`; fails
  the commit on drift, auto-fix is `npm run gen:tokens` (no `:check`).
- `AGENTS.md`'s generated §5 (generated-artifacts table) and §6 (frontend
  rules) now spell out, in the generated project's own AGENTS.md, that
  colours live in `stapel.theme.json` → semantic roles, that **the default
  button colour is the `brand` role**, and how to re-theme (edit
  `ramps`/`core` → `npm run gen:tokens` or just commit — the pre-commit
  hook regenerates and gates drift).
- No forked/vendored copy of the generator anywhere in the scaffold
  templates — the exact `gen-tokens.mjs`/`tokens-lib.mjs` failure mode the
  color-token-matrix spec diagnosed in a live host is closed by
  construction (regression-tested: `TestThemeJsonScaffold::
  test_no_forked_generator_vendored_into_scaffold_templates`).

## [0.12.0] — 2026-07-17

### Added — `stapel-gen-client` + `stapel-docs`: the regenerator-of-everything pre-commit surface (owner directive)

Owner directive: "в pre-commit должен быть регенератор ВСЕГО, что можно:
клиентов (если был оверрайд), CONFIG.MD с полной сводкой энвов, документация
по api/флоу — в идеале двуязычная." CONFIG.MD/reserved-paths/PRESENTERS.MD
regeneration already existed (§57); this release closes the other two:

- **`stapel-gen-client`** — tier 2 of the two-tier answer to "our profile
  is overridden, its frontend pair needs to handle that"
  (`docs/pending/profile-fields.md` "Дополнение владельца" §17.07): a
  universal, non-library-specific command that regenerates a typed TS
  client from a PROJECT's OWN `schema.json` into
  `frontend/src/api/generated-override/<module>/schema.ts`, reusing
  openapi-typescript (the exact engine stapel-react's own
  `scripts/gen-api.mjs` already uses) via `npx` rather than reimplementing
  it. Gated on `override_active()` — a non-empty `STAPEL_SWAP = {...}`
  anywhere in the project, or an explicit `stapel.override.json`
  `"clientOverride"` flag — so the `gen-client-check` pre-commit hook is a
  silent no-op on every project that hasn't overridden anything yet, and
  comes alive automatically the day one does. `--check` is the drift gate,
  `--force` bypasses the gate for manual runs.
- **`stapel-docs`** — bilingual `docs/api.en.md` + `docs/api.ru.md`
  generation from a project's `schema.json`/`flows.json`/`errors.json`
  (endpoints + DTO fields sourced from backend docstrings, R004 canon; flow
  user-stories; error catalog). Where a module already ships a Russian
  translation (`translations/flows.ru.json`/`translations/errors.ru.json`
  — the stapel-translate precedent) it's used verbatim; otherwise the
  English text is shown with an honest `(en)` marker, never fabricated.
  Supports the monolith `codegen/generated/` aggregate (re-split into
  per-module sections by path prefix), per-service/vendored `docs/`
  checkouts, and the literal `<mod>/api/v1/schema.json` shape. `--check`
  is the `api-docs-check` pre-commit drift gate; a project with no
  `schema.json` yet is a graceful no-op.
- New shared discovery module `_docgen_scan.discover_modules()` — the one
  scanner both commands key off, so a project's doc sections and its
  client-override folders always agree on module names.
- `.pre-commit-config.yaml` template gains `api-docs-check` (every project
  type) and `gen-client-check` (frontend-carrying project types); AGENTS.md
  template gains a "Generated artifacts" table naming every regenerator and
  its source, plus a frontend bullet on the generated-override seam;
  README "Checks" section templates and this repo's own README document
  both new commands.

## [0.11.5] — 2026-07-17

### Added — `stapel-catalog --index`: the full machine index (agent-knowledge-base.md §64 "Волна 1")

`docs/pending/agent-knowledge-base.md` §64 found the catalog aggregator's
mechanism complete but its artifact never materialized on disk, and flagged
it as the cheapest unblock for the ADVISOR exact-layer (stapel-studio's
`studio_cto.advisor_index`, which already documented the consumer shape it
expects — this release is the producer side.

- `build_index()` extends `build_catalog()`'s per-module aggregate with,
  per module: `flows` (verbatim `docs/flows.json`, `[]` if the module hasn't
  documented any — an honest gap, not fabricated narrative), `errors`
  (verbatim `docs/errors.json`), `config_md` (CONFIG.MD table rows, key
  omitted for a module without one yet), `stapel_libs` (the
  `STAPEL_LIBS` registry's `url_prefix`/`requires`/`pin` for that module,
  omitted for an unregistered module) and, when a matching `-react` sibling
  package exists, `components` (operations/hooks/demos projected from its
  `manifest.json`).
- New CLI surface: `stapel-catalog --index -o catalog.json [--workspace W]
  [--react-root R]` emits the single-file full index; `--check` drift-gates
  either mode (index or the classic catalog.json+catalog.md pair) without
  writing.
- `catalog.json`/`catalog.md` are materialized at the repo root as a real
  snapshot of the current workspace (10 modules with a swept
  `capabilities.json` today) — the artifact existed only as an unrun
  mechanism before this release.

## [0.11.4] — 2026-07-17

### Fixed — ADO001 false positive on stapel-tools' OWN generated monolith

Three v0.11.x tags in a row (0.11.1/0.11.2/0.11.3) failed their own release-
gating `e2e-generated-project` CI job at the `stapel-verify` step:
`stapel-adoption-lint` reported `ADO001` ("module 'stapel_auth' is installed
and ships a urlconf but is not mounted") for every HTTP-capable feature lib
in a freshly generated monolith — even though `config/urls.py` genuinely
mounts each one (`path(f"{url_prefix}api/", include("stapel_auth.urls"))`,
this canon's own mount idiom — see `_templates.URLS_PY` /
`new_service.make_context`; the prefix is a runtime `settings.URL_PREFIX`
value, so the route is written as an f-string, not a plain string literal).

Root cause was in the linter, not the generator: `adoption_lint._route_literal`
only recognized `ast.Constant` route arguments, so an f-string route parsed as
neither a constant nor anything else it handled — `_walk_patterns` bailed via
its `raw_route is None` guard *before* ever inspecting the `include(...)`
target one argument over, silently dropping the mount from `ADO001`'s
`mounts` set.

- **`_route_literal` now also renders `ast.JoinedStr`** (f-string) routes:
  literal segments kept verbatim, each dynamic `FormattedValue` replaced with
  a `"{}"` placeholder (same normalization `re_path` regex groups already
  get) — enough for `ADO001`'s mount detection, and a reasonable
  best-effort route for `ADO002`'s duplicate-route check too.
- New regression coverage: `tests/test_adoption_lint.py` (mount via an
  f-string route) and `tests/test_create_project.py`
  (`TestGeneratedMonolithPassesAdoptionLint`) — the latter drives
  `create_project()` for real (monolith + `auth`) and asserts
  `stapel-adoption-lint` reports zero `ADO001` findings, so this specific
  generator/linter interaction can't silently regress again.

Verified locally end to end (assemble → `stapel-verify` 0 errors → live OTP
circle → frontend build → nginx circuit), including inside a clean
`python:3.12-slim` container mirroring the CI job, before retagging.

## [0.11.3] — 2026-07-17

### Fixed — reserved-prefix canon: a module's bare root belongs to the frontend

Live-run collision (owner report): a generated nginx/Vite rule reserved a
selected lib's ENTIRE prefix (`location /calendar/ { proxy_pass ...; }`),
silently swallowing an identically-named frontend SPA page (`/calendar` —
the calendar view). Root cause: `_reserved_backend_prefixes` reserved a
module's bare root defensively ("so a future root-mount lands already
proxied"), which is exactly what a frontend router also needs.

- **Reservation narrowed to named sub-surfaces**: `/<mod>/api/`,
  `/<mod>/swagger/`, `/<mod>/schema.json`, `/<mod>/admin/` — the sub-surfaces
  our canon's generic per-service URLconf (`URLS_PY`) would mount if that lib
  became its own service — never the bare root or an arbitrary sub-path
  (both stay the frontend catch-all's). `admin`/`staticfiles`/`media`/the
  project's own slug keep their full-subtree reservation (unchanged — those
  genuinely own their whole namespace today). Applies to all three
  consumers: local-nginx (`^~`/`=` locations on the sub-surface), prod-nginx
  (same), and the Vite dev proxy.
- **New `reserved-paths.json`** at the generated project root — the single
  source every consumer above renders from, schema agreed with
  `@stapel/eslint-plugin`'s `no-reserved-backend-route` rule:
  `{"reservedPathPrefixes": [...]}`, a flat array of `/`-leading prefixes.
  The generated `frontend/eslint.config.js` points
  `settings.stapel.reservedPathsFile` at it; `@stapel/eslint-plugin` +
  `eslint` are now frontend devDependencies.
- **New `stapel-reserved-paths` CLI** (`--check` for the pre-commit drift
  gate, no-flag to regenerate) — wired into a monolith's
  `.pre-commit-config.yaml` as `reserved-paths-check`, next to
  `config-manifest-check`/`presenter-catalog-check`.
- **AGENTS.md**'s reserved-namespace rule reworded: a lib's bare `/<mod>`
  root is the frontend router's; only its named sub-surfaces are the
  backend's.
- **CI**: the live nginx circuit (`ci.yml`/`publish.yml`) now asserts a
  module's bare root resolves to the frontend (200, not a redirect into the
  backend reservation) and its `/api/` sub-path still resolves into the
  (down, in this nginx-only circuit) backend reservation (502 — proves
  routing, not content).

## [0.11.2] — 2026-07-17

### Changed — env/deploy canon revision (owner decisions after the live run)

- **`.env.local` is COMMITTED** (renamed from `.env.dev` — "dev" reads as the
  dev STAND; this file is strictly the local machine). Deterministic,
  recognizably dev-marked values only: `django-insecure-dev-*` SECRET_KEY/JWT
  (the prefix stapel-core's prodguard already refuses at prod boot — no new
  core mechanics), `POSTGRES_PASSWORD=stapel` (refused by guard_db_password),
  admin/admin superuser, and an explicit `STAPEL_LOCAL_ENV=1` flag. Clone →
  `docker compose -f docker-compose.local.yml --env-file .env.local up` works
  with zero manual config. The stand names `.env.dev`/`.env.stage`/`.env.prod`
  are RESERVED and gitignored (generated per stand, never committed).
- **`deploy/` scripts generated with a hard gate** (`deploy/deploy.sh` +
  `deploy/check-env.sh`, monolith + microservices): deploy refuses any env
  carrying dev markers (`STAPEL_LOCAL_ENV`, `django-insecure-*`/
  `dev-insecure-*`/`change_me*` secrets, `DEBUG=true`, default passwords,
  `*_PROVIDER=mock`, non-prod `DJANGO_ENV`) with a clear "сгенерируйте боевой
  env" error. The same values are refused at boot by core's prodguard —
  script fails BEFORE containers restart, guard covers bypasses.
- **Compose naming scale**: local stack = `docker-compose.local.yml` (was
  .dev); dev/stage/prod compose names reserved for stands (prod stays
  `docker-compose.yml`). nginx-local dir + FRONTEND_LOCAL_UPSTREAM renamed to
  match.
- **`docker-compose.local.yml` is SELF-CONTAINED** (no `include:` of base) —
  root fix for the v0.11.0/0.11.1 CI failures: several compose versions
  reject overriding an included service ("services.nginx conflicts with
  imported resource"). Local volumes are `-local`-suffixed; local nginx
  mounts its envsubst template (`default.conf.template` — must have exactly
  that name to overwrite the image's default site) at /etc/nginx/templates
  only, defaults to port 8080.
- **nginx canon (owner, live-run root-causes)**: `proxy_set_header Host
  $http_host` (not `$host` — strips the port), `absolute_redirect off;` in
  every generated server block (nginx's own /admin → /admin/ redirect bakes
  in the internal port 80 and loses the external mapping; port_in_redirect
  does not fix it), and deferred upstream resolution (`set $stapel_backend
  …; proxy_pass $stapel_backend;` — a literal host refuses to START while
  the backend container is down).

### Added — §55 presenter discipline in the generators

- `new-library`/`new-module` scaffold `presenters.py` (default presenter +
  `declare_swap()` + `get_<x>_presenter()`; DTO instantiated ONLY there);
  view templates go through `get_presenter` — a generated project passes
  stapel-verify (SWAP001/SWAP002 included) from scratch, proven by test.
- `create_project`/`assemble_scaffold` generate the project-root
  `PRESENTERS.MD` through stapel-core's exported `write_presenters_md()`
  hook (best-effort with a manual-command note when core isn't importable);
  new `presenter-catalog-check` pre-commit hook (`manage.py
  presenter_catalog --check`) keeps it fresh.
- AGENTS.md §2: imperative CORRECT/WRONG snippet pair (get_presenter vs
  direct DTO in a view).

### Added — generative prefixes + the E2E "оно едет" CI gate

- nginx locations (local + prod) and the Vite proxy table are GENERATED from
  the selected libs (STAPEL_LIBS url_prefixes + slug + admin + static/media)
  — one list, three surfaces; the "forgot /calendar in the proxy" bug class
  is unrepresentable.
- New CI job `e2e-generated-project` (ci.yml + publish.yml, release-gating):
  stapel-assemble monolith (auth+notifications) with green gates →
  stapel-verify=0 → live circle via `scripts/e2e_live_circle.py` (migrate →
  register → OTP code read from the LOG (mock canon) → verify → REGISTERED →
  authenticated /me 200) → `npm install` + vite build of the generated
  frontend → compose config validity → live nginx circuit (`/e2e` without a
  slash → 301 with a RELATIVE Location — the redirect-port regression,
  pinned forever).
- Fix found BY the e2e circle: the scaffold never generated
  `config/celery.py`, so every `@shared_task` in an installed lib bound to
  Celery's default unconfigured app (amqp://localhost) — stapel-auth's
  login-notification `.delay()` 500'd the login. Now: standard celery app
  module + `config/__init__.py` binding (all presets) +
  `CELERY_TASK_ALWAYS_EAGER` in dev/minimal settings (no broker needed
  locally).
- Fix (studio-integration finding, root): `assemble_scaffold`'s check gate
  hardcoded manage.py at the project root — every `--type monolith` assembly
  was `result.ok=False` by construction. The gate now resolves cwd by
  project type (svc-<slug>/, config.settings.base, project .env loaded);
  studio's local workaround can be removed.
- CFG004 (warning): CONFIG.MD row with an empty Purpose column;
  `stapel-config-manifest` CLI (--check/regenerate) + `config-manifest-check`
  pre-commit hook (both landed with the 0.11.x wave, documented here).

## [0.11.1] — 2026-07-17

### Fixed
- Retried the 0.11.0 PyPI publish: the tag build failed on a resolver
  conflict (installs `stapel-gdpr`/`stapel-auth` from git main alongside
  `stapel-core` from git main; `stapel-gdpr`'s pyproject still pinned
  `stapel-core<0.11` at the time). Cleared now that the 0.11 fleet re-pin
  has landed on `stapel-gdpr` main (0.3.8) and `stapel-auth` main. No
  functional change.

## [0.11.0] — 2026-07-17

### Added — §57 dev/prod compose + nginx canon, entrypoint canon, AGENTS.md, pre-commit README canon, dev-env canon, CONFIG.MD regeneration hook

Live-run postmortem, owner directive package. New generator surfaces for
`stapel-create-project --type monolith` (the "recommended" preset — scope
for this pass; microservices/minimal frontend wiring is a tracked follow-up).

- **Dev/prod compose + nginx (§57 item 1).** `frontend/` — a real Vite +
  React + TypeScript scaffold (`stapel_tools/_frontend_templates.py`) — is
  now generated alongside the backend. `docker-compose.dev.yml` starts the
  Vite dev server (`frontend`, hot reload, logs visible) + the Django
  backend (now actually booting `config.settings.dev`, not the baked-image
  prod default — see the entrypoint-canon fix below) + a dev-nginx that
  proxies the reserved backend namespace (`/<slug>/`, `/staticfiles/`,
  `/media/`) to Django and everything else to Vite. `docker-compose.yml`
  (prod) gains a one-shot `frontend-build` service that populates a
  `frontend-dist` volume the main nginx serves as static files (SPA
  fallback). Proxy targets are env vars with compose-network defaults
  (`BACKEND_UPSTREAM`, `FRONTEND_DEV_UPSTREAM`), overridable via `.env`/
  `.env.dev` for a native run — nginx's own envsubst-on-templates feature
  renders `service-configs/nginx-dev/nginx-dev.conf.template`.
- **Static/media collision check (§57 item 2, answered).** No collision:
  monolith already namespaces `STATIC_URL`/`MEDIA_URL` per service slug
  (`/staticfiles/<slug>/`, `/media/<slug>/`), and every backend route lives
  under its own `/<slug>/` prefix — a frontend router must simply not claim
  those reserved prefixes, which nginx enforces by prefix-match specificity
  (documented in `NGINX_CONF`'s comments and the generated `AGENTS.md` §3).
- **Entrypoint canon (§57 item 3).** `bootstrap.sh` now runs
  `createsuperuser --noinput` (Django's own env-driven flow —
  `DJANGO_SUPERUSER_USERNAME/EMAIL/PASSWORD`) between migrate and
  collectstatic — no project-specific Python, no model imports (closes the
  live-run defect: a hand-rolled entrypoint importing a since-deleted model).
- **`AGENTS.md` (§57 item 4).** `create_project` emits a base OSS
  coding-rules file at every project root (`_agents_template.py`):
  StapelResponse/ERR_*/@flow_step (R001-R007), get_model/get_presenter
  indirection (SWAP001/002), config-in-one-place + purpose (CFG001-004),
  URLField max_length (URL001), and — for a monolith's `frontend/` — the
  `@stapel/eslint-plugin` rule set (no raw colours/fetch/storage, typed
  events, i18n-key existence).
- **Pre-commit README canon (§57 item 5).** Every project type (plus
  `new-library`) gets a `.pre-commit-config.yaml` (`_precommit_templates.py`)
  running `stapel-verify` (+ `eslint` for a monolith's frontend) and a root
  README "Checks" section documenting `pip install pre-commit && pre-commit
  install`.
- **Dev-env canon (§57 item 7 — owner follow-up).** `.env.dev` is generated
  with real secrets (not placeholders): DB/comm-bus (inline by default, never
  assumes Kafka), `DJANGO_SUPERUSER_*` for the entrypoint canon, Vite/backend
  proxy targets. On a local stand, **mock providers are on by default**:
  stapel-notifications already defaults `EMAIL_PROVIDER`/`SMS_PROVIDER` to
  `"mock"`; when stapel-auth is selected, `config/settings/dev.py` now also
  sets `STAPEL_AUTH["USE_MOCK_SMS_OTP"/"USE_MOCK_EMAIL_OTP"] = True` (booleans
  in stapel-auth's `no_env` list — only settable this way), so OTP codes are
  logged, never sent — registration/login is completable by reading the
  service log. A new `--env-preset` (`standalone` default, `studio`) picks
  the channel-origin preset; `studio` adds DOCUMENTED STUB keys (generic
  email sender, "Login via Stapel Studio" OAuth) with `TODO(§57 studio
  preset)` markers — no sender/studio-OAuth infrastructure exists yet, this
  is only the shape of the future preset. Threaded through
  `create_project`/`assemble_scaffold`.
- **CONFIG.MD regeneration hook (§57 item 8 — owner follow-up).**
  `stapel-config-manifest` (new CLI, `config_manifest.py`) regenerates a
  project's root `CONFIG.MD` from its libs' own registries — `--check` fails
  (drift) without writing, no flag regenerates + exits 0 for `git add`.
  Wired into every generated `.pre-commit-config.yaml` as
  `config-manifest-check`. New **CFG004** (warning, `config_lint.py`): a
  `CONFIG.MD` row with an empty Purpose column — closes the "documented in
  name only" gap CFG001-003 didn't cover; promotes to error once the
  per-lib CONFIG.MD sweep completes (DOC001's posture).
- Bug fix found auditing the above: `stapel-new-service`'s compose-file
  containment check false-positived against the monolith/microservices
  templates' own commented example (`"  # svc-app:"`), silently leaving a
  project's first/default backend service never actually wired into
  `docker-compose.yml`/`docker-compose.dev.yml`. Fixed to match a real
  service key line, not a substring.
- Bug fix: `assemble_scaffold`'s static gates (`manage.py check`/boot-smoke)
  now load the generated project's own `.env` into the subprocess
  environment — needed since stapel_core's newer `config.E001` system check
  resolves `required` CONFIG.MD keys (e.g. `SECRET_KEY`) against the actual
  process environment, independent of any settings.py fallback.

## [0.10.4] — 2026-07-16

### Added — v1 canon in the scaffolds (§60, api-versioning.md §2)

- `new-library` (module kind) now scaffolds `urls_v1.py` from day one: the
  root `urls.py` is a thin `api/v1/` mount, the actual URL set lives in
  `urls_v1.py`, the ping example serves at `/<slug>/api/v1/ping`. No bare
  `/<mod>/api/...` variant exists — canon, not a choice.
- `new_module` (service-embedded app) scaffolds the same split:
  `urls.py` mounts `api/v1/` → `urls_v1.py`.
- `new-react-lib` default `path_prefix` → `/<module>/api/v1/`
  (`MANIFEST_TAGPREFIX` follows).
- (also shipping in this release: the §55 SWAP001/SWAP002 + DOC001 lints and
  the STAPEL_LIBS composite registry entries listed under their own headings
  below — committed on main since 0.10.3.)

### Added — SWAP001/SWAP002 + DOC001: the §55 anti-lock-in lints

- New `stapel-swap-lint` (`stapel_tools/swap_lint.py`), two error-level rules
  (`docs/pending/extensibility-presenters.md` §1/§6 — the django-oscar #3232
  bug class):
  - **SWAP001** — direct import (or import-and-instantiate) of a class that
    is registered as the `default=` of a `get_model()`/`get_presenter()`
    call anywhere in the scanned tree (`stapel_core.django.swappable`,
    STAPEL_SWAP registry). Registry is built statically in one AST pass
    (every accessor call's dotted `default` string literal), violations
    found in a second pass over `from X import Y` bindings — no Django
    execution. A stray direct import silently defeats a host's config-swap
    for that call site; this makes the discipline machine-checked.
  - **SWAP002** — a `views.py` instantiating a `@dataclass` DTO imported
    from a `dto.py` module directly, bypassing the presenter
    (`get_presenter(...)` → `.present(...)`). Only cross-module `dto.py`
    imports are in scope (a local view-only dataclass is not the presenter
    contract); `tests/` and `test_*.py` are excluded for both rules
    (fixtures/factories legitimately build concrete classes).
  - False-positive posture: unresolvable imports (`import pkg.mod` +
    attribute access) resolve toward NOT flagging — opposite of URL001's
    default, because a false positive here blocks a legitimate
    definition/consumer file, not a width choice. `# noqa: SWAP001`/
    `# noqa: SWAP002` escapes supported.
- New `stapel-doc-lint` (`stapel_tools/doc_lint.py`):
  - **DOC001** (warning, the spec's "DOC-FIELD") — a Django model field with
    neither `help_text=` nor a `#` comment on the line above. Warning, not
    error: the legacy surface is large (74 findings on stapel-core alone at
    introduction), same W-before-E rollout as R100. Undocumented fields are
    a silent gap in the presenter auto-catalog (§4) and generated OpenAPI
    schema (§2). `@dataclass` DTO docstrings stay R004's job (`lint.py`) —
    DOC001 never scans `dto.py`. `--strict` flips warnings to exit 1.
- Both wired into `stapel-verify` as sections 6 and 7 (`run_swap_lint`,
  `run_doc_lint`); console scripts `stapel-swap-lint` / `stapel-doc-lint`
  registered.
- Tests: `tests/test_swap_lint.py` (registry build, direct import, direct
  instantiation, accessor-path clean, tests-dir exclusion, noqa, SWAP002
  positive/negative incl. local-dataclass and non-views exclusions),
  `tests/test_doc_lint.py` (help_text pass, preceding-comment pass, noqa
  forms, FK/relation fields, manager/constant non-fields, dto.py exclusion,
  migrations skip), `tests/test_verify.py` extended to 7 linters.

## [0.10.3] - 2026-07-16

### Added — `stapel-verify`: one gate running the whole lint arsenal

- New `stapel-verify <project_root> [--workspace ROOT ...] [--base-sha SHA]
  [--json]` CLI (`stapel_tools/verify.py`). Pure composition — reuses each
  existing linter's own public entrypoint (`lint.scan_paths`,
  `adoption_lint.lint_project`, `url_lint.lint_paths`,
  `config_lint.lint_project`, `migration_lint.lint_paths`) and adds no new
  checking logic of its own.
- Motivation: a project's CI can be green on a generic linter while R006
  (`StapelResponse({...})` raw dict, skipping the serializer) and ADO002
  (a hand-rolled route shadowing an operation the installed module already
  ships) sit unexercised — not because the rules don't exist, but because
  nothing wires all the linters into the pipeline that actually runs.
  `stapel-verify` is the mechanical answer: one command, the entire arsenal,
  exit 1 if any of them found an error.
- Output: a summary table (linter → errors/warnings), full findings from
  every linter, and a machine `--json` form (per-linter errors/warnings/
  findings) for agents/CI. `--workspace`/`--base-sha` are forwarded to the
  sub-linters that accept them (adoption-lint, migration-lint).
- Console script registered in `[project.scripts]`.
- Tests (`tests/test_verify.py`): a fixture project with a deliberate
  violation for every composed linter (R006, ADO001/ADO002/ADO004, URL001,
  CFG001, MIG001) asserting each linter contributes to the aggregate report,
  a clean-project all-zero case, CLI exit codes (0/1/2), `--json` shape, and
  `--workspace` forwarding.
- **Fixed a latent bug found by this integration**: `adoption_lint.py`'s
  ADO002 findings stored a `Path` object (from the `urlconf_by_route` map)
  as `.path` while every other rule stores a `str` — `findings.sort()`
  crashed with `TypeError: '<' not supported between instances of
  'PosixPath' and 'str'` whenever ADO001 and ADO002 both fired on the same
  project, a combination its own test suite never exercised together.
  One-line fix: `str(uf)` at the point of insertion.

## [0.10.2] - 2026-07-16

### Fixed — CI: `TestAuthSubfeatureAxes` depended on a workspace sibling not present in an isolated checkout

- `test_unknown_auth_axis_is_a_hard_error_not_silently_passed_through` (and
  its siblings in `TestAuthSubfeatureAxes`, `tests/test_assemble_scaffold.py`)
  validated `STAPEL_AUTH` config keys against the real
  `stapel-auth/docs/capabilities.json`, resolved via
  `_module_config._default_workspace_root()` as a sibling directory of this
  repo's own checkout. That sibling exists in the shared dev workspace but
  not in the publish-workflow's isolated single-repo checkout (`stapel-auth`
  is pip-installed there for importability, which does not recreate the
  sibling *directory* layout the validator looks for) — so
  `known_config_keys` silently fell back to its warn-and-pass-through path
  and the hard-error assertion never raised, failing the gate that blocked
  `v0.10.1`/`v0.10.2` publishing.
- Fixed the test design, not the check it exercises: `TestAuthSubfeatureAxes`
  now carries an autouse fixture that builds a tmp fixture mini-registry
  (`stapel-auth/docs/capabilities.json` with exactly the axes the class
  references) and monkeypatches `_default_workspace_root` to it, so
  validation is genuinely exercised — unknown axis still a hard error —
  without depending on any sibling checkout. Same pattern already used
  correctly by `test_create_project.py`'s `TestModuleConfigValidation`.
- Audited `tests/` for the same disease (absolute/`../` paths, sibling-repo
  reads outside `tests/fixtures/`); no other instance found — every other
  `stapel-*` string reference in the suite is either a `tests/fixtures/`
  file, a hardcoded registry-pin/rendered-content assertion, or already
  workspace-fixture/`pytest.skip`-guarded.
- Verified packaging: `url_lint`/`config_lint`/`config_manifest`/
  `assemble_scaffold` (and every other `stapel_tools/*.py` module) land in
  the built wheel — `stapel_tools` is a flat package with no subpackages, so
  `[tool.setuptools.packages.find]`'s package-level discovery isn't exposed
  to the explicit-subpackage-list-lags-behind class of bug that hit
  `stapel-core`'s `projections`.

## [0.10.1] - 2026-07-14

### Fixed — CI: `v0.10.0` tagged but never published (pre-existing gap)

- `v0.10.0`'s publish run failed: `test_minimal_with_auth_still_resolves_to_stapel_user`
  and two `assemble_scaffold` auth-axis tests assemble a project with the auth
  module and import `stapel_auth` in a subprocess — only importable on this
  dev workspace (every stapel-* module editable-installed as siblings), never
  in CI's isolated checkout, which installed `stapel-core` but not
  `stapel-auth`/`stapel-gdpr`. Confirmed pre-existing (same failure on
  `ci.yml` since before 2026-07-09). Added both to the `Tests` step's install
  line, same pattern already used for `stapel-core`.

## [0.10.0] - 2026-07-14

### Added — `stapel-url-lint`: bare Django `URLField()` gate (library-standard.md §3.8)

- New `stapel-url-lint [paths...]` CLI (`stapel_tools/url_lint.py`), in the
  `stapel-migration-lint` / `stapel-config-lint` idiom (rule codes, `--json`,
  `--strict`, exit 1 on any error).
- **URL001 (error)** — `models.URLField(...)` (Django ORM field, including a
  bare `URLField(...)` bound to `django.db.models` via `from
  django.db.models import URLField`) with no explicit `max_length` keyword.
  Django's implicit default is `varchar(200)`, which real external URLs
  (OAuth avatar, IdP SSO/OIDC discovery, webhooks) routinely exceed —
  degrading from a validation-time problem to a `StringDataRightTruncation`
  500 on INSERT (incident: OAuth signup crash on a Google avatar URL > 200
  chars; fixed in `stapel-core` 0.10.1 + `stapel-auth` 0.5.5). Suppress a
  deliberate exception with `# noqa: URL001`.
- `rest_framework.serializers.URLField` (and other DRF field classes) are
  excluded by design — a `CharField` with no implicit `max_length` and no
  backing DB column, so the truncation bug this rule guards against cannot
  occur there. Detection is import-alias based; a `URLField(` bound to an
  unrecognized qualifier defaults to flagged rather than silently passing.
- Migrations directories are skipped (the model source is the single place
  to fix; flagging the generated migration too would duplicate the finding).

### Added — `stapel-adoption-lint`: honesty gate for stapel-module adoption (BACKLOG §26/§30/§32, §35)

- New `stapel-adoption-lint <project_dir>` CLI (`stapel_tools/adoption_lint.py`),
  in the `stapel-migration-lint` idiom (rule codes, `--json`, `--strict`, exit 1
  on any error). It fails the ways a module gets "adopted" on paper but not in
  fact.
- **ADO001 (error)** — a stapel module is installed (in `requirements*.txt` or
  `INSTALLED_APPS`) and ships a urlconf, but its urls are not mounted in the
  project's ROOT_URLCONF (no `include("stapel_<mod>.urls")`); its endpoints
  don't exist. Deliberate headless use is declared with a file-level
  `# stapel: headless <mod>` marker (short or full package name) in the urlconf
  or a settings file. Library-only modules (no `urls`) are never flagged.
- **ADO002 (error)** — a project-owned urlpattern duplicates an installed
  module's operation: its route, normalized (so `<int:pk>` ≡ `{id}`), equals a
  path the module publishes in `docs/schema.json` (OpenAPI). The finding names
  the shadowed operation(s). Schemas are read next to the installed package
  (`importlib` spec — editable/dev installs and the neighbour-repo workspace
  layout) or a sibling `stapel-<mod>/docs/schema.json`; when none is discoverable
  the check is skipped for that module with a note (never a false error).
- **ADO003 (warning)** — a `STAPEL-MIGRATION.md` records *done* work but the
  current git branch is neither `main`/`master` nor merged into it (a finished
  migration lingering off `main`). Git-only, no network.
- **ADO004 (warning)** — a `requirements` pin is never imported anywhere in the
  project (dead pin; canonical case `PyJWT`, correctly resolved to its `jwt`
  import via `packages_distributions()` + a small alias table). stapel modules
  (referenced by dotted string), a small entry-point-only runtime/tooling
  allowlist (servers, DB drivers, test/lint tools), and packages configured by
  string in settings (INSTALLED_APPS/backends) are exempt.
- Deliberate parsing limits are documented in the module docstring: mounts are
  recognised only from literal `include("<pkg>.urls")` strings and inline-list
  includes (opaque/dynamic includes need the headless marker); custom routes are
  gathered from the ROOT_URLCONF file(s), not from app-level urlconfs reached via
  a string include; `re_path` regexes are normalised best-effort; ADO004 judges
  only dists whose import names resolve, and sees only the project's own tree (a
  dep used solely transitively by a module reads as a dead pin — don't pin your
  dependencies' dependencies). Covered by `tests/test_adoption_lint.py` (mounted
  / unmounted / headless-marker / inline-include; duplicate-route + param
  normalization + no-schema skip; dead pin + imported/stapel/settings-string/
  runtime-only/unresolvable exemptions; the git branch gate; CLI/JSON/exit codes).

### Added — `stapel-catalog`: module-catalog aggregator (BACKLOG §33 p.1)

- New `stapel-catalog` CLI (`stapel_tools/catalog.py`) that aggregates every
  module's `docs/capabilities.json` (the fourth contract artifact) into two
  catalog artifacts: `catalog.json` — the full machine aggregate (every source
  document verbatim + roll-up totals + curated recipes) — and `catalog.md` — a
  compact, prompt-ready projection (header roll-up, then per module: name,
  version, `provides` one-liner, an axis table `key | default | ops gated`,
  extension-point names, requires).
- Inputs are explicit module repo paths (or direct `capabilities.json` paths)
  and/or `--workspace <dir>`, which scans `stapel-*/docs/capabilities.json`.
  A source with no artifact, malformed JSON, or no `module` field is skipped
  with a warning — never a crash; a partial catalog still emits.
- Curated **recipes** (composite projections — a marketplace = N modules) are
  read from a separate `--recipes <file>` and rendered as their own catalog.md
  section. The minimal recipe schema (a restricted, dependency-free YAML
  subset — `recipes:` list of `{name, summary, modules, notes}`) is documented
  in the module docstring; a malformed recipes file is a loud error (curated
  input, not a discovered artifact).
- Both artifacts are deterministic (modules sorted by name, axes by key, no
  timestamps) so `catalog.md` is stable enough to commit into other repos'
  system prompts. Covered by `tests/test_catalog.py` (fixture capabilities of
  every shape — full / minimal / broken JSON / absent; byte-for-byte
  determinism across two runs).

## [0.10.0] — 2026-07-10

### Changed — generated-project layout aligned with the community canon (BACKLOG §29)

**Breaking for generated projects** (the scaffolders' output changes; the CLI
surface is unchanged). Regenerate, or rename by hand in existing projects.

- **Settings package `core/` → `config/`** across every preset (minimal,
  monolith, microservices). `ROOT_URLCONF`, `WSGI_APPLICATION`,
  `DJANGO_SETTINGS_MODULE`, the WSGI/ASGI modules, `Dockerfile`/compose
  `gunicorn`/`celery` targets, `manage.py`, `pytest.ini`/`pyproject`, and the
  isort `known-first-party` list all now point at `config`. Matches
  cookiecutter-django / HackSoft / Two Scoops, and drops the name collision
  with the `stapel-core` package. The monolith/microservices settings split
  keeps its existing file set (`base`/`dev`/`local`/`prod`) — only the package
  name moved.
- **User modules live under `apps/` uniformly, as a regular package.** Every
  scaffolded app — the starter module in a service AND anything added by
  `stapel-new-module` — is now `apps/<module>` with `apps/__init__.py` present,
  `INSTALLED_APPS = ["apps.<module>"]`, and `AppConfig.name = "apps.<module>"`
  (full dotted path, Django ticket #24801). Fixes the layout bug where a
  monolith's first module was created top-level while `stapel-new-module`
  placed later ones in `apps/` (incompatible paths in one fresh service), and
  where the minimal preset's `apps/` had no `__init__.py` (namespace-package
  edge case). `stapel-new-module` now also writes `apps/__init__.py`
  defensively. Follows the wemake-django-template pattern.
- The minimal preset keeps its single `config/settings.py` (deliberate — the
  falco / Adam Johnson camp for a small, no-Docker project); only larger
  presets get the settings split.

## [0.9.3] — 2026-07-10

### Fixed
- CI/publish gate: two more real test deps (psycopg for generated-project boot
  tests, pytest-django for the generated harness run) — verified against a
  clean venv this time, full suite green. 0.9.2 never reached PyPI.

## [0.9.2] — 2026-07-10

### Fixed
- CI/publish install stapel-core from git main: the generated templates depend
  on prodguard/SecretProvider not yet in a PyPI core release (templates
  themselves install core from git). 0.9.1 never reached PyPI (gate failure).

## [0.9.1] — 2026-07-10

### Fixed
- CI/publish workflows install the real test deps (django, DRF, stapel-core) —
  the 0.9.0 publish gate failed at test collection on a bare `pip install pytest`.

## [0.9.0] — 2026-07-09

### Added — release-management R-1: migration-lint + release.json manifest (release-management.md §1/§3/§8)
The OSS mechanism layer of release management (platform models/UI are R-2,
private):
- **`stapel-migration-lint`** (`stapel_tools/migration_lint.py`) — AST-based
  expand/contract gate over Django migration files (no Django boot needed, so
  it runs on customer checkouts at cut time AND on stapel-* module repos in
  CI). Rules: MIG001 destructive op (RemoveField/DeleteModel/Rename*/narrowing
  AlterField) requires `# stapel: contract-phase`; MIG002 `--base-sha`
  verifies the previous release no longer references the destroyed target
  (grep-level via `git show`, new-since-base migrations only); MIG003
  RunPython/RunSQL without reverse requires `# stapel: irreversible` (lowers
  the app's reversible_floor); MIG004 NOT NULL AddField without
  default/db_default on an existing model (breaks N-1); MIG101/MIG102
  warnings. `--json`, `--strict`, exit 1 on errors.
- **`stapel-release-manifest`** (`stapel_tools/release.py`) — builds the open
  `release.json` (schema_version 1): release r\<N\>, git_sha (verified against
  HEAD), images, per-app migration watermarks (max migration FILE at the sha —
  describes the artifact, not a DB), reversible_floor (shared analyzer, latest
  irreversible migration or "zero"), contracts (stapel-* pins: vendored
  checkout pyproject > ==pin > git tag > spec verbatim), config_digest
  (sha256 over `STAPEL_<MOD>` settings blocks), gates (migration_lint
  computed, prodguard/handover_scan recorded via `--gate`), created_at
  (SOURCE_DATE_EPOCH-aware). Byte-deterministic output (sorted keys) — the
  codegen drift-gate discipline.
- **Minimal scaffold Makefile** grows `migration-lint` and `release-manifest`
  targets (the seam the R-2 bake step calls); generated `.gitignore` excludes
  `release.json`. Container bake itself is R-2, deliberately not built.
- Tests: 65 new (every rule incl. a throwaway-git-repo base-sha fixture, floor
  computation, manifest determinism, scaffolded-minimal end-to-end).

### Added — contract artifact freshness gate in release-manifest (process-gap §26)
Caught in production: the stapel-calendar 0.2.3 and stapel-recordings 0.1.3
release bumps raised `version` in `pyproject.toml` but did not regenerate
`docs/capabilities.json` — the tag went out with a stale version baked into
the artifact and the contract tests red. `stapel_tools.release` now catches
this BEFORE the tag:
- **`check_contract_freshness()`** — compares each `docs/*.json` contract
  artifact's own embedded TOP-LEVEL `version` against the repo's
  `pyproject.toml`. REL001 (error): an artifact's version is behind
  pyproject — `build_manifest`/the CLI aborts, no manifest is emitted.
  REL002 (warning): `docs/capabilities.json` is missing while the repo has a
  `make contract` Makefile target — printed, non-fatal. Only
  `capabilities.json` is ever actually flagged by REL001: `schema.json`'s
  OpenAPI version lives nested under `info` (a drf-spectacular placeholder,
  never wired to the module version) and `flows.json`/`errors.json` are bare
  lists with no envelope — looking only at the top level correctly skips
  both without special-casing filenames.
- Unlike `gates.migration_lint` (recorded, not fatal — the pipeline is the
  actual gate), this check is fatal to the manifest build itself.
- Tests: 9 new (clean match, stale capabilities.json, schema.json's nested
  placeholder never checked, missing artifact with/without a contract
  target, no pyproject.toml, `build_manifest` abort/pass-through).

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
