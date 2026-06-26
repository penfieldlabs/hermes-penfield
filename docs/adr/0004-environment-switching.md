# ADR-0004: Environment switching via PENFIELD_ENV / *-dev host pairs

- **Date:** 2026-06-26
- **Status:** Accepted

## Context

Penfield runs separate prod and dev stacks. The host convention (confirmed
during v0.1.0 development) is: prod uses bare hosts
(`api.penfield.app`, `auth.penfield.app`, `portal.penfield.app`,
`mcp.penfield.app`); dev uses `*-dev` hosts
(`api-dev.penfield.app`, etc.). Development and testing happen against
dev; prod is the default for end users.

## Decision

A single `PENFIELD_ENV` switch (`prod` | `dev`) selects the whole host
family — API, auth, portal, and MCP together — via the
`PROD_HOSTS` / `DEV_HOSTS` tables in `constants.py`. Resolution order
in `PenfieldConfig.load`:

1. Explicit `PENFIELD_URL` env var (full base URL, e.g. for staging) —
   overrides host derivation.
2. `PENFIELD_ENV` env var.
3. Saved `penfield_env` config value.
4. Default: `prod`.

`auth_url`, `portal_url`, and `mcp_url` are derived from the same
environment so OAuth and CLI commands stay coherent with the API.

## Consequences

- One env var flips the entire stack; users can't accidentally mix
  dev auth with prod API.
- `PENFIELD_URL` remains as an escape hatch for staging/self-hosted.
- The integration tests set `PENFIELD_ENV=dev`; CI must do the same.
