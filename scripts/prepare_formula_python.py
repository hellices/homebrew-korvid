"""Prepare a formula's Homebrew Python on disposable CI runners."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


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


def remove_legacy_openssl_aliases(prefix: Path) -> None:
    """Remove only stale bin aliases targeting the legacy OpenSSL opt path."""
    for name in ("openssl", "c_rehash"):
        alias = prefix / "bin" / name
        if not alias.is_symlink():
            continue
        target = Path(os.path.abspath(alias.parent / alias.readlink()))
        if target == prefix / "opt/openssl@1.1/bin" / name:
            print(f"Removing legacy OpenSSL alias: {alias}", flush=True)
            alias.unlink()


def prepare_formula_python(formula: str) -> str:
    """Prepare CI dependency links and the selected Python, propagating failures."""
    output = subprocess.check_output(["brew", "info", "--json=v2", formula], text=True)
    python_formula = formula_python(json.loads(output))
    installed = subprocess.check_output(["brew", "list", "--formula"], text=True)
    if "openssl@1.1" in installed.splitlines():
        # Hosted Intel images can retain aliases that block openssl@3 installation.
        subprocess.run(["brew", "unlink", "openssl@1.1"], check=True)
        prefix = Path(subprocess.check_output(["brew", "--prefix"], text=True).strip())
        if not prefix.is_absolute() or prefix == Path("/"):
            raise ValueError("Expected an absolute non-root Homebrew prefix")
        remove_legacy_openssl_aliases(prefix)
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
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    print(f"Prepared {python_formula} for {args.formula}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
