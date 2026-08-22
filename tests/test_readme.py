"""Contract tests for README air-gapped staging instructions.

Standard library only: no PyYAML, no third-party imports. The executable tests
extract the documented Python preflight straight out of the README and drive it
through the documented data channel (environment variables), so a broken
invocation -- e.g. a heredoc that swallows piped stdin -- is caught here.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

README = Path(__file__).parent.parent / "README.md"

# The documented environment-variable channel the connected-host preflight uses
# to hand the captured `brew info --json=v2` payload and the resolved expected
# bottle tag to its heredoc Python program.
INFO_ENV = "KORVID_BREW_INFO_JSON"
TAG_ENV = "KORVID_EXPECTED_TAG"

# A minimal but schema-valid `brew info --json=v2` payload with both supported
# macOS 15 bottle tags present.
VALID_INFO = (
    '{"formulae": [{"versions": {"stable": "0.2.0"}, "bottle": {"stable": '
    '{"files": {"arm64_sequoia": {"url": "https://example/a"}, '
    '"sequoia": {"url": "https://example/b"}}}}}]}'
)


class ReadmePreflightExtractionMixin(unittest.TestCase):
    def setUp(self) -> None:
        self.text = README.read_text()

    def _connected_host_block(self) -> str:
        """Return the fenced ```bash block that stages on a connected Mac -- the
        one that both runs the `brew info --json=v2` preflight and the
        copy/fetch staging commands."""
        for block in re.findall(r"```bash\n(.*?)\n```", self.text, re.DOTALL):
            if "brew info --json=v2" in block and "brew fetch" in block:
                return block
        self.fail("connected-host bash block (brew info + brew fetch) not found")

    def _extract_preflight_python(self) -> str:
        """Return the Python program embedded in the connected-host heredoc."""
        block = self._connected_host_block()
        match = re.search(r"<<'PY'\n(.*?)\nPY\b", block, re.DOTALL)
        self.assertIsNotNone(
            match,
            "connected-host block must embed a heredoc-delimited (<<'PY' ... PY) "
            "Python preflight program",
        )
        return match.group(1)

    def _run_preflight(self, info: str, tag: str) -> subprocess.CompletedProcess:
        """Run the documented preflight Python, feeding JSON and the expected tag
        through the documented environment-variable channel (never through
        stdin, which a heredoc would clobber)."""
        src = self._extract_preflight_python()
        env = dict(os.environ)
        env[INFO_ENV] = info
        env[TAG_ENV] = tag
        return subprocess.run(
            [sys.executable, "-c", src],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )


class ReadmeAirgapPreflightTests(ReadmePreflightExtractionMixin):
    def test_normal_installation_claim_depends_on_formula_bottle_metadata(self) -> None:
        section = self.text.split(
            "### Normal restricted-network behavior", 1
        )[1].split("### Prefetch on a matching connected Mac", 1)[0]

        self.assertIn("When the formula lists a bottle", section)
        self.assertIn("falls back to a source build", section)
        self.assertIn("PyPI", section)

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


class ReadmePreflightStructureTests(ReadmePreflightExtractionMixin):
    def test_block_begins_with_pipefail_and_preflight_precedes_copy_and_fetch(
        self,
    ) -> None:
        """The connected-host block must begin with `set -euo pipefail`, and the
        `brew info --json=v2` preflight (through its heredoc terminator) must run
        before any `cp -R` copy or `brew fetch` staging command."""
        block = self._connected_host_block()
        first_line = block.lstrip().splitlines()[0]
        self.assertEqual(
            first_line.strip(),
            "set -euo pipefail",
            "connected-host block must begin with `set -euo pipefail`",
        )

        self.assertIn("cp -R", block, "block must copy the tap checkout")
        self.assertIn("brew fetch", block, "block must fetch bottles")
        self.assertIn("brew info --json=v2", block)

        set_idx = block.index("set -euo pipefail")
        info_idx = block.index("brew info --json=v2")
        # End of the preflight = the closing heredoc terminator.
        py_match = re.search(r"\nPY\b", block)
        self.assertIsNotNone(py_match, "preflight heredoc must close with `PY`")
        preflight_end = py_match.end()
        copy_idx = block.index("cp -R")
        fetch_idx = block.index("brew fetch")

        self.assertLess(set_idx, info_idx, "pipefail must precede the preflight")
        self.assertLess(set_idx, copy_idx, "pipefail must precede the copy")
        self.assertLess(set_idx, fetch_idx, "pipefail must precede the fetch")
        self.assertLess(
            preflight_end, copy_idx, "the preflight must complete before `cp -R`"
        )
        self.assertLess(
            preflight_end,
            fetch_idx,
            "the preflight must complete before `brew fetch`",
        )

    def test_preflight_python_uses_env_channel_not_stdin(self) -> None:
        """The heredoc Python must read the payload from environment variables,
        not stdin: a `... | python3 - <<'PY'` heredoc replaces the piped stdin,
        so `json.load(sys.stdin)` would always see EOF."""
        src = self._extract_preflight_python()
        self.assertIn(
            "os.environ",
            src,
            "preflight Python must read its input from os.environ, not stdin",
        )
        self.assertNotIn(
            "sys.stdin",
            src,
            "preflight Python must not read sys.stdin (a heredoc clobbers the "
            "piped stdin, so json.load(sys.stdin) always sees EOF)",
        )
        self.assertIn(INFO_ENV, src, f"preflight must read {INFO_ENV} from the env")
        self.assertIn(TAG_ENV, src, f"preflight must read {TAG_ENV} from the env")


class ReadmePreflightBehaviorTests(ReadmePreflightExtractionMixin):
    def test_valid_payload_with_expected_tag_exits_zero(self) -> None:
        """A schema-valid payload whose files map contains the expected tag must
        pass the preflight (exit 0)."""
        result = self._run_preflight(VALID_INFO, "arm64_sequoia")
        self.assertEqual(
            result.returncode,
            0,
            f"valid payload must pass preflight; stdout={result.stdout!r} "
            f"stderr={result.stderr!r}",
        )

    def test_missing_expected_tag_exits_nonzero(self) -> None:
        """A schema-valid payload that does not publish the expected tag must
        fail the preflight (non-zero)."""
        info_without_arm = (
            '{"formulae": [{"versions": {"stable": "0.2.0"}, "bottle": '
            '{"stable": {"files": {"sequoia": {"url": "https://example/b"}}}}}]}'
        )
        result = self._run_preflight(info_without_arm, "arm64_sequoia")
        self.assertNotEqual(
            result.returncode,
            0,
            "a payload missing the expected tag must fail the preflight",
        )

    def test_malformed_json_exits_nonzero(self) -> None:
        """A payload that is not valid JSON must fail the preflight (non-zero),
        not crash with an uncaught traceback that could be mistaken for success
        or hang waiting on stdin."""
        result = self._run_preflight("this is not json {", "arm64_sequoia")
        self.assertNotEqual(
            result.returncode,
            0,
            "malformed JSON must fail the preflight",
        )

    def test_schema_invalid_payload_exits_nonzero(self) -> None:
        """Valid JSON that does not match the expected brew info schema (no
        formulae / no bottle.stable.files) must fail the preflight."""
        for bad in ('{}', '{"formulae": []}', '{"formulae": [{"bottle": {}}]}'):
            with self.subTest(payload=bad):
                result = self._run_preflight(bad, "arm64_sequoia")
                self.assertNotEqual(
                    result.returncode,
                    0,
                    f"schema-invalid payload {bad!r} must fail the preflight",
                )


if __name__ == "__main__":
    unittest.main()
