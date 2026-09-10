#!/usr/bin/env bash
set -euo pipefail

git fetch origin main:refs/remotes/origin/main
git show origin/main:Formula/korvid.rb > "$RUNNER_TEMP/current-formula.rb"
git show "$GITHUB_SHA:Formula/korvid.rb" > "$RUNNER_TEMP/built-formula.rb"
python3 scripts/delivery_policy.py \
  --current "$RUNNER_TEMP/current-formula.rb" \
  --built "$RUNNER_TEMP/built-formula.rb" \
  --version "$VERSION"
