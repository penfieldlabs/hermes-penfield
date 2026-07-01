# ADR-0003: Environment switching via PENFIELD_ENV / *-dev host pairs

- **Date:** 2026-06-29
- **Status:** Accepted

## Context

Penfield runs separate prod and dev stacks. Development and testing
happen against dev; prod is the default for end users.

## Decision

A single `PENFIELD_ENV` switch (`prod` | `dev`) selects the whole host
family — API, auth, portal, and MCP together. Resolution order:

1. Explicit `PENFIELD_URL` env var (full base URL override).
2. `PENFIELD_ENV` env var.
3. Default: `prod`.

## Consequences

- One env var flips the entire stack; users can't accidentally mix dev
  auth with prod API.
- `PENFIELD_URL` remains as an escape hatch for staging/self-hosted.
