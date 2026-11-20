# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Built-in memory writes mirrored into Penfield through Hermes's MemoryManager."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.support.fake_penfield import FakePenfield

from .conftest import ISOLATION_MODES, run_driver


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_opted_in_writes_are_applied_in_order(
    isolation: str, fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    home = make_home(
        api_url=fake_api.url, isolation=isolation, settings={"mirror_builtin_writes": True}
    )

    report = run_driver(home, "mirror")

    failed = {name: s["error"] for name, s in report["steps"].items() if not s["ok"]}
    assert failed == {}
    assert report["swallowed_failures"] == []
    # One note was added then edited, the other added then removed.
    (memory,) = fake_api.memories.values()
    assert memory["content"] == "User prefers short replies with examples."
    assert set(memory["tags"]) == {"hermes-builtin", "hermes-user"}


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_nothing_is_written_by_default(
    isolation: str, fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    home = make_home(api_url=fake_api.url, isolation=isolation)

    report = run_driver(home, "mirror")

    assert fake_api.memories == {}, "no mirror and no checkpoint unless switched on"
    assert report["steps"]["on_pre_compress"]["value"] == ""
