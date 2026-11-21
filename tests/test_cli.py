# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""``hermes penfield <command>`` against the local fake API."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest
from penfield.cli import penfield_command, register_cli
from penfield.constants import VERSION

from tests.support.fake_penfield import FakePenfield, refused_url


@pytest.fixture
def run(
    api: FakePenfield,
    hermes_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """Run a CLI command the way Hermes does and return (exit code, stdout, stderr)."""
    # Retries stay; the pauses between them and the wait for another process
    # to rotate a token are not what these tests are about.
    monkeypatch.setattr("penfield.client.BACKOFF_BASE", 0.0)
    monkeypatch.setattr("penfield.auth._ROTATION_WAIT_SECONDS", 0.05)
    monkeypatch.setenv("PENFIELD_URL", api.url)
    monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_test_ak_cli")

    def invoke(*argv: str) -> tuple[int, str, str]:
        parser = argparse.ArgumentParser(prog="hermes penfield")
        register_cli(parser)
        args = parser.parse_args(list(argv))
        with pytest.raises(SystemExit) as exit_info:
            args.func(args)
        captured = capsys.readouterr()
        return int(exit_info.value.code or 0), captured.out, captured.err

    return invoke


class TestHermesHome:
    def test_explicit_home_wins(self) -> None:
        from penfield.cli import _resolve_hermes_home

        assert _resolve_hermes_home("/custom/path") == "/custom/path"

    def test_defaults_to_the_active_profile_home(self, hermes_home: Path) -> None:
        from penfield.cli import _resolve_hermes_home

        assert _resolve_hermes_home("") == str(hermes_home)

    def test_falls_back_to_dot_hermes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from penfield.cli import _resolve_hermes_home

        monkeypatch.delenv("HERMES_HOME", raising=False)
        assert _resolve_hermes_home("").endswith("/.hermes")


class TestStatus:
    def test_reports_the_connection(self, run, api: FakePenfield) -> None:
        api.seed_memory("one")
        api.seed_memory("two")
        code, out, _ = run("status")
        assert code == 0
        assert f"penfield {VERSION}" in out
        assert f"api_base: {api.url}" in out
        assert "credentials: API key" in out
        assert "connection: ok" in out
        assert "memory count: 2" in out

    def test_never_prints_the_api_key(self, run) -> None:
        _, out, err = run("status")
        assert "tm_pf_test_ak_cli" not in out + err

    def test_survives_an_unreachable_api(self, run, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PENFIELD_URL", refused_url())
        code, out, _ = run("status")
        assert code == 0
        assert "connection: failed" in out
        assert "memory count" not in out

    def test_says_when_nothing_is_set_up(self, run, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PENFIELD_API_KEY")
        _, out, _ = run("status")
        assert "credentials: none" in out
        assert "connection: not signed in" in out
        assert "hermes memory setup penfield" in out

    def test_a_saved_login_that_no_longer_works_is_not_reported_as_fine(
        self, run, api: FakePenfield, hermes_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from penfield.auth import PenfieldAuth, TokenSet
        from penfield.exceptions import TokenExpiredError

        cache = hermes_home / "penfield" / "tokens.json"
        cache.parent.mkdir(parents=True)
        signed_in = TokenSet(
            "AT",
            "RT",
            expires_at=4102444800.0,
            source="oauth",
            client_id="CID",
            api_base=os.environ["PENFIELD_URL"],
        )
        cache.write_text(json.dumps(signed_in.to_dict()), encoding="utf-8")
        monkeypatch.delenv("PENFIELD_API_KEY")

        def refuse(self: PenfieldAuth, refresh_token: str) -> TokenSet:
            raise TokenExpiredError("the sign-in was revoked")

        monkeypatch.setattr(PenfieldAuth, "_refresh_oauth", refuse)

        _, out, _ = run("status")

        assert "credentials: browser sign-in" in out
        assert "connection: not signed in" in out
        assert "connection: ok" not in out

    @pytest.mark.parametrize(
        ("scopes", "level"),
        [
            ({"read"}, "read only"),
            ({"read", "write"}, "read and write"),
            ({"read", "write", "delete"}, "read, write and delete"),
        ],
    )
    def test_says_what_the_api_key_may_do(
        self, run, api: FakePenfield, scopes: set[str], level: str
    ) -> None:
        api.api_key_scopes["tm_pf_test_ak_cli"] = frozenset(scopes)
        _, out, _ = run("status")
        assert "credentials: API key" in out
        assert f"access: {level}\n" in out

    def test_says_what_a_browser_sign_in_may_do(
        self, run, api: FakePenfield, hermes_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PENFIELD_API_KEY")
        api.approve_sign_ins = True
        assert run("login")[0] == 0

        _, out, _ = run("status")

        assert "credentials: browser sign-in" in out
        assert "access: read, write and delete\n" in out
        assert "connection: ok" in out

    @pytest.mark.parametrize("command", ["status", "stats", "login", "logout", "search"])
    def test_an_unusable_setting_is_an_error_message_not_a_traceback(
        self, run, monkeypatch: pytest.MonkeyPatch, command: str
    ) -> None:
        monkeypatch.setenv("PENFIELD_ENV", "staging")
        code, out, err = run(command, *(["anything"] if command == "search" else []))
        assert code == 2
        assert "unknown PENFIELD_ENV 'staging'" in err
        assert "Traceback" not in out + err

    def test_removing_the_api_key_disconnects_the_profile(
        self, run, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run("status")  # caches tokens from the key exchange
        monkeypatch.delenv("PENFIELD_API_KEY")

        _, out, _ = run("status")

        assert "credentials: none" in out
        assert "connection: not signed in" in out


class TestSearch:
    def test_prints_id_score_and_content(self, run, api: FakePenfield) -> None:
        memory_id = api.seed_memory("The launch checklist lives in the release folder.")
        code, out, _ = run("search", "launch", "checklist")
        assert code == 0
        assert memory_id in out
        assert "The launch checklist lives in the release folder." in out

    def test_long_content_is_cut_for_the_terminal(self, run, api: FakePenfield) -> None:
        api.seed_memory("launch " + "word " * 200)
        _, out, _ = run("search", "launch")
        (line,) = out.strip().splitlines()
        assert line.endswith("...")
        assert len(line) < 300

    def test_limit_is_passed_to_the_api(self, run, api: FakePenfield) -> None:
        for n in range(5):
            api.seed_memory(f"launch note {n}")
        _, out, _ = run("search", "--limit", "2", "launch")
        assert len(out.strip().splitlines()) == 2

    def test_an_option_after_the_query_is_still_an_option(self, run, api: FakePenfield) -> None:
        run("search", "launch", "checklist", "--limit", "3")
        sent = api.calls("POST", "/search/hybrid")[-1].body
        assert sent["query"] == "launch checklist"
        assert sent["limit"] == 3

    def test_a_query_is_required(self, run) -> None:
        code, _, err = run("search")
        assert code == 2
        assert "query required" in err

    def test_api_failure_is_reported(self, run, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PENFIELD_URL", refused_url())
        code, _, err = run("search", "anything")
        assert code == 1
        assert "search failed" in err


class TestStats:
    def test_prints_the_stats_as_json(self, run, api: FakePenfield) -> None:
        api.seed_memory("one")
        code, out, _ = run("stats")
        assert code == 0
        assert json.loads(out)["total_memories"] == 1

    def test_api_failure_is_reported(self, run, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PENFIELD_URL", refused_url())
        code, _, err = run("stats")
        assert code == 1
        assert "stats failed" in err


class TestLoginLogout:
    def test_logout_clears_the_token_cache(self, run, hermes_home: Path) -> None:
        run("status")  # exchanges the key and caches tokens
        cache = hermes_home / "penfield" / "tokens.json"
        assert cache.exists()
        code, out, _ = run("logout")
        assert code == 0
        assert "logged out" in out
        assert not cache.exists()

    def test_logout_when_not_logged_in(self, run) -> None:
        code, out, _ = run("logout")
        assert code == 0
        assert "not logged in" in out

    def test_logout_says_the_api_key_is_still_in_use(self, run) -> None:
        _, out, _ = run("logout")
        assert "API key in this profile is still in use" in out

    def test_logout_without_an_api_key_says_nothing_about_one(
        self, run, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PENFIELD_API_KEY")
        _, out, _ = run("logout")
        assert out.strip() == "not logged in; nothing to clear."

    def test_logout_ends_a_browser_sign_in_an_api_key_had_set_aside(
        self, run, hermes_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from penfield.auth import PenfieldAuth, TokenSet

        cache = hermes_home / "penfield" / "tokens.json"
        cache.parent.mkdir(parents=True)
        signed_in = TokenSet("AT", "RT", expires_at=4102444800.0, source="oauth", client_id="CID")
        cache.write_text(json.dumps(signed_in.to_dict()), encoding="utf-8")
        revoked: list[str] = []

        def revoke(self: PenfieldAuth, tokens: TokenSet) -> bool:
            revoked.append(tokens.refresh_token)
            return True

        monkeypatch.setattr(PenfieldAuth, "_revoke", revoke)

        _, out, _ = run("logout")

        assert revoked == ["RT"]
        assert "the sign-in was revoked" in out
        assert not cache.exists()

    def test_login_is_not_started_while_an_api_key_is_set(
        self, run, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from penfield.auth import PenfieldAuth

        started: list[bool] = []
        monkeypatch.setattr(
            PenfieldAuth, "device_code_flow", lambda self, **_kwargs: started.append(True)
        )
        code, out, err = run("login")
        assert code == 1
        assert started == []
        assert "PENFIELD_API_KEY" in err
        assert "tm_pf_test_ak_cli" not in out + err

    def test_login_failure_is_reported(self, run, monkeypatch: pytest.MonkeyPatch) -> None:
        from penfield.auth import PenfieldAuth
        from penfield.exceptions import AuthError

        def refuse(self: PenfieldAuth, **_kwargs: object) -> None:
            raise AuthError("authorization was denied")

        monkeypatch.delenv("PENFIELD_API_KEY")
        monkeypatch.setattr(PenfieldAuth, "device_code_flow", refuse)
        code, _, err = run("login")
        assert code == 1
        assert "login failed: authorization was denied" in err

    def test_login_success_is_reported(self, run, monkeypatch: pytest.MonkeyPatch) -> None:
        from penfield.auth import PenfieldAuth

        seen: dict[str, object] = {}
        monkeypatch.delenv("PENFIELD_API_KEY")
        monkeypatch.setattr(
            PenfieldAuth, "device_code_flow", lambda self, **kwargs: seen.update(kwargs)
        )
        code, out, _ = run("login", "--client-id", "client-123")
        assert code == 0
        assert "Signed in" in out
        assert seen == {"client_id": "client-123"}

    def test_login_signs_in_through_the_browser(
        self, run, api: FakePenfield, hermes_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PENFIELD_API_KEY")
        api.approve_sign_ins = True

        code, out, _ = run("login")

        assert code == 0
        assert f"{api.root}/device?user_code=TEST-0001" in out
        assert "Signed in. This login may read, write and delete." in out
        assert "Start a new session" in out
        saved = json.loads((hermes_home / "penfield" / "tokens.json").read_text())
        assert saved["source"] == "oauth"

    def test_an_interrupted_login_is_a_message_not_a_traceback(
        self, run, hermes_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from penfield.auth import PenfieldAuth

        def interrupted(self: PenfieldAuth, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.delenv("PENFIELD_API_KEY")
        monkeypatch.setattr(PenfieldAuth, "device_code_flow", interrupted)

        code, _, err = run("login")

        assert code == 130
        assert "login cancelled" in err
        assert not (hermes_home / "penfield" / "tokens.json").exists()


class TestDispatch:
    def test_version(self, run) -> None:
        code, out, _ = run("version")
        assert (code, out.strip()) == (0, VERSION)

    def test_unknown_command(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exit_info:
            penfield_command(argparse.Namespace(penfield_command="nope"))
        assert exit_info.value.code == 2
        assert "unknown penfield command" in capsys.readouterr().err
