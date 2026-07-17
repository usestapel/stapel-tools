"""AGENTS.md — base OSS rules for coding agents in a stapel-create-project /
new-library scaffold (§57 owner directive). Short, imperative, no prose —
this file is read by agents, not humans. NOT studio/proprietary rules — the
generic OSS discipline every stapel-* project shares, sourced from the actual
lint tools this repo ships (stapel-verify's composed linters + the frontend
eslint plugin), not invented.

{{FRONTEND_SECTION}} is "" for a project with no frontend/ dir (minimal,
microservices today — §57 follow-up), or the frontend-specific block for a
monolith (Vite scaffold wired in, see _frontend_templates.py).
"""

AGENTS_MD = """\
# AGENTS.md

Rules for coding agents working in this repository. Imperative, not
descriptive — violations are caught by `make controls` / `stapel-verify` /
`npx eslint`, not by review.

## 1. Backend responses — never bypass the envelope

- Views return `StapelResponse(SomeSerializer(dto))` or
  `StapelErrorResponse(status, ERR_CONST)`. Never `Response({...})` /
  `Response(dict_literal)` (R001, R006) — a dict literal skips the
  serializer, silently dropping field-level contract checks.
- Errors are `ERR_*` module constants, never a raw string literal
  (R005) — `StapelErrorResponse(404, ERR_404_THING_NOT_FOUND)`, not
  `StapelErrorResponse(404, "not found")`.
- Client-facing validation raises `StapelValidationError`, not
  `serializers.ValidationError` (R002).
- Every `@extend_schema`'d view method carries `@flow_step` (R007) — an
  endpoint with no flow is undocumented by construction. Every `@action`
  carries `@extend_schema`/`@extend_schema_view` (R003).
- `@dataclass` DTOs in `dto.py` carry a docstring (R004) — OpenAPI docs are
  generated FROM it, not hand-duplicated.

## 2. Swappable model / presenter — never reach around the indirection

- Never `import`/`from ... import` a class that is registered as the
  `default=` of a `get_model(key, default=...)` / `get_presenter(key,
  default=...)` call, and never instantiate it directly (SWAP001). Go
  through `get_model()` / `get_presenter()` — a direct import silently
  defeats a host's config-swap for that call site (django-oscar #3232
  class of bug).
- Never build a response straight from a `dto.py` dataclass in a view
  (SWAP002). The DTO is instantiated in ONE place — the presenter.

CORRECT (the only shape a view may have):

```python
from .presenters import get_thing_presenter
from .serializers import ThingResponseSerializer

def get(self, request):
    dto = get_thing_presenter()().present(obj)
    return StapelResponse(ThingResponseSerializer(dto))
```

WRONG (SWAP002 — bypasses the presenter, kills the host's swap seam):

```python
from .dto import ThingResponse                       # in a view: forbidden

def get(self, request):
    return StapelResponse(
        ThingResponseSerializer(ThingResponse(id=obj.id))
    )
```

Every scaffolded module ships `presenters.py` (default presenter +
`declare_swap()` + `get_<module>_presenter()`) — extend it, don't route
around it. The project-root `PRESENTERS.MD` is the generated swap/presenter
catalog (`manage.py presenter_catalog`; the `presenter-catalog-check`
pre-commit hook fails the commit when it drifts).

## 3. Config — one place, one registry, one purpose per key

- Every `get_config(...)` / `get_secret(...)` / `os.environ[...]` /
  `os.getenv(...)` read lives in the settings module (or a `settings/`
  package member) — never scattered in views/models/services (CFG001).
- Every key the settings module reads has a row in the project-root
  `CONFIG.MD` (CFG002) — no undeclared knob. Every `STAPEL_<MOD>` axis this
  project actually sets is a real axis in that module's own
  `docs/capabilities.json` (CFG003) — not a guessed key.
- Every `CONFIG.MD` row's Purpose column is filled in (CFG004) — a key with
  no stated purpose is undocumented in every way that matters, even if a
  row technically exists.
- `CONFIG.MD` is a GENERATED aggregate of the selected libs' own registries
  — never hand-edit the lib-owned sections (`## stapel-<lib>`) directly, run
  `stapel-config-manifest .` (regenerates from each lib's current CONFIG.MD)
  and commit the result. The `config-manifest-check` pre-commit hook fails
  the commit on drift; the fix is that same command, no flag.

## 4. URLs — never a bare `URLField()`

- `models.URLField()` with no `max_length` defaults to 200 — real external
  URLs (OAuth avatar, SAML SSO/SLO, OIDC discovery) routinely exceed that
  and turn into a `StringDataRightTruncation` 500 on insert, not a
  validation error (URL001). Always pass an explicit `max_length` (500+
  unless you have a specific reason for less).

## 4b. Dev env vs stands — the committed `.env.local` is LOCAL-ONLY

- `.env.local` IS committed by design (clone → compose up must just work). It
  contains ONLY recognizable dev markers (`django-insecure-dev-*` keys, the
  default postgres password, admin/admin superuser, `STAPEL_LOCAL_ENV=1`) —
  never put a real secret into it, and never "fix" its values to make a
  deploy pass.
- Stands/prod deploy ONLY through `deploy/deploy.sh <env-file>`; its gate
  (`deploy/check-env.sh`) refuses any env carrying a dev marker
  (`STAPEL_LOCAL_ENV`, `django-insecure-*`/`dev-insecure-`/`change_me*`
  secrets, `DEBUG=true`, default passwords, `*_PROVIDER=mock`). Do not
  bypass or weaken the gate — stapel-core's prodguard refuses the same
  secrets/password at prod boot anyway (`ImproperlyConfigured`).
- A stand env is generated fresh per stand (shape: `.env.example`, real
  random secrets), never copied from `.env.local`.

## 5. Generated artifacts — never hand-edit, always regenerate

Everything below is produced by a `stapel-tools` command from a real
source (schema/registry/lib CONFIG.MD) and re-checked by a pre-commit
hook; a hand-edit is silently overwritten (or fails the drift gate) the
next time someone runs the generator. Fix the SOURCE, then regenerate —
never patch the output file directly.

| Generated | Command | Source | Pre-commit gate |
|---|---|---|---|
| `CONFIG.MD` | `stapel-config-manifest .` | selected libs' own CONFIG.MD | `config-manifest-check` |
| `reserved-paths.json` (frontend only) | `stapel-reserved-paths .` | this project's lib selection | `reserved-paths-check` |
| `PRESENTERS.MD` | `manage.py presenter_catalog` | live presenter/swap registries | `presenter-catalog-check` |
| `docs/api.en.md` + `docs/api.ru.md` | `stapel-docs .` | `schema.json`/`flows.json`/`errors.json` (+ ru translations, where a module ships them) | `api-docs-check` |
| `frontend/src/api/generated-override/<mod>/schema.ts` (frontend only, only once this project overrides a default) | `stapel-gen-client .` | THIS project's own `schema.json` | `gen-client-check` |
| `frontend/src/stapel-tokens/` (frontend only) | `npm run gen:tokens` (in `frontend/`) | `frontend/stapel.theme.json` (§68 neutral colour roles) | `tokens-check` |

`stapel-docs` and `stapel-gen-client` are both no-ops (exit 0, nothing
written) when their source doesn't exist yet — `stapel-docs` until
`schema.json` has been generated (`stapel-codegen` / the codegen pipeline),
`stapel-gen-client` until this project actually overrides a stapel default
(a non-empty `STAPEL_SWAP = {...}` anywhere, or an explicit
`stapel.override.json` with `"clientOverride": true`). Neither command
invents output from nothing.
{{FRONTEND_SECTION}}
## Verify before you claim done

    stapel-verify .          # composes every backend linter above (R/SWAP/CFG/URL/ADO/MIG/DOC codes); exit 0 required
    npx eslint .              # frontend/, if this project has one

`stapel-verify` runs `stapel_tools.lint` (R001-R007), `adoption_lint`
(module actually mounted, no shadow routes — ADO-codes), `url_lint`
(URL001), `config_lint` (CFG001-003), `migration_lint` (MIG-codes,
expand/contract), `swap_lint` (SWAP001/SWAP002) and `doc_lint` (DOC001) in
one pass — no reimplemented rules, pure composition. A green run is
required before calling backend work done; a green `npx eslint` (with
`@stapel/eslint-plugin`'s flat config) is required before calling frontend
work done.
"""

