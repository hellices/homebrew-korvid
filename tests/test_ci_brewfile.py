"""Only runtime dependencies, not the application, enter the prefix cache."""

import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


def runtime_dependencies(formula: str) -> set[str]:
    return set(re.findall(
        r"""^[ \t]+depends_on[ \t]+["']([^"']+)["'][ \t]*(?:#.*)?$""",
        formula, re.MULTILINE,
    ))


class CIBrewfileTests(unittest.TestCase):
    def test_cache_contains_only_core_runtime_and_overwrites_known_alias_conflicts(self) -> None:
        brewfile = ROOT / "Brewfile"
        self.assertTrue(brewfile.is_file(), "CI runtime Brewfile is missing")
        result = subprocess.run(
            ["ruby", "-r", "json", "-e",
             'entries=[]; define_singleton_method(:brew) { |name, **options| '
             'entries << [name, options] }; eval(STDIN.read); print JSON.generate(entries)'],
            input=brewfile.read_text(), text=True, capture_output=True, check=True,
        )
        entries = json.loads(result.stdout)
        self.assertEqual(entries, [
            ["openssl@3", {"args": ["overwrite"], "link": "overwrite"}],
            ["python@3.13", {"args": ["overwrite"], "link": "overwrite"}],
            ["libyaml", {}],
        ])
        self.assertEqual(
            {name for name, _options in entries},
            runtime_dependencies((ROOT / "Formula/korvid.rb").read_text()),
        )

    def test_runtime_dependency_indentation_does_not_hide_new_packages(self) -> None:
        formula = (ROOT / "Formula/korvid.rb").read_text()
        for indent in (" ", "    ", "\t"):
            with self.subTest(indent=indent):
                mutated = formula.replace(
                    '  depends_on "libyaml"',
                    f'  depends_on "libyaml"\n{indent}depends_on "readline"',
                )
                self.assertIn("readline", runtime_dependencies(mutated))

    def test_duplicate_manual_python_installer_is_removed(self) -> None:
        self.assertFalse((ROOT / "scripts/prepare_formula_python.py").exists())
