# save_context — MCP server algorithm (verified from source)

## memory_ids selection

NOT time-windowed. NOT session-scoped. NOT a fixed count.

1. Take first 200 chars of description as search query
2. Call hybrid search (BM25 + vector + graph expansion) with limit=30
3. Filter results to score >= 0.25 (min_score_threshold)
4. Merge with explicitly referenced memories (UUIDs + phrase-matched)
5. Deduplicate

The variable count (7, 10, 12 observed) is because different descriptions
match different numbers of memories above the 0.25 threshold.

Fallback to limit=20 recent memories only fires if hybrid search fails
(404, 400, timeout) — it's error handling, not the primary path.

## checkpoint memory structure

- memory_type: checkpoint
- source_type: direct_input
- importance: 0.9
- tags: ["checkpoint", "context", <name>]
- content: JSON string with keys:
  - checkpoint_name
  - description
  - memory_count (number of memories in memory_ids)
  - memory_ids (array of UUIDs from the search above)
  - referenced_memories (array of UUIDs resolved from description refs)

## reference parsing

- `memory_id: UUID` → direct lookup, always resolves
- `memory: 'phrase'` → hybrid search, best-effort (may resolve differently per call)

## name uniqueness

Checked client-side before creation: query existing checkpoints, scan
content JSON for exact checkpoint_name match. Reject if found.
