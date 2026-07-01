# ADR-0002: Stdlib-only HTTP, client-side rate limit, jittered backoff

- **Date:** 2026-06-29
- **Status:** Accepted

## Context

The plugin must work on any Python 3.10+ install without dependency
conflicts. The Penfield API has an unofficial ~250 RPM limit and returns
429/5xx under load.

## Decision

All network I/O uses `urllib.request` / `urllib.parse` only — no
`requests`, `httpx`, or `aiohttp`. `PenfieldClient` implements:

- A sliding 60-second window rate limiter at 250 RPM.
- Exponential backoff with jitter on 429 and 5xx (base 1s, factor 2,
  max 60s, ±10% jitter), max 5 retries.
- No retry on 4xx client errors.
- A single 401 triggers one refresh then a retry.

## Consequences

- Zero runtime dependencies.
- Rate limiting is client-side and conservative; multi-process
  deployments could exceed the limit in aggregate.