# Appended when the project has a frontend/ dir (monolith today — §57).
FRONTEND_SECTION = """
## 6. Frontend — no hardcodes, typed events, repositories not raw storage

- No raw colours: every colour is a design token (`@stapel/eslint-plugin`
  rule `no-raw-colors`) — `cssVar("...")`, never a literal hex/rgb.
  Tokens are imported from the generated tokens package, never re-declared
  (`no-raw-token-import`).
- Colours are defined in ONE place, `frontend/stapel.theme.json` — a
  neutral role dictionary (§68: `surface*`/`text*`/`border*`/`brand*`/
  `link*` + `success`/`warning`/`error`/`info` × `{base, -bg, -border,
  -on}`), never a design-system-specific name. **The colour of every
  default button in this project is the `brand` role** — whatever design
  system renders it (antd's `colorPrimary`, a Tailwind `bg-brand` utility,
  a raw `var(--stapel-brand)`) reads the SAME `brand` entry, never a
  hardcoded blue. To re-theme, edit `stapel.theme.json`'s `ramps`/`core`
  entries (e.g. swap the `brand` ramp's hex, or repoint a role to a
  different ramp step) and regenerate — `npm run gen:tokens` in
  `frontend/`, or just commit: the `tokens-check` pre-commit hook
  regenerates `frontend/src/stapel-tokens/` and fails the commit on drift
  (§5's table). Never hand-edit the generated output, and never fork the
  generator itself — `stapel-tokens` ships INSIDE `@stapel/tokens` as its
  own bin (`package.json`'s `gen:tokens`/`gen:tokens:check` scripts call it
  directly).
- No hardcoded user-facing text (`no-hardcoded-text`) — every label goes
  through the i18n engine (`t("key")`), and every key used must exist in a
  registered bundle (`i18n-key-exists`).
- No raw `fetch()` (`no-raw-fetch`) — go through the generated typed API
  client. No string literal paths for routes/query keys
  (`no-string-paths`); query keys come from the namespaced factory
  (`query-keys-from-factory`).
- Analytics events are typed and registered, never ad hoc
  (`known-event`, `event-literal-meta`, `no-double-count`,
  `no-direct-analytics-provider`); every clickable element fires a
  tracked event (`clickable-needs-event`).
- No raw `localStorage`/`sessionStorage`/cookie access (`no-raw-storage`)
  — go through this project's repository layer, never touch browser
  storage directly from a component.
- Reserved backend namespace (see `reserved-paths.json` at the project
  root, and this project's nginx canon): never define a client-side route
  matching an entry in `reserved-paths.json`'s `"prefixes"` array — always
  `/{{SLUG}}/`, `/staticfiles/`, `/media/` in full, plus, per installed lib,
  ONLY its `/<mod>/api/`, `/<mod>/swagger/`, `/<mod>/schema.json` and
  `/<mod>/admin/` — those are proxied to Django by nginx/local-nginx, not
  rendered by this app. The frontend router OWNS a lib's bare `/<mod>` root
  and any other sub-path under it (e.g. `/calendar` the page, distinct from
  `/calendar/api/` the backend) — never reserve more than
  `reserved-paths.json` actually lists; regenerate it with
  `stapel-reserved-paths .` if it looks stale (the
  `reserved-paths-check` pre-commit hook fails the commit on drift).
- If this project has overridden a stapel default (a `STAPEL_SWAP` entry, or
  extra profile/attribute fields), point the affected pair's api layer at
  `frontend/src/api/generated-override/<mod>/schema.ts` instead of the
  pair's own bundled types — regenerate it with `stapel-gen-client .`, never
  hand-edit (§5's table; `gen-client-check` gates drift, and is a silent
  no-op until an override actually exists).
"""
