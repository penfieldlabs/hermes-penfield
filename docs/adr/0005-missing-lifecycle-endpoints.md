# ADR-0005: Missing lifecycle endpoints — defensive no-ops + synthesized checkpoints

- **Date:** 2026-06-26
- **Status:** Accepted

## Context

The v1.0 spec described two provider lifecycle hooks backed by Penfield
endpoints that **do not exist** in the real API:

- `sync_turn` → `POST /turns/sync`
- `on_pre_compress` → `POST /context/save`

Implementing these as real calls would fail on every turn. Silently
swallowing the failure would hide a broken contract.

## Decision

- **`sync_turn`** is an intentional **no-op** in v0.1.0. It logs at debug
  level and returns. Turn-level context is still captured via explicit
  `penfield_store` calls and via `on_pre_compress`. The
  `sync_turn_enabled` config flag exists for forward compatibility if a
  real endpoint appears.
- **`on_pre_compress`** synthesizes the intent using a real endpoint:
  when `pre_compress_save` is enabled (default), it stores a compact
  `memory_type: checkpoint` memory via `POST /memories`, summarizing the
  tail of the message window (last 20 messages, one line each). This
  uses `source_type: checkpoint`, matching the API's documented
  semantics.

## Consequences

- Neither hook ever makes a call to a non-existent endpoint.
- Pre-compress checkpoints accumulate in the user's memory graph; they're
  tagged `hermes-penfield` + `pre-compress` so they can be found and
  pruned. Importance is set low (0.4) so they don't dominate recall.
- If Penfield later ships real `/turns/sync` or `/context/save`
  endpoints, the hooks can be wired to them without changing the
  provider's public interface.
