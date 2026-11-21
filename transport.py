# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""The one place where the plugin talks HTTP.

The API client and the sign-in code both send their requests through
:func:`send`, so three rules hold for every request the plugin makes:

* **A redirect is never followed.** Every request carries a login (an API
  key, an access token or a refresh token). ``urllib`` would repeat the
  request, ``Authorization`` header included, at whatever address a
  redirect names. Here a redirect comes back as the answer it is.
* **An HTTP status is an answer, not an exception.** Callers look at
  ``Answer.status`` and decide.
* **Anything that is not a complete HTTP answer is one exception.** A
  refused connection, a timeout, a body cut off half way, bytes that are
  not HTTP and a request that cannot be put on the wire all raise
  :class:`NoAnswerError`. Its message never quotes a header.

Standard library only (ADR-0002).
"""

from __future__ import annotations

import http.client
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Mapping
    from email.message import Message


class NoAnswerError(Exception):
    """The server could not be reached, or did not send a complete answer."""


@dataclass(frozen=True)
class Answer:
    """One HTTP answer, whatever its status."""

    status: int
    body: bytes
    #: Anything with ``.get(name)``: an ``email.message.Message`` or a dict.
    headers: Mapping[str, str] | Message

    @property
    def redirect_target(self) -> str:
        """Scheme and host a redirect points to, or ``""``."""
        location = self.headers.get("Location") or ""
        if not (300 <= self.status < 400 and location):
            return ""
        parts = urlsplit(location)
        return f"{parts.scheme}://{parts.netloc}" if parts.netloc else location[:80]


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Answer every redirect with "do not follow"."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


# Proxies configured in the environment are honoured, as urlopen() does.
_OPENER = urllib.request.build_opener(_NoRedirects)


def urlopen(request: urllib.request.Request, timeout: float) -> Any:
    """Open ``request`` without following redirects. Tests replace this."""
    return _OPENER.open(request, timeout=timeout)


def send(request: urllib.request.Request, timeout: float) -> Answer:
    """Send ``request`` and return the answer.

    Raises:
        NoAnswerError: If no complete HTTP answer arrived.
    """
    try:
        with urlopen(request, timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            headers = getattr(response, "headers", None)
            return Answer(status, response.read(), headers if headers is not None else {})
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except (OSError, http.client.HTTPException):
            body = b""
        finally:
            exc.close()
        return Answer(exc.code, body, exc.headers if exc.headers is not None else {})
    except (OSError, http.client.HTTPException) as exc:
        # OSError covers URLError, timeouts and reset connections;
        # HTTPException a cut-off body and bytes that are not HTTP.
        raise NoAnswerError(" ".join(str(exc).split()) or type(exc).__name__) from exc
    except ValueError:
        # http.client refuses a header it cannot put on the wire (a line
        # break, a character outside Latin-1) and its error quotes the
        # header, which here is a login. Say so without it.
        raise NoAnswerError(
            "the request could not be sent: a header or the address holds something "
            "HTTP cannot carry"
        ) from None


def is_loopback(host: str) -> bool:
    """True if ``host`` names this machine."""
    return host.lower() in {"localhost", "127.0.0.1", "::1"}


def is_safe_address(url: str) -> bool:
    """True if a login may be sent to ``url``: HTTPS, or HTTP on this machine."""
    parts = urlsplit(url)
    if parts.scheme == "https":
        return bool(parts.hostname)
    return parts.scheme == "http" and is_loopback(parts.hostname or "")
