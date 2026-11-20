# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Run one scenario inside a real Hermes process and write a JSON report.

The integration tests start this file in a subprocess, one per scenario, so
each run gets a fresh Hermes process the way a real session does. It wires the
memory provider through the same entry points the agent uses at startup
(``load_hermes_dotenv``, ``resolve_memory_provider``, ``MemoryManager``) and
then walks the hooks a session triggers.

Usage: ``python driver.py <scenario> <report.json>`` with ``HERMES_HOME`` set.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import sys
import time
from typing import Any

PROVIDER = "penfield"
SESSION_ID = "it-session-0001"
MESSAGES = [
    {"role": "user", "content": "Let us plan the launch checklist for the plugin release."},
    {"role": "assistant", "content": "Here is the checklist: tests, docs, tag, publish."},
]


class _Capture(logging.Handler):
    """Collect what Hermes logs about the provider while a scenario runs."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if PROVIDER not in text.lower():
            return
        # Under the plugin host the plugin's own log lines are relayed into
        # Hermes's log at INFO, each carrying the level it was logged at.
        relayed = "plugin host [" in text and (" WARNING " in text or " ERROR " in text)
        if record.levelno >= logging.WARNING or relayed:
            self.warnings.append(text)
        elif "failed" in text.lower():
            # MemoryManager swallows hook exceptions and logs them at DEBUG.
            self.failures.append(text)


def _call(obj: Any, name: str, *args: Any, **kwargs: Any) -> Any:
    """Call ``obj.name`` passing only the keyword arguments it accepts.

    Hermes adds keyword arguments to these methods between releases; the
    driver has to run unchanged against every supported one.
    """
    fn = getattr(obj, name)
    params = inspect.signature(fn).parameters
    if not any(p.kind is p.VAR_KEYWORD for p in params.values()):
        kwargs = {k: v for k, v in kwargs.items() if k in params}
    return fn(*args, **kwargs)


def _step(report: dict[str, Any], name: str, fn: Any) -> Any:
    started = time.monotonic()
    entry: dict[str, Any] = {"ok": True, "started": time.time()}
    try:
        value = fn()
        entry["value"] = value if _is_jsonable(value) else repr(value)
        return value
    except Exception as exc:  # recorded for the test to assert on
        entry.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        return None
    finally:
        entry["seconds"] = round(time.monotonic() - started, 3)
        entry["finished"] = time.time()
        report["steps"][name] = entry


def _is_jsonable(value: Any) -> bool:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True


def _tool(report: dict[str, Any], mgr: Any, label: str, name: str, args: dict[str, Any]) -> Any:
    """Run a tool through the manager and return its parsed JSON result."""
    raw = _step(report, f"tool:{label}", lambda: mgr.handle_tool_call(name, args))
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        parsed = raw
    report["steps"][f"tool:{label}"]["value"] = parsed
    return parsed


def _describe_provider(report: dict[str, Any]) -> Any:
    from agent.memory_provider import MemoryProvider
    from plugins.memory import discover_memory_providers, load_memory_provider

    report["discovered"] = any(row[0] == PROVIDER for row in discover_memory_providers())
    provider = load_memory_provider(PROVIDER)
    report["loaded"] = provider is not None
    if provider is None:
        return None
    report["is_memory_provider"] = isinstance(provider, MemoryProvider)
    report["missing_members"] = sorted(
        m for m in dir(MemoryProvider) if not m.startswith("_") and not hasattr(provider, m)
    )
    report["available"] = bool(provider.is_available())
    report["tool_names"] = sorted(s["name"] for s in provider.get_tool_schemas())
    return provider


def _resolve_provider(said: list[str]) -> Any:
    """The configured provider, resolved the way agent startup resolves it."""
    try:
        from agent.agent_init_memory_provider import resolve_memory_provider
    except ImportError:
        return _resolve_provider_inline(said)
    return resolve_memory_provider(PROVIDER, say=said.append)


def _resolve_provider_inline(said: list[str]) -> Any:
    """Hermes before 0.23 did this inline in ``agent_init``: the same steps."""
    from agent import agent_init
    from plugins.memory import load_memory_provider

    provider = load_memory_provider(PROVIDER)
    if provider is None or provider.is_available():
        return provider
    _call(
        agent_init,
        "_warn_memory_provider_unavailable",
        PROVIDER,
        provider.unavailable_reason(),
        say=said.append,
    )
    return None


def _manager(report: dict[str, Any]) -> Any:
    """Build a MemoryManager the way agent startup does."""
    from agent.memory_manager import MemoryManager

    said: list[str] = []
    report["said"] = said
    provider = _resolve_provider(said)
    report["resolved"] = provider is not None
    if provider is None:
        return None
    mgr = MemoryManager()
    mgr.add_provider(provider)
    init = "initialize_for_agent" if hasattr(mgr, "initialize_for_agent") else "initialize_all"
    _step(
        report,
        "initialize",
        lambda: _call(
            mgr,
            init,
            say=said.append,
            session_id=SESSION_ID,
            platform="cli",
            hermes_home=os.environ["HERMES_HOME"],
            agent_context="primary",
            resumed=False,
            message_count=0,
        ),
    )
    return mgr


def scenario_load(report: dict[str, Any]) -> None:
    _describe_provider(report)


def scenario_session(report: dict[str, Any]) -> None:
    if _describe_provider(report) is None:
        return
    mgr = _manager(report)
    if mgr is None:
        return
    query = "What did we decide about the launch checklist?"
    _step(report, "system_prompt", mgr.build_system_prompt)
    _step(report, "system_prompt_again", mgr.build_system_prompt)
    _step(report, "tool_schemas", lambda: sorted(s["name"] for s in mgr.get_all_tool_schemas()))
    _step(
        report,
        "on_turn_start",
        lambda: _call(
            mgr,
            "on_turn_start",
            1,
            query,
            model="test-model",
            platform="cli",
            author_id=None,
            author_name=None,
            author_is_bot=False,
            previous_message="",
        ),
    )
    _step(report, "prefetch", lambda: _call(mgr, "prefetch_all", query, session_id=SESSION_ID))
    _step(report, "describe_recall", mgr.describe_recall)
    _step(
        report,
        "prefetch_no_match",
        lambda: _call(mgr, "prefetch_all", "zzqx unrelated vvkw", session_id=SESSION_ID),
    )
    _step(report, "describe_recall_no_match", mgr.describe_recall)
    _step(
        report,
        "sync_turn",
        lambda: _call(
            mgr,
            "sync_all",
            MESSAGES[0]["content"],
            MESSAGES[1]["content"],
            session_id=SESSION_ID,
            messages=MESSAGES,
        ),
    )
    _step(
        report,
        "queue_prefetch",
        lambda: _call(mgr, "queue_prefetch_all", "Next topic: the docs", session_id=SESSION_ID),
    )
    if hasattr(mgr, "flush_pending"):
        _step(report, "flush_pending", lambda: _call(mgr, "flush_pending", timeout=10))

    first = _tool(
        report,
        mgr,
        "store",
        "penfield_store",
        {
            "content": "The launch checklist review is done and the plan is approved.",
            "tags": ["launch"],
            "importance": 0.8,
        },
    )
    second = _tool(
        report,
        mgr,
        "store_second",
        "penfield_store",
        {"content": "Docs for the launch checklist live in the handbook.", "tags": ["launch"]},
    )
    _tool(
        report,
        mgr,
        "store_prose",
        "penfield_store",
        {"content": "The task-list shows the risk-adjusted plan; the desk-side review is done."},
    )
    first_id = (first or {}).get("id") if isinstance(first, dict) else None
    second_id = (second or {}).get("id") if isinstance(second, dict) else None
    _tool(report, mgr, "recall", "penfield_recall", {"query": "launch checklist", "limit": 5})
    _tool(
        report,
        mgr,
        "recall_memories_only",
        "penfield_recall",
        {"query": "launch checklist", "source_type": "memory", "tags": ["launch"]},
    )
    _tool(report, mgr, "search", "penfield_search", {"query": "launch checklist"})
    _tool(report, mgr, "fetch", "penfield_fetch", {"id": first_id})
    _tool(
        report,
        mgr,
        "update_memory",
        "penfield_update_memory",
        {"memory_id": first_id, "importance": 0.9},
    )
    _tool(
        report,
        mgr,
        "connect",
        "penfield_connect",
        {"from_memory": second_id, "to_memory": first_id, "relationship_type": "supports"},
    )
    _tool(report, mgr, "explore", "penfield_explore", {"start_memory": first_id, "max_depth": 2})
    _tool(
        report,
        mgr,
        "disconnect",
        "penfield_disconnect",
        {"from_memory": second_id, "to_memory": first_id},
    )
    _tool(report, mgr, "reflect", "penfield_reflect", {"time_window": "30d"})
    _tool(report, mgr, "reflect_named_window", "penfield_reflect", {"time_window": "week"})
    _tool(report, mgr, "reflect_invalid", "penfield_reflect", {"time_window": "fortnight"})
    _tool(
        report,
        mgr,
        "save_artifact",
        "penfield_save_artifact",
        {"path": "/it/notes.md", "content": "# Notes\nlaunch"},
    )
    _tool(
        report,
        mgr,
        "list_artifacts",
        "penfield_list_artifacts",
        {"path_prefix": "/it/", "name_pattern": "notes"},
    )
    _tool(report, mgr, "retrieve_artifact", "penfield_retrieve_artifact", {"path": "/it/notes.md"})
    _tool(report, mgr, "delete_artifact", "penfield_delete_artifact", {"path": "/it/notes.md"})
    _tool(
        report,
        mgr,
        "save_context",
        "penfield_save_context",
        {"name": "it-handoff", "description": "Launch checklist handoff for the next session."},
    )
    _tool(report, mgr, "list_contexts", "penfield_list_contexts", {})
    _tool(report, mgr, "restore_context", "penfield_restore_context", {"name": "it-handoff"})
    _tool(report, mgr, "awaken", "penfield_awaken", {})
    _tool(report, mgr, "missing_argument", "penfield_store", {})

    _step(report, "on_pre_compress", lambda: mgr.on_pre_compress(MESSAGES))
    _step(
        report,
        "on_session_switch",
        lambda: _call(mgr, "on_session_switch", "it-session-0002", parent_session_id=SESSION_ID),
    )
    _step(
        report,
        "on_delegation",
        lambda: _call(
            mgr, "on_delegation", "summarize the docs", "done", child_session_id="it-child-1"
        ),
    )
    _step(
        report,
        "on_memory_write",
        lambda: _call(
            mgr, "on_memory_write", "add", "memory", "prefers short replies", metadata={}
        ),
    )
    _step(report, "on_session_end", lambda: mgr.on_session_end(MESSAGES))
    _step(report, "shutdown", mgr.shutdown_all)


def scenario_outage(report: dict[str, Any]) -> None:
    """Time the hot paths while the API does not answer."""
    mgr = _manager(report)
    if mgr is None:
        return
    _step(report, "system_prompt", mgr.build_system_prompt)
    _step(
        report,
        "prefetch",
        lambda: _call(mgr, "prefetch_all", "What did we decide?", session_id=SESSION_ID),
    )
    _step(report, "on_pre_compress", lambda: mgr.on_pre_compress(MESSAGES))
    _step(report, "shutdown", mgr.shutdown_all)


def scenario_unconfigured(report: dict[str, Any]) -> None:
    """No credentials: Hermes must leave the provider out and say why."""
    _describe_provider(report)
    _manager(report)


def scenario_profiles(report: dict[str, Any]) -> None:
    """One process serving several profiles: the key must come from the profile's own scope.

    Hermes's gateway installs a secret scope per profile and stops trusting
    the process environment. The key is taken out of the environment here, so
    the provider can only find it by asking Hermes.
    """
    from pathlib import Path

    from agent import secret_scope

    home = os.environ["HERMES_HOME"]
    secrets = dict(secret_scope.build_profile_secret_scope(Path(home)))
    report["scope_has_key"] = bool(secrets.get("PENFIELD_API_KEY"))
    for name in [n for n in os.environ if n.startswith("PENFIELD_")]:
        del os.environ[name]
    report["environment_has_key"] = "PENFIELD_API_KEY" in os.environ

    secret_scope.set_multiplex_active(True)
    token = secret_scope.set_secret_scope(secrets, profile_home=home)
    try:
        if _describe_provider(report) is None:
            return
        mgr = _manager(report)
        if mgr is None:
            return
        _tool(report, mgr, "store", "penfield_store", {"content": "Stored from a scoped profile."})
        _step(report, "shutdown", mgr.shutdown_all)
    finally:
        secret_scope.reset_secret_scope(token)
        secret_scope.set_multiplex_active(False)


def scenario_mirror(report: dict[str, Any]) -> None:
    """Built-in memory writes reach Penfield when the user opted in."""
    mgr = _manager(report)
    if mgr is None:
        return
    note, edited = "User prefers short replies.", "User prefers short replies with examples."
    _step(report, "add", lambda: _call(mgr, "on_memory_write", "add", "user", note, metadata={}))
    _step(
        report,
        "add_second",
        lambda: _call(
            mgr, "on_memory_write", "add", "memory", "Project uses Python 3.14.", metadata={}
        ),
    )
    _step(
        report,
        "replace",
        lambda: _call(
            mgr,
            "on_memory_write",
            "replace",
            "user",
            edited,
            metadata={"previous_content": note},
        ),
    )
    _step(
        report,
        "remove",
        lambda: _call(
            mgr,
            "on_memory_write",
            "remove",
            "memory",
            "",
            metadata={"previous_content": "Project uses Python 3.14."},
        ),
    )
    _step(report, "on_pre_compress", lambda: mgr.on_pre_compress(MESSAGES))
    _step(report, "on_session_end", lambda: mgr.on_session_end(MESSAGES))
    _step(report, "shutdown", mgr.shutdown_all)


def scenario_setup(report: dict[str, Any]) -> None:
    """What ``hermes memory setup`` asks the user and where the answers go.

    The wizard is interactive, so its field loop is run directly with the
    prompt answered for it. That loop is internal to Hermes; if a release
    moves it, the scenario says so instead of guessing.
    """
    from plugins.memory import load_memory_provider

    try:
        from hermes_cli import memory_setup

        prompt_fields = memory_setup._prompt_schema_fields
        memory_setup._prompt  # noqa: B018 - must exist to be replaced below
    except (ImportError, AttributeError) as exc:
        report["wizard"] = f"unavailable: {exc}"
        return

    asked: list[dict[str, Any]] = []

    def answer(label: str, default: str | None = None, secret: bool = False) -> str:
        asked.append({"label": label, "secret": secret})
        return "tm_pf_setup_ak_entered"

    memory_setup._prompt = answer
    provider = load_memory_provider(PROVIDER)
    schema = [dict(field) for field in provider.get_config_schema()]
    provider_config: dict[str, Any] = {}
    env_writes: dict[str, Any] = {}
    completed = prompt_fields(PROVIDER, schema, provider_config, env_writes)
    report.update(
        wizard="ran",
        completed=completed,
        asked=asked,
        provider_config=provider_config,
        env_writes=env_writes,
    )


SCENARIOS = {
    "setup": scenario_setup,
    "load": scenario_load,
    "session": scenario_session,
    "outage": scenario_outage,
    "unconfigured": scenario_unconfigured,
    "profiles": scenario_profiles,
    "mirror": scenario_mirror,
}


def main(argv: list[str]) -> int:
    scenario, out_path = argv[1], argv[2]
    report: dict[str, Any] = {"scenario": scenario, "steps": {}}
    capture = _Capture()
    logging.getLogger().addHandler(capture)
    logging.getLogger().setLevel(logging.DEBUG)
    try:
        from hermes_cli.env_loader import load_hermes_dotenv
        from hermes_cli.plugin_isolation import isolation_mode

        load_hermes_dotenv(hermes_home=os.environ["HERMES_HOME"])
        report["isolation"] = isolation_mode()
        SCENARIOS[scenario](report)
    except Exception as exc:
        report["crashed"] = f"{type(exc).__name__}: {exc}"
    report["swallowed_failures"] = capture.failures
    report["warnings"] = capture.warnings
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=repr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
