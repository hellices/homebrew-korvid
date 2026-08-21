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


if __name__ == "__main__":
    unittest.main()
