# ADR-0001: Record architecture decisions

- **Date:** 2026-06-29
- **Status:** Accepted

## Context

hermes-penfield has non-obvious decisions spanning API contract reality,
environment switching, missing endpoints, and authentication strategy.
Without an in-tree record, a reader seeing only the code would reasonably
ask "why this way and not the obvious other way?"

## Decision

Use Architecture Decision Records (ADRs) in `docs/adr/`, one short
Markdown file per decision, numbered sequentially. Each ADR captures
context (the forcing problem), decision (what we chose), and
consequences (what follows). ADRs are immutable — superseding decisions
get new ADRs that reference the originals.

## Consequences

- New non-trivial decisions require an ADR before merge.
- ADR review is part of code review.
- The index in `docs/adr/README.md` is the entry point for "why is it
  this way?"
