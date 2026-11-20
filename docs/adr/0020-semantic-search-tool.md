# ADR-0020: penfield_search ranks by meaning

- **Date:** 2026-11-19
- **Status:** Accepted

## Context

Penfield offers two search tools. Its documentation describes `recall` as
hybrid search and `search` as semantic search with a higher vector weight,
for finding a memory without knowing its wording. Both use the same REST
endpoint, `POST /search/hybrid`, which scores a result as a weighted sum of
a keyword score, a vector similarity and a graph score. The default
weights are 0.4, 0.4 and 0.2, and a request may set its own.

With the default weights the best keyword hit of a search always takes the
full keyword share, so a memory that shares words with the query outranks
one that only shares its meaning.

## Decision

- `penfield_recall` leaves the weights to the API.
- `penfield_search` sends `bm25_weight` 0.1, `vector_weight` 0.7 and
  `graph_weight` 0.2.
- Auto-recall before a turn uses the API's default weights.

## Consequences

- The two tools return different rankings for the same query, which is
  the reason to have both.
- `penfield_search` is the weaker tool for names, ids and exact phrases.
  The tool descriptions say which one to use for what.
- The weights are this plugin's choice. Nothing in the API fixes them, and
  another Penfield client may weigh its search differently.
- Scores from the two tools are not comparable with each other.
