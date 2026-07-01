# ADR-0004: Missing lifecycle endpoints — defensive no-ops + synthesized checkpoints

- **Date:** 2026-06-29
- **Status:** Accepted

## Context

Hermes' MemoryProvider ABC calls lifecycle hooks (`sync_turn`,
`on_pre_compress`) that the original implementation spec mapped to
endpoints that don't exist in the real Penfield API.

## Decision

- **`sync_turn`** is an intentional no-op. Turn-level context is
  captured via explicit `penfield_store` calls and `on_pre_compress`.
- **`on_pre_compress`** synthesizes the intent using a real endpoint:
  when enabled (default), it stores a compact `checkpoint`-type memory
  via `POST /memories` summarizing the tail of the message window.

## Consequences

- Neither hook ever makes a call to a non-existent endpoint.
- Pre-compress checkpoints accumulate in the memory graph; they're
  tagged for findability and set to low importance.
