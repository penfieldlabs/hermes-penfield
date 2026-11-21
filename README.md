# hermes-penfield

**Penfield persistent memory for [Hermes Agent](https://github.com/NousResearch/hermes-agent).**

Penfield gives an agent a memory that outlives the session: a knowledge
graph with hybrid search (keyword, vector and graph), typed connections
between memories, saved context checkpoints and file storage. The same
memory is shared with every other agent connected to your Penfield account.

This plugin makes Penfield the memory provider of a Hermes agent. Hermes
loads your Penfield briefing at session start, recalls relevant memories
before each turn, and gives the agent 17 `penfield_*` tools to store,
search and connect what it learns.

## Requirements

- Hermes Agent 0.21.6 or newer.
- A Penfield account.

The plugin has no dependencies of its own. It uses the Python standard
library only.

## Install

```bash
hermes plugins install penfieldlabs/hermes-penfield --enable
```

Hermes copies the plugin into your profile's `plugins/penfield/` directory
and selects it as the memory provider.

To upgrade an existing install:

```bash
hermes plugins update penfield
```

## Connect

```bash
hermes memory setup penfield
```

Press Enter to sign in with your browser, the recommended way. The
command shows a link and a code. Open the link in a browser on any
device and approve it within ten minutes. The profile is then connected
and Penfield is its memory provider. The tokens are kept in
`penfield/tokens.json` in your profile, readable only by you, and renewed
automatically.

A profile registers with Penfield as "Hermes Agent (Penfield plugin)" at
its first sign-in and uses the same registration for every later one. It
is kept in `penfield/client.json`. Should a sign-in ever be refused
because of it, delete that file and sign in again: the profile then
registers anew.

Check the result at any time:

```bash
hermes memory status
```

It names the login in use and what that login may do. Start a new session
and Penfield is active.

`hermes penfield login` signs in the same way, and `hermes penfield
logout` revokes the sign-in at Penfield and deletes the file.

Run the setup command again whenever you like. It asks Penfield whether
the sign-in still works and offers to keep it, or to sign in again when
it has run out or was revoked. Signing in again ends the earlier sign-in
at Penfield.

### Using an API key instead

An API key is the backup for a machine nobody sits at, and the way to
limit what the agent may do: a key can be read-only, read and write, or
read, write and delete. Create one at
[portal.penfield.app/api-keys](https://portal.penfield.app/api-keys), run
`hermes memory setup penfield` and choose the API key.

Paste the key; it is not shown as you type. The plugin checks the key
with Penfield and Hermes stores it in the profile's `.env` as
`PENFIELD_API_KEY`. A key Penfield does not accept is not saved, and
neither is one with a character that cannot be part of a key, such as an
invisible character picked up while copying; the command names it.
Whitespace around a key is ignored. You can also add that line to `.env`
yourself.

With a key that may not write or delete, the tools that need more are
refused, and the agent is told what its login may do.

A profile uses one login at a time, and an API key takes precedence, so
a key you set to limit access is always the one in use. While
`PENFIELD_API_KEY` is set, setup asks about the key only and
`hermes penfield login` does not start. Storing a key that Penfield
accepted ends a saved sign-in. If Penfield could not be reached to check
the key, the sign-in is kept; run the setup command again later and
enter the key again to end it. To go back to a browser sign-in, remove
the key from `.env` and run the setup command again.

### Without a terminal, and with plugin isolation

The setup command asks its questions on a terminal. Run from a script or
a service it only selects Penfield, and the profile needs
`PENFIELD_API_KEY` in its `.env`.

With `plugins.isolation: host`, `hermes memory setup penfield` works as
described above. The `hermes penfield` commands exist only when Hermes
runs plugins in-process, which is the default.

## What it does in a session

| When | What happens |
| ---- | ------------ |
| Session start | Your Penfield briefing (persona and custom instructions) is added to the system prompt, next to Hermes's own `SOUL.md`. |
| Before each turn | Memories and document passages relevant to your message are recalled and shown to the agent, each with its id, type and date. Saved checkpoints are left out. |
| During a turn | The agent uses the `penfield_*` tools as it sees fit. |
| Before Hermes compresses a long conversation | Nothing, unless you turn on `pre_compress_save`. Then a context checkpoint is saved, linking the memories the conversation was about, and Hermes is given its name for the compression summary so the agent can restore it later. |

Penfield stores what the agent decides to remember. Conversation turns are
not copied into it.

If Penfield is slow or unreachable, the session carries on without it.
Session start waits at most 6 seconds for the briefing, a turn at most
2.5 seconds for recall, and a compression at most 8 seconds for its
checkpoint. A tool call reports a failure to the agent instead of
hanging. Recall on the current message normally adds about a second to a
turn. A session that had to start without your briefing says so, with the
reason, in Hermes's log.

## Tools

| Tool | What it does |
| ---- | ------------ |
| `penfield_store` | Store a memory. Penfield detects its type. |
| `penfield_recall` | Hybrid search: keywords, meaning and graph links, with filters for tags, dates and source. The one to use for names, ids and exact phrases. |
| `penfield_search` | Semantic search: ranks by meaning far more than by shared words. For when you do not know how a memory was worded. |
| `penfield_fetch` | Get one memory by id. |
| `penfield_update_memory` | Change a memory's content, importance or tags. |
| `penfield_connect` | Link two memories with one of 24 relationship types. |
| `penfield_disconnect` | Remove the link between two memories. |
| `penfield_explore` | Walk the graph outwards from a memory. |
| `penfield_reflect` | Summarise recent activity: topics, patterns, saved contexts. |
| `penfield_save_context` | Save a checkpoint for a later session or another agent. |
| `penfield_list_contexts` | List saved checkpoints. |
| `penfield_restore_context` | Load a checkpoint and the memories it links. |
| `penfield_save_artifact` | Save a file at a path. Files are not searched by recall; store a memory that points to the file. |
| `penfield_retrieve_artifact` | Read a file by path. |
| `penfield_list_artifacts` | List the files and folders in a directory. |
| `penfield_delete_artifact` | Delete a file. |
| `penfield_awaken` | Reload the briefing. Hermes already does this at session start. |

Tool names and parameters match Penfield's MCP server, with a `penfield_`
prefix, so an agent that knows Penfield from another client works the
same way here. One parameter is missing: `include_inactive` on recall and
search, which Penfield's REST API does not act on.

Penfield rewrites a tag before it stores it: lower case, spaces and
underscores as hyphens, and every other character that is not a-z, 0-9
or a hyphen removed, so `C++ Tips` is stored as `c-tips`. What is left
has to be 2 to 50 characters long, and a memory can have up to 10 tags.
`penfield_store` and `penfield_update_memory` refuse a tag that would be
left shorter or longer than that, naming it by its position in the list,
and refuse more than 10 tags, so the agent can correct the call and make
it again.

## Settings

Everything works without configuration. To change behaviour, create
`penfield/config.json` in your Hermes profile directory (`~/.hermes` unless
you use profiles):

```json
{
  "recall_limit": 8,
  "pre_compress_save": true
}
```

| Setting | Default | Meaning |
| ------- | ------- | ------- |
| `auto_recall` | `true` | Recall memories before each turn. |
| `recall_limit` | `5` | Most memories recalled per turn (1 to 100). |
| `recall_threshold` | `0.25` | Lowest search score a recalled memory may have (0 to 1). Penfield scores each result relative to the best keyword match of the same search, so this trims the weak end of a result list. Lower `recall_limit` if you want less recalled. |
| `max_prefetch_chars` | `3000` | Size limit of the recalled text added to a turn. |
| `pre_compress_save` | `false` | Save a checkpoint before Hermes compresses the conversation. Each one is added to your saved contexts with the tag `auto-checkpoint`, so a long-running agent adds several a day. |
| `mirror_builtin_writes` | `false` | Also copy what Hermes's built-in memory tool writes (`MEMORY.md`, `USER.md`) into Penfield, tagged `hermes-builtin`. Edits update the copy. Removals delete it if your login has the delete permission. |

Environment settings, read from the profile's `.env`:

| Variable | Meaning |
| -------- | ------- |
| `PENFIELD_API_KEY` | An API key, if you use one. |
| `PENFIELD_URL` | Use a different API base URL. It must be `https`; plain `http` is accepted for `localhost` only. |
| `PENFIELD_ENV` | `prod` (default) or `dev`. |

## What is sent to Penfield

The plugin talks to the Penfield API at `api.penfield.app` and to nothing
else. Browser sign-in also contacts `auth.penfield.app`. It does not
follow redirects. There is no telemetry. Requests identify themselves as
`hermes-penfield/<version>`.

| Data | When |
| ---- | ---- |
| Your message text, as a search query | Before each turn, unless `auto_recall` is off. Hermes skips greetings, one-word replies and slash commands. |
| Whatever the agent puts in a tool call: memory text, tags, search queries, file content | When the agent calls a `penfield_*` tool. |
| Up to five of your recent messages, 160 characters each, as the checkpoint description | Before a compression, only with `pre_compress_save` on. |
| Entries of Hermes's built-in memory | Only with `mirror_builtin_writes` on. |

Hermes removes credentials it recognises from all of this before the
plugin sees it.

On your machine the plugin writes up to three files in the profile
directory: `penfield/config.json` (settings, never the API key), after a
browser sign-in or a token exchange `penfield/tokens.json`, and after a
browser sign-in `penfield/client.json`, the profile's registration with
Penfield's sign-in server. The last two have mode `0600`.

## Command line

Available when plugins run in-process (the default):

```bash
hermes penfield status     # the login in use, what it may do, whether it works
hermes penfield search "launch checklist" --limit 5
hermes penfield stats
hermes penfield login      # browser sign-in
hermes penfield logout     # revoke the sign-in and forget cached tokens
hermes penfield version
```

## Troubleshooting

- `hermes memory status` shows whether the provider is available, which
  login and settings are in effect, and what the login may do.
- "Memory provider 'penfield' is selected but reports unavailable": the
  profile has no sign-in and no API key. Run `hermes memory setup
  penfield`. A gateway started by systemd does not read your shell's
  environment, so an API key has to be in the profile's `.env`. A browser
  sign-in is kept in the profile itself.
- "The saved Penfield sign-in is no longer valid": it ran out or was
  revoked. Run `hermes memory setup penfield` or `hermes penfield login`
  to sign in again. Sessions that are already running use the new
  sign-in from their next request.
- "The API key cannot be used": the key holds a character that cannot be
  part of one, usually picked up while copying. The message names the
  character and where it is. Copy the key again and run `hermes memory
  setup penfield`.
- "Penfield is off": a setting cannot be used, for example an unknown
  `PENFIELD_ENV` or a `PENFIELD_URL` that is not `https`. The message
  names it. The agent is told the same, so it can say why its memory
  tools fail.
- "Penfield refused this": the login may not do what the tool needs.
  The message says what it may do.
- `hermes penfield status` makes a request with the login in use and
  says whether Penfield accepts it.
- `hermes plugins doctor penfield` checks the installed plugin.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). Design decisions are recorded in
[docs/adr](docs/adr/README.md).

## License

MIT. Copyright (C) 2026 Penfield.
