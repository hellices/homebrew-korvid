from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_bottle_artifacts import validate_artifacts


ROOT_URL = "https://github.com/hellices/homebrew-korvid/releases/download/korvid-0.2.0"


class ValidateBottleArtifactsTests(unittest.TestCase):
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

    def _bottle_payload(self, archive: Path, tag: str, *, root_url: str = ROOT_URL) -> dict:
        return {
            "hellices/korvid/korvid": {
                "formula": {"name": "korvid", "pkg_version": "0.2.0"},
                "bottle": {
                    "root_url": root_url,
                    "tags": {
                        tag: {
                            "local_filename": archive.name,
                            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                        }
                    },
                },
            }
        }

    def _write_json(self, name: str, payload: dict) -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _write_valid_fixture(self) -> None:
        arm64_archive = self._archive(
            "korvid-0.2.0.arm64_sequoia.bottle.tar.gz", b"arm64 bottle contents"
        )
        sequoia_archive = self._archive(
            "korvid-0.2.0.sequoia.bottle.tar.gz", b"sequoia bottle contents"
        )
        self._write_json(
            "korvid-0.2.0.arm64_sequoia.bottle.json",
            self._bottle_payload(arm64_archive, "arm64_sequoia"),
        )
        self._write_json(
            "korvid-0.2.0.sequoia.bottle.json",
            self._bottle_payload(sequoia_archive, "sequoia"),
        )

    def test_accepts_complete_two_architecture_set(self) -> None:
        self._write_valid_fixture()

        paths = validate_artifacts(
            self.root, "0.2.0", ROOT_URL, {"arm64_sequoia", "sequoia"}
        )

        self.assertEqual(len(paths), 2)

    def test_rejects_missing_expected_tag(self) -> None:
        arm64_archive = self._archive(
            "korvid-0.2.0.arm64_sequoia.bottle.tar.gz", b"arm64 bottle contents"
        )
        self._write_json(
            "korvid-0.2.0.arm64_sequoia.bottle.json",
            self._bottle_payload(arm64_archive, "arm64_sequoia"),
        )

        with self.assertRaisesRegex(ValueError, "platform tags"):
            validate_artifacts(
                self.root, "0.2.0", ROOT_URL, {"arm64_sequoia", "sequoia"}
            )

    def test_rejects_wrong_archive_checksum(self) -> None:
        self._write_valid_fixture()
        (self.root / "korvid-0.2.0.sequoia.bottle.tar.gz").write_bytes(
            b"tampered bottle contents"
        )

        with self.assertRaisesRegex(ValueError, "checksum"):
            validate_artifacts(
                self.root, "0.2.0", ROOT_URL, {"arm64_sequoia", "sequoia"}
            )

    def test_rejects_wrong_root_url(self) -> None:
        self._write_valid_fixture()

        with self.assertRaisesRegex(ValueError, "root URL"):
            validate_artifacts(
                self.root,
                "0.2.0",
                "https://example.com/not-the-right-root",
                {"arm64_sequoia", "sequoia"},
            )

    def test_rejects_path_traversal_filename(self) -> None:
        arm64_archive = self._archive(
            "korvid-0.2.0.arm64_sequoia.bottle.tar.gz", b"arm64 bottle contents"
        )
        self._write_json(
            "korvid-0.2.0.arm64_sequoia.bottle.json",
            self._bottle_payload(arm64_archive, "arm64_sequoia"),
        )
        traversal_payload = self._bottle_payload(
            arm64_archive, "sequoia", root_url=ROOT_URL
        )
        traversal_payload["hellices/korvid/korvid"]["bottle"]["tags"]["sequoia"][
            "local_filename"
        ] = "../escape.bottle.tar.gz"
        self._write_json("korvid-0.2.0.sequoia.bottle.json", traversal_payload)

        with self.assertRaisesRegex(ValueError, "plain filename"):
            validate_artifacts(
                self.root, "0.2.0", ROOT_URL, {"arm64_sequoia", "sequoia"}
            )


if __name__ == "__main__":
    unittest.main()
