# ADR-0007: Provider implements the real Hermes MemoryProvider ABC

- **Date:** 2026-06-26 (original), 2026-06-26 (revised)
- **Status:** Superseded by ADR-0013
- **Supersedes:** the original v0.1.0 text of this ADR

## Context (original — now known to be false)

The original v0.1.0 text of this ADR claimed the Hermes Agent
`MemoryProvider` ABC "could not be located in a canonical source" during
development, and that the provider therefore "duck-types the interface
rather than importing it."

**That premise was false.** Hermes is fully open source at
`github.com/NousResearch/hermes-agent`. The ABC sits at
`agent/memory_provider.py`, with a published developer guide and eight
real provider implementations (`plugins/memory/{honcho,hindsight,...}`)
to copy from. The original author either could not web-search during
that session or did not look, then wrote an ADR to dignify the gap.

## What that false premise cost

Because the provider was built against a guessed interface, three real
signature mismatches shipped:

1. **`register() -> type` vs the real `register(ctx) -> None`.** The real
   contract (verbatim from `plugins/memory/hindsight/__init__.py`) is
   `def register(ctx) -> None: ctx.register_memory_provider(Instance())`.
   The v0.1.0 version took zero arguments, so Hermes' loader raised
   `TypeError: register() takes 0 positional arguments but 1 was given`
   at plugin-load time. **The plugin could not load.**
2. **`on_pre_compress -> None` vs the real `-> str`.** The ABC returns a
   string that Hermes folds into the compression summary; v0.1.0 returned
   `None`, silently dropping the digest.
3. **`on_memory_write(action, target, content)` missing the `metadata`
   parameter** the ABC passes (`metadata: Optional[Dict] = None`).
4. **Tool schema key `input_schema` vs the real `parameters`.** The ABC
   docstring is explicit: `{"name", "description", "parameters"}`.

None of these were caught by the unit tests, because the tests mocked
everything around the seam — exactly the failure mode "fully verified"
is supposed to protect against.

## Decision (revised)

Build to the **real ABC**, copied from
`github.com/NousResearch/hermes-agent` and validated against the hindsight
provider. Specifically:

- `register(ctx) -> None`, calls `ctx.register_memory_provider(instance)`.
- `on_pre_compress -> str` (returns the digest; stored as a checkpoint
  memory AND returned for Hermes' compression summary).
- `on_memory_write(action, target, content, metadata=None)`.
- Tool schemas use `"parameters"`, not `"input_schema"`.
- All other signatures (`name`, `is_available`, `initialize`,
  `system_prompt_block`, `prefetch`, `sync_turn`, `queue_prefetch`,
  `get_tool_schemas`, `handle_tool_call`, `get_config_schema`,
  `save_config`, `shutdown`, optional hooks) line up with the ABC.

The provider still does **not** `import` Hermes at module load — the ABC
is structurally matched, not subclassed — so unit tests run without
Hermes installed. The difference from v0.1.0 is that the match is now
verified against real source, not guessed.

## Consequences

- The plugin now actually loads. `tests/test_plugin_contract.py` pins
  the `register(ctx)` contract with a mock `PluginContext` so a regression
  raises immediately rather than at Hermes load time.
- The lesson, recorded so it's not relearned: **"I couldn't find the
  source" is not a finding worth an ADR — it's a reason to search harder.**
  When an integration seam exists, the default is to clone the real
  thing and diff, not to write a defensive ADR. ADRs document real
  decisions; they do not legitimate skipped work.
- The original ADR-0007 text is preserved above so the error is visible,
  not memory-holed.
