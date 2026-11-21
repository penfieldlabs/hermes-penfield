# ADR-0024: Browser sign-in first

- **Date:** 2026-11-20
- **Status:** Accepted. Amends [ADR-0005](0005-two-auth-paths.md) and
  [ADR-0022](0022-setup-hook.md).

## Context

A Penfield account is all a user needs: `hermes penfield login` signs in
through the browser and no API key is involved. An API key is for a
machine nobody sits at, and for limiting what an agent may do, since a
key can be created as read only, read and write, or read, write and
delete.

Version 0.3.0 was built the other way round. `hermes memory setup
penfield`, the command Hermes prints after an install, asked only for an
API key, and the messages a disconnected profile showed asked for one
too. Under `plugins.isolation: host` the command asked nothing at all,
because the plugin runs in the plugin host, whose standard streams are
Hermes's channel (ADR-0022), and `hermes penfield login` does not exist
there. A profile with host isolation could only be connected with a key.

The sign-in asked Penfield for the scopes `read write offline_access`.
Penfield's auth server also has a `delete` scope, and two of the tools
delete.

## Decision

- The setup hook offers the browser sign-in first, as the default
  answer, and the API key second. Choosing the sign-in runs the device
  code flow in place: the hook shows the link and the code and waits for
  the approval.
- A profile that is already signed in is offered to keep that sign-in
  (the default), to use an API key, or to sign in again. The saved
  tokens do not tell whether Penfield still accepts them, so the hook
  makes one request first, and says that it is checking, since the
  answer can take seconds. A sign-in that has run out or was revoked is
  not offered to keep: signing in again is then the default, because
  this is the command a disconnected profile is told to run. A sign-in
  that could not be checked, because Penfield did not answer, can still
  be kept, and the hook says that it was not checked.
- Signing in again revokes the sign-in it replaces once the new one is
  stored. Overwritten, the earlier tokens would stay valid at Penfield
  with nothing left that could revoke them.
- A profile with an API key is asked about the key only, and told how
  to move to a browser sign-in. The key still takes precedence
  ([ADR-0023](0023-one-login-at-a-time.md)): a key someone set to limit
  access must not be widened by a sign-in.
- Nothing is changed unless the profile ends up connected. A sign-in that
  is refused, runs out or is cancelled leaves the profile as it was.
- In the plugin host the hook talks through the controlling terminal
  (`/dev/tty`), the way `ssh` and `sudo` reach the user when their
  standard streams are taken. Hermes starts the plugin host without
  detaching it from the terminal and waits for the hook without a time
  limit. Where there is no controlling terminal (a service, a script,
  Windows) there is no hook, as before.
- Ctrl-C cancels the setup at any point and changes nothing. In the
  plugin host the terminal would also deliver it to Hermes as a signal,
  and Hermes, waiting for the hook, would end with a traceback. For the
  length of the hook the terminal is therefore set to pass Ctrl-C on as a
  key (signals off, Ctrl-C as an end-of-line key), and the hook reads it
  as "cancel", at a question and while it waits for the approval. The
  settings are put back when the hook ends. Hermes ends the plugin host
  without unwinding it, so a small guard process puts them back if the
  host goes away first.
- The sign-in asks for `read write delete offline_access`, so that every
  tool works under the recommended login.
- What a login may do is shown wherever the login is named: `hermes
  memory status`, `hermes penfield status`, after a sign-in, and in the
  answer to a tool call Penfield refused. It is read from the scope the
  auth server granted, or from the `scopes` claim of an access token made
  from an API key. The API enforces it; the plugin only reports it.
- Messages name the sign-in first and the key second.

## Consequences

- `hermes plugins install`, `hermes memory setup penfield`, Enter, and an
  approval in the browser connect a profile. No key is created or stored.
- Profiles with host isolation can sign in.
- A sign-in made with 0.3.0 keeps the scope it was given. Signing in
  again, from the setup command or with `hermes penfield login`, asks for
  the wider one.
- The hook writes to a terminal it did not get from Hermes. It writes
  nothing there that it would not print, and a key is still read without
  echo.
- While the hook runs in the plugin host, Ctrl-\ and Ctrl-Z do nothing
  either. They work again when it ends.
