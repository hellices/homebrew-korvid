# Task 3 Report

## Status
✅ Complete

## Commit
`270e9c4` — ci: verify bottle installs avoid PyPI

## Files Changed
- `.github/workflows/test.yml` — added `workflow_dispatch:` trigger; added `test-bottle` job over `macos-15` + `macos-15-intel` with conditional skip when no matching bottle tag, PyPI host-block, pipefail install, and `Pouring korvid--` assertion
- `tests/test_workflows.py` — added `TEST_WORKFLOW` path constant and `TestTestWorkflow` class with two new assertions

## Commands and Results

```
python3 -m unittest tests/test_workflows.py -v
→ Ran 13 tests in 0.070s — OK (all pass; bottle test was FAIL before workflow edits ✓ RED confirmed)

ruby -e 'require "yaml"; YAML.parse_file(".github/workflows/test.yml")'
→ YAML OK (exit 0)
```

## Test Summary
13/13 tests pass; RED→GREEN cycle verified for `test_formula_tests_verify_bottle_without_pypi`.

## Concerns
- ~~`test-bottle` uses an inline Python snippet to resolve the bottle tag; if the Homebrew JSON schema changes, the skip logic may produce a false positive skip instead of a failure. The job will be silent in that case rather than noisy.~~ **Fixed in `b5ab876`** (see below).
- `macos-15-intel` runners are hosted runners; availability depends on GitHub's runner fleet.

---

## Correctness fix — commit `b5ab876`

**Problem:** The inline bottle-tag resolver used `.get('bottle', {}).get('stable', {}).get('files', {})` with chained silent defaults. A missing key, wrong container type, or any unexpected schema change would produce an empty string for `$tag`, causing the job to set `skip=true` and silently pass instead of failing with a clear error.

**Fix:** Replaced the permissive `.get()` chain with explicit `isinstance` guards and `sys.exit(1)` on every structural violation. Silent skip is now only possible when the schema is structurally valid and the current runner's expected tag is genuinely absent from `stable.files`.

**New contract test:** `test_bottle_tag_resolver_fails_on_malformed_schema` — asserts that the permissive chained-`.get()` patterns are absent and that an explicit failure path (`sys.exit(1)` or `raise`) is present.

**Test evidence (all 14 pass, YAML valid):**

```
python3 -m unittest tests/test_workflows.py -v
test_bottle_workflow_builds_both_macos_architectures ... ok
test_bottle_workflow_opens_but_never_merges_a_pr ... ok
test_bottle_workflow_validates_before_publishing ... ok
test_branch_checkout_before_formula_merge ... ok
test_build_concurrency_includes_matrix_tag ... ok
test_commit_guarded_when_no_staged_difference ... ok
test_draft_asset_deletion_passes_release_tag_via_env_not_literal ... ok
test_no_dead_json_file_assignment_in_build ... ok
test_prepare_calls_brew_info_at_most_once ... ok
test_publish_job_has_concurrency_group ... ok
test_published_recovery_uses_validated_artifact_dir_not_local_artifacts ... ok
test_bottle_tag_resolver_fails_on_malformed_schema ... ok  ← new
test_formula_tests_preserve_source_fallback ... ok
test_formula_tests_verify_bottle_without_pypi ... ok

Ran 14 tests in 0.070s — OK

ruby -e 'require "yaml"; YAML.parse_file(".github/workflows/test.yml")'
→ YAML OK (exit 0)
```

---

## Review-blocker fixes — commit `dcc0c63`

### Blocker 1: Empty bottle treated as malformed schema

**Root cause:** `if not isinstance(bottle, dict) or 'stable' not in bottle:` raised `SCHEMA_ERROR` on `bottle == {}`, which is a legitimate Homebrew response for source-only formulae. Any source-only PR would silently fail rather than skip.

**Fix:** Added an explicit empty-bottle check immediately after the `isinstance` guard:
```python
if not bottle:
    print('')
    sys.exit(0)
```
`bottle == {}` now sets `skip=true`; non-dict bottle or a non-empty bottle missing `stable` still triggers `sys.exit(1)`.

### Blocker 2: Tag mapping reversed

**Root cause:** `suffix = '_x86_64' if arch == 'x86_64' else ''` + `target = base + suffix` produced `sequoia_x86_64` (wrong) for Intel and `sequoia` (wrong) for arm64. Homebrew convention is `arm64_sequoia` / `sequoia`.

**Fix:**
```python
target = 'arm64_' + base if arch == 'arm64' else base
```

### TDD evidence

**RED** (before workflow edits):
```
test_bottle_tag_resolver_maps_arch_correctly ... FAIL
test_bottle_tag_resolver_skips_on_empty_bottle ... FAIL
Ran 16 tests — FAILED (failures=2)
```

**GREEN** (after workflow edits):
```
python3 -m unittest tests/test_workflows.py -v
Ran 16 tests in 0.085s — OK

ruby -e 'require "yaml"; YAML.parse_file(".github/workflows/test.yml")'
→ YAML OK (exit 0)
```

### Concerns
- `sw_vers -productVersion` is macOS-only. The `test-bottle` job only runs on macOS runners, so this is intentional, but if the job matrix ever gains Linux runners the resolver will fail.
- `arm64` detection relies on `platform.machine()` returning exactly `"arm64"`. On some Rosetta environments this may return `"x86_64"` even on Apple Silicon; that would cause a false skip, which is safe (not a false positive pour).
