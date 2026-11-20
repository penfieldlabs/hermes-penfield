# ADR-0016: The checkpoint saved before compression

- **Date:** 2026-11-19
- **Status:** Accepted. Supersedes [ADR-0012](0012-pre-compress-save-context.md) and amends [ADR-0004](0004-missing-lifecycle-endpoints.md).

## Context

When a conversation outgrows the context window, Hermes compresses the
older part into a summary and calls `on_pre_compress` first. Whatever the
provider returns is given to the summariser.

A checkpoint saved at that moment lets the agent get back to the memories
the discussion was about. It also adds an entry to the user's list of
saved contexts each time, and it runs while the user waits.

## Decision

- The checkpoint is saved by the same code as `penfield_save_context`,
  named `pre-compress-<session id>-<UTC timestamp>` and tagged
  `auto-checkpoint` so these can be told apart from checkpoints the agent
  saved on purpose.
- Its description is built from the user's recent messages: the last
  five, 160 characters each, with pasted JSON-like blocks removed. No
  model is called. The description is also the search that selects the
  linked memories, so it is made of what the user asked about.
- The hook returns one sentence naming the checkpoint, so the compression
  summary can carry a pointer to it.
- Only the primary agent saves one. Subagents, cron runs and flush passes
  do not.
- The save runs in the background and the hook waits at most eight
  seconds for it ([ADR-0013](0013-never-stall-the-agent.md)). A failure
  is logged and compression goes ahead. A pointer is returned only for a
  checkpoint that has been saved.
- The provider stays on Hermes's best-effort pre-compress contract. The
  stricter contract lets a provider failure stop a compression, which is
  the wrong trade for a remote service.
- The feature is off unless the user sets `pre_compress_save: true`. A
  checkpoint is an entry in the user's saved contexts, which every agent
  on the account sees, and a long-running agent compresses many times a
  day.

## Consequences

- After compression the agent can call `penfield_restore_context` with
  the name it was given.
- Long sessions add several `auto-checkpoint` entries to the saved
  contexts.
- The description is plain and literal. It does not summarise.
