# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Answers that are not what the plugin expects.

A proxy, a captive portal or a failing server can answer with anything.
Whatever arrives, the plugin raises one of its own errors with a message
a person can act on, and it never sends credentials anywhere but the
address it was configured with.
"""

from __future__ import annotations

import json
import time
import traceback
from collections.abc import Callable
from pathlib import Path

import pytest
from penfield.auth import PenfieldAuth, TokenSet
from penfield.client import PenfieldClient
from penfield.config import Environment, PenfieldConfig
from penfield.exceptions import APIError, AuthError, ConfigError, PenfieldError
from penfield.provider import PenfieldMemoryProvider

from tests.support.raw_server import RawServer, Reply, Seen

JSON = {"Content-Type": "application/json"}


def _client(server: RawServer, *, api_key: str | None = None) -> PenfieldClient:
    cfg = PenfieldConfig(env=Environment.DEV, api_key=api_key)
    cfg.api_base = server.url
    tokens = (
        None
        if api_key
        else TokenSet("access", "refresh", expires_at=time.time() + 3600, source="oauth")
    )
    return PenfieldClient(PenfieldAuth(cfg, None, tokens=tokens), cfg, sleep=lambda _s: None)


def _always(reply: Reply) -> Callable[[Seen], Reply]:
    return lambda _request: reply


UNREADABLE: dict[str, Reply] = {
    "a web page": (200, {"Content-Type": "text/html"}, b"<html>Sign in to the network</html>"),
    "a body that is cut off": (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 500\r\n\r\n"
        b'{"status": "succ'
    ),
    "bytes that are not HTTP": b"\x00\x01 not http\r\n\r\n",
    "bytes that are not text": (200, JSON, b"\xff\xfe{}"),
}

ODD_ERRORS: dict[str, Reply] = {
    "an error whose body is null": (502, JSON, b"null"),
    "an error whose body is a list": (500, JSON, b"[]"),
    "an error whose body is a string": (400, JSON, b'"bad request"'),
    "an error envelope with a string": (200, JSON, b'{"status": "error", "error": "nope"}'),
}


@pytest.mark.parametrize("answer", [*UNREADABLE, *ODD_ERRORS])
def test_a_read_that_gets_an_odd_answer_fails_with_a_penfield_error(answer: str) -> None:
    reply = {**UNREADABLE, **ODD_ERRORS}[answer]
    with RawServer(_always(reply)) as server, pytest.raises(APIError) as caught:
        _client(server).call("search_stats")
    assert str(caught.value)


@pytest.mark.parametrize("answer", list(UNREADABLE))
def test_an_unreadable_answer_to_a_read_is_tried_again(answer: str) -> None:
    replies = [UNREADABLE[answer], (200, JSON, b'{"status": "success", "data": {"n": 1}}')]
    with RawServer(lambda _request: replies.pop(0)) as server:
        assert _client(server).call("search_stats") == {"n": 1}
        assert len(server.seen) == 2


@pytest.mark.parametrize("answer", [*UNREADABLE, *ODD_ERRORS])
def test_a_write_that_gets_an_odd_answer_is_not_sent_twice(answer: str) -> None:
    """The first attempt may have been stored; repeating it would duplicate the memory."""
    with RawServer(_always({**UNREADABLE, **ODD_ERRORS}[answer])) as server:
        with pytest.raises(APIError):
            _client(server).call("memory_create", body={"content": "once"})
        assert len(server.seen) == 1


def test_an_unreadable_answer_is_named_in_the_error() -> None:
    reply = UNREADABLE["a web page"]
    with RawServer(_always(reply)) as server, pytest.raises(APIError, match="could not be read"):
        _client(server).call("memory_create", body={"content": "x"})


def test_a_tool_call_reports_an_odd_answer_as_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with RawServer(_always(ODD_ERRORS["an error whose body is null"])) as server:
        monkeypatch.setenv("PENFIELD_URL", server.url)
        provider = PenfieldMemoryProvider()
        provider.initialize("s", hermes_home=str(tmp_path), platform="cli")
        assert provider._client is not None
        provider._client._auth = _client(server)._auth
        provider._client._sleep = lambda _s: None

        result = json.loads(provider.handle_tool_call("penfield_recall", {"query": "anything"}))

        assert "502" in result["error"]
        provider.shutdown()


SIGN_IN_ANSWERS: dict[str, Reply] = {
    "an answer without tokens": (200, JSON, b'{"status": "success", "data": {"note": "none"}}'),
    "a list": (200, JSON, b"[1, 2]"),
    "a body that is cut off": b'HTTP/1.1 200 OK\r\nContent-Length: 300\r\n\r\n{"data":',
    "a web page": (200, {"Content-Type": "text/html"}, b"<html>Sign in to the network</html>"),
}


@pytest.mark.parametrize("answer", list(SIGN_IN_ANSWERS))
def test_an_odd_answer_to_a_key_exchange_fails_with_an_auth_error(answer: str) -> None:
    with RawServer(_always(SIGN_IN_ANSWERS[answer])) as server, pytest.raises(AuthError):
        _client(server, api_key="tm_pf_test_ak_unit").call("search_stats")


def test_auth_server_metadata_without_a_token_endpoint_fails_with_an_auth_error(
    tmp_path: Path,
) -> None:
    with RawServer(_always((200, JSON, b'{"issuer": "x"}'))) as server:
        cfg = PenfieldConfig(env=Environment.DEV)
        cfg.api_base = server.url
        expired = TokenSet("a", "r", expires_at=time.time() - 60, source="oauth", client_id="c")
        with pytest.raises(AuthError):
            PenfieldAuth(cfg, tmp_path, tokens=expired).get_header()
        with pytest.raises(AuthError):
            PenfieldAuth(cfg, tmp_path).device_code_flow()


# ---------------------------------------------------------------------------
# Redirects
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_is_not_followed(status: int) -> None:
    """Following it would hand the login to whatever address the answer names."""
    with RawServer(_always((200, JSON, b'{"status": "success", "data": {}}'))) as elsewhere:
        redirect: Reply = (status, {"Location": f"{elsewhere.root}/elsewhere"}, b"")
        with RawServer(_always(redirect)) as server:
            with pytest.raises(APIError, match="redirect") as caught:
                _client(server).call("search_stats")
            assert caught.value.status == status
            assert len(server.seen) == 1
        assert elsewhere.seen == []


def test_a_redirected_key_exchange_does_not_send_the_key_on() -> None:
    with RawServer(_always((200, JSON, b"{}"))) as elsewhere:
        redirect: Reply = (307, {"Location": f"{elsewhere.root}/elsewhere"}, b"")
        with RawServer(_always(redirect)) as server, pytest.raises(PenfieldError):
            _client(server, api_key="tm_pf_test_ak_unit").call("search_stats")
        assert elsewhere.seen == []


# ---------------------------------------------------------------------------
# A login that cannot be sent
# ---------------------------------------------------------------------------

SECRET = "tm_pf_test_ak_SECRETPART"

DAMAGED_KEYS: dict[str, tuple[str, str]] = {
    "an invisible character from a web page": (
        f"{SECRET[:10]}\u200b{SECRET[10:]}",
        "the character ZERO WIDTH SPACE at position 11",
    ),
    "a line break in the middle": (f"{SECRET[:10]}\n{SECRET[10:]}", "a line break at position 11"),
    "a space in the middle": (f"{SECRET[:10]} {SECRET[10:]}", "a space at position 11"),
    "a letter HTTP cannot carry as it is": (
        f"{SECRET}\u00e9",
        "the character LATIN SMALL LETTER E WITH ACUTE at position 25",
    ),
    "a control character": (f"{SECRET}\x07", "the character U+0007 at position 25"),
}


@pytest.mark.parametrize("case", list(DAMAGED_KEYS))
def test_a_damaged_api_key_is_refused_before_it_is_sent(case: str) -> None:
    """Sent as it is, it would fail in the HTTP library with an error of its own."""
    key, found = DAMAGED_KEYS[case]
    with RawServer(_always((200, JSON, b"{}"))) as server:
        with pytest.raises(AuthError) as caught:
            _client(server, api_key=key).call("search_stats")
        assert server.seen == []

    assert str(caught.value) == (
        f"the API key cannot be used: it contains {found}, which cannot be part of a key"
    )
    assert "SECRETPART" not in str(caught.value)


@pytest.mark.parametrize("damage", ["\n", "\u200b"], ids=["a line break", "not Latin-1"])
def test_a_token_that_cannot_be_sent_is_not_shown_in_the_error(damage: str) -> None:
    """The HTTP library names the header it refuses, and that header is the login."""
    cfg = PenfieldConfig(env=Environment.DEV)
    with RawServer(_always((200, JSON, b"{}"))) as server:
        cfg.api_base = server.url
        damaged = TokenSet(
            f"SECRETPART{damage}", "refresh", expires_at=time.time() + 3600, source="oauth"
        )
        client = PenfieldClient(
            PenfieldAuth(cfg, None, tokens=damaged), cfg, sleep=lambda _s: None
        )
        with pytest.raises(APIError, match="could not be sent") as caught:
            client.call("search_stats")
        assert server.seen == []

    assert "SECRETPART" not in "".join(traceback.format_exception(caught.value))


# ---------------------------------------------------------------------------
# Where requests may go
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    ["http://api.example.com/api/v2", "http://10.0.0.5:8080/api/v2", "ftp://api.example.com"],
)
def test_an_address_that_is_not_https_is_refused(tmp_path: Path, url: str) -> None:
    """Over plain HTTP the login would cross the network readable by anyone on it."""
    with pytest.raises(ConfigError, match="https"):
        PenfieldConfig.load(tmp_path, api_url_override=url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com/api/v2",
        "http://127.0.0.1:8080/api/v2",
        "http://localhost:8080/api/v2",
        "http://[::1]:8080/api/v2",
    ],
)
def test_https_and_this_machine_are_accepted(tmp_path: Path, url: str) -> None:
    assert PenfieldConfig.load(tmp_path, api_url_override=url).api_base == url
