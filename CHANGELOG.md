# Changelog

All notable changes to hermes-penfield are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] — 2026-06-26

Initial alpha. The client, auth, and tool layers are fully verified
against the live `api-dev.penfield.app` contract. The Hermes provider
adapter is implemented to the documented ABC but is the one seam still
needing validation against real Hermes source
([ADR-0007](docs/adr/0007-provider-duck-types-hermes-abc.md)).

### Added
- `PenfieldClient`: stdlib-only HTTP with sliding-window rate limiting
  (250 RPM) and jittered exponential backoff on 429/5xx
  ([ADR-0002](docs/adr/0002-stdlib-only-http.md)).
- `PenfieldAuth`: API-key exchange (verified default) + OAuth 2.1 device
  code flow (RFC 8628) with dynamic client registration and RFC 9700
  refresh-token rotation ([ADR-0006](docs/adr/0006-two-auth-paths.md)).
- `PenfieldConfig`: `PENFIELD_ENV`-based prod/dev host switching with
  `PENFIELD_URL` override ([ADR-0004](docs/adr/0004-environment-switching.md)).
- 13 `penfield_*` tools mapped to the real Penfield v2 API
  ([ADR-0003](docs/adr/0003-spec-vs-real-api-divergence.md)):
  - P0: store, recall, search, connect, fetch, update, delete
  - P1: explore, reflect, list/save/retrieve artifacts
  - P2: awaken
- `PenfieldMemoryProvider`: Hermes adapter with lifecycle hooks.
  `sync_turn` is a documented no-op; `on_pre_compress` synthesizes a
  `checkpoint` memory via the real `POST /memories` endpoint
  ([ADR-0005](docs/adr/0005-missing-lifecycle-endpoints.md)).
- CLI: `hermes penfield status|login|logout|search|stats|version`.
- Integration test suite against `api-dev.penfield.app`
  ([ADR-0011](docs/adr/0011-integration-tests-hit-live-dev-api.md)).
- 12 ADRs documenting the architectural decisions.

### Security
- Secret-shaped content rejected from store paths
  ([ADR-0009](docs/adr/0009-secret-shape-rejection.md)).
- Real `User-Agent` required — Cloudflare blocks bare urllib
  ([ADR-0012](docs/adr/0012-real-user-agent-required.md)).
- Builtin MEMORY.md/USER.md writes not mirrored by default
  ([ADR-0010](docs/adr/0010-no-builtin-mirror-by-default.md)).

[Unreleased]: https://github.com/penfieldlabs/hermes-penfield/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/penfieldlabs/hermes-penfield/releases/tag/v0.1.0
