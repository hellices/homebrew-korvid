"""The Intel build toolchain installs verified upstream binaries, not LLVM."""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).parents[1]
FORMULA = ROOT / "Formula/korvid-rust.rb"


def nodes(tree: object) -> Iterator[list[Any]]:
    if isinstance(tree, list):
        yield tree
        for child in tree:
            yield from nodes(child)


def tokens(tree: object) -> list[str]:
    return [
        node[1] for node in nodes(tree)
        if node and isinstance(node[0], str) and node[0].startswith("@")
    ]


class PrebuiltRustTests(unittest.TestCase):
    def formula(self) -> str:
        self.assertTrue(FORMULA.is_file(), "prebuilt Intel toolchain formula is missing")
        return FORMULA.read_text()

    def ruby_tree(self, text: str) -> object:
        parsed = subprocess.run(
            ["ruby", "-r", "ripper", "-r", "json", "-e",
             "print JSON.generate(Ripper.sexp(STDIN.read))"],
            input=text, capture_output=True, text=True, check=True,
        )
        tree = json.loads(parsed.stdout)
        self.assertIsNotNone(tree, "formula must parse as Ruby")
        return tree

    def assert_no_compiler_dependency(self, text: str) -> None:
        for node in nodes(self.ruby_tree(text)):
            if node and node[0] == "command" and tokens(node[1]) == ["depends_on"]:
                for name in tokens(node[2]):
                    self.assertNotRegex(name, r"^(rust|llvm|rustup)(@.*)?$")

    def assert_native_smoke_calls(self, text: str) -> None:
        blocks = [
            node[2] for node in nodes(self.ruby_tree(text))
            if node and node[0] == "method_add_block" and tokens(node[1]) == ["test"]
        ]
        self.assertEqual(len(blocks), 1)
        calls = [
            tokens(node) for node in nodes(blocks[0])
            if node and node[0] in ("command", "method_add_arg")
        ]
        self.assertIn(["system", "bin", "rustc", "hello.rs", "-o", "hello"], calls)
        self.assertIn(["shell_output", "./hello"], calls)

    def test_toolchain_has_no_compiler_build_dependency(self) -> None:
        text = self.formula()
        self.assertIn("depends_on :macos", text)
        self.assertIn("depends_on arch: :x86_64", text)
        self.assert_no_compiler_dependency(text)
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
        self.assert_native_smoke_calls(text)
        self.assertIn('bin/"cargo"', text)
        result = subprocess.run(
            ["ruby", "-c", str(FORMULA)], capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_upstream_rpath_is_preserved_without_skipping_linkage_checks(self) -> None:
        text = self.formula()
        self.assertRegex(text, r"(?m)^  preserve_rpath$")
        self.assertNotIn("skip_relocation", text)
        self.assertNotIn("skip_clean", text)

    def test_single_and_double_quoted_compiler_dependencies_are_rejected(self) -> None:
        for quote in ("'", '"'):
            for name in ("rust", "llvm", "rustup", "llvm@23"):
                with self.subTest(quote=quote, name=name):
                    mutated = self.formula().replace(
                        "  depends_on :macos",
                        f"  depends_on :macos\n  depends_on {quote}{name}{quote}",
                    )
                    with self.assertRaisesRegex(AssertionError, "Regex matched"):
                        self.assert_no_compiler_dependency(mutated)

    def test_commented_native_compile_or_run_is_rejected(self) -> None:
        for call in ('system bin/"rustc"', 'assert_equal "korvid toolchain'):
            with self.subTest(call=call):
                mutated = self.formula().replace(f"    {call}", f"    # {call}")
                with self.assertRaisesRegex(AssertionError, "not found"):
                    self.assert_native_smoke_calls(mutated)
