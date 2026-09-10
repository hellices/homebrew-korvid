"""Execute the bottle checkout step with local, read-only Git/PR fixtures."""

from __future__ import annotations

import json
import os
import subprocess
import unittest

from test_workflows import BOTTLES_WORKFLOW, load_workflow


BRANCH = "bottles-korvid-0.5.0"
HEAD_SHA = "a" * 40
REPOSITORY = "hellices/homebrew-korvid"

# Only API transport and Git effects are replaced; the workflow's actual jq
# projection and shell control flow run unchanged. Unexpected commands fail.
COMMAND_FIXTURES = r"""
git() {
  case "$*" in
    "ls-remote --exit-code --heads origin $BRANCH")
      return "$REMOTE_STATUS" ;;
    "fetch origin $BRANCH:refs/remotes/origin/$BRANCH")
      return "$FETCH_STATUS" ;;
    "diff --name-only origin/main origin/$BRANCH")
      printf '%s\n' "$CHANGED_FILES" ;;
    "rev-parse origin/$BRANCH")
      printf '%s\n' "$HEAD_SHA" ;;
    "checkout -b $BRANCH origin/$BRANCH" | "checkout -b $BRANCH origin/main")
      printf 'CHECKOUT %s\n' "$*" ;;
    *)
      printf 'Unexpected git command: %s\n' "$*" >&2
      return 99 ;;
  esac
}
gh() {
  if [ "$API_STATUS" -ne 0 ]; then
    return "$API_STATUS"
  fi
  if [ "$1" != api ] || [ "$3" != --jq ] || [ "$#" -ne 4 ]; then
    printf 'Unexpected gh command: %s\n' "$*" >&2
    return 99
  fi
  python3 -S -c '
import json, os, sys
from urllib.parse import parse_qs, urlsplit
endpoint = urlsplit(sys.argv[1])
assert endpoint.path == "repos/" + os.environ["GITHUB_REPOSITORY"] + "/pulls"
query = parse_qs(endpoint.query)
assert query["head"] == ["hellices:" + os.environ["BRANCH"]]
pulls = json.loads(os.environ["PULLS_JSON"])
state = query.get("state", ["open"])[0]
base = query.get("base")
json.dump([
    pull for pull in pulls
    if (state == "all" or pull["state"] == state)
    and (base is None or pull["base"]["ref"] == base[0])
], sys.stdout)
' "$2" | jq -r "$4"
}
"""


def pull(
    number: int,
    *,
    author: str | None = "delivery[bot]",
    state: str = "open",
    base: str = "main",
    head: str = HEAD_SHA,
) -> dict:
    return {
        "number": number,
        "state": state,
        "user": {"login": author},
        "base": {"ref": base},
        "head": {"sha": head},
    }


class TestBottleBranchOwnership(unittest.TestCase):
    def checkout(
        self, pulls: list[dict], **environment: str
    ) -> subprocess.CompletedProcess[str]:
        steps = load_workflow(BOTTLES_WORKFLOW)["jobs"]["publish"]["steps"]
        checkout = next(
            step["run"]
            for step in steps
            if step.get("name") == "Checkout bottle branch"
        )
        return subprocess.run(
            ["bash", "-c", COMMAND_FIXTURES + checkout],
            cwd=BOTTLES_WORKFLOW.parents[2],
            env={
                "PATH": os.environ["PATH"],
                "VERSION": "0.5.0",
                "APP_SLUG": "delivery",
                "GITHUB_REPOSITORY": REPOSITORY,
                "GITHUB_ENV": "/dev/stdout",
                "BRANCH": BRANCH,
                "HEAD_SHA": HEAD_SHA,
                "PULLS_JSON": json.dumps(pulls),
                "CHANGED_FILES": "Formula/korvid.rb",
                "REMOTE_STATUS": "0",
                "FETCH_STATUS": "0",
                "API_STATUS": "0",
                **environment,
            },
            capture_output=True,
            text=True,
        )

    def test_reopened_app_pr_wins_over_newer_closed_human_pr(self) -> None:
        result = self.checkout(
            [pull(22, author="maintainer", state="closed"), pull(11)]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"CHECKOUT checkout -b {BRANCH} origin/{BRANCH}", result.stdout)
        self.assertIn("RESUMED_BOTTLE_BRANCH=true", result.stdout)

    def test_open_main_pr_wins_over_newer_pr_targeting_another_base(self) -> None:
        result = self.checkout(
            [pull(22, author="maintainer", base="development"), pull(11)]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESUMED_BOTTLE_BRANCH=true", result.stdout)

    def test_closed_app_pr_cannot_authorize_an_open_human_pr(self) -> None:
        result = self.checkout(
            [pull(22, state="closed"), pull(11, author="maintainer")]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not owned by the delivery App", result.stderr)
        self.assertNotIn("CHECKOUT", result.stdout)

    def test_closed_app_pr_at_current_head_allows_no_open_pr_retry(self) -> None:
        result = self.checkout([pull(22, state="closed")])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESUMED_BOTTLE_BRANCH=true", result.stdout)

    def test_no_open_pr_retry_requires_existing_ownership_provenance(self) -> None:
        for pulls in (
            [],
            [pull(22, author="maintainer", state="closed")],
            [pull(22, state="closed", head="b" * 40)],
            [pull(22, state="closed", base="development")],
        ):
            with self.subTest(pulls=pulls):
                result = self.checkout(pulls)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("not owned by the delivery App", result.stderr)
                self.assertNotIn("CHECKOUT", result.stdout)

    def test_historical_app_pr_cannot_override_newer_human_ownership(self) -> None:
        result = self.checkout(
            [
                pull(22, author="maintainer", state="closed"),
                pull(11, state="closed"),
            ]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("CHECKOUT", result.stdout)

    def test_missing_open_pr_author_cannot_fall_back_to_closed_app_pr(self) -> None:
        result = self.checkout([pull(22, state="closed"), pull(11, author=None)])
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("CHECKOUT", result.stdout)

    def test_unrelated_branch_changes_are_rejected_before_checkout(self) -> None:
        result = self.checkout([pull(11)], CHANGED_FILES="Formula/korvid.rb\nREADME.md")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a formula-only update", result.stderr)
        self.assertNotIn("CHECKOUT", result.stdout)

    def test_transport_errors_do_not_create_a_fresh_branch(self) -> None:
        for environment in (
            {"REMOTE_STATUS": "128"},
            {"FETCH_STATUS": "128"},
            {"API_STATUS": "1"},
        ):
            with self.subTest(environment=environment):
                result = self.checkout([pull(11)], **environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("CHECKOUT", result.stdout)

    def test_absent_remote_branch_starts_from_main(self) -> None:
        result = self.checkout([], REMOTE_STATUS="2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"CHECKOUT checkout -b {BRANCH} origin/main", result.stdout)
        self.assertNotIn("RESUMED_BOTTLE_BRANCH=true", result.stdout)

    def test_pr_creation_lookup_targets_same_main_base(self) -> None:
        steps = load_workflow(BOTTLES_WORKFLOW)["jobs"]["publish"]["steps"]
        create = next(
            step["run"]
            for step in steps
            if step.get("name") == "Open pull request if none exists"
        )
        lookup = next(line for line in create.splitlines() if "gh pr list" in line)
        self.assertIn("--base main", lookup)


if __name__ == "__main__":
    unittest.main()
