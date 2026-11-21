# ADR-0022: A setup hook for `hermes memory setup penfield`

- **Date:** 2026-11-20
- **Status:** Accepted. Extends [ADR-0017](0017-settings-through-hermes.md). Amended by [ADR-0024](0024-browser-sign-in-first.md).

## Context

Hermes has two ways into memory setup. `hermes memory setup` shows a list
of providers and, for the one chosen, asks the questions in the
provider's config schema. `hermes memory setup <name>` skips the list. It
asks nothing unless the provider has a `post_setup` hook, and otherwise
only selects the provider. That second form is the command Hermes prints
after `hermes plugins install`, so a new user who followed it ended up
with Penfield selected and no API key.

When a provider has the hook, Hermes hands the whole setup to it, from
either entry point.

Under `plugins.isolation: host` the provider object lives in the plugin
host, a process without a terminal. A hook called there cannot ask
anything.

## Decision

- The provider has a `post_setup` hook, in `setup_wizard.py`. It asks for
  the API key with input hidden, asks Penfield whether it accepts the
  key, writes the key to the profile's `.env`, and selects Penfield as
  the memory provider.
- A key Penfield refuses changes nothing. A key that cannot be checked,
  because Penfield could not be reached, is saved and reported as
  unchecked. An empty answer keeps the key already there.
- The check makes one key exchange and caches nothing.
- The key is asked for with the standard library. It is saved with
  `save_env_value` and `save_config` from Hermes's published plugin API,
  the writers Hermes's own setup uses, so the key is stored exactly as
  Hermes would store it.
- The hook exists only where it can run. `post_setup` is a property that
  raises `AttributeError` when the process has no terminal or Hermes does
  not offer those functions. Hermes then sees a provider without a hook
  and asks its schema questions, as before.
- The config schema stays, for that case and for Hermes's other setup
  screens.

## Consequences

- `hermes plugins install`, then `hermes memory setup penfield`, leaves a
  working provider, and a mistyped key is caught at once.
- Under the plugin host the named command still only selects the
  provider; `hermes memory setup` without a name asks for the key. The
  README says so.
- Scripts and tests, which have no terminal, get Hermes's own behaviour.
- The integration tests type into the real command on a pseudo-terminal,
  on every supported Hermes release.
