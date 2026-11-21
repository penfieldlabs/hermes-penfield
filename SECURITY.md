# Security Policy

## Reporting a vulnerability

Report security issues through
[GitHub's private vulnerability reporting](https://github.com/penfieldlabs/hermes-penfield/security/advisories/new).
Do not open a public issue for a vulnerability.

Best-effort response, typically within two weeks.

## How credentials are handled

- **Browser sign-in.** The recommended login. `hermes memory setup
  penfield` or `hermes penfield login` shows a link and a code; the
  approval happens in your browser and the plugin never sees a password.
  It receives an access token and a refresh token and caches them in
  `penfield/tokens.json` inside the profile directory. `hermes penfield
  logout` revokes the sign-in at Penfield, so the tokens stop working
  everywhere, and deletes the file. Signing in again revokes the sign-in
  it replaces.
- **API key.** The alternative, and the way to limit what an agent may
  do: a key can be read-only, read and write, or read, write and delete.
  Hermes stores it in the profile's `.env` as `PENFIELD_API_KEY`. The
  plugin reads it through Hermes, so a gateway serving several profiles
  gives each profile its own key. The plugin keeps no copy of the key:
  setup hands it to Hermes to store, and it is never logged. Setup shows
  the last four characters of a key that is already set, and nothing of
  one being typed. The key is exchanged for an access token, cached in
  the same file. Whitespace around the key is dropped, and a key that
  holds anything but visible ASCII is refused before it is sent, with a
  message that names the character and never the key.
- **The token file** is created with mode `0600` and replaced
  atomically, so another process never reads a half-written file. It is
  not deleted when a renewal fails; see
  [ADR-0025](docs/adr/0025-a-login-ends-when-the-server-says-so.md).
- **One login at a time.** Tokens made from an API key are stored with a
  short one-way fingerprint of that key and are used only while the same
  key is configured. Replace the key and the next request signs in with
  the new one, so nothing is read from or written to the account the old
  key belonged to. Remove the key and its tokens are no longer used. A
  configured API key takes precedence over a saved browser sign-in, so a
  key set to limit access is never widened by a sign-in.
- **One address.** The token file records the API address its tokens were
  issued for, and they are sent to that address only. A profile pointed
  at another address with `PENFIELD_ENV` or `PENFIELD_URL` starts from no
  login there. Logging out revokes a sign-in where it was issued.
- **What a login may do** is enforced by Penfield. The plugin shows it
  (`hermes memory status`, `hermes penfield status`) and does not rely on
  it.
- **Settings.** `penfield/config.json` holds behaviour settings only.

## What leaves the machine

The README section
[What is sent to Penfield](README.md#what-is-sent-to-penfield) lists every
kind of data the plugin sends and when. All of it goes to the Penfield API
over HTTPS, and a login is sent nowhere else:

- A redirect is never followed. The plugin reports it instead.
- `PENFIELD_URL` and the auth endpoints must be `https`. Plain `http` is
  accepted for `localhost` only.
- An error about a request that could not be sent never quotes the
  request's headers.

The plugin contacts no other service. See
[ADR-0026](docs/adr/0026-one-door-for-http.md).

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

1. For an API key, revoke it at
   [portal.penfield.app/settings/api-keys](https://portal.penfield.app/settings/api-keys)
   and create a new one. For a browser sign-in, run `hermes penfield
   logout`, which revokes it, and sign in again.
2. Treat the old credential as compromised whether or not it was used.
3. Review recent memories in your Penfield account for anything that
   should not be there.
