# ADR-0005: Two auth paths — API-key exchange and OAuth device flow

- **Date:** 2026-06-29
- **Status:** Accepted

## Context

Users need both headless/CI authentication (API key) and interactive
authentication (browser-based OAuth).

## Decision

Both are implemented, both produce the same `TokenSet` shape:

- **API-key exchange** (`POST /auth/token`): key as bearer header → JWT
  + refresh token. Used in CI, headless, and `hermes penfield login`.
- **OAuth 2.1 device code** (RFC 8628): dynamic client registration,
  endpoint discovery, polling. Used for interactive `hermes penfield login`.

Tokens cache to `{hermes_home}/penfield/tokens.json` (mode 0600).
Refresh uses RFC 9700 rotation (each refresh invalidates the old refresh
token). When a cached refresh token is stale, the provider falls back to
a fresh API-key exchange rather than stranding the user.

OAuth-derived tokens refresh via the OAuth token endpoint (not
`/auth/refresh`); API-key-derived tokens refresh via `/auth/refresh`.
Routing is decided by the token's `source` field.

## Consequences

- The verified path is API-key exchange (tested live end-to-end).
- OAuth device flow is verified live against the dev IdP.
- RFC 9700 strict rotation means refresh tokens are single-use.
