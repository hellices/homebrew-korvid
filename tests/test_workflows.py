"""Contract tests for .github/workflows/bottles.yml and test.yml.

Standard library only: workflow YAML is parsed through the runner-provided
Ruby/Psych toolchain (see ``load_workflow``), never PyYAML, so this suite runs
under ``python3 -S`` with no site-packages.
"""
from __future__ import annotations

import ast
import json
import subprocess
import unittest
from functools import lru_cache
from pathlib import Path

BOTTLES_WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "bottles.yml"
TEST_WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "test.yml"

# Ruby one-liner: read a YAML file and print it as JSON on stdout. Psych and JSON
# are part of Ruby's standard library, and this repository already requires Ruby
# for `ruby -c Formula/korvid.rb` and Psych-based workflow validation, so parsing
# YAML this way introduces no third-party (PyYAML) dependency.
_RUBY_YAML_TO_JSON = (
    'require "psych"; require "json"; '
    "print JSON.generate(Psych.safe_load(File.read(ARGV[0])))"
)


@lru_cache(maxsize=None)
def _workflow_json(path: str) -> str:
    """Return the JSON serialization of a YAML file, via runner-provided Ruby.

    Cached so Ruby is spawned at most once per workflow file across the suite.
    """
    completed = subprocess.run(
        ["ruby", "-e", _RUBY_YAML_TO_JSON, path],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def load_workflow(path) -> dict:
    """Parse a workflow YAML file into Python objects without PyYAML.

    Returns a fresh object on every call (the cache holds the JSON text, not the
    decoded structure), so callers may read the result freely without any
    cross-test coupling.
    """
    return json.loads(_workflow_json(str(path)))


class TestBottlesWorkflow(unittest.TestCase):
    def test_bottle_workflow_builds_both_macos_architectures(self):
        text = BOTTLES_WORKFLOW.read_text()
        self.assertIn("macos-15", text)
        self.assertIn("macos-15-intel", text)
        self.assertIn("brew install --build-bottle", text)
        self.assertIn("brew bottle --json", text)

    def test_bottle_build_disables_rebuild_suffix(self):
        """Post-merge bottling sees the same version at origin/HEAD, so Homebrew
        otherwise emits rebuild 1 filenames that the release contract does not
        use."""
        wf = load_workflow(BOTTLES_WORKFLOW)
        build_step = next(
            step
            for step in wf["jobs"]["build"]["steps"]
            if step.get("name") == "Build bottle"
        )

        self.assertIn("--no-rebuild", build_step["run"])

    def test_bottle_workflow_validates_before_publishing(self):
        text = BOTTLES_WORKFLOW.read_text()
        self.assertLess(
            text.index("validate_bottle_artifacts.py"),
            text.index("gh release create"),
        )
        self.assertIn("brew bottle --merge --write --no-commit", text)

    def test_bottle_workflow_opens_but_never_merges_a_pr(self):
        wf = load_workflow(BOTTLES_WORKFLOW)
        run_scripts = "\n".join(
            step.get("run") or ""
            for job in wf["jobs"].values()
            for step in job.get("steps", [])
        )

        self.assertIn("gh pr create", run_scripts)
        self.assertIn("gh workflow run test.yml", run_scripts)
        self.assertNotIn("gh pr merge", run_scripts)

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

    def test_checkout_persist_credentials_scoped_by_job(self):
        """actions/checkout persists the job's GITHUB_TOKEN in the workspace git
        config by default. `prepare` and `build` never push, so they must disable
        it. The publish job's setup-homebrew step replaces the checkout's .git,
        so it must receive the token itself to persist git authentication."""
        wf = load_workflow(BOTTLES_WORKFLOW)
        jobs = wf["jobs"]

        def checkout_step(job_name):
            steps = jobs[job_name]["steps"]
            step = next(
                (s for s in steps if "actions/checkout" in (s.get("uses") or "")),
                None,
            )
            self.assertIsNotNone(
                step, f"{job_name} job has no actions/checkout step"
            )
            return step

        for job_name in ("prepare", "build"):
            with_block = checkout_step(job_name).get("with") or {}
            self.assertIn(
                "persist-credentials",
                with_block,
                f"{job_name} checkout must set persist-credentials: false "
                "(it never pushes; leave no usable token in the workspace git "
                "config)",
            )
            self.assertIs(
                with_block["persist-credentials"],
                False,
                f"{job_name} checkout must set persist-credentials: false, got "
                f"{with_block['persist-credentials']!r}",
            )

        publish_steps = jobs["publish"]["steps"]
        publish_setup = next(
            step
            for step in publish_steps
            if "setup-homebrew" in (step.get("uses") or "")
        )
        self.assertEqual(
            publish_setup.get("with", {}).get("token"),
            "${{ secrets.GITHUB_TOKEN }}",
            "publish setup-homebrew replaces the checkout repository and must "
            "persist a token that survives for git push",
        )

    def test_publish_job_has_concurrency_group(self):
        """The publish job must declare its own concurrency group keyed by version
        so two workflow runs for the same version cannot race in the publish step."""
        wf = load_workflow(BOTTLES_WORKFLOW)
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
        wf = load_workflow(BOTTLES_WORKFLOW)
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

    def test_formula_merge_requires_exactly_two_validated_json_files(self):
        wf = load_workflow(BOTTLES_WORKFLOW)
        merge_step = next(
            step
            for step in wf["jobs"]["publish"]["steps"]
            if "brew bottle --merge" in (step.get("run") or "")
        )
        run = merge_step["run"]

        cardinality_check = '"${#json_paths[@]}" -ne 2'
        self.assertIn(cardinality_check, run)
        self.assertLess(
            run.index(cardinality_check),
            run.index("brew bottle --merge"),
        )

    def test_branch_checkout_before_formula_merge(self):
        """The bottle branch must be checked out BEFORE brew bottle --merge writes
        Formula/korvid.rb; otherwise the merge lands on main and the push may
        overwrite unrelated remote changes."""
        wf = load_workflow(BOTTLES_WORKFLOW)
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

    def test_generated_commit_uses_separate_trailer_message(self):
        wf = load_workflow(BOTTLES_WORKFLOW)
        commit_step = next(
            step
            for step in wf["jobs"]["publish"]["steps"]
            if step.get("name") == "Commit and push bottle branch"
        )

        self.assertIn(
            '-m "Co-authored-by: github-actions[bot]',
            commit_step["run"],
        )

    def test_existing_pr_lookup_emits_empty_for_no_match(self):
        wf = load_workflow(BOTTLES_WORKFLOW)
        pr_step = next(
            step
            for step in wf["jobs"]["publish"]["steps"]
            if step.get("name") == "Open pull request if none exists"
        )

        self.assertIn(".[0].number // empty", pr_step["run"])

    def test_existing_pr_lookup_propagates_gh_failures(self):
        wf = load_workflow(BOTTLES_WORKFLOW)
        pr_step = next(
            step
            for step in wf["jobs"]["publish"]["steps"]
            if step.get("name") == "Open pull request if none exists"
        )
        lookup = next(
            line for line in pr_step["run"].splitlines() if "gh pr list" in line
        )

        self.assertNotIn("|| true", lookup)

    def test_build_concurrency_includes_matrix_tag(self):
        """Build job concurrency group must include the matrix tag so both
        architecture legs can run in parallel (not serially blocked by each other)."""
        wf = load_workflow(BOTTLES_WORKFLOW)
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

    def test_build_requires_exactly_one_bottle_json(self):
        wf = load_workflow(BOTTLES_WORKFLOW)
        verify_step = next(
            step
            for step in wf["jobs"]["build"]["steps"]
            if step.get("name") == "Verify bottle tag matches matrix"
        )
        run = verify_step["run"]

        self.assertIn("json_files", run)
        self.assertIn("len(json_files) != 1", run)
        self.assertNotIn(
            'next(f for f in os.listdir(".") if f.endswith(".bottle.json"))',
            run,
        )

    def test_prepare_calls_brew_info_at_most_once(self):
        """brew info --json=v2 must be called at most once in the prepare job
        (capture output and reuse instead of three separate calls)."""
        wf = load_workflow(BOTTLES_WORKFLOW)
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
        wf = load_workflow(BOTTLES_WORKFLOW)
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
        every bottle archive and JSON sidecar is present, not just inspect the
        formula metadata. If an already-published release is incomplete, fail
        before the matrix build because immutable assets cannot be repaired."""
        wf = load_workflow(BOTTLES_WORKFLOW)
        prepare_steps = wf["jobs"]["prepare"]["steps"]
        prepare_text = "".join(s.get("run", "") for s in prepare_steps)
        self.assertIn(
            "gh release",
            prepare_text,
            "prepare job must query gh release to check whether bottle assets "
            "are already present before setting should_build=false",
        )
        for expected in (
            "arm64_sequoia.bottle.tar.gz",
            "sequoia.bottle.tar.gz",
            "arm64_sequoia.bottle.json",
            "sequoia.bottle.json",
        ):
            self.assertIn(
                expected,
                prepare_text,
                f"prepare must require release asset {expected!r} before "
                "setting should_build=false",
            )
        self.assertIn(
            'if [ "$RELEASE_HAS_ASSETS" != "true" ]',
            prepare_text,
        )
        incomplete_idx = prepare_text.index(
            'if [ "$RELEASE_HAS_ASSETS" != "true" ]'
        )
        formula_done_idx = prepare_text.index("should_build=false")
        self.assertIn("exit 1", prepare_text[incomplete_idx:formula_done_idx])
        self.assertNotIn("2>/dev/null", prepare_text)
        self.assertIn("cat \"$release_error\"", prepare_text)

    def test_release_lookup_only_treats_not_found_as_absent(self):
        wf = load_workflow(BOTTLES_WORKFLOW)
        publish_run = next(
            step["run"]
            for step in wf["jobs"]["publish"]["steps"]
            if step.get("name") == "Determine release state and validate/publish"
        )

        self.assertIn("release not found", publish_run)
        self.assertIn("cat \"$release_error\"", publish_run)
        self.assertNotIn('|| echo "absent"', publish_run)
        self.assertNotIn("2>/dev/null", publish_run)

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
        wf = load_workflow(BOTTLES_WORKFLOW)
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
    # Remote-filename fix: stage single-dash assets before upload          #
    # ------------------------------------------------------------------ #

    def _publish_validate_step_run(self):
        """Return the run script of the publish step that both uploads assets
        and flips the draft to published."""
        wf = load_workflow(BOTTLES_WORKFLOW)
        step = next(
            (
                s
                for s in wf["jobs"]["publish"]["steps"]
                if "gh release upload" in (s.get("run") or "")
                and "--draft=false" in (s.get("run") or "")
            ),
            None,
        )
        self.assertIsNotNone(
            step, "no publish step both uploads assets and flips the draft to published"
        )
        return step["run"]

    def _staged_dir_variable(self, run):
        """Extract the shell variable name passed to the validator --stage-dir."""
        import re

        match = re.search(r"--stage-dir\s+\"?\$\{?(\w+)\}?\"?", run)
        self.assertIsNotNone(
            match,
            "publish must stage remote-named assets with "
            "validate_bottle_artifacts.py --stage-dir <dir>",
        )
        return match.group(1)

    def test_publish_stages_remote_named_assets_before_upload(self):
        """GitHub Releases serve the single-dash remote `filename`, not the
        double-dash `local_filename` Homebrew writes to disk. The publish step
        must therefore build a staged upload directory (validator --stage-dir)
        and upload *from that staged directory*, not from the raw build
        artifacts whose archives carry the double-dash name and would 404."""
        import re

        run = self._publish_validate_step_run()

        # Staging must be produced by the validator, before any upload.
        self.assertIn(
            "--stage-dir",
            run,
            "publish must stage remote-named assets via "
            "validate_bottle_artifacts.py --stage-dir",
        )
        self.assertLess(
            run.index("--stage-dir"),
            run.index("gh release upload"),
            "staging must precede upload",
        )

        staged_var = self._staged_dir_variable(run)

        # The read-loop that performs the upload must be fed by a `find` over
        # the staged directory ...
        self.assertRegex(
            run,
            r"find\s+\"\$" + re.escape(staged_var) + r"\"[^\n]*-print0",
            f"the upload loop's find must walk the staged dir (${staged_var})",
        )
        # ... and must not walk the raw build-artifacts dir, whose archives use
        # the double-dash local_filename that 404s on the release root_url.
        self.assertNotRegex(
            run,
            r"find\s+\"\$artifact_dir\"[^\n]*-print0",
            "upload must read the staged remote-named dir, not the raw "
            "build-artifacts dir",
        )

    def test_completeness_check_compares_staged_directory(self):
        """The pre-publish completeness check must compare the remote asset
        names against every *staged* asset basename (the names actually
        uploaded), not the raw build-artifact basenames."""
        run = self._publish_validate_step_run()
        staged_var = self._staged_dir_variable(run)

        upload_idx = run.index("gh release upload")
        publish_idx = run.index("--draft=false")
        after_upload = run[upload_idx:publish_idx]
        check_block = after_upload[after_upload.index("gh release view") :]
        self.assertIn(
            staged_var,
            check_block,
            f"completeness check must compare remote assets against the staged "
            f"dir (${staged_var}), got: {check_block!r}",
        )

    # ------------------------------------------------------------------ #
    # New contract tests for root-cause fix (task-5)                      #
    # ------------------------------------------------------------------ #

    PINNED_SHA = "a657b8b0cd35d0f65cce41fce9b24cf054b49869"

    def test_every_brew_job_has_setup_homebrew_before_tap_symlink(self):
        """Every job in bottles.yml that contains a brew command must have a
        setup-homebrew step that appears before the tap-symlink step and before
        the first brew command step."""
        wf = load_workflow(BOTTLES_WORKFLOW)

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
        wf = load_workflow(BOTTLES_WORKFLOW)

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
    PINNED_SETUP_HOMEBREW_SHA = "a657b8b0cd35d0f65cce41fce9b24cf054b49869"
    PINNED_CHECKOUT_SHA = "11bd71901bbe5b1630ceea73d27597364c9af683"

    def test_checkout_refs_use_pinned_sha(self):
        wf = load_workflow(TEST_WORKFLOW)
        refs = [
            step["uses"]
            for job in wf["jobs"].values()
            for step in job.get("steps", [])
            if "actions/checkout" in (step.get("uses") or "")
        ]

        self.assertTrue(refs, "test workflow must check out the repository")
        for ref in refs:
            self.assertEqual(ref, "actions/checkout@" + self.PINNED_CHECKOUT_SHA)

    def test_setup_homebrew_refs_use_pinned_sha(self):
        wf = load_workflow(TEST_WORKFLOW)
        refs = [
            step["uses"]
            for job in wf["jobs"].values()
            for step in job.get("steps", [])
            if "setup-homebrew" in (step.get("uses") or "")
        ]

        self.assertTrue(refs, "test workflow must set up Homebrew")
        for ref in refs:
            self.assertEqual(
                ref,
                "Homebrew/actions/setup-homebrew@"
                + self.PINNED_SETUP_HOMEBREW_SHA,
            )

    def test_formula_tests_preserve_source_fallback(self):
        text = TEST_WORKFLOW.read_text()
        self.assertIn("brew install --build-from-source", text)
        self.assertIn("ubuntu-latest", text)
        self.assertIn("macos-latest", text)

    def test_formula_tests_verify_bottle_without_pypi(self):
        wf = load_workflow(TEST_WORKFLOW)
        text = TEST_WORKFLOW.read_text()
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("macos-15-intel", text)
        self.assertIn("files.pythonhosted.org", text)
        self.assertIn("brew install --verbose hellices/korvid/korvid", text)
        steps = wf["jobs"]["test-bottle"]["steps"]
        check_run = next(step["run"] for step in steps if step.get("id") == "bottle_check")
        verify_step = next(
            step
            for step in steps
            if step.get("name") == "Verify bottle was poured"
        )
        verify_run = verify_step["run"]
        self.assertIn("filename=$bottle_filename", check_run)
        self.assertEqual(
            verify_step["env"]["BOTTLE_FILENAME"],
            "${{ steps.bottle_check.outputs.filename }}",
        )
        self.assertNotIn("${{", verify_run)
        self.assertIn("$BOTTLE_FILENAME", verify_run)
        self.assertIn("Pouring ", verify_run)
        self.assertNotIn("Pouring korvid--", text)

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
        wf = load_workflow(TEST_WORKFLOW)
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
        wf = load_workflow(TEST_WORKFLOW)
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
        wf = load_workflow(TEST_WORKFLOW)
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

    def test_bottle_tag_resolver_fails_on_unknown_macos_major(self):
        wf = load_workflow(TEST_WORKFLOW)
        bottle_check = next(
            step
            for step in wf["jobs"]["test-bottle"]["steps"]
            if step.get("id") == "bottle_check"
        )
        run = bottle_check["run"]

        self.assertIn("if major not in tag_map", run)
        self.assertIn("sys.exit(1)", run[run.index("if major not in tag_map") :])


class TestSuiteHygiene(unittest.TestCase):
    """Guard the no-PyYAML contract so the suite keeps running under
    ``python3 -S`` (no site-packages)."""

    def test_workflow_tests_do_not_import_pyyaml(self):
        """These tests must parse workflow YAML through the runner-provided
        Ruby/Psych helper, never PyYAML. Reintroducing ``import yaml`` (an
        undeclared, site-packages-only dependency) breaks ``python3 -S`` runs;
        catch it here with a clear message instead of a bare ImportError."""
        tree = ast.parse(Path(__file__).read_text())
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [
                    alias.name
                    for alias in node.names
                    if alias.name.split(".")[0] == "yaml"
                ]
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] == "yaml":
                    offenders.append(node.module)
        self.assertEqual(
            offenders,
            [],
            "test_workflows.py must not import PyYAML; parse workflow YAML via "
            f"the runner-provided Ruby/Psych helper instead. Found: {offenders}",
        )

    def test_load_workflow_helper_matches_direct_ruby_parse(self):
        """The load_workflow helper must faithfully reproduce the workflow
        structure (round-tripped through Ruby/Psych -> JSON), so tests that rely
        on it observe the same jobs a direct parse would."""
        for path in (BOTTLES_WORKFLOW, TEST_WORKFLOW):
            wf = load_workflow(path)
            self.assertIsInstance(wf, dict)
            self.assertIn("jobs", wf)
            self.assertTrue(wf["jobs"], f"{path} parsed with no jobs")


if __name__ == "__main__":
    unittest.main()
