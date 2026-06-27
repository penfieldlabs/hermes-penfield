# Architecture Decision Records

This directory records the non-obvious architectural decisions behind
hermes-penfield. Each ADR captures the context that forced a decision,
the decision itself, and its consequences — so a reader (or future-us)
can understand *why* the code is the way it is without re-deriving it.

ADR-0001 is the format reference. ADRs are immutable; a decision that
supersedes an earlier one gets a new, numbered ADR that links back.

## Index

| #   | Title                                                        | Status   |
| --- | ------------------------------------------------------------ | -------- |
| 1   | [Record architecture decisions](0001-record-architecture-decisions.md) | Accepted |
| 2   | [Stdlib-only HTTP, client-side rate limit, jittered backoff](0002-stdlib-only-http.md) | Accepted |
| 3   | [Spec vs. real API divergence — build to the real contract](0003-spec-vs-real-api-divergence.md) | Accepted |
| 4   | [Environment switching via PENFIELD_ENV / \*-dev host pairs](0004-environment-switching.md) | Accepted |
| 5   | [Missing lifecycle endpoints — defensive no-ops + synthesized checkpoints](0005-missing-lifecycle-endpoints.md) | Accepted |
| 6   | [Two auth paths: API-key exchange default, OAuth device flow for interactive](0006-two-auth-paths.md) | Accepted |
| 7   | [Provider implements the real Hermes MemoryProvider ABC](0007-provider-duck-types-hermes-abc.md) | Superseded by 13 |
| 8   | [Awaken supplements SOUL.md; does not replace it](0008-awaken-supplements-soul.md) | Accepted |
| 9   | [Secret-shaped content is rejected from store paths](0009-secret-shape-rejection.md) | Accepted |
| 10  | [Builtin MEMORY.md/USER.md writes are not mirrored by default](0010-no-builtin-mirror-by-default.md) | Accepted |
| 11  | [Integration tests hit the live dev API; skip cleanly without creds](0011-integration-tests-hit-live-dev-api.md) | Accepted |
| 12  | [Send a real User-Agent; Cloudflare blocks bare urllib](0012-real-user-agent-required.md) | Accepted |
| 13  | [Validate integration seams against real source, not guesses](0013-validate-seams-against-real-source.md) | Accepted |
| 14  | [Memory providers are directory-installed, not pip entry points](0014-directory-discovery-not-entry-points.md) | Accepted |

## When to write an ADR

Write one before merge when a decision is non-obvious — when a reader
seeing only the code would reasonably ask "why this way and not the
obvious other way?" Code-level conventions (lint config, naming) and
reversible product decisions (CLI flag names) do not get ADRs.
