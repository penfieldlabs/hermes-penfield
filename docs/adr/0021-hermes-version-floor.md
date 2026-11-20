# ADR-0021: The Hermes version floor

- **Date:** 2026-11-20
- **Status:** Accepted

## Context

`plugin.yaml` can declare `requires_hermes`, and Hermes refuses to install
or update a plugin whose floor is above the Hermes that is running.

A Hermes installed from a git checkout works out its own version by
running git, with a three second limit on each command. On a slow or busy
machine the command that lists release tags runs out of time. Hermes then
falls back to the version of its last date-tagged release, which is
0.21.5, or to "unknown", which passes every floor. A floor above 0.21.5
therefore makes `hermes plugins install` fail on such a machine with
"requires hermes >=X, running 0.21.5", on a Hermes that is in fact new
enough. Only installing and updating consult the floor; a session and the
plugin's own commands do not.

Separately, the plugin depends on two things Hermes 0.21.6 introduced.
Hermes removes credentials it recognises from text before a memory
provider sees it, which is why the plugin has no content filter
(ADR-0014). And plugins can run in the plugin host
(`plugins.isolation: host`). Hermes 0.21.5 has neither.

## Decision

- Hermes Agent 0.21.6 is the oldest supported release. The README says
  so, and CI runs the integration tests on it, on 0.22.7, on 0.23.0 and
  on Hermes `main`.
- `requires_hermes` is `">=0.21.5"`, one patch below the oldest supported
  release, so that a Hermes which misread its own version is never
  refused. A comment in `plugin.yaml` says why, and a unit test fails if
  the floor is raised above 0.21.5.
- The plugin makes no version check of its own at run time. Hermes's
  version is exactly what cannot be trusted there.

## Consequences

- `hermes plugins install` and `hermes plugins update` cannot be refused
  by a version misread.
- Hermes 0.21.5 itself is not refused either. Its installer does not
  apply the floor in any case. On it the plugin runs without Hermes's
  credential redaction, which is why the documentation names 0.21.6.
- Releases older than 0.21.5 are still refused.
- Dropping support for old releases later means changing the CI matrix
  and the README. The manifest floor stays where it is unless Hermes
  changes how it reads its version.
