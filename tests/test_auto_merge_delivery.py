from __future__ import annotations

import copy
import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from test_delivery_policy import TAP, formula, release


SCRIPTS = Path(__file__).parent.parent / "scripts"
SCRIPT = SCRIPTS / "auto_merge_delivery.py"
CHECKS = {
    "test (macos-latest)",
    "test (ubuntu-latest)",
    "test-bottle (macos-15)",
    "test-bottle (macos-15-intel)",
}


def pr():
    return {
        "number": 12,
        "state": "open",
        "draft": False,
        "changed_files": 1,
        "user": {"login": "delivery[bot]", "type": "Bot"},
        "head": {
            "ref": "bump-korvid-0.5.0",
            "sha": "a" * 40,
            "repo": {"full_name": TAP},
        },
        "base": {"ref": "main", "sha": "b" * 40, "repo": {"full_name": TAP}},
        "mergeable": True,
        "mergeable_state": "clean",
    }


def run():
    return {
        "id": 42,
        "path": ".github/workflows/test.yml",
        "run_attempt": 1,
        "event": "pull_request",
        "head_sha": "a" * 40,
        "head_branch": "bump-korvid-0.5.0",
        "head_repository": {"full_name": TAP},
        "status": "completed",
        "conclusion": "success",
        "pull_requests": [{"number": 12}],
    }


def jobs():
    return [
        {"name": name, "status": "completed", "conclusion": "success"}
        for name in CHECKS
    ]


def rules():
    return [
        {
            "type": "pull_request",
            "ruleset_id": 1,
            "parameters": {"required_review_thread_resolution": True},
        },
        {
            "type": "required_status_checks",
            "ruleset_id": 1,
            "parameters": {
                "strict_required_status_checks_policy": True,
                "required_status_checks": [{"context": name} for name in CHECKS],
            },
        },
    ]


class FakeGitHub:
    def __init__(self):
        self.pull = pr()
        self.current_run = run()
        self.current_jobs = jobs()
        self.current_rules = rules()
        self.ruleset = {"enforcement": "active", "current_user_can_bypass": "never"}
        self.main_sha = "b" * 40
        self.released_formula = formula()
        self.changed_files = [{"filename": "Formula/korvid.rb", "status": "modified"}]
        self.calls = []

    def api(self, endpoint):
        self.calls.append(endpoint)
        routes = {
            f"repos/{TAP}/actions/runs/42": self.current_run,
            f"repos/{TAP}/pulls?state=open&head=hellices%3Abump-korvid-0.5.0&base=main&per_page=100": [
                self.pull
            ],
            f"repos/{TAP}/pulls/12": self.pull,
            f"repos/{TAP}/pulls/12/files?per_page=100": self.changed_files,
            f"repos/{TAP}/commits/main": {"sha": self.main_sha},
            f"repos/{TAP}/rules/branches/main?per_page=100": self.current_rules,
            f"repos/{TAP}/rulesets/1": self.ruleset,
            f"repos/{TAP}/actions/workflows/test.yml/runs?event=pull_request&head_sha={'a' * 40}&per_page=100": {
                "workflow_runs": [self.current_run]
            },
            f"repos/{TAP}/actions/runs/42/attempts/1/jobs?per_page=100": {
                "total_count": len(self.current_jobs),
                "jobs": self.current_jobs,
            },
            "repos/hellices/korvid/releases/tags/v0.5.0": release(prefix="v"),
        }
        return copy.deepcopy(routes[endpoint])

    def contents(self, repository, sha):
        if repository != TAP:
            raise AssertionError("unexpected repository")
        return formula("0.4.1") if sha == "b" * 40 else formula()

    def asset(self, repository, published, name):
        if (repository, name) != ("hellices/korvid", "korvid.rb"):
            raise AssertionError("unexpected asset")
        return self.released_formula.encode()


class TestAutoMergeDelivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SCRIPT.exists():
            sys.path.insert(0, str(SCRIPTS))
            try:
                spec = importlib.util.spec_from_file_location(
                    "auto_merge_delivery", SCRIPT
                )
                cls.module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(cls.module)
            finally:
                sys.path.pop(0)

    def setUp(self):
        self.assertTrue(
            SCRIPT.exists(), "trusted default-branch merger is not implemented"
        )

    def test_complete_source_bump_is_qualified_without_executing_pr_code(self):
        client = FakeGitHub()
        self.assertEqual(self.module.qualify(client, 12, "delivery", 42), "a" * 40)
        self.assertIn(f"repos/{TAP}/rules/branches/main?per_page=100", client.calls)

    def test_ordinary_pr_is_explicitly_skipped_without_release_or_token_access(self):
        client = FakeGitHub()
        client.pull["user"] = {"login": "maintainer", "type": "User"}
        self.assertIsNone(self.module.qualify(client, 12, "delivery", 42))
        self.assertEqual(client.calls, [f"repos/{TAP}/pulls/12"])

    def test_all_four_install_jobs_must_really_succeed(self):
        for conclusion in ("skipped", "failure", "cancelled", None):
            client = FakeGitHub()
            client.current_jobs[0]["conclusion"] = conclusion
            with (
                self.subTest(conclusion=conclusion),
                self.assertRaisesRegex(ValueError, "installation"),
            ):
                self.module.qualify(client, 12, "delivery", 42)
        client = FakeGitHub()
        client.current_jobs.pop()
        with self.assertRaisesRegex(ValueError, "installation"):
            self.module.qualify(client, 12, "delivery", 42)

    def test_manual_or_fork_run_cannot_supply_trusted_ci(self):
        for field, value in (
            ("event", "workflow_dispatch"),
            ("head_sha", "f" * 40),
            ("head_repository", {"full_name": "attacker/tap"}),
            ("head_branch", "unrelated-branch"),
            ("path", ".github/workflows/other.yml"),
            ("conclusion", "failure"),
            ("status", "in_progress"),
        ):
            client = FakeGitHub()
            client.current_run[field] = value
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "CI run"),
            ):
                self.module.qualify(client, 12, "delivery", 42)

    def test_empty_github_run_pr_array_uses_branch_and_exact_head(self):
        client = FakeGitHub()
        client.current_run["pull_requests"] = []
        self.assertEqual(self.module.qualify(client, 12, "delivery", 42), "a" * 40)
        self.assertEqual(self.module.pr_for_run(client, 42), 12)

    def test_explicit_fork_run_cannot_discover_a_tap_delivery_pr(self):
        client = FakeGitHub()
        client.current_run["head_repository"]["full_name"] = "contributor/fork"
        with self.assertRaisesRegex(ValueError, "same-repository"):
            self.module.pr_for_run(client, 42)
        self.assertEqual(client.calls, [f"repos/{TAP}/actions/runs/42"])

    def test_old_workflow_completion_cannot_merge_after_newer_run(self):
        with self.assertRaisesRegex(ValueError, "latest"):
            self.module.qualify(FakeGitHub(), 12, "delivery", 41)

    def test_missing_strict_branch_protection_blocks_automation(self):
        client = FakeGitHub()
        client.current_rules[1]["parameters"][
            "strict_required_status_checks_policy"
        ] = False
        with self.assertRaisesRegex(ValueError, "strict"):
            self.module.qualify(client, 12, "delivery", 42)
        client.current_rules = []
        with self.assertRaisesRegex(ValueError, "protection"):
            self.module.qualify(client, 12, "delivery", 42)

    def test_current_base_and_clean_mergeability_are_mandatory(self):
        for changes in ({"mergeable_state": "blocked"}, {"mergeable": None}):
            client = FakeGitHub()
            client.pull.update(changes)
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(ValueError, "mergeable"),
            ):
                self.module.qualify(client, 12, "delivery", 42)
        client = FakeGitHub()
        client.main_sha = "c" * 40
        with self.assertRaisesRegex(ValueError, "main changed"):
            self.module.qualify(client, 12, "delivery", 42)

    def test_mismatched_branch_version_or_release_formula_blocks_merge(self):
        client = FakeGitHub()
        client.pull["head"]["ref"] = "bump-korvid-0.6.0"
        client.current_run["head_branch"] = "bump-korvid-0.6.0"
        with self.assertRaisesRegex(ValueError, "branch version"):
            self.module.qualify(client, 12, "delivery", 42)
        client = FakeGitHub()
        client.released_formula += "# unexpected\n"
        with self.assertRaisesRegex(ValueError, "release formula"):
            self.module.qualify(client, 12, "delivery", 42)

    def test_full_bottle_sidecar_size_is_supported_and_digest_verified(self):
        data = b"a" * 1182130
        published = {
            "assets": [
                {
                    "name": "metadata.json",
                    "size": len(data),
                    "id": 123,
                    "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                }
            ]
        }
        with patch.object(self.module.subprocess, "check_output", return_value=data):
            self.assertEqual(
                self.module.GitHub().asset(TAP, published, "metadata.json"), data
            )
        with patch.object(
            self.module.subprocess, "check_output", return_value=b"tampered"
        ):
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                self.module.GitHub().asset(TAP, published, "metadata.json")

    def test_oversized_sidecar_fails_before_download(self):
        published = {
            "assets": [
                {
                    "name": "metadata.json",
                    "size": 8 * 1024 * 1024 + 1,
                    "id": 123,
                    "digest": "sha256:" + "a" * 64,
                }
            ]
        }
        with patch.object(self.module.subprocess, "check_output") as download:
            with self.assertRaisesRegex(ValueError, "metadata asset exceeds"):
                self.module.GitHub().asset(TAP, published, "metadata.json")
        download.assert_not_called()

    def test_merge_is_one_shot_sha_guarded_and_never_bypasses_rules(self):
        client = FakeGitHub()
        with (
            patch.object(self.module, "GitHub", return_value=client),
            patch.object(self.module.subprocess, "run") as execute,
        ):
            self.module.merge(12, "a" * 40)
        self.assertIn(f"repos/{TAP}/rulesets/1", client.calls)
        self.assertEqual(
            execute.call_args.args[0],
            [
                "gh",
                "pr",
                "merge",
                "12",
                "--repo",
                TAP,
                "--squash",
                "--match-head-commit",
                "a" * 40,
            ],
        )
        self.assertTrue(execute.call_args.kwargs["check"])

    def test_merge_rejects_actual_actor_bypass_or_unknown_visibility(self):
        for bypass in ("always", "pull_requests_only", "exempt", None):
            client = FakeGitHub()
            client.ruleset["current_user_can_bypass"] = bypass
            with (
                self.subTest(bypass=bypass),
                patch.object(self.module, "GitHub", return_value=client),
                patch.object(self.module.subprocess, "run") as execute,
            ):
                with self.assertRaisesRegex(ValueError, "cannot bypass"):
                    self.module.merge(12, "a" * 40)
                execute.assert_not_called()

    def test_merge_revalidates_checks_and_active_rules_before_writing(self):
        for field in ("checks", "enforcement"):
            client = FakeGitHub()
            if field == "checks":
                client.current_rules[1]["parameters"][
                    "strict_required_status_checks_policy"
                ] = False
            else:
                client.ruleset["enforcement"] = "evaluate"
            with (
                self.subTest(field=field),
                patch.object(self.module, "GitHub", return_value=client),
                patch.object(self.module.subprocess, "run") as execute,
            ):
                with self.assertRaisesRegex(ValueError, "strict required|active"):
                    self.module.merge(12, "a" * 40)
                execute.assert_not_called()

    def test_merge_queue_cannot_turn_one_shot_merge_into_enrollment(self):
        client = FakeGitHub()
        client.current_rules.append({"type": "merge_queue", "ruleset_id": 1})
        with (
            patch.object(self.module, "GitHub", return_value=client),
            patch.object(self.module.subprocess, "run") as execute,
        ):
            with self.assertRaisesRegex(ValueError, "merge queue"):
                self.module.merge(12, "a" * 40)
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
