# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Authentication for the Penfield API.

Two logins, both ending in the same ``{access_token, refresh_token, ...}``
shape so the client does not care which produced the tokens:

* **Browser sign-in**, the OAuth 2.1 device code flow (RFC 8628):
  ``hermes penfield login``. Discovers the endpoints, registers a client
  when none is given, shows a link and a code, and polls the token endpoint
  until the user approves in a browser. Logging out revokes these tokens at
  the server (RFC 7009). See ADR-0005.
* **API key exchange** (``POST /api/v2/auth/token``): sends the API key as
  a bearer header; the API returns a JWT and usually a refresh token. The
  API does not accept the key itself on other endpoints.

Tokens are cached to ``{hermes_home}/penfield/tokens.json``, created with
mode 0o600 and replaced atomically. Refresh uses the documented rotation
behavior (RFC 9700): each refresh invalidates the old refresh token and
returns a new one. Several Hermes processes share one cache (gateway, CLI,
cron runs), so before giving up on a token this module checks whether
another process has already rotated it.

A login is given up only when the server says it is no longer valid. An
auth server that is down, overloaded or answering nonsense says nothing
about the login, so the tokens are kept and the next request tries again.

One login is in use at a time (ADR-0023): the API key when one is
configured, with only the tokens made from that key, and otherwise a saved
browser sign-in. Tokens are used only with the API address that issued
them (ADR-0026): the cache records it, and a profile pointed at another
address starts from no login.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import transport
from .constants import (
    API_PREFIX,
    AUTH_TIMEOUT,
    DEV_HOSTS,
    PROD_HOSTS,
    REFRESH_SKEW_SECONDS,
    SIGN_IN_SCOPE,
    USER_AGENT,
)
from .exceptions import AuthError, AuthTransportError, TokenExpiredError

if TYPE_CHECKING:
    from collections.abc import Callable

    from .config import PenfieldConfig

logger = logging.getLogger(__name__)

TOKEN_FILE_MODE = 0o600
# How long to wait for another process to finish writing a rotated token.
_ROTATION_WAIT_SECONDS = 1.0


