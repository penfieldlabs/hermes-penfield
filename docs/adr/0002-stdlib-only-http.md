# ADR-0002: Stdlib-only HTTP, client-side rate limit, jittered backoff

- **Date:** 2026-06-26
- **Status:** Accepted

## Context

The v1.0 spec mandates zero external runtime dependencies so the plugin
installs cleanly into any Python 3.10+ environment without dependency
conflicts. The Penfield API has an unofficial ~250 RPM limit and
returns 429/5xx under load. A naive client would either get rate-limited
or hammer the server on transient failures.

## Decision

All network I/O uses `urllib.request` / `urllib.parse` only — no
`requests`, `httpx`, or `aiohttp`. `PenfieldClient` implements:

- A **sliding 60-second window** rate limiter at 250 RPM that blocks
  (sleeps) when the window is full.
- **Exponential backoff with jitter** on 429 and 5xx (base 1s, factor
  2, max 60s, ±10% jitter), max 5 retries.
- **No retry on 4xx** client errors (they won't fix themselves).
- A single 401 triggers one refresh then a retry; further 401s raise.

A `sleep` callable is injectable so unit tests don't actually sleep.

## Consequences

- The package has no runtime dependencies, as required.
- Rate limiting is client-side and conservative; a multi-process
  deployment could still exceed the limit in aggregate — that's a known
  gap acceptable for a single-agent provider.
- The sliding-window limiter uses `time.monotonic`, so it survives clock
  skew but not process restarts (the window resets on restart).
- Anyone adding a third-party HTTP dep must update this ADR or revert
  the change.
