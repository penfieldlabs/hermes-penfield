# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""The Hermes ``MemoryProvider`` for Penfield.

Subclasses Hermes Agent's ``agent.memory_provider.MemoryProvider`` so every
hook Hermes defines exists on the provider, and so Hermes can load it in its
plugin host as well as in-process. When Hermes is not importable (unit tests,
linters) a plain base class stands in.

Two rules shape this module:

* **Never stall the agent.** Hermes calls ``system_prompt_block`` at session
  start, ``prefetch`` before every turn and ``on_pre_compress`` before a
  compression, synchronously and without a time limit of its own. Each
  does its network work on a background thread and stops waiting at a
  deadline.
* **Never raise into Hermes.** A memory failure degrades to "no memory this
  turn"; tool calls report errors as JSON the model can read.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from . import setup_wizard
from .auth import PenfieldAuth, browser_sign_in_saved
from .client import PenfieldClient
from .config import PenfieldConfig, default_hermes_home, read_setting
from .constants import (
    API_KEYS_URL,
    BRIEFING_DEADLINE,
    BRIEFING_TIMEOUT,
    CHECKPOINT_DEADLINE,
    RECALL_DEADLINE,
    RECALL_TIMEOUT,
)
from .exceptions import PenfieldError
from .mirror import BuiltinMirror
from .tools import PENFIELD_TOOL_SCHEMAS, dispatch, save_checkpoint, search_memories

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    _ProviderBase = object

    def _spawn_thread(target: Callable[[], None], *, name: str) -> threading.Thread: ...

else:
    try:
        from agent.memory_provider import MemoryProvider as _ProviderBase
        from agent.memory_provider import spawn_context_thread as _spawn_thread
    except ImportError:  # Hermes Agent is not installed here

        class _ProviderBase:
            """Stand-in base class for environments without Hermes Agent."""

        def _spawn_thread(target: Callable[[], None], *, name: str) -> threading.Thread:
            return threading.Thread(target=target, name=name, daemon=True)


logger = logging.getLogger("penfield.provider")

TOOL_GUIDANCE = (
    "You have access to Penfield persistent memory via the "
    "penfield_* tools. Memories persist across sessions and form a "
    "queryable knowledge graph. Use penfield_store to save important "
    "decisions/preferences/context, penfield_recall for semantic "
    "search of prior context, penfield_connect to link related "
    "memories, and penfield_explore to traverse the graph. Do not "
    "store secrets."
)

#: Seconds a session end waits for queued mirror writes before moving on.
MIRROR_DRAIN_SECONDS = 3.0

#: Characters of each memory shown by auto-recall. Enough to carry the point;
#: the full id is shown so the agent can fetch the rest.
AUTO_RECALL_ITEM_CHARS = 500


class _Deferred:
    """A value computed on a background thread and awaited with a deadline.

    ``result(timeout)`` returns the value once it is ready, or ``None`` if
    the work has not finished in time or has failed. The thread keeps
    running after a timeout, so a slow answer is still there for a later
    call.
    """

    def __init__(
        self, work: Callable[[], Any], *, name: str, failure_level: int = logging.DEBUG
    ) -> None:
        self._work = work
        self._name = name
        self._failure_level = failure_level
        self._done = threading.Event()
        self._value: Any = None
        self._error: Exception | None = None
        _spawn_thread(self._run, name=name).start()

    def _run(self) -> None:
        try:
            self._value = self._work()
        except Exception as exc:  # background work must never raise
            self._error = exc
            logger.log(self._failure_level, "%s failed: %s", self._name, exc)
        finally:
            self._done.set()

    def result(self, timeout: float) -> Any:
        self._done.wait(timeout)
        return self._value if self._done.is_set() else None

    def problem(self) -> str:
        """Say why there is no value yet, or return ``""`` if the work succeeded."""
        if not self._done.is_set():
            return "no answer in time"
        return str(self._error) if self._error is not None else ""


# What `hermes memory setup` asks for. The key is a secret, so Hermes stores
# it in the profile's .env and never passes it to save_config. Everything
# else has a working default; see the README for the optional settings.
PENFIELD_CONFIG_SCHEMA: list[dict[str, Any]] = [
    {
        "key": "api_key",
        "description": "Penfield API key",
        "secret": True,
        "required": False,
        "env_var": "PENFIELD_API_KEY",
        "url": API_KEYS_URL,
    },
]


