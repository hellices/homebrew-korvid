"""Only runtime dependencies, not the application, enter the prefix cache."""

import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


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
        runtime = re.findall(
            r'^  depends_on "([^"]+)"$', (ROOT / "Formula/korvid.rb").read_text(), re.MULTILINE,
        )
        self.assertEqual({name for name, _options in entries}, set(runtime))

    def test_duplicate_manual_python_installer_is_removed(self) -> None:
        self.assertFalse((ROOT / "scripts/prepare_formula_python.py").exists())
