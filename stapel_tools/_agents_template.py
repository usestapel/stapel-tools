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
  (SWAP002). Go through the presenter: `get_presenter(key,
  default=X)().present(dto)`.

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
## 5. Frontend — no hardcodes, typed events, repositories not raw storage

- No raw colours: every colour is a design token (`@stapel/eslint-plugin`
  rule `no-raw-colors`) — `cssVar("...")`, never a literal hex/rgb.
  Tokens are imported from the generated tokens package, never re-declared
  (`no-raw-token-import`).
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
- Reserved backend namespace (see this project's nginx canon): never
  define a client-side route under `/{{SLUG}}/`, `/staticfiles/` or
  `/media/` — those are proxied to Django by nginx/dev-nginx, not
  rendered by this app.
"""
