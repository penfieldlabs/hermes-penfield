# ADR-0017: Credentials and settings are read through Hermes

- **Date:** 2026-11-19
- **Status:** Accepted. Extended by [ADR-0022](0022-setup-hook.md).

## Context

A Hermes gateway can serve several profiles from one process. Each
profile has its own `.env` and its own home directory, and Hermes scopes
secrets to the profile handling the current request. A plugin that reads
`os.environ` or assumes `~/.hermes` can pick up another profile's API key
or token file.

## Decision

- `PENFIELD_API_KEY`, `PENFIELD_ENV` and `PENFIELD_URL` are read with
  Hermes's `agent.secret_scope.get_secret`. Where Hermes is not installed
  (unit tests) the plugin reads the process environment instead.
- The profile directory comes from the `hermes_home` Hermes passes to
  `initialize()`. Before initialization (`is_available`, status, CLI) it
  comes from `hermes_constants.get_hermes_home()`.
- Everything is bound to the provider instance at `initialize()`. Nothing
  profile-specific is kept at module level, and `register()` hands Hermes
  a new instance every time.
- `hermes memory setup` asks for the API key and nothing else. Hermes
  writes it to the profile's `.env`.

## Consequences

- Two profiles in one gateway use two Penfield accounts without seeing
  each other's credentials.
- The plugin has no credential storage of its own for API keys.
