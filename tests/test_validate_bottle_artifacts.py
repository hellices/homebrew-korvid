from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_bottle_artifacts import (
    stage_release_assets,
    validate_artifacts,
)


ROOT_URL = "https://github.com/hellices/homebrew-korvid/releases/download/korvid-0.2.0"
VERSION = "0.2.0"
TAGS = {"arm64_sequoia", "sequoia"}


def _local_name(tag: str) -> str:
    # Homebrew's on-disk build artifact (Filename#to_s): double dash.
    return f"korvid--{VERSION}.{tag}.bottle.tar.gz"


def _remote_name(tag: str) -> str:
    # The name Homebrew requests from a GitHub Release root_url
    # (Filename#url_encode): single dash.
    return f"korvid-{VERSION}.{tag}.bottle.tar.gz"


def _json_name(tag: str) -> str:
    return f"korvid--{VERSION}.{tag}.bottle.json"


class _FixtureBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1], prefix="validate-bottle-artifacts-"
        )
        self.root = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _archive(self, name: str, content: bytes) -> Path:
        path = self.root / name
        path.write_bytes(content)
        return path

    def _payload(
        self,
        tag: str,
        *,
        local_filename: str,
        filename: str,
        sha256: str,
        root_url: str = ROOT_URL,
    ) -> dict:
        return {
            "hellices/korvid/korvid": {
                "formula": {"name": "korvid", "pkg_version": VERSION},
                "bottle": {
                    "root_url": root_url,
                    "tags": {
                        tag: {
                            "filename": filename,
                            "local_filename": local_filename,
                            "sha256": sha256,
                        }
                    },
                },
            }
        }

    def _write_json(self, name: str, payload: dict) -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _write_local_fixture(self) -> None:
        """Build-time artifacts: archives on disk under the double-dash name."""
        for tag in ("arm64_sequoia", "sequoia"):
            archive = self._archive(_local_name(tag), f"{tag} bottle contents".encode())
            self._write_json(
                _json_name(tag),
                self._payload(
                    tag,
                    local_filename=_local_name(tag),
                    filename=_remote_name(tag),
                    sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                ),
            )

    def _write_remote_fixture(self) -> None:
        """Downloaded published artifacts: archives on disk under the
        single-dash remote name, JSON unchanged (still lists local_filename)."""
        for tag in ("arm64_sequoia", "sequoia"):
            archive = self._archive(
                _remote_name(tag), f"{tag} bottle contents".encode()
            )
            self._write_json(
                _json_name(tag),
                self._payload(
                    tag,
                    local_filename=_local_name(tag),
                    filename=_remote_name(tag),
                    sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                ),
            )


