# ADR-0011: Context tools — partial implementation, save_context held

- **Date:** 2026-06-29
- **Status:** Accepted

## Context

The Penfield MCP defines three context-management tools. All map to
regular memory operations — checkpoints are just memories with
`memory_type=checkpoint`. However, `save_context` includes server-side
enrichment (reference parsing, relationship creation, name uniqueness)
that the REST API does not expose directly.

## Decision

Implement `list_contexts` and `restore_context` now. Hold
`save_context` for v1.1.0.

**Architecture decision: no MCP shim.** The REST client + drift test is
the right architecture. An MCP shim is rejected because:

1. Hermes already speaks MCP natively — users can add the Penfield MCP
   server for full enrichment. No plugin needed for that.
2. The only reason hermes-penfield exists as a plugin is lifecycle hooks
   (prefetch, sync_turn, on_pre_compress, system_prompt_block). Raw MCP
   tools can't do those.
3. The shim would add three layers (Hermes → plugin → MCP → REST) where
   two work fine.
4. Subprocess lifecycle management (spawning, crash recovery, auth
   duplication) just to avoid a ~30-line drift test.

The clean architecture: hermes-penfield does lifecycle hooks + tools via
REST. A drift test catches tool surface divergence. Users who want full
MCP enrichment add the Penfield MCP server alongside the plugin.

### save_context path forward

Reimplement the enrichment client-side in v1.1.0:
1. Parse `memory_id:` / `memory:` references from description
2. Resolve via search_hybrid
3. Create relationships via relationship_create
4. Enforce name uniqueness via memory_list?memory_type=checkpoint
5. Return enriched response

## Consequences

- 16 tools total (save_context held).
- Checkpoints created via PENpi are visible and restorable through Hermes.
- The `save_context` gap is documented with a clear path to closure.
