# ADR-0028: Refuse a tag Penfield does not store

- **Date:** 2026-11-21
- **Status:** Accepted. Extends [ADR-0027](0027-say-why-penfield-is-off.md).

## Context

Penfield stores a tag in lower case, with spaces and underscores as
hyphens and nothing but a-z, 0-9 and hyphens, and only if 2 to 50
characters are left. A tag outside that is not stored. The plugin sent
every tag the agent wrote, so a memory could come back as stored while
one of its tags was gone, and the agent had no way to know. That covers
more than long tags: a single letter, `c++`, and any tag written in
another script.

## Decision

- `penfield_store` and `penfield_update_memory` work out what Penfield
  would store for each tag and refuse the call, before anything is sent,
  if that is longer than 50 or shorter than 2 characters. The refusal
  names the tag by its position in the list as the agent wrote it. Tags
  written as one comma-separated text are counted without the empty
  parts.
- Other characters do not get a tag refused. Penfield removes them, so
  `C++ Tips` is sent and stored as `c-tips`, and the answer to the call
  shows the tags as stored.
- More than 10 tags are refused with their count. Penfield refuses them
  too, but in its own words and after the request was made.
- The limit counts the stored form. A tag is not refused for padding or
  for characters that are dropped anyway.
- A blank tag is left out. It is what a model sends when it means none.
- A tag is sent as the agent wrote it. Penfield does the rewriting.
- A filter by tag on a search is not checked: it is not a write, and
  Penfield reads it the same way it stores tags.
- Tags the plugin makes itself follow the rule too: a checkpoint's name
  is sent as a tag only where it can be stored as one.

## Consequences

- The agent learns about the tag while it can still correct it.
- The plugin repeats a rule that lives in Penfield. Live tests keep
  them together: stored tags must equal what the plugin predicts, the
  tags it refuses must be exactly the ones Penfield does not store, and
  Penfield must refuse an eleventh tag.
  If Penfield changes the rule, those tests fail first.