@dataclass
class TokenSet:
    """A cached token pair with its expiry.

    ``expires_at`` is an absolute ``time.time()`` epoch second, derived from
    the response ``expires_in`` at storage time (not from JWT claims, so we
    don't depend on a JWT library).
    """

    access_token: str
    refresh_token: str
    expires_at: float
    token_type: str = "Bearer"
    tenant_id: str | None = None
    # Origin of the token, so refresh routes to the correct endpoint:
    #   "api_key" -> POST /api/v2/auth/refresh (JSON body)
    #   "oauth"   -> POST {token_endpoint} (form-encoded, requires client_id)
    # See Penfield auth docs: "Refresh Request (API Key Tokens)" vs
    # "Refresh Request (OAuth Tokens)": different endpoints, different bodies.
    source: str = "api_key"
    # Required for source="oauth" refresh; unused for api_key tokens.
    client_id: str | None = None
    # Which API key the token was made from (see key_fingerprint); None for a
    # browser sign-in and for caches written before this was recorded.
    key_id: str | None = None
    # What a browser sign-in was granted, as the auth server reported it
    # (space-separated scopes); None for API key tokens and older caches.
    scope: str | None = None
    # The API address the tokens were issued for; None for caches written
    # before this was recorded.
    api_base: str | None = None

    def expired(self, *, skew: float = REFRESH_SKEW_SECONDS) -> bool:
        """True if the access token is within ``skew`` seconds of expiry."""
        return time.time() >= (self.expires_at - skew)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the token set to a JSON-friendly dict."""
        d: dict[str, Any] = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_type": self.token_type,
            "source": self.source,
        }
        if self.tenant_id is not None:
            d["tenant_id"] = self.tenant_id
        if self.client_id is not None:
            d["client_id"] = self.client_id
        if self.key_id is not None:
            d["key_id"] = self.key_id
        if self.scope is not None:
            d["scope"] = self.scope
        if self.api_base is not None:
            d["api_base"] = self.api_base
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TokenSet:
        """Reconstruct a :class:`TokenSet` from a cached dict.

        Raises:
            KeyError, ValueError: If ``d`` is not a usable token record.
        """
        if not isinstance(d, dict):
            raise ValueError("the token cache is not a JSON object")
        return cls(
            access_token=str(d["access_token"]),
            refresh_token=str(d.get("refresh_token") or ""),
            expires_at=float(d["expires_at"]),
            token_type=str(d.get("token_type", "Bearer")),
            tenant_id=str(d["tenant_id"]) if d.get("tenant_id") else None,
            source=str(d.get("source", "api_key")),
            client_id=str(d["client_id"]) if d.get("client_id") else None,
            key_id=str(d["key_id"]) if d.get("key_id") else None,
            scope=str(d["scope"]) if d.get("scope") else None,
            api_base=str(d["api_base"]) if d.get("api_base") else None,
        )


#: Penfield's own API addresses. A login made before the cache recorded its
#: address was made at one of these, unless ``PENFIELD_URL`` said otherwise.
_PENFIELD_APIS = frozenset(
    f"https://{hosts['api']}{API_PREFIX}" for hosts in (PROD_HOSTS, DEV_HOSTS)
)


def _issued_for(tokens: TokenSet, api_base: str) -> bool:
    """True if ``tokens`` may be sent to ``api_base``."""
    if tokens.api_base is not None:
        return tokens.api_base == api_base
    return api_base in _PENFIELD_APIS


def saved_browser_sign_in(hermes_home: str | Path, api_base: str | None = None) -> TokenSet | None:
    """Return the browser sign-in in the profile's token cache, if it holds one.

    Reads one small file and nothing else. A cache of tokens made from an
    API key does not count: without that key they are not used. With
    ``api_base``, neither does a sign-in made at another address.
    """
    path = Path(hermes_home) / "penfield" / "tokens.json"
    try:
        cached = TokenSet.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (KeyError, ValueError, TypeError, OSError):
        return None
    if cached.source != "oauth":
        return None
    if api_base is not None and not _issued_for(cached, api_base):
        return None
    return cached


def browser_sign_in_saved(hermes_home: str | Path, api_base: str | None = None) -> bool:
    """Return True if the profile can sign in without an API key."""
    return saved_browser_sign_in(hermes_home, api_base) is not None


def key_problem(api_key: str) -> str:
    """Say why ``api_key`` cannot be an API key, or return ``""``.

    A key travels in an HTTP header and is made of visible ASCII. Anything
    else in it got there on the way: an invisible character copied from a
    web page, a line break from a file. Sent as it is, such a key fails
    inside the HTTP library, whose error may quote the header. The answer
    here names the character and where it is, never the key.
    """
    for position, char in enumerate(api_key, 1):
        if "!" <= char <= "~":
            continue
        found = _WHITESPACE_NAMES.get(char) or (
            f"the character {unicodedata.name(char, f'U+{ord(char):04X}')}"
        )
        return f"it contains {found} at position {position}, which cannot be part of a key"
    return ""


_WHITESPACE_NAMES = {" ": "a space", "\t": "a tab", "\n": "a line break", "\r": "a line break"}


def key_fingerprint(api_key: str) -> str:
    """Return a short one-way mark of an API key.

    Stored with the tokens made from that key, so a token cached for one key
    is never used after another key has been configured.
    """
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


class PenfieldAuth:
    """Owns the current token set and the refresh-exchange logic.

    The :class:`~penfield.client.PenfieldClient` calls
    :meth:`get_header` before every request and :meth:`refresh` on a 401.
    """

    def __init__(
        self,
        config: PenfieldConfig,
        hermes_home: str | Path | None = None,
        *,
        tokens: TokenSet | None = None,
        timeout: float = AUTH_TIMEOUT,
    ) -> None:
        """Bind config, optional home dir, and optional pre-loaded tokens."""
        self._config = config
        self._hermes_home = Path(hermes_home) if hermes_home else None
        self._tokens: TokenSet | None = tokens
        self._api_key = config.api_key
        self._key_id = key_fingerprint(config.api_key) if config.api_key else None
        self._timeout = timeout
        # True once the server has refused to renew the tokens held here.
        # They are kept, so the same dead login is not tried again and again,
        # until a new login shows up in the cache.
        self._ended = False
        # The provider fetches in the background while tool calls run.
        self._lock = threading.RLock()
        # If no tokens were passed but a cache exists, load lazily on first use.
        if self._tokens is None and self._hermes_home is not None:
            cached = self._load_cached()
            if cached is not None:
                self._tokens = cached

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def has_api_key(self) -> bool:
        """True if an API key is configured."""
        return bool(self._api_key)

    def is_authenticated(self) -> bool:
        """True if we hold a usable access token (network not touched)."""
        return self._tokens is not None

    def has_saved_tokens(self) -> bool:
        """True if any token set is held or cached, in use here or not."""
        return self._tokens is not None or self._read_cache() is not None

    def access_level(self) -> str:
        """Say what the login in use may do; see :func:`access_level`."""
        return access_level(self._tokens)

    def holds_browser_sign_in(self) -> bool:
        """True if a browser sign-in is held or cached, in use here or not."""
        tokens = self._tokens or self._read_cache()
        return tokens is not None and tokens.source == "oauth"

    def get_header(self) -> str:
        """Return an ``Authorization`` header value (``Bearer <access>``)."""
        with self._lock:
            tokens = self._tokens
            if tokens is None or tokens.expired() or self._ended:
                tokens = self.authenticate()
            return f"{tokens.token_type} {tokens.access_token}"

    def authenticate(self) -> TokenSet:
        """Obtain a token set, refreshing or exchanging as needed.

        Cached tokens count only if they belong to the credentials in use
        (see :meth:`_load_cached`). Priority:

        1. Valid access token in memory -> return it.
        2. Another login in the cache (a renewal by another process, or a
           new sign-in) -> adopt it, and return it if its access token is
           still valid.
        3. Refresh token present -> attempt refresh (handles rotation).
        4. API key present -> key exchange.
        5. Otherwise -> :class:`AuthError` (caller must run device flow).

        Raises:
            AuthTransportError: If the auth server did not give a usable
                answer. The login is kept; try again later.
            TokenExpiredError: If a saved sign-in is no longer valid.
            AuthError: If there is no login at all, or the API key is refused.
        """
        with self._lock:
            if self._tokens is not None and not self._tokens.expired() and not self._ended:
                return self._tokens
            # A newer login in the cache replaces the one held here, whether
            # or not its access token has run out too: renewing with the
            # refresh token held here would send one another process has
            # used already. A login that ended here is replaced by whatever
            # else the cache holds.
            adopted = self._adopt_cached(newer_only=not self._ended)
            if adopted is not None and not adopted.expired():
                return adopted
            if self._tokens is not None and self._tokens.refresh_token and not self._ended:
                try:
                    return self._refresh(self._tokens.refresh_token)
                except TokenExpiredError:
                    adopted = self._wait_for_rotation()
                    if adopted is not None:
                        return adopted
                    # The login is over; fall through to a key exchange if possible.
                    self._ended = True
            if self._api_key:
                return self._exchange_api_key(self._api_key)
            if self._tokens is not None:
                # Held but not renewable: refused above, or it never had a refresh token.
                self._ended = True
                raise TokenExpiredError(_LOGIN_AGAIN)
            raise AuthError(_NOT_CONNECTED)

    def refresh(self) -> TokenSet:
        """Replace a token the API rejected; called by the client on a 401.

        Refresh tokens rotate (RFC 9700): each refresh invalidates the one
        used. When another Hermes process sharing this cache has already
        rotated, the token held here is dead and the new one is on disk, so
        the cache is checked before and after the refresh attempt. If the
        refresh token is permanently invalid and an API key is configured,
        fall back to a fresh key exchange.
        """
        with self._lock:
            adopted = self._adopt_cached()
            if adopted is not None:
                return adopted
            if self._tokens is None or not self._tokens.refresh_token or self._ended:
                if self._api_key:
                    return self._exchange_api_key(self._api_key)
                self._ended = self._tokens is not None
                raise TokenExpiredError(_LOGIN_AGAIN)
            try:
                return self._refresh(self._tokens.refresh_token)
            except TokenExpiredError as exc:
                adopted = self._wait_for_rotation()
                if adopted is not None:
                    return adopted
                if self._api_key:
                    return self._exchange_api_key(self._api_key)
                self._ended = True
                raise TokenExpiredError(_LOGIN_AGAIN) from exc

    def logout(self) -> bool:
        """Forget the cached tokens, and revoke a browser sign-in at the server.

        The local cache is removed whatever the server says. Tokens from an
        API-key exchange are only forgotten: they expire on their own and
        the key itself is managed in the Penfield portal.

        Returns:
            True if a browser sign-in was revoked at the server; False if
            there was none to revoke or the server could not be reached.
        """
        with self._lock:
            # The cache may hold a browser sign-in this process does not use
            # because an API key is configured; logging out ends that too.
            tokens, self._tokens = self._tokens or self._read_cache(), None
            self._ended = False
            if self._hermes_home is not None:
                path = self._config.token_path(self._hermes_home)
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)
        if tokens is None or tokens.source != "oauth" or not tokens.client_id:
            return False
        return self._revoke(tokens)

    def _revoke(self, tokens: TokenSet) -> bool:
        """Revoke an OAuth token pair (RFC 7009). Never raises.

        The tokens go back to the address that issued them, which need not
        be the one configured now. Where that is not known they are only
        forgotten.
        """
        issuer = tokens.api_base
        if issuer is None and _issued_for(tokens, self._config.api_base):
            issuer = self._config.api_base
        if issuer is None or not transport.is_safe_address(issuer):
            return False
        try:
            endpoint = _endpoint(self._discovery(issuer), "revocation_endpoint", required=False)
            if not endpoint:
                return False
            for token, hint in (
                (tokens.refresh_token, "refresh_token"),
                (tokens.access_token, "access_token"),
            ):
                form = {"token": token, "token_type_hint": hint, "client_id": tokens.client_id}
                _http(
                    "POST",
                    endpoint,
                    body=urllib.parse.urlencode(form).encode(),
                    headers=_FORM_HEADERS,
                    raw_error=True,
                    timeout=self._timeout,
                )
        except AuthError as exc:
            logger.warning("could not revoke the Penfield sign-in at the server: %s", exc)
            return False
        return True

    # ------------------------------------------------------------------
    # API key exchange: the default path
    # ------------------------------------------------------------------
    def _exchange_api_key(self, api_key: str) -> TokenSet:
        problem = key_problem(api_key)
        if problem:
            raise AuthError(f"the API key cannot be used: {problem}")
        url = self._url("/auth/token")
        body = b""  # empty body; key is in the Authorization header
        resp = _http(
            "POST", url, body=body, headers=_default_headers(api_key), timeout=self._timeout
        )
        # _http already unwrapped the envelope; resp is the token object.
        ts = self._tokens_from_exchange(resp)
        ts.key_id = key_fingerprint(api_key)
        self._tokens, self._ended = ts, False
        self._persist()
        return ts

    def _refresh(self, refresh_token: str) -> TokenSet:
        """Refresh a token, routing to the correct endpoint by origin.

        Per the Penfield auth docs there are TWO refresh paths and they are
        NOT interchangeable:

        - API-key-derived tokens -> ``POST /api/v2/auth/refresh`` (JSON body,
          ``{"refresh_token": ...}``). Returns the ``{status, data}`` envelope.
        - OAuth-derived tokens -> ``POST {token_endpoint}`` (form-encoded,
          ``grant_type=refresh_token&refresh_token=...&client_id=...``).
          Returns the bare token object.

        Routing is decided by ``self._tokens.source``. Hitting the wrong
        endpoint silently strands the user 24h after OAuth login, which is
        why this branches rather than guessing.
        """
        source = self._tokens.source if self._tokens else "api_key"
        if source == "oauth":
            return self._refresh_oauth(refresh_token)
        return self._refresh_api_key(refresh_token)

    def _refresh_api_key(self, refresh_token: str) -> TokenSet:
        """Refresh an API-key-derived token via ``/auth/refresh`` (JSON)."""
        url = self._url("/auth/refresh")
        body = json.dumps({"refresh_token": refresh_token}).encode()
        try:
            resp = _http("POST", url, body=body, headers=_default_headers(), timeout=self._timeout)
        except AuthTransportError:
            raise  # the API did not answer; the refresh token may still be good
        except AuthError as exc:
            # invalid_grant on refresh means the refresh token is dead.
            raise TokenExpiredError(str(exc)) from exc
        ts = self._tokens_from_exchange(resp)
        ts.key_id = self._tokens.key_id if self._tokens else None
        self._tokens, self._ended = ts, False
        self._persist()
        return ts

    def _refresh_oauth(self, refresh_token: str) -> TokenSet:
        """Refresh an OAuth-derived token via the OAuth ``token_endpoint``.

        Per spec ("Refresh Request (OAuth Tokens)") the OAuth refresh uses a
        form-encoded body with ``grant_type=refresh_token``, the refresh
        token, and the original ``client_id``, sent to the discovered token
        endpoint, NOT ``/auth/refresh``. The response is the bare token
        object (no envelope). Requires the cached ``client_id`` from the
        original device-code/auth-code flow.
        """
        if self._tokens is None or not self._tokens.client_id:
            # Without the client_id we can't satisfy the spec; treat as expired
            # so the caller falls back to whatever re-auth is available.
            raise TokenExpiredError(
                "the saved sign-in does not name its client. "
                "Run `hermes memory setup penfield` to sign in again."
            )
        token_url = _endpoint(self._discovery(), "token_endpoint")
        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._tokens.client_id,
            }
        ).encode()
        try:
            # raw_error=True so OAuth error envelopes (invalid_grant) raise
            # _OAuthError rather than envelope-unwrapping logic.
            resp = _http(
                "POST",
                token_url,
                body=body,
                headers=_FORM_HEADERS,
                raw_error=True,
                timeout=self._timeout,
            )
        except _OAuthError as exc:
            if exc.code in _LOGIN_OVER:
                raise TokenExpiredError(str(exc)) from exc
            # Refused for a reason that says nothing about the login itself.
            raise AuthTransportError(f"the auth server refused the renewal ({exc.code})") from exc
        ts = self._tokens_from_oauth(resp)
        # The refresh answer does not repeat the client id, and may leave out
        # the refresh token (not rotated) and the scope (unchanged).
        ts.client_id = self._tokens.client_id
        ts.refresh_token = ts.refresh_token or refresh_token
        ts.scope = ts.scope or self._tokens.scope
        self._tokens, self._ended = ts, False
        self._persist()
        return ts

    # ------------------------------------------------------------------
    # OAuth 2.1 device code flow (RFC 8628): interactive login
    # ------------------------------------------------------------------
    def device_code_flow(
        self,
        *,
        client_id: str | None = None,
        scope: str = SIGN_IN_SCOPE,
        poll_interval_override: float | None = None,
        say: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> TokenSet:
        """Run the OAuth device code flow.

        Performs dynamic client registration when ``client_id`` is not
        supplied, then requests a device code, shows the verification URL,
        and polls until the user authorizes or the code expires. An auth
        server that stumbles while the user is in the browser does not end
        the wait; one that keeps failing is named, while waiting and in the
        error if the code runs out. A browser sign-in the profile already
        had is revoked once the new one is stored, since nothing can reach
        it afterwards.

        Args:
            client_id: Pre-registered client id. If absent, registers one.
            scope: OAuth scopes; ``offline_access`` is required for refresh.
            poll_interval_override: Tests inject this to avoid real sleeps.
            say: Shows one line to the user; ``print`` by default.
            sleep: Pauses between polls; ``time.sleep`` by default.

        Returns:
            The token set obtained on success.

        Raises:
            AuthError: If the user denies, the code expires, or registration
                fails.
        """
        discovery = self._discovery()
        device_auth_url = _endpoint(discovery, "device_authorization_endpoint")
        token_url = _endpoint(discovery, "token_endpoint")
        cid = client_id or self._register_client(discovery, scope)

        data = urllib.parse.urlencode({"client_id": cid, "scope": scope}).encode()
        resp = _http(
            "POST", device_auth_url, body=data, headers=_FORM_HEADERS, timeout=self._timeout
        )
        device_code = _field(resp, "device_code")
        user_code = str(resp.get("user_code") or "")
        verification_uri = str(
            resp.get("verification_uri_complete") or resp.get("verification_uri") or ""
        )
        interval = _seconds(resp.get("interval"), 5.0)
        expires_in = _seconds(resp.get("expires_in"), 900.0)
        if poll_interval_override is not None:
            interval = poll_interval_override

        # Flushed, so the link shows at once even when output is piped.
        show = say or (lambda line: print(line, flush=True))
        if verification_uri:
            show(
                f"To authorize, visit: {verification_uri}"
                + (f" (code: {user_code})" if user_code else "")
            )
            show(f"The code is valid for {round(expires_in / 60)} minutes.")

        form = urllib.parse.urlencode(
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": cid,
            }
        ).encode()
        pause = sleep or time.sleep
        deadline = time.time() + expires_in
        # Polls in a row the auth server gave no usable answer to, and why.
        unanswered, problem = 0, ""
        while time.time() < deadline:
            try:
                token_resp = _http(
                    "POST",
                    token_url,
                    body=form,
                    headers=_FORM_HEADERS,
                    raw_error=True,
                    timeout=self._timeout,
                )
            except _OAuthPendingError:
                # Not approved yet.
                unanswered = 0
                pause(interval)
                continue
            except AuthTransportError as exc:
                # No usable answer this time. The code is still good, so the
                # wait goes on; past a stumble or two the user is told why
                # nothing happens.
                unanswered, problem = unanswered + 1, str(exc)
                if unanswered == _UNANSWERED_POLLS_BEFORE_SAYING:
                    show(f"The sign-in server is not answering: {problem}. Still trying.")
                pause(interval)
                continue
            except _OAuthSlowDownError:
                unanswered = 0
                interval += 5
                pause(interval)
                continue
            except _OAuthError as exc:
                raise AuthError(_DEVICE_FLOW_FAILURES.get(exc.code, str(exc))) from exc
            ts = self._tokens_from_oauth(token_resp)
            # Persist the client_id on the token set so subsequent refresh()
            # calls can satisfy the OAuth refresh spec (which requires it).
            ts.client_id = cid
            ts.scope = ts.scope or scope
            replaced = self._sign_in_to_replace()
            self._tokens, self._ended = ts, False
            self._persist()
            if replaced is not None:
                self._revoke(replaced)
            return ts
        if unanswered:
            raise AuthError(_NO_ANSWER_UNTIL_EXPIRY.format(problem=problem))
        raise AuthError(_DEVICE_FLOW_FAILURES["expired_token"])

    def _sign_in_to_replace(self) -> TokenSet | None:
        """Return the browser sign-in a new one is about to overwrite, if any.

        The cache is asked first: another process may have renewed the
        sign-in since this one loaded it.
        """
        tokens = self._read_cache() or self._tokens
        if tokens is None or tokens.source != "oauth" or not tokens.client_id:
            return None
        return tokens

    def _discovery(self, api_base: str | None = None) -> dict[str, Any]:
        """Fetch OAuth authorization-server metadata from the API host.

        The docs direct clients to ``https://api(-dev).penfield.app/.well-known/
        oauth-authorization-server``. We resolve the well-known URL from the
        api_base (the configured one by default), stripping the /api/v2 prefix.
        """
        base = (api_base or self._config.api_base).removesuffix(API_PREFIX)
        well_known = f"{base}/.well-known/oauth-authorization-server"
        return _http("GET", well_known, headers=_default_headers(), timeout=self._timeout)

    def _register_client(self, discovery: dict[str, Any], scope: str) -> str:
        reg_url = _endpoint(discovery, "registration_endpoint", required=False)
        if not reg_url:
            raise AuthError(
                "no client_id given and server offers no dynamic registration; provide a client_id"
            )
        body = json.dumps(
            {
                "client_name": "hermes-penfield",
                "redirect_uris": ["http://localhost:0/callback"],
                "grant_types": [
                    "urn:ietf:params:oauth:grant-type:device_code",
                    "refresh_token",
                ],
                "token_endpoint_auth_method": "none",
                "scope": scope,
            }
        ).encode()
        resp = _http("POST", reg_url, body=body, headers=_default_headers(), timeout=self._timeout)
        return _field(resp, "client_id")

    # ------------------------------------------------------------------
    # Token shape normalization
    # ------------------------------------------------------------------
    @staticmethod
    def _tokens_from_exchange(data: dict[str, Any]) -> TokenSet:
        return TokenSet(
            access_token=_field(data, "access_token"),
            # The API may answer without a refresh token; the key renews then.
            refresh_token=str(data.get("refresh_token") or ""),
            expires_at=time.time() + _seconds(data.get("expires_in"), 86400.0),
            token_type=str(data.get("token_type", "Bearer")),
            tenant_id=str(data["tenant_id"]) if data.get("tenant_id") else None,
            source="api_key",
        )

    @staticmethod
    def _tokens_from_oauth(data: dict[str, Any]) -> TokenSet:
        # source="oauth" so refresh() routes to the OAuth token endpoint
        # (not /auth/refresh). client_id is set by the caller; the factory
        # doesn't see it because the OAuth token response doesn't echo it.
        return TokenSet(
            access_token=_field(data, "access_token"),
            # Present only when ``offline_access`` was granted.
            refresh_token=str(data.get("refresh_token") or ""),
            expires_at=time.time() + _seconds(data.get("expires_in"), 86400.0),
            token_type=str(data.get("token_type", "Bearer")),
            source="oauth",
            scope=str(data["scope"]) if data.get("scope") else None,
        )

    # ------------------------------------------------------------------
    # Cache persistence
    # ------------------------------------------------------------------
    def _persist(self) -> None:
        """Write the token cache: private from creation, replaced atomically.

        Other processes read this file at any moment, so it must never be
        visible half-written or with wider permissions than its final ones.
        A write failure is logged and otherwise ignored: the tokens stay
        usable in memory for this process.
        """
        if self._hermes_home is None or self._tokens is None:
            return
        self._tokens.api_base = self._config.api_base
        path = self._config.token_path(self._hermes_home)
        tmp_name = ""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # mkstemp creates the file with mode 0o600.
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tokens-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._tokens.to_dict(), fh)
            os.replace(tmp_name, path)
        except OSError as exc:
            logger.warning("could not write the Penfield token cache: %s", exc)
            if tmp_name:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)

    def _read_cache(self) -> TokenSet | None:
        """Return whatever token set is on disk, usable here or not."""
        if self._hermes_home is None:
            return None
        path = self._config.token_path(self._hermes_home)
        try:
            return TokenSet.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (KeyError, ValueError, TypeError, OSError):
            # Missing, unreadable or not a token record: the same as no cache.
            return None

    def _load_cached(self) -> TokenSet | None:
        """Return the cached token set if it belongs to the credentials in use.

        With an API key configured, only tokens made from that key are used.
        Without one, only a browser sign-in is. Anything else in the cache
        is a different login and possibly a different account: tokens of
        another key, a sign-in an API key has replaced, or tokens of a key
        that has since been removed. Tokens issued for another address are
        not this profile's login either, and are never sent to this one.
        """
        cached = self._read_cache()
        if cached is None or not _issued_for(cached, self._config.api_base):
            return None
        if self._key_id is not None:
            return cached if cached.key_id == self._key_id else None
        return cached if cached.source == "oauth" else None

    def _adopt_cached(self, *, newer_only: bool = False) -> TokenSet | None:
        """Take over a token another process wrote to the cache, if there is one.

        Returns the cached token set when it differs from the one held in
        memory, else ``None``. Its access token may have run out; its
        refresh token is then the one to renew with.

        With ``newer_only`` a cached set that runs out before the one held
        is left alone. That is not another process's work: it is what this
        process could not replace when the cache could not be written.
        """
        cached = self._load_cached()
        if cached is None:
            return None
        if self._tokens is not None:
            if cached.access_token == self._tokens.access_token:
                return None
            if newer_only and cached.expires_at < self._tokens.expires_at:
                return None
        self._tokens, self._ended = cached, False
        return cached

    def _wait_for_rotation(self) -> TokenSet | None:
        """After a rejected refresh, wait briefly for the winner's new token.

        Two processes can refresh with the same token at the same moment.
        The API accepts one; the other gets here and picks up the result as
        soon as the first process has written it.
        """
        deadline = time.monotonic() + _ROTATION_WAIT_SECONDS
        while True:
            adopted = self._adopt_cached()
            if adopted is not None or time.monotonic() >= deadline:
                return adopted
            time.sleep(0.1)

    def _url(self, path: str) -> str:
        return f"{self._config.api_base}{path}"


# ---------------------------------------------------------------------------
# Internal: minimal stdlib HTTP for the auth module.
# ---------------------------------------------------------------------------
# Auth has slightly different needs from the client (form-encoded bodies,
# raw OAuth error envelopes) so it ships its own small helper rather than
# importing the client (which would create a circular dependency).

#: Shown when a saved sign-in has run out or been revoked.
_LOGIN_AGAIN = (
    "the saved Penfield sign-in is no longer valid (it ran out or was revoked): "
    "run `hermes memory setup penfield` to sign in again"
)

#: Shown when a profile has no login at all.
_NOT_CONNECTED = (
    "Penfield is not connected: run `hermes memory setup penfield` to sign in "
    "with your browser or to enter an API key"
)

#: Answers of the token endpoint that mean the login itself is over (RFC 6749
#: section 5.2). Anything else it refuses with says nothing about the login.
_LOGIN_OVER = frozenset({"invalid_grant", "invalid_client", "unauthorized_client"})

_FORM_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
    "User-Agent": USER_AGENT,
}


class _OAuthError(AuthError):
    """An OAuth endpoint refused a request; ``code`` is its ``error`` value."""

    def __init__(self, code: str) -> None:
        super().__init__(f"OAuth error: {code}")
        self.code = code


#: What a refused device-code poll means for the person signing in. Penfield
#: answers a code that has run out with ``invalid_grant``; RFC 8628 names it
#: ``expired_token``.
_DEVICE_FLOW_FAILURES = {
    "expired_token": "the code expired before it was approved; run the login again",
    "invalid_grant": "the code expired before it was approved; run the login again",
    "access_denied": "the sign-in was refused in the browser",
}

#: Shown when the code ran out while the auth server gave no usable answer:
#: the user may well have approved it.
_NO_ANSWER_UNTIL_EXPIRY = (
    "the code ran out while the sign-in server was not answering: {problem}; run the login again"
)

#: How many polls in a row may go unanswered before the user is told.
_UNANSWERED_POLLS_BEFORE_SAYING = 3


class _OAuthPendingError(Exception):
    """RFC 8628 ``authorization_pending``: keep polling."""


class _OAuthSlowDownError(Exception):
    """RFC 8628 ``slow_down``: increase interval."""


def _default_headers(auth_value: str | None = None) -> dict[str, str]:
    """Build the default header set, optionally adding a bearer value."""
    h: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if auth_value:
        bearer = auth_value if auth_value.startswith("Bearer ") else f"Bearer {auth_value}"
        h["Authorization"] = bearer
    return h


def _field(answer: dict[str, Any], name: str) -> str:
    """Return a field an auth answer must carry.

    Raises:
        AuthTransportError: If it is missing. Whatever answered was not the
            auth server doing its job, which says nothing about the login.
    """
    value = answer.get(name)
    if not isinstance(value, str) or not value:
        raise AuthTransportError(f"the auth server's answer held no {name}")
    return value


def _seconds(value: object, default: float) -> float:
    """Read a number of seconds from an auth answer, or use ``default``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value) if value >= 0 else default


