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

Every change must pass:

```bash
ruff check .          # lint (zero warnings)
ruff format --check . # formatting
mypy hermes_penfield/ # types
pytest -m "not integration"   # unit tests + 80% coverage gate
```

Integration tests (`-m integration`) hit the live dev API and need
`PENFIELD_API_KEY` + `PENFIELD_ENV=dev`. They are skipped without creds
and are run before every release. See
[ADR-0011](docs/adr/0011-integration-tests-hit-live-dev-api.md).

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
the provider layer is grounded in the verified Penfield API. See
[ADR-0007](docs/adr/0007-provider-duck-types-hermes-abc.md). When real
Hermes source is available, reconciling `provider.py` is the priority
integration task.

## Secrets Policy

- No secrets in the repo — ever. No API keys, tokens, or PATs.
- All credentials come from `.env` (gitignored) or environment variables.
- See [SECURITY.md](SECURITY.md) and
  [ADR-0009](docs/adr/0009-secret-shape-rejection.md).
