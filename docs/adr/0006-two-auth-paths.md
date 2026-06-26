# ADR-0006: Two auth paths — API-key exchange default, OAuth device flow for interactive

- **Date:** 2026-06-26
- **Status:** Accepted

## Context

The spec named OAuth 2.1 device code as the primary auth method and an
API key as a CI/headless fallback. In practice, the API-key exchange
(`POST /auth/token` with the key as a bearer header → JWT + refresh
token) is the simplest fully-verifiable path: it needs no browser, no
client registration, and round-trips in one call. The OAuth device flow
needs a human to visit a URL, so it can't be exercised in automated
tests against the live dev IdP.

## Decision

Both are implemented; both produce the same `TokenSet` shape.

- **API-key exchange** is the path used by tests, CI, and headless
  setups. It's fully verified against `api-dev`.
- **OAuth 2.1 device code** (RFC 8628) is implemented to the documented
  contract with dynamic client registration, endpoint discovery, and
  RFC-compliant polling (`authorization_pending`, `slow_down`,
  `expired_token`). It's unit-tested with mocked HTTP, including the
  polling state machine. It's the path `hermes penfield login` uses.

Tokens are cached to `{hermes_home}/penfield/tokens.json` with mode
0o600. Refresh uses the documented RFC 9700 rotation (each refresh
invalidates the old refresh token).

## Consequences

- The verified path is the one that runs in automation; the interactive
  path is contract-faithful but only manually exercisable.
- A future regression in the device flow wouldn't be caught by CI; the
  unit tests narrow this risk but can't fully replace a live IdP run.
- Refresh-token rotation means a crashed refresh can leave the cached
  refresh token invalid; the client falls back to API-key exchange on
  the next request, which is why having an API key configured is the
  robust default.
