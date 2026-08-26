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
.PHONY: check lint test nav-sync sibling-lint peer-graph

check: lint sibling-lint nav-sync test

lint:
	$(PYTHON) -m ruff check .

sibling-lint:
	$(PYTHON) -m stapel_tools.sibling_lint .

nav-sync:
	$(PYTHON) scripts/check_nav_manifest_sync.py

peer-graph:
	$(PYTHON) scripts/check_npm_peer_graph.py

test:
	$(PYTHON) -m pytest -q -p no:cacheprovider
