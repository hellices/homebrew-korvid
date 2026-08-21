"""Contract tests for .github/workflows/bottles.yml."""
from __future__ import annotations

import unittest
from pathlib import Path

BOTTLES_WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "bottles.yml"


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


if __name__ == "__main__":
    unittest.main()
