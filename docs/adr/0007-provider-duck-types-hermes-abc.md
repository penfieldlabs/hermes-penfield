# ADR-0007: Provider duck-types the Hermes ABC; Hermes not imported at dev time

- **Date:** 2026-06-26
- **Status:** Accepted

## Context

The Hermes Agent `MemoryProvider` ABC (`agent/memory_provider.py`) is the
integration seam the provider must implement. During v0.1.0 development
no canonical Hermes source repository could be verified, so the exact
ABC signature could not be confirmed against primary source. Importing
`agent` at module load would also force every unit test and every
`pip install` to have Hermes present, which is wrong for a standalone
plugin.

## Decision

`PenfieldMemoryProvider` implements the spec's documented method set
(`initialize`, `is_available`, `get_tool_schemas`, `handle_tool_call`,
`get_config_schema`, `save_config`, `system_prompt_block`, `prefetch`,
`sync_turn`, `on_pre_compress`, `on_memory_write`, `on_session_end`,
`shutdown`) **without importing Hermes**. The provider duck-types the
interface; it does not subclass a Hermes base class.

## Consequences

- **This is the one layer with unverified risk.** If the real Hermes
  ABC differs (method names, signatures, registration protocol), this
  single file is the adjustment point. Everything below it (client,
  auth, tools, constants) is fully grounded in the verified Penfield API.
- Unit tests run without Hermes installed.
- When Hermes source is available, reconciling this file is the
  integration task. A passing `hermes memory setup` + `hermes penfield
  status` smoke test is the acceptance criterion.
- The `register()` function in `__init__.py` is deliberately lazy so the
  plugin entry point doesn't pull Hermes into the import graph until
  Hermes itself is resolving it.
