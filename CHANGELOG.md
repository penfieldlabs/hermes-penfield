# Changelog

All notable changes to hermes-penfield are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] — 2026-06-29

Initial release. Self-contained Hermes Agent memory provider for Penfield.

### Added
- 16 tools mirroring the Penfield MCP surface exactly:
  awaken, store, recall, search, fetch, update_memory, connect,
  disconnect, explore, reflect, save/retrieve/list/delete_artifact,
  list_contexts, restore_context
- Self-contained plugin directory (no pip install, no external deps)
- OAuth 2.1 device-code flow (RFC 8628) with dynamic client registration
- API-key exchange with RFC 9700 refresh-token rotation fallback
- Stdlib-only HTTP client with sliding-window rate limiting and backoff
- Environment switching via `PENFIELD_ENV` (prod/dev)
- Auto-awakening: persona and persistent context load on every session
- 11 ADRs documenting all architectural decisions

### Held
- `save_context` — requires MCP-server-side enrichment (reference
  parsing, relationship creation). See ADR-0011 and issue #3.

[Unreleased]: https://github.com/penfieldlabs/hermes-penfield/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/penfieldlabs/hermes-penfield/releases/tag/v0.1.0
