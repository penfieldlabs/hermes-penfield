# ADR-0023: One login at a time

- **Date:** 2026-11-20
- **Status:** Accepted. Extends [ADR-0005](0005-two-auth-paths.md).

## Context

The plugin can sign in two ways (ADR-0005): with an API key, or with a
browser sign-in. Either way it ends up with an access token and a refresh
token cached in `penfield/tokens.json`, and the cached tokens were used
for as long as they could be refreshed.

The cache did not record where its tokens came from. After the API key
was replaced with one for another account, the plugin kept using the
tokens of the first account, reading and writing that account's memories
until the old key was revoked. A browser sign-in cached before a key was
configured had the same effect, while `hermes penfield status` reported
the API key as the credentials in use.

## Decision

- A profile uses one login: the API key when one is configured, and
  otherwise a saved browser sign-in.
- Tokens made from an API key are stored with a fingerprint of that key:
  the first 16 hex digits of its SHA-256.
- While an API key is configured, cached tokens are used only if they
  carry that key's fingerprint. Any other cached token set, including a
  browser sign-in and a cache written by an earlier version, is ignored
  and replaced by the next key exchange.
- Without an API key, cached tokens are used only if they come from a
  browser sign-in. Tokens left behind by a key that has been removed are
  not.
- `hermes penfield login` does not start while a key is configured, and
  says why.
- `hermes penfield logout` revokes a browser sign-in found in the cache
  even when a key had set it aside, and says when an API key is still in
  use.
- `hermes memory setup penfield` revokes a saved browser sign-in when it
  stores a key, since the key replaces it.

## Consequences

- A changed API key takes effect on the next request, and removing the
  key disconnects the profile.
- A key added to `.env` by hand overwrites a saved browser sign-in
  without revoking it at Penfield. `hermes penfield logout` beforehand
  revokes it.
- Upgrading costs one extra key exchange per profile, the first time a
  cache without a fingerprint is met.
- The token file has one more field. Earlier versions ignore it, so
  moving back to one of them still works.
- Processes sharing a profile share its tokens as before, because they
  share its key.
