# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Agent tool schemas and dispatch — mirrors the Penfield MCP surface.

16 tools (save_context held for v1.1; see ADR-0011). Each maps 1:1 to an
MCP tool with matching parameter names.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .constants import (
    RELATIONSHIP_TYPES,
)

if TYPE_CHECKING:
    from .client import PenfieldClient

_RELATIONSHIP_TYPE_ENUM = sorted(RELATIONSHIP_TYPES)


def _enum_str(values: tuple[str, ...]) -> dict[str, list[str]]:
    return {"enum": sorted(values)}


# ---------------------------------------------------------------------------
# Tool schemas — mirrors the Penfield MCP tool list exactly
# ---------------------------------------------------------------------------

PENFIELD_TOOL_SCHEMAS: list[dict[str, Any]] = [
    # ---------------------------------------------------------------- awaken
    {
        "name": "penfield_awaken",
        "description": (
            "Load the user's personality briefing. Used at the start of a "
            "conversation to orient with persona, custom instructions, and "
            "system philosophy."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    # ----------------------------------------------------------------- store
    {
        "name": "penfield_store",
        "description": (
            "Store a new memory. Memory type is auto-detected from content. "
            "Use for decisions, preferences, facts, and context worth "
            "persisting across sessions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "minLength": 1, "maxLength": 10000},
                "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                "importance": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["content"],
        },
    },
    # ---------------------------------------------------------------- recall
    {
        "name": "penfield_recall",
        "description": (
            "Hybrid search (BM25 + vector + graph) for context retrieval. "
            "Use when you need prior context, decisions, or facts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 4000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                "source_type": {
                    "type": "string",
                    "description": "Filter: 'memory', 'document', or omit for all",
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "start_date": {"type": "string", "description": "ISO 8601"},
                "end_date": {"type": "string", "description": "ISO 8601"},
            },
            "required": ["query"],
        },
    },
    # ---------------------------------------------------------------- search
    {
        "name": "penfield_search",
        "description": (
            "Semantic search for fuzzy concept matching. Use when you don't "
            "have exact terms. Returns citation format with id, title, url, text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 4000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
            },
            "required": ["query"],
        },
    },
    # ----------------------------------------------------------------- fetch
    {
        "name": "penfield_fetch",
        "description": "Get a specific memory by ID.",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string", "format": "uuid"}},
            "required": ["id"],
        },
    },
    # -------------------------------------------------------- update_memory
    {
        "name": "penfield_update_memory",
        "description": "Update an existing memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "format": "uuid"},
                "content": {"type": "string", "minLength": 1, "maxLength": 10000},
                "importance": {"type": "number", "minimum": 0, "maximum": 1},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["memory_id"],
        },
    },
    # --------------------------------------------------------------- connect
    {
        "name": "penfield_connect",
        "description": (
            "Create a relationship between two memories, building the "
            "knowledge graph. Uses the 24 documented relationship types."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "from_memory": {"type": "string", "format": "uuid"},
                "to_memory": {"type": "string", "format": "uuid"},
                "relationship_type": {
                    "type": "string",
                    **_enum_str(RELATIONSHIP_TYPES),
                },
                "strength": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["from_memory", "to_memory", "relationship_type"],
        },
    },
    # ------------------------------------------------------------ disconnect
    {
        "name": "penfield_disconnect",
        "description": "Remove a relationship between two memories.",
        "parameters": {
            "type": "object",
            "properties": {
                "from_memory": {"type": "string", "format": "uuid"},
                "to_memory": {"type": "string", "format": "uuid"},
            },
            "required": ["from_memory", "to_memory"],
        },
    },
    # --------------------------------------------------------------- explore
    {
        "name": "penfield_explore",
        "description": "Traverse the knowledge graph from a starting memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "start_memory": {"type": "string", "format": "uuid"},
                "max_depth": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
                "relationship_types": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["start_memory"],
        },
    },
    # --------------------------------------------------------------- reflect
    {
        "name": "penfield_reflect",
        "description": "Analyze memory patterns and generate insights over a time window.",
        "parameters": {
            "type": "object",
            "properties": {
                "time_window": {
                    "type": "string",
                    "enum": ["recent", "today", "week"],
                    "default": "recent",
                },
                "include_documents": {"type": "boolean", "default": False},
                "start_date": {"type": "string", "description": "ISO 8601"},
                "end_date": {"type": "string", "description": "ISO 8601"},
            },
        },
    },
    # ---------------------------------------------------------- save_artifact
    {
        "name": "penfield_save_artifact",
        "description": "Save a file artifact. Artifacts are NOT searchable via recall/search.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "Must start with / and include a filename",
                },
            },
            "required": ["content", "path"],
        },
    },
    # ----------------------------------------------------- retrieve_artifact
    {
        "name": "penfield_retrieve_artifact",
        "description": "Get a stored artifact by path.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    # --------------------------------------------------------- list_artifacts
    {
        "name": "penfield_list_artifacts",
        "description": "List stored files under a directory prefix.",
        "parameters": {
            "type": "object",
            "properties": {
                "prefix": {"type": "string", "default": "/"},
            },
        },
    },
    # -------------------------------------------------------- delete_artifact
    {
        "name": "penfield_delete_artifact",
        "description": "Delete a stored artifact by path.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    # ---------------------------------------------------------- list_contexts
    {
        "name": "penfield_list_contexts",
        "description": (
            "List saved context checkpoints. Checkpoints are session-handoff "
            "points created via save_context (coming in a future version). "
            "Use to browse or find prior cognitive state snapshots."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "name_pattern": {
                    "type": "string",
                    "description": "Case-insensitive substring filter on context name",
                },
                "include_descriptions": {"type": "boolean", "default": False},
            },
        },
    },
    # ------------------------------------------------------- restore_context
    {
        "name": "penfield_restore_context",
        "description": (
            "Restore a saved context checkpoint by name. Loads the checkpoint "
            "and its linked memories. Special case: name='awakening' loads "
            "the personality configuration."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "required": ["name"],
        },
    },
]

