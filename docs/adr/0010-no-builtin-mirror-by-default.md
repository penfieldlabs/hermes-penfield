# ADR-0010: Builtin MEMORY.md/USER.md writes are not mirrored by default

- **Date:** 2026-06-26
- **Status:** Accepted

## Context

Hermes's built-in memory writes to MEMORY.md/USER.md. If Penfield is the
active memory provider and also mirrors those writes, the same
information ends up stored twice — once in the file, once in the graph —
and recall surfaces duplicates, confusing the agent and degrading search
quality.

## Decision

`on_memory_write` is a **no-op by default**. Penfield is the source of
truth when active. Users who want the mirror (e.g. migrating off the
built-in system) opt in via `mirror_builtin_writes: true` in
`penfield.json`. Even when opted in, content is secret-guarded
(ADR-0009).

## Consequences

- No duplicate stores; recall stays clean.
- Users migrating from the built-in system have an explicit, documented
  migration path rather than silent duplication.
- The default makes Penfield authoritative, which matches the spec's
  "single provider rule."
