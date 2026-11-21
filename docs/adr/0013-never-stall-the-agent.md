# ADR-0013: Never stall the agent

- **Date:** 2026-11-19
- **Status:** Accepted. Amends [ADR-0002](0002-stdlib-only-http.md). Amended by [ADR-0025](0025-a-login-ends-when-the-server-says-so.md), extended by [ADR-0027](0027-say-why-penfield-is-off.md).

## Context

Hermes calls a memory provider synchronously at three points the user
waits through: when it builds the system prompt at session start, before
every turn to recall context, and before it compresses a long
conversation. It puts a time limit on the second and none on the other
two. A provider that waits on a slow network there makes the whole agent
slow.

Hermes builds the system prompt once and keeps it for the whole session,
resumes included. A briefing that is not there when the prompt is built
is missing for good.

Tool calls are different. The agent asked for them and waits for the
result, but a retry of a request that creates something can create it
twice.

## Decision

**Hooks the user waits through.** Each does its network work on a
background thread, started through Hermes's `spawn_context_thread`, and
stops waiting at a deadline:

| Hook | Deadline | Attempts |
| ---- | -------- | -------- |
| Briefing at session start | 6 seconds | Two, 5 second timeout each. Fetched once per session, starting when the provider is initialized. |
| Auto-recall before a turn | 2.5 seconds | One, 2.5 second timeout. |
| Checkpoint before a compression | 8 seconds | The normal tool-call policy. The save carries on in the background if it is late. |

The briefing gets the longest wait because it is needed once and cannot
be added later. Recall gets the shortest because it runs every turn.

**Tool calls:**

- 15 second timeout, at most two retries with exponential backoff.
- A 429 is always retried: the request was refused before processing.
- A 5xx or a transport error is retried only for requests that are safe
  to repeat. Creating a memory or a relationship is not, so those are
  attempted once.
- A failure is returned to the model as `{"error": ...}` JSON.

**Signing in.** A request may first have to exchange the API key or
refresh a token. If the auth server does not answer, that step is tried
again within the same number of attempts as the request it belongs to,
for every request including the ones that are sent only once: signing in
changes nothing at Penfield. A key or a token the server refuses is not
tried again.

No hook raises into Hermes.

The plugin supports Python 3.11 and newer. Hermes Agent itself runs on
Python 3.14.

## Consequences

- With Penfield unreachable, a session starts within six seconds and
  each turn proceeds within two and a half, without memory.
- A session that starts without the briefing writes one warning to
  Hermes's log with the reason, so an agent missing its persona and
  instructions can be explained afterwards.
- Recall is done on the current message, which costs about a second per
  turn against the live API. A recall slower than the deadline is lost
  for that turn.
- A checkpoint slower than its deadline is still saved, but Hermes gets
  no pointer to it for the compression summary.
- A create request that times out is reported as failed although the
  server may have completed it. The agent can search before storing again.
