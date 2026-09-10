#!/usr/bin/env python3
"""Qualify deployment PRs using default-branch code and read-only GitHub data."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote

from delivery_policy import (
    FORMULA,
    SOURCE,
    TAGS,
    TAP,
    Json,
    asset_digest,
    delivery_kind,
    published_release,
    validate_bottles,
    validate_bump,
    version_text,
    without_bottle,
)

INSTALL_CHECKS = {
    "test (macos-latest)",
    "test (ubuntu-latest)",
    "test-bottle (macos-15)",
    "test-bottle (macos-15-intel)",
}


class GitHub:
    def api(self, endpoint: str) -> Any:
        return json.loads(subprocess.check_output(["gh", "api", endpoint]))

    def contents(self, repository: str, sha: str) -> str:
        content = self.api(
            f"repos/{repository}/contents/{FORMULA}?ref={quote(sha, safe='')}"
        )
        if (
            content["type"] != "file"
            or content["encoding"] != "base64"
            or content["size"] > 262144
        ):
            raise ValueError("unexpected formula content response")
        return base64.b64decode(content["content"]).decode("utf-8")

    def asset(self, repository: str, release: Json, name: str) -> bytes:
        digest = asset_digest(release, name)
        asset = next(asset for asset in release["assets"] if asset["name"] == name)
        if asset["size"] > 8 * 1024 * 1024:
            raise ValueError("delivery metadata asset exceeds 8 MiB")
        data = subprocess.check_output(
            [
                "gh",
                "api",
                f"repos/{repository}/releases/assets/{int(asset['id'])}",
                "-H",
                "Accept: application/octet-stream",
            ]
        )
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("downloaded release asset digest mismatch")
        return data


def validate_rules(rules: list[Json]) -> None:
    if len(rules) >= 100:
        raise ValueError("effective rules may be truncated; manual delivery required")
    if any(rule["type"] == "merge_queue" for rule in rules):
        raise ValueError("one-shot delivery does not support a merge queue")
    review_rules = [
        rule["parameters"] for rule in rules if rule["type"] == "pull_request"
    ]
    if not review_rules or not all(
        rule["required_review_thread_resolution"] for rule in review_rules
    ):
        raise ValueError("PR protection must require review thread resolution")
    check_rules = [
        rule["parameters"] for rule in rules if rule["type"] == "required_status_checks"
    ]
    required = {
        check["context"]
        for rule in check_rules
        if rule["strict_required_status_checks_policy"]
        for check in rule["required_status_checks"]
    }
    if not INSTALL_CHECKS <= required:
        raise ValueError("all four installation checks must be strict required checks")


def validate_run(pr: Json, run: Json, jobs: list[Json]) -> None:
    if (
        run["event"] != "pull_request"
        or run["path"] != ".github/workflows/test.yml"
        or run["head_sha"] != pr["head"]["sha"]
        or run["head_branch"] != pr["head"]["ref"]
        or run["head_repository"]["full_name"] != TAP
        or run["status"] != "completed"
        or run["conclusion"] != "success"
    ):
        raise ValueError("CI run is not a successful PR run for this exact head")
    for name in INSTALL_CHECKS:
        matching = [job for job in jobs if job["name"] == name]
        if (
            len(matching) != 1
            or matching[0]["status"] != "completed"
            or matching[0]["conclusion"] != "success"
        ):
            raise ValueError(
                "installation qualification is missing or unsuccessful: " + name
            )


def _check_ci(client: GitHub, pr: Json, expected_run: int | None) -> None:
    runs = client.api(
        f"repos/{TAP}/actions/workflows/test.yml/runs"
        f"?event=pull_request&head_sha={pr['head']['sha']}&per_page=100"
    )["workflow_runs"]
    if not runs:
        raise ValueError("no PR CI run exists for this head")
    latest = max(runs, key=lambda run: run["id"])
    if expected_run is not None and latest["id"] != expected_run:
        raise ValueError("completion event is not the latest CI run")
    result = client.api(
        f"repos/{TAP}/actions/runs/{latest['id']}/attempts/{latest['run_attempt']}/jobs?per_page=100"
    )
    if result["total_count"] != len(result["jobs"]):
        raise ValueError("CI run jobs were truncated")
    validate_run(pr, latest, result["jobs"])


def pr_for_run(client: GitHub, run_id: int) -> int | None:
    run = client.api(f"repos/{TAP}/actions/runs/{run_id}")
    if (
        run["event"] != "pull_request"
        or run["path"] != ".github/workflows/test.yml"
        or run["head_repository"]["full_name"] != TAP
    ):
        raise ValueError("CI run is not a same-repository formula PR run")
    # GitHub can return an empty workflow_run.pull_requests array even for
    # successful same-repository PR runs; resolve the still-open head explicitly.
    pulls = client.api(
        f"repos/{TAP}/pulls?state=open&head={quote('hellices:' + run['head_branch'], safe='')}"
        "&base=main&per_page=100"
    )
    matching = [pr for pr in pulls if pr["head"]["sha"] == run["head_sha"]]
    if not matching:
        print("No open PR still matches the completed CI head; no delivery action.")
        return None
    if len(matching) != 1:
        raise ValueError("CI run matches more than one open PR")
    return int(matching[0]["number"])


def _check_formula(
    client: GitHub, kind: str, version: str, base: str, head: str
) -> None:
    if version_text(head) != version:
        raise ValueError("branch version does not match formula")
    source_release = client.api(f"repos/{SOURCE}/releases/tags/v{version}")
    published_release(source_release, "v" + version)
    if kind == "bump":
        published = client.asset(SOURCE, source_release, "korvid.rb").decode("utf-8")
        validate_bump(base, head, published, source_release)
    else:
        release = client.api(f"repos/{TAP}/releases/tags/korvid-{version}")
        metadata = [
            json.loads(
                client.asset(TAP, release, f"korvid--{version}.{tag}.bottle.json")
            )
            for tag in sorted(TAGS)
        ]
        revision = validate_bottles(base, head, release, metadata)
        built = client.contents(TAP, revision)
        if without_bottle(built) != base:
            raise ValueError(
                "bottle build provenance does not match current source formula"
            )


def qualify(
    client: GitHub, number: int, app_slug: str, expected_run: int | None = None
) -> str | None:
    if not app_slug:
        raise ValueError("HOMEBREW_APP_SLUG is required")
    pr = client.api(f"repos/{TAP}/pulls/{number}")
    if pr["state"] != "open" or pr["user"]["login"] != app_slug + "[bot]":
        print(
            f"PR #{number} is not an open delivery App PR; leaving it to the maintainer."
        )
        return None
    files = client.api(f"repos/{TAP}/pulls/{number}/files?per_page=100")
    kind, version = delivery_kind(pr, files, app_slug)
    if pr["changed_files"] != 1:
        raise ValueError("deployment PR must be formula-only")
    main_sha = client.api(f"repos/{TAP}/commits/main")["sha"]
    if pr["base"]["sha"] != main_sha:
        raise ValueError("main changed; rebase and requalify this deployment")
    validate_rules(client.api(f"repos/{TAP}/rules/branches/main?per_page=100"))
    _check_ci(client, pr, expected_run)
    base = client.contents(TAP, main_sha)
    head = client.contents(TAP, pr["head"]["sha"])
    _check_formula(client, kind, version, base, head)
    fresh = client.api(f"repos/{TAP}/pulls/{number}")
    if (
        fresh["head"]["sha"] != pr["head"]["sha"]
        or fresh["base"]["sha"] != main_sha
        or client.api(f"repos/{TAP}/commits/main")["sha"] != main_sha
    ):
        raise ValueError("PR or main changed during qualification")
    if fresh["mergeable"] is not True or fresh["mergeable_state"] != "clean":
        raise ValueError(
            "PR is not cleanly mergeable; resolve reviews/conflicts and retry"
        )
    return pr["head"]["sha"]


def check_merge_rules(client: GitHub) -> None:
    rules = client.api(f"repos/{TAP}/rules/branches/main?per_page=100")
    validate_rules(rules)
    for ruleset_id in {int(rule["ruleset_id"]) for rule in rules}:
        ruleset = client.api(f"repos/{TAP}/rulesets/{ruleset_id}")
        if ruleset["enforcement"] != "active":
            raise ValueError("merge ruleset must remain active")
        # Full bypass lists require administration privileges; this field
        # checks the actual merge actor using only implicit Metadata read.
        if ruleset.get("current_user_can_bypass") != "never":
            raise ValueError("cannot confirm that the merge actor cannot bypass rules")


def merge(number: int, head: str) -> None:
    check_merge_rules(GitHub())
    subprocess.run(
        [
            "gh",
            "pr",
            "merge",
            str(number),
            "--repo",
            TAP,
            "--squash",
            "--match-head-commit",
            head,
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int)
    parser.add_argument("--run", type=int)
    parser.add_argument("--app-slug", default=os.environ.get("HOMEBREW_APP_SLUG", ""))
    parser.add_argument(
        "--merge-head", help="Perform the separately qualified, SHA-guarded merge."
    )
    args = parser.parse_args()
    if args.pr is not None and args.pr < 1:
        parser.error("--pr must be a positive integer")
    if args.pr is None and (args.run is None or args.merge_head):
        parser.error("--pr or a qualification --run is required")
    if args.run is not None and args.run < 1:
        parser.error("--run must be a positive integer")
    if args.merge_head:
        if not re.fullmatch(r"[0-9a-f]{40}", args.merge_head):
            parser.error("--merge-head must be a full commit SHA")
        merge(args.pr, args.merge_head)
    else:
        client = GitHub()
        number = args.pr if args.pr is not None else pr_for_run(client, args.run)
        if number is None:
            return 0
        head = qualify(client, number, args.app_slug, args.run)
        if head:
            print(f"Qualified PR #{number} at {head}; no merge performed by this step.")
            if os.environ.get("GITHUB_OUTPUT"):
                with Path(os.environ["GITHUB_OUTPUT"]).open(
                    "a", encoding="utf-8"
                ) as output:
                    output.write(f"pr={number}\nhead={head}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
