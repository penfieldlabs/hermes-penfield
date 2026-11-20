# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Hermes can discover, load and recognise the provider in both plugin modes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from penfield.tools import PENFIELD_TOOL_SCHEMAS

from tests.support.fake_penfield import FakePenfield

from .conftest import ISOLATION_MODES, run_driver


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_hermes_loads_the_provider(
    isolation: str, fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    home = make_home(api_url=fake_api.url, isolation=isolation)

    report = run_driver(home, "load")

    assert report["isolation"] == isolation
    assert report["discovered"], "Hermes did not list the provider"
    assert report["loaded"], f"Hermes could not load the provider: {report['warnings']}"
    assert report["is_memory_provider"], "provider is not a MemoryProvider instance"
    assert report["missing_members"] == []
    assert report["available"]
    assert report["tool_names"] == sorted(s["name"] for s in PENFIELD_TOOL_SCHEMAS)
