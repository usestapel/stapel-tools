# Changelog

## [Unreleased]

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
