# ADR-0014: Memory providers are directory-installed, not pip entry points

- **Date:** 2026-06-26
- **Status:** Accepted
- **Follows from:** ADR-0013

## Context

The second major seam error in v0.1.0 (after the ABC-signature bugs in
ADR-0007/0013): the plugin was packaged with a pip entry point
(`[project.entry-points."hermes_agent.plugins"] penfield = "hermes_penfield:register"`)
and the README claimed "Hermes discovers the provider via the
hermes_agent.plugins entry point."

Verified against real source (`NousResearch/hermes-agent`): **that claim
is false for memory providers.** The memory subsystem discovers
providers by scanning two **directories** only — `plugins/memory/<name>/`
(bundled) and `$HERMES_HOME/plugins/<name>/` (user-installed). See
`plugins/memory/__init__.py:_iter_provider_dirs` (iterdir, not
importlib.metadata). The general plugin entry-point path uses a
different `PluginContext` (in `hermes_cli/plugins.py`) that has no
`register_memory_provider` method — confirmed by the source comment at
`hermes_cli/plugins.py:~1476`: *"the general PluginManager … has no
register_memory_provider on PluginContext."*

So a pip-only memory provider, on install, has its entry point fired by
the general PluginManager → `ctx.register_memory_provider(...)` →
`AttributeError` → swallowed by `_load_plugin`'s `except Exception` →
plugin marked failed with one WARNING. **Silent non-load.** Pip-install
it exactly as the README documented and the memory provider never
registers.

## Decision

Ship a **directory-install shim** as the discovery mechanism, and stop
claiming pip entry-point discovery.

Concretely:

- `plugin_dir/` in the repo contains the shim Hermes' directory loader
  finds: `__init__.py` (defines `register(ctx)` calling
  `ctx.register_memory_provider(PenfieldMemoryProvider())`, and contains
  the literal `register_memory_provider` so the loader's text-scan
  `_is_memory_provider_dir` recognizes it), `plugin.yaml`, `README.md`.
- `hermes-penfield install` copies `plugin_dir/` into
  `$HERMES_HOME/plugins/penfield/` (the user-installed scan dir).
- The `[project.entry-points."hermes_agent.plugins"]` declaration is
  **removed** from `pyproject.toml` — keeping it would be misleading, and
  the entry-point path cannot register a memory provider.
- The pip package still ships the implementation (`hermes_penfield/`); the
  shim just imports from it after `pip install hermes-penfield`.

## Consequences

- The plugin now loads via the path Hermes actually uses for memory
  providers. Verified end-to-end: `_is_memory_provider_dir` recognizes
  the shim, and `register(ctx)` with a `_ProviderCollector` replica
  registers `PenfieldMemoryProvider`.
- `tests/test_plugin_contract.py::TestDirectoryDiscovery` pins this: it
  asserts the shim is text-recognized, loads via `spec_from_file_location`
  exactly as the loader does, and registers; plus that `install` drops a
  working shim; plus that pyproject does NOT advertise the dead entry
  point.
- The real acceptance test remains: `pip install -e hermes-agent`, drop
  the shim into a real `$HERMES_HOME`, set `memory.provider: penfield`,
  and watch it register in a running Hermes. That's the live integration
  ADR-0013 demands and that no unit test fully substitutes for.
- Install UX changed: users run `pip install hermes-penfield` **then**
  `hermes-penfield install`. Documented in the README and the install
  command's output.
