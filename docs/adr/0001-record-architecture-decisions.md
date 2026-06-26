# ADR-0001: Record architecture decisions

- **Date:** 2026-06-26
- **Status:** Accepted

## Context

hermes-penfield has non-obvious decisions spanning API contract reality,
environment switching, missing endpoints, and authentication strategy.
Without an in-tree record, a future contributor (or future-us) sees the
code without the reasoning and either re-litigates settled questions or
quietly violates constraints whose purpose isn't visible.

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
- Existing decisions are backfilled as ADRs when they come up, not all
  at once.