TOOL_NAMES = {t["name"] for t in PENFIELD_TOOL_SCHEMAS}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

Handler = Callable[["PenfieldClient", dict[str, Any]], Any]
_HANDLERS: dict[str, Handler] = {}


def _tool(name: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        _HANDLERS[name] = fn
        return fn

    return deco


def dispatch(client: PenfieldClient, tool_name: str, args: dict[str, Any]) -> str:
    """Dispatch a tool call and return a JSON string result."""
    if tool_name not in _HANDLERS:
        raise ValueError(f"unknown tool {tool_name!r}")
    result = _HANDLERS[tool_name](client, args)
    return json.dumps(result, default=_json_default)


def _json_default(obj: object) -> str:
    return str(obj)


# --- Handlers (parameter names match MCP exactly) ------------------------


@_tool("penfield_awaken")
def _awaken(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.call("personality_awakening")


@_tool("penfield_store")
def _store(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    content = str(args["content"])
    _reject_secret_like(content)
    body: dict[str, Any] = {"content": content}
    for key in ("tags", "importance"):
        if key in args and args[key] is not None:
            body[key] = args[key]
    return client.call("memory_create", body=body)


@_tool("penfield_recall")
def _recall(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"query": args["query"]}
    if args.get("limit") is not None:
        body["limit"] = args["limit"]
    # MCP → REST translations (the MCP server does these internally).
    # source_type (MCP string) → source_types (REST array)
    if args.get("source_type") is not None:
        body["source_types"] = [args["source_type"]]
    # tags (MCP array) → tags (REST comma-separated string)
    if args.get("tags") is not None:
        tags_val = args["tags"]
        body["tags"] = ",".join(tags_val) if isinstance(tags_val, list) else tags_val
    for key in ("start_date", "end_date"):
        if key in args and args[key] is not None:
            body[key] = args[key]
    items = client.call("search_hybrid", body=body)
    return _search_result(items)


@_tool("penfield_search")
def _search(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    body = {"query": args["query"]}
    if args.get("limit") is not None:
        body["limit"] = args["limit"]
    items = client.call("search_hybrid", body=body)
    return _search_result(items)


@_tool("penfield_fetch")
def _fetch(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.call("memory_get", path_params={"memory_id": args["id"]})


@_tool("penfield_update_memory")
def _update_memory(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    memory_id = args["memory_id"]
    body = {k: v for k, v in args.items() if k != "memory_id" and v is not None}
    if not body:
        raise ValueError("penfield_update_memory requires at least one field to update")
    return client.call("memory_update", path_params={"memory_id": memory_id}, body=body)


@_tool("penfield_connect")
def _connect(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    rtype = str(args["relationship_type"])
    if rtype not in RELATIONSHIP_TYPES:
        raise ValueError(
            f"relationship_type {rtype!r} not in the {len(RELATIONSHIP_TYPES)} valid types"
        )
    # MCP uses from_memory/to_memory → API uses from_id/to_id
    body: dict[str, Any] = {
        "from_id": args["from_memory"],
        "to_id": args["to_memory"],
        "relationship_type": rtype,
    }
    if args.get("strength") is not None:
        body["strength"] = args["strength"]
    return client.call("relationship_create", body=body)


@_tool("penfield_disconnect")
def _disconnect(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    client.call(
        "relationship_delete_between",
        query={"from_id": args["from_memory"], "to_id": args["to_memory"]},
    )
    return {"disconnected": True}


@_tool("penfield_explore")
def _explore(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    # MCP uses start_memory → API uses start_memory_id
    body: dict[str, Any] = {"start_memory_id": args["start_memory"]}
    for key in ("max_depth", "relationship_types"):
        if key in args and args[key] is not None:
            body[key] = args[key]
    return client.call("relationship_traverse", body=body)


@_tool("penfield_reflect")
def _reflect(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for key in ("time_window", "include_documents", "start_date", "end_date"):
        if key in args and args[key] is not None:
            body[key] = args[key]
    return client.call("analysis_reflect", body=body)


@_tool("penfield_save_artifact")
def _save_artifact(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.call(
        "artifact_save",
        body={"path": args["path"], "content": args["content"]},
    )


@_tool("penfield_retrieve_artifact")
def _retrieve_artifact(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.call("artifact_retrieve", query={"path": args["path"]})


@_tool("penfield_list_artifacts")
def _list_artifacts(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.call("artifact_list", query={"prefix": args.get("prefix", "/")})


@_tool("penfield_delete_artifact")
def _delete_artifact(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    client.call("artifact_delete", query={"path": args["path"]})
    return {"deleted": True, "path": args["path"]}


@_tool("penfield_list_contexts")
def _list_contexts(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    """List checkpoint-type memories.

    Maps to GET /memories?memory_type=checkpoint. The REST API doesn't
    support name_pattern or include_descriptions natively (those are
    MCP-server enrichments), so we filter client-side.
    """
    limit = int(args.get("limit", 20))
    offset = int(args.get("offset", 0))
    name_pattern = args.get("name_pattern", "")
    include_descs = args.get("include_descriptions", False)

    # REST API uses page/per_page, not limit/offset
    page = (offset // limit) + 1
    resp = client.call(
        "memory_list",
        query={
            "memory_type": "checkpoint",
            "per_page": limit,
            "page": page,
        },
    )

    items = resp.get("items", []) if isinstance(resp, dict) else []
    # Client-side name_pattern filter (REST API doesn't support it)
    if name_pattern:
        name_lower = name_pattern.lower()
        items = [it for it in items if name_lower in str(it.get("content", "")[:200]).lower()]

    # Format to MCP-like context shape
    contexts = []
    for it in items:
        ctx = {
            "id": it.get("id"),
            "name": str(it.get("content", ""))[:100],
            "memory_count": 0,  # REST API doesn't return linked count
            "created": it.get("created_at"),
        }
        if include_descs:
            ctx["description"] = it.get("content", "")
        contexts.append(ctx)

    return {
        "contexts": contexts,
        "total": resp.get("pagination", {}).get("total", len(contexts))
        if isinstance(resp, dict)
        else len(contexts),
        "limit": limit,
        "offset": offset,
    }


@_tool("penfield_restore_context")
def _restore_context(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    """Restore a checkpoint by name.

    Special case: name='awakening' loads personality config (same as
    penfield_awaken). Otherwise searches for a checkpoint-type memory
    matching the name.
    """
    name = args["name"]
    limit = int(args.get("limit", 20))

    # Special case: awakening loads personality config
    if name.lower() == "awakening":
        return client.call("personality_awakening")

    # Search for checkpoint memories matching the name
    # REST API doesn't have a name field, so we search the content
    results = client.call(
        "search_hybrid",
        body={
            "query": name,
            "limit": limit,
            "memory_types": ["checkpoint"],
        },
    )

    items = results.get("items", []) if isinstance(results, dict) else []
    if not items:
        return {"error": f"No checkpoint found with name '{name}'"}

    # Return the best match + its neighbors
    best = items[0]
    return {
        "context_id": best.get("id"),
        "content": best.get("content", ""),
        "memories": [
            {
                "id": it.get("id"),
                "score": it.get("score"),
                "snippet": it.get("snippet", ""),
            }
            for it in items
        ],
        "count": len(items),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = (
    "-----BEGIN ",
    "ghp_",
    "github_pat_",
    "xoxb-",
    "AKIA",
    "sk-",
    "sk_live_",
)


def _reject_secret_like(content: str) -> None:
    lowered = content.lower()
    for pat in _SECRET_PATTERNS:
        if pat.lower() in lowered:
            raise ValueError(
                f"content appears to contain a secret (matched {pat!r}); refusing to store."
            )


def _search_result(response: Any) -> dict[str, Any]:
    """Normalize hybrid-search responses to a compact agent-facing shape."""
    if not isinstance(response, dict):
        return {"items": response}
    items = response.get("items", [])
    slim = [
        {
            "id": it.get("id"),
            "score": it.get("score"),
            "snippet": it.get("snippet") or (str(it.get("content", ""))[:200]),
            "memory_type": (it.get("metadata") or {}).get("memory_type"),
        }
        for it in items
        if isinstance(it, dict)
    ]
    return {"items": slim, "count": len(slim)}
