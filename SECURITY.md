# Security Policy

## Reporting a vulnerability

Report security issues through
[GitHub's private vulnerability reporting](https://github.com/penfieldlabs/hermes-penfield/security/advisories/new).
Do not open a public issue for a vulnerability.

Best-effort response, typically within two weeks.

## How credentials are handled

- **API key.** Hermes stores it in the profile's `.env` as
  `PENFIELD_API_KEY`. The plugin reads it through Hermes, so a gateway
  serving several profiles gives each profile its own key. The plugin
  keeps no copy of the key: `hermes memory setup penfield` hands it to
  Hermes to store, and it is never logged. Setup shows the last four
  characters of a key that is already set, and nothing of one being
  typed.
- **Tokens.** The API key is exchanged for a short-lived access token and
  a refresh token. Both are cached in `penfield/tokens.json` inside the
  profile directory. The file is created with mode `0600` and replaced
  atomically, so another process never reads a half-written file. Browser
  sign-in (`hermes penfield login`) stores its tokens in the same file.
  `hermes penfield logout` revokes a browser sign-in at Penfield, so the
  tokens stop working everywhere, and deletes the file.
- **One login at a time.** Tokens made from an API key are stored with a
  short one-way fingerprint of that key and are used only while the same
  key is configured. Replace the key and the next request signs in with
  the new one, so nothing is read from or written to the account the old
  key belonged to. Remove the key and its tokens are no longer used. A
  configured API key takes precedence over a saved browser sign-in.
- **Settings.** `penfield/config.json` holds behaviour settings only.

## What leaves the machine

The README section
[What is sent to Penfield](README.md#what-is-sent-to-penfield) lists every
kind of data the plugin sends and when. All of it goes to the Penfield API
over HTTPS. The plugin contacts no other service.

## Secrets in memory content

Hermes Agent removes credentials it recognises from recall queries, tool
arguments and conversation text before handing them to a memory provider.
The plugin relies on that and does not filter content itself. See
[ADR-0014](docs/adr/0014-no-content-filter.md).

Penfield memories are shared with every agent connected to the account.
Do not ask an agent to remember a password or key.

## Scope

**In scope:** the plugin's source code, its handling of the token file,
its HTTP client and its CLI commands.

**Out of scope:** the Penfield service itself, Hermes Agent, and anything
in the Hermes profile directory that this plugin did not write.

## If you leaked a credential

If an API key or token was pasted into a chat, commit or issue:

1. Revoke it at
   [portal.penfield.app/settings/api-keys](https://portal.penfield.app/settings/api-keys)
   and create a new one.
2. Treat the old key as compromised whether or not it was used.
3. Review recent memories in your Penfield account for anything that
   should not be there.
