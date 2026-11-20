# ADR-0019: No package metadata in the repository root

- **Date:** 2026-11-19
- **Status:** Accepted. Extends [ADR-0010](0010-directory-discovery-not-entry-points.md).

## Context

ADR-0010 makes the repository root the plugin directory. Hermes's plugin
installer inspects that directory. If it finds a `pyproject.toml` it
treats the plugin as declaring Python dependencies, asks the user to
approve an install step, and refuses to enable the plugin in an
unattended install. Hermes 0.23 does this only for a file with a
`[project]` table, a build system or optional dependencies. Hermes before
0.23 does it for any `pyproject.toml`, and `hermes memory setup` then
fails while preparing an environment for a project that has no name.

The plugin has no runtime dependencies. Its `pyproject.toml` existed for
development tools.

## Decision

- The repository has no `pyproject.toml`, `setup.py`, `setup.cfg` or
  `requirements.txt`. A test fails if one appears.
- Each development tool is configured in its own file: `ruff.toml`,
  `mypy.ini`, `pytest.ini` and `.coveragerc`.
- Development tools are listed in `requirements-dev.txt`, a name Hermes
  does not read.
- The version lives in `plugin.yaml`, where Hermes reads it, and in
  `constants.py` for the User-Agent. A test keeps the two equal.
- `plugin.yaml` declares `requires_hermes`.
  [ADR-0021](0021-hermes-version-floor.md) explains its value.

## Consequences

- `hermes plugins install ... --enable` finishes without questions on
  every supported Hermes release.
- The plugin cannot be installed with pip, which it never needed.
- mypy cannot check the root directory as a package; `make typecheck`
  copies the modules into one first.
