# Changelog

All notable changes to hermes-penfield are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.2] - 2026-11-21

The agent is told about a tag Penfield does not store, and a profile
keeps one registration for its browser sign-ins.

### Changed
- `penfield_store` and `penfield_update_memory` refuse a tag Penfield
  does not store and name it by its position in the list, so the agent
  can correct it. Penfield rewrites a tag to lower case with only a-z,
  0-9 and hyphens, and stores it if 2 to 50 characters are left. A tag
  left with more, or with fewer (one letter, `c++`, a tag in another
  script), used to be sent, and the memory was stored without it.
- More than 10 tags in one call are refused before anything is sent,
  with the count.
- A blank tag, which a model sends when it means none, is left out
  instead of being sent.
- The descriptions of both tools say what becomes of a tag and when one
  is refused.
- `penfield_save_context` and the checkpoint saved before compression
  send the checkpoint's name as a tag only where it can be stored as
  one. The name itself is not limited and is found as before.
- A profile registers with Penfield's sign-in server once and uses that
  registration for later browser sign-ins. It is kept in
  `penfield/client.json` (mode `0600`). Before, every sign-in made a new
  registration. The plugin registers again only if the sign-in server no
  longer knows the one it kept.
- The plugin registers as "Hermes Agent (Penfield plugin)" instead of
  `hermes-penfield`. The first sign-in after the upgrade registers under
  the new name.
- A sign-in that is refused while it uses the kept registration says how
  to start over: delete `penfield/client.json` and sign in again.

See ADR-0028 and ADR-0029.

## [0.3.1] - 2026-11-21

Browser sign-in becomes the first way to connect, and the sign-in and
the HTTP layer are made to survive servers and networks that misbehave.

### Upgrading from 0.3.0
Most profiles need nothing. These do:
- A `PENFIELD_URL` that starts with `http://` and does not point at this
  machine is refused. Use the `https` address.
- A browser sign-in made while `PENFIELD_URL` was set is not used after
  the upgrade, because 0.3.0 did not record where a sign-in was made.
  Run `hermes memory setup penfield` to sign in again.
- An API key stored as `api_key` in `penfield/config.json` is no longer
  read, and the log says so. Put the key in the profile's `.env` as
  `PENFIELD_API_KEY`, or run `hermes memory setup penfield` and enter it
  there.
- If the address in `PENFIELD_URL` answers with a redirect, set it to
  the address the redirect names.

### Added
- `hermes memory setup penfield` offers a browser sign-in first: press
  Enter, approve the code in a browser, and the profile is connected. An
  API key is the second choice. A profile that is already signed in can
  keep its sign-in or sign in again. The command first asks Penfield
  whether that sign-in still works, and offers to sign in again when it
  has run out or was revoked.
- The setup command works under `plugins.isolation: host`, where it
  talks through the terminal the command was typed in. Profiles with
  host isolation can now sign in with a browser.
- `hermes memory status` and `hermes penfield status` say what the login
  in use may do: read only, read and write, or read, write and delete.
- A tool call Penfield refuses for lack of permission tells the agent
  what its login may do.
- A setting the plugin cannot use (an unknown `PENFIELD_ENV`, for one)
  is named in the log, in what the agent is told, in every tool error
  and in the status commands, instead of "Penfield is not initialized".

### Changed
- README: browser sign-in is the recommended way to connect. An API key
  is the alternative, and the way to limit what the agent may do.
- A browser sign-in asks Penfield for read, write and delete access, so
  that every tool works. A sign-in made earlier keeps what it was given
  until you sign in again.
- `PENFIELD_URL` must be an `https` address. Plain `http` is accepted for
  `localhost` only.
- Redirects are not followed. Every request carries a login, and a
  redirect would have sent it on to another address.
- Cached tokens are used only with the API address that issued them. A
  profile switched to another address with `PENFIELD_ENV` or
  `PENFIELD_URL` signs in again there; before, its saved tokens were
  sent to the new address first.
