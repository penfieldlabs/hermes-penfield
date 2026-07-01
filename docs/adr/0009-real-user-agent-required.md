# ADR-0009: Send a real User-Agent; Cloudflare blocks bare urllib

- **Date:** 2026-06-29
- **Status:** Accepted

## Context

Requests from Python's bare `urllib.request` to the Penfield API return
HTTP 403 with Cloudflare error code 1010. Cloudflare's bot management
blocks the default Python-urllib User-Agent string.

## Decision

All HTTP requests set `User-Agent: hermes-penfield/{version}`. This
applies to the client, the auth helper, and all form-encoded OAuth calls.

## Consequences

- Requests pass Cloudflare on both prod and dev.
- The User-Agent lets Penfield's server-side telemetry distinguish
  hermes-penfield traffic.
