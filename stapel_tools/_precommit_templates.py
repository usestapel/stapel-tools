"""``.pre-commit-config.yaml`` — project-specific gates, NOT generic linters
(§57 owner directive, README-canon item 5). Runs the SAME two commands
AGENTS.md tells an agent to run before claiming done: `stapel-verify` (backend)
and `npx eslint` with `@stapel/eslint-plugin` (frontend, only for a project
that has a `frontend/` dir).

Both hooks are `language: system` — they shell out to tooling the project's
own environment already has (stapel-tools on PATH, node_modules installed),
not a pre-commit-managed venv, since stapel-verify needs THIS project's
Django settings importable and eslint needs THIS project's plugin config.

Plus the regenerator-of-everything-that-can-be-regenerated set (owner
directive: "в pre-commit должен быть регенератор ВСЕГО, что можно"):
config-manifest-check (CONFIG.MD), reserved-paths-check
(reserved-paths.json, frontend projects only), api-docs-check
(docs/api.en.md + api.ru.md, every project — `stapel-docs`), gen-client-
check (frontend/src/api/generated-override/, frontend projects only,
no-op unless the project actually overrides a default — `stapel-gen-
client`), tokens-check (frontend/src/stapel-tokens/, frontend projects
only — §68 neutral colour-role dictionary compiled from `stapel.theme.json`
by @stapel/tokens' OWN published generator, the `stapel-tokens` bin; never
a vendored/forked copy of the engine), and presenter-catalog-check
(PRESENTERS.MD, wired in separately by `presenter_catalog_hook` where a
manage.py exists). Every one of these hooks runs `<command> . --check` and
fails the commit on drift; the fix is always the SAME command without
`--check`, review the diff, commit.
"""

# config-manifest-check (§57 owner directive item 8): regenerates CONFIG.MD
# from the libs' own registries and fails the commit on drift (a lib's
# CONFIG.MD changed since this project last regenerated); auto-fix by
# running `stapel-config-manifest .` (no --check) and committing the result.
_CONFIG_MANIFEST_HOOK = """\
      - id: config-manifest-check
        name: stapel-config-manifest --check (CONFIG.MD drift)
        entry: stapel-config-manifest . --check
        language: system
        pass_filenames: false
        always_run: true
"""

# reserved-paths-check (owner directive, "/calendar page vs backend"
# postmortem): regenerates reserved-paths.json — the flat backend-path
# projection nginx-local/prod-nginx/Vite AND @stapel/eslint-plugin's
# no-reserved-backend-route rule all read — and fails the commit on drift
# (stapel-tools' module-sub-surface definition changed since this project
# was generated/last regenerated); auto-fix by running
# `stapel-reserved-paths .` (no --check) and committing the result. Only
# wired into the frontend pre-commit config — reserved-paths.json only
# exists where there's a frontend router that could collide with it.
_RESERVED_PATHS_HOOK = """\
      - id: reserved-paths-check
        name: stapel-reserved-paths --check (reserved-paths.json drift)
        entry: stapel-reserved-paths . --check
        language: system
        pass_filenames: false
        always_run: true
"""

# stapel-docs --check (owner directive: "документация по api/флоу — в
# идеале двуязычная"): regenerates docs/api.en.md + docs/api.ru.md from the
# project's own schema.json/flows.json/errors.json (+ translations, when a
# module ships them) and fails the commit on drift; auto-fix by running
# `stapel-docs .` (no --check) and committing the result. Wired into EVERY
# project type that reaches `_write_agents_and_checks` — docs are a
# backend-only concern (no frontend/ dependency), and the command itself is
# a graceful no-op (exit 0) on a project with no schema.json generated yet,
# so it is never a false-positive blocker for a project that hasn't run
# `stapel-codegen` yet.
_API_DOCS_HOOK = """\
      - id: api-docs-check
        name: stapel-docs --check (docs/api.en.md + api.ru.md drift)
        entry: stapel-docs . --check
        language: system
        pass_filenames: false
        always_run: true
"""

# stapel-gen-client --check (docs/pending/profile-fields.md "Дополнение
# владельца" §17.07, tier 2): regenerates the project's OWN typed-client
# override (frontend/src/api/generated-override/<mod>/schema.ts) from its
# own schema.json and fails the commit on drift; auto-fix by running
# `stapel-gen-client .` (no --check) and committing the result. The command
# itself gates on override_active() — a project with no STAPEL_SWAP
# override and no stapel.override.json flag is a no-op (exit 0), so this
# hook is harmless to wire unconditionally into every frontend-carrying
# project, not just ones that have overridden something TODAY: the day a
# host adds a STAPEL_SWAP entry, this hook comes alive without any project
# regeneration. Frontend-only (the output lives under frontend/) — omitted
# from the backend-only config.
_GEN_CLIENT_HOOK = """\
      - id: gen-client-check
        name: stapel-gen-client --check (generated-override client drift; no-op without an override)
        entry: stapel-gen-client . --check
        language: system
        pass_filenames: false
        always_run: true
"""

