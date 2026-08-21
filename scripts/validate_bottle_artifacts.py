#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_artifacts(
    root: Path,
    version: str,
    root_url: str,
    expected_tags: set[str],
) -> list[Path]:
    json_paths = sorted(root.rglob("*.bottle.json"))
    if not json_paths:
        raise ValueError(f"no bottle JSON files found under {root}")

    seen_tags: set[str] = set()
    seen_archives: set[Path] = set()
    for json_path in json_paths:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        if len(payload) != 1:
            raise ValueError(f"{json_path}: expected exactly one formula")
        entry = next(iter(payload.values()))
        formula = entry.get("formula", {})
        if formula.get("name") != "korvid":
            raise ValueError(f"{json_path}: expected formula name korvid")
        if formula.get("pkg_version") != version:
            raise ValueError(f"{json_path}: expected package version {version}")

        bottle = entry.get("bottle", {})
        if bottle.get("root_url") != root_url:
            raise ValueError(f"{json_path}: unexpected bottle root URL")
        tags = bottle.get("tags", {})
        if len(tags) != 1:
            raise ValueError(f"{json_path}: expected exactly one platform tag")

        tag, metadata = next(iter(tags.items()))
        if tag in seen_tags:
            raise ValueError(f"{json_path}: duplicate platform tag {tag}")
        seen_tags.add(tag)

        filename = metadata.get("local_filename")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError(f"{json_path}: local_filename must be a plain filename")
        matches = list(root.rglob(filename))
        if len(matches) != 1:
            raise ValueError(f"{json_path}: expected one archive named {filename}")
        archive = matches[0]
        if archive in seen_archives:
            raise ValueError(f"{json_path}: archive reused by multiple tags")
        seen_archives.add(archive)
        if _sha256(archive) != metadata.get("sha256"):
            raise ValueError(f"{json_path}: archive checksum does not match {filename}")

    if seen_tags != expected_tags:
        raise ValueError(
            f"platform tags were {sorted(seen_tags)}, expected {sorted(expected_tags)}"
        )
    return json_paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Homebrew bottle JSON metadata and archives."
    )
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--root-url", required=True)
    parser.add_argument("--tag", action="append", default=[])
    args = parser.parse_args()

    if not args.tag:
        parser.error("--tag must be provided at least once")

    try:
        for json_path in validate_artifacts(
            args.artifacts, args.version, args.root_url, set(args.tag)
        ):
            print(json_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
