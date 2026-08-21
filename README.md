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

On macOS 15 (Apple Silicon and Intel), `brew install hellices/korvid/korvid`
downloads a pre-built Korvid bottle from GitHub Releases and does **not**
contact PyPI or `files.pythonhosted.org`. Homebrew's own dependency bottles
are also fetched from GitHub; no other external network paths are required
provided those two domains are reachable.

### Prefetch on a matching connected Mac

Run the following on a Mac that matches the **architecture and macOS version**
of the target host (the bottle tag must match):

```bash
brew tap hellices/korvid
tap="$(brew --repository)/Library/Taps/hellices/homebrew-korvid"
cp -R "$tap" "$PWD/homebrew-korvid"
mkdir -p "$PWD/korvid-homebrew-cache"
HOMEBREW_CACHE="$PWD/korvid-homebrew-cache" \
  brew fetch --force --deps --force-bottle hellices/korvid/korvid
```

This copies the tap checkout so that formula metadata is transferred verbatim,
and populates `korvid-homebrew-cache/` with the Korvid bottle and every
Homebrew dependency bottle. `--force-bottle` forces Homebrew to download the
Korvid bottle instead of silently falling back to a source build, so staging
fails immediately if no bottle matches this Mac. Transfer both
`homebrew-korvid/` and `korvid-homebrew-cache/` to the restricted host.

### Disconnected installation

On the target host, place the tap checkout where Homebrew expects it and
point the installer at the pre-populated cache:

```bash
tap="$(brew --repository)/Library/Taps/hellices/homebrew-korvid"
mkdir -p "$(dirname "$tap")"
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
that builds against tagged releases; verify that a bottle for your exact
macOS version and architecture exists before staging. Because of
`--force-bottle`, the `brew fetch` step above **fails on the connected
machine** when no Korvid bottle matches its macOS version and architecture, so
an unsupported or not-yet-published target is caught during staging rather than
on the disconnected host. A normal online `brew install hellices/korvid/korvid`
behaves differently: without `--force-bottle`, Homebrew silently falls back to
building Korvid from source when no matching bottle exists, and that source
build reaches PyPI (`files.pythonhosted.org`) for the Python dependencies,
which a restricted network may block.

## Maintenance

`Formula/korvid.rb` is **generated**, not written. Every resource comes
from the `uv.lock` the corresponding korvid release was tested against, by
[`scripts/generate_homebrew_formula.py`](https://github.com/hellices/korvid/blob/main/scripts/generate_homebrew_formula.py)
in the main repository. The release workflow opens the bump here
automatically; do not edit the formula by hand.
