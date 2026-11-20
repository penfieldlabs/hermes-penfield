# ADR-0015: Subclass MemoryProvider and test inside Hermes

- **Date:** 2026-11-19
- **Status:** Accepted

## Context

Hermes defines memory providers by the abstract class
`agent.memory_provider.MemoryProvider`. Its in-process loader accepts any
object with the right methods, but its plugin host
(`plugins.isolation: host`) accepts only instances of that class, and
Hermes adds hooks to the class between releases.

Hermes moves fast. A provider tested only against a copy of the interface
keeps passing its tests after it has stopped working in Hermes.

## Decision

- `PenfieldMemoryProvider` subclasses `MemoryProvider`. Hooks the plugin
  does not implement fall through to Hermes's defaults, including hooks
  added after this was written. Where Hermes is not installed (unit
  tests, linters) a plain base class stands in.
- The plugin imports Hermes only through its public plugin API.
- `tests/hermes/` runs the plugin inside a real Hermes Agent: discovery,
  loading, a whole session through `MemoryManager`, behaviour during an
  API outage, Hermes's validator and its installer. Each scenario runs in
  both isolation modes, in a fresh Hermes process, against a local fake
  of the Penfield API.
- CI runs those tests on the oldest supported Hermes release, on two
  releases after it and on Hermes `main`, on every change and weekly.
  [ADR-0021](0021-hermes-version-floor.md) says which release is the
  oldest and why.

## Consequences

- A Hermes change that breaks the plugin fails CI within a week, without
  a commit here.
- The integration job installs Hermes from source and needs Python 3.14.
- Changing the oldest supported release is a deliberate change to the CI
  matrix and the documentation together.
