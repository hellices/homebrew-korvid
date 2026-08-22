#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_plain_filename(name: object) -> bool:
    return (
        isinstance(name, str)
        and name not in {"", ".", ".."}
        and Path(name).name == name
    )


def _expected_remote_filename(local_filename: str) -> str:
    """Homebrew writes the on-disk bottle as ``<name>--<version>...``
    (``Bottle::Filename#to_s``) but publishes it under ``<name>-<version>...``
    (``Bottle::Filename#url_encode``); the two differ only in the ``--`` that
    separates the formula name from the version. A GitHub Release ``root_url``
    serves the single-dash name, so that is what a client requests."""
    return local_filename.replace("--", "-", 1)


def _expected_json_filename(local_filename: str) -> str:
    suffix = ".bottle.tar.gz"
    if not local_filename.endswith(suffix):
        raise ValueError(
            f"local_filename {local_filename!r} must end with {suffix}"
        )
    return f"{local_filename[:-len(suffix)]}.bottle.json"


@dataclass(frozen=True)
class BottleArtifact:
    json_path: Path
    tag: str
    archive: Path
    filename: str  # remote single-dash name served from root_url
    local_filename: str  # build-time double-dash name on disk


def _collect_artifacts(
    root: Path,
    version: str,
    root_url: str,
    expected_tags: set[str],
    excluded_root: Path | None = None,
) -> list[BottleArtifact]:
    excluded = excluded_root.resolve() if excluded_root is not None else None

    def included(path: Path) -> bool:
        return excluded is None or not path.resolve().is_relative_to(excluded)

    json_paths = sorted(path for path in root.rglob("*.bottle.json") if included(path))
    if not json_paths:
        raise ValueError(f"no bottle JSON files found under {root}")

    seen_tags: set[str] = set()
    seen_archives: set[Path] = set()
    artifacts: list[BottleArtifact] = []
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

        local_filename = metadata.get("local_filename")
        if not _is_plain_filename(local_filename):
            raise ValueError(f"{json_path}: local_filename must be a plain filename")

        filename = metadata.get("filename")
        if not _is_plain_filename(filename):
            raise ValueError(f"{json_path}: filename must be a plain filename")

        expected = _expected_remote_filename(local_filename)
        if filename != expected:
            raise ValueError(
                f"{json_path}: filename {filename!r} does not match the single-dash "
                f"form of local_filename {local_filename!r} (expected {expected!r})"
            )
        expected_json = _expected_json_filename(local_filename)
        if json_path.name != expected_json:
            raise ValueError(
                f"{json_path}: JSON sidecar name must be {expected_json!r}"
            )

        # Exactly one archive must be present, named by *either* the build-time
        # local_filename or the published remote filename -- never both, which
        # would make the checksum target ambiguous.
        present = sorted(
            {
                path
                for name in {local_filename, filename}
                for path in root.rglob(name)
                if included(path)
            }
        )
        if not present:
            raise ValueError(
                f"{json_path}: no archive found named {filename} or {local_filename}"
            )
        if len(present) > 1:
            raise ValueError(
                f"{json_path}: ambiguous archives for tag {tag}: "
                f"{[p.name for p in present]}"
            )
        archive = present[0]
        if archive in seen_archives:
            raise ValueError(f"{json_path}: archive reused by multiple tags")
        seen_archives.add(archive)
        if _sha256(archive) != metadata.get("sha256"):
            raise ValueError(
                f"{json_path}: archive checksum does not match {archive.name}"
            )

        artifacts.append(
            BottleArtifact(
                json_path=json_path,
                tag=tag,
                archive=archive,
                filename=filename,
                local_filename=local_filename,
            )
        )

    if seen_tags != expected_tags:
        raise ValueError(
            f"platform tags were {sorted(seen_tags)}, expected {sorted(expected_tags)}"
        )
    return artifacts


def validate_artifacts(
    root: Path,
    version: str,
    root_url: str,
    expected_tags: set[str],
) -> list[Path]:
    artifacts = _collect_artifacts(root, version, root_url, expected_tags)
    return sorted(artifact.json_path for artifact in artifacts)


def stage_release_assets(
    root: Path,
    version: str,
    root_url: str,
    expected_tags: set[str],
    stage_dir: Path,
) -> list[Path]:
    """Validate ``root`` then build a clean upload directory at ``stage_dir``
    containing each JSON metadata sidecar and each archive copied under its
    remote ``filename`` (the single-dash name Homebrew requests from the
    release ``root_url``). Returns the staged paths."""
    if stage_dir.resolve() == root.resolve():
        raise ValueError("stage directory must not be the artifact root")
    artifacts = _collect_artifacts(
        root,
        version,
        root_url,
        expected_tags,
        excluded_root=stage_dir,
    )

    stage_dir.mkdir(parents=True, exist_ok=True)
    for stale in (*stage_dir.glob("*.bottle.tar.gz"), *stage_dir.glob("*.bottle.json")):
        stale.unlink()

    staged: list[Path] = []
    for artifact in artifacts:
        json_dest = stage_dir / artifact.json_path.name
        shutil.copyfile(artifact.json_path, json_dest)
        archive_dest = stage_dir / artifact.filename
        shutil.copyfile(artifact.archive, archive_dest)
        staged.extend((json_dest, archive_dest))
    return sorted(staged)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Homebrew bottle JSON metadata and archives."
    )
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--root-url", required=True)
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument(
        "--stage-dir",
        type=Path,
        default=None,
        help=(
            "After validation, copy each JSON sidecar and each archive -- "
            "renamed to its remote single-dash filename -- into this directory "
            "for release upload."
        ),
    )
    args = parser.parse_args()

    if not args.tag:
        parser.error("--tag must be provided at least once")

    try:
        if args.stage_dir is not None:
            staged = stage_release_assets(
                args.artifacts,
                args.version,
                args.root_url,
                set(args.tag),
                args.stage_dir,
            )
            for path in staged:
                print(path)
        else:
            for json_path in validate_artifacts(
                args.artifacts, args.version, args.root_url, set(args.tag)
            ):
                print(json_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
