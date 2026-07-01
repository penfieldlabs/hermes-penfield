# Contributing to hermes-penfield

hermes-penfield is the Penfield persistent memory provider for Hermes
Agent, licensed under MIT. Contributions are welcome.

## Development Setup

```bash
git clone https://github.com/penfieldlabs/hermes-penfield.git
cd hermes-penfield
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

## Quality Bar

Every change must pass the CI checks, which are structured as:

| Job            | What it enforces                          | Scope        |
| -------------- | ------------------------------------------ | ------------ |
| `lint`         | `ruff check` + `ruff format --check` + `mypy` | Runs once, fast, blocks test/integration |
| `test`         | `pytest -m "not integration"` + **80% coverage gate** | Matrix: Python 3.10–3.13 |
| `integration`  | Live round-trip vs `api-dev.penfield.app` | Dev-only, needs `PENFIELD_DEV_API_KEY` secret + `RUN_INTEGRATION=true` var |
| `version-tag-guard` | Tag must match `pyproject.toml` version | Tags only |

Lint runs as a standalone job (not duplicated across the version matrix)
so a lint failure is fast feedback, not buried in test output. `test` and
`integration` both depend on `lint` passing.

### Required status checks (branch protection)

Configure these as required status checks on `main` and `dev`:

- `lint`
- `test (3.10)` / `test (3.11)` / `test (3.12)` / `test (3.13)`

(`integration` is optional — it only runs when `RUN_INTEGRATION=true`.)

### Local checks before push

```bash
ruff check .          # lint (zero warnings)
ruff format --check . # formatting
mypy auth.py client.py config.py constants.py exceptions.py provider.py tools.py cli.py __init__.py # types
pytest -m "not integration"   # unit tests + 80% coverage gate
```

Integration tests (`-m integration`) hit the live dev API and need
`PENFIELD_API_KEY` + `PENFIELD_ENV=dev`. They are skipped without creds
and are run before every release. See

## Coding Standards

- Python 3.10+. Use `from __future__ import annotations`.
- Type hints on all signatures. `Any` is acceptable for JSON-passthrough
  values (the client/tools layer); avoid it elsewhere.
- Google-style docstrings on public classes and functions.
- Ruff enforces lint + format. Zero warnings before commit.
- Every source file starts with the MIT license header and copyright.

## Architecture Decisions

Non-obvious decisions are recorded as ADRs in
[docs/adr/](docs/adr/README.md). Read them before asking "why is it this
way?" — and add one before merge for any new non-trivial decision.

## The One Unverified Seam

The Hermes `MemoryProvider` ABC is duck-typed, not subclassed, because no
canonical Hermes source was verifiable during v0.1.0. Everything below
the provider layer is verified against the real Hermes MemoryProvider
ABC (github.com/NousResearch/hermes-agent). See
[ADR-0010](docs/adr/0010-directory-discovery-not-entry-points.md).

## Secrets Policy

- No secrets in the repo — ever. No API keys, tokens, or PATs.
- All credentials come from `.env` (gitignored) or environment variables.
- See [SECURITY.md](SECURITY.md) and
  [ADR-0007](docs/adr/0007-secret-shape-rejection.md).
