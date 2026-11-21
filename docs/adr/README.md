# Architecture Decision Records

This directory records the decisions behind hermes-penfield that are not
obvious from the code. Each record gives the context that forced a
decision, the decision, and what follows from it. Records are not
rewritten: when a decision changes, a new record supersedes or amends the
old one and the old one says so in its status line.

| #  | Title | Status |
| -- | ----- | ------ |
| 1  | [Record architecture decisions](0001-record-architecture-decisions.md) | Accepted |
| 2  | [Stdlib-only HTTP, rate limit, backoff](0002-stdlib-only-http.md) | Accepted, amended by 13 and 26 |
| 3  | [Environment switching via PENFIELD_ENV](0003-environment-switching.md) | Accepted |
| 4  | [Missing lifecycle endpoints: no-ops and checkpoints](0004-missing-lifecycle-endpoints.md) | Accepted, amended by 16 |
| 5  | [Two auth paths: API key and OAuth device flow](0005-two-auth-paths.md) | Accepted, extended by 23, amended by 24 and 25 |
| 6  | [Awaken supplements SOUL.md](0006-awaken-supplements-soul.md) | Accepted |
| 7  | [Secret-shaped content rejected](0007-secret-shape-rejection.md) | Superseded by 14 |
| 8  | [No builtin memory mirror by default](0008-no-builtin-mirror-by-default.md) | Accepted, extended by 18 |
| 9  | [Real User-Agent required (Cloudflare)](0009-real-user-agent-required.md) | Accepted |
| 10 | [Directory discovery, not pip entry points](0010-directory-discovery-not-entry-points.md) | Accepted, extended by 19 |
| 11 | [Context tools: full implementation, no MCP shim](0011-context-tools.md) | Accepted |
| 12 | [on_pre_compress uses save_context for checkpoints](0012-pre-compress-save-context.md) | Superseded by 16 |
| 13 | [Never stall the agent](0013-never-stall-the-agent.md) | Accepted, amended by 25, extended by 27 |
| 14 | [The plugin does not filter memory content](0014-no-content-filter.md) | Accepted |
| 15 | [Subclass MemoryProvider and test inside Hermes](0015-subclass-and-test-inside-hermes.md) | Accepted |
| 16 | [The checkpoint saved before compression](0016-pre-compress-checkpoints.md) | Accepted |
| 17 | [Credentials and settings are read through Hermes](0017-settings-through-hermes.md) | Accepted, extended by 22 |
| 18 | [How the built-in memory mirror works](0018-builtin-memory-mirror.md) | Accepted |
| 19 | [No package metadata in the repository root](0019-no-package-metadata.md) | Accepted |
| 20 | [penfield_search ranks by meaning](0020-semantic-search-tool.md) | Accepted |
| 21 | [The Hermes version floor](0021-hermes-version-floor.md) | Accepted |
| 22 | [A setup hook for `hermes memory setup penfield`](0022-setup-hook.md) | Accepted, amended by 24 |
| 23 | [One login at a time](0023-one-login-at-a-time.md) | Accepted |
| 24 | [Browser sign-in first](0024-browser-sign-in-first.md) | Accepted |
| 25 | [A login ends only when the server says so](0025-a-login-ends-when-the-server-says-so.md) | Accepted |
| 26 | [One door for HTTP](0026-one-door-for-http.md) | Accepted |
| 27 | [Say why Penfield is off](0027-say-why-penfield-is-off.md) | Accepted |
