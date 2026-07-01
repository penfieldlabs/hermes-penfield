# hermes-penfield

**Penfield persistent memory provider for Hermes Agent.**

Connects Hermes Agent to the Penfield memory API. Exposes 16 tools
mirroring the Penfield MCP surface. Stdlib-only HTTP, OAuth device-code
+ API-key authentication, dev/prod environment switching.

Penfield gives Hermes cross-session, cross-agent memory with hybrid
search (BM25 + vector + graph), typed connections (24 relationship
types), and artifact storage.

---

## Requirements

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) installed
  and runnable (`hermes` on your PATH).
- A Penfield account and API key — create one at
  [portal.penfield.app](https://portal.penfield.app).

## Install

```
hermes plugins install penfieldlabs/hermes-penfield --enable
```

That's it. Hermes clones the repo, drops it into `~/.hermes/plugins/penfield/`,
and enables it. The plugin is self-contained — no pip install needed.

## Configure

Set `memory.provider: penfield` in `~/.hermes/config.yaml`:

```bash
hermes config set memory.provider penfield
```

Then authenticate:

```bash
hermes penfield login
```

Or set an API key directly in `~/.hermes/.env`:

```
PENFIELD_API_KEY=tm_pf_yourtenant_ak_yourkey
PENFIELD_ENV=prod
```

## Use

```
hermes
```

The persona and persistent context load automatically on every session.
The 16 tools are available to the agent:

| Tool | What it does |
| ---- | ------------ |
| `penfield_awaken` | Load persona + persistent context briefing |
| `penfield_store` | Store a memory (auto-detected type) |
| `penfield_recall` | Hybrid search (BM25 + vector + graph) |
| `penfield_search` | Semantic search for fuzzy concept matching |
| `penfield_fetch` | Get a memory by ID |
| `penfield_update_memory` | Update a memory |
| `penfield_connect` | Create a relationship between memories |
| `penfield_disconnect` | Remove a relationship between memories |
| `penfield_explore` | Traverse the knowledge graph from a memory |
| `penfield_reflect` | Analyze memory patterns over a time window |
| `penfield_save_artifact` | Store a file (not searchable via recall) |
| `penfield_retrieve_artifact` | Get a stored file by path |
| `penfield_list_artifacts` | List files under a directory prefix |
| `penfield_delete_artifact` | Delete a stored file by path |
| `penfield_list_contexts` | List saved context checkpoints |
| `penfield_restore_context` | Restore a checkpoint by name |

> **Note:** `save_context` (creating new checkpoints) is held for a
> future version. See [ADR-0011](docs/adr/0011-context-tools-partial-implementation.md).

## Development

```bash
git clone https://github.com/penfieldlabs/hermes-penfield.git
cd hermes-penfield
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -m "not integration"
```

## License

MIT. Copyright (C) 2026 Penfield.