class ValidateBottleArtifactsTests(_FixtureBase):
    def test_accepts_complete_two_architecture_set(self) -> None:
        self._write_local_fixture()

        paths = validate_artifacts(self.root, VERSION, ROOT_URL, TAGS)

        self.assertEqual(len(paths), 2)

    def test_accepts_remote_name_downloaded_artifacts(self) -> None:
        self._write_remote_fixture()

        paths = validate_artifacts(self.root, VERSION, ROOT_URL, TAGS)

        self.assertEqual(len(paths), 2)

    def test_rejects_missing_expected_tag(self) -> None:
        tag = "arm64_sequoia"
        archive = self._archive(_local_name(tag), b"arm64 bottle contents")
        self._write_json(
            _json_name(tag),
            self._payload(
                tag,
                local_filename=_local_name(tag),
                filename=_remote_name(tag),
                sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            ),
        )

        with self.assertRaisesRegex(ValueError, "platform tags"):
            validate_artifacts(self.root, VERSION, ROOT_URL, TAGS)

    def test_rejects_wrong_archive_checksum(self) -> None:
        self._write_local_fixture()
        (self.root / _local_name("sequoia")).write_bytes(b"tampered bottle contents")

        with self.assertRaisesRegex(ValueError, "checksum"):
            validate_artifacts(self.root, VERSION, ROOT_URL, TAGS)

    def test_rejects_wrong_root_url(self) -> None:
        self._write_local_fixture()

        with self.assertRaisesRegex(ValueError, "root URL"):
            validate_artifacts(
                self.root,
                VERSION,
                "https://example.com/not-the-right-root",
                TAGS,
            )

    def test_rejects_path_traversal_local_filename(self) -> None:
        tag = "arm64_sequoia"
        archive = self._archive(_local_name(tag), b"arm64 bottle contents")
        self._write_json(
            _json_name(tag),
            self._payload(
                tag,
                local_filename=_local_name(tag),
                filename=_remote_name(tag),
                sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            ),
        )
        # Second tag carries a traversing local_filename.
        other = self._archive(_local_name("sequoia"), b"sequoia bottle contents")
        payload = self._payload(
            "sequoia",
            local_filename="../escape.bottle.tar.gz",
            filename=_remote_name("sequoia"),
            sha256=hashlib.sha256(other.read_bytes()).hexdigest(),
        )
        self._write_json(_json_name("sequoia"), payload)

        with self.assertRaisesRegex(ValueError, "plain filename"):
            validate_artifacts(self.root, VERSION, ROOT_URL, TAGS)

    def test_rejects_missing_filename(self) -> None:
        tag = "arm64_sequoia"
        archive = self._archive(_local_name(tag), b"arm64 bottle contents")
        payload = self._payload(
            tag,
            local_filename=_local_name(tag),
            filename=_remote_name(tag),
            sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        )
        # Drop the remote filename entirely.
        del payload["hellices/korvid/korvid"]["bottle"]["tags"][tag]["filename"]
        self._write_json(_json_name(tag), payload)

        with self.assertRaisesRegex(ValueError, "filename must be a plain filename"):
            validate_artifacts(self.root, VERSION, ROOT_URL, {tag})

    def test_rejects_traversing_filename(self) -> None:
        tag = "arm64_sequoia"
        archive = self._archive(_local_name(tag), b"arm64 bottle contents")
        payload = self._payload(
            tag,
            local_filename=_local_name(tag),
            filename="../escape.bottle.tar.gz",
            sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        )
        self._write_json(_json_name(tag), payload)

        with self.assertRaisesRegex(ValueError, "plain filename"):
            validate_artifacts(self.root, VERSION, ROOT_URL, {tag})

    def test_rejects_dot_path_segment_as_filename(self) -> None:
        tag = "arm64_sequoia"
        archive = self._archive(_local_name(tag), b"arm64 bottle contents")
        payload = self._payload(
            tag,
            local_filename=_local_name(tag),
            filename=".",
            sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        )
        self._write_json(_json_name(tag), payload)

        with self.assertRaisesRegex(ValueError, "plain filename"):
            validate_artifacts(self.root, VERSION, ROOT_URL, {tag})

    def test_rejects_parent_path_segment_as_local_filename(self) -> None:
        tag = "arm64_sequoia"
        archive = self._archive(_local_name(tag), b"arm64 bottle contents")
        payload = self._payload(
            tag,
            local_filename="..",
            filename=_remote_name(tag),
            sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        )
        self._write_json(_json_name(tag), payload)

        with self.assertRaisesRegex(ValueError, "plain filename"):
            validate_artifacts(self.root, VERSION, ROOT_URL, {tag})

    def test_rejects_filename_local_filename_mismatch(self) -> None:
        tag = "arm64_sequoia"
        archive = self._archive(_local_name(tag), b"arm64 bottle contents")
        payload = self._payload(
            tag,
            local_filename=_local_name(tag),
            # A plausible-but-wrong remote name (does not match the single-dash
            # form of local_filename).
            filename=f"korvid-9.9.9.{tag}.bottle.tar.gz",
            sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        )
        self._write_json(_json_name(tag), payload)

        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_artifacts(self.root, VERSION, ROOT_URL, {tag})

    def test_rejects_ambiguous_duplicate_archives(self) -> None:
        tag = "arm64_sequoia"
        # Both the build-time local copy AND the published remote copy exist:
        # the checksum target is ambiguous, so this must be rejected.
        local = self._archive(_local_name(tag), b"arm64 bottle contents")
        self._archive(_remote_name(tag), b"arm64 bottle contents")
        self._write_json(
            _json_name(tag),
            self._payload(
                tag,
                local_filename=_local_name(tag),
                filename=_remote_name(tag),
                sha256=hashlib.sha256(local.read_bytes()).hexdigest(),
            ),
        )

        with self.assertRaisesRegex(ValueError, "ambiguous"):
            validate_artifacts(self.root, VERSION, ROOT_URL, {tag})

    def test_rejects_unexpected_json_sidecar_name(self) -> None:
        tag = "arm64_sequoia"
        archive = self._archive(_local_name(tag), b"arm64 bottle contents")
        self._write_json(
            "unexpected.bottle.json",
            self._payload(
                tag,
                local_filename=_local_name(tag),
                filename=_remote_name(tag),
                sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            ),
        )

        with self.assertRaisesRegex(ValueError, "JSON sidecar"):
            validate_artifacts(self.root, VERSION, ROOT_URL, {tag})


class StageReleaseAssetsTests(_FixtureBase):
    def test_stages_remote_named_assets(self) -> None:
        self._write_local_fixture()
        stage_dir = self.root / "staged"

        staged = stage_release_assets(
            self.root, VERSION, ROOT_URL, TAGS, stage_dir
        )

        staged_names = {p.name for p in stage_dir.iterdir()}
        expected = {
            _remote_name("arm64_sequoia"),
            _remote_name("sequoia"),
            _json_name("arm64_sequoia"),
            _json_name("sequoia"),
        }
        self.assertEqual(staged_names, expected)
        # The double-dash local archive name must never appear in the upload set.
        self.assertNotIn(_local_name("arm64_sequoia"), staged_names)
        self.assertNotIn(_local_name("sequoia"), staged_names)
        # Staged archives carry the exact bytes of the build artifacts.
        for tag in ("arm64_sequoia", "sequoia"):
            self.assertEqual(
                (stage_dir / _remote_name(tag)).read_bytes(),
                (self.root / _local_name(tag)).read_bytes(),
            )
        # Returned paths point at the staged files.
        self.assertEqual({p.name for p in staged}, expected)

    def test_staging_validates_before_writing(self) -> None:
        self._write_local_fixture()
        (self.root / _local_name("sequoia")).write_bytes(b"tampered")
        stage_dir = self.root / "staged"

        with self.assertRaisesRegex(ValueError, "checksum"):
            stage_release_assets(self.root, VERSION, ROOT_URL, TAGS, stage_dir)

    def test_staging_is_idempotent_when_directory_is_under_root(self) -> None:
        self._write_local_fixture()
        stage_dir = self.root / "staged"

        first = stage_release_assets(
            self.root, VERSION, ROOT_URL, TAGS, stage_dir
        )
        second = stage_release_assets(
            self.root, VERSION, ROOT_URL, TAGS, stage_dir
        )

        self.assertEqual({path.name for path in first}, {path.name for path in second})

    def test_staging_rejects_artifact_root_as_destination(self) -> None:
        self._write_local_fixture()

        with self.assertRaisesRegex(ValueError, "must not be the artifact root"):
            stage_release_assets(
                self.root, VERSION, ROOT_URL, TAGS, self.root
            )


if __name__ == "__main__":
    unittest.main()
