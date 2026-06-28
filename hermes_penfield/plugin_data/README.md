# penfield (Hermes memory-provider plugin directory)

This directory is the **Hermes discovery shim** for hermes-penfield. It is
what Hermes' memory-plugin loader actually finds and loads — not the pip
package's entry point. See ADR-0014 for why.

## What lives where

- **`pip install hermes-penfield`** installs the implementation
  (`hermes_penfield/`) — client, auth, tools, provider, CLI.
- **This directory** is the thin shim that Hermes' directory-based
  discovery (`plugins/memory/__init__.py:_iter_provider_dirs`) requires.
  It imports `PenfieldMemoryProvider` from the installed package and
  exposes `register(ctx)` matching the Hermes contract.

## Install

After `pip install hermes-penfield`:

```bash
hermes-penfield install            # copies this dir to $HERMES_HOME/plugins/penfield/
```

Then set `memory.provider: penfield` in `$HERMES_HOME/config.yaml`.

## Why not just a pip entry point?

Hermes' memory subsystem discovers providers by scanning two
directories only — bundled `plugins/memory/<name>/` and user
`$HERMES_HOME/plugins/<name>/`. The general plugin entry-point path has
no `register_memory_provider` on its context, so a pip-only memory
provider is silently never registered. This shim exists to satisfy the
directory discovery. (See ADR-0014.)
