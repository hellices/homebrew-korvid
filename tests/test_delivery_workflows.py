from __future__ import annotations

import unittest
from pathlib import Path

from test_workflows import BOTTLES_WORKFLOW, TEST_WORKFLOW, load_workflow


WORKFLOW = Path(__file__).parent.parent / ".github/workflows/automatic-delivery.yml"
TOKEN_ACTION = (
    "actions/create-github-app-token@fee1f7d63c2ff003460e3d139729b119787bc349"
)


class TestDeliveryWorkflow(unittest.TestCase):
    def test_delivery_workflow_is_default_branch_only_and_read_only_until_qualified(
        self,
    ):
        self.assertTrue(WORKFLOW.exists(), "automatic delivery workflow is missing")
        workflow = load_workflow(WORKFLOW)
        events = workflow.get("on", workflow.get("true", {}))
        self.assertEqual(
            events["workflow_run"],
            {"workflows": ["Test formula"], "types": ["completed"]},
        )
        self.assertNotIn("pull_request_target", events)
        self.assertEqual(
            workflow["permissions"],
            {
                "contents": "read",
                "actions": "read",
                "pull-requests": "read",
            },
        )
        job = workflow["jobs"]["deliver"]
        self.assertIn("github.ref == 'refs/heads/main'", job["if"])
        self.assertIn("vars.HOMEBREW_APP_SLUG != ''", job["if"])
        steps = job["steps"]
        checkout = next(
            step
            for step in steps
            if step.get("uses", "").startswith("actions/checkout@")
        )
        self.assertEqual(checkout["with"]["ref"], "main")
        self.assertIs(checkout["with"]["persist-credentials"], False)
        token_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("uses") == TOKEN_ACTION
        )
        qualify_index = next(
            index for index, step in enumerate(steps) if step.get("id") == "qualify"
        )
        self.assertLess(qualify_index, token_index)
        self.assertIn("steps.qualify.outputs.head != ''", steps[token_index]["if"])
        self.assertIn("inputs.dry_run", steps[token_index]["if"])
        self.assertTrue(events["workflow_dispatch"]["inputs"]["dry_run"]["default"])

    def test_merge_token_is_scoped_to_tap_contents_and_pull_requests(self):
        self.assertTrue(WORKFLOW.exists(), "automatic delivery workflow is missing")
        steps = load_workflow(WORKFLOW)["jobs"]["deliver"]["steps"]
        token = next(step for step in steps if step.get("uses") == TOKEN_ACTION)
        self.assertEqual(
            token["with"],
            {
                "app-id": "${{ vars.HOMEBREW_APP_ID }}",
                "private-key": "${{ secrets.HOMEBREW_APP_PRIVATE_KEY }}",
                "owner": "hellices",
                "repositories": "homebrew-korvid",
                "permission-contents": "write",
                "permission-pull-requests": "write",
            },
        )
        merge = next(
            step for step in steps if step.get("name") == "Merge qualified head"
        )
        self.assertEqual(
            merge["env"]["GH_TOKEN"], "${{ steps.app-token.outputs.token }}"
        )
        self.assertIn('--merge-head "$HEAD_SHA"', merge["run"])
        self.assertNotIn("--admin", merge["run"])
        self.assertNotIn("--auto", merge["run"])
        self.assertIn('[ "$APP_SLUG" = "$EXPECTED_APP_SLUG" ]', merge["run"])

    def test_bottle_push_and_pr_use_late_app_credentials_without_competing_header(self):
        steps = load_workflow(BOTTLES_WORKFLOW)["jobs"]["publish"]["steps"]
        names = [step.get("name") for step in steps]
        tokens = [step for step in steps if step.get("uses") == TOKEN_ACTION]
        self.assertEqual(len(tokens), 1, "bottle handoff needs an App token")
        token = tokens[0]
        self.assertGreater(
            steps.index(token),
            names.index("Determine release state and validate/publish"),
        )
        setup = steps[names.index("Set up Homebrew")]
        self.assertNotIn("token", setup.get("with", {}))
        for name in (
            "Checkout bottle branch",
            "Commit and push bottle branch",
            "Open pull request if none exists",
        ):
            step = steps[names.index(name)]
            self.assertEqual(
                step["env"]["GH_TOKEN"], "${{ steps.app-token.outputs.token }}"
            )
            self.assertEqual(step["if"], "steps.delivery.outputs.needed == 'true'")
        configure = steps[names.index("Configure delivery App Git identity")]
        self.assertIn("gh auth setup-git", configure["run"])
        self.assertIn("BOT_ID", configure["run"])
        self.assertNotIn(
            "Do not merge this PR without human review.", BOTTLES_WORKFLOW.read_text()
        )
        checkout = steps[names.index("Checkout bottle branch")]["run"]
        self.assertIn("gh api", checkout)
        self.assertIn(".user.login", checkout)
        self.assertNotIn(".author.login", checkout)

    def test_publication_checks_current_main_before_resuming_branch(self):
        steps = load_workflow(BOTTLES_WORKFLOW)["jobs"]["publish"]["steps"]
        names = [step.get("name") for step in steps]
        self.assertIn("Guard against stale bottle delivery", names)
        guard = steps[names.index("Guard against stale bottle delivery")]
        self.assertEqual(guard["id"], "delivery")
        self.assertIn("bash scripts/check_bottle_main.sh", guard["run"])
        self.assertLess(
            names.index("Guard against stale bottle delivery"),
            names.index("Checkout bottle branch"),
        )
        push = steps[names.index("Commit and push bottle branch")]["run"]
        self.assertIn("git push origin", push)
        self.assertNotIn("--force", push)

    def test_stale_build_cannot_write_or_publish_a_release(self):
        steps = load_workflow(BOTTLES_WORKFLOW)["jobs"]["publish"]["steps"]
        publish = next(
            step["run"]
            for step in steps
            if step.get("name") == "Determine release state and validate/publish"
        )
        guard = "bash scripts/check_bottle_main.sh"
        self.assertEqual(publish.count(guard), 2)
        self.assertLess(publish.index(guard), publish.index("gh release create"))
        self.assertLess(publish.index("gh release upload"), publish.rindex(guard))
        self.assertLess(
            publish.rindex(guard),
            publish.index('gh release edit "$RELEASE_TAG" --draft=false'),
        )

    def test_policy_regressions_run_in_ci(self):
        jobs = load_workflow(TEST_WORKFLOW)["jobs"]
        self.assertIn("policy-tests", jobs)
        commands = "\n".join(
            step.get("run", "") for step in jobs["policy-tests"]["steps"]
        )
        self.assertIn("python3 -S -m unittest discover -s tests -q", commands)


if __name__ == "__main__":
    unittest.main()