class PenfieldMemoryProvider(_ProviderBase):
    """Penfield memory provider for Hermes Agent.

    Constructed by Hermes (or directly in tests). Call :meth:`initialize`
    once with the session context before any tool dispatch.
    """

    def __init__(self) -> None:
        """Initialize an unbound provider; call :meth:`initialize` next."""
        self._hermes_home: str = ""
        self._session_id: str = ""
        self._agent_context: str = "primary"
        self._config: PenfieldConfig | None = None
        self._auth: PenfieldAuth | None = None
        self._client: PenfieldClient | None = None
        self._briefing: _Deferred | None = None
        self._briefing_text: str = ""
        self._briefing_problem_logged: bool = False
        self._recalled: int = 0
        self._mirror: BuiltinMirror | None = None

    # ------------------------------------------------------------------
    # Identity / availability (no network)
    # ------------------------------------------------------------------
    @property
    def name(self) -> str:
        """Return the provider identifier."""
        return "penfield"

    def is_available(self) -> bool:
        """Return True if credentials are present; never makes a network call.

        Hermes calls this before :meth:`initialize`, so the token cache is
        looked up under the active profile's home as well as the bound one.
        """
        if read_setting("PENFIELD_API_KEY"):
            return True
        homes = [self._hermes_home, default_hermes_home()]
        if any(h and browser_sign_in_saved(h) for h in homes):
            return True
        return bool(self._auth and self._auth.is_authenticated())

    def unavailable_reason(self) -> str:
        """Say what is missing when :meth:`is_available` is False."""
        return (
            "No Penfield credentials were found. Run `hermes memory setup` and enter an "
            f"API key (create one at {API_KEYS_URL})."
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def initialize(self, session_id: str, **kwargs: Any) -> None:
        """Bind the provider to a session and start loading the briefing.

        Args:
            session_id: Hermes session id.
            **kwargs: ``hermes_home`` selects the profile; ``agent_context``
                says who is running (``primary``, ``subagent``, ``cron``,
                ``flush``). Anything else is ignored, so new Hermes
                arguments never break the provider.
        """
        self._session_id = session_id
        self._hermes_home = str(kwargs.get("hermes_home") or "")
        self._agent_context = str(kwargs.get("agent_context") or "primary")
        self._config = PenfieldConfig.load(self._hermes_home or None)
        self._auth = PenfieldAuth(self._config, self._hermes_home or None)
        self._client = PenfieldClient(self._auth, self._config)
        self._briefing_text = ""
        self._briefing_problem_logged = False
        self._mirror = BuiltinMirror(self._client, _spawn_thread)
        # Session start reads the briefing next; fetch it now so it is ready.
        self._briefing = _Deferred(self._fetch_briefing, name="penfield-briefing")

    def on_session_switch(
        self, new_session_id: str, *, parent_session_id: str = "", **kwargs: Any
    ) -> None:
        """Follow Hermes to a new session id (``/new``, ``/resume``, compression)."""
        self._session_id = new_session_id

    def on_session_end(self, messages: object = None) -> None:
        """Let queued mirror writes finish; nothing else is buffered."""
        if self._mirror is not None:
            self._mirror.close(MIRROR_DRAIN_SECONDS)

    def shutdown(self) -> None:
        """Release the session. Background fetches are daemon threads."""
        if self._mirror is not None:
            self._mirror.close(MIRROR_DRAIN_SECONDS)
        self._briefing = None

    # ------------------------------------------------------------------
    # Tool surface
    # ------------------------------------------------------------------
    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return the penfield_* tool schemas for the host."""
        return [dict(t) for t in PENFIELD_TOOL_SCHEMAS]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **_: object) -> str:
        """Dispatch a tool call and return a JSON-string result.

        Failures come back as ``{"error": ...}`` so the model can read what
        went wrong and correct the call.
        """
        if self._client is None:
            return json.dumps({"error": "Penfield is not initialized", "tool": tool_name})
        try:
            return dispatch(self._client, tool_name, args)
        except PenfieldError as exc:
            logger.warning("tool %s failed: %s", tool_name, exc)
            return json.dumps({"error": str(exc), "tool": tool_name})
        except (ValueError, TypeError) as exc:
            return json.dumps({"error": str(exc), "tool": tool_name})

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------
    def get_config_schema(self) -> list[dict[str, Any]]:
        """Return the config schema for ``hermes memory setup``."""
        return [dict(c) for c in PENFIELD_CONFIG_SCHEMA]

    @property
    def post_setup(self) -> Callable[[str, dict[str, Any]], None]:
        """Hermes's hook for ``hermes memory setup``; see :mod:`setup_wizard`.

        When a provider has this attribute Hermes hands the whole setup to
        it. It exists only where the hook can ask a question. Elsewhere
        reading it raises :class:`AttributeError`, as a missing attribute
        does, and Hermes asks its own schema questions.
        """
        hook = setup_wizard.hook(self.name)
        if hook is None:
            raise AttributeError("post_setup")
        return hook

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        """Persist non-secret config values to ``penfield/config.json``."""
        cfg = PenfieldConfig.load(hermes_home)
        # Apply non-secret values; persist via config (secrets stay in env).
        if values.get("penfield_env"):
            from .config import resolve_environment

            cfg.env = resolve_environment(str(values["penfield_env"]))
            cfg.reapply_hosts()
        if values.get("penfield_url"):
            cfg.api_base = str(values["penfield_url"]).rstrip("/")
        cfg.save(hermes_home)

    def get_status_config(self, provider_config: object = None) -> dict[str, Any]:
        """Describe the active configuration for ``hermes memory status``.

        Works before :meth:`initialize` and never includes a secret.
        """
        home = self._hermes_home or default_hermes_home()
        cfg = self._config or PenfieldConfig.load(home)
        if cfg.api_key:
            credentials = "API key"
        elif browser_sign_in_saved(home):
            credentials = "saved login"
        else:
            credentials = "none"
        recall = (
            f"on (up to {cfg.recall_limit} memories, minimum score {cfg.recall_threshold})"
            if cfg.auto_recall
            else "off"
        )
        return {
            "environment": cfg.env.value,
            "api": cfg.api_base,
            "credentials": credentials,
            "auto_recall": recall,
            "checkpoint_before_compression": "on" if cfg.pre_compress_save else "off",
            "mirror_builtin_memory": "on" if cfg.mirror_builtin_writes else "off",
        }

    # ------------------------------------------------------------------
    # System prompt integration
    # ------------------------------------------------------------------
    def system_prompt_block(self) -> str:
        """Return the awakening briefing followed by tool guidance.

        The briefing (persona, custom instructions) is fetched once per
        session, in the background, starting at :meth:`initialize`. This
        waits for it up to the briefing deadline. If Penfield does not
        answer in time the tool guidance alone is returned. Hermes keeps the
        prompt it built for the rest of the session, so a late briefing is
        only picked up if the prompt is built again.
        """
        if not self._briefing_text and self._briefing is not None:
            self._briefing_text = str(self._briefing.result(BRIEFING_DEADLINE) or "")
            problem = self._briefing.problem()
            if problem and not self._briefing_problem_logged:
                # Without this the user only sees an agent that has lost its
                # persona and instructions, with nothing to say why.
                self._briefing_problem_logged = True
                logger.warning(
                    "The Penfield briefing was not loaded (%s); this session runs without it.",
                    problem,
                )
        if self._briefing_text:
            return f"{self._briefing_text}\n\n---\n\n{TOOL_GUIDANCE}"
        return TOOL_GUIDANCE

    def _fetch_briefing(self) -> str:
        assert self._client is not None
        data = self._client.call("personality_awakening", timeout=BRIEFING_TIMEOUT, retries=1)
        return str(data.get("briefing") or "") if isinstance(data, dict) else ""

    # ------------------------------------------------------------------
    # Auto-recall
    # ------------------------------------------------------------------
    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Hermes calls this after every turn. Recall runs in :meth:`prefetch`."""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Recall memories relevant to the coming turn (best effort).

        One search attempt with a short timeout, awaited up to the recall
        deadline. Any failure, or no answer in time, returns an empty string
        so the turn goes ahead without recalled context.
        """
        self._recalled = 0
        client, config = self._client, self._config
        if client is None or config is None or not config.auto_recall or not query.strip():
            return ""
        args = {
            "query": query,
            # Ask for spares: saved checkpoints match many queries and are left out.
            "limit": min(config.recall_limit * 2, 100),
            "min_score_threshold": config.recall_threshold,
        }
        search = _Deferred(
            lambda: search_memories(
                client,
                args,
                max_chars=AUTO_RECALL_ITEM_CHARS,
                timeout=RECALL_TIMEOUT,
                retries=0,
            ),
            name="penfield-recall",
        )
        found = search.result(RECALL_DEADLINE)
        items = found.get("items", []) if isinstance(found, dict) else []
        # A checkpoint is a list of memory ids for penfield_restore_context,
        # not something to read in passing.
        shown = [it for it in items if it.get("memory_type") != "checkpoint"]
        shown = shown[: config.recall_limit]
        if not shown:
            return ""
        self._recalled = len(shown)
        lines = ["[Penfield memory: recalled context]"]
        lines += [_recall_line(it) for it in shown]
        return "\n".join(lines)[: config.max_prefetch_chars]

    def recall_status(self) -> Any:
        """Tell Hermes what the last auto-recall injected, for its recall indicator."""
        if not self._recalled:
            return None
        try:
            from agent.memory_provider import RecallStatus  # type: ignore[import-not-found]
        except ImportError:
            return None
        return RecallStatus(provider_label="Penfield", count=self._recalled)

    # ------------------------------------------------------------------
    # Turn sync
    # ------------------------------------------------------------------
    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: object = None,
        **_: object,
    ) -> None:
        """Do nothing: Penfield stores what the agent chooses to remember.

        Turns are not mirrored. Memories come from explicit ``penfield_store``
        calls, and long sessions are captured by :meth:`on_pre_compress`.
        """

    # ------------------------------------------------------------------
    # Pre-compress checkpoint. See ADR-0012.
    # ------------------------------------------------------------------
    def on_pre_compress(self, messages: Sequence[object]) -> str:
        """Save a checkpoint before Hermes compresses the context window.

        The checkpoint links the memories relevant to the conversation so
        far. Returns a one-line pointer for Hermes's compression summary, so
        the agent can still find the checkpoint afterwards, or an empty
        string when nothing was saved in time. The save runs in the
        background and is awaited up to the checkpoint deadline, so a slow
        API delays the compression by that much at most. Only the primary
        agent writes: subagents, cron runs and flush passes are skipped.
        """
        if self._client is None or not self._config or not self._config.pre_compress_save:
            return ""
        if self._agent_context != "primary":
            return ""
        tail = list(messages[-20:]) if messages else []
        if not tail:
            return ""
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        name = f"pre-compress-{self._session_id}-{stamp}"
        client, description = self._client, _describe_window(self._session_id, tail)
        save = _Deferred(
            lambda: save_checkpoint(
                client,
                name,
                description,
                unique=False,  # the name carries a timestamp
                extra_tags=["auto-checkpoint"],
            ),
            name="penfield-checkpoint",
            failure_level=logging.WARNING,
        )
        saved = save.result(CHECKPOINT_DEADLINE)
        if not isinstance(saved, dict) or not saved.get("success"):
            # Failed, or still saving: either way there is nothing to point at yet.
            return ""
        return (
            f"Penfield saved a checkpoint named '{name}' before this compression; "
            "penfield_restore_context can reload its memories."
        )

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Copy a built-in memory write to Penfield, if the user opted in.

        Off by default: Penfield is the source of truth and a second copy of
        every note is rarely wanted. See ADR-0008. When on, the work runs in
        the background so the memory tool that triggered it is not held up,
        and only the primary agent's writes are copied.
        """
        if self._mirror is None or not self._config or not self._config.mirror_builtin_writes:
            return
        if self._agent_context != "primary":
            return
        previous = str((metadata or {}).get("previous_content") or "")
        self._mirror.submit(action, target, content, previous)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _recall_line(item: dict[str, Any]) -> str:
    """Format one recalled memory for the context Hermes injects."""
    content = " ".join(str(item.get("content") or "").split())
    if item.get("truncated"):
        content += " [...] (penfield_fetch this id for the rest)"
    if item.get("source_type") == "document":
        kind = f"document {item.get('filename') or ''}".strip()
    else:
        kind = str(item.get("memory_type") or "")
    details = [d for d in (kind, str(item.get("created_at") or "")[:10]) if d]
    label = f" ({', '.join(details)})" if details else ""
    return f"- [{item.get('id')}]{label} {content}"


def _describe_window(session_id: str, messages: Sequence[object]) -> str:
    """Describe recent conversation for a checkpoint, from the user's own words.

    The description doubles as the search query that selects which memories
    the checkpoint links, so it is built from what the user asked about.
    """
    asks: list[str] = []
    for msg in messages:
        if _msg_role(msg) != "user":
            continue
        text = _msg_text(msg)
        # Drop pasted payloads; they say little about the topic.
        text = re.sub(r"\{[^{}]{40,}\}", "", text)
        text = re.sub(r"\[[^\[\]]{40,}\]", "", text)
        text = " ".join(text.split())
        if len(text) > 10:
            asks.append(text[:160])
    if not asks:
        return f"Session {session_id}"
    return " | ".join(asks[-5:])


def _msg_role(msg: object) -> str:
    """Extract a role label from a dict or attribute-style message."""
    if isinstance(msg, dict):
        return str(msg.get("role", "message"))
    return str(getattr(msg, "role", "message"))


def _msg_text(msg: object) -> str:
    """Extract textual content from a dict or attribute-style message."""
    content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
    if isinstance(content, list):
        # OpenAI-style content blocks.
        return " ".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return str(content or "")


__all__ = ["PENFIELD_CONFIG_SCHEMA", "PenfieldMemoryProvider"]
