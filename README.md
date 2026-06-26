# hermes-penfield

**Penfield persistent memory provider for Hermes Agent.**

Connects a [Hermes Agent](https://hermes-agent.nousresearch.com) instance
to the [Penfield](https://penfield.app) memory API, exposing store /
search / relationship / analysis / artifact operations as agent tools.
Stdlib-only HTTP, OAuth device-code + API-key authentication, dev/prod
environment switching.

Penfield gives Hermes users cross-session, cross-agent memory with hybrid
search (BM25 + vector + graph), typed connections (24 relationship
types), and artifact storage — capabilities that go well beyond the
built-in MEMORY.md/USER.md flat files. Memories are explicit,
user-inspectable objects the user owns and can edit or delete.

> **Status: v0.1.0 (alpha).** The client, auth, and tool layers are fully
> verified against the live `api-dev.penfield.app` contract. The Hermes
> provider adapter layer is implemented to the documented ABC but is the
> one seam that still needs validation against real Hermes source — see
> [ADR-0007](docs/adr/0007-provider-duck-types-hermes-abc.md).

---

## Install

```bash
pip install hermes-penfield
```

No runtime dependencies — stdlib only (Python 3.10+).

## Configure

Set credentials via environment variables (recommended) or
`hermes memory setup`:

```bash
# API key (simplest — CI/headless). OAuth device code is the interactive
# alternative via `hermes penfield login`.
export PENFIELD_API_KEY=tm_yourtenant_ak_yourkey

# Target dev during development; prod is the default.
export PENFIELD_ENV=dev   # or "prod"
```

Credentials are never written to the repo. Tokens cache to
`{hermes_home}/penfield/tokens.json` with mode `0600`. See
[SECURITY.md](SECURITY.md).

## Use

Once installed and configured, Hermes discovers the provider via the
`hermes_agent.plugins` entry point. The 13 `penfield_*` tools become
available to the agent:

| Tool                    | What it does                                  |
| ----------------------- | --------------------------------------------- |
| `penfield_store`        | Store a memory (decision/preference/fact)     |
| `penfield_recall`       | Semantic search (hybrid: BM25+vector+graph)   |
| `penfield_search`       | Structured search with weight controls        |
| `penfield_connect`      | Create a typed relationship between memories  |
| `penfield_fetch`        | Get a memory by UUID                          |
| `penfield_update`       | Update a memory (PUT)                         |
| `penfield_delete`       | Delete a memory (needs `delete` scope)        |
| `penfield_explore`      | Graph traversal from a memory                 |
| `penfield_reflect`      | Generate insights over a time window          |
| `penfield_list_artifacts` / `penfield_save_artifact` / `penfield_retrieve_artifact` | Artifact storage |
| `penfield_awaken`       | Load persona + persistent context briefing    |

### Standalone CLI

```bash
hermes-penfield status            # connection + memory count
hermes-penfield search <query>    # quick semantic search
hermes-penfield stats             # memory/relationship counts
hermes-penfield login             # OAuth device code flow
hermes-penfield logout            # clear cached tokens
```

## Architecture

```
hermes_penfield/
  __init__.py     # register() — plugin entry point
  constants.py    # ENDPOINTS, enums, host tables (single source of truth)
  config.py       # PenfieldConfig — env resolution, host switching
  auth.py         # PenfieldAuth — API-key exchange + OAuth device flow
  client.py       # PenfieldClient — stdlib HTTP, rate limit, backoff
  tools.py        # 13 tool schemas + dispatch (real endpoint mapping)
  provider.py     # PenfieldMemoryProvider — Hermes ABC adapter
  cli.py          # hermes penfield <subcommand>
```

The design is layered so that the low-level modules (`constants`,
`config`, `auth`, `client`, `tools`) are fully testable and grounded in
the verified Penfield API, independent of Hermes. The `provider` module
is the single Hermes-integration seam.

### Penfield awaken vs. Hermes SOUL.md

`penfield_awaken` **supplements** SOUL.md, it does not replace it. SOUL.md
defines the base persona (local, user-editable); awaken loads persistent,
learned context from Penfield (preferences, project history, custom
instructions). See [ADR-0008](docs/adr/0008-awaken-supplements-soul.md).

## Development

```bash
git clone https://github.com/penfieldlabs/hermes-penfield.git
cd hermes-penfield
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install

# unit tests (no network)
pytest -m "not integration"

# integration tests (need dev creds)
PENFIELD_API_KEY=... PENFIELD_ENV=dev pytest -m integration
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full quality bar (ruff +
mypy + pytest coverage gate) and [docs/adr/](docs/adr/) for the
architectural decisions.

## License

MIT. Copyright (C) 2026 Penfield.
