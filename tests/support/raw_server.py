# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""A loopback HTTP server that answers with whatever a test tells it to.

``FakePenfield`` behaves like the API. This one misbehaves on purpose: it
can send a body that is cut off, bytes that are not HTTP, or a redirect,
which is what proxies, captive portals and failing servers do.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class Seen:
    """One request the server received."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes


#: What a route returns: ``(status, headers, body)`` for a regular answer, or
#: raw bytes written to the socket as they are.
Reply = tuple[int, dict[str, str], bytes] | bytes


class RawServer:
    """Serve ``route(request)`` on a loopback port."""

    def __init__(self, route: Callable[[Seen], Reply]) -> None:
        self.seen: list[Seen] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                return

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                request = Seen(
                    self.command,
                    self.path,
                    dict(self.headers.items()),
                    self.rfile.read(length) if length else b"",
                )
                outer.seen.append(request)
                reply = route(request)
                if isinstance(reply, bytes):
                    self.wfile.write(reply)
                    self.wfile.flush()
                    self.close_connection = True
                    return
                status, headers, body = reply
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                if "Content-Length" not in headers:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle  # noqa: N815

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()

    @property
    def root(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def url(self) -> str:
        """API base URL, suitable for ``PENFIELD_URL``."""
        return f"{self.root}/api/v2"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> RawServer:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
