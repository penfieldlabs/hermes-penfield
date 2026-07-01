# ADR-0010: Memory providers are directory-installed, not pip entry points

- **Date:** 2026-06-29
- **Status:** Accepted

## Context

Hermes discovers memory providers by scanning two directories —
bundled `plugins/memory/<name>/` and user `$HERMES_HOME/plugins/<name>/`.
The general plugin entry-point path has no `register_memory_provider`
on its context.

## Decision

hermes-penfield ships as a self-contained directory at the repo root.
`hermes plugins install penfieldlabs/hermes-penfield --enable` clones the
repo and drops it into `~/.hermes/plugins/penfield/`. No pip install, no
entry points, no external dependencies.

The plugin is loaded via `spec_from_file_location` with
`submodule_search_locations`, so relative imports resolve correctly.

## Consequences

- One-command install that works on any Hermes setup.
- The plugin is fully self-contained — all source files at repo root.
- No dependency conflicts with Hermes or other plugins.
