#!/bin/sh
set -eu
git config core.hooksPath .githooks
chmod +x .githooks/* 2>/dev/null || true
configured="$(git config --get core.hooksPath || true)"
if [ "$configured" != ".githooks" ]; then
    echo "FAILED: core.hooksPath reads back as '${configured:-<unset>}'." >&2
    exit 1
fi
echo "Git hooks active: core.hooksPath=.githooks"
echo "  pre-push refuses a branch that does not contain origin/<default>."
echo "  Audit any time with: stapel-hooks doctor"
