#!/usr/bin/env python3
"""Validate generated delivery changes as data, never by evaluating Ruby."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Mapping

TAP = "hellices/homebrew-korvid"
SOURCE = "hellices/korvid"
FORMULA = "Formula/korvid.rb"
TAGS = {"arm64_sequoia", "sequoia"}
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
_BOTTLE = re.compile(r"^  bottle do\n.*?^  end\n\n", re.MULTILINE | re.DOTALL)
Json = Mapping[str, Any]


def formula_version(formula: str) -> tuple[int, ...]:
    urls = re.findall(r'^  url "([^"\n]+)"$', formula, re.MULTILINE)
    match = (
        re.fullmatch(
            rf"https://files\.pythonhosted\.org/packages/[a-zA-Z0-9/]+/korvid-({VERSION})\.tar\.gz",
            urls[0],
        )
        if len(urls) == 1
        else None
    )
    if match is None:
        raise ValueError("formula must have one stable public PyPI source URL")
    return tuple(int(part) for part in match[1].split("."))


def version_text(formula: str) -> str:
    return ".".join(str(part) for part in formula_version(formula))


def without_bottle(formula: str) -> str:
    blocks = _BOTTLE.findall(formula)
    if len(blocks) > 1 or formula.count("  bottle do") != len(blocks):
        raise ValueError("unexpected bottle block structure")
    return _BOTTLE.sub("", formula)


def published_release(release: Json, tag: str) -> None:
    if (
        release.get("tag_name") != tag
        or release.get("draft") is not False
        or release.get("prerelease") is not False
    ):
        raise ValueError("expected a published stable release for " + tag)


def delivery_kind(pr: Json, files: list[Json], app_slug: str) -> tuple[str, str]:
    head, base = pr["head"], pr["base"]
    match = re.fullmatch(rf"(bump|bottles)-korvid-({VERSION})", head["ref"])
    if (
        not app_slug
        or pr["state"] != "open"
        or pr["draft"]
        or pr["user"]["login"] != app_slug + "[bot]"
        or pr["user"]["type"] != "Bot"
        or head["repo"]["full_name"] != TAP
        or base["repo"]["full_name"] != TAP
        or base["ref"] != "main"
        or match is None
    ):
        raise ValueError("not a trusted open deployment PR")
    if (
        len(files) != 1
        or files[0].get("filename") != FORMULA
        or files[0].get("status") != "modified"
    ):
        raise ValueError("deployment PR must be a formula-only modification")
    return match[1], match[2]


def validate_bump(base: str, head: str, published_formula: str, release: Json) -> None:
    published_release(release, "v" + version_text(head))
    if formula_version(head) <= formula_version(base):
        raise ValueError("release must be newer than the formula on main")
    if head != published_formula or without_bottle(head) != head:
        raise ValueError(
            "PR must exactly match the source release formula without bottles"
        )


def asset_digest(release: Json, filename: str) -> str:
    assets = [asset for asset in release["assets"] if asset["name"] == filename]
    digest = assets[0].get("digest", "") if len(assets) == 1 else ""
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("missing or ambiguous published asset digest: " + filename)
    return digest.removeprefix("sha256:")


def _sidecar(metadata: Json, version: str, root_url: str) -> tuple[str, str, str, str]:
    if set(metadata) != {"hellices/korvid/korvid"}:
        raise ValueError("unexpected bottle formula provenance")
    entry = metadata["hellices/korvid/korvid"]
    source, bottle = entry["formula"], entry["bottle"]
    if any(
        source.get(key) != value
        for key, value in {
            "name": "korvid",
            "pkg_version": version,
            "tap_git_remote": "https://github.com/" + TAP,
            "tap_git_path": FORMULA,
        }.items()
    ):
        raise ValueError("unexpected bottle source provenance")
    revision = source.get("tap_git_revision", "")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("invalid bottle build revision")
    if (
        bottle.get("root_url") != root_url
        or bottle.get("cellar") not in {"any", "any_skip_relocation"}
        or bottle.get("rebuild") != 0
    ):
        raise ValueError("unsupported bottle metadata")
    tags = bottle["tags"]
    if len(tags) != 1 or not set(tags) <= TAGS:
        raise ValueError("unexpected bottle platform")
    tag, data = next(iter(tags.items()))
    if (
        data.get("filename") != f"korvid-{version}.{tag}.bottle.tar.gz"
        or data.get("local_filename") != f"korvid--{version}.{tag}.bottle.tar.gz"
        or not re.fullmatch(r"[0-9a-f]{64}", data.get("sha256", ""))
    ):
        raise ValueError("unexpected platform metadata")
    return tag, data["sha256"], bottle["cellar"], revision


def _bottle_lines(head: str, root_url: str) -> dict[str, tuple[str, str]]:
    blocks = _BOTTLE.findall(head)
    if len(blocks) != 1:
        raise ValueError("expected exactly one bottle block")
    lines = blocks[0].splitlines()
    if len(lines) != 6 or lines[1] != f'    root_url "{root_url}"':
        raise ValueError("unexpected bottle block")
    result = {}
    for line in lines[2:4]:
        match = re.fullmatch(
            r"    sha256 cellar: :(any|any_skip_relocation),\s+"
            r'(arm64_sequoia|sequoia):\s+"([0-9a-f]{64})"',
            line,
        )
        if match is None or match[2] in result:
            raise ValueError("unexpected bottle block platform line")
        result[match[2]] = (match[3], match[1])
    if set(result) != TAGS:
        raise ValueError("bottle block must contain both platforms")
    return result


def validate_bottles(base: str, head: str, release: Json, sidecars: list[Json]) -> str:
    version = version_text(head)
    published_release(release, "korvid-" + version)
    if without_bottle(base) != base:
        raise ValueError("main already has bottles; do not replace a completed release")
    if without_bottle(head) != base:
        raise ValueError("bottle update changed content outside the bottle block")
    root_url = f"https://github.com/{TAP}/releases/download/korvid-{version}"
    lines = _bottle_lines(head, root_url)
    expected = {}
    revisions = set()
    for metadata in sidecars:
        tag, digest, cellar, revision = _sidecar(metadata, version, root_url)
        if tag in expected:
            raise ValueError("duplicate platform metadata")
        published_digest = asset_digest(
            release, f"korvid-{version}.{tag}.bottle.tar.gz"
        )
        if digest != published_digest:
            raise ValueError("bottle digest does not match the published archive")
        expected[tag] = (digest, cellar)
        revisions.add(revision)
    if set(expected) != TAGS:
        raise ValueError("both bottle platforms are required")
    if len(revisions) != 1:
        raise ValueError("bottle platforms must share one build revision")
    if lines != expected:
        raise ValueError("bottle block does not match published metadata")
    return revisions.pop()


def bottle_update_needed(current: str, built: str, version: str) -> bool:
    if (
        version_text(current) != version
        or version_text(built) != version
        or without_bottle(current) != without_bottle(built)
    ):
        raise ValueError(
            "main formula changed since this bottle build; refusing stale publication"
        )
    return without_bottle(current) == current


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Guard a bottle publication against current main."
    )
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument("--built", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    needed = bottle_update_needed(
        args.current.read_text(encoding="utf-8"),
        args.built.read_text(encoding="utf-8"),
        args.version,
    )
    print("needed=" + ("true" if needed else "false"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
