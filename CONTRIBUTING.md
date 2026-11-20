# Contributing to hermes-penfield

hermes-penfield is the Penfield memory provider for Hermes Agent, licensed
under MIT. Contributions are welcome.

## Layout

The repository root is the plugin. Hermes installs a plugin by copying
its repository into the profile's `plugins/` directory, so the Python
modules sit at the top level and there is no package to build.

| Path | Contents |
| ---- | -------- |
| `provider.py` | The Hermes `MemoryProvider`: session hooks, auto-recall, checkpoints. |
| `tools.py` | The `penfield_*` tool schemas and their handlers. |
| `client.py`, `auth.py` | HTTP client and token handling. Standard library only. |
| `config.py`, `constants.py` | Settings, hosts, endpoints, limits. |
| `mirror.py` | Optional copy of Hermes's built-in memory writes. |
| `cli.py` | `hermes penfield ...` commands. |
| `setup_wizard.py` | What `hermes memory setup penfield` asks and saves. |
| `plugin.yaml` | The manifest Hermes reads. |
| `tests/` | Unit tests, which run against a local fake of the Penfield API. |
| `tests/hermes/` | Integration tests, which run the plugin inside a real Hermes Agent. |
| `docs/adr/` | Architecture decision records. |

There is no `pyproject.toml`, and there must not be one: Hermes reads a
`pyproject.toml` in a plugin's directory as a declaration of Python
dependencies and asks users to approve an install step the plugin does
not need. Each tool has its own file: `ruff.toml`, `mypy.ini`,
`pytest.ini` and `.coveragerc`.

## Setup

```bash
git clone https://github.com/penfieldlabs/hermes-penfield.git
cd hermes-penfield
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pre-commit install
```

Python 3.11 or newer.

## Checks

```bash
make check        # ruff, mypy and the unit tests with the coverage gate
```

The unit tests need no network and no Hermes install. They talk to
`tests/support/fake_penfield.py`, an in-memory Penfield API served on
loopback, so the real HTTP client, token handling and error paths are
exercised.

### Integration tests inside Hermes Agent

These load the plugin through Hermes's own plugin loader and
`MemoryManager`, in both plugin isolation modes, and run Hermes's
validator and installer against it. Hermes Agent needs Python 3.14 and is
installed from a checkout:

```bash
git clone https://github.com/NousResearch/hermes-agent.git
uv venv --python 3.14 .venv-hermes && source .venv-hermes/bin/activate
(cd hermes-agent && uv pip install -e .)   # from inside the checkout
uv pip install -r requirements-dev.txt
make test-hermes
```

CI runs them against Hermes 0.21.6, the oldest supported release, against
0.22.7 and 0.23.0, and against Hermes's `main` branch. It repeats the run
weekly so a change in Hermes shows up within days.

### Tests against the live Penfield API

`pytest -m integration --no-cov` runs every tool and the session hooks
against the real API, and checks that the API still behaves the way the
local fake does. It needs `PENFIELD_ENV=dev` and a dev `PENFIELD_API_KEY`
and is skipped without them. The tests create memories, tagged
`hermes-penfield-int-*`, and delete them afterwards; see the module
docstring for keys without the delete permission. To run against
production, set `PENFIELD_ENV=prod` and `PENFIELD_TEST_ALLOW_PROD=1`. In
CI the dev run happens only where the repository variable
`RUN_INTEGRATION` is `true`.

When the live tests and the unit tests disagree, the fake is wrong. Fix
`tests/support/fake_penfield.py` to answer the way the API does, then the
plugin.

## CI jobs

| Job | What it runs |
| --- | ------------ |
| `lint` | `make lint` and `make typecheck` |
| `unit` | `make test` on Python 3.11, 3.12, 3.13 and 3.14, with an 80% coverage gate on the plugin's own code |
| `hermes` | `make test-hermes` on the Hermes floor release and on Hermes `main` |
| `live` | The live API tests, where enabled |
| `version-tag-guard` | On a tag: the tag must equal the version in `plugin.yaml` |

## Keeping the tool surface in step with Penfield

`tests/data/penfield_mcp_tools.json` is a snapshot of the tool list
Penfield's MCP server reports. `tests/test_surface.py` fails when the
plugin's tools and that snapshot disagree on a tool name, a parameter, a
type, a default or a required field. A parameter the REST API does not
act on is left out of the plugin and listed, with the reason, in
`NOT_AVAILABLE_OVER_REST` in that test.

To refresh the snapshot, connect any MCP client to
`https://mcp.penfield.app`, take the `tools` array of its `tools/list`
response, drop each tool's `description`, and replace the `tools` array
in the file. Update `captured` and, if a description lists new values for
a parameter, `documented_values`. Then make the tests pass.

## Coding standards

- `from __future__ import annotations` in every module.
- Type hints on all signatures. `Any` is acceptable for JSON passed
  through from the API; avoid it elsewhere.
- Google-style docstrings on public classes and functions.
- Standard library only at runtime.
- Import Hermes only through its public plugin API (`agent.memory_provider`,
  `agent.secret_scope`, `hermes_constants`). `hermes plugins validate`
  reports anything else.
- Every source file starts with the MIT license header.

## Architecture decisions

Decisions that are not obvious from the code are recorded in
[docs/adr](docs/adr/README.md). Add one with any change that makes a new
decision of that kind, and supersede the old record instead of editing its
decision.

## Releasing

1. Set the new version in `plugin.yaml` and in `constants.py` (`VERSION`).
   A test keeps the two equal.
2. Add the release to `CHANGELOG.md`.
3. Tag the merge commit `v<version>`.

## Secrets

No API keys, tokens or personal data in the repository, in tests or in
issue reports. Tests use made-up keys against the local fake API. See
[SECURITY.md](SECURITY.md).
