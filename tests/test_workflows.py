"""Contract tests for .github/workflows/bottles.yml and test.yml."""
from __future__ import annotations

import unittest
from pathlib import Path

BOTTLES_WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "bottles.yml"
TEST_WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "test.yml"


class TestBottlesWorkflow(unittest.TestCase):
    def test_bottle_workflow_builds_both_macos_architectures(self):
        text = BOTTLES_WORKFLOW.read_text()
        self.assertIn("macos-15", text)
        self.assertIn("macos-15-intel", text)
        self.assertIn("brew install --build-bottle", text)
        self.assertIn("brew bottle --json", text)

    def test_bottle_workflow_validates_before_publishing(self):
        text = BOTTLES_WORKFLOW.read_text()
        self.assertLess(
            text.index("validate_bottle_artifacts.py"),
            text.index("gh release create"),
        )
        self.assertIn("brew bottle --merge --write --no-commit", text)

    def test_bottle_workflow_opens_but_never_merges_a_pr(self):
        text = BOTTLES_WORKFLOW.read_text()
        self.assertIn("gh pr create", text)
        self.assertIn("gh workflow run test.yml", text)
        self.assertNotIn("gh pr merge", text)
        self.assertNotIn("--auto", text)

    def test_draft_asset_deletion_passes_release_tag_via_env_not_literal(self):
        """The Python block that deletes draft assets must not embed $RELEASE_TAG
        as a literal string inside a single-quoted Python string. It must receive
        the release tag through os.environ (RELEASE_TAG env var) or equivalent."""
        text = BOTTLES_WORKFLOW.read_text()
        # The safe pattern: reference os.environ with RELEASE_TAG in Python (either quote style)
        self.assertTrue(
            'os.environ["RELEASE_TAG"]' in text or "os.environ['RELEASE_TAG']" in text,
            "Python delete-asset block must read RELEASE_TAG from os.environ",
        )
        # The unsafe pattern must not be present
        self.assertNotIn("'$RELEASE_TAG'", text)

    def test_publish_job_has_concurrency_group(self):
        """The publish job must declare its own concurrency group keyed by version
        so two workflow runs for the same version cannot race in the publish step."""
        import yaml  # available on GitHub runners and dev machines
        with open(BOTTLES_WORKFLOW) as f:
            wf = yaml.safe_load(f)
        publish_job = wf.get("jobs", {}).get("publish", {})
        self.assertIn(
            "concurrency",
            publish_job,
            "publish job must have a concurrency key",
        )
        group = publish_job["concurrency"].get("group", "")
        # Must reference the release tag or version so different versions don't block each other
        self.assertTrue(
            "release_tag" in group or "version" in group,
            f"publish concurrency group must be keyed by version or release_tag, got: {group!r}",
        )


    def test_published_recovery_uses_validated_artifact_dir_not_local_artifacts(self):
        """In the published-release recovery path, brew bottle --merge must use the
        directory that was downloaded and validated from the release, not the local
        build artifacts directory. The merge step must not hardcode 'artifacts'."""
        import yaml
        with open(BOTTLES_WORKFLOW) as f:
            wf = yaml.safe_load(f)
        publish_job = wf["jobs"]["publish"]
        steps = publish_job["steps"]
        # Find the merge step
        merge_step = next(
            (s for s in steps if "brew bottle --merge" in (s.get("run") or "")),
            None,
        )
        self.assertIsNotNone(merge_step, "brew bottle --merge step not found")
        run = merge_step["run"]
        # Must not unconditionally use the local 'artifacts' directory for find
        # (that would ignore the published_artifacts in the recovery path)
        self.assertNotIn(
            'find artifacts',
            run,
            "merge step must not hardcode 'find artifacts'; it must use a variable "
            "that reflects whichever dir was validated",
        )

    def test_branch_checkout_before_formula_merge(self):
        """The bottle branch must be checked out BEFORE brew bottle --merge writes
        Formula/korvid.rb; otherwise the merge lands on main and the push may
        overwrite unrelated remote changes."""
        import yaml
        with open(BOTTLES_WORKFLOW) as f:
            wf = yaml.safe_load(f)
        publish_job = wf["jobs"]["publish"]
        steps = publish_job["steps"]
        step_names = [s.get("name", "") for s in steps]

        # Find index of branch-checkout step (the one that does git checkout -b)
        checkout_idx = next(
            (i for i, s in enumerate(steps)
             if "checkout" in (s.get("name") or "").lower()
             and "git checkout -b" in (s.get("run") or "")),
            None,
        )
        merge_idx = next(
            (i for i, s in enumerate(steps)
             if "brew bottle --merge" in (s.get("run") or "")),
            None,
        )
        self.assertIsNotNone(checkout_idx, "No step with 'git checkout -b' found in publish job")
        self.assertIsNotNone(merge_idx, "No step with 'brew bottle --merge' found in publish job")
        self.assertLess(
            checkout_idx,
            merge_idx,
            f"Branch checkout step (idx {checkout_idx}) must come before "
            f"brew bottle --merge step (idx {merge_idx})",
        )

    def test_commit_guarded_when_no_staged_difference(self):
        """The commit step must guard git commit so it does not fail when there
        is no staged difference (idempotent rerun)."""
        text = BOTTLES_WORKFLOW.read_text()
        # Must use git diff --cached or git diff --staged before committing
        self.assertTrue(
            "git diff --cached" in text or "git diff --staged" in text,
            "commit step must guard 'git commit' when there is no staged difference "
            "(use 'git diff --cached --quiet' or equivalent)",
        )

    def test_build_concurrency_includes_matrix_tag(self):
        """Build job concurrency group must include the matrix tag so both
        architecture legs can run in parallel (not serially blocked by each other)."""
        import yaml
        with open(BOTTLES_WORKFLOW) as f:
            wf = yaml.safe_load(f)
        build_job = wf["jobs"]["build"]
        concurrency = build_job.get("concurrency", {})
        group = concurrency.get("group", "")
        self.assertTrue(
            "matrix.tag" in group or "matrix.os" in group,
            f"build concurrency group must include matrix.tag or matrix.os to allow "
            f"parallel matrix legs, got: {group!r}",
        )

    def test_no_dead_json_file_assignment_in_build(self):
        """The dead 'json_file=$(ls *.bottle.json)' assignment in the verify step
        must be removed (the variable is never used after assignment)."""
        text = BOTTLES_WORKFLOW.read_text()
        self.assertNotIn(
            "json_file=$(ls *.bottle.json)",
            text,
            "Dead 'json_file=$(ls *.bottle.json)' assignment must be removed",
        )

    def test_prepare_calls_brew_info_at_most_once(self):
        """brew info --json=v2 must be called at most once in the prepare job
        (capture output and reuse instead of three separate calls)."""
        import yaml
        with open(BOTTLES_WORKFLOW) as f:
            wf = yaml.safe_load(f)
        prepare_steps = wf["jobs"]["prepare"]["steps"]
        brew_info_calls = sum(
            (s.get("run") or "").count("brew info --json=v2")
            for s in prepare_steps
        )
        self.assertLessEqual(
            brew_info_calls,
            1,
            f"brew info --json=v2 is called {brew_info_calls} times in prepare; "
            "capture once and reuse to avoid redundant calls",
        )

    def test_prepare_sets_up_homebrew(self):
        """The prepare job must invoke Homebrew/actions/setup-homebrew before
        calling any brew command so that Homebrew is always correctly
        initialised, matching what every other brew-using job does."""
        import yaml
        with open(BOTTLES_WORKFLOW) as f:
            wf = yaml.safe_load(f)
        prepare_steps = wf["jobs"]["prepare"]["steps"]
        setup_uses = [
            s.get("uses", "") for s in prepare_steps
            if "setup-homebrew" in s.get("uses", "")
        ]
        self.assertTrue(
            setup_uses,
            "prepare job is missing Homebrew/actions/setup-homebrew step; "
            "every brew-using job must set up Homebrew consistently",
        )
        # setup-homebrew must appear before the tap symlink step
        step_uses_list = [s.get("uses", "") for s in prepare_steps]
        step_run_list = [s.get("run", "") for s in prepare_steps]
        setup_idx = next(
            i for i, u in enumerate(step_uses_list) if "setup-homebrew" in u
        )
        tap_idx = next(
            i for i, r in enumerate(step_run_list) if "brew --repository" in r
        )
        self.assertLess(
            setup_idx,
            tap_idx,
            "setup-homebrew must appear before the tap-symlink step",
        )

    def test_should_build_also_checks_release_assets(self):
        """should_build must also query the GitHub Release to check whether
        bottle assets are present, not just inspect the formula metadata.
        A workflow_dispatch recovery with missing release assets must
        not be silently skipped."""
        text = BOTTLES_WORKFLOW.read_text()
        # The prepare job must call gh release to inspect assets
        import yaml
        with open(BOTTLES_WORKFLOW) as f:
            wf = yaml.safe_load(f)
        prepare_steps = wf["jobs"]["prepare"]["steps"]
        prepare_text = "".join(s.get("run", "") for s in prepare_steps)
        self.assertIn(
            "gh release",
            prepare_text,
            "prepare job must query gh release to check whether bottle assets "
            "are already present before setting should_build=false; a "
            "workflow_dispatch recovery with deleted assets must re-trigger builds",
        )

    # ------------------------------------------------------------------ #
    # Final-review contract tests: observable uploads + completeness check #
    # ------------------------------------------------------------------ #

    def test_release_upload_failures_fail_the_step(self):
        r"""A failed `gh release upload` must fail the publish step.

        `find ... -exec gh release upload ... \;` swallows each child's exit
        status: find returns 0 even when an upload fails, so the workflow can
        publish a release with missing assets and then open a formula PR that
        points at 404 download URLs. Uploads must instead run as standalone
        statements under `set -euo pipefail` (e.g. a read loop) so any nonzero
        upload aborts the step.
        """
        text = BOTTLES_WORKFLOW.read_text()
        self.assertNotIn(
            "-exec gh release upload",
            text,
            "find -exec does not propagate gh release upload failures; upload in "
            "a loop whose nonzero status fails the step under set -euo pipefail",
        )
        # Uploads must run as a standalone command inside a read loop so set -e
        # observes a failed upload (failures in a loop *condition* are ignored,
        # but a loop-*body* statement aborts the step).
        self.assertRegex(
            text,
            r"while[^\n]*read[^\n]*\n\s*gh release upload",
            "bottle uploads must run `gh release upload` as a standalone loop-body "
            "statement so a failed upload aborts the step",
        )

    def test_draft_publish_requires_remote_completeness_check(self):
        """Before publishing a draft, re-query remote assets and confirm every
        locally validated artifact basename is present.

        The completeness check must run between the upload and
        `gh release edit --draft=false`, and must cover *all* validated
        artifacts -- both the `.bottle.tar.gz` archives and their `.bottle.json`
        sidecars -- not merely the two tarballs. Otherwise a dropped upload can
        be published with missing assets.
        """
        import yaml
        with open(BOTTLES_WORKFLOW) as f:
            wf = yaml.safe_load(f)
        publish_steps = wf["jobs"]["publish"]["steps"]
        step = next(
            (s for s in publish_steps
             if "--draft=false" in (s.get("run") or "")
             and "gh release upload" in (s.get("run") or "")),
            None,
        )
        self.assertIsNotNone(
            step,
            "no publish step both uploads assets and flips the draft to published",
        )
        run = step["run"]

        upload_idx = run.index("gh release upload")
        publish_idx = run.index("--draft=false")
        self.assertLess(
            upload_idx, publish_idx,
            "assets must be uploaded before the release is published",
        )

        after_upload = run[upload_idx:publish_idx]
        # A remote re-query of the release assets must occur after uploading and
        # before publishing the draft.
        self.assertIn(
            "gh release view",
            after_upload,
            "before `--draft=false`, re-query the release assets "
            "(`gh release view --json assets`) to confirm the upload landed",
        )
        self.assertIn("--json assets", after_upload)

        # Isolate the completeness-check block (from its re-query to publish) so
        # the assertions below cannot be satisfied by the upload loop's own find.
        check_block = after_upload[after_upload.index("gh release view"):]
        self.assertIn(
            ".bottle.tar.gz",
            check_block,
            "completeness check must cover the .bottle.tar.gz archives",
        )
        self.assertIn(
            ".bottle.json",
            check_block,
            "completeness check must cover every validated artifact basename "
            "(including the .bottle.json sidecars), not only the two tarballs",
        )

    # ------------------------------------------------------------------ #
    # New contract tests for root-cause fix (task-5)                      #
    # ------------------------------------------------------------------ #

    PINNED_SHA = "a657b8b0cd35d0f65cce41fce9b24cf054b49869"

    def test_every_brew_job_has_setup_homebrew_before_tap_symlink(self):
        """Every job in bottles.yml that contains a brew command must have a
        setup-homebrew step that appears before the tap-symlink step and before
        the first brew command step."""
        import yaml

        with open(BOTTLES_WORKFLOW) as f:
            wf = yaml.safe_load(f)

        for job_name, job in wf.get("jobs", {}).items():
            steps = job.get("steps", [])
            step_runs = [s.get("run", "") for s in steps]

            # Does this job contain any brew command?
            if not any("brew " in r for r in step_runs):
                continue

            # Must have a setup-homebrew step
            setup_indices = [
                i for i, s in enumerate(steps)
                if "setup-homebrew" in s.get("uses", "")
            ]
            self.assertTrue(
                setup_indices,
                f"Job '{job_name}' runs brew commands but is missing a "
                "Homebrew/actions/setup-homebrew step",
            )
            setup_idx = setup_indices[0]

            # setup-homebrew must be before the tap-symlink step
            tap_idx = next(
                (i for i, r in enumerate(step_runs) if "brew --repository" in r),
                None,
            )
            if tap_idx is not None:
                self.assertLess(
                    setup_idx, tap_idx,
                    f"Job '{job_name}': setup-homebrew (idx {setup_idx}) must come "
                    f"before the tap-symlink step (idx {tap_idx})",
                )

            # setup-homebrew must be before the first brew command step
            first_brew_idx = next(
                i for i, r in enumerate(step_runs) if "brew " in r
            )
            self.assertLess(
                setup_idx, first_brew_idx,
                f"Job '{job_name}': setup-homebrew (idx {setup_idx}) must come "
                f"before the first brew command step (idx {first_brew_idx})",
            )

    def test_all_setup_homebrew_refs_in_bottles_yml_use_pinned_sha(self):
        """Every Homebrew/actions/setup-homebrew reference in bottles.yml must
        use the exact pinned 40-character SHA, not a mutable ref like @master."""
        import yaml

        with open(BOTTLES_WORKFLOW) as f:
            wf = yaml.safe_load(f)

        for job_name, job in wf.get("jobs", {}).items():
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                if "setup-homebrew" in uses:
                    self.assertIn(
                        self.PINNED_SHA,
                        uses,
                        f"Job '{job_name}': setup-homebrew must be pinned to SHA "
                        f"{self.PINNED_SHA!r}, got {uses!r}",
                    )
                    self.assertNotIn(
                        "@master",
                        uses,
                        f"Job '{job_name}': setup-homebrew must not use @master",
                    )


