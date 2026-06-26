# ADR-0011: Integration tests hit the live dev API; skip cleanly without creds

- **Date:** 2026-06-26
- **Status:** Accepted

## Context

The plugin is a thin client over a remote API. Mock-only tests can verify
plumbing but cannot prove the endpoint mapping, envelope shapes, or auth
flow are correct against reality. The v1.0 spec had wrong endpoints
(ADR-0003); only live calls would have caught that. But CI and casual
local runs often lack dev credentials, and tests that hard-fail without
creds get disabled and rot.

## Consequences

- Integration tests are the living proof the contract mapping in
  `constants.ENDPOINTS` is correct. They are run before any release.
- A contract drift on the Penfield side surfaces as an integration
  failure, not a silent field mismatch.
- The dev API key used in development is held in `.env` (gitignored,
  mode 0600) and never committed.
