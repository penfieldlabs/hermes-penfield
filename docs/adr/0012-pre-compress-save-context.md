# ADR-0012: on_pre_compress uses save_context for checkpoints

- **Date:** 2026-07-01
- **Status:** Accepted

## Context

The Hermes `on_pre_compress` hook fires before context compression discards
messages. The provider needs to save a checkpoint capturing what was being
discussed so the agent can restore context later.

The original v0.1.0 implementation stored a raw memory with
`memory_type: checkpoint` and returned the digest text to Hermes. This was
functionally OK but didn't use the `save_context` tool — meaning no
reference parsing, no hybrid-search memory snapshot, no enriched checkpoint
structure. It was a poor man's checkpoint.

## Decision

`on_pre_compress` now calls `penfield_save_context` with:

- **name**: `pre-compress-{session_id[:8]}-{timestamp}` (ISO 8601, always unique)
- **description**: Meaningful summary extracted from the messages being
  compressed (conversation turns, not metadata prefixes)

This leverages the full `save_context` pipeline: hybrid search against the
description captures relevant memories from the knowledge graph, references
in the description are parsed and resolved, and the checkpoint is structured
identically to a manually-created context checkpoint.

**Returns empty string.** Hermes compresses how it wants — we don't inject
into the compression prompt. The checkpoint is the persistence mechanism,
not the return value.

## Consequences

- Pre-compress checkpoints are first-class context checkpoints — visible in
  `list_contexts`, restorable via `restore_context`.
- The checkpoint name includes timestamp so multiple compressions in one
  session don't collide.
- The description leads with substantive content (not metadata) because the
  first 200 chars become the hybrid search query that selects which memories
  to bundle.
- Config: existing `pre_compress_save: bool = True`, no changes.