class TestTestWorkflow(unittest.TestCase):
    def test_formula_tests_preserve_source_fallback(self):
        text = TEST_WORKFLOW.read_text()
        self.assertIn("brew install --build-from-source", text)
        self.assertIn("ubuntu-latest", text)
        self.assertIn("macos-latest", text)

    def test_formula_tests_verify_bottle_without_pypi(self):
        text = TEST_WORKFLOW.read_text()
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("macos-15-intel", text)
        self.assertIn("files.pythonhosted.org", text)
        self.assertIn("brew install --verbose hellices/korvid/korvid", text)
        self.assertIn("Pouring korvid--", text)

    def test_bottle_tag_resolver_fails_on_malformed_schema(self):
        """The inline bottle-tag resolver must not silently skip when the Homebrew
        JSON schema is malformed or structurally invalid.  It must raise (exit
        non-zero) for:
          - missing 'formulae' key (empty or wrong container)
          - 'formulae' is not a list / is an empty list
          - formula entry has no 'bottle' key
          - 'bottle' exists but 'stable' is missing
          - 'stable' exists but 'files' is missing or wrong type
        Silent skip is only acceptable when 'formulae[0].bottle.stable.files'
        is a valid dict and the current runner tag is simply absent from it.
        """
        import yaml
        with open(TEST_WORKFLOW) as f:
            wf = yaml.safe_load(f)
        bottle_check_step = None
        for step in wf["jobs"]["test-bottle"]["steps"]:
            if step.get("id") == "bottle_check":
                bottle_check_step = step
                break
        self.assertIsNotNone(bottle_check_step, "bottle_check step not found in test-bottle job")
        run = bottle_check_step["run"]

        # The resolver must NOT use bare .get() chains that swallow structural errors.
        # Presence of explicit schema-validation patterns is required:
        # either KeyError/TypeError propagation (no .get on structural keys) or
        # explicit isinstance/raise guards.
        permissive_patterns = [
            # These chained .get() patterns silently return {} on any missing key
            ".get('bottle', {}).get('stable', {})",
            '.get("bottle", {}).get("stable", {})',
            ".get('bottle', {}).get('stable', {}).get('files', {})",
            '.get("bottle", {}).get("stable", {}).get("files", {})',
        ]
        for pattern in permissive_patterns:
            self.assertNotIn(
                pattern,
                run,
                f"Resolver must not use permissive chained .get() that silently "
                f"swallows schema errors; found: {pattern!r}",
            )

        # The resolver must fail explicitly: require either 'sys.exit(1)' or 'raise'
        # so structural schema violations produce a non-zero exit, not a silent skip.
        self.assertTrue(
            "sys.exit(1)" in run or "raise " in run,
            "Resolver must explicitly fail (sys.exit(1) or raise) on structural schema errors",
        )


    def test_bottle_tag_resolver_skips_on_empty_bottle(self):
        """bottle == {} is a legitimate source-only formula response.
        The resolver must set skip=true (not exit 1) in that case."""
        import yaml
        with open(TEST_WORKFLOW) as f:
            wf = yaml.safe_load(f)
        bottle_check_step = None
        for step in wf["jobs"]["test-bottle"]["steps"]:
            if step.get("id") == "bottle_check":
                bottle_check_step = step
                break
        self.assertIsNotNone(bottle_check_step, "bottle_check step not found")
        run = bottle_check_step["run"]
        # Must explicitly handle bottle == {} as a valid skip, not a schema error.
        # The phrase "bottle == {}" or equivalent empty-dict check must appear.
        self.assertTrue(
            "bottle == {}" in run or "not bottle" in run or "len(bottle) == 0" in run
            or "bottle is None or not bottle" in run,
            "Resolver must treat bottle == {} as a valid skip (source-only formula), "
            "not a schema error. Add an explicit empty-bottle check before the stable key guard.",
        )

    def test_bottle_tag_resolver_maps_arch_correctly(self):
        """arm64 -> arm64_${base}, x86_64 -> ${base} (NOT base_x86_64)."""
        import yaml
        with open(TEST_WORKFLOW) as f:
            wf = yaml.safe_load(f)
        bottle_check_step = None
        for step in wf["jobs"]["test-bottle"]["steps"]:
            if step.get("id") == "bottle_check":
                bottle_check_step = step
                break
        self.assertIsNotNone(bottle_check_step, "bottle_check step not found")
        run = bottle_check_step["run"]
        # arm64 tag is arm64_${base}, x86_64 tag is bare ${base}
        self.assertIn(
            "arm64_",
            run,
            "Resolver must produce arm64_<base> for arm64 architecture",
        )
        # Must NOT produce base_x86_64 (wrong order)
        self.assertNotIn(
            "base + suffix",
            run,
            "Resolver must not append suffix to base (produces sequoia_x86_64); "
            "use prefix for arm64 instead",
        )
        # The correct pattern is 'arm64_' + base for arm64
        self.assertTrue(
            "'arm64_' + base" in run or '"arm64_" + base' in run
            or "f'arm64_{base}'" in run or 'f"arm64_{base}"' in run,
            "Resolver must compute arm64 tag as 'arm64_' + base (e.g. arm64_sequoia)",
        )


if __name__ == "__main__":
    unittest.main()
