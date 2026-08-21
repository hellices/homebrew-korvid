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


if __name__ == "__main__":
    unittest.main()
