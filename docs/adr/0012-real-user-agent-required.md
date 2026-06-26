# ADR-0012: Send a real User-Agent; Cloudflare blocks bare urllib

- **Date:** 2026-06-26
- **Status:** Accepted

## Context

During v0.1.0 integration testing, requests from Python's bare
`urllib.request` to `api-dev.penfield.app` returned HTTP 403 with
Cloudflare error code 1010 (the same requests succeeded via `curl`).
Cloudflare's bot management blocks the default Python-urllib User-Agent
string. The fix is to send a real identifying User-Agent.

## Decision

`PenfieldClient` and the auth HTTP helper always set
`User-Agent: hermes-penfield/{version}` on every request (see
`constants.USER_AGENT`). The version is bumped in lockstep with the
package version. Any future "minimal headers" refactor must keep a
non-default User-Agent.

## Consequences

- Requests pass Cloudflare on both prod and dev.
- The User-Agent also lets Penfield's server-side telemetry distinguish
  hermes-penfield traffic, which is useful for the upstream team.
- A future change that drops the User-Agent will be caught by the
  integration tests (they'd 403).
