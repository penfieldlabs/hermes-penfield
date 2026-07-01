# ADR-0011: Context tools — full implementation, no MCP shim

- **Date:** 2026-07-01
- **Status:** Accepted

## Context

The Penfield MCP defines three context-management tools: `save_context`,
`restore_context`, and `list_contexts`. All map to regular memory
operations — checkpoints are memories with `memory_type=checkpoint`.

`save_context` includes enrichment (reference parsing, name uniqueness,
recent-memory snapshotting) that the MCP server does internally. Since
we're a REST client, we reimplement that enrichment client-side.

## Decision

All three context tools are implemented via REST, reimplementing the
MCP server's enrichment logic:

- **`save_context`**: parses `memory_id: UUID` and `memory: 'phrase'`
  references from description, resolves them, snapshots recent memories,
  enforces name uniqueness, creates a structured checkpoint memory.
- **`restore_context`**: searches checkpoint-type memories by name.
  Special case: `name="awakening"` loads personality config.
- **`list_contexts`**: queries checkpoint-type memories with optional
  name filtering.

**No MCP shim.** The REST client + drift test is the right architecture:

1. Hermes already speaks MCP natively — users can add the Penfield MCP
   server for full enrichment if desired.
2. hermes-penfield exists as a plugin for lifecycle hooks (prefetch,
   sync_turn, on_pre_compress, system_prompt_block). MCP tools can't
   provide those.
3. The shim would add three layers where two work fine.
4. Subprocess lifecycle management just to avoid a drift test is not
   worth the complexity.

## Consequences

- 17 tools total — full MCP parity.
- All context operations work via REST without MCP server dependency.
- A drift test catches tool surface divergence from the MCP.
