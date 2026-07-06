# stapel-tools

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
stapel-example-monolith on sqlite) and emits the two language-agnostic backend
artifacts the frontend TS client is generated from (docs/flow-system.md §0.1):

- `schema.json` — the unified drf-spectacular OpenAPI for every installed
  module (same document the instance serves at `/schema/`, produced offline via
  the `spectacular` management command — no server, byte-stable).
- `flows.json` — the `generate_flow_docs` machine artifact.

Both use a byte-stable JSON encoding, so regenerating without a code change
yields zero diff — the invariant a drift gate rests on.

```bash
DJANGO_ENV=local DJANGO_SETTINGS_MODULE=core.settings.codegen \
    python -m stapel_tools.codegen --out codegen/generated
```

In stapel-example-monolith this is wrapped as `make codegen` (regenerate) and
`make codegen-check` (drift gate). The generated `schema.json` then feeds
`stapel-react`'s `pnpm gen:api` (openapi-typescript → typed `@stapel/core` API).

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
