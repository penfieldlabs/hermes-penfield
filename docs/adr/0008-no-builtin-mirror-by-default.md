# ADR-0008: Builtin MEMORY.md/USER.md writes are not mirrored by default

- **Date:** 2026-06-29
- **Status:** Accepted

## Context

If Penfield mirrors Hermes's built-in memory writes, the same information
ends up stored twice — once in the file, once in the graph — and recall
surfaces duplicates.

## Decision

`on_memory_write` is a no-op by default. Penfield is the source of truth
when active. Users who want the mirror opt in via `mirror_builtin_writes:
true` in config.

## Consequences

- No duplicate stores; recall stays clean.
- Migration from the built-in system is explicit, not silent.
