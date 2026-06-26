# ADR-0013: Validate integration seams against real source, not guesses

- **Date:** 2026-06-26
- **Status:** Accepted
- **Supersedes:** ADR-0007 (original text)

## Context

ADR-0007 shipped with a false premise: that the Hermes `MemoryProvider`
ABC was not locatable. It was always public at
`github.com/NousResearch/hermes-agent`, with a developer guide and eight
reference implementations. Building the provider against a guessed
interface let four real contract mismatches ship — including a
`register()` signature that prevented the plugin from loading at all.

The unit test suite passed throughout because every test mocked the
seam. "100% of tests green" described nothing about whether the plugin
would actually integrate.

## Decision

**Integration seams must be validated against real upstream source before
they're claimed as "verified," not after.** Concretely, for any
third-party interface this plugin depends on (the Hermes ABC, the
Penfield API):

1. Clone the real source. Read it. If it has reference implementations,
   read those too.
2. Diff the integration point line-by-line against what we wrote.
3. Pin the contract in a test that exercises the real shape (a mock that
   mirrors what the real caller does — e.g. `register(ctx)` with a fake
   `PluginContext`, not a signature we invented).
4. If upstream source genuinely cannot be located, that's a reason to
   stop and say so out loud — not to write an ADR that converts "I
   didn't look" into "the source doesn't exist."

## Consequences

- ADR-0007's false premise is now corrected in-place; the original text
  is preserved there so the error is visible, not buried.
- `tests/test_plugin_contract.py` exists as the structural enforcement:
  it asserts `register(ctx)` accepts one arg, returns None, and calls
  `register_memory_provider`. A future regression to the v0.1.0
  signature fails this test before it can ship.
- "Verified" now means "diffed against real source," not "tests pass
  against my own assumptions." The word is cheaper than the work; the
  work is the bar.
- This ADR is the one to cite when reviewing future claims of
  "integration complete." If there's no diff against real source, it's
  not complete.
