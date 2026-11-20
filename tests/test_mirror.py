# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Copying Hermes's built-in memory writes into Penfield."""

from __future__ import annotations

import logging
import time

import pytest
from penfield.mirror import MIRROR_TAG
from penfield.provider import PenfieldMemoryProvider

from tests.support.fake_penfield import FakePenfield

NOTE = "User prefers short replies."
EDITED = "User prefers short replies with examples."


@pytest.fixture
def mirroring(provider: PenfieldMemoryProvider) -> PenfieldMemoryProvider:
    """A provider with the mirror switched on."""
    assert provider._config is not None
    provider._config.mirror_builtin_writes = True
    return provider


def settle(provider: PenfieldMemoryProvider) -> None:
    """Wait for queued mirror writes, as the end of a session does."""
    provider.on_session_end([])


def mirrored(api: FakePenfield) -> list[dict[str, object]]:
    return [m for m in api.memories.values() if MIRROR_TAG in m["tags"]]


class TestOptIn:
    def test_off_by_default(self, api: FakePenfield, provider: PenfieldMemoryProvider) -> None:
        provider.on_memory_write("add", "memory", NOTE)
        settle(provider)
        assert api.calls("POST", "/memories") == []

    def test_only_the_primary_agent_is_mirrored(
        self, api: FakePenfield, mirroring: PenfieldMemoryProvider
    ) -> None:
        mirroring._agent_context = "subagent"
        mirroring.on_memory_write("add", "memory", NOTE)
        settle(mirroring)
        assert mirrored(api) == []


class TestAdd:
    def test_stores_the_entry_tagged_by_store(
        self, api: FakePenfield, mirroring: PenfieldMemoryProvider
    ) -> None:
        mirroring.on_memory_write("add", "user", NOTE, metadata={"write_origin": "memory_tool"})
        settle(mirroring)

        (memory,) = mirrored(api)
        assert memory["content"] == NOTE
        assert set(memory["tags"]) == {MIRROR_TAG, "hermes-user"}

    def test_leaves_the_memory_type_to_penfield(
        self, api: FakePenfield, mirroring: PenfieldMemoryProvider
    ) -> None:
        mirroring.on_memory_write("add", "memory", NOTE)
        settle(mirroring)

        (request,) = api.calls("POST", "/memories")
        assert "memory_type" not in request.body

    def test_blank_entries_are_skipped(
        self, api: FakePenfield, mirroring: PenfieldMemoryProvider
    ) -> None:
        mirroring.on_memory_write("add", "memory", "   ")
        settle(mirroring)
        assert api.calls("POST", "/memories") == []


class TestReplace:
    def test_edits_the_mirrored_memory_in_place(
        self, api: FakePenfield, mirroring: PenfieldMemoryProvider
    ) -> None:
        mirroring.on_memory_write("add", "memory", NOTE)
        mirroring.on_memory_write("replace", "memory", EDITED, metadata={"previous_content": NOTE})
        settle(mirroring)

        (memory,) = mirrored(api)
        assert memory["content"] == EDITED

    def test_finds_a_memory_mirrored_in_an_earlier_session(
        self, api: FakePenfield, mirroring: PenfieldMemoryProvider
    ) -> None:
        memory_id = api.seed_memory(NOTE, tags=[MIRROR_TAG, "hermes-memory"])

        mirroring.on_memory_write("replace", "memory", EDITED, metadata={"previous_content": NOTE})
        settle(mirroring)

        assert api.memories[memory_id]["content"] == EDITED
        assert len(mirrored(api)) == 1

    def test_stores_a_new_memory_when_the_old_one_was_never_mirrored(
        self, api: FakePenfield, mirroring: PenfieldMemoryProvider
    ) -> None:
        mirroring.on_memory_write("replace", "memory", EDITED, metadata={"previous_content": NOTE})
        settle(mirroring)

        (memory,) = mirrored(api)
        assert memory["content"] == EDITED

    def test_never_edits_a_memory_the_mirror_did_not_write(
        self, api: FakePenfield, mirroring: PenfieldMemoryProvider
    ) -> None:
        own = api.seed_memory(NOTE, tags=["preferences"])

        mirroring.on_memory_write("replace", "memory", EDITED, metadata={"previous_content": NOTE})
        settle(mirroring)

        assert api.memories[own]["content"] == NOTE


class TestRemove:
    def test_deletes_the_mirrored_memory(
        self, api: FakePenfield, mirroring: PenfieldMemoryProvider
    ) -> None:
        mirroring.on_memory_write("add", "memory", NOTE)
        mirroring.on_memory_write("remove", "memory", "", metadata={"previous_content": NOTE})
        settle(mirroring)
        assert mirrored(api) == []

    def test_never_deletes_a_memory_the_mirror_did_not_write(
        self, api: FakePenfield, mirroring: PenfieldMemoryProvider
    ) -> None:
        own = api.seed_memory(NOTE, tags=["preferences"])

        mirroring.on_memory_write("remove", "memory", "", metadata={"previous_content": NOTE})
        settle(mirroring)

        assert own in api.memories

    def test_keeps_the_memory_when_the_key_may_not_delete(
        self,
        api: FakePenfield,
        mirroring: PenfieldMemoryProvider,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        api.allow_memory_delete = False
        caplog.set_level(logging.INFO, logger="penfield.mirror")
        mirroring.on_memory_write("add", "memory", NOTE)
        mirroring.on_memory_write("remove", "memory", "", metadata={"previous_content": NOTE})
        settle(mirroring)

        assert len(mirrored(api)) == 1
        assert "not allowed to delete" in caplog.text


class TestFailures:
    def test_an_api_failure_is_logged_not_raised(
        self,
        api: FakePenfield,
        mirroring: PenfieldMemoryProvider,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        api.fail_next[("POST", "/memories")] = [500]
        mirroring.on_memory_write("add", "memory", NOTE)
        settle(mirroring)

        assert mirrored(api) == []
        assert "could not mirror a built-in memory add" in caplog.text

    def test_a_failed_write_does_not_stop_later_ones(
        self, api: FakePenfield, mirroring: PenfieldMemoryProvider
    ) -> None:
        api.fail_next[("POST", "/memories")] = [500]
        mirroring.on_memory_write("add", "memory", NOTE)
        mirroring.on_memory_write("add", "memory", "Second note.")
        settle(mirroring)

        assert [m["content"] for m in mirrored(api)] == ["Second note."]

    def test_the_memory_tool_is_not_held_up_by_a_slow_api(
        self, api: FakePenfield, mirroring: PenfieldMemoryProvider
    ) -> None:
        api.outage = "hang"
        started = time.monotonic()
        mirroring.on_memory_write("add", "memory", NOTE)
        assert time.monotonic() - started < 0.5