# tokens-check (§68 color-token-matrix, Ф5): regenerates
# frontend/src/stapel-tokens/ (the CSS custom-property core, §68 neutral
# colour-role dictionary) from this project's OWN frontend/stapel.theme.json
# and fails the commit on drift. Runs THROUGH the frontend's own npm script
# (package.json's `gen:tokens:check`), which in turn calls @stapel/tokens'
# published `stapel-tokens` bin — the generator ships AS the npm package
# (0.5.0+), so this hook never shells out to a vendored/forked copy of the
# engine (the exact failure mode §68 closes; see
# docs/pending/color-token-matrix.md). Auto-fix: `npm run gen:tokens` (no
# `:check`) inside `frontend/`, review the diff, commit. Frontend-only, same
# reasoning as reserved-paths-check/gen-client-check above.
_TOKENS_HOOK = """\
      - id: tokens-check
        name: stapel-tokens --check (frontend/src/stapel-tokens drift; Sec.68 neutral colour dictionary)
        entry: sh -c "cd frontend && npm run gen:tokens:check"
        language: system
        pass_filenames: false
        always_run: true
"""

PRE_COMMIT_CONFIG_BACKEND_ONLY = """\
# Install: pip install pre-commit && pre-commit install
# Run on demand: pre-commit run --all-files
# Drift/auto-fix: `stapel-config-manifest .` (no --check) regenerates
# CONFIG.MD — review the diff and commit it.
repos:
  - repo: local
    hooks:
      - id: stapel-verify
        name: stapel-verify (R/SWAP/CFG/URL/ADO/MIG/DOC codes)
        entry: stapel-verify .
        language: system
        pass_filenames: false
        always_run: true
""" + _CONFIG_MANIFEST_HOOK + _API_DOCS_HOOK

PRE_COMMIT_CONFIG_WITH_FRONTEND = """\
# Install: pip install pre-commit && pre-commit install
# Run on demand: pre-commit run --all-files
# Drift/auto-fix: `stapel-config-manifest .` (no --check) regenerates
# CONFIG.MD — review the diff and commit it.
repos:
  - repo: local
    hooks:
      - id: stapel-verify
        name: stapel-verify (R/SWAP/CFG/URL/ADO/MIG/DOC codes)
        entry: stapel-verify .
        language: system
        pass_filenames: false
        always_run: true
      - id: eslint-frontend
        name: eslint (@stapel/eslint-plugin, frontend/)
        entry: sh -c "cd frontend && npx eslint ."
        language: system
        pass_filenames: false
        always_run: true
""" + _CONFIG_MANIFEST_HOOK + _RESERVED_PATHS_HOOK + _API_DOCS_HOOK + _GEN_CLIENT_HOOK + _TOKENS_HOOK

# ── README "Checks" section (dropped in verbatim, no tokens) ───────────────
README_CHECKS_SECTION_BACKEND_ONLY = """\
## Checks

Install the pre-commit hooks once:

```bash
pip install pre-commit
pre-commit install
```

Every commit then runs `stapel-verify .` (composes every backend linter this
project ships: R001-R007, SWAP001-002, CFG001-003, URL001, ADO-codes,
MIG-codes, DOC001 — see `AGENTS.md`) plus the regenerator/drift gates:
`stapel-config-manifest . --check` (CONFIG.MD) and `stapel-docs . --check`
(bilingual `docs/api.en.md`/`api.ru.md` — no-op until a `schema.json` has
been generated). Run the full suite on demand with `pre-commit run
--all-files`; a drifted gate's fix is always the same command without
`--check`, reviewed and committed.
"""

README_CHECKS_SECTION_WITH_FRONTEND = """\
## Checks

Install the pre-commit hooks once:

```bash
pip install pre-commit
pre-commit install
```

Every commit then runs `stapel-verify .` (backend — see `AGENTS.md` for the
full rule list) and `npx eslint .` in `frontend/` (`@stapel/eslint-plugin`'s
flat config — no raw colours/fetch/storage, typed events, i18n-key
existence), plus the regenerator/drift gates: `stapel-config-manifest .
--check` (CONFIG.MD), `stapel-reserved-paths . --check`
(reserved-paths.json), `stapel-docs . --check` (bilingual
`docs/api.en.md`/`api.ru.md`), `stapel-gen-client . --check`
(`frontend/src/api/generated-override/` — a no-op unless this project has
actually overridden a stapel default; see AGENTS.md §6) and `npm run
gen:tokens:check` in `frontend/` (`frontend/src/stapel-tokens/` — the §68
neutral colour-role dictionary compiled from `frontend/stapel.theme.json`
by `@stapel/tokens`' own `stapel-tokens` bin, never a vendored generator).
Run the full suite on demand with `pre-commit run --all-files`; a drifted
gate's fix is always the same command without `--check`, reviewed and
committed.
"""


def presenter_catalog_hook(manage_dir: str) -> str:
    """PRESENTERS.MD freshness hook (§55) — `manage.py presenter_catalog
    --check` (stapel-core's own command; the catalog is generated through
    core's exported write_presenters_md() at scaffold time). ``manage_dir``
    is the directory holding manage.py, relative to the project root ("."
    for minimal, "svc-<slug>" for a monolith)."""
    if manage_dir in (".", ""):
        entry = "python manage.py presenter_catalog --check"
    else:
        # PRESENTERS.MD lives at the PROJECT root, not the service dir.
        entry = (
            f'sh -c "cd {manage_dir} && python manage.py presenter_catalog '
            f'--check --out ../PRESENTERS.MD"'
        )
    return f"""\
      - id: presenter-catalog-check
        name: manage.py presenter_catalog --check (PRESENTERS.MD drift)
        entry: {entry}
        language: system
        pass_filenames: false
        always_run: true
"""
