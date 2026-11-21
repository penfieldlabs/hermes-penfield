# ADR-0029: One registration per profile

- **Date:** 2026-11-21
- **Status:** Accepted. Extends [ADR-0024](0024-browser-sign-in-first.md).

## Context

A browser sign-in needs a client id, which the plugin gets by registering
with Penfield's sign-in server. It registered again at every sign-in.
Each registration is a new client to Penfield, named `hermes-penfield`,
and every sign-in left one more behind. Registering is also one more
request that has to succeed before the user sees a link.

## Decision

- A profile registers once and keeps the client id in
  `penfield/client.json`, written like the token cache: private from
  creation and replaced atomically. It is kept for the API address it
  was made at and for the scope it was registered with.
- Later sign-ins at that address use it. The plugin registers again only
  when nothing is kept, when the scope it asks for has changed, or when
  the sign-in server answers `invalid_client` for the kept one.
- Any other refusal met with the kept registration is reported, and the
  message says how to start over: delete `penfield/client.json` and
  sign in again. A server that fails or cannot be reached is not a
  refusal and gets no such advice.
- A client id given by hand (`hermes penfield login --client-id`) is used
  as given, never kept and never replaced.
- The registration carries the name "Hermes Agent (Penfield plugin)".
- `logout` ends the sign-in and leaves the registration. It is not a
  credential.
- A registration that cannot be written is used for that sign-in and
  made again at the next.

## Consequences

- Every sign-in of a profile is made by the same client.
- A sign-in with a kept registration no longer depends on the
  registration endpoint.
- Signing in again ends the sign-in it replaces
  ([ADR-0023](0023-one-login-at-a-time.md)); both now belong to the same
  client. The earlier sign-in is ended by its own tokens, as before.
