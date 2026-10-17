"""Tests for selecting and preparing the bottle formula's Python."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import call, patch

SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare_formula_python.py"


class PrepareFormulaPythonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(SCRIPT.is_file(), "shared Python preparation script is missing")
        spec = importlib.util.spec_from_file_location("prepare_formula_python", SCRIPT)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_selects_the_formula_interpreter(self) -> None:
        for version in ("3.13", "3.14"):
            with self.subTest(version=version):
                metadata = {"formulae": [{"dependencies": ["libyaml", f"python@{version}"]}]}
                self.assertEqual(self.module.formula_python(metadata), f"python@{version}")

    def test_rejects_missing_ambiguous_or_malformed_metadata(self) -> None:
        payloads = [
            None,
            {},
            {"formulae": []},
            {"formulae": [{}, {}]},
            {"formulae": [{"dependencies": []}]},
            {"formulae": [{"dependencies": ["python@3.13", "python@3.14"]}]},
            {"formulae": [{"dependencies": ["python@3.13;unexpected"]}]},
            {"formulae": [{"dependencies": [42]}]},
            {"formulae": [{"dependencies": {"python@3.13": "unexpected"}}]},
        ]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "Expected"):
                self.module.formula_python(payload)

    def test_prepares_only_the_selected_python(self) -> None:
        metadata = json.dumps({"formulae": [{"dependencies": ["python@3.13"]}]})
        with patch.object(
            self.module.subprocess, "check_output",
            side_effect=[metadata, "openssl@3\npython@3.13\n"],
        ) as info:
            with patch.object(self.module.subprocess, "run") as run:
                result = self.module.prepare_formula_python("hellices/korvid/korvid")
        self.assertEqual(result, "python@3.13")
        self.assertEqual(
            info.call_args_list,
            [
                call(["brew", "info", "--json=v2", "hellices/korvid/korvid"], text=True),
                call(["brew", "list", "--formula"], text=True),
            ],
        )
        self.assertEqual(
            run.call_args_list,
            [
                call(["brew", "install", "--overwrite", "--verbose", "python@3.13"], check=True),
                call(["brew", "link", "--overwrite", "python@3.13"], check=True),
            ],
        )

    def test_unlinks_legacy_openssl_before_installing_python_dependencies(self) -> None:
        metadata = json.dumps({"formulae": [{"dependencies": ["python@3.13"]}]})
        legacy_linked = True

        def execute(command: list[str], *, check: bool) -> None:
            nonlocal legacy_linked
            self.assertTrue(check)
            if command == ["brew", "unlink", "openssl@1.1"]:
                legacy_linked = False
            if command[:2] == ["brew", "install"] and legacy_linked:
                raise subprocess.CalledProcessError(1, command, stderr="Could not symlink bin/openssl")

        with patch.object(
            self.module.subprocess, "check_output",
            side_effect=[metadata, "openssl@1.1\nopenssl@3\npython@3.13\n"],
        ):
            with patch.object(self.module.subprocess, "run", side_effect=execute) as run:
                result = self.module.prepare_formula_python("hellices/korvid/korvid")
        self.assertEqual(result, "python@3.13")
        self.assertFalse(legacy_linked)
        self.assertEqual(
            run.call_args_list,
            [
                call(["brew", "unlink", "openssl@1.1"], check=True),
                call(["brew", "install", "--overwrite", "--verbose", "python@3.13"], check=True),
                call(["brew", "link", "--overwrite", "python@3.13"], check=True),
            ],
        )

    def test_legacy_openssl_unlink_failure_blocks_python_install(self) -> None:
        metadata = json.dumps({"formulae": [{"dependencies": ["python@3.13"]}]})
        failure = subprocess.CalledProcessError(1, ["brew", "unlink", "openssl@1.1"])
        with patch.object(
            self.module.subprocess, "check_output",
            side_effect=[metadata, "openssl@1.1\n"],
        ):
            with patch.object(self.module.subprocess, "run", side_effect=failure) as run:
                with self.assertRaisesRegex(subprocess.CalledProcessError, "non-zero"):
                    self.module.prepare_formula_python("hellices/korvid/korvid")
        run.assert_called_once_with(["brew", "unlink", "openssl@1.1"], check=True)

    def test_formula_listing_failure_blocks_all_mutations(self) -> None:
        metadata = json.dumps({"formulae": [{"dependencies": ["python@3.13"]}]})
        failure = subprocess.CalledProcessError(1, ["brew", "list", "--formula"])
        with patch.object(
            self.module.subprocess, "check_output", side_effect=[metadata, failure],
        ):
            with patch.object(self.module.subprocess, "run") as run:
                with self.assertRaisesRegex(subprocess.CalledProcessError, "non-zero"):
                    self.module.prepare_formula_python("hellices/korvid/korvid")
        run.assert_not_called()

    def test_install_failure_does_not_attempt_linking(self) -> None:
        metadata = json.dumps({"formulae": [{"dependencies": ["python@3.13"]}]})
        failure = subprocess.CalledProcessError(1, ["brew", "install"])
        with patch.object(
            self.module.subprocess, "check_output", side_effect=[metadata, "openssl@3\n"],
        ):
            with patch.object(self.module.subprocess, "run", side_effect=failure) as run:
                with self.assertRaisesRegex(subprocess.CalledProcessError, "non-zero"):
                    self.module.prepare_formula_python("hellices/korvid/korvid")
        self.assertEqual(run.call_count, 1)

    def test_link_failure_is_not_reported_as_success(self) -> None:
        metadata = json.dumps({"formulae": [{"dependencies": ["python@3.13"]}]})
        failure = subprocess.CalledProcessError(1, ["brew", "link"])
        with patch.object(
            self.module.subprocess, "check_output", side_effect=[metadata, "openssl@3\n"],
        ):
            with patch.object(self.module.subprocess, "run", side_effect=[None, failure]) as run:
                with self.assertRaisesRegex(subprocess.CalledProcessError, "non-zero"):
                    self.module.prepare_formula_python("hellices/korvid/korvid")
        self.assertEqual(run.call_count, 2)

    def test_invalid_metadata_does_not_install_anything(self) -> None:
        with patch.object(self.module.subprocess, "check_output", return_value='{"formulae": []}'):
            with patch.object(self.module.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "Expected"):
                    self.module.prepare_formula_python("hellices/korvid/korvid")
        run.assert_not_called()
