# ADR-0027: Say why Penfield is off

- **Date:** 2026-11-20
- **Status:** Accepted. Extends [ADR-0013](0013-never-stall-the-agent.md).

## Context

Hermes keeps a memory provider whose `initialize` raised: it logs a
warning and leaves the provider, and its tools, in the session. With a
setting the plugin could not use (an unknown `PENFIELD_ENV`, for one) the
agent was told that Penfield memory was available, and every tool call
answered "Penfield is not initialized" without a reason. A settings file
that was valid JSON but not an object, or not text at all, had the same
effect, and so did a damaged token cache.

## Decision

- A setting the plugin cannot use does not raise out of `initialize`.
  The provider stays in place and says why it is off at every point of
  contact: an error in the log, the text it adds to the system prompt,
  the answer to every tool call, and the status commands. The command
  line prints the reason and exits with status 2.
- A `penfield/config.json` that cannot be read as a JSON object is
  ignored, with a warning, the way a single setting of the wrong kind
  already was. Defaults apply.
- A `penfield/tokens.json` that cannot be read as a token record is the
  same as no cache.
- Tool arguments are checked against the kinds the tool declares before
  anything is sent. A value that is plainly meant is taken (`"3"` for a
  number, `"a, b"` for a list of tags); anything else is refused by name.
- Recalled text added to a turn ends with a whole memory. The size limit
  drops memories; it does not cut one in the middle, and the recall
  indicator counts what was shown.

## Consequences

- The agent can tell the user what is wrong, and the user finds the same
  reason in `hermes memory status` and in the log.
- A damaged file costs its contents, not the session.
