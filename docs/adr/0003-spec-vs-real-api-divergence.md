# ADR-0003: Spec vs. real API divergence — build to the real contract

- **Date:** 2026-06-26
- **Status:** Accepted

## Context

The v1.0 implementation spec listed tool-to-endpoint mappings that do
not match the real Penfield v2 API. Before writing client code we cloned
the authoritative docs (`github.com/penfieldlabs/docs`) and exercised the
live `api-dev.penfield.app` contract. The divergence is substantial:

| Spec said                  | Real API                                   |
| -------------------------- | ------------------------------------------ |
| `POST /store`              | `POST /api/v2/memories`                    |
| `POST /recall`             | `POST /api/v2/search/hybrid`               |
| `POST /search`             | `POST /api/v2/search/hybrid` (same)        |
| `POST /connect`            | `POST /api/v2/relationships`               |
| `POST /explore`            | `POST /api/v2/relationships/traverse`      |
| `POST /reflect`            | `POST /api/v2/analysis/reflect`            |
| `POST /awaken`             | `GET /api/v2/personality/awakening`        |
| `PATCH /memories/{id}`     | `PUT /api/v2/memories/{id}`                |
| `GET /artifacts/{id}`      | `GET /api/v2/artifacts?path=...` (path-keyed) |
| `POST /turns/sync`         | **does not exist**                         |
| `POST /context/save`       | **does not exist**                         |

## Decision

Build to the **real, verified contract**. All endpoint paths live in
`hermes_penfield/constants.ENDPOINTS` as the single source of truth. The
tool names (`penfield_store`, `penfield_recall`, ...) are preserved for
agent-facing stability, but each maps to its real endpoint via
`constants.ENDPOINTS` and `tools.py`. The two non-existent lifecycle
endpoints are handled per ADR-0005.

## Consequences

- The plugin works against the real API, not a fiction.
- `constants.ENDPOINTS` is the one place to change when an endpoint
  moves — client, tools, and tests all read from it.
- Anyone comparing this code to the v1.0 spec will see the divergence;
  this ADR (and the `constants` docstrings) explain it.
- The integration test suite (`-m integration`) is the living proof the
  mapping is correct; it runs against `api-dev` and would break loudly
  if the contract drifts.
