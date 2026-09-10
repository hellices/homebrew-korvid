# Agent instructions — hellices/homebrew-korvid

This repository is the Homebrew tap for
[korvid](https://github.com/hellices/korvid). It holds one file that matters:
`Formula/korvid.rb`, the formula that every `brew install hellices/korvid/korvid`
resolves. A wrong merge here breaks installs for everyone, immediately, with no
staging step in between.

## Merging

- **Ordinary PRs remain maintainer-owned.** Do not merge code, workflow or
  documentation changes without explicit human authorization.
- **The sole unattended exception is verified release delivery.** The
  default-branch `automatic-delivery.yml` workflow may merge formula-only
  version/bottle PRs authored by the configured delivery GitHub App after
  release provenance, exact-head validation, all four installation checks,
  and branch protection pass. It never bypasses checks or resolves reviews.
- Do not expand this exception to other actors, paths or PR types. Never
  enable persistent auto-merge as a substitute for the one-shot delivery gate.
- Do not open a pull request without explicit human instruction, and never
  approve your own work (`gh pr review --approve`).
- When a pull request is ready, report that it is ready and stop.

`main` must keep the `Protect default branch` ruleset with **no bypass actors**:
pull request required, review threads resolved, both source and both bottle
test legs green against an up-to-date base, no direct push, no force push, no
deletion. The delivery gate checks these requirements before using its App
token. Normal PRs are not opted into automatic merging.

## Changing the formula

`Formula/korvid.rb` is generated, not hand-written. It comes from
`scripts/generate_homebrew_formula.py` in the korvid repository, run against a
tag-revalidated `uv.lock`. Edit it by hand only to fix something the generator
got wrong, and fix the generator in the same breath — otherwise the next
release silently reverts you.

Homebrew installs with `--no-deps`, so **every** transitive dependency must
appear as its own `resource` block. A dependency that is only declared through
an extra (`markdown-it-py[linkify]`) still needs its resources listed
explicitly.

## Checks

`.github/workflows/test.yml` runs `brew audit` and `brew install` on
`ubuntu-latest` and `macos-latest`, on pull requests and weekly. The weekly run
is not redundant: a formula can break with nobody touching it when an upstream
sdist is yanked.

`brew` refuses `audit` and `install` by path, so the workflow symlinks the
checkout into place as a real tap. Keep that step if you touch the workflow —
without it the formula is never tested the way a user reaches it.