def _endpoint(discovery: dict[str, Any], name: str, *, required: bool = True) -> str:
    """Return an endpoint from the auth server's metadata.

    Raises:
        AuthTransportError: If a required endpoint is missing, or the address
            is one a login must not be sent to (not HTTPS, not this machine).
    """
    url = discovery.get(name)
    if not isinstance(url, str) or not url:
        if required:
            raise AuthTransportError(f"the auth server's metadata names no {name}")
        return ""
    if not transport.is_safe_address(url):
        raise AuthTransportError(f"the auth server's {name} is not an https address")
    return url


def _http(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str],
    raw_error: bool = False,
    timeout: float = AUTH_TIMEOUT,
) -> dict[str, Any]:
    """Send a request and return the parsed JSON envelope ``data`` field.

    For OAuth endpoints (``raw_error=True``) the success body is the answer
    itself, without a ``status`` envelope, and a refusal carries its reason
    in an ``error`` field.

    Raises:
        AuthTransportError: If no usable answer arrived: the server could
            not be reached, failed (5xx), was too busy (429), redirected, or
            sent something that is not the expected JSON. None of that says
            anything about the login.
        AuthError: If the server refused the request.
    """
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        answer = transport.send(req, timeout)
    except transport.NoAnswerError as exc:
        raise AuthTransportError(f"auth transport error: {exc}") from exc

    status = answer.status
    if status >= 500 or status == 429:
        raise AuthTransportError(f"auth server error ({status})")
    if answer.redirect_target:
        raise AuthTransportError(
            f"unexpected redirect ({status}) to {answer.redirect_target}; not followed"
        )
    if status >= 300:
        detail = answer.body.decode(errors="replace")
        if raw_error:
            # OAuth error envelopes: {"error": "authorization_pending"}
            try:
                err = json.loads(detail)
            except ValueError:
                err = {}
            err_code = str(err.get("error") or "") if isinstance(err, dict) else ""
            if err_code == "authorization_pending":
                raise _OAuthPendingError
            if err_code == "slow_down":
                raise _OAuthSlowDownError
            if err_code in {"server_error", "temporarily_unavailable"}:
                raise AuthTransportError(f"auth server error ({err_code})")
            raise _OAuthError(err_code or str(status))
        refused = AuthError(f"auth request failed ({status}): {' '.join(detail.split())[:200]}")
        refused.status = status
        raise refused

    if not answer.body:
        return {}
    try:
        parsed = json.loads(answer.body.decode())
    except ValueError as exc:
        raise AuthTransportError("the auth server's answer could not be read") from exc
    if not isinstance(parsed, dict):
        raise AuthTransportError("the auth server's answer could not be read")
    # OAuth endpoints answer with the object itself.
    if raw_error or "data" not in parsed:
        return parsed
    data = parsed["data"]
    if parsed.get("status") != "success" or not isinstance(data, dict):
        raise AuthTransportError("the auth server's answer could not be read")
    return data


_ACCESS_LEVELS = {
    frozenset({"read"}): "read only",
    frozenset({"read", "write"}): "read and write",
    frozenset({"read", "write", "delete"}): "read, write and delete",
}


def granted_scopes(tokens: TokenSet) -> frozenset[str] | None:
    """Return what ``tokens`` may do, or ``None`` if they do not say.

    A browser sign-in records the scope the auth server granted. An access
    token made from an API key carries a ``scopes`` claim. The claim is read
    for display only; the API enforces it.
    """
    if tokens.scope:
        return frozenset(tokens.scope.split())
    parts = tokens.access_token.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except ValueError:
        return None
    if not isinstance(claims, dict):
        return None
    found = claims.get("scopes", claims.get("scope"))
    if isinstance(found, str):
        return frozenset(found.split())
    if isinstance(found, list) and all(isinstance(item, str) for item in found):
        return frozenset(found)
    return None


def access_level(tokens: TokenSet | None) -> str:
    """Say in words what a login may do: ``read only``, ..., or ``unknown``."""
    scopes = granted_scopes(tokens) if tokens is not None else None
    if scopes is None:
        return "unknown"
    return _ACCESS_LEVELS.get(scopes & {"read", "write", "delete"}, "unknown")