- Tool arguments of the wrong kind are refused by name ("limit must be a
  whole number"). A number or a list of tags written as text is taken as
  meant.
- An `api_key` entry in `penfield/config.json` is no longer read. The
  key belongs in the profile's `.env`, where Hermes puts it.
- Signing in and renewing a token give up after 8 seconds per attempt
  instead of 15.
- Signing in again, with the setup command or `hermes penfield login`,
  revokes the earlier sign-in at Penfield once the new one is stored.
- The setup command ends a saved browser sign-in when the API key it
  stores was accepted by Penfield. With a key that could not be checked,
  the sign-in stays.

### Fixed
- One bad answer from the auth server (a 5xx or a 429) while a browser
  sign-in renewed its token no longer disconnects the profile until
  Hermes is restarted. A sign-in ends only when Penfield says it is no
  longer valid.
- A saved sign-in that has run out or was revoked says so, and says to
  sign in again, instead of "no credentials available". Sessions that
  are already running use the new sign-in from their next request.
- An API key that ends with a line break, as a key read from a file
  often does, made every request fail inside the HTTP library with an
  error that quoted the key, and a tool call passed that error on to
  the agent. Whitespace around a key is now dropped. A key with any
  other character that cannot be part of one is refused with a message
  that names the character and never the key, and an error about a
  request that could not be sent no longer quotes its headers.
- When several Hermes processes share a profile, one no longer renews
  with a refresh token another has already used.
- An answer without a refresh token, which the API may give to a key
  exchange, is handled. The text `None` was stored and sent as a refresh
  token before.
- `hermes penfield login` keeps waiting when the auth server stumbles
  while you are in the browser, says so when the server keeps failing,
  and names the failure if the code runs out. Ctrl-C ends it with a
  message instead of a traceback.
- Answers that are not what the plugin expects (a web page from a
  captive portal, a body cut off half way, an error that is not JSON)
  are reported as Penfield errors. They surfaced as Python errors such
  as "'NoneType' object has no attribute 'get'" before.
- A `penfield/config.json` or `penfield/tokens.json` that is damaged no
  longer stops the provider. The file is ignored, with a warning.
- Recalled context is no longer cut in the middle of a memory when it
  reaches `max_prefetch_chars`, and the recall indicator counts the
  memories that were shown.
- `hermes penfield search words --limit 3` takes `--limit` as the option
  it is. It was read as part of the query.
- The `hermes penfield` commands report a setting they cannot use as an
  error message instead of a traceback.

## [0.3.0] - 2026-11-20

Brings the plugin up to date with Hermes Agent 0.23 and with Penfield's
current tool surface. Requires Hermes Agent 0.21.6 or newer.

### Added
- Works with Hermes's plugin host (`plugins.isolation: host`) as well as
  in-process.
- Recall indicator: Hermes shows how many memories were recalled for a turn.
- A session that starts without the briefing writes a warning to Hermes's
  log, with the reason.
- `hermes memory status` shows the environment, API address, credentials
  in use and behaviour settings.
- `hermes memory setup penfield`, the command Hermes suggests after an
  install, asks for the API key, checks it with Penfield before saving
  it, and selects the provider. A key Penfield refuses is not saved.
- `hermes penfield logout` revokes a browser sign-in at Penfield as well
  as clearing the local token cache, and says when an API key is still in
  use.
- `penfield_reflect`: time windows `month`, `1d`, `7d`, `30d`, `90d`.
- Recall results from uploaded documents carry the file name, the
  document id and the surrounding text of the passage.
- `penfield_list_artifacts`: `name_pattern` filter.
- `penfield_restore_context`: accepts a checkpoint id as well as a name.
- `penfield_save_context`: reports references that did not resolve, and
  links the most recent memories when the description is too short to
  search on.
- Integration tests that run the plugin inside Hermes Agent 0.21.6,
  0.22.7, 0.23.0 and Hermes `main`, weekly.
- A snapshot of Penfield's MCP tool list, with tests that compare every
  tool, parameter, type and default against it.
- Python 3.14 support.

### Changed
- The provider now subclasses Hermes's `MemoryProvider`.
- Hermes is never held up for long: session start waits at most 6
  seconds for the briefing, a turn at most 2.5 seconds for recall, a
  compression at most 8 seconds for its checkpoint. Tool calls time out
  after 15 seconds with at most two retries.
- The briefing is fetched once per session, in the background.
- Auto-recall leaves saved checkpoints out.
- Recall returns whole memories (up to 2,000 characters each) with tags,
  type, importance and date. Auto-recall shows full memory ids.
- `hermes penfield status` makes a request and reports whether the
  credentials in use still work.
- `hermes memory setup` asks for the API key only. The key and the
  `PENFIELD_*` settings are read through Hermes, per profile.
- A profile uses one login at a time, and an API key takes precedence
  over a browser sign-in. While `PENFIELD_API_KEY` is set,
  `hermes penfield login` does not start and a saved sign-in is not used.
- The checkpoint saved before compression is off by default; set
  `pre_compress_save` to turn it on. When on, it is built from the user's
  own messages without a model call, is tagged `auto-checkpoint`, and its
  name is returned to Hermes for the compression summary. Only the
  primary agent saves one.
- `penfield_search` is a semantic search: it ranks by meaning far more
  than by shared words. It used to return the same results as
  `penfield_recall`.
- `mirror_builtin_writes` now updates and removes mirrored memories when
  the built-in entry is edited or removed, and runs in the background.
- `penfield_list_artifacts` takes `path_prefix`, as Penfield's MCP tool does.
- `recall_threshold` is applied as a minimum search score. Default `0.25`.
- Installing no longer asks for a dependency step: the plugin declares
  none, and the repository has no `pyproject.toml` for Hermes to read as
  one.
- Python 3.11 is the oldest supported version.

### Fixed
- Requests that create a memory or a relationship are not repeated after
  a timeout or server error, so a slow response can no longer store the
  same thing twice.
- A changed or removed API key takes effect at once. Tokens cached for
  the previous key, or for a browser sign-in, were used until they could
  no longer be refreshed, so memories could be read from and written to
  the account the old key belonged to.
- A sign-in the server did not answer is tried again, like any other
  request. One dropped connection at session start used to leave the
  whole session without the briefing.
- Expired access tokens are refreshed before a request is sent.
- A login code that expires or is refused, and a saved login that has
  stopped working, are reported in plain words.
- Several Hermes processes sharing one sign-in no longer invalidate each
  other's refresh token.
- The token cache is written atomically with owner-only permissions from
  the start.
- Checkpoint name checks, `penfield_list_contexts` filtering and paging
  cover every saved checkpoint, however many there are.
- `penfield_restore_context` honours `limit` and returns whole memories.
- `penfield_recall` with `source_type="memory"` returned nothing. It now
  returns memories and leaves uploaded documents out.
- A tool call with a missing argument names the argument, and a request
  the API rejects names the field and the reason.
- The stored session id follows `/new`, `/resume` and compression.

### Removed
- The content filter on `penfield_store`. Hermes redacts credentials
  before a provider sees them, and the filter refused ordinary text.
- The `sync_turn_enabled` and `auth_method` settings, which had no effect.

## [0.2.0] - 2026-07-01

### Added
- save_context: full MCP parity with reference parsing, hybrid search
  memory snapshot, and name uniqueness enforcement
- on_pre_compress now uses save_context for enriched checkpoints (ADR-0012)
- disconnect tool (DELETE /relationships/between)
- delete_artifact tool (DELETE /artifacts)
- MCP drift test pinning tool surface to canonical source
- 12 ADRs documenting architectural decisions

### Fixed
- tags array translated to comma string for REST API (recall)
- on_pre_compress filters tool messages from description
- restore_context uses exact name match, not fuzzy search
- restore_context returns all saved memories (no limit cap)
- list_contexts shows clean checkpoint names, not raw JSON
- save_context deduplicates memory_ids and referenced_memories
- save_context guards empty description
- HERMES_HOME resolution defaults to ~/.hermes
- input_schema renamed to parameters (Hermes expects "parameters")
- Cloudflare User-Agent fix for OAuth form-encoded calls
- OAuth refresh routing (API-key vs OAuth token endpoints)
- Stale refresh token fallback to API-key re-exchange

### Changed
- Tool surface mirrors MCP exactly (17 tools, full parity)
- Self-contained plugin (no pip install, no external deps)
- Parameter names match MCP (from_memory, start_memory, etc.)

## [0.1.0] - 2026-06-29

Initial release. Self-contained Hermes Agent memory provider for Penfield.

### Added
- 16 tools mirroring the Penfield MCP surface
- Self-contained plugin directory (no pip install, no external deps)
- OAuth 2.1 device-code flow (RFC 8628) with dynamic client registration
- API-key exchange with RFC 9700 refresh-token rotation fallback
- Stdlib-only HTTP client with sliding-window rate limiting and backoff
- Environment switching via `PENFIELD_ENV` (prod/dev)
- Auto-awakening: persona and persistent context load on every session

[Unreleased]: https://github.com/penfieldlabs/hermes-penfield/compare/v0.3.2...HEAD
[0.3.2]: https://github.com/penfieldlabs/hermes-penfield/releases/tag/v0.3.2
[0.3.1]: https://github.com/penfieldlabs/hermes-penfield/releases/tag/v0.3.1
[0.3.0]: https://github.com/penfieldlabs/hermes-penfield/releases/tag/v0.3.0
[0.2.0]: https://github.com/penfieldlabs/hermes-penfield/releases/tag/v0.2.0
[0.1.0]: https://github.com/penfieldlabs/hermes-penfield/releases/tag/v0.1.0
