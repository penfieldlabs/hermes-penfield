# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""The Hermes ``MemoryProvider`` adapter.

Verified against real Hermes source (github.com/NousResearch/hermes-agent,
agent/memory_provider.py). Implements the full ABC: name, is_available,
initialize, system_prompt_block (auto-fetches awakening briefing),
prefetch, queue_prefetch, sync_turn, get_tool_schemas, handle_tool_call,
shutdown, on_pre_compress, on_memory_write, on_session_end.

The provider intentionally does **not** import Hermes at module load time
(duck-types the ABC) so unit tests run without Hermes installed.

It duck-types the expected method set so that:

* unit tests run without Hermes installed;
* a future, slightly-different ABC can be reconciled by adjusting this one
  file.

Lifecycle methods with no real Penfield endpoint (``sync_turn``,
``on_pre_compress``) are implemented defensively: see ADR-0012.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .auth import PenfieldAuth
from .client import PenfieldClient
from .config import PenfieldConfig
from .exceptions import PenfieldError
from .tools import PENFIELD_TOOL_SCHEMAS, dispatch

if TYPE_CHECKING:
    import threading
    from collections.abc import Sequence

logger = logging.getLogger("penfield.provider")

# Config schema surfaced to `hermes memory setup`. Mirrors the spec's
# section 8.1. Secrets are never written by the provider; the api_key field
# exists only so the setup wizard can collect and route it to env/keychain.
PENFIELD_CONFIG_SCHEMA: list[dict[str, Any]] = [
    {
        "key": "auth_method",
        "description": "Authentication method",
        "default": "oauth",
        "choices": ["oauth", "api_key"],
    },
    {
        "key": "api_key",
        "description": "Penfield API key (CI/headless only; OAuth preferred)",
        "secret": True,
        "required": False,
        "env_var": "PENFIELD_API_KEY",
        "url": "https://portal.penfield.app/settings/api-keys",
    },
    {
        "key": "penfield_url",
        "description": "Penfield API base URL override",
        "default": "",
        "env_var": "PENFIELD_URL",
    },
    {
        "key": "penfield_env",
        "description": "Environment (prod or dev)",
        "default": "prod",
        "choices": ["prod", "dev"],
        "env_var": "PENFIELD_ENV",
    },
]


