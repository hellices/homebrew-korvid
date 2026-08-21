"""Contract tests for README air-gapped staging instructions.

Standard library only: no PyYAML, no third-party imports.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

README = Path(__file__).parent.parent / "README.md"


class ReadmeAirgapPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = README.read_text()

    def test_explicit_schema_tag_preflight_precedes_fetch(self) -> None:
        """`brew fetch --force-bottle` does not fail when no bottle exists, so
        the README must run an explicit `brew info --json=v2` schema/tag
        preflight *before* `brew fetch`. The preflight must inspect
        `.formulae[0].bottle.stable.files`, check the exact supported tags, and
        be able to exit non-zero on an unsupported host or an absent tag."""
        text = self.text
        self.assertIn("brew info --json=v2", text)
        self.assertIn("brew fetch", text)
        info_idx = text.index("brew info --json=v2")
        fetch_idx = text.index("brew fetch")
        self.assertLess(
            info_idx,
            fetch_idx,
            "an explicit brew info schema/tag preflight must run before brew fetch",
        )

        preflight = text[info_idx:fetch_idx]
        for token in ("bottle", "stable", "files", "arm64_sequoia", "sequoia"):
            self.assertIn(
                token,
                preflight,
                f"preflight must reference {token!r} "
                "(inspect .formulae[0].bottle.stable.files and the exact tags)",
            )
        self.assertTrue(
            "sys.exit" in preflight or "raise " in preflight,
            "preflight must exit non-zero on an unsupported host or absent tag",
        )

    def test_prose_does_not_claim_force_bottle_guarantees_failure(self) -> None:
        """No prose may credit `--force-bottle` itself with failing when no
        bottle exists -- the explicit preflight is the failure gate. The only
        allowed mention of `--force-bottle` alongside a "fail" word is an
        explicit negation ("does not fail")."""
        chunks = re.split(r"(?<=[.:])\s+|\n", self.text)
        offenders = [
            chunk
            for chunk in chunks
            if "--force-bottle" in chunk
            and re.search(r"\bfail", chunk)
            and not re.search(r"\bnot\b", chunk)
        ]
        self.assertEqual(
            offenders,
            [],
            "prose must not describe --force-bottle as the failure gate; the "
            f"explicit preflight is: {offenders}",
        )

    def test_prose_names_preflight_as_the_failure_gate(self) -> None:
        """The prose must positively attribute the staging failure to the
        preflight, not to brew fetch."""
        self.assertRegex(
            self.text,
            r"preflight[^.\n]*\bgate\b",
            "prose must state the preflight is the failure gate",
        )


if __name__ == "__main__":
    unittest.main()
