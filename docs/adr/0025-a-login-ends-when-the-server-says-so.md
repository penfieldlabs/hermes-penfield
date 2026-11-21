# ADR-0025: A login ends only when the server says so

- **Date:** 2026-11-20
- **Status:** Accepted. Amends [ADR-0005](0005-two-auth-paths.md) and
  [ADR-0013](0013-never-stall-the-agent.md).

## Context

Tokens are renewed with a refresh token. Version 0.3.0 treated every
refusal of a browser sign-in's renewal as "the sign-in is dead": a 503
or a 429 from the auth server had the same effect as a revoked token.
The tokens were then dropped from memory, and a process without an API
key answered every later request with "no credentials available" until
it was restarted, while the refresh token on disk was still good. A
gateway that runs for weeks meets a failing auth server sooner or later.

Two more cases were not handled. The API may answer a key exchange
without a refresh token, and the auth server leaves it out when
`offline_access` was not granted; the text `None` was then stored and
sent as a refresh token. And one failed poll ended `hermes penfield
login` while the user was still in the browser.

## Decision

- A login is over when the token endpoint answers `invalid_grant`,
  `invalid_client` or `unauthorized_client` (RFC 6749, section 5.2), or
  when the API refuses an API key.
- Everything else says nothing about the login: no connection, a
  timeout, a 5xx, a 429, a redirect, an answer that is not the expected
  JSON, and any other refusal. These are reported as the auth server not
  answering. The tokens are kept, the request is retried within its
  attempts (ADR-0013), and the next request tries again.
- A login that is over is remembered as over, not forgotten. The same
  dead refresh token is not sent again, requests fail at once with a
  message that says to sign in again, and a new login written to the
  cache by another process or by `hermes penfield login` is picked up on
  the next request.
- The token file is never deleted because a renewal was refused. Another
  process may have rotated the token a moment before.
- Before renewing, a process looks at the cache. A newer login there
  replaces the one it holds, also when that login's access token has run
  out in the meantime: its refresh token is then the one to renew with.
  This keeps a process from sending a refresh token another process has
  already used, and lets it pick up a new sign-in however long it sat
  idle. A cached login older than the one held is left alone: that is
  what remains when the cache could not be written. Once the login held
  has ended, whatever else the cache holds replaces it.
- A token set without a refresh token is normal. With an API key it is
  renewed by a key exchange. A browser sign-in without one ends when its
  access token does.
- `hermes penfield login` keeps polling through an auth server that does
  not answer, until the code runs out. After three polls in a row
  without a usable answer it says so, and if the code runs out that way
  the error names the failure, since the user may well have approved.
- The sign-in step has its own time limit, shorter than a request's,
  because a tool call may go through it up to three times.

## Consequences

- An auth server outage costs the requests made during it and nothing
  after it.
- A revoked sign-in costs one request to find out, per process.
- A hung auth server holds a tool call for at most three sign-in time
  limits plus the pauses between attempts.