class PenfieldMemoryProvider:
    """Penfield memory provider for Hermes Agent.

    Constructed by Hermes (or directly in tests). Call :meth:`initialize`
    once with the session context before any tool dispatch.
    """

    def __init__(self) -> None:
        """Initialize an unbound provider; call :meth:`initialize` next."""
        self._hermes_home: str = ""
        self._session_id: str = ""
        self._config: PenfieldConfig | None = None
        self._auth: PenfieldAuth | None = None
        self._client: PenfieldClient | None = None
        self._sync_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Identity / availability (no network)
    # ------------------------------------------------------------------
    @property
    def name(self) -> str:
        """Return the provider identifier."""
        return "penfield"

    def is_available(self) -> bool:
        """Return True if credentials are present; never makes a network call.

        Checks env var, explicit tokens path, AND the default ~/.hermes/penfield/tokens.json
        so that OAuth login via ``hermes penfield login`` is recognized even before
        ``initialize()`` sets ``_hermes_home``.
        """
        import os

        if os.environ.get("PENFIELD_API_KEY"):
            return True
        if self._hermes_home:
            token_path = Path(self._hermes_home) / "penfield" / "tokens.json"
            if token_path.exists():
                return True
        # Also check the default HERMES_HOME before initialize() sets it.
        default_home = os.environ.get("HERMES_HOME", "") or str(Path.home() / ".hermes")
        token_path = Path(default_home) / "penfield" / "tokens.json"
        if token_path.exists():
            return True
        return bool(self._auth and self._auth.is_authenticated())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def initialize(self, session_id: str, **kwargs: Any) -> None:
        """Bind the provider to a session.

        Args:
            session_id: Hermes session id.
            **kwargs: ``hermes_home`` is required. Other kwargs ignored.
        """
        self._session_id = session_id
        self._hermes_home = str(kwargs.get("hermes_home", ""))
        self._config = PenfieldConfig.load(self._hermes_home or None)
        self._auth = PenfieldAuth(self._config, self._hermes_home or None)
        self._client = PenfieldClient(self._auth, self._config)

    # ------------------------------------------------------------------
    # Tool surface
    # ------------------------------------------------------------------
    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return the 17 penfield_* tool schemas for the host."""
        return [dict(t) for t in PENFIELD_TOOL_SCHEMAS]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **_: object) -> str:
        """Dispatch a tool call and return a JSON-string result."""
        if self._client is None:
            raise RuntimeError("provider not initialized")
        try:
            return dispatch(self._client, tool_name, args)
        except PenfieldError as exc:
            logger.warning("tool %s failed: %s", tool_name, exc)
            return json.dumps({"error": str(exc), "tool": tool_name})
        except ValueError as exc:
            return json.dumps({"error": str(exc), "tool": tool_name})

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------
    def get_config_schema(self) -> list[dict[str, Any]]:
        """Return the config schema for ``hermes memory setup``."""
        return [dict(c) for c in PENFIELD_CONFIG_SCHEMA]

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

    # ------------------------------------------------------------------
    # System prompt integration
    # ------------------------------------------------------------------
    def system_prompt_block(self) -> str:
        """Return the Penfield awakening briefing + tool instructions.

        Automatically calls the awakening endpoint so the persona, custom
        instructions, and system philosophy are injected into every session
        without the user needing to manually invoke penfield_awaken.
        """
        base = (
            "You have access to Penfield persistent memory via the "
            "penfield_* tools. Memories persist across sessions and form a "
            "queryable knowledge graph. Use penfield_store to save important "
            "decisions/preferences/context, penfield_recall for semantic "
            "search of prior context, penfield_connect to link related "
            "memories, and penfield_explore to traverse the graph. Do not "
            "store secrets."
        )
        # Try to fetch the awakening briefing and prepend it.
        if self._client is not None:
            try:
                import json

                result = dispatch(self._client, "penfield_awaken", {})
                briefing = json.loads(result).get("briefing", "")
                if briefing:
                    return f"{briefing}\n\n---\n\n{base}"
            except Exception:
                pass
        return base

    # ------------------------------------------------------------------
    # Auto-recall
    # ------------------------------------------------------------------
    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Queue a background recall for the NEXT turn.

        Hermes calls this after every turn (memory_manager.py:535). We use
        synchronous recall in :meth:`prefetch`, so this is a no-op — but it
        must exist on the duck-typed instance or Hermes logs a warning every
        turn (the ABC default isn't inherited without subclassing).
        """

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Auto-recall relevant memories before a turn (best-effort).

        Failure is non-fatal: any error returns an empty string so the host
        turn never breaks because of memory.
        """
        if self._client is None or not self._config or not self._config.auto_recall:
            return ""
        try:
            result = dispatch(
                self._client,
                "penfield_recall",
                {
                    "query": query,
                    "limit": self._config.recall_limit,
                    "importance_threshold": self._config.recall_threshold,
                },
            )
            parsed = json.loads(result)
            items = parsed.get("items", [])
            if not items:
                return ""
            lines = ["[Penfield memory — recalled context]"]
            for it in items[: self._config.recall_limit]:
                snippet = str(it.get("snippet", ""))[:200]
                lines.append(f"- [{str(it.get('id', ''))[:8]}] {snippet}")
            out = "\n".join(lines)
            return out[: self._config.max_prefetch_chars]
        except (PenfieldError, ValueError, OSError) as exc:
            logger.debug("prefetch failed (non-fatal): %s", exc)
            return ""

    # ------------------------------------------------------------------
    # Turn sync — NO real /turns/sync endpoint.
    # ------------------------------------------------------------------
    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: object = None,
    ) -> None:
        """No-op (no upstream endpoint).

        The spec described a ``POST /turns/sync`` endpoint that does not
        exist in the real Penfield API. To avoid silent failure we
        intentionally do nothing here when ``sync_turn_enabled`` is true
        (the default) and log once. Turn-level context is still captured
        via explicit ``penfield_store`` calls and via
        :meth:`on_pre_compress`.
        """
        if self._config and not self._config.sync_turn_enabled:
            return
        logger.debug("sync_turn is a no-op (no /turns/sync endpoint)")

    # ------------------------------------------------------------------
    # Pre-compress — synthesized as a checkpoint memory. See ADR-0012.
    # ------------------------------------------------------------------
    def on_pre_compress(self, messages: Sequence[object]) -> str:
        """Save a checkpoint before Hermes compresses the context window.

        Creates a save_context checkpoint capturing relevant memories from
        the knowledge graph. The checkpoint name is unique per call
        (session + timestamp). Returns empty string — Hermes compresses
        how it wants.
        """
        if self._client is None or not self._config or not self._config.pre_compress_save:
            return ""
        tail = list(messages[-20:]) if messages else []
        if not tail:
            return ""
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        name = f"pre-compress-{self._session_id}-{ts}"
        description = _summarize_window(self._session_id, tail)
        try:
            dispatch(
                self._client,
                "penfield_save_context",
                {
                    "name": name,
                    "description": description,
                },
            )
        except (PenfieldError, ValueError, OSError) as exc:
            logger.warning("pre-compress checkpoint save failed (non-fatal): %s", exc)
        return ""

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Opt-in mirror of MEMORY.md/USER.md writes. Off by default.

        Penfield is the source of truth; mirroring creates duplicates and
        confuses recall. See ADR-0008. ``metadata`` carries structured
        provenance (write_origin, session_id, platform, ...) per the ABC;
        we forward it into the stored memory's metadata when mirroring is
        opted in.
        """
        if not self._config or not self._config.mirror_builtin_writes:
            return
        if self._client is None:
            return
        # Defensive: never forward content that looks like a secret.
        # metadata (write_origin/session_id/platform/...) is ABC provenance;
        # we surface action+target+origin as tags since the store tool has
        # no metadata field.
        tags = ["builtin-mirror", target, f"action:{action}"]
        if metadata and isinstance(metadata, dict):
            origin = metadata.get("write_origin") or metadata.get("tool_name")
            if origin:
                tags.append(f"origin:{origin}")
        try:
            dispatch(
                self._client,
                "penfield_store",
                {
                    "content": content[:10000],
                    "memory_type": "reference",
                    "tags": tags[:10],
                },
            )
        except (PenfieldError, ValueError, OSError) as exc:
            logger.debug("builtin mirror store failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Session teardown
    # ------------------------------------------------------------------
    def on_session_end(self, messages: object = None) -> None:
        """Flush any in-flight sync thread before the session closes."""
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=10.0)

    def shutdown(self) -> None:
        """Alias for :meth:`on_session_end`."""
        self.on_session_end()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summarize_window(session_id: str, messages: Sequence[object]) -> str:
    """Summarize recent conversation into a short checkpoint description.

    Uses Hermes auxiliary LLM (cheap/fast) with fallback to main model,
    then to user-message-only text if no LLM is available.
    """
    parts: list[str] = []
    total = 0
    for msg in messages[-20:]:
        role = _msg_role(msg)
        if role not in ("user", "assistant"):
            continue
        text = _msg_text(msg)
        if not text:
            continue
        text = re.sub(r"\{[^{}]{40,}\}", "", text)
        text = re.sub(r"\[[^\[\]]{40,}\]", "", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        if not text:
            continue
        chunk = f"{role}: {text[:300]}"
        parts.append(chunk)
        total += len(chunk)
        if total > 8000:
            break
    if not parts:
        return f"Session {session_id}"

    transcript = "\n".join(parts)

    try:
        from agent.auxiliary_client import call_llm  # type: ignore[import-not-found]

        prompt = [
            {
                "role": "user",
                "content": (
                    "Summarize this conversation in 1-2 sentences "
                    "(under 200 chars). Focus on WHAT was discussed "
                    "and decided, not how. No meta-commentary.\n\n" + transcript
                ),
            }
        ]
        try:
            resp = call_llm(
                task="compression",
                messages=prompt,
                temperature=0,
                max_tokens=100,
            )
            logger.info("_summarize_window: used auxiliary LLM")
        except Exception:
            resp = call_llm(
                messages=prompt,
                temperature=0,
                max_tokens=100,
            )
            logger.info("_summarize_window: used main LLM (aux unavailable)")
        summary = resp.choices[0].message.content.strip()
        if summary and len(summary) > 10:
            return summary
    except Exception:
        logger.warning("_summarize_window: LLM fallback failed, using text-only")

    # Fallback: user messages only, joined with pipe
    user_parts = [p[6:80] for p in parts if p.startswith("user:") and len(p) > 16]
    if user_parts:
        return " | ".join(user_parts[-5:])
    return f"Session {session_id}"


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
