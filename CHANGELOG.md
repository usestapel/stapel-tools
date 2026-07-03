# Changelog

## [Unreleased]

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
