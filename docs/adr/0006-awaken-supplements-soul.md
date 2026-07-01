# ADR-0006: Awaken supplements SOUL.md; does not replace it

- **Date:** 2026-06-29
- **Status:** Accepted

## Context

Hermes ships a built-in `SOUL.md` persona system. Penfield's awakening
endpoint returns persistent context (preferences, project history, custom
instructions). Both shape agent behavior.

## Decision

`system_prompt_block()` automatically fetches the awakening briefing on
every session and prepends it to the tool instructions. This supplements
SOUL.md — it does not replace it. SOUL.md remains the base persona;
awaken enriches it with learned, persistent context.

## Consequences

- Users keep full control of base personality via SOUL.md.
- Penfield carries the learned layer (what the agent has come to know
  about this user/project across sessions).
