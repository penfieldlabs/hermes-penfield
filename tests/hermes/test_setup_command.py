# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""``hermes memory setup penfield`` typed into a real terminal.

This is the command Hermes prints after installing the plugin. With a
terminal it runs the plugin's own setup hook; under the plugin host there
is no terminal for the plugin to ask on and Hermes keeps to its own steps.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.support.fake_penfield import FakePenfield

from .conftest import TEST_API_KEY, run_in_terminal

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pseudo-terminal")

KEY = "tm_pf_setup_ak_typed"
ASKS_FOR_THE_KEY = "Penfield API key"


def _unselected(home: Path) -> Path:
    """Leave the plugin installed but not chosen as the memory provider."""
    config = home / "config.yaml"
    kept = config.read_text(encoding="utf-8").replace("memory:\n  provider: penfield\n", "")
    config.write_text(kept, encoding="utf-8")
    return home


def _exchanged_keys(api: FakePenfield) -> list[str]:
    return [c.authorization for c in api.calls() if c.path == "/auth/token"]


def test_it_asks_for_the_key_checks_it_and_saves_it(
    fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    home = _unselected(make_home(api_url=fake_api.url, api_key=""))

    result = run_in_terminal(
        home, ["memory", "setup", "penfield"], [(ASKS_FOR_THE_KEY, f"{KEY}\r")]
    )

    shown = result["shown"]
    assert result["exit"] == 0, shown
    assert result["typed"] == 1, shown
    assert "API key accepted by Penfield." in shown
    assert "Start a new session to activate." in shown
    assert KEY not in shown, "the key must be masked as it is typed"
    assert _exchanged_keys(fake_api) == [f"Bearer {KEY}"]
    assert f"PENFIELD_API_KEY={KEY}" in (home / ".env").read_text(encoding="utf-8")
    assert "provider: penfield" in (home / "config.yaml").read_text(encoding="utf-8")


def test_a_key_penfield_refuses_is_not_saved(
    fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    fake_api.rejected_api_keys.add(KEY)
    home = _unselected(make_home(api_url=fake_api.url, api_key=""))

    result = run_in_terminal(
        home, ["memory", "setup", "penfield"], [(ASKS_FOR_THE_KEY, f"{KEY}\r")]
    )

    shown = result["shown"]
    assert result["exit"] == 0, shown
    assert "Penfield did not accept this API key. Nothing was changed." in shown
    assert KEY not in (home / ".env").read_text(encoding="utf-8")
    assert "provider: penfield" not in (home / "config.yaml").read_text(encoding="utf-8")


def test_an_empty_answer_keeps_the_key_already_there(
    fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    home = _unselected(make_home(api_url=fake_api.url))
    before = (home / ".env").read_text(encoding="utf-8")

    result = run_in_terminal(home, ["memory", "setup", "penfield"], [("blank to keep", "\r")])

    shown = result["shown"]
    assert result["exit"] == 0, shown
    assert result["typed"] == 1, shown
    assert "Current API key accepted by Penfield." in shown
    assert TEST_API_KEY not in shown
    assert (home / ".env").read_text(encoding="utf-8") == before
    assert "provider: penfield" in (home / "config.yaml").read_text(encoding="utf-8")


def test_under_the_plugin_host_hermes_keeps_to_its_own_steps(
    fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    """The plugin runs in a process with no terminal, so it leaves setup to Hermes."""
    home = _unselected(make_home(api_url=fake_api.url, isolation="host", api_key=""))

    result = run_in_terminal(
        home, ["memory", "setup", "penfield"], [(ASKS_FOR_THE_KEY, f"{KEY}\r")]
    )

    shown = result["shown"]
    assert result["exit"] == 0, shown
    assert "Traceback" not in shown
    assert "Memory provider: penfield" in shown
    assert "provider: penfield" in (home / "config.yaml").read_text(encoding="utf-8")
