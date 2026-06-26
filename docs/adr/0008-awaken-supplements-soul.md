# ADR-0008: Awaken supplements SOUL.md; does not replace it

- **Date:** 2026-06-26
- **Status:** Accepted

## Context

Hermes ships a built-in `SOUL.md` persona system (local, file-based,
user-editable) that defines base personality and behavioral rules.
Penfield's awakening endpoint returns persistent context: user
preferences, project history, custom instructions. Both want to shape
agent behavior; conflating them creates confusion about which system
controls what.

## Decision

`penfield_awaken` **supplements** SOUL.md, never replaces it. SOUL.md
remains the base persona; awaken enriches it with learned, persistent
context. In `system_prompt_block()`, awaken content (if injected) appears
in a clearly delineated `[Penfield Context]` section after the host's
SOUL.md content. The README documents the split.

## Consequences

- Users keep full control of base personality via SOUL.md.
- Penfield carries the *learned* layer (what the agent has come to know
  about this user/project across sessions).
- Custom instructions from awaken can override base-persona settings;
  that's the documented Penfield behavior and we surface it as such.
