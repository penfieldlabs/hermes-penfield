# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""``hermes memory setup penfield`` typed into a real terminal.

This is the command Hermes prints after installing the plugin. With a
terminal it runs the plugin's own setup hook, which offers a browser
sign-in first and an API key second. Under the plugin host the plugin runs
in another process and reaches the same terminal. Without any terminal
Hermes keeps to its own steps.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.support.fake_penfield import FakePenfield

from .conftest import ISOLATION_MODES, TEST_API_KEY, child_env, hermes_cli, run_in_terminal

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pseudo-terminal")

KEY = "tm_pf_setup_ak_typed"
ASKS_HOW_TO_CONNECT = "Choice [1]"
ASKS_FOR_THE_KEY = "Penfield API key"
SETUP = ["memory", "setup", "penfield"]


def _unselected(home: Path) -> Path:
    """Leave the plugin installed but not chosen as the memory provider."""
    config = home / "config.yaml"
    kept = config.read_text(encoding="utf-8").replace("memory:\n  provider: penfield\n", "")
    config.write_text(kept, encoding="utf-8")
    return home


def _log(home: Path) -> str:
    log = home / "logs" / "agent.log"
    return log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""


def _exchanged_keys(api: FakePenfield) -> list[str]:
    return [c.authorization for c in api.calls() if c.path == "/auth/token"]


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_enter_signs_in_with_the_browser(
    isolation: str, fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    """The default answer is the browser sign-in, in-process and in the plugin host."""
    fake_api.approve_sign_ins = True
    home = _unselected(make_home(api_url=fake_api.url, isolation=isolation, api_key=""))

    result = run_in_terminal(home, SETUP, [(ASKS_HOW_TO_CONNECT, "\r")])

    shown = result["shown"]
    assert result["exit"] == 0, shown
    assert result["typed"] == 1, shown
    assert "1. Sign in with your browser (recommended)" in shown
    assert f"To authorize, visit: {fake_api.root}/device?user_code=TEST-0001" in shown
    assert "Signed in. This login may read, write and delete." in shown
    assert "Start a new session to activate." in shown
    saved = json.loads((home / "penfield" / "tokens.json").read_text(encoding="utf-8"))
    assert saved["source"] == "oauth"
    assert "PENFIELD_API_KEY=tm_" not in (home / ".env").read_text(encoding="utf-8")
    assert "provider: penfield" in (home / "config.yaml").read_text(encoding="utf-8")
    assert saved["access_token"] not in shown + _log(home)


def _saved_sign_in(api: FakePenfield, home: Path) -> dict[str, Any]:
    """Leave a finished browser sign-in in the profile and return what was saved."""
    issued = api.sign_in()
    saved = {
        "access_token": issued["access_token"],
        "refresh_token": issued["refresh_token"],
        "expires_at": 4102444800.0,
        "source": "oauth",
        "client_id": issued["client_id"],
        "scope": "read write delete offline_access",
        "api_base": api.url,
    }
    (home / "penfield").mkdir(parents=True, exist_ok=True)
    (home / "penfield" / "tokens.json").write_text(json.dumps(saved), encoding="utf-8")
    return saved


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_a_sign_in_that_still_works_is_kept_by_pressing_enter(
    isolation: str, fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    home = _unselected(make_home(api_url=fake_api.url, isolation=isolation, api_key=""))
    before = _saved_sign_in(fake_api, home)

    result = run_in_terminal(home, SETUP, [(ASKS_HOW_TO_CONNECT, "\r")])

    shown = result["shown"]
    assert result["exit"] == 0, shown
    assert "Checking the saved browser sign-in with Penfield..." in shown
    assert "1. Keep this sign-in" in shown
    assert "Keeping the browser sign-in. This login may read, write and delete." in shown
    saved = json.loads((home / "penfield" / "tokens.json").read_text(encoding="utf-8"))
    assert saved == before
    assert "provider: penfield" in (home / "config.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_a_sign_in_penfield_has_ended_is_replaced_by_pressing_enter(
    isolation: str, fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    """This is the command the agent names when a sign-in stops working."""
    home = _unselected(make_home(api_url=fake_api.url, isolation=isolation, api_key=""))
    before = _saved_sign_in(fake_api, home)
    fake_api.valid_access_tokens.discard(before["access_token"])
    fake_api.valid_refresh_tokens.discard(before["refresh_token"])
    fake_api.approve_sign_ins = True

    result = run_in_terminal(home, SETUP, [(ASKS_HOW_TO_CONNECT, "\r")])

    shown = result["shown"]
    assert result["exit"] == 0, shown
    assert "This profile's browser sign-in is no longer valid" in shown
    assert "Keep this sign-in" not in shown
    assert "1. Sign in again with your browser (recommended)" in shown
    assert "Signed in. This login may read, write and delete." in shown
    saved = json.loads((home / "penfield" / "tokens.json").read_text(encoding="utf-8"))
    assert saved["access_token"] != before["access_token"]
    assert saved["access_token"] in fake_api.valid_access_tokens
    assert "provider: penfield" in (home / "config.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_a_sign_in_that_is_refused_changes_nothing(
    isolation: str, fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    fake_api.refuse_sign_ins = True
    home = _unselected(make_home(api_url=fake_api.url, isolation=isolation, api_key=""))

    result = run_in_terminal(home, SETUP, [(ASKS_HOW_TO_CONNECT, "1\r")])

    shown = result["shown"]
    assert result["exit"] == 0, shown
    assert "The sign-in did not finish: the sign-in was refused in the browser." in shown
    assert not (home / "penfield" / "tokens.json").exists()
    assert "provider: penfield" not in (home / "config.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_choosing_an_api_key_asks_for_it_checks_it_and_saves_it(
    isolation: str, fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    home = _unselected(make_home(api_url=fake_api.url, isolation=isolation, api_key=""))

    result = run_in_terminal(
        home, SETUP, [(ASKS_HOW_TO_CONNECT, "2\r"), (ASKS_FOR_THE_KEY, f"{KEY}\r")]
    )

    shown = result["shown"]
    assert result["exit"] == 0, shown
    assert result["typed"] == 2, shown
    assert "API key accepted by Penfield." in shown
    assert "Start a new session to activate." in shown
    assert KEY not in shown, "the key must be masked as it is typed"
    assert KEY not in _log(home)
    assert _exchanged_keys(fake_api) == [f"Bearer {KEY}"]
    assert f"PENFIELD_API_KEY={KEY}" in (home / ".env").read_text(encoding="utf-8")
    assert "provider: penfield" in (home / "config.yaml").read_text(encoding="utf-8")


def test_a_key_penfield_refuses_is_not_saved(
    fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    fake_api.rejected_api_keys.add(KEY)
    home = _unselected(make_home(api_url=fake_api.url, api_key=""))

    result = run_in_terminal(
        home, SETUP, [(ASKS_HOW_TO_CONNECT, "2\r"), (ASKS_FOR_THE_KEY, f"{KEY}\r")]
    )

    shown = result["shown"]
    assert result["exit"] == 0, shown
    assert "Penfield did not accept this API key. Nothing was changed." in shown
    assert KEY not in (home / ".env").read_text(encoding="utf-8")
    assert "provider: penfield" not in (home / "config.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_an_empty_answer_keeps_the_key_already_there(
    isolation: str, fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    home = _unselected(make_home(api_url=fake_api.url, isolation=isolation))
    before = (home / ".env").read_text(encoding="utf-8")

    result = run_in_terminal(home, SETUP, [("blank to keep", "\r")])

    shown = result["shown"]
    assert result["exit"] == 0, shown
    assert result["typed"] == 1, shown
    assert ASKS_HOW_TO_CONNECT not in shown
    assert "Current API key accepted by Penfield." in shown
    assert TEST_API_KEY not in shown
    assert (home / ".env").read_text(encoding="utf-8") == before
    assert "provider: penfield" in (home / "config.yaml").read_text(encoding="utf-8")


CTRL_C = "\x03"


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
@pytest.mark.parametrize(
    "answers",
    [
        [(ASKS_HOW_TO_CONNECT, CTRL_C)],
        [(ASKS_HOW_TO_CONNECT, "2\r"), (ASKS_FOR_THE_KEY, CTRL_C)],
        [(ASKS_HOW_TO_CONNECT, "\r"), ("The code is valid for", CTRL_C)],
    ],
    ids=["at the first question", "at the key question", "while waiting for the approval"],
)
def test_ctrl_c_cancels_the_setup_and_changes_nothing(
    isolation: str,
    answers: list[tuple[str, str]],
    fake_api: FakePenfield,
    make_home: Callable[..., Path],
) -> None:
    """In the plugin host too: Hermes must not be torn down with a traceback."""
    fake_api.device_poll_interval = 1.0
    home = _unselected(make_home(api_url=fake_api.url, isolation=isolation, api_key=""))

    result = run_in_terminal(home, SETUP, answers)

    shown = result["shown"]
    assert result["typed"] == len(answers), shown
    assert "Setup cancelled. Nothing was changed." in shown
    assert "Traceback" not in shown
    assert result["exit"] == 0, shown
    assert result["terminal_restored"] is True
    assert not (home / "penfield" / "tokens.json").exists()
    assert "PENFIELD_API_KEY=tm_" not in (home / ".env").read_text(encoding="utf-8")
    assert "provider: penfield" not in (home / "config.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_the_terminal_is_left_as_it_was_found(
    isolation: str, fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    fake_api.approve_sign_ins = True
    home = _unselected(make_home(api_url=fake_api.url, isolation=isolation, api_key=""))

    result = run_in_terminal(home, SETUP, [(ASKS_HOW_TO_CONNECT, "\r")])

    assert result["exit"] == 0, result["shown"]
    assert result["terminal_restored"] is True


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_without_a_terminal_hermes_keeps_to_its_own_steps(
    isolation: str, fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    """A script or a service has nobody to ask: the provider is selected and nothing waits."""
    home = _unselected(make_home(api_url=fake_api.url, isolation=isolation, api_key=""))

    result = subprocess.run(
        [*hermes_cli(), *SETUP],
        env=child_env(home),
        cwd=str(home),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        start_new_session=True,  # no controlling terminal either
        check=False,
    )

    shown = result.stdout + result.stderr
    assert result.returncode == 0, shown
    assert "Traceback" not in shown
    assert "Memory provider: penfield" in shown
    assert "provider: penfield" in (home / "config.yaml").read_text(encoding="utf-8")
    assert fake_api.calls() == []
