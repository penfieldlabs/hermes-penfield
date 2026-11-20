# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""The hook ``hermes memory setup`` runs: ask for the key, check it, save it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from penfield import setup_wizard
from penfield.provider import PenfieldMemoryProvider

from tests.support.fake_penfield import FakePenfield, refused_url

KEY = "tm_pf_setup_ak_unit"


class FakeHermes:
    """Stands in for the functions of Hermes's plugin API the hook uses."""

    def __init__(self, answer: str | BaseException = KEY) -> None:
        self.answer = answer
        self.asked: list[str] = []
        self.env_writes: list[tuple[str, str, str | None]] = []
        self.saved_configs: list[tuple[dict[str, Any], str | None]] = []
        self.home: str | None = None
        self.fail_saving: BaseException | None = None

    def ask(self, question: str) -> str:
        self.asked.append(question)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer

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
        answer: str | BaseException = KEY, config: dict[str, Any] | None = None
    ) -> tuple[FakeHermes, dict[str, Any], str]:
        hermes = FakeHermes(answer)
        config = {} if config is None else config
        setup_wizard.run(hermes.helpers(), "penfield", str(hermes_home), config, ask=hermes.ask)
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
    def _signed_in(self, hermes_home: Path) -> Path:
        from penfield.auth import TokenSet

        cache = hermes_home / "penfield" / "tokens.json"
        cache.parent.mkdir(parents=True)
        tokens = TokenSet("AT", "RT", expires_at=4102444800.0, source="oauth", client_id="CID")
        cache.write_text(json.dumps(tokens.to_dict()), encoding="utf-8")
        return cache

    def test_is_revoked_and_forgotten(
        self, setup, hermes_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from penfield.auth import PenfieldAuth

        cache = self._signed_in(hermes_home)
        revoked: list[str] = []
        monkeypatch.setattr(
            PenfieldAuth, "_revoke", lambda self, tokens: revoked.append(tokens.refresh_token)
        )

        _, _, out = setup()

        assert revoked == ["RT"]
        assert not cache.exists()
        assert "The saved browser sign-in was ended; the API key is used from now on." in out

    def test_is_kept_when_no_key_is_entered(
        self, setup, hermes_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from penfield.auth import PenfieldAuth

        cache = self._signed_in(hermes_home)
        monkeypatch.setattr(PenfieldAuth, "_revoke", lambda self, tokens: pytest.fail("revoked"))

        _, _, out = setup("")

        assert cache.exists()
        assert "The saved browser sign-in will be used." in out

    def test_is_kept_when_the_key_is_refused(
        self, setup, api: FakePenfield, hermes_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from penfield.auth import PenfieldAuth

        cache = self._signed_in(hermes_home)
        api.rejected_api_keys.add(KEY)
        monkeypatch.setattr(PenfieldAuth, "_revoke", lambda self, tokens: pytest.fail("revoked"))

        setup()

        assert cache.exists()

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

        setup_wizard.run(hermes.helpers(), "penfield", str(hermes_home), {}, ask=hermes.ask)

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


class TestAKeyThatCannotBeChecked:
    def test_is_saved_when_penfield_cannot_be_reached(
        self, setup, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PENFIELD_URL", refused_url())

        hermes, config, out = setup()

        assert hermes.env_writes[0][:2] == ("PENFIELD_API_KEY", KEY)
        assert config["memory"]["provider"] == "penfield"
        assert "API key could not be checked" in out
        assert "It was saved; `hermes penfield status` checks it again." in out

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
        assert "It was kept; `hermes penfield status` checks it again." in out

    def test_without_any_credentials_says_how_to_finish(self, setup, api: FakePenfield) -> None:
        hermes, config, out = setup("")

        assert hermes.env_writes == []
        assert config["memory"]["provider"] == "penfield"
        assert "No API key entered. Penfield stays off until there is one" in out
        assert "hermes penfield login" in out
        assert api.calls() == []

    def test_a_saved_browser_sign_in_is_enough(self, setup, hermes_home: Path) -> None:
        (hermes_home / "penfield").mkdir()
        signed_in = {"access_token": "AT", "refresh_token": "RT", "expires_at": 4102444800.0}
        (hermes_home / "penfield" / "tokens.json").write_text(
            json.dumps({**signed_in, "source": "oauth", "client_id": "CID"}), encoding="utf-8"
        )

        _, config, out = setup("")

        assert config["memory"]["provider"] == "penfield"
        assert "The saved browser sign-in will be used." in out


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
