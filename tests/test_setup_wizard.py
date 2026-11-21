# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""The hook ``hermes memory setup`` runs: sign in with a browser, or take an API key."""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from penfield import setup_wizard
from penfield.provider import PenfieldMemoryProvider

from tests.support.fake_penfield import FakePenfield, refused_url

KEY = "tm_pf_setup_ak_unit"


class FakeHermes:
    """Stands in for the functions of Hermes's plugin API the hook uses."""

    def __init__(
        self,
        answer: str | BaseException = KEY,
        *,
        choice: str | BaseException | list[str] = "2",
    ) -> None:
        #: What is typed at the API key question.
        self.answer = answer
        #: What is typed at "how do you want to connect"; ``2`` is the API key.
        self.choices = list(choice) if isinstance(choice, list) else [choice]
        self.asked: list[str] = []
        self.choices_asked: list[str] = []
        self.env_writes: list[tuple[str, str, str | None]] = []
        self.saved_configs: list[tuple[dict[str, Any], str | None]] = []
        self.home: str | None = None
        self.fail_saving: BaseException | None = None

    def ask(self, question: str) -> str:
        self.asked.append(question)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer

    def choose(self, question: str) -> str:
        self.choices_asked.append(question)
        choice = self.choices.pop(0) if len(self.choices) > 1 else self.choices[0]
        if isinstance(choice, BaseException):
            raise choice
        return choice

    def terminal(self) -> setup_wizard.Terminal:
        return setup_wizard.Terminal(say=print, ask=self.choose, ask_secret=self.ask)

    def save_env_value(self, name: str, value: str) -> None:
        if self.fail_saving is not None:
            raise self.fail_saving
        self.env_writes.append((name, value, self.home))

    def save_config(self, config: dict[str, Any]) -> None:
        copy = {k: dict(v) if isinstance(v, dict) else v for k, v in config.items()}
        self.saved_configs.append((copy, self.home))

    def set_home(self, path: str) -> str | None:
        previous, self.home = self.home, path
        return previous

    def reset_home(self, token: str | None) -> None:
        self.home = token

    def helpers(self) -> setup_wizard._Hermes:
        return setup_wizard._Hermes(
            save_env_value=self.save_env_value,
            save_config=self.save_config,
            set_home=self.set_home,
            reset_home=self.reset_home,
        )


