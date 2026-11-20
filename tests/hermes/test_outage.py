# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""An unreachable Penfield API must not stall a Hermes session.

Session start (building the system prompt) and every turn (auto-recall) call
the provider synchronously, so each has a hard time budget whatever the API
is doing: answering errors, not answering at all, or gone entirely.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tests.support.fake_penfield import Blackhole, FakePenfield, refused_url

from .conftest import ISOLATION_MODES, run_driver

#: Longest the provider may hold up session start or a turn, in seconds.
#: Longest each hook may hold Hermes up while the API is down: the plugin's
#: own deadline plus a little for process and thread overhead.
BUDGET_SECONDS = {"system_prompt": 6.5, "prefetch": 3.0, "on_pre_compress": 8.5}


@pytest.fixture
def api_url(request: pytest.FixtureRequest) -> Iterator[str]:
    kind = request.param
    if kind == "refused":
        yield refused_url()
    elif kind == "blackhole":
        with Blackhole() as hole:
            yield hole.url
    else:
        with FakePenfield() as fake:
            fake.outage = kind
            yield fake.url


@pytest.mark.parametrize(
    ("api_url", "isolation"),
    [
        ("errors", "in_process"),
        ("hang", "in_process"),
        ("hang", "host"),
        ("refused", "in_process"),
        ("blackhole", "in_process"),
    ],
    indirect=["api_url"],
)
def test_session_keeps_moving_while_the_api_is_down(
    api_url: str, isolation: str, make_home: Callable[..., Path]
) -> None:
    home = make_home(api_url=api_url, isolation=isolation, settings={"pre_compress_save": True})

    report = run_driver(home, "outage", timeout=600)

    assert report["resolved"], "the provider must still load when the API is down"
    steps = report["steps"]
    assert all(steps[name]["ok"] for name in BUDGET_SECONDS)
    assert "penfield" in steps["system_prompt"]["value"].lower(), "tool guidance must survive"
    assert steps["prefetch"]["value"] == ""
    assert steps["on_pre_compress"]["value"] == ""
    for name, budget in BUDGET_SECONDS.items():
        assert steps[name]["seconds"] < budget, (
            f"{name} held Hermes up for {steps[name]['seconds']}s"
        )


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_one_failed_sign_in_at_session_start_does_not_cost_the_briefing(
    isolation: str, fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    """Hermes keeps the first prompt it builds, so a briefing missed here is missed all session."""
    fake_api.fail_next[("POST", "/auth/token")] = [503]
    home = make_home(api_url=fake_api.url, isolation=isolation)

    report = run_driver(home, "outage", timeout=600)

    assert fake_api.briefing in report["steps"]["system_prompt"]["value"]
    assert [c.path for c in fake_api.calls()].count("/auth/token") == 2
    assert not any("briefing" in line for line in report["warnings"])


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_a_session_that_starts_without_its_briefing_says_so_in_the_log(
    isolation: str, make_home: Callable[..., Path]
) -> None:
    with FakePenfield() as fake:
        fake.outage = "errors"
        home = make_home(api_url=fake.url, isolation=isolation)

        report = run_driver(home, "outage", timeout=600)

    said = [line for line in report["warnings"] if "briefing was not loaded" in line]
    assert len(said) == 1, report["warnings"]
