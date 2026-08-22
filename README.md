# homebrew-korvid

Homebrew tap for [korvid](https://github.com/hellices/korvid), an AI-native
Kubernetes TUI.

```sh
brew install hellices/korvid/korvid
```

korvid needs Python 3.11+, and macOS ships 3.9. The formula builds against
Homebrew's own Python, so the system interpreter is irrelevant.

## What is installed

The base package plus the `[agent]` extra — the AI features are the point
of korvid, and `httpx`/`keyring` are what they need.

The `[mcp]` extra is **not** installed. It puts an HTTP server on the
machine, and a convenience channel should not opt anyone into that. If you
want it, install from PyPI instead:

```sh
uv tool install 'korvid[mcp]'
```

## Restricted-network and air-gapped installation

### Normal restricted-network behavior

When the formula lists a bottle for the host's macOS 15 tag (Apple Silicon or
Intel), `brew install hellices/korvid/korvid` downloads that pre-built bottle
from GitHub Releases and does **not** contact PyPI or
`files.pythonhosted.org`. If matching bottle metadata has not been merged yet,
Homebrew falls back to a source build, which requires PyPI. Homebrew's own
dependency bottles are also fetched from GitHub.

### Prefetch on a matching connected Mac

Run the following on a Mac that matches the **architecture and macOS version**
of the target host (the bottle tag must match).
The preflight below is the failure gate for staging.
Fetching with `--force-bottle` does not fail when no bottle exists, so an
explicit `brew info --json=v2` schema and tag check runs first and stops
staging on an unsupported host before anything is fetched.

```bash
set -euo pipefail

brew tap hellices/korvid
tap="$(brew --repository)/Library/Taps/hellices/homebrew-korvid"

# Preflight (the failure gate): stop on this connected Mac unless a bottle for
# its exact architecture and macOS 15 is published. Reject an unsupported macOS
# version or CPU up front; only arm64_sequoia (Apple Silicon) and sequoia
# (Intel) on macOS 15 are supported.
macos_version="$(sw_vers -productVersion)"
if [ "${macos_version%%.*}" != "15" ]; then
  echo "unsupported macOS ${macos_version}: only macOS 15 bottles are published" >&2
  exit 1
fi
case "$(uname -m)" in
  arm64)  expected_tag="arm64_sequoia" ;;
  x86_64) expected_tag="sequoia" ;;
  *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

# Capture the metadata once, then hand the JSON payload and the resolved tag to
# the validator through environment variables. Piping into `python3 - <<'PY'`
# would not work: the heredoc becomes the program on stdin, so a
# `json.load(sys.stdin)` would only ever read the script itself, never the JSON.
brew_info_json="$(brew info --json=v2 hellices/korvid/korvid)"
KORVID_BREW_INFO_JSON="$brew_info_json" KORVID_EXPECTED_TAG="$expected_tag" \
  python3 - <<'PY'
import json, os, sys

expected_tag = os.environ["KORVID_EXPECTED_TAG"]
try:
    data = json.loads(os.environ["KORVID_BREW_INFO_JSON"])
except json.JSONDecodeError as exc:
    sys.exit(f"SCHEMA_ERROR: brew info payload is not valid JSON: {exc}")
if not isinstance(data, dict) or not isinstance(data.get("formulae"), list) or not data["formulae"]:
    sys.exit("SCHEMA_ERROR: brew info payload missing formulae")
formula = data["formulae"][0]
if not isinstance(formula, dict):
    sys.exit("SCHEMA_ERROR: formula entry is not an object")
bottle = formula.get("bottle")
if not isinstance(bottle, dict) or "stable" not in bottle:
    sys.exit("SCHEMA_ERROR: formula has no stable bottle block")
stable = bottle["stable"]
if not isinstance(stable, dict) or not isinstance(stable.get("files"), dict):
    sys.exit("SCHEMA_ERROR: stable bottle has no files map")
files = stable["files"]
if expected_tag not in files:
    sys.exit(f"no {expected_tag} bottle published for korvid; cannot stage on this Mac")
print(f"preflight ok: {expected_tag} bottle is published")
PY

# Staging (copy + fetch) runs only after the preflight above exits 0.
cp -R "$tap" "$PWD/homebrew-korvid"
mkdir -p "$PWD/korvid-homebrew-cache"
HOMEBREW_CACHE="$PWD/korvid-homebrew-cache" \
  brew fetch --force --deps --force-bottle hellices/korvid/korvid
```

This copies the tap checkout so that formula metadata is transferred verbatim,
and populates `korvid-homebrew-cache/` with the Korvid bottle and every
Homebrew dependency bottle. `--force-bottle` makes Homebrew download the Korvid
bottle rather than silently building it from source; the preflight above, not
that flag, is what stops staging when no matching bottle exists. Transfer both
`homebrew-korvid/` and `korvid-homebrew-cache/` to the restricted host.

### Disconnected installation

On the target host, place the tap checkout where Homebrew expects it and
point the installer at the pre-populated cache:

```bash
set -euo pipefail

tap="$(brew --repository)/Library/Taps/hellices/homebrew-korvid"
mkdir -p "$(dirname "$tap")"
rm -rf -- "$tap"
cp -R /path/from-transfer/homebrew-korvid "$tap"

HOMEBREW_NO_AUTO_UPDATE=1 \
HOMEBREW_CACHE=/path/from-transfer/korvid-homebrew-cache \
  brew install hellices/korvid/korvid
```

`HOMEBREW_NO_AUTO_UPDATE=1` prevents Homebrew from attempting a network fetch
before the install begins. Homebrew itself, current formula metadata for all
Homebrew dependencies, and every dependency bottle must already be present on
the host. An internal mirror that serves the same versioned GitHub Release
paths may substitute for removable media.

**Caveats:** bottle availability is controlled by the Homebrew infrastructure
that builds against tagged releases; the preflight above verifies that a bottle
for your exact macOS version and architecture exists before staging. That
explicit preflight, not `brew fetch`, is what fails on the connected machine
when no `arm64_sequoia` (Apple Silicon) or `sequoia` (Intel) bottle is
published for macOS 15, so an unsupported or not-yet-published target is caught
during staging rather than on the disconnected host.
Note that `brew fetch --force-bottle` alone does not fail on a missing
bottle, which is why the explicit preflight runs first. A normal online
`brew install hellices/korvid/korvid` behaves differently again: without
`--force-bottle`, Homebrew silently falls back to building Korvid from source
when no matching bottle exists, and that source build reaches PyPI
(`files.pythonhosted.org`) for the Python dependencies, which a restricted
network may block.

## Maintenance

`Formula/korvid.rb` is **generated**, not written. Every resource comes
from the `uv.lock` the corresponding korvid release was tested against, by
[`scripts/generate_homebrew_formula.py`](https://github.com/hellices/korvid/blob/main/scripts/generate_homebrew_formula.py)
in the main repository. The release workflow opens the bump here
automatically; do not edit the formula by hand.
