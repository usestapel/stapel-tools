# stapel-tools

[![CI](https://img.shields.io/github/actions/workflow/status/usestapel/stapel-tools/ci.yml?branch=main&logo=github&label=CI)](https://github.com/usestapel/stapel-tools/actions/workflows/ci.yml?query=branch%3Amain)
[![coverage](https://img.shields.io/codecov/c/github/usestapel/stapel-tools?branch=main&logo=codecov&label=coverage)](https://app.codecov.io/gh/usestapel/stapel-tools)
[![pypi](https://img.shields.io/pypi/v/stapel-tools?logo=pypi&logoColor=white&label=pypi)](https://pypi.org/project/stapel-tools/)
[![downloads](https://static.pepy.tech/badge/stapel-tools/month)](https://pepy.tech/project/stapel-tools)
[![python](https://img.shields.io/pypi/pyversions/stapel-tools?logo=python&logoColor=white)](https://pypi.org/project/stapel-tools/)
[![license](https://img.shields.io/github/license/usestapel/stapel-tools)](https://github.com/usestapel/stapel-tools/blob/main/LICENSE)

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
plus roll-up totals and curated recipes), `catalog.md` (a compact,
prompt-ready projection: header roll-up, then per module a `provides`
one-liner, an axis table `key | default | ops gated`, extension-point names and
requires) and `llms.txt` (the fleet's own **root index**, badge-canon §3 p.5:
one line per module — its `provides` one-liner and a link to that module's
own `docs/llms.txt`, the fifth contract artifact from `stapel-llms-txt`
below). `llms.txt` is what an agent that does not yet know which module it
needs should read FIRST — one small file instead of catalog.md in full or
every modular `docs/llms.txt`. A module counts as "described" only once its
own `docs/llms.txt` actually exists on disk (or in the wheel, under
`--from-installed`) — a module without one yet is listed by name under "Not
yet described", loudly, never silently dropped. All three outputs are
deterministic (modules sorted by name, axes by key, no timestamps), so they
are stable enough to commit into other repos' system prompts.

```bash
# scan a workspace (repos without capabilities.json are skipped with a warning)
stapel-catalog --workspace ~/Projects/stapel --out-dir ./catalog

# explicit module repos (or direct capabilities.json paths)
stapel-catalog ../stapel-auth ../stapel-billing --out-dir ./catalog

# add curated composite recipes (marketplace = N modules) as their own section
stapel-catalog --workspace ~/Projects/stapel --recipes recipes.yaml --out-dir ./catalog

# the environment IS the source: every installed stapel-* wheel, no checkout
stapel-catalog --from-installed --index -o ./stack_index.json
```

#### Freshness is a property, not a chore

Two rules keep a catalog from becoming a lie:

1. **`--from-installed`** sources the aggregate from the *current
   environment* — every installed `stapel-*` distribution that ships
   `docs/capabilities.json` in its wheel (modules ship `capabilities.json`,
   `flows.json`, `errors.json`, `CONFIG.MD` and — once a module adopts
   `stapel-llms-txt` — `llms.txt` as package-data, so an installed-sourced
   index is not a degraded one; the root `llms.txt`'s "described" count comes
   from the same wheel-shipped files). The result is a pure function of the
   lockfile: it cannot drift away from the code the product actually runs, no
   matter whose discipline lapses.
2. **`--check`** is the drift gate for a *committed* catalog: it rebuilds in
   memory and compares byte-for-byte against the artifact on disk, exiting
   non-zero on any mismatch. A committed aggregate without this gate in CI is
   a snapshot that will go stale silently — that is not a hypothesis, it is
   how both of this project's aggregates rotted. Wire it next to
   `make contract-check`.

`stapel-tools` deliberately commits **no** catalog of its own: the artifact
belongs in the repo that consumes it, behind that repo's `--check` gate.

Recipes are curated, not derived — a minimal, dependency-free YAML subset:

```yaml
recipes:
  - name: marketplace
    summary: Two-sided marketplace — accounts, profiles and listings.
    modules: [stapel-auth, stapel-profiles, stapel-listings]
    notes: reviews live in a separate target-generic module
```

### `stapel-docs` — bilingual API/flow documentation

Renders `docs/api.en.md` + `docs/api.ru.md` at a generated project's root
from artifacts it already ships: `schema.json` (endpoints, DTO fields —
descriptions sourced straight from backend docstrings, R004 canon),
`flows.json` (flow/step user stories) and `errors.json` (every
`error.<status>.<name>` code). Where a module has already shipped a
Russian translation (`translations/flows.ru.json` /
`translations/errors.ru.json` — the stapel-translate precedent), the
Russian doc uses it verbatim; where it hasn't (yet), the English text is
shown with an honest `(en)` marker instead of a fabricated translation.
Supports three schema layouts: a monolith's `codegen/generated/schema.json`
aggregate (grouped back into per-module sections by path prefix), a
per-service/vendored-lib `<service>/docs/schema.json`, or a literal
`<mod>/api/v1/schema.json`.

```bash
stapel-docs .              # write docs/api.en.md + docs/api.ru.md
stapel-docs . --check      # drift gate (pre-commit's api-docs-check)
```

A project with no `schema.json` generated yet is a graceful no-op, exit 0.

### `stapel-gen-client` — project-owned typed API-client override

Tier 2 of the answer to "if we override a module's backend, its frontend
pair must handle that" (`docs/pending/profile-fields.md` "Owner
addendum"): regenerates a typed TS client from the PROJECT'S OWN
`schema.json` (not the library's upstream one) into
`frontend/src/api/generated-override/<module>/schema.ts` — reusing
openapi-typescript (the same generation core stapel-react's own
`scripts/gen-api.mjs` uses) via `npx`, not a reimplementation. A pair's api
layer can point at this file instead of its own bundled types once a host
has overridden that module.

Only does anything once the project actually HAS an override — a
non-empty `STAPEL_SWAP = {...}` anywhere in the project, or an explicit
`stapel.override.json` with `"clientOverride": true`:

```bash
stapel-gen-client .              # no-op without an override signal
stapel-gen-client . --check      # drift gate (pre-commit's gen-client-check)
stapel-gen-client . --force      # generate even without a detected override
```

### `stapel-verify` + `stapel-lint.toml` — the arsenal, and the switch a legacy project needs

```bash
stapel-verify .                    # every composed linter, human table
stapel-verify . --json             # machine output (profile + per-linter)
stapel-verify . --run-native       # also execute the profile's native gates
```

Every linter `stapel-verify` composes encodes a **stapel** contract, which is
a fair gate only against a project stapel generated. An imported legacy tree
trips hundreds of them on its first commit, none describing a defect — and a
permanently red gate is a gate that is off, minus the record.

`stapel-lint.toml` at the project root is that record. One **mode per
surface** (`python`, `frontend`, `docs`, `i18n`, `deploy`):

| mode | meaning |
|---|---|
| `stapel` | run the stapel arsenal for the surface (the default — an absent file means this everywhere, so a generated project is unaffected) |
| `native` | the project's **own** linter is the gate; `command` is mandatory |
| `off` | the surface is not gated; `reason` is mandatory |

```toml
# stapel-lint.toml
[surface.python]
mode = "native"
command = "ruff check ."

[surface.frontend]
mode = "native"
command = "npm run lint"

[surface.docs]
mode = "off"
reason = "reference docs live in Confluence, not the repo"

[waivers]
SWAP002 = "presenters are the app's own; see ADR-7"
```

Three rules keep it from becoming a silent kill switch, all borrowed from
`stapel_core.django.check_guard`'s waiver canon:

* `off` without a `reason`, `native` without a `command`, or a `[waivers]`
  entry with an empty reason is an **error in the profile** — the gate stops
  and names the line, it never degrades into "run nothing";
* every non-`stapel` surface still emits a report line carrying its mode and
  its reason, in the table and in `--json`, so what was *not* checked is
  visible next to what was — including a waiver that matched nothing;
* a `native` command is a shell command out of the tree under inspection, so
  it does not run without `--run-native`. Studio's sandbox passes it (it
  already runs the project's own `make controls` there); a bare local run of
  an untrusted checkout does not.

A native gate's **exit code is the verdict** and its output tail is the
evidence — stapel deliberately does not parse another tool's format, and the
coder loop never needed it to: it already reads `make controls` tails the
same way.

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
`AlterField`) requires a phase marker on the file (see below); MIG002 with
`--base-sha` the destroyed target must not be referenced by the app's code at
the previous release's sha; MIG003 `RunPython`/`RunSQL` without a reverse
requires `# stapel: irreversible` (lowers the app's `reversible_floor` in
release.json); MIG004 NOT NULL `AddField` without `default`/`db_default` on an
existing model (breaks N-1 rollback); MIG005 both phase markers on one file
(contradictory claims — neither licenses the destructive op).

**Two phase markers, two different claims** — pick one, never both:

| marker | claim | machine-checked |
|---|---|---|
| `# stapel: contract-phase` | the code stopped using the target one release AGO (expand rN → contract rN+1); the target is already dead code when this migration runs | nothing beyond the marker — pure assertion |
| `# stapel: cutover-phase` | deletion-driven cutover: THIS migration carries the data out and then removes the target, in one release | a data-carrying `RunPython` (forward code is not `RunPython.noop`) must appear **before** the destructive op in the same `operations` list |

`contract-phase` is the default shape and the only one safe under a rolling
deploy. `cutover-phase` is safe only where a deployment never runs old and
new code against the same schema at once — a stop-the-world deploy
(`docker compose up -d`, what this fleet does); under rolling/blue-green the
old process would keep writing to a table already drained and dropped. It is
not a synonym for `contract-phase` and not a general licence to destroy: a
`cutover-phase` file whose destructive op has no data path before it is still
MIG001. What stays the author's assertion: that the `RunPython` carries THIS
target's rows (the callable's body is out of AST reach) and that the deploy
is stop-the-world. `RunSQL` does not count as the data path — from the AST a
copying `INSERT…SELECT` and a destructive DDL string look the same.

### `stapel-api-lint` — HTTP surface versioning gate (§60)

```bash
stapel-api-lint .                            # diff against the newest reachable v<semver> tag
stapel-api-lint . --base-ref v0.6.2          # diff against a specific release
stapel-api-lint . --json                     # machine output
stapel-api-lint . --strict                   # warnings (SCHEMA001) become errors
```

Diffs the committed `docs/schema.json` at a baseline git ref against the one
in the working tree and classifies the diff as additive or breaking, per
`docs/pending/api-versioning.md` §3. The contract pipeline's drift gate
already tells you the schema *changed*; nothing until now decided whether the
change breaks a caller — the author did, from memory, at release time.

Canon: `/<mod>/api/v1/…`, the version segment right after `api/`, one counter
per module. **Breaking** = an endpoint removed/renamed; a field removed,
renamed or retyped; a required-status flip in the direction that hurts
(optional→required in a REQUEST, required→optional in a RESPONSE — the
opposite flip on each side is a strengthening and stays additive); a response
status code disappearing; an operation's auth contract changing; an enum value
removed (always), or added to an enum marked `x-stapel-closed-enum`. Enums are
OPEN by default: adding a value is additive and clients must ignore what they
do not know.

| rule | level | what it holds |
|---|---|---|
| API001 | error | a breaking diff must be carried by BOTH a sufficient version bump (pre-1.0 minor, post-1.0 major — library-standard §1.4) AND a `docs/UPGRADE.json` record of `kind: "api_change"` for the new version |
| API002 | error | a breaking change may not land in place: `vN+1` must appear beside the frozen `vN`, and `urls.py` must still mount `urls_vN.py` — a version in the schema but not in the URLconf is documented, not served |
| API003 | error | a `vN` present at the baseline may not disappear before its `x-stapel-sunset` date (or with no sunset ever declared — a window that never opened cannot have closed) |
| SCHEMA001 | warning | `docs/schema.json`'s `info` block must follow the fleet convention (below): the drf defaults `info.version: "0.0.0"` + empty `info.title` are clean; a lib that writes its package version there, or any other `info` shape, is flagged |

**The `info.version` convention.** `info.version` is *not* the contract's
version. Every lib leaves `SPECTACULAR_SETTINGS` unset in its
`_codegen_settings.py` so its emitted triad stays byte-identical to the
monolith aggregate's slice — and the aggregate runs on drf defaults, which
emit `info.version: "0.0.0"` with an empty `info.title`. That pair is the
correct state and SCHEMA001 is silent on it. The version a consumer pins
lives in `pyproject.toml` and in the pair's `manifest.json`
(`backend.contract`). SCHEMA001 therefore fires on *divergence from* the
convention — most usefully when a lib passes `package=`/`version=` to
`get_spectacular_settings` and quietly stops matching the aggregate.

No baseline (no git repo, no `v<semver>` tag, or no schema at that ref) means
API001-003 do not run, and the linter says so in a note rather than inventing
a "before". A repo with no `docs/schema.json` has no HTTP contract to check
and is silent. Composed into `stapel-verify`, which forwards its `--base-sha`
as the baseline ref.

### `stapel-authz-lint` — the "credentials verified, authorization never asked" gate

```bash
stapel-authz-lint .                          # lint the project in .
stapel-authz-lint . --json                   # machine output
stapel-authz-lint . --strict                 # AUTHZ003 (warning) becomes an error
```

On 2026-08-24 stapel-core shipped five security releases (0.38.0-0.43.0) for
one defect class wearing five costumes: every place where the code proved
*who you are* and then never asked *what you may have*. `JWTCookieLoginView`
is the admin login view, but named no `authentication_form`, so Django used
the plain `AuthenticationForm` — `is_active` and nothing else — and
`form_valid()` then called `login()`, `create_tokens()` and
`set_jwt_cookies()` with no staff check on that path. Any active account's own
password minted a fleet-wide JWT pair. The file *did* read `is_staff` three
times; all three sat in `dispatch()`'s already-authenticated branch, so every
file-level grep called it safe.

Every one of the five was found by a human reading code. None was found by a
test, because each component's suite was green: the login view did log people
in, the refresh endpoint did return a token, the blacklist did blacklist. The
defect lived in what was **not** written, and nothing in the fleet read for
absence.

| rule | level | what it holds |
|---|---|---|
| AUTHZ001 | error | a `LoginView` subclass defining `form_valid` must either name an `Admin*` authentication form (class attribute or `get_form_class`) or read authorization (`is_staff`/`is_superuser`/a permission call) inside `form_valid`'s own body — a gate in a sibling method is not on the minting path |
| AUTHZ002 | error | `create_tokens`/`set_jwt_cookies` in a function that also calls `login()` or `form.get_user()` needs an authorization read **earlier in the same function**. A token outlives the request that minted it, so a check after the mint is a log line |
| AUTHZ003 | warning | an explicit `refresh_access_token(x, None)` re-mints from the presented token's own claims (up to `JWT_REFRESH_TOKEN_LIFETIME`, 7 days), resurrecting revoked flags. Since core 0.39.0 the django-layer default is the database loader, so the bare call is the safe form and only the typed-out `None` is flagged — it must be a decision, not an omission |
| AUTHZ004 | error | a `get_user()` **method** override returning `objects.get()` needs an `is_active`/`user_can_authenticate` check. `get_user` resolves `request.user` on every request after the one that authenticated, so dropping it lets a deactivated account keep a live session for the life of the session cookie |
| AUTHZ005 | error | a revocation/blacklist entry read or written through `django.core.cache.cache`. Django builds the real key from *this deployment's* `KEY_PREFIX`, so `auth` wrote `auth:1:jwt_blacklist:<jti>` while `profiles` read `stapel_profiles:1:jwt_blacklist:<jti>` — use `stapel_core.core.revocation_store.revocation_cache()` |
| AUTHZ006 | error | a view or `@action` reachable **without authentication** (`AllowAny`, an empty `permission_classes`, `IsAuthenticatedOrReadOnly` on a read) that reads a **filter-bypassing manager** (`all_objects`, `_base_manager`, `with_deleted()`, `all_with_deleted()`, `only_deleted()`) and answers with **identity or moderation state** (`owner`/`owner_id`/`user_id`/`created_by`, `moderation_*`, or the `status` of a row the default filter hides). Each half is ordinary; together they are an enumeration oracle |

Suppress with `# noqa: AUTHZ00N` on the reported line; a written reason after
the code on the same line is read fine, and is the point.

**AUTHZ006 — the second defect this family was paid for.** On 2026-08-28
`stapel-listings` shipped 0.8.0 for a hole none of AUTHZ001-005 could see,
because nothing on the path minted a credential or overrode `get_user`:

```python
@action(detail=True, methods=["get"], permission_classes=[AllowAny])
def status(self, request, pk=None):
    listing = Listing.all_objects.get(pk=pk)                  # the UNFILTERED manager
    return StapelResponse(ListingStatusSerializer(listing))   # owner_id, moderation_status, …
```

Live on a production stand, unauthenticated, over sequential ids:

```
GET /listings/api/v1/listings/10/status/
→ 200 {"status":"draft","moderation_status":"pending","is_deleted":true,
       "owner_id":"720a67e2-…"}
```

An anonymous walk of the ids harvested the owner's user UUID and the moderation
verdict for every listing in the deployment — other people's drafts, rejected
and soft-deleted rows included.

The rule keys on **the missing decision**, not on the permission and not on the
manager, because the shipped fix changed neither: 0.8.0 is still `AllowAny`,
still `all_objects`, still the same serializer in the schema. Deleting the
endpoint or locking it to services was the *wrong* fix and was tried first — a
real browser client (`@stapel/listings-react`'s `ListingDetail`) needs the
probe to tell "removed" from "never existed", since `GET /{pk}/` 404s
identically for a deleted row and a made-up id. **Keep the capability, remove
the disclosure:** full view for a fleet service and for the row's own owner,
one boolean for everyone else — a signed-in stranger included, or the oracle
costs an attacker one free account. A branch on ownership or permission in the
handler (or a named predicate in the same module) is what silences the rule,
because that branch *is* the fix.

**What these rules cannot catch, stated plainly** — a rule that pretends to
cover more than it does is worse than one whose edge is written down:

- Recognition is syntactic. There is no import resolution, no cross-module
  call graph, no dataflow. An authorization helper **imported from another
  module** reads as absence; only same-module predicates are followed (one
  fixed-point hop, so `form_valid` → `has_admin_access` → `is_staff` passes).
- AUTHZ001 keys on a base class whose name ends in `LoginView`. A credential
  view built on `APIView`, `View` or a bespoke base is invisible to it —
  AUTHZ002 is the net underneath, and it only fires where `login()` or
  `form.get_user()` appears.
- AUTHZ002 does not fire on a mint with no credential call in the same
  function: a DRF flow that mints for an account some service already verified
  (password reset, OTP promotion — ~15 such call sites in stapel-auth) is the
  intended grant, not this class. The cost of that precision is that a genuine
  bypass **split across two functions** is not seen.
- "Before the mint" is source order, not execution order. A read inside a
  branch that never runs, or a helper whose refusal is a logged warning rather
  than a return, satisfies the rule.
- AUTHZ003 sees a literal `None`, never a value. `loader or None` and a
  variable that happens to be `None` at runtime both pass.
- AUTHZ004 covers `objects.get()`. `objects.filter(...).first()`, a manager
  method, a cache lookup or a raw query in a `get_user` override are not
  matched.
- AUTHZ005 decides "is this about revocation?" from the enclosing function
  name, the enclosing class (name and class-level constants), and identifiers
  and string literals **inside the call itself**. Deliberately not from prose:
  a docstring mentioning the blacklist next to an unrelated `cache.clear()` is
  how a rule earns its first blanket suppression. A revocation key built from
  a constant imported from elsewhere and named nothing revocation-ish is
  missed.
- AUTHZ006 reads **statically declared** response fields: serializer class
  attributes, `Meta.fields`/`read_only_fields` string lists, the literal dict a
  hand-written `to_representation` returns, and a literal dict built in the
  handler itself. It does **not** see a serializer chosen at runtime, a
  response assembled from `**kwargs` or a comprehension, `fields = "__all__"`
  (no model resolution, and guessing would be its first false positive), or a
  disclosing column aliased to an innocuous name. Silence from AUTHZ006 is not
  proof a response is clean, and the message says so on every hit.
- AUTHZ006's serializer index is keyed by **bare class name** across the
  project — no import resolution, so two same-named serializers in different
  apps merge. Linting a single file indexes that file's own directory; linting
  a project indexes the whole tree.
- AUTHZ006 reads permissions only from a **list literal** — `permission_classes
  = PUBLIC_PERMS` or a computed list is "not declared", and with nothing
  declared anywhere the project's `DEFAULT_PERMISSION_CLASSES` decides, which
  is a setting this linter does not resolve. Undeclared means silent, not
  assumed-open.
- AUTHZ006 treats any ownership/permission decision in the handler as the fix,
  without checking that the disclosing branch is the guarded one. A handler
  that branches on `owner_id` and then returns the full serializer from *both*
  arms passes. Source-order and branch-reachability are outside every rule in
  this family.

Composed into `stapel-verify` (surface `python`), so a project picks the gate
up on its next stapel-tools upgrade with nothing to regenerate.

### `stapel-sibling-lint` — the suite imports what nothing declares

```bash
stapel-sibling-lint .                        # lint the repo in .
stapel-sibling-lint . --json                 # machine output
stapel-sibling-lint . --strict               # SIB004/SIB005 (warnings) become errors
```

On 2026-08-24 three releases died of one defect within hours of each other —
stapel-chat 0.5.0, stapel-core 0.44.0, stapel-notifications 0.17.0. All green
on a laptop, all red on the runner, all at test *setup* with
`ModuleNotFoundError`, all re-released the next morning with no product change.
The mechanism: this fleet develops in ONE virtualenv that has every sibling
installed, so a test may import `stapel_moderation` while `pyproject.toml`
never says the suite needs it. Nothing in the repo disagrees — pytest is happy,
ruff is happy, review is happy, because a reader cannot see an absence. The
runner, which installs only what is declared, is the first machine to find out.

| rule | level | what it holds |
|---|---|---|
| SIB001 | error | a suite file imports a top-level `stapel_*` package that pyproject declares in neither `dependencies` nor the `test` extra. Imports are collected at any depth — function bodies, fixtures, `try` blocks — because that is where the ones that shipped were hiding |
| SIB002 | error | a suite file names an undeclared `stapel_*` package in an `INSTALLED_APPS` list. There is no `import` token on that line and it imports all the same: `override_settings` fires `setting_changed`, Django reloads the app registry, and every label is loaded for real (stapel-core 0.44.0, verbatim) |
| SIB003 | error | `pytest.importorskip("stapel_*")` — or the `requires`/`installed` seam from `tests/siblings.py` — naming an undeclared sibling. A skip is not a declaration; it is a decision to run nothing, taken silently, on every machine that lacks the package — which on CI is every machine |
| SIB004 | warning | a **declared** sibling behind a skip guard (`importorskip`, or `try: import … except ImportError: skip`) in a repo whose workflows never set `STAPEL_TEST_STRICT_SIBLINGS=1`. On CI the extra IS installed, so a skip means the install step did not do what the workflow says, and the run stays green having asserted nothing |
| SIB005 | warning | the `test` extra declares a `stapel-*` sibling no suite file imports or names (a `requires("stapel_x")` counts — the seam speaks in strings) — the other direction, so the extra cannot rot into a wish list |
| SIB006 | error | a committed `docs/errors.json` entry whose `owner` is neither this module nor `stapel_core`. The codegen reads the error registry of the interpreter it runs in, so a workspace virtualenv holding every sibling emits keys this package does not own while CI emits fewer: the artifact then describes the machine that generated it, and its drift gate flips depending on who ran it last |

**The `STAPEL_TEST_STRICT_SIBLINGS` contract** (what SIB004 asks for). Every
stapel library's suite honours one environment variable: **a DECLARED sibling
that is not installed is a FAILURE, never a skip.** Unset — a laptop, a fork,
someone who ran `pip install -e .` — a missing sibling skips with a named
reason. Set (CI, always), it fails, because CI installed `.[test]` two steps
earlier and a skip there is the install step lying. The seam is three names in
`tests/siblings.py` (`STRICT`, `installed(module)`, `requires(module, …)`), and
`stapel-new-library` now scaffolds it along with the `test` extra, the CI step
that installs it and the flag on the pytest step. The linter never runs your
suite, so it checks the two things it can see: that the declaration exists, and
that a skip-guarded suite is not shipped with CI silent about strictness.

Suppress with `# noqa: SIB00N` on the reported line, and write the reason.

**What this rule cannot catch, stated plainly:** it is not a dependency
resolver. A module maps to a distribution by the fleet's convention
(`stapel_foo` → `stapel-foo`); a distribution shipping a top-level module under
another name reads as undeclared. It does not follow transitive dependencies
either, on purpose — transitivity is not a contract, and "it worked because
something else pulled it in" is what this class is made of. The one expansion
it performs is a self-referential extra (`stapel-notifications[realtime]`
inside notifications' own `test` extra), because that is this file talking
about this file.

Composed into `stapel-verify` (surface `python`).

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
runtime/tooling allowlist are exempt. ADO005 (error) an installed **gdpr data
owner** (a library shipping `schemas/consumes/gdpr.erasure.requested.json`)
that cannot answer an erasure: no `consume_actions` process in the service's
deploy (`docker-compose*.yml`, or the `<svc>.yml` fragment next to
`services.conf`), or the owner name the library declares in its
`erasure.py`/`gdpr.py` (`OWNER`/`GDPR_OWNER` — `stapel-cdn` answers to
`media`, `stapel-profiles` to `profile`) missing from
`STAPEL_GDPR["DATA_OWNERS"]` on the gdpr host, or listed there without the
subject types the library claims. Both halves skip with a stderr note when
their input is not discoverable.

### `stapel-surface-lint` — pre-merge gate against reinventing what the fleet ships

```bash
stapel-surface-lint .                        # lint the project in .
stapel-surface-lint proj/ --json             # machine output
stapel-surface-lint proj/ --workspace ~/ws   # extra root for sibling module repos
stapel-surface-lint proj/ --no-installed     # workspace checkouts only
```

Reads the `surface` section of every `capabilities.json` the environment and the
workspace expose (installed distributions first — the index is a function of the
lockfile) and fails the branch that rebuilds one of its entries. SUR001 (error) a
`BasePermission` subclass declared under a name an installed module already
publishes as a `permission_class` — matched on the published name, so a
product's own domain permissions stay quiet. SUR002 (error) a symbol listed in
`instead_of` sits in `permission_classes` while its published replacement is used
nowhere in the project; one finding per displaced symbol, never per call site.
SUR003 (error) a `gate_function` imported and never mentioned again — re-export
hubs, `__all__`, `TYPE_CHECKING` and any value reference are cleared first.
SUR004 (error) a `capability_field` with `consumer: frontend` that the `-react`
package reads nowhere outside its generated OpenAPI types; reported only to the
publishing module and to the consuming package. Composed into `stapel-verify`.
Silent, with a note, in an environment whose installed modules ship no
`docs/capabilities.json` yet.

### `stapel-swap-lint` — the anti-lock-in indirection gate

```bash
stapel-swap-lint .            # lint the project in .
stapel-swap-lint proj/ --json # machine output
```

SWAP001 (error) a direct import — or instantiation — of a class registered as
the `default=` of a `get_model()` / `get_presenter()` call somewhere in the
tree: one stray import silently defeats a host's config-swap for that call
site, with no error, just an override that never takes effect. SWAP002 (error)
a `views.py` filling in a DTO imported from a sibling `dto.py` by hand instead
of going through a presenter.

SWAP003 (error) **a hardcoded dotted path into another top-level package,
resolved at runtime** — `import_string("other_pkg.validators.validate")`,
`importlib.import_module("other_pkg.thing")`, `apps.get_model("other_app",
"Model")`, `apps.is_installed("other_app")`, `find_spec("other_pkg")`,
`getattr(other_pkg_module, "symbol")`. Prototype: `stapel-workspaces 0.19.0`,
which asked Django's app registry whether `stapel_profiles` ran in this
process and then resolved `stapel_profiles.validators.validate_display_name`
by string. It worked in a monolith and answered a permanent 503 in a split
deployment, because **a symbol resolution has no remote form**.

The line the rule draws — and the reason it does not outlaw the fleet's own
extension mechanism — is *where the value comes from*, decided at the call
site with no index and no configuration:

| shape | verdict |
| --- | --- |
| `import_string(settings.STAPEL_RECORDINGS["STORAGE"])` | silent — the host chose it |
| `import_string(getattr(settings, "NORMALIZER", DEFAULT))` | silent — a declared extension point |
| `get_model(KEY, default="ourpkg.models.Thing")` | silent — a swap seam waiting to be overridden |
| `import_string("ourpkg.validators.validate")` | silent — your own overridable entity |
| `apps.is_installed("django.contrib.admin")` | silent — a question about the host's config |
| `find_spec("pyvips")` with `pyvips` in an extra | silent — declared, pinned, installed with you |
| `import_string("other_pkg.validators.validate")` | **error** — nobody chose it but the author |

"Ours" is derived, never configured: every top-level package in the tree,
every `AppConfig` label, the `pyproject.toml` distribution name, everything
the manifest pins (`[project.dependencies]`, any extra, any
`requirements*.txt`), the standard library, and `django`. There is no
allowlist to add yourself to. `tests/`, `test_*.py`, `migrations/` and
`.vendor/` are out of scope; a dotted path inside a code template or a
settings assignment is data, not resolution, and is never seen. Suppress a
deliberate exception with `# noqa: SWAP003`. Composed into `stapel-verify`.

SWAP004 (error) **a vendor SDK imported outside the fleet library that owns
the integration** — today `livekit` outside `stapel_video`. Prototype: a
product carrying its own copy of the LiveKit provider next to the library's.
It was not a bad copy; it was *ahead* of the library on two capabilities, and
that is the mechanism — a fork of a provider layer never starts as a fork, it
starts as one call the library did not have yet, added where the engineer was
standing. Every capability added there is one no other consumer gets, and the
day the library fixes something the product with the fork cannot receive the
fix at all.

Not a dependency ban: depend on the SDK, run it in a worker, whatever. What
you may not do is *import* it, because that is the one act that puts a
provider call in product code. The fix is always the same: add the capability
to the library's provider contract and call it through the seam. The
owner table (`_VENDOR_SDK_OWNERS`) is explicit and tiny — a row is added the
day a library ships the capability for the whole fleet, not before. Suppress
with `# noqa: SWAP004`.

Measured across the fleet on release: 34 raw hits, 3 after triage.

### `stapel-makemessages` / `stapel-po-lint` / `stapel-po-prune` — the gettext seam

```bash
make messages                  # extract + gate (the command to reach for)
stapel-po-lint .               # the gate alone — CI, pre-commit
stapel-po-prune . --sample 20  # product-side dry run; --apply to rewrite
```

`makemessages` is what everybody reaches for the moment they add a translatable
string, and run bare it demotes every entry whose source it could not find.
gettext skips both demotions: **obsolete** (`#~`, parked at the end of the file)
and **fuzzy** (left among the live entries, still carrying a translation, still
looking translated). Fuzzy is the dangerous one — a `python-format` →
`python-brace-format` flag flip produces it with no edit to the msgid at all —
and a suite that asserts almost no strings stays green while the product
reverts to its source language.

`stapel-makemessages` runs the extraction with the ignores Django does not
apply itself, runs the gate on the result, and **restores the catalogues
byte-for-byte if anything was demoted**. `--accept-losses` keeps the result
when the demotion is the point.

`stapel-po-lint` is the gate on its own, and is composed into `stapel-verify`:

| rule | level | what |
| --- | --- | --- |
| PO001 | error | fuzzy entry — gettext skips it, the file still shows a translation |
| PO002 | error | obsolete (`#~`) entry — its translation is switched off |
| PO003 | warning | empty `msgstr` — ships the source language |
| PO004 | warning | unowned entry — no `#:` reference resolves to a file in this tree |

`--max-fuzzy N` / `--max-obsolete N` let a known count stand during a sweep, so
the gate still fails when the count *rises*.

PO004 states the rule as a rule: **a catalogue is a projection of its own
sources; it is never a place to park somebody else's strings.** It applies only
to catalogues that are projections — at least one `#:` reference resolving to a
real file — so a hand-authored library catalogue whose `#:` slot holds
translation keys (`#: notification.otp_code.subject`) is never judged on
ownership. PO001/PO002 apply to every catalogue.

`stapel-po-prune` is the product-side fixer. It asks `makemessages` — run in a
scratch copy — what the tree actually contains, then sorts every entry:

* `sourced` — the extraction found it. Kept.
* `shadow` — the extraction found it, but an installed package owns the same
  msgid; it survives only because something in the tree quotes the literal.
* `foreign` — the extraction did not find it and a library owns it.
* `dead` — nobody owns it. **Removed** on `--apply`.

`foreign` and `shadow` are never deleted blindly — deleting them hands the
string back to the library default. They are reported with the override
rewritten into the owning library's own seam (for stapel-notifications,
`STAPEL_NOTIFICATIONS["TEXT"]`, keyed by translation key, read out of the
library's catalogue and key registry), and removed only with
`--relocate-applied`.

Dry run is the default and it proves idempotence rather than claiming it:
it applies to a scratch copy, classifies again, and reports what a second
apply would remove.

### `stapel-llms-txt` — generate the module's own `docs/llms.txt`

```bash
stapel-llms-txt .                       # emit <repo>/docs/llms.txt
stapel-llms-txt . --check               # drift gate (nonzero exit, no write)
stapel-llms-txt . --out /tmp/x          # render a checkout you must not write to
stapel-llms-txt . --budget 8000         # raise the ceiling DELIBERATELY
stapel-llms-txt . --skip-missing        # a repo with no contract is a loud no-op
```

The fifth per-module contract artifact, next to
`docs/{schema,flows,errors,capabilities}.json` and under the same discipline:
emitted by `make contract`, gated by `make contract-check`, committed, shipped
in the wheel. It renders the module's surface slice for an agent's context —
header + `provides`, then **Configuration axes** (key, kind, default, business
label, gated operations and the OR-composed co-gates), **Usage surface** (the
main section: `name — path`, `instead of`, `consumer`, the curated intent),
**Extension points**, **Fits with**, then the compact `METHOD /path —
operationId` catalog grouped by tag with the mount prefix factored out, the
error codes one line each (`code [status] remediation {slots}` — localized
prose stays in `errors.<lang>.md`), and the flow index. Sections whose source
document is absent do not appear at all, so `stapel-core` — no OpenAPI, no
axes — renders as surface + seams.

Three properties, copied from the frontend's `scripts/gen-manifest.mjs` rather
than reinvented:

- **deterministic** — every list has an explicit sort key, nothing carries a
  timestamp or an absolute path, so the drift gate compares bytes without
  false reds;
- **hard token budget** — 4000 by default, the frontend's `LLMS_TOKEN_BUDGET`.
  Over budget **fails** with a per-section cost breakdown and writes *nothing*;
  it never truncates, because a cut context file is indistinguishable from a
  complete one at the point of use. The reported trim order is `surface → axes
  → extension_points`; `--budget N` in the module's Makefile is the deliberate
  alternative;
- **loud when there is nothing to say** — a module with no
  `docs/capabilities.json` is an error naming the module, never an empty
  `llms.txt`. An empty context file answers "does the fleet have a mechanism
  for X?" with a confident no.

Per-module wiring is two lines:

```make
contract:       python3 -m stapel_tools.llms_txt .
contract-check: python3 -m stapel_tools.llms_txt . --check
```

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

### `stapel-disk` — build/disk lifecycle: preflight guard, tiered reclaim, ephemeral reaper

```bash
stapel-disk guard --min-free-gb 15 --for "make e2e"   # refuse BEFORE the build
stapel-disk doctor                                    # where the space went
stapel-disk reclaim [--images]                        # tier 1 [+ tier 2]
stapel-disk reap [--dry-run] [--owner studio-e2e]     # this run's own garbage
```

A build that runs out of disk does not fail cleanly. It dies mid-layer on an
opaque `ENOSPC`, and it can take the docker daemon's socket with it — after
which every shell tool on the machine fails with an EOF too. Measured on the
fleet's own workstation the night this shipped: 27.9 GB across 187 unreferenced
volumes (131 of them anonymous PGDATA directories orphaned by a test script
that removed its postgres container without `-v`), plus 12.4 GB of
unreferenced images.

**`guard`** is the preflight: put it in front of every target that builds an
image or starts a sandbox. Below the threshold it refuses with the free space,
the threshold, the shortfall and the exact reclaim command; above it, it is
silent and cheap. Threshold: `--min-free-gb`, else `STAPEL_DISK_MIN_FREE_GB`,
else 15 GiB.

**`reclaim`** is tiered:

| tier | what | how |
| --- | --- | --- |
| 1 | build cache, dangling images, stopped containers | always, safe to automate |
| 2 | unreferenced images | `--images`, opt-in |
| — | **volumes** | **refused, at every tier** |

The volume refusal is a hard rule in the tool, not a convention: a project's
repositories, its databases and its snapshots live in named volumes, and
`docker system prune --volumes` cannot tell them from build garbage. Passing
`--volumes` exits 2 and prints why. No argv this command emits can delete a
volume.

**`reap`** is the other half — the thing that creates throwaway resources must
own their death. It removes only resources that *identify themselves* as
ephemeral: the `stapel.ephemeral=true` label (the contract, stamped at
creation) or an explicit name pattern (`studio-vol-e2e-*` and friends — the
fallback for resources created before the label existed). A pattern must carry
at least four literal characters before its first wildcard, so `--pattern '*'`
is refused rather than obeyed. `--dry-run` lists matches and the count of
resources inspected and left untouched.

Wire it into a Makefile so the whole lifecycle is one target's business:

```make
e2e: disk-guard
	@set -e; trap 'stapel-disk reap --quiet' EXIT INT TERM; \
		docker compose up -d && ./run-the-suite.sh
```

The `trap` is the point: a crashed run is exactly the run that leaks, so the
reaper has to fire on failure as well as success. Generated projects inherit
`disk-guard` / `disk-doctor` targets from the scaffold.

### `stapel-catalog-import` — an external classified schema as reviewable fixtures

```bash
# the 20 acceptance leaves, with the values_link catalogues resolved locally
stapel-catalog-import --dataset data/ --catalogs catalogs/ --out fixtures/catalog

# the whole catalogue: 3444 categories, 2901 leaves
stapel-catalog-import --dataset data/ --catalogs catalogs/ --all --out fixtures/catalog

# a named subset
stapel-catalog-import --dataset data/ --leaves 129639,67035 --out fixtures/catalog

# the generated half of the shared rule corpus (stapel-attributes §1.5)
stapel-catalog-import --dataset data/ --all --emit-rule-cases tests/golden/rules/catalog
```

Django-free: no settings module, no database, no network. It reads the source catalogue's
autoload dataset (`catalog-autoload-full.json`, `fields.jsonl`) and writes

```
<out>/catalog/features.json      root FeatureDefs — stapel-categories catalog_fixtures format
<out>/catalog/categories.json    the tree, parent_slug edges, per-category feature lists
<out>/vocabularies/<slug>.json   levels + terms + edges, for load_vocabulary
<out>/report.{json,md}           every counter of the run
```

which `manage.py load_vocabulary` and `manage.py load_catalog` apply. Fixtures
are byte-stable — two runs of the same input produce identical files, so a
re-import diffs to nothing and a real change shows up as a real diff.

The decisions worth knowing before you read the output:

- **The whole tree is always parsed, whichever leaves you emit.** Category slugs
  are transliterated from the node name and, on a global collision, prefixed
  with ancestor slugs until unique — 343 slugs collide across 1041 nodes, and a
  20-leaf import must name a category exactly as a full import does.
- **Not every source-catalogue field becomes a feature.** Listing columns (`Title`,
  `Price`, `Address`, …), the source catalogue's own monetization and logistics enums and the
  category path the source catalogue re-emits as single-option selects are counted and skipped,
  with the counts in the report.
- **A `children` field becomes a `group`.** The two composite list fields —
  `DiscountLadderList` (2 454 leaves) and `CompatibleCars` (14) — are a table:
  a list of rows, each row a set of ordinary fields. They emit the composite
  `group` kind (stapel-attributes 0.6.0), each child through the same type
  rules as a top-level field. The parent's own `values_range` is the **row
  cap** (`{"max": 5}` = five ladder steps), so it becomes `repeat`, not a value
  bound. A child's conditional prose is dropped and counted: the rule engine
  reads a flat map of top-level slugs, so a rule inside a row could never fire.
- **Conditional prose becomes rules, or nothing.** The dependency sentences are
  parsed into the closed `require`/`show`/`hide`/`forbid_option`/`limit`
  grammar; a sentence whose controlling field is a listing column, or which does
  not parse, is dropped *with a reason* rather than approximated.
- **«Заполните, если …» demands an answer only when the source catalogue says the field is
  required.** The same sentence sits on two kinds of field, and the record's own
  `required` flag is what tells them apart. On «Мобильные телефоны» all five
  defect fields carry «Заполните, если в поле Condition указано не 'Новое'»;
  `ScreenCondition`/`CaseCondition` are `required: true` and their option lists
  open with «Без дефектов», while `CameraFlaws`/`SensorsFlaws` are
  `required: false` and list **defects only** — «Не работает вспышка», «GPS»,
  «Wi-Fi» — because the sentence means *fill this in if it applies*. Imported as
  `require` regardless, that pair made an honest empty answer
  `mandatory_missing`: on a client fleet's live stand it refused 24 of 42 seeded
  listings and left a seller of a working used phone no way through but to tick
  a fault that does not exist. So a `require` on a field the source marks
  optional is emitted as `show`, counted in the report under
  `rules.require_downgraded_on_optional_field` (7 228 field occurrences of the
  full corpus). **No «нет дефектов» option is synthesized** — the source has no
  such value, and a code no source-catalogue feed can produce is not data. Every
  conditional requirement is additionally paired with the `show` that scopes it:
  the sentence says the field belongs to that case, which is what stops the
  seven wholesale fields standing open for somebody selling one phone.
- **A Да/Нет list stays a `select` when an answer is demanded.** It collapses to
  `bool` — a switch, whose rest state already *shows* one of the two answers —
  only where nothing requires it (6 418 occurrences). Where the source says
  `required: true` (105 occurrences, `BoxSealed` among them) the two options are
  kept explicit, so «Нет» is an answer the seller gave rather than a control's
  default that no model holds.
- **Groups are emitted in form order, not feed order.** The source catalogue's `field_groups`
  arrive in XML-column order, which put «Доставка» and a Rutube video link above
  the phone's manufacturer and closed the form with «Оптовые продажи» — 9 589 px,
  11.4 phone screens. `catalog_import/groups.py` is the ordering table: identity
  groups first, the vertical's own groups next in source order, the plumbing of
  selling (delivery, wholesale, VAT, promotion, contact) last. A group name that
  repeats in one document becomes one contiguous run.
- **A `description` is rewritten for a seller, not copied from the feed docs.**
  `catalog_import/prose.py` resolves every field reference — `<Set>`,
  `WholesaleMinOrderType`, and the group names the source also writes in angle
  brackets — to the Russian label the seller reads on that field, and drops any
  sentence about the file, the feed, a column, XML, CDATA or the source catalogue's seller
  cabinet. A reference that resolves to nothing takes its sentence with it: the
  old `<...>`-as-HTML strip left «Значение – текст внутри из» and «Обязательно,
  если вы указали в поле , что коробка есть» standing under live form fields.
  `example` is the field's placeholder, so it is reduced to ONE sample — the
  first `|` alternative, past any heading line.
- **A big `values_link` catalogue becomes a vocabulary, not inline options.**
  `--catalogs` maps a `values_link` URL to a local XML by basename; a parentless
  level with at most `--inline-threshold` terms is inlined as a plain `select`,
  everything else becomes a `ref_select` pointing at the vocabulary fixture, with
  `parentFeature` linking it to the level above. A truncated download is
  tolerated: complete top-level records are kept, the partial tail is dropped.

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
