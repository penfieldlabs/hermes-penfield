# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Copy writes of Hermes's built-in memory tool into Penfield.

Hermes keeps its own short notes in ``MEMORY.md`` and ``USER.md``. With
``mirror_builtin_writes`` on, each change to them is repeated in Penfield so
other agents sharing the account see the same facts. See ADR-0008.

A mirrored memory carries the ``hermes-builtin`` tag. An edit or removal is
matched to it by that tag and by its exact previous text, so the mirror only
ever touches memories it created.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import TYPE_CHECKING

from .exceptions import PenfieldError, PermissionDeniedError

if TYPE_CHECKING:
    from collections.abc import Callable

    from .client import PenfieldClient

logger = logging.getLogger("penfield.mirror")

MIRROR_TAG = "hermes-builtin"
_MAX_CONTENT_CHARS = 10_000
#: Seconds the worker thread waits for more work before it exits.
_IDLE_SECONDS = 5.0


class BuiltinMirror:
    """Applies built-in memory writes to Penfield, in order, off the caller's thread.

    Hermes reports each write from inside the memory tool call. The work is
    queued and done by one background thread, so the tool call returns at
    once and an edit is never applied before the write it follows.
    """

    def __init__(
        self,
        client: PenfieldClient,
        spawn: Callable[..., threading.Thread],
    ) -> None:
        """Create an idle mirror.

        Args:
            client: API client.
            spawn: Builds the worker thread; Hermes supplies one that
                carries the session's context into it.
        """
        self._client = client
        self._spawn = spawn
        self._jobs: queue.SimpleQueue[tuple[str, str, str, str]] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        #: Text of each memory this mirror wrote, mapped to its id. Search can
        #: lag behind a write, so recent entries are matched here first.
        self._written: dict[str, str] = {}

    def submit(self, action: str, target: str, content: str, previous: str = "") -> None:
        """Queue one write.

        Args:
            action: ``add``, ``replace`` or ``remove``.
            target: Which built-in store was written: ``memory`` or ``user``.
            content: The entry after the write (empty for ``remove``).
            previous: The entry before the write, for ``replace`` and ``remove``.
        """
        with self._lock:
            self._jobs.put((action, target, content, previous))
            if self._worker is None:
                self._worker = self._spawn(self._run, name="penfield-mirror")
                self._worker.start()

    def close(self, timeout: float) -> None:
        """Give queued writes up to ``timeout`` seconds to finish."""
        worker = self._worker
        if worker is not None:
            worker.join(timeout)

    def _run(self) -> None:
        while True:
            try:
                job = self._jobs.get(timeout=_IDLE_SECONDS)
            except queue.Empty:
                with self._lock:
                    if self._jobs.empty():
                        self._worker = None
                        return
                continue
            try:
                self._apply(*job)
            except PenfieldError as exc:
                logger.warning(
                    "could not mirror a built-in memory %s to Penfield: %s", job[0], exc
                )
            except Exception:  # the worker must outlive one bad write
                logger.exception("unexpected error mirroring a built-in memory write")
            if self._jobs.empty():
                with self._lock:
                    if self._jobs.empty():
                        self._worker = None
                        return

    def _apply(self, action: str, target: str, content: str, previous: str) -> None:
        if action == "add":
            self._store(target, content)
        elif action == "replace":
            existing = self._find(previous)
            if existing and content.strip():
                self._client.call(
                    "memory_update",
                    path_params={"memory_id": existing},
                    body={"content": content[:_MAX_CONTENT_CHARS]},
                )
                self._written.pop(_normalize(previous), None)
                self._written[_normalize(content)] = existing
            else:
                self._store(target, content)
        elif action == "remove":
            existing = self._find(previous or content)
            if existing:
                self._delete(existing)
                self._written.pop(_normalize(previous or content), None)

    def _store(self, target: str, content: str) -> None:
        if not content.strip():
            return
        created = self._client.call(
            "memory_create",
            body={
                "content": content[:_MAX_CONTENT_CHARS],
                "tags": [MIRROR_TAG, f"hermes-{target}"],
            },
        )
        if isinstance(created, dict) and created.get("id"):
            self._written[_normalize(content)] = str(created["id"])

    def _find(self, text: str) -> str | None:
        """Return the id of the mirrored memory whose content is exactly ``text``."""
        wanted = _normalize(text)
        if not wanted:
            return None
        if wanted in self._written:
            return self._written[wanted]
        found = self._client.call(
            "search_hybrid",
            body={"query": text[:500], "limit": 10, "tags": MIRROR_TAG},
        )
        items = found.get("items", []) if isinstance(found, dict) else []
        for item in items:
            if isinstance(item, dict) and _normalize(str(item.get("content") or "")) == wanted:
                return str(item.get("id"))
        return None

    def _delete(self, memory_id: str) -> None:
        try:
            self._client.call("memory_delete", path_params={"memory_id": memory_id})
        except PermissionDeniedError:
            # Deleting needs an API key with the delete permission.
            logger.info(
                "Penfield kept memory %s: this API key is not allowed to delete memories",
                memory_id,
            )


def _normalize(text: str) -> str:
    return " ".join(text.split())
