"""Prepare a formula's Homebrew Python on disposable CI runners."""

from __future__ import annotations

import argparse
import json
import re
import subprocess


def formula_python(metadata: object) -> str:
    """Select exactly one versioned interpreter from formula metadata."""
    if not isinstance(metadata, dict):
        raise ValueError("Expected formula metadata")
    formulae = metadata.get("formulae")
    if not isinstance(formulae, list) or len(formulae) != 1 or not isinstance(formulae[0], dict):
        raise ValueError("Expected exactly one formula")
    dependencies = formulae[0].get("dependencies")
    if not isinstance(dependencies, list):
        raise ValueError("Expected a dependency list")
    names: list[str] = []
    for name in dependencies:
        if not isinstance(name, str):
            raise ValueError("Expected dependency names to be strings")
        if re.fullmatch(r"python@3\.[0-9]+", name):
            names.append(name)
    if len(names) != 1:
        raise ValueError("Expected exactly one versioned Python dependency")
    return names[0]


def prepare_formula_python(formula: str) -> str:
    """Install and link only the selected interpreter, propagating failures."""
    output = subprocess.check_output(["brew", "info", "--json=v2", formula], text=True)
    python_formula = formula_python(json.loads(output))
    subprocess.run(["brew", "install", "--overwrite", "--verbose", python_formula], check=True)
    subprocess.run(["brew", "link", "--overwrite", python_formula], check=True)
    return python_formula


def main() -> int:
    """Prepare the interpreter required by a named formula."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("formula")
    args = parser.parse_args()
    try:
        python_formula = prepare_formula_python(args.formula)
    except (ValueError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    print(f"Prepared {python_formula} for {args.formula}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
