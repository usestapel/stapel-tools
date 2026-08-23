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
.PHONY: check lint test nav-sync

check: lint nav-sync test

lint:
	$(PYTHON) -m ruff check .

nav-sync:
	$(PYTHON) scripts/check_nav_manifest_sync.py

test:
	$(PYTHON) -m pytest -q -p no:cacheprovider
