# ADR-0009: Secret-shaped content is rejected from store paths

- **Date:** 2026-06-26
- **Status:** Accepted

## Context

Agents will occasionally attempt to store content that contains secrets
(pasted tokens, PEM keys, AWS keys). Once stored, a secret is in the
memory graph and hard to fully expunge. The provider cannot truly detect
all secrets, but it can catch the obvious shapes before they're sent.

## Decision

`tools._reject_secret_like` runs on every `penfield_store` and on
`on_memory_write` mirroring. It matches a small set of high-signal
prefixes (`-----BEGIN `, `ghp_`, `github_pat_`, `xoxb-`, `AKIA`) and
raises `ValueError` on a hit. This is a **guardrail, not a security
boundary** — the real guarantee is "we don't ship secrets," and the
guardrail stops the obvious paste.

## Consequences

- Some legitimate content matching these prefixes would be rejected;
  the prefix list is deliberately tiny to keep false positives rare.
- This is not a substitute for not putting secrets in conversations in
  the first place; it's a tripwire.
- `on_memory_write` mirrors are also guarded, so opting into builtin
  mirroring can't exfiltrate a secret that slipped into MEMORY.md.
