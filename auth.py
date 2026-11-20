# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Authentication for the Penfield API.

Two flows, both returning the same ``{access_token, refresh_token, ...}``
shape so the client does not care which produced the tokens:

* **API key exchange** (``POST /api/v2/auth/token``): the default. Sends
  the API key as a bearer header; the API returns a JWT and a refresh
  token. The API does not accept the key itself on other endpoints.
* **OAuth 2.1 device code** (RFC 8628): ``hermes penfield login``.
  Discovers the endpoints, registers a client when none is given, shows
  a link and a code, and polls the token endpoint until the user approves
  in a browser. Logging out revokes these tokens at the server (RFC 7009).
  See ADR-0005.

Tokens are cached to ``{hermes_home}/penfield/tokens.json``, created with
mode 0o600 and replaced atomically. Refresh uses the documented rotation
behavior (RFC 9700): each refresh invalidates the old refresh token and
returns a new one. Several Hermes processes share one cache (gateway, CLI,
cron runs), so before giving up on a token this module checks whether
another process has already rotated it.

One login is in use at a time (ADR-0023): the API key when one is
configured, with only the tokens made from that key, and otherwise a saved
browser sign-in.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .constants import REFRESH_SKEW_SECONDS, REQUEST_TIMEOUT, USER_AGENT
from .exceptions import AuthError, AuthTransportError, TokenExpiredError

if TYPE_CHECKING:
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
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TokenSet:
        """Reconstruct a :class:`TokenSet` from a cached dict."""
        return cls(
            access_token=str(d["access_token"]),
            refresh_token=str(d["refresh_token"]),
            expires_at=float(d["expires_at"]),
            token_type=str(d.get("token_type", "Bearer")),
            tenant_id=str(d["tenant_id"]) if d.get("tenant_id") else None,
            source=str(d.get("source", "api_key")),
            client_id=str(d["client_id"]) if d.get("client_id") else None,
            key_id=str(d["key_id"]) if d.get("key_id") else None,
        )


