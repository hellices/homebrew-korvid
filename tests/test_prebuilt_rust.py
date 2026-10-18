"""The Intel build toolchain installs verified upstream binaries, not LLVM."""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
FORMULA = ROOT / "Formula/korvid-rust.rb"


class PrebuiltRustTests(unittest.TestCase):
    def formula(self) -> str:
        self.assertTrue(FORMULA.is_file(), "prebuilt Intel toolchain formula is missing")
        return FORMULA.read_text()

    def test_toolchain_has_no_compiler_build_dependency(self) -> None:
        text = self.formula()
        self.assertIn("depends_on :macos", text)
        self.assertIn("depends_on arch: :x86_64", text)
        self.assertNotRegex(text, r'depends_on "(?:rust|llvm|rustup)')
        self.assertIn("keg_only", text)
        self.assertNotIn('system "cargo", "build"', text)
        self.assertNotIn('system "make"', text)

    def test_all_three_components_are_pinned_official_intel_binaries(self) -> None:
        text = self.formula()
        urls = re.findall(r'url "([^"]+)"', text)
        hashes = re.findall(r'sha256 "([a-f0-9]{64})"', text)
        self.assertEqual(len(hashes), 3)
        self.assertEqual(
            urls,
            [
                f"https://static.rust-lang.org/dist/2026-10-15/"
                f"{component}-1.99.1-x86_64-apple-darwin.tar.xz"
                for component in ("rustc", "cargo", "rust-std")
            ],
        )
        self.assertEqual(hashes, [
            "203feef2c2165d7dda1f30ccf75590f72a6abc52ce259ed1c418de84a6887698",
            "6636dabcec164909d1c6eb9e2c596148787c84f7d3a02ff68a141569077f80b5",
            "484d5b88b424ddd29df196e1eb9420c8335bb5fac90a3c37140253345a8c65c4",
        ])

    def test_install_uses_keg_prefix_and_tests_native_compilation(self) -> None:
        text = self.formula()
        self.assertIn('"--prefix=#{prefix}"', text)
        self.assertIn('"--disable-ldconfig"', text)
        self.assertIn("resource.stage", text)
        self.assertIn('system bin/"rustc", "hello.rs", "-o", "hello"', text)
        self.assertIn('shell_output("./hello")', text)
        self.assertIn('bin/"cargo"', text)
        result = subprocess.run(
            ["ruby", "-c", str(FORMULA)], capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
