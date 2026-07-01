# Architecture Decision Records

This directory records the non-obvious architectural decisions behind
hermes-penfield. Each ADR captures the context that forced a decision,
the decision itself, and its consequences.

## Index

| #    | Title                                                                    | Status   |
| ---- | ------------------------------------------------------------------------ | -------- |
| 1    | [Record architecture decisions](0001-record-architecture-decisions.md)   | Accepted |
| 2    | [Stdlib-only HTTP, rate limit, backoff](0002-stdlib-only-http.md)        | Accepted |
| 3    | [Environment switching via PENFIELD_ENV](0003-environment-switching.md)  | Accepted |
| 4    | [Missing lifecycle endpoints — no-ops + checkpoints](0004-missing-lifecycle-endpoints.md) | Accepted |
| 5    | [Two auth paths — API key + OAuth device flow](0005-two-auth-paths.md)   | Accepted |
| 6    | [Awaken supplements SOUL.md](0006-awaken-supplements-soul.md)            | Accepted |
| 7    | [Secret-shaped content rejected](0007-secret-shape-rejection.md)         | Accepted |
| 8    | [No builtin memory mirror by default](0008-no-builtin-mirror-by-default.md) | Accepted |
| 9    | [Real User-Agent required (Cloudflare)](0009-real-user-agent-required.md) | Accepted |
| 10   | [Directory discovery, not pip entry points](0010-directory-discovery-not-entry-points.md) | Accepted |
| 11   | [Context tools — full implementation, no MCP shim](0011-context-tools.md) | Accepted |
| 12   | [on_pre_compress uses save_context for checkpoints](0012-pre-compress-save-context.md) | Accepted |
