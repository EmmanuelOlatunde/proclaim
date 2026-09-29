#!/usr/bin/env bash
# Dev-only static check: no-undef across the three runtime JS files.
# Flags references to identifiers that were never declared in scope
# (catches ReferenceError class bugs that `node --check` cannot see).
#
# Not part of the packaged app: lives outside presentation/static and
# presentation/templates, is excluded from Proclaim.spec, and needs nothing
# from requirements.txt. Requires `eslint` on PATH (works fully offline once
# installed).
#
# Run it with:   bash scripts/lint-js.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
FILES=(
  presentation/static/presentation/js/control.js
  presentation/static/presentation/js/display.js
  presentation/static/presentation/js/song-slides.js
)
if ! command -v eslint >/dev/null 2>&1; then
  echo "error: eslint not found on PATH (install once, e.g. 'npm i -g eslint@6')" >&2
  exit 1
fi
ARGS=()
for f in "${FILES[@]}"; do ARGS+=("$DIR/$f"); done
eslint --no-eslintrc --config "$DIR/scripts/eslintrc.browser.json" "${ARGS[@]}"