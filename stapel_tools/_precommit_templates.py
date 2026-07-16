"""``.pre-commit-config.yaml`` — project-specific gates, NOT generic linters
(§57 owner directive, README-canon item 5). Runs the SAME two commands
AGENTS.md tells an agent to run before claiming done: `stapel-verify` (backend)
and `npx eslint` with `@stapel/eslint-plugin` (frontend, only for a project
that has a `frontend/` dir).

Both hooks are `language: system` — they shell out to tooling the project's
own environment already has (stapel-tools on PATH, node_modules installed),
not a pre-commit-managed venv, since stapel-verify needs THIS project's
Django settings importable and eslint needs THIS project's plugin config.
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
""" + _CONFIG_MANIFEST_HOOK

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
""" + _CONFIG_MANIFEST_HOOK

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
MIG-codes, DOC001 — see `AGENTS.md`). Run the full suite on demand with
`pre-commit run --all-files`.
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
existence). Run the full suite on demand with `pre-commit run --all-files`.
"""
