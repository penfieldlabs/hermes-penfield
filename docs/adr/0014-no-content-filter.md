# ADR-0014: The plugin does not filter memory content

- **Date:** 2026-11-19
- **Status:** Accepted. Supersedes [ADR-0007](0007-secret-shape-rejection.md).

## Context

ADR-0007 had `penfield_store` refuse content containing a short list of
secret-like prefixes. Matching prefixes inside ordinary words refuses
ordinary sentences, and a list short enough to avoid that catches little.

Hermes Agent redacts credentials itself: recall queries, tool arguments
and conversation text pass through its scrubber before any memory
provider receives them.

## Decision

The plugin stores what it is given. Redaction is Hermes's job, done once,
for every provider, with patterns Hermes maintains.

## Consequences

- No text is refused for looking like a secret.
- Protection against storing credentials is exactly as good as Hermes's
  redaction. SECURITY.md says so.
