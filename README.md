# stapel-tools

[![CI](https://github.com/usestapel/stapel-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/usestapel/stapel-tools/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/stapel-tools.svg)](https://pypi.org/project/stapel-tools/)

CLI scaffold and linting tools for Stapel/Django projects.

## Install

```bash
pip install git+https://github.com/usestapel/stapel-tools.git
```

Or as a dev dependency in your project:

```bash
pip install -e path/to/stapel-tools
```

## Commands

### `stapel-create-project` — interactive project wizard

```bash
stapel-create-project                          # full wizard
stapel-create-project my-app --type monolith   # skip some wizard steps
stapel-create-project my-app \
    --type monolith \
    --title "My App" \
    --url https://myapp.com \
    --company-name "ACME" \
    --company-email hello@myapp.com \
    --modules auth billing              # fully non-interactive
```

Project types: `monolith` (recommended), `microservices`, `minimal` (no Docker, SQLite).

### `stapel-new-service` — add a service to an existing project

```bash
stapel-new-service auth
stapel-new-service auth --title "Auth Service" --prefix iron-
stapel-new-service blog --celery
stapel-new-service blog --dry-run
```

### `stapel-new-library` — scaffold a standalone stapel-* package repo

For contributing a new reusable package to the framework (or building your
own to the same standard). Materializes the Stapel library standard: flat
layout, `STAPEL_<NAME>` settings namespace, comm surface with JSON schemas,
serializer seams, MODULE.md, community files, CI with the codecov
ratchet/floor policy, ruff git hooks. The generated repo's own test suite
is green out of the box.

```bash
stapel-new-library search                                 # L2 service module
stapel-new-library attributes --kind library              # L1 importable lib
stapel-new-library support-chat --title "Support chat" --dir ~/Projects
```

Kinds: `module` (default — Django app with models/views/comm surface;
modules never import each other) and `library` (importable package without
service identity, like stapel-attributes).

### `stapel-new-react-lib` — scaffold a headless `@stapel/<module>-react` pair

The frontend counterpart to `stapel-new-library`: materializes a headless
React/TS pair from the auth-react etalon (frontend-standard §9,
frontend-core-architecture §4 checklist) into a stapel-react monorepo
(`packages/<module>-react`). Emits the layer stack `api → model → flows →
headless → i18n`, namespaced query keys, the `create<Module>Runtime` /
`<Module>Provider` wiring, a module-scoped i18n bundle + errors map, a vitest
smoke suite, and full package hygiene (ESM, `sideEffects:false`,
`isolatedDeclarations`, src-in-tarball, size-limit, `manifest`/`llms.txt`
exports). The `createFlowMachine` primitive is imported from `@stapel/core`,
never copied.

```bash
stapel-new-react-lib notifications                        # → @stapel/notifications-react
stapel-new-react-lib billing --title "Billing"            # backend defaults to stapel-billing
stapel-new-react-lib profiles --react-dir ~/Projects/stapel/stapel-react
```

Fork-free: the generated `package.json` wires the monorepo's env-parametrized
codegen drivers (`scripts/gen-{flows,errors,manifest}.mjs`) via env knobs
rather than duplicating them. Each pair owns three per-package drift gates
(`gen:{flows,errors,manifest}:check`); `gen:api` is core-owned. After
scaffolding: `pnpm install && pnpm --filter @stapel/<module>-react gen build
lint test`.

### `stapel-new-module` — add a Django app to a service

```bash
cd svc-auth/
stapel-new-module users
stapel-new-module billing --title "Billing Plans"
```

### `stapel-remove-service` — remove a service

```bash
stapel-remove-service auth
stapel-remove-service auth --prefix iron- --yes
stapel-remove-service auth --dry-run
```

### `stapel-codegen` — emit the frontend codegen source artifacts

Runs *inside* a configured all-modules Django instance (e.g.
stapel-example-monolith on sqlite) and emits the three language-agnostic backend
artifacts the frontend TS client is generated from (docs/done/flow-system-v1.md §0.1):

- `schema.json` — the unified drf-spectacular OpenAPI for every installed
  module (same document the instance serves at `/schema/`, produced offline via
  the `spectacular` management command — no server, byte-stable).
- `flows.json` — the `generate_flow_docs` machine artifact.
- `errors.json` — the `generate_error_keys` machine artifact: every
  `error.<status>.<name>` key the instance can raise, with its HTTP status,
  `{param}` slots, remediation hint and canonical English text
  (stapel-core's `stapel_core/django/api/errors.py`).

All three use a byte-stable JSON encoding, so regenerating without a code
change yields zero diff — the invariant a drift gate rests on.

```bash
DJANGO_ENV=local DJANGO_SETTINGS_MODULE=config.settings.codegen \
    python -m stapel_tools.codegen --out codegen/generated
```

In stapel-example-monolith this is wrapped as `make codegen` (regenerate) and
`make codegen-check` (drift gate). The generated `schema.json` then feeds
`stapel-react`'s `pnpm gen:api` (openapi-typescript → typed `@stapel/core` API).

### `stapel-analytics-report` — typed-analytics summary report

Generates the analytics/flow report (frontend-guardrails §3.3) across a pnpm
workspace of `@stapel/*-react` pairs and/or a customer app, from static
generated artifacts (`events.json`/`manifest.events`, backend `flows.json`,
`manifest.machines`) plus a syntactic scan of TS/TSX call sites
(`tracked()`/`trackedSubmit()`/`track()`, `data-analytics="flow"/"none"`
markers, `eslint-disable … -- description`). Two slices are always separated:
**app** (customer code) and **library** (`@stapel/*` pairs).

```bash
# machine-readable report.json to stdout
stapel-analytics-report path/to/stapel-react

# report.json + report.md + report.html into a dir, with canonical backend prose
stapel-analytics-report path/to/stapel-react \
    --backend-flows path/to/monolith/codegen/generated/flows.json \
    --out ./analytics-report

stapel-analytics-report ./my-app --package packages/web --format md
```

Outputs `report.json` (for the Studio project passport), `report.md`, and a
self-contained `report.html`. Per event: description, typed props, emit sites
(`file:line` + component), linked flow. The flow report joins backend flows with
frontend coverage and renders a `[gated: <ENV>]` badge (from `gated_by`, task
G6 — absent means always-on). `--capabilities` is reserved (§3.4 env-aware).

### `stapel-catalog` — module-catalog aggregator

Aggregates every module's `docs/capabilities.json` (the fourth contract
artifact) into a catalog for stack advisors and CTO prompts. Emits
`catalog.json` (the full machine aggregate — every source document verbatim
plus roll-up totals and curated recipes) and `catalog.md` (a compact,
prompt-ready projection: header roll-up, then per module a `provides`
one-liner, an axis table `key | default | ops gated`, extension-point names and
requires). Both outputs are deterministic (modules sorted by name, axes by key,
no timestamps), so `catalog.md` is stable enough to commit into other repos'
system prompts.

```bash
# scan a workspace (repos without capabilities.json are skipped with a warning)
stapel-catalog --workspace ~/Projects/stapel --out-dir ./catalog

# explicit module repos (or direct capabilities.json paths)
stapel-catalog ../stapel-auth ../stapel-billing --out-dir ./catalog

# add curated composite recipes (marketplace = N modules) as their own section
stapel-catalog --workspace ~/Projects/stapel --recipes recipes.yaml --out-dir ./catalog
```

Recipes are curated, not derived — a minimal, dependency-free YAML subset:

```yaml
recipes:
  - name: marketplace
    summary: Two-sided marketplace — accounts, profiles and listings.
    modules: [stapel-auth, stapel-profiles, stapel-listings]
    notes: reviews live in a separate target-generic module
```

### `stapel-lint` — project-specific static linter

```bash
stapel-lint                        # scan current directory
stapel-lint svc-auth/              # scan specific service
stapel-lint --stats                # show per-rule counts
stapel-lint --ignore R002          # skip a rule
```

Rules: R001 bare `Response()`, R002 `serializers.ValidationError`, R003 undocumented `@action`,
R004 `@dataclass` without docstring, R005 hardcoded error string, R006 `StapelResponse(dict)`.

Suppress per-line: `# noqa: R001`

### `stapel-migration-lint` — expand/contract gate for Django migrations

```bash
stapel-migration-lint                        # lint every app under .
stapel-migration-lint svc-app/ --json        # machine output (+watermarks/floors)
stapel-migration-lint . --base-sha <sha>     # verify against the previous release
stapel-migration-lint . --strict             # warnings become errors
```

Static (AST) analysis — no Django settings needed, so it runs on customer
project checkouts at release cut AND on `stapel-*` module repos in CI.
Rules: MIG001 destructive op (`RemoveField`/`DeleteModel`/`Rename*`/narrowing
`AlterField`) requires the `# stapel: contract-phase` file marker (destructive
changes ship one release after the code stopped using the target); MIG002
with `--base-sha` the destroyed target must not be referenced by the app's
code at the previous release's sha; MIG003 `RunPython`/`RunSQL` without a
reverse requires `# stapel: irreversible` (lowers the app's
`reversible_floor` in release.json); MIG004 NOT NULL `AddField` without
`default`/`db_default` on an existing model (breaks N-1 rollback).

### `stapel-adoption-lint` — honesty gate for stapel-module adoption

```bash
stapel-adoption-lint .                       # lint the project in .
stapel-adoption-lint proj/ --json            # machine output
stapel-adoption-lint proj/ --strict          # warnings become errors
stapel-adoption-lint proj/ --workspace ~/ws  # extra root for sibling module repos
```

Catches modules "adopted" on paper but not in fact (a dependency without a
mount, a route re-implemented over one the module ships, a done migration off
`main`). ADO001 (error) a module installed (`requirements`/`INSTALLED_APPS`)
and shipping a urlconf but not mounted in ROOT_URLCONF — declare intentional
headless use with `# stapel: headless <mod>`; library-only modules are exempt.
ADO002 (error) a project-owned urlpattern whose route (params normalized, so
`<int:pk>` ≡ `{id}`) duplicates a path in an installed module's
`docs/schema.json`. ADO003 (warning) `STAPEL-MIGRATION.md` records done work
but the branch is not `main`/`master` nor merged into it. ADO004 (warning) a
`requirements` pin never imported anywhere (dead pin, e.g. `PyJWT`→`jwt`);
stapel modules, settings-configured packages, and an entry-point-only
runtime/tooling allowlist are exempt.

### `stapel-release-manifest` — build the open `release.json` manifest

```bash
stapel-release-manifest . --release r4 --git-sha $(git rev-parse HEAD) \
    --image app=registry/tenant/proj/app:r4 --out release.json
```

Describes one gated build (release-management.md §1): per-app migration
watermarks (max migration *file* at the sha — the artifact, not a DB),
`reversible_floor` per app, `contracts` (stapel-* version pins),
`config_digest` over the `STAPEL_<MOD>` settings blocks, and gate results
(`migration_lint` computed via the shared analyzer; `prodguard`/
`handover_scan` recorded from `--gate name=pass|fail`). Output is
byte-deterministic (sorted keys; `--created-at`/`SOURCE_DATE_EPOCH`).
The platform bake step calls this during image build and bakes the file
into the image at `/app/release.json`.

## Project layout

Generated projects follow the mainstream Django community canon so the shape is
familiar to anyone who has used the popular templates.

- **`config/` — the settings/URLs/WSGI package.** Not `core/`. This is the
  convention of [cookiecutter-django](https://github.com/cookiecutter/cookiecutter-django),
  the [HackSoft styleguide](https://github.com/HackSoftware/Django-Styleguide),
  and *Two Scoops of Django* (`ROOT_URLCONF = "config.urls"`). It also avoids
  colliding with the `stapel-core` package name. The monolith and microservices
  presets split it into `config/settings/{base,dev,local,prod}.py`; **minimal**
  keeps a single `config/settings.py` — a deliberate choice for a small,
  no-Docker project (the [falco](https://github.com/falcopackages/falco) / Adam
  Johnson "one settings file until you need more" camp).

- **`apps/` — a regular package holding your Django apps.** Every module lives at
  `apps/<module>` with an `apps/__init__.py`, is listed as
  `INSTALLED_APPS = ["apps.<module>"]`, and sets `AppConfig.name = "apps.<module>"`
  (the full dotted path — see [Django ticket #24801](https://code.djangoproject.com/ticket/24801)).
  This is the [wemake-django-template](https://github.com/wemake-services/wemake-django-template)
  pattern, and it is uniform: the starter module and everything added later by
  `stapel-new-module` share the same import path.

```
myapp/                      # monolith / microservices service (svc-myapp/)
├── config/
│   ├── settings/{base,dev,local,prod}.py
│   ├── urls.py  wsgi.py  asgi.py
├── apps/
│   ├── __init__.py         # regular package (required)
│   └── myapp/              # apps.myapp — INSTALLED_APPS + AppConfig.name
├── tests/                  # outbox/mailtrap integration harness
└── manage.py

myapp/                      # minimal preset (no Docker, SQLite)
├── config/
│   ├── settings.py         # single file (deliberate)
│   ├── urls.py  wsgi.py
├── apps/__init__.py  apps/myapp/
└── manage.py
```

## Available modules

| Module | Description |
|--------|-------------|
| `core` | Core framework (always included) |
| `auth` | Authentication — JWT, OAuth, OTP |
| `billing` | Billing & subscriptions |
| `cdn` | File uploads & CDN |
| `notifications` | Email / push notifications |
| `profiles` | User profiles |
| `translate` | Translations & i18n |
| `workspaces` | Workspaces & multi-tenancy |
| `gdpr` | GDPR — data export & deletion |