@pytest.fixture
def setup(
    api: FakePenfield,
    hermes_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """Run the hook against the fake API and return (hermes, config, printed)."""
    monkeypatch.setenv("PENFIELD_URL", api.url)

    def run(
        answer: str | BaseException = KEY,
        config: dict[str, Any] | None = None,
        *,
        choice: str | BaseException | list[str] = "2",
    ) -> tuple[FakeHermes, dict[str, Any], str]:
        hermes = FakeHermes(answer, choice=choice)
        config = {} if config is None else config
        setup_wizard.run(
            hermes.helpers(), "penfield", str(hermes_home), config, terminal=hermes.terminal()
        )
        return hermes, config, capsys.readouterr().out

    return run


class TestANewKey:
    def test_is_checked_saved_and_the_provider_selected(
        self, setup, api: FakePenfield, hermes_home: Path
    ) -> None:
        hermes, config, out = setup()

        assert [c.authorization for c in api.calls() if c.path == "/auth/token"] == [
            f"Bearer {KEY}"
        ]
        assert hermes.env_writes == [("PENFIELD_API_KEY", KEY, str(hermes_home))]
        assert config["memory"]["provider"] == "penfield"
        assert hermes.saved_configs == [({"memory": {"provider": "penfield"}}, str(hermes_home))]
        assert hermes.home is None, "the profile override must be undone"
        assert "API key accepted by Penfield." in out
        assert "API key saved to .env" in out
        assert "Start a new session to activate." in out

    def test_is_asked_for_once_and_never_printed(self, setup) -> None:
        hermes, _, out = setup()

        assert hermes.asked == ["  Penfield API key"]
        assert KEY not in out

    def test_the_answer_is_read_without_echo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        shown: list[str] = []
        monkeypatch.setattr(
            "getpass.getpass", lambda prompt="": shown.append(prompt) or "typed-unseen"
        )

        assert setup_wizard._ask_secret("  Penfield API key") == "typed-unseen"
        assert shown == ["  Penfield API key: "]

    def test_says_where_to_get_one(self, setup) -> None:
        _, _, out = setup()

        assert "https://portal.penfield.app/settings/api-keys" in out

    def test_surrounding_spaces_are_dropped(self, setup) -> None:
        hermes, _, _ = setup(f"  {KEY}\n")

        assert hermes.env_writes[0][:2] == ("PENFIELD_API_KEY", KEY)

    def test_the_check_leaves_no_token_cache(self, setup, hermes_home: Path) -> None:
        setup()

        assert not (hermes_home / "penfield" / "tokens.json").exists()

    def test_other_settings_in_the_config_are_kept(self, setup) -> None:
        _, config, _ = setup(config={"memory": {"nudge_interval": 5}, "model": {"default": "m"}})

        assert config == {
            "memory": {"nudge_interval": 5, "provider": "penfield"},
            "model": {"default": "m"},
        }

    def test_a_broken_memory_section_is_replaced(self, setup) -> None:
        _, config, _ = setup(config={"memory": "penfield"})

        assert config["memory"] == {"provider": "penfield"}


class TestABrowserSignInTheKeyReplaces:
    def test_is_revoked_and_forgotten(self, setup, api: FakePenfield, hermes_home: Path) -> None:
        _sign_in(api, hermes_home)
        before = _saved(hermes_home)

        _, _, out = setup()

        assert api.revoked == [before["refresh_token"], before["access_token"]]
        assert not (hermes_home / "penfield" / "tokens.json").exists()
        assert "The saved browser sign-in was ended; the API key is used from now on." in out

    def test_is_kept_when_no_key_is_entered(
        self, setup, api: FakePenfield, hermes_home: Path
    ) -> None:
        _sign_in(api, hermes_home)
        before = _saved(hermes_home)

        _, _, out = setup("")

        assert api.revoked == []
        assert _saved(hermes_home) == before
        assert "The saved browser sign-in will be used." in out

    def test_is_kept_when_the_key_is_refused(
        self, setup, api: FakePenfield, hermes_home: Path
    ) -> None:
        _sign_in(api, hermes_home)
        before = _saved(hermes_home)
        api.rejected_api_keys.add(KEY)

        setup()

        assert api.revoked == []
        assert _saved(hermes_home) == before

    def test_is_kept_when_the_key_cannot_be_checked(
        self, setup, api: FakePenfield, hermes_home: Path
    ) -> None:
        """An unchecked key may not work; the login that does is not given up for it."""
        _sign_in(api, hermes_home)
        before = _saved(hermes_home)
        api.fail_next[("POST", "/auth/token")] = [503]

        hermes, _, out = setup()

        assert hermes.env_writes == [("PENFIELD_API_KEY", KEY, str(hermes_home))]
        assert "API key could not be checked" in out
        assert api.revoked == []
        assert _saved(hermes_home) == before
        assert "It was saved; run this command again later to check it." in out
        assert "The saved browser sign-in was kept, because the key could not be checked." in out
        assert "Enter the key again when you do: once Penfield has accepted it, the" in out
        assert "sign-in is ended." in out

    def test_one_set_aside_by_the_current_key_is_ended_with_a_new_key(
        self, setup, api: FakePenfield, hermes_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _sign_in(api, hermes_home)
        before = _saved(hermes_home)
        monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_current_ak_wxyz")

        _, _, out = setup()

        assert api.revoked == [before["refresh_token"], before["access_token"]]
        assert "The saved browser sign-in was ended; the API key is used from now on." in out

    def test_tokens_of_an_earlier_key_are_left_for_the_next_exchange(
        self, setup, hermes_home: Path
    ) -> None:
        from penfield.auth import TokenSet

        cache = hermes_home / "penfield" / "tokens.json"
        cache.parent.mkdir(parents=True)
        earlier = TokenSet("AT", "RT", expires_at=4102444800.0, key_id="0123456789abcdef")
        cache.write_text(json.dumps(earlier.to_dict()), encoding="utf-8")

        _, _, out = setup()

        assert cache.exists()
        assert "browser sign-in" not in out


def test_the_hook_names_no_command_the_plugin_host_does_not_have() -> None:
    """The hook runs in the plugin host too, where `hermes penfield` commands do not exist."""
    import ast

    tree = ast.parse(Path(setup_wizard.__file__).read_text(encoding="utf-8"))
    documented = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, documented)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    said = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]

    assert said, "the hook's messages were not found"
    assert [text for text in said if "hermes penfield" in text] == []


