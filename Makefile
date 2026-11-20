SHELL := /bin/sh
PYTHON ?= python

# `make check` — everything CI runs that a laptop can run, in the order that
# fails cheapest first. The nav gate is IN here, and that is the point of it
# existing at all: `scripts/check_nav_manifest_sync.py` has been in this repo
# since the scripted-navigation wave and was wired into nothing, so the mirror
# it guards drifted five minors without a word (auth-react pinned at 0.10.1
# against a published 0.16.0, and the qr_confirm entry missing entirely).
#
# SIBLING_ROOT is the workspace convention the generated `gen:*` invocations
# already use (`${SIBLING_ROOT:-..}`): point it at the checkout that carries
# stapel-react. Absent that checkout the nav gate SKIPS — it has nothing to
# compare against, which is not a defect of a machine that does not have it.
#
# `sibling-lint` runs the rule this repo ships against this repo. It is second
# because it is the cheapest real gate here and because the class it catches
# (a test importing a sibling nothing declares) only ever shows up on a clean
# runner — i.e. after the tag, at the worst possible moment. Warnings do not
# fail it; SIB001-003 do.
#
# `peer-graph` is deliberately NOT in `check`: it asks the npm registry, so a
# laptop without node or without a network cannot run it and `make check` must
# stay runnable there. CI runs it in the e2e job — the job that already has
# node and already installs from the real registry — right before
# `e2e_npm_pins.py`, and the daily schedule fires that job with no push, which
# is the only way the drift it catches (a PAIR raising its peer floor in
# ANOTHER repo) ever shows up.
#
# `hooks-doctor` is first because it is the only gate here that reports on
# the CLONE rather than the code: `core.hooksPath` is per-clone and
# unversioned, so every fresh clone silently has no hooks at all, and an
# inactive hook is indistinguishable from a passing one until a branch cut
# from an ancient base lands and reverts a week of work.
# `preset-gate` holds every composite's installed preset.py equal to
# STAPEL_LIBS (apps, order, mounts, defaults shape, frontend pairs). It reads
# the INSTALLED composites, so it needs the `test` extra.
.PHONY: kmp-vendor check lint test nav-sync sibling-lint peer-graph pins-check hooks-doctor preset-gate composite-gates golden golden-update registry-export registry-export-check release-check

check: hooks-doctor lint sibling-lint nav-sync kmp-vendor registry-export-check preset-gate golden test

hooks-doctor:
	@$(PYTHON) -m stapel_tools.hooks doctor || { \
	  echo ""; \
	  echo "!!! git hooks are NOT active in this clone — run ./setup-hooks.sh"; \
	  echo "!!! until you do, nothing stops a stale branch from being pushed."; \
	  exit 1; \
	}

lint:
	$(PYTHON) -m ruff check .

sibling-lint:
	$(PYTHON) -m stapel_tools.sibling_lint .

nav-sync:
	$(PYTHON) scripts/check_nav_manifest_sync.py

# The KMP emitter's vendored stapel-kmp data equals its pin (digests, no
# network). Re-vendor with: python -m stapel_tools.composition.kmp.vendor --from <stapel-kmp>
kmp-vendor:
	$(PYTHON) -m stapel_tools.composition.kmp.vendor --check
	$(PYTHON) -m stapel_tools.composition.kmp.ios_parity

# The golden whole-tree gate over generated projects (scripts/golden_scaffold.py):
# every case of the composition matrix must generate byte-identical trees.
# A deliberate output change (a template edit, a pin bump) runs golden-update
# in the same commit; the printed diff names every file that moved.
golden:
	$(PYTHON) scripts/golden_scaffold.py

golden-update:
	$(PYTHON) scripts/golden_scaffold.py --update

# The neutral registry's export for non-Python consumers (stapel-kmp parity,
# Studio): data/composition-registry.json and data/route-plans/*.json.
registry-export:
	$(PYTHON) -m stapel_tools.composition.export

registry-export-check:
	$(PYTHON) -m stapel_tools.composition.export --check

preset-gate:
	$(PYTHON) -m stapel_tools.preset_gate

# Heavy (installs every composite's requirements and runs each generated
# project's gates); CI's `composites` job runs it. Not part of `check`.
composite-gates:
	$(PYTHON) scripts/composite_gates.py

peer-graph:
	$(PYTHON) scripts/check_npm_peer_graph.py

# Network: every registry pin's ceiling admits PyPI's newest release (PIN002).
pins-check:
	$(PYTHON) scripts/check_pypi_pins.py --strict

test:
	$(PYTHON) -m pytest -q -p no:cacheprovider

# `make release-check` — every pre-tag gate, one exit status. The tag is cut
# only when this exits 0; run it bare, never through a pipe (`| tail` returns
# tail's status and once let a red pin gate through to a burned tag). Each
# line is its own recipe step, so the first red gate stops the target and
# make exits non-zero.
#   release-check.sh       on main, contains every earlier v* tag
#   check_release_version  tree clean, HEAD == origin/main, v<version> unused, CHANGELOG section
#   pins (strict)          PIN001/PIN002/PIN003 against PyPI — the blocking pin gate
#   check                  everything CI runs that a laptop can run
RELEASE_CHECK_SH ?= sh ./release-check.sh

release-check:
	$(RELEASE_CHECK_SH)
	$(PYTHON) scripts/check_release_version.py
	$(PYTHON) scripts/check_pypi_pins.py --strict
	$(MAKE) --no-print-directory check PYTHON="$(PYTHON)"
	@echo "release-check: every pre-tag gate is green — tag with: git tag -a v<version> -m ... && git push origin v<version>"
