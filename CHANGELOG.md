# Changelog

All notable changes to hermes-penfield are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] — 2026-07-01

### Added
- save_context: full MCP parity with reference parsing, hybrid search
  memory snapshot, and name uniqueness enforcement
- on_pre_compress now uses save_context for enriched checkpoints (ADR-0012)
- disconnect tool (DELETE /relationships/between)
- delete_artifact tool (DELETE /artifacts)
- MCP drift test pinning tool surface to canonical source
- 12 ADRs documenting architectural decisions

### Fixed
- tags array translated to comma string for REST API (recall)
- on_pre_compress filters tool messages from description
- restore_context uses exact name match, not fuzzy search
- restore_context returns all saved memories (no limit cap)
- list_contexts shows clean checkpoint names, not raw JSON
- save_context deduplicates memory_ids and referenced_memories
- save_context guards empty description
- HERMES_HOME resolution defaults to ~/.hermes
- input_schema → parameters (Hermes ABC expects "parameters")
- Cloudflare User-Agent fix for OAuth form-encoded calls
- OAuth refresh routing (API-key vs OAuth token endpoints)
- Stale refresh token fallback to API-key re-exchange

### Changed
- Tool surface mirrors MCP exactly (17 tools, full parity)
- Self-contained plugin (no pip install, no external deps)
- Parameter names match MCP (from_memory, start_memory, etc.)

## [0.1.0] — 2026-06-29

Initial release. Self-contained Hermes Agent memory provider for Penfield.

### Added
- 16 tools mirroring the Penfield MCP surface
- Self-contained plugin directory (no pip install, no external deps)
- OAuth 2.1 device-code flow (RFC 8628) with dynamic client registration
- API-key exchange with RFC 9700 refresh-token rotation fallback
- Stdlib-only HTTP client with sliding-window rate limiting and backoff
- Environment switching via `PENFIELD_ENV` (prod/dev)
- Auto-awakening: persona and persistent context load on every session

[Unreleased]: https://github.com/penfieldlabs/hermes-penfield/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/penfieldlabs/hermes-penfield/releases/tag/v0.2.0
[0.1.0]: https://github.com/penfieldlabs/hermes-penfield/releases/tag/v0.1.0
