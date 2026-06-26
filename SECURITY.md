# Security Policy

## Reporting a vulnerability

Report security issues through
[GitHub's private vulnerability reporting](https://github.com/penfieldlabs/hermes-penfield/security/advisories/new).
Do not open a public issue for security vulnerabilities.

Best-effort response, typically within two weeks.

## Secrets handling

- **No secrets in the repository.** No API keys, tokens, PATs, or
  personal data are committed. The pre-commit hook and CI reject known
  secret shapes.
- Credentials come from `.env` (gitignored) or environment variables
  (`PENFIELD_API_KEY`).
- OAuth tokens cache to `{hermes_home}/penfield/tokens.json` with mode
  `0600` (owner read/write only).
- The `penfield_store` path rejects content matching high-signal secret
  prefixes (`ghp_`, `github_pat_`, `-----BEGIN `, `xoxb-`, `AKIA`). This
  is a guardrail, not a security boundary — see
  [ADR-0009](docs/adr/0009-secret-shape-rejection.md).
- `on_memory_write` mirroring (off by default) applies the same guardrail
  so opted-in mirroring can't exfiltrate a secret that slipped into
  MEMORY.md.

## Scope

**In scope:** hermes-penfield source code, local token file handling,
HTTP client behavior, CLI behavior.

**Out of scope:** Penfield server-side data correctness, upstream Hermes
Agent behavior, anything in the user's Hermes home directory not written
by this plugin.

## If you leaked a credential

If an API key or token was pasted into a chat, commit, or issue:

1. **Rotate it immediately** at
   [portal.penfield.app/settings/api-keys](https://portal.penfield.app/settings/api-keys).
2. Treat the leaked credential as compromised regardless of whether it
   was used — it may be in logs upstream of where you can see.
3. Audit recent stores in your Penfield tenant for content that shouldn't
   be there.
