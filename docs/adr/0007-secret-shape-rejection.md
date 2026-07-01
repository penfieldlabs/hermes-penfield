# ADR-0007: Secret-shaped content is rejected from store paths

- **Date:** 2026-06-29
- **Status:** Accepted

## Context

Agents occasionally attempt to store content containing secrets (pasted
tokens, PEM keys, AWS keys).

## Decision

`_reject_secret_like` runs on every `penfield_store` call. It matches a
small set of high-signal prefixes (`-----BEGIN `, `ghp_`, `github_pat_`,
`xoxb-`, `AKIA`, `sk-`, `sk_live_`) and raises `ValueError` on a hit.
This is a guardrail, not a security boundary.

## Consequences

- Some legitimate content matching these prefixes would be rejected;
  the prefix list is deliberately tiny to keep false positives rare.
