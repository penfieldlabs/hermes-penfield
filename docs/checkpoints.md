# Context checkpoints

How `penfield_save_context`, `penfield_restore_context` and
`penfield_list_contexts` work. The Penfield REST API has no checkpoint
endpoints, so the plugin builds checkpoints from memories and search, in
the format Penfield's MCP server uses. A checkpoint saved here restores in
any other Penfield client. See [ADR-0011](adr/0011-context-tools.md).

## What a checkpoint is

A memory with:

- `memory_type`: `checkpoint`
- `importance`: 0.9
- `tags`: `checkpoint`, `context`, the checkpoint name, and
  `auto-checkpoint` when it was saved before a compression
- `content`: a JSON document

```json
{
  "checkpoint_name": "API investigation",
  "description": "Found the auth bug. Next: test the fix.",
  "memory_count": 8,
  "memory_ids": ["..."],
  "referenced_memories": ["..."]
}
```

## Saving

1. **Name.** A name used by any existing checkpoint is refused with
   `DUPLICATE_NAME`.
2. **Linked memories.** The first 200 characters of the description are
   run as a hybrid search, limit 30. Results scoring 0.25 or more become
   `memory_ids`. If the description has fewer than three words, or the
   search fails, the 20 most recent memories are linked instead.
3. **References.** The description may point at specific memories:

   | Form | Resolution |
   | ---- | ---------- |
   | `memory_id: <uuid>` | Direct lookup. Reliable. |
   | `memory: 'a phrase'` | Top search result scoring 0.25 or more. Best effort. |

   Resolved references not already linked become `referenced_memories`.
   The result lists the rest under `unresolved_references`.
4. **Store.** The checkpoint memory is created. This request is not
   retried, so a slow response cannot create two.

## Restoring

`penfield_restore_context(name, limit=20)` finds the checkpoint by exact,
case-sensitive name, or by its id, and fetches the linked and referenced
memories in full, up to `limit`. Memories deleted since are left out.
`saved_count` is how many the checkpoint holds and `truncated` says
whether `limit` cut the list. The name `awakening` returns the briefing.

## Listing

`penfield_list_contexts` reads every checkpoint, 100 per request, newest
first, then applies `name_pattern` (case-insensitive substring), `offset`
and `limit`. `total` counts all matches.
