# ADR-0018: How the built-in memory mirror works

- **Date:** 2026-11-19
- **Status:** Accepted. Extends [ADR-0008](0008-no-builtin-mirror-by-default.md).

## Context

ADR-0008 keeps Hermes's built-in memory (`MEMORY.md`, `USER.md`) out of
Penfield unless the user sets `mirror_builtin_writes`. A user who does
set it wants other agents on the account to see what Hermes noted.

Hermes reports each built-in write as an action (`add`, `replace`,
`remove`) with the entry's text and, for edits and removals, its previous
text. It makes the call from inside the memory tool, while the agent
waits.

## Decision

- An added entry is stored as a memory tagged `hermes-builtin` and
  `hermes-memory` or `hermes-user`. Penfield chooses the memory type.
- An edited entry updates that memory in place. A removed entry deletes
  it. Deleting needs an API key with the delete permission; without it
  the memory stays and that is logged.
- The memory is found by the `hermes-builtin` tag and its exact previous
  text. The mirror therefore changes only memories it created.
- Writes are queued and applied in order by one background thread, so the
  memory tool returns at once and an edit never overtakes the write it
  follows. The end of a session waits up to three seconds for the queue.
- Only the primary agent's writes are mirrored.

## Consequences

- The copy in Penfield follows the built-in memory instead of piling up
  stale versions.
- A removal without the delete permission leaves a memory behind.
- An edit made in a later session finds the memory by search, so it can
  miss if the entry has been changed in Penfield in the meantime. A new
  memory is stored then.
