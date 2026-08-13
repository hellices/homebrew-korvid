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

## Maintenance

`Formula/korvid.rb` is **generated**, not written. Every resource comes
from the `uv.lock` the corresponding korvid release was tested against, by
[`scripts/generate_homebrew_formula.py`](https://github.com/hellices/korvid/blob/main/scripts/generate_homebrew_formula.py)
in the main repository. The release workflow opens the bump here
automatically; do not edit the formula by hand.