class TestSavingFails:
    @pytest.mark.parametrize(
        "failure", [OSError("read-only file system"), ValueError("not a valid value")]
    )
    def test_the_provider_is_not_selected_without_its_key(
        self,
        api: FakePenfield,
        hermes_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        failure: BaseException,
    ) -> None:
        monkeypatch.setenv("PENFIELD_URL", api.url)
        hermes = FakeHermes()
        hermes.fail_saving = failure

        setup_wizard.run(
            hermes.helpers(), "penfield", str(hermes_home), {}, terminal=hermes.terminal()
        )

        out = capsys.readouterr().out
        assert hermes.saved_configs == []
        assert f"Setup could not be saved: {failure}" in out
        assert "Start a new session" not in out
        assert hermes.home is None


class TestAKeyPenfieldRefuses:
    def test_changes_nothing(self, setup, api: FakePenfield) -> None:
        api.rejected_api_keys.add(KEY)

        hermes, config, out = setup()

        assert hermes.env_writes == []
        assert hermes.saved_configs == []
        assert config == {}
        assert "Penfield did not accept this API key. Nothing was changed." in out
        assert "Start a new session" not in out
        assert KEY not in out

    def test_does_not_replace_a_key_that_works(
        self, setup, api: FakePenfield, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_current_ak_unit")
        api.rejected_api_keys.add(KEY)

        hermes, _, _ = setup()

        assert hermes.env_writes == []


class TestAKeyThatCannotBeAKey:
    """An invisible character picked up while copying is the usual cause."""

    DAMAGED = "tm_pf_setup\u200b_ak_unit"

    def test_is_refused_without_asking_penfield(self, setup, api: FakePenfield) -> None:
        hermes, config, out = setup(self.DAMAGED)

        assert (
            "That cannot be an API key: it contains the character ZERO WIDTH SPACE "
            "at position 12, which"
        ) in out
        assert "Nothing was changed." in out
        assert hermes.env_writes == []
        assert hermes.saved_configs == []
        assert config == {}
        assert api.calls() == []
        assert "tm_pf_setup" not in out, "the key is never shown"

    def test_as_the_current_key_it_is_named_as_the_problem(
        self, setup, api: FakePenfield, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_cur\u200brent_ak_wxyz")

        _, _, out = setup("")

        assert "The current API key cannot be used: it contains the character ZERO" in out
        assert "Run this command again and enter a new one." in out
        assert api.calls("POST", "/auth/token") == []

    def test_a_good_key_replaces_one_that_cannot_be_a_key(
        self, setup, hermes_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_cur\u200brent_ak_wxyz")

        hermes, _, out = setup()

        assert hermes.env_writes == [("PENFIELD_API_KEY", KEY, str(hermes_home))]
        assert "API key accepted by Penfield." in out


class TestAKeyThatCannotBeChecked:
    def test_is_saved_when_penfield_cannot_be_reached(
        self, setup, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PENFIELD_URL", refused_url())

        hermes, config, out = setup()

        assert hermes.env_writes[0][:2] == ("PENFIELD_API_KEY", KEY)
        assert config["memory"]["provider"] == "penfield"
        assert "API key could not be checked" in out
        assert "It was saved; run this command again later to check it." in out

    def test_a_server_fault_is_not_taken_for_a_refusal(self, setup, api: FakePenfield) -> None:
        api.fail_next[("POST", "/auth/token")] = [503]

        hermes, _, out = setup()

        assert hermes.env_writes[0][:2] == ("PENFIELD_API_KEY", KEY)
        assert "could not be checked" in out

    def test_an_answer_that_is_not_about_the_key_is_not_taken_for_a_refusal(
        self, setup, api: FakePenfield
    ) -> None:
        api.fail_next[("POST", "/auth/token")] = [404]

        hermes, _, out = setup()

        assert hermes.env_writes[0][:2] == ("PENFIELD_API_KEY", KEY)
        assert "could not be checked" in out


class TestNoKeyEntered:
    def test_keeps_the_current_key_and_checks_it(
        self, setup, api: FakePenfield, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_current_ak_wxyz")

        hermes, config, out = setup("")

        assert hermes.asked == ["  Penfield API key (current: ...wxyz, blank to keep)"]
        assert hermes.env_writes == []
        assert config["memory"]["provider"] == "penfield"
        assert "Current API key accepted by Penfield." in out
        assert "API key saved to .env" not in out
        assert "tm_pf_current_ak_wxyz" not in out

    def test_a_very_short_current_value_is_not_shown_at_all(
        self, setup, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PENFIELD_API_KEY", "abcd")

        hermes, _, out = setup("")

        assert hermes.asked == ["  Penfield API key (current: set, blank to keep)"]
        assert "abcd" not in out

    def test_says_when_the_current_key_no_longer_works(
        self, setup, api: FakePenfield, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_current_ak_wxyz")
        api.rejected_api_keys.add("tm_pf_current_ak_wxyz")

        _, config, out = setup("")

        assert config["memory"]["provider"] == "penfield"
        assert "Penfield does not accept the current API key." in out

    def test_says_when_the_current_key_cannot_be_checked(
        self, setup, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_current_ak_wxyz")
        monkeypatch.setenv("PENFIELD_URL", refused_url())

        _, _, out = setup("")

        assert "Current API key could not be checked" in out
        assert "It was kept; run this command again later to check it." in out

    def test_without_any_credentials_says_how_to_finish(self, setup, api: FakePenfield) -> None:
        hermes, config, out = setup("")

        assert hermes.env_writes == []
        assert config["memory"]["provider"] == "penfield"
        assert "No API key entered. Penfield stays off until the profile is connected" in out
        assert "run this command again to sign in with your browser" in out
        assert api.calls() == []

    def test_a_saved_browser_sign_in_is_enough(
        self, setup, api: FakePenfield, hermes_home: Path
    ) -> None:
        _sign_in(api, hermes_home)

        _, config, out = setup("")

        assert config["memory"]["provider"] == "penfield"
        assert "The saved browser sign-in will be used." in out


def _saved(hermes_home: Path) -> dict[str, Any]:
    saved: dict[str, Any] = json.loads(
        (hermes_home / "penfield" / "tokens.json").read_text(encoding="utf-8")
    )
    return saved


def _sign_in(
    api: FakePenfield, hermes_home: Path, scope: str = "read write offline_access"
) -> None:
    """Leave a finished browser sign-in in the profile."""
    issued = api.sign_in(scope)
    (hermes_home / "penfield").mkdir(parents=True, exist_ok=True)
    (hermes_home / "penfield" / "tokens.json").write_text(
        json.dumps(
            {
                "access_token": issued["access_token"],
                "refresh_token": issued["refresh_token"],
                "expires_at": 4102444800.0,
                "source": "oauth",
                "client_id": issued["client_id"],
                "scope": scope,
                "api_base": api.url,
            }
        ),
        encoding="utf-8",
    )
    api.reset_calls()


class TestSigningInWithTheBrowser:
    def test_is_the_first_choice_and_the_default(
        self, setup, api: FakePenfield, hermes_home: Path
    ) -> None:
        api.approve_sign_ins = True

        hermes, config, out = setup(choice="")

        assert "1. Sign in with your browser (recommended)" in out
        assert "2. Use an API key" in out
        assert "Checking the saved browser sign-in" not in out, "there is none to check"
        assert hermes.choices_asked == ["  Choice [1]"]
        assert hermes.asked == [], "no API key is asked for"
        assert f"To authorize, visit: {api.root}/device?user_code=TEST-0001" in out
        assert _saved(hermes_home)["source"] == "oauth"
        assert hermes.env_writes == []
        assert hermes.saved_configs == [({"memory": {"provider": "penfield"}}, str(hermes_home))]
        assert config["memory"]["provider"] == "penfield"
        assert "Signed in. This login may read, write and delete." in out
        assert "Start a new session to activate." in out
        assert hermes.home is None, "the profile override must be undone"

    def test_a_sign_in_that_is_refused_changes_nothing(
        self, setup, api: FakePenfield, hermes_home: Path
    ) -> None:
        api.refuse_sign_ins = True

        hermes, config, out = setup(choice="1")

        assert "The sign-in did not finish: the sign-in was refused in the browser." in out
        assert "Nothing was changed." in out
        assert hermes.saved_configs == []
        assert config == {}
        assert not (hermes_home / "penfield" / "tokens.json").exists()

    def test_a_penfield_that_cannot_be_reached_changes_nothing(
        self, setup, hermes_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PENFIELD_URL", refused_url())

        hermes, config, out = setup(choice="1")

        assert "The sign-in did not finish" in out
        assert hermes.saved_configs == []
        assert config == {}

    def test_a_setting_it_cannot_use_stops_the_setup(
        self, setup, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PENFIELD_ENV", "staging")

        hermes, config, out = setup(choice="1")

        assert "Setup stopped: unknown PENFIELD_ENV 'staging'" in out
        assert "Nothing was changed." in out
        assert hermes.saved_configs == []
        assert config == {}

    def test_a_profile_that_is_signed_in_keeps_its_sign_in_by_default(
        self, setup, api: FakePenfield, hermes_home: Path
    ) -> None:
        _sign_in(api, hermes_home)
        before = _saved(hermes_home)

        hermes, config, out = setup(choice="")

        assert hermes.asked == [], "no API key is asked for"
        assert "This profile is signed in with a browser. That login may read and write." in out
        assert "1. Keep this sign-in" in out
        assert "3. Sign in again" in out
        asked = [(call.method, call.path) for call in api.calls()]
        assert asked == [("GET", "/search/stats")], "one request shows that it still works"
        checking = out.index("Checking the saved browser sign-in with Penfield...")
        assert checking < out.index("This profile is signed in with a browser."), (
            "said before the request, which can take a while when Penfield is slow"
        )
        assert _saved(hermes_home) == before
        assert config["memory"]["provider"] == "penfield"
        assert "Keeping the browser sign-in. This login may read and write." in out

    def test_signing_in_again_replaces_the_sign_in(
        self, setup, api: FakePenfield, hermes_home: Path
    ) -> None:
        _sign_in(api, hermes_home)
        before = _saved(hermes_home)
        api.approve_sign_ins = True

        _, config, out = setup(choice="3")

        after = _saved(hermes_home)
        assert after["access_token"] != before["access_token"]
        assert after["scope"] == "read write delete offline_access"
        assert config["memory"]["provider"] == "penfield"
        assert "Signed in. This login may read, write and delete." in out

    def test_an_answer_that_is_not_a_choice_is_asked_again(self, setup) -> None:
        hermes, _, out = setup(choice=["x", "9", "0", "2"])

        assert hermes.choices_asked == ["  Choice [1]"] * 4
        assert out.count("Please answer with a number from 1 to 2.") == 3
        assert hermes.asked == ["  Penfield API key"]

    @pytest.mark.parametrize("interruption", [KeyboardInterrupt(), EOFError()])
    def test_cancelling_at_the_choice_changes_nothing(
        self, setup, interruption: BaseException
    ) -> None:
        hermes, config, out = setup(choice=interruption)

        assert hermes.saved_configs == []
        assert config == {}
        assert "Setup cancelled. Nothing was changed." in out

    def test_cancelling_while_waiting_for_the_approval_changes_nothing(
        self, setup, hermes_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from penfield.auth import PenfieldAuth

        def interrupted(self: PenfieldAuth, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(PenfieldAuth, "device_code_flow", interrupted)

        hermes, config, out = setup(choice="1")

        assert hermes.saved_configs == []
        assert config == {}
        assert "Setup cancelled. Nothing was changed." in out
        assert not (hermes_home / "penfield" / "tokens.json").exists()


def _end_at_penfield(api: FakePenfield, hermes_home: Path) -> None:
    """End the profile's sign-in at the server, as revoking it in the portal does."""
    saved = _saved(hermes_home)
    api.valid_access_tokens.discard(saved["access_token"])
    api.valid_refresh_tokens.discard(saved["refresh_token"])


class TestASavedSignInIsCheckedWithPenfield:
    """The file alone does not tell whether a sign-in still works."""

    def test_one_that_has_ended_is_not_offered_to_keep(
        self, setup, api: FakePenfield, hermes_home: Path
    ) -> None:
        _sign_in(api, hermes_home)
        before = _saved(hermes_home)
        _end_at_penfield(api, hermes_home)
        api.approve_sign_ins = True

        hermes, config, out = setup(choice="")

        assert "Checking the saved browser sign-in with Penfield..." in out
        assert "This profile's browser sign-in is no longer valid: it ran out or was" in out
        assert "Keep this sign-in" not in out
        assert "1. Sign in again with your browser (recommended)" in out
        assert "2. Use an API key" in out
        assert hermes.choices_asked == ["  Choice [1]"]
        after = _saved(hermes_home)
        assert after["access_token"] != before["access_token"]
        assert after["access_token"] in api.valid_access_tokens
        assert config["memory"]["provider"] == "penfield"
        assert "Signed in. This login may read, write and delete." in out

    def test_one_that_has_ended_gives_way_to_an_api_key(
        self, setup, api: FakePenfield, hermes_home: Path
    ) -> None:
        _sign_in(api, hermes_home)
        _end_at_penfield(api, hermes_home)

        hermes, _, out = setup(choice="2")

        assert hermes.env_writes == [("PENFIELD_API_KEY", KEY, str(hermes_home))]
        assert "API key accepted by Penfield." in out
        assert not (hermes_home / "penfield" / "tokens.json").exists()

    def test_one_that_has_ended_is_not_what_the_profile_falls_back_on(
        self, setup, api: FakePenfield, hermes_home: Path
    ) -> None:
        _sign_in(api, hermes_home)
        _end_at_penfield(api, hermes_home)

        _, _, out = setup("", choice="2")

        assert "The saved browser sign-in will be used." not in out
        assert "Penfield stays off until the profile is connected" in out

    def test_one_whose_access_token_ran_out_is_renewed_and_kept(
        self, setup, api: FakePenfield, hermes_home: Path
    ) -> None:
        _sign_in(api, hermes_home)
        before = _saved(hermes_home)
        cache = hermes_home / "penfield" / "tokens.json"
        cache.write_text(json.dumps({**before, "expires_at": 0.0}), encoding="utf-8")

        _, config, out = setup(choice="")

        after = _saved(hermes_home)
        assert after["access_token"] != before["access_token"]
        assert after["access_token"] in api.valid_access_tokens
        assert config["memory"]["provider"] == "penfield"
        assert "Keeping the browser sign-in." in out

    def test_one_that_cannot_be_renewed_just_now_can_still_be_kept(
        self, setup, api: FakePenfield, hermes_home: Path
    ) -> None:
        """A sign-in server that is down says nothing about the sign-in."""
        _sign_in(api, hermes_home)
        cache = hermes_home / "penfield" / "tokens.json"
        cache.write_text(json.dumps({**_saved(hermes_home), "expires_at": 0.0}), encoding="utf-8")
        before = _saved(hermes_home)
        api.fail_next[("POST", "/oauth/token")] = [503]

        _, config, out = setup(choice="")

        assert "The sign-in could not be checked (auth server error (503))." in out
        assert "1. Keep this sign-in" in out
        assert _saved(hermes_home) == before
        assert config["memory"]["provider"] == "penfield"

    def test_one_that_may_not_read_is_a_sign_in_all_the_same(
        self, setup, api: FakePenfield, hermes_home: Path
    ) -> None:
        _sign_in(api, hermes_home, scope="write offline_access")

        _, _, out = setup(choice="")

        assert "could not be checked" not in out
        assert "no longer valid" not in out
        assert "1. Keep this sign-in" in out

    def test_one_that_cannot_be_checked_can_still_be_kept(
        self, setup, api: FakePenfield, hermes_home: Path
    ) -> None:
        _sign_in(api, hermes_home)
        before = _saved(hermes_home)
        api.outage = "errors"

        _, config, out = setup(choice="")

        assert "This profile is signed in with a browser. That login may read and write." in out
        assert "The sign-in could not be checked (" in out
        assert "1. Keep this sign-in" in out
        assert _saved(hermes_home) == before
        assert config["memory"]["provider"] == "penfield"
        assert "Keeping the browser sign-in. This login may read and write." in out


class TestWithAnApiKeyAlreadySet:
    def test_the_key_is_what_is_asked_about(self, setup, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_current_ak_wxyz")

        hermes, _, out = setup("")

        assert hermes.choices_asked == []
        assert "This profile connects with an API key" in out
        assert "remove\n  PENFIELD_API_KEY from the profile's .env" in out
        assert hermes.asked == ["  Penfield API key (current: ...wxyz, blank to keep)"]

    def test_a_setting_it_cannot_use_is_reported_with_the_key(
        self, setup, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_current_ak_wxyz")
        monkeypatch.setenv("PENFIELD_ENV", "staging")

        hermes, _, out = setup("")

        assert hermes.asked == ["  Penfield API key (current: ...wxyz, blank to keep)"]
        assert "Current API key could not be checked (unknown PENFIELD_ENV 'staging'" in out
        assert "Setup stopped" not in out


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), EOFError()])
def test_cancelling_at_the_prompt_changes_nothing(setup, interruption: BaseException) -> None:
    hermes, config, out = setup(interruption)

    assert hermes.env_writes == []
    assert hermes.saved_configs == []
    assert config == {}
    assert "Setup cancelled. Nothing was changed." in out


class TestWhereTheHookExists:
    """Hermes takes the hook's presence as "this provider does its own setup"."""

    def test_not_without_a_terminal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(setup_wizard, "_has_terminal", lambda: False)
        monkeypatch.setattr(setup_wizard, "_hermes_helpers", lambda: FakeHermes().helpers())

        assert setup_wizard.hook("penfield") is None
        assert not hasattr(PenfieldMemoryProvider(), "post_setup")

    def test_not_when_hermes_lacks_its_setup_helpers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(setup_wizard, "_has_terminal", lambda: True)

        assert setup_wizard._hermes_helpers() is None  # Hermes is not importable here
        assert setup_wizard.hook("penfield") is None
        assert not hasattr(PenfieldMemoryProvider(), "post_setup")

    @pytest.mark.parametrize(
        ("stdin_is_terminal", "stdout_is_terminal", "expected"),
        [(True, True, True), (True, False, False), (False, True, False)],
    )
    def test_a_terminal_means_both_asking_and_answering(
        self,
        monkeypatch: pytest.MonkeyPatch,
        stdin_is_terminal: bool,
        stdout_is_terminal: bool,
        expected: bool,
    ) -> None:
        class Stream:
            def __init__(self, terminal: bool) -> None:
                self._terminal = terminal

            def isatty(self) -> bool:
                return self._terminal

        monkeypatch.setattr("sys.stdin", Stream(stdin_is_terminal))
        monkeypatch.setattr("sys.stdout", Stream(stdout_is_terminal))

        assert setup_wizard._has_terminal() is expected

    def test_a_closed_stream_counts_as_no_terminal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", None)

        assert setup_wizard._has_terminal() is False

    def test_where_it_can_run_the_provider_offers_it(
        self,
        api: FakePenfield,
        hermes_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        hermes = FakeHermes()
        monkeypatch.setenv("PENFIELD_URL", api.url)
        monkeypatch.setattr(setup_wizard, "_has_terminal", lambda: True)
        monkeypatch.setattr(setup_wizard, "_hermes_helpers", hermes.helpers)
        monkeypatch.setattr(setup_wizard, "_ask_secret", hermes.ask)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "2")
        provider = PenfieldMemoryProvider()
        config: dict[str, Any] = {}

        assert hasattr(provider, "post_setup")
        provider.post_setup(str(hermes_home), config)

        assert hermes.env_writes == [("PENFIELD_API_KEY", KEY, str(hermes_home))]
        assert config == {"memory": {"provider": "penfield"}}
        assert "API key accepted by Penfield." in capsys.readouterr().out


class TestCheckKey:
    def test_reports_the_three_outcomes(
        self, api: FakePenfield, hermes_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PENFIELD_URL", api.url)
        api.rejected_api_keys.add("tm_pf_revoked_ak_unit")

        assert setup_wizard.check_key(KEY, str(hermes_home)) == (setup_wizard.ACCEPTED, "")
        verdict, reason = setup_wizard.check_key("tm_pf_revoked_ak_unit", str(hermes_home))
        assert (verdict, "401" in reason) == (setup_wizard.REJECTED, True)
        monkeypatch.setenv("PENFIELD_URL", refused_url())
        assert setup_wizard.check_key(KEY, str(hermes_home))[0] == setup_wizard.UNCHECKED

    def test_a_setting_it_cannot_use_is_reported_not_raised(
        self, hermes_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PENFIELD_ENV", "no-such-environment")

        verdict, reason = setup_wizard.check_key(KEY, str(hermes_home))

        assert verdict == setup_wizard.UNCHECKED
        assert reason


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pseudo-terminal")
class TestTheControllingTerminal:
    """How the hook reaches the user from the plugin host, on a terminal of its own."""

    @pytest.fixture
    def keyboard(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
        """A pseudo-terminal standing in for ``/dev/tty``; yields the end a user types into."""
        user_end, plugin_end = os.openpty()
        monkeypatch.setattr(setup_wizard, "_TTY", os.ttyname(plugin_end))
        yield user_end
        os.close(user_end)
        os.close(plugin_end)

    def _screen(self, keyboard: int) -> str:
        shown = b""
        while select.select([keyboard], [], [], 0.2)[0]:
            shown += os.read(keyboard, 4096)
        return shown.decode()

    def test_a_question_returns_the_line_that_was_typed(self, keyboard: int) -> None:
        terminal = setup_wizard._controlling_terminal()
        assert terminal is not None
        os.write(keyboard, b"2\n")

        assert terminal.ask("  Choice [1]") == "2"
        assert "  Choice [1]: " in self._screen(keyboard)

    def test_a_secret_is_not_shown_as_it_is_typed(self, keyboard: int) -> None:
        terminal = setup_wizard._controlling_terminal()
        assert terminal is not None
        typist = threading.Timer(0.3, os.write, (keyboard, b"tm_pf_typed_unseen\n"))
        typist.start()

        assert terminal.ask_secret("  Penfield API key") == "tm_pf_typed_unseen"
        typist.join()
        assert "tm_pf_typed_unseen" not in self._screen(keyboard)

    def test_a_line_is_shown(self, keyboard: int) -> None:
        terminal = setup_wizard._controlling_terminal()
        assert terminal is not None

        terminal.say("  Signed in.")

        assert "  Signed in." in self._screen(keyboard)

    @pytest.mark.parametrize("secret", [False, True])
    def test_ctrl_c_cancels_a_question_at_once(self, keyboard: int, secret: bool) -> None:
        terminal = setup_wizard._controlling_terminal()
        assert terminal is not None
        typist = threading.Timer(0.3, os.write, (keyboard, b"\x03"))
        with terminal.session():
            typist.start()
            started = time.monotonic()
            with pytest.raises(KeyboardInterrupt):
                (terminal.ask_secret if secret else terminal.ask)("  A question")
        typist.join()
        assert time.monotonic() - started < 5, "no Enter was needed"

    def test_ctrl_c_ends_a_pause(self, keyboard: int) -> None:
        terminal = setup_wizard._controlling_terminal()
        assert terminal is not None
        typist = threading.Timer(0.3, os.write, (keyboard, b"\x03"))
        with terminal.session():
            typist.start()
            started = time.monotonic()
            with pytest.raises(KeyboardInterrupt):
                terminal.wait(30)
        typist.join()
        assert time.monotonic() - started < 5

    def test_a_pause_lasts_its_time_whatever_else_is_typed(self, keyboard: int) -> None:
        terminal = setup_wizard._controlling_terminal()
        assert terminal is not None
        os.write(keyboard, b"hello\n\n")
        with terminal.session():
            started = time.monotonic()
            terminal.wait(0.5)
        assert 0.45 <= time.monotonic() - started < 3

    def test_the_terminal_is_put_back_as_it_was_found(self, keyboard: int) -> None:
        import termios

        terminal = setup_wizard._controlling_terminal()
        assert terminal is not None
        found = termios.tcgetattr(keyboard)
        assert found[3] & termios.ISIG

        with pytest.raises(RuntimeError), terminal.session():
            assert not termios.tcgetattr(keyboard)[3] & termios.ISIG
            raise RuntimeError("the hook failed half way")

        assert termios.tcgetattr(keyboard) == found

    def test_the_terminal_is_put_back_when_the_process_is_ended_abruptly(
        self, keyboard: int
    ) -> None:
        """Hermes ends the plugin host with ``os._exit``; nothing in it gets to clean up."""
        import termios

        found = termios.tcgetattr(keyboard)
        repo = Path(setup_wizard.__file__).parent
        script = f"""
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location(
    "penfield", {str(repo / "__init__.py")!r}, submodule_search_locations=[{str(repo)!r}])
package = importlib.util.module_from_spec(spec)
sys.modules["penfield"] = package
spec.loader.exec_module(package)
from penfield import setup_wizard
setup_wizard._TTY = {setup_wizard._TTY!r}
with setup_wizard._ctrl_c_as_a_key():
    print("changed", flush=True)
    sys.stdin.readline()
    os._exit(0)
"""
        child = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert child.stdout is not None
        assert child.stdin is not None
        assert child.stdout.readline().strip() == "changed"
        assert not termios.tcgetattr(keyboard)[3] & termios.ISIG

        child.communicate("go\n", timeout=10)

        deadline = time.monotonic() + 5
        while termios.tcgetattr(keyboard) != found and time.monotonic() < deadline:
            time.sleep(0.05)
        assert termios.tcgetattr(keyboard) == found

    def test_there_is_none_without_a_controlling_terminal(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(setup_wizard, "_TTY", str(tmp_path / "no-such-terminal"))
        assert setup_wizard._controlling_terminal() is None

    def test_in_the_plugin_host_the_hook_uses_it(
        self, keyboard: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HERMES_PLUGIN_HOST_PROCESS", "1")
        monkeypatch.setattr(setup_wizard, "_hermes_helpers", lambda: FakeHermes().helpers())
        monkeypatch.setattr(setup_wizard, "_has_terminal", lambda: False)

        hook = setup_wizard.hook("penfield")

        assert hook is not None
        assert hook.keywords["terminal"].session is setup_wizard._ctrl_c_as_a_key

    def test_in_the_plugin_host_without_one_there_is_no_hook(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HERMES_PLUGIN_HOST_PROCESS", "1")
        monkeypatch.setattr(setup_wizard, "_TTY", str(tmp_path / "no-such-terminal"))
        monkeypatch.setattr(setup_wizard, "_hermes_helpers", lambda: FakeHermes().helpers())
        monkeypatch.setattr(setup_wizard, "_has_terminal", lambda: True)

        assert setup_wizard.hook("penfield") is None