def browser_sign_in_saved(hermes_home: str | Path) -> bool:
    """Return True if the profile's token cache holds a browser sign-in.

    Reads one small file and nothing else, for callers that only need to
    know whether the profile can sign in without an API key. A cache of
    tokens made from an API key does not count: without that key they are
    not used.
    """
    path = Path(hermes_home) / "penfield" / "tokens.json"
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return False
    return isinstance(cached, dict) and cached.get("source") == "oauth"


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
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        """Bind config, optional home dir, and optional pre-loaded tokens."""
        self._config = config
        self._hermes_home = Path(hermes_home) if hermes_home else None
        self._tokens: TokenSet | None = tokens
        self._api_key = config.api_key
        self._key_id = key_fingerprint(config.api_key) if config.api_key else None
        self._timeout = timeout
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

    def holds_browser_sign_in(self) -> bool:
        """True if a browser sign-in is held or cached, in use here or not."""
        tokens = self._tokens or self._read_cache()
        return tokens is not None and tokens.source == "oauth"

    def get_header(self) -> str:
        """Return an ``Authorization`` header value (``Bearer <access>``)."""
        with self._lock:
            tokens = self._tokens
            if tokens is None or tokens.expired():
                tokens = self.authenticate()
            return f"{tokens.token_type} {tokens.access_token}"

    def authenticate(self) -> TokenSet:
        """Obtain a token set, refreshing or exchanging as needed.

        Cached tokens count only if they belong to the credentials in use
        (see :meth:`_load_cached`). Priority:

        1. Valid access token in memory -> return it.
        2. Valid access token another process cached -> adopt it.
        3. Refresh token present -> attempt refresh (handles rotation).
        4. API key present -> key exchange.
        5. Otherwise -> :class:`AuthError` (caller must run device flow).
        """
        with self._lock:
            if self._tokens is not None and not self._tokens.expired():
                return self._tokens
            adopted = self._adopt_cached(require_fresh=True)
            if adopted is not None:
                return adopted
            if self._tokens is not None and self._tokens.refresh_token:
                try:
                    return self._refresh(self._tokens.refresh_token)
                except TokenExpiredError:
                    adopted = self._wait_for_rotation()
                    if adopted is not None:
                        return adopted
                    # Refresh token dead; fall through to key exchange if possible.
                    self._tokens = None
            if self._api_key:
                return self._exchange_api_key(self._api_key)
            raise AuthError(
                "no credentials available: run `hermes penfield login` or set PENFIELD_API_KEY"
            )

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
            if self._tokens is None or not self._tokens.refresh_token:
                if self._api_key:
                    return self._exchange_api_key(self._api_key)
                raise TokenExpiredError(_LOGIN_AGAIN)
            try:
                return self._refresh(self._tokens.refresh_token)
            except TokenExpiredError as exc:
                adopted = self._wait_for_rotation()
                if adopted is not None:
                    return adopted
                if self._api_key:
                    self._tokens = None
                    return self._exchange_api_key(self._api_key)
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
            if self._hermes_home is not None:
                path = self._config.token_path(self._hermes_home)
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)
        if tokens is None or tokens.source != "oauth" or not tokens.client_id:
            return False
        return self._revoke(tokens)

    def _revoke(self, tokens: TokenSet) -> bool:
        """Revoke an OAuth token pair (RFC 7009). Never raises."""
        try:
            endpoint = self._discovery().get("revocation_endpoint")
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
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "User-Agent": USER_AGENT,
                    },
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
        url = self._url("/auth/token")
        body = b""  # empty body; key is in the Authorization header
        resp = _http(
            "POST", url, body=body, headers=_default_headers(api_key), timeout=self._timeout
        )
        # _http already unwrapped the envelope; resp is the token object.
        ts = self._tokens_from_exchange(resp)
        ts.key_id = key_fingerprint(api_key)
        self._tokens = ts
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
        self._tokens = ts
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
                "OAuth refresh requires the original client_id; none cached. "
                "Re-authenticate via `hermes penfield login`."
            )
        discovery = self._discovery()
        token_url = discovery["token_endpoint"]
        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._tokens.client_id,
            }
        ).encode()
        try:
            # raw_error=True so OAuth error envelopes (invalid_grant) raise
            # AuthError rather than envelope-unwrapping logic.
            resp = _http(
                "POST",
                token_url,
                body=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": USER_AGENT,
                },
                raw_error=True,
                timeout=self._timeout,
            )
        except AuthTransportError:
            raise  # the API did not answer; the refresh token may still be good
        except AuthError as exc:
            raise TokenExpiredError(str(exc)) from exc
        ts = self._tokens_from_oauth(resp)
        # Preserve the client_id across rotation (the OAuth refresh response
        # doesn't echo it back, but it remains valid for subsequent refreshes).
        ts.client_id = self._tokens.client_id
        self._tokens = ts
        self._persist()
        return ts

    # ------------------------------------------------------------------
    # OAuth 2.1 device code flow (RFC 8628): interactive login
    # ------------------------------------------------------------------
    def device_code_flow(
        self,
        *,
        client_id: str | None = None,
        scope: str = "read write offline_access",
        poll_interval_override: float | None = None,
    ) -> TokenSet:
        """Run the OAuth device code flow.

        Performs dynamic client registration when ``client_id`` is not
        supplied, then requests a device code, prints the verification URL,
        and polls until the user authorizes or the code expires.

        Args:
            client_id: Pre-registered client id. If absent, registers one.
            scope: OAuth scopes; ``offline_access`` is required for refresh.
            poll_interval_override: Tests inject this to avoid real sleeps.

        Returns:
            The token set obtained on success.

        Raises:
            AuthError: If the user denies, the code expires, or registration
                fails.
        """
        discovery = self._discovery()
        cid = client_id or self._register_client(discovery, scope)

        device_auth_url = discovery["device_authorization_endpoint"]
        token_url = discovery["token_endpoint"]

        data = urllib.parse.urlencode({"client_id": cid, "scope": scope}).encode()
        resp = _http(
            "POST",
            device_auth_url,
            body=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
        )
        device_code = resp["device_code"]
        user_code = resp.get("user_code", "")
        verification_uri = resp.get("verification_uri_complete") or resp.get(
            "verification_uri", ""
        )
        interval = float(resp.get("interval", 5))
        expires_in = float(resp.get("expires_in", 900))
        if poll_interval_override is not None:
            interval = poll_interval_override

        if verification_uri:
            # Surface to the user via stdout for the CLI; tests ignore this.
            # Flushed, so the link shows at once even when output is piped.
            print(
                f"To authorize, visit: {verification_uri}"
                + (f" (code: {user_code})" if user_code else "")
                + f"\nThe code is valid for {round(expires_in / 60)} minutes.",
                flush=True,
            )

        deadline = time.time() + expires_in
        while time.time() < deadline:
            form = urllib.parse.urlencode(
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": cid,
                }
            ).encode()
            try:
                token_resp = _http(
                    "POST",
                    token_url,
                    body=form,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "User-Agent": USER_AGENT,
                    },
                    raw_error=True,
                )
            except _OAuthPendingError:
                time.sleep(interval)
                continue
            except _OAuthSlowDownError:
                interval += 5
                time.sleep(interval)
                continue
            except _OAuthError as exc:
                raise AuthError(_DEVICE_FLOW_FAILURES.get(exc.code, str(exc))) from exc
            ts = self._tokens_from_oauth(token_resp)
            # Persist the client_id on the token set so subsequent refresh()
            # calls can satisfy the OAuth refresh spec (which requires it).
            ts.client_id = cid
            self._tokens = ts
            self._persist()
            return ts
        raise AuthError(_DEVICE_FLOW_FAILURES["expired_token"])

    def _discovery(self) -> dict[str, str]:
        """Fetch OAuth authorization-server metadata from the API host.

        The docs direct clients to ``https://api(-dev).penfield.app/.well-known/
        oauth-authorization-server``. We resolve the well-known URL from the
        configured api_base (stripping the /api/v2 prefix).
        """
        base = self._config.api_base.removesuffix("/api/v2").removesuffix(API_PREFIX)
        well_known = f"{base}/.well-known/oauth-authorization-server"
        return _http("GET", well_known, headers=_default_headers())

    def _register_client(self, discovery: dict[str, str], scope: str) -> str:
        reg_url = discovery.get("registration_endpoint")
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
        resp = _http(
            "POST",
            reg_url,
            body=body,
            headers=_default_headers(),
        )
        return str(resp["client_id"])

    # ------------------------------------------------------------------
    # Token shape normalization
    # ------------------------------------------------------------------
    @staticmethod
    def _tokens_from_exchange(data: dict[str, Any]) -> TokenSet:
        return TokenSet(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_at=time.time() + float(data.get("expires_in", 86400)),
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
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_at=time.time() + float(data.get("expires_in", 86400)),
            token_type=str(data.get("token_type", "Bearer")),
            source="oauth",
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
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            return None

    def _load_cached(self) -> TokenSet | None:
        """Return the cached token set if it belongs to the credentials in use.

        With an API key configured, only tokens made from that key are used.
        Without one, only a browser sign-in is. Anything else in the cache
        is a different login and possibly a different account: tokens of
        another key, a sign-in an API key has replaced, or tokens of a key
        that has since been removed.
        """
        cached = self._read_cache()
        if cached is None:
            return None
        if self._key_id is not None:
            return cached if cached.key_id == self._key_id else None
        return cached if cached.source == "oauth" else None

    def _adopt_cached(self, *, require_fresh: bool = False) -> TokenSet | None:
        """Take over a token another process wrote to the cache, if there is one.

        Returns the cached token set when it differs from the one held in
        memory, else ``None``. With ``require_fresh`` an expired cached
        token is ignored.
        """
        cached = self._load_cached()
        if cached is None:
            return None
        if self._tokens is not None and cached.access_token == self._tokens.access_token:
            return None
        if require_fresh and cached.expired():
            return None
        self._tokens = cached
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

from .constants import API_PREFIX  # noqa: E402  (local to module)

#: Shown when a saved sign-in has run out or been revoked.
_LOGIN_AGAIN = (
    "the saved Penfield login is no longer valid: "
    "run `hermes penfield login` or set PENFIELD_API_KEY"
)


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


def _http(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str],
    raw_error: bool = False,
    timeout: float = REQUEST_TIMEOUT,
) -> dict[str, Any]:
    """Send a request and return the parsed JSON envelope ``data`` field.

    For OAuth token endpoints the success body is the token object itself
    (no ``status`` envelope), so we return the whole object in that case
    (``raw_error=True`` path).

    Raises:
        AuthTransportError: If the server could not be reached or did not answer.
        AuthError: On a non-2xx response or an unreadable body.
    """
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode(errors="replace")
        except OSError:
            detail = ""
        finally:
            exc.close()
        # OAuth device-flow error envelopes: {"error": "authorization_pending"}
        if raw_error:
            try:
                err = json.loads(detail)
            except (json.JSONDecodeError, ValueError):
                err = {}
            err_code = str(err.get("error", "")) if isinstance(err, dict) else ""
            if err_code == "authorization_pending":
                raise _OAuthPendingError from exc
            if err_code == "slow_down":
                raise _OAuthSlowDownError from exc
            raise _OAuthError(err_code or str(exc.code)) from exc
        if exc.code >= 500:
            raise AuthTransportError(f"auth server error ({exc.code})") from exc
        refused = AuthError(f"auth request failed ({exc.code}): {detail[:200]}")
        refused.status = exc.code
        raise refused from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AuthTransportError(f"auth transport error: {exc}") from exc

    if not payload:
        return {}
    try:
        parsed = json.loads(payload.decode())
    except (json.JSONDecodeError, ValueError) as exc:
        raise AuthError("auth response was not JSON") from exc
    # OAuth token endpoint returns the token object directly.
    if raw_error or "data" not in parsed:
        return parsed
    if parsed.get("status") != "success":
        raise AuthError(f"auth response not success: {parsed!r}")
    return parsed["data"]
