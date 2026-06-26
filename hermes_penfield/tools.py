# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Agent tool schemas and dispatch for the Penfield provider.

Thirteen tools (3 priority tiers per the v1.0 spec), each mapped to the
*real* Penfield v2 endpoints — see ADR-0003 for the full reconciliation
table. The spec's hypothetical paths (/store, /recall, /connect, ...) are
not used; this module is the authoritative mapping of tool name to
verified API call.

Tools are exposed to Hermes as a list of JSON-schema dicts plus a
``dispatch`` function the provider calls from ``handle_tool_call``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from hermes_penfield.constants import (
    DIRECTION_TYPES,
    MEMORY_TYPES,
    PROTECTED_MEMORY_TYPES,
    RELATIONSHIP_TYPES,
    SOURCE_TYPES,
)

if TYPE_CHECKING:
    from hermes_penfield.client import PenfieldClient

# ---------------------------------------------------------------------------
# Tool schema table. Mirrors the shape Hermes expects from
# get_tool_schemas(): a JSON-Schema-ish descriptor per tool.
# ---------------------------------------------------------------------------
# Building these as data rather than as code keeps the agent-facing surface
# in one scannable place.

_RELATIONSHIP_TYPE_ENUM = sorted(RELATIONSHIP_TYPES)


def _enum_str(values: tuple[str, ...]) -> dict[str, list[str]]:
    return {"enum": sorted(values)}


PENFIELD_TOOL_SCHEMAS: list[dict[str, Any]] = [
    # ------------------------------------------------------------------ P0
    {
        "name": "penfield_store",
        "description": (
            "Store a memory in Penfield. Use for decisions, preferences, "
            "facts, and context worth persisting across sessions. Avoid "
            "storing secrets. Prefer concise, self-contained content."
        ),
        "priority": 0,
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "minLength": 1, "maxLength": 10000},
                "memory_type": {"type": "string", **_enum_str(MEMORY_TYPES)},
                "importance": {"type": "number", "minimum": 0, "maximum": 1},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
            },
            "required": ["content"],
        },
    },
    {
        "name": "penfield_recall",
        "description": (
            "Semantic recall: hybrid search across memories using natural "
            "language. The default tool when you need prior context, "
            "decisions, or facts. Combines BM25, vector, and graph search."
        ),
        "priority": 0,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 4000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                "memory_types": {"type": "array", "items": {"type": "string"}},
                "importance_threshold": {"type": "number", "minimum": 0, "maximum": 1},
                "tags": {"type": "string", "description": "comma-separated, OR logic"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "penfield_search",
        "description": (
            "Structured search with explicit control over BM25/vector/graph "
            "weights and graph expansion. Prefer penfield_recall for general "
            "queries; use this when tuning retrieval behavior."
        ),
        "priority": 0,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 4000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "bm25_weight": {"type": "number", "minimum": 0, "maximum": 1},
                "vector_weight": {"type": "number", "minimum": 0, "maximum": 1},
                "graph_weight": {"type": "number", "minimum": 0, "maximum": 1},
                "memory_types": {"type": "array", "items": {"type": "string"}},
                "importance_threshold": {"type": "number", "minimum": 0, "maximum": 1},
                "enable_graph_expansion": {"type": "boolean"},
                "graph_max_depth": {"type": "integer", "minimum": 1, "maximum": 5},
                "tags": {
                    "type": "string",
                    "description": "comma-separated, OR logic (API filter shape)",
                },
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "penfield_connect",
        "description": (
            "Create a typed relationship between two memories, building the "
            "knowledge graph. Use the 24 documented relationship types "
            "(supports, contradicts, follows, depends_on, ...)."
        ),
        "priority": 0,
        "parameters": {
            "type": "object",
            "properties": {
                "from_id": {"type": "string", "format": "uuid"},
                "to_id": {"type": "string", "format": "uuid"},
                "relationship_type": {
                    "type": "string",
                    **_enum_str(RELATIONSHIP_TYPES),
                },
                "direction_type": {"type": "string", **_enum_str(DIRECTION_TYPES)},
                "inverse_type": {
                    "type": "string",
                    "description": "required when direction_type is INVERSE_PAIRED",
                    **_enum_str(RELATIONSHIP_TYPES),
                },
                "strength": {"type": "number", "minimum": 0, "maximum": 1},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "evidence": {"type": "object"},
            },
            "required": ["from_id", "to_id", "relationship_type"],
        },
    },
    {
        "name": "penfield_fetch",
        "description": "Retrieve a single memory by its UUID.",
        "priority": 0,
        "parameters": {
            "type": "object",
            "properties": {"memory_id": {"type": "string", "format": "uuid"}},
            "required": ["memory_id"],
        },
    },
    {
        "name": "penfield_update",
        "description": (
            "Update an existing memory's content, importance, tags, or "
            "metadata. Only provided fields are changed. NOTE: the API uses "
            "PUT (not PATCH)."
        ),
        "priority": 0,
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "format": "uuid"},
                "content": {"type": "string", "minLength": 1, "maxLength": 10000},
                "memory_type": {"type": "string", **_enum_str(MEMORY_TYPES)},
                "importance": {"type": "number", "minimum": 0, "maximum": 1},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "tags": {"type": "array", "items": {"type": "string"}},
                "metadata": {"type": "object"},
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "penfield_delete",
        "description": "Permanently delete a memory by UUID. Requires delete permission.",
        "priority": 0,
        "parameters": {
            "type": "object",
            "properties": {"memory_id": {"type": "string", "format": "uuid"}},
            "required": ["memory_id"],
        },
    },
    # ------------------------------------------------------------------ P1
    {
        "name": "penfield_explore",
        "description": (
            "Traverse the knowledge graph from a starting memory. Useful for "
            "understanding how a memory relates to its neighborhood "
            "(supports/contradicts/follows chains)."
        ),
        "priority": 1,
        "parameters": {
            "type": "object",
            "properties": {
                "start_memory_id": {"type": "string", "format": "uuid"},
                "max_depth": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
                "direction": {
                    "type": "string",
                    "enum": ["OUTBOUND", "INBOUND", "ANY"],
                },
                "relationship_types": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["start_memory_id"],
        },
    },
    {
        "name": "penfield_reflect",
        "description": (
            "Generate insights from memory patterns over a time window. Use "
            "periodically to surface themes, active topics, and open threads."
        ),
        "priority": 1,
        "parameters": {
            "type": "object",
            "properties": {
                "time_window": {
                    "type": "string",
                    "enum": ["recent", "day", "week", "month", "quarter", "year", "all"],
                    "default": "recent",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "penfield_list_artifacts",
        "description": "List artifacts stored under a directory prefix.",
        "priority": 1,
        "parameters": {
            "type": "object",
            "properties": {
                "prefix": {"type": "string", "default": "/"},
                "name_pattern": {"type": "string"},
            },
        },
    },
    {
        "name": "penfield_save_artifact",
        "description": (
            "Store a file/document as an artifact, keyed by a slash-path. "
            "Use for long-form reference material (notes, specs, configs)."
        ),
        "priority": 1,
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "pattern": r"^/[^.][^/].*$",
                    "description": "must start with / and name a file",
                },
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "penfield_retrieve_artifact",
        "description": "Retrieve an artifact's content by its slash-path.",
        "priority": 1,
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    # ------------------------------------------------------------------ P2
    {
        "name": "penfield_awaken",
        "description": (
            "Load the Penfield awakening briefing (persona + custom "
            "instructions + system philosophy). Supplements the host "
            "SOUL.md; does not replace it. See ADR-0008."
        ),
        "priority": 2,
        "parameters": {"type": "object", "properties": {}},
    },
]

# Quick lookup by name.
TOOL_NAMES = {t["name"] for t in PENFIELD_TOOL_SCHEMAS}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
# Each handler takes (client, args) and returns a JSON-serializable result.
# Handlers validate arg shape defensively but rely on the client for
# transport-level error handling.

Handler = Callable[["PenfieldClient", dict[str, Any]], Any]
_HANDLERS: dict[str, Handler] = {}


def _tool(name: str) -> Callable[[Handler], Handler]:
    """Register a handler under ``name`` and return it unchanged."""

    def deco(fn: Handler) -> Handler:
        _HANDLERS[name] = fn
        return fn

    return deco


def dispatch(client: PenfieldClient, tool_name: str, args: dict[str, Any]) -> str:
    """Dispatch a tool call and return a JSON string result.

    Args:
        client: An authenticated :class:`PenfieldClient`.
        tool_name: One of :data:`TOOL_NAMES`.
        args: Parsed tool arguments.

    Returns:
        JSON-encoded string (Hermes tools return strings).

    Raises:
        ValueError: If ``tool_name`` is unknown.
    """
    if tool_name not in _HANDLERS:
        raise ValueError(f"unknown tool {tool_name!r}")
    result = _HANDLERS[tool_name](client, args)
    return json.dumps(result, default=_json_default)


def _json_default(obj: object) -> str:
    """Fallback JSON encoder for non-standard types."""
    return str(obj)


# --- P0 handlers ---------------------------------------------------------


@_tool("penfield_store")
def _store(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    content = str(args["content"])
    _reject_secret_like(content)
    body = {"content": content}
    for key in ("memory_type", "importance", "confidence", "tags"):
        if key in args and args[key] is not None:
            body[key] = args[key]
    if body.get("memory_type") in PROTECTED_MEMORY_TYPES:
        raise ValueError(
            f"memory_type {body['memory_type']!r} is managed via the "
            "/personality endpoints and cannot be stored via penfield_store"
        )
    return client.call("memory_create", body=body)


@_tool("penfield_recall")
def _recall(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"query": args["query"]}
    for key in ("limit", "memory_types", "importance_threshold", "tags"):
        if key in args and args[key] is not None:
            body[key] = args[key]
    items = client.call("search_hybrid", body=body)
    return _search_result(items)


@_tool("penfield_search")
def _search(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"query": args["query"]}
    # Validate weight sum if any weights provided.
    weights = [args.get(w) for w in ("bm25_weight", "vector_weight", "graph_weight")]
    if any(w is not None for w in weights):
        total = sum(float(w) for w in weights if w is not None)
        # If all three provided, require sum == 1.0 (API requirement).
        if all(w is not None for w in weights) and abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"bm25_weight + vector_weight + graph_weight must sum to 1.0 (got {total})"
            )
    for key in (
        "limit",
        "bm25_weight",
        "vector_weight",
        "graph_weight",
        "memory_types",
        "importance_threshold",
        "enable_graph_expansion",
        "graph_max_depth",
        "tags",
        "start_date",
        "end_date",
    ):
        if key in args and args[key] is not None:
            body[key] = args[key]
    items = client.call("search_hybrid", body=body)
    return _search_result(items)


@_tool("penfield_connect")
def _connect(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    rtype = str(args["relationship_type"])
    if rtype not in RELATIONSHIP_TYPES:
        raise ValueError(
            f"relationship_type {rtype!r} not in the {len(RELATIONSHIP_TYPES)} valid types"
        )
    body: dict[str, Any] = {
        "from_id": args["from_id"],
        "to_id": args["to_id"],
        "relationship_type": rtype,
    }
    for key in ("direction_type", "inverse_type", "strength", "confidence", "evidence"):
        if key in args and args[key] is not None:
            body[key] = args[key]
    if body.get("direction_type") == "INVERSE_PAIRED" and not body.get("inverse_type"):
        raise ValueError("direction_type INVERSE_PAIRED requires inverse_type")
    return client.call("relationship_create", body=body)


@_tool("penfield_fetch")
def _fetch(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.call("memory_get", path_params={"memory_id": args["memory_id"]})


@_tool("penfield_update")
def _update(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    memory_id = args["memory_id"]
    body = {k: v for k, v in args.items() if k != "memory_id" and v is not None}
    if not body:
        raise ValueError("penfield_update requires at least one field to update")
    if body.get("memory_type") in PROTECTED_MEMORY_TYPES:
        raise ValueError(f"cannot set memory_type to protected value {body['memory_type']!r}")
    return client.call("memory_update", path_params={"memory_id": memory_id}, body=body)


@_tool("penfield_delete")
def _delete(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    client.call("memory_delete", path_params={"memory_id": args["memory_id"]})
    return {"deleted": True, "memory_id": args["memory_id"]}


# --- P1 handlers ---------------------------------------------------------


@_tool("penfield_explore")
def _explore(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"start_memory_id": args["start_memory_id"]}
    for key in ("max_depth", "direction", "relationship_types"):
        if key in args and args[key] is not None:
            body[key] = args[key]
    return client.call("relationship_traverse", body=body)


@_tool("penfield_reflect")
def _reflect(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if args.get("time_window"):
        body["time_window"] = args["time_window"]
    if args.get("limit"):
        body["limit"] = args["limit"]
    return client.call("analysis_reflect", body=body)


@_tool("penfield_list_artifacts")
def _list_artifacts(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    query = {
        "prefix": args.get("prefix", "/"),
    }
    if args.get("name_pattern"):
        query["name_pattern"] = args["name_pattern"]
    return client.call("artifact_list", query=query)


@_tool("penfield_save_artifact")
def _save_artifact(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.call(
        "artifact_save",
        body={"path": args["path"], "content": args["content"]},
    )


@_tool("penfield_retrieve_artifact")
def _retrieve_artifact(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.call("artifact_retrieve", query={"path": args["path"]})


# --- P2 handler ----------------------------------------------------------


@_tool("penfield_awaken")
def _awaken(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.call("personality_awakening")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Lightweight secret-shape guard. Not a security boundary — the real
# guarantee is "we don't ship secrets"; this just stops the obvious
# "Authorization: Bearer ..." paste from being stored. See ADR-0009.
_SECRET_PATTERNS = (
    "-----BEGIN ",
    "ghp_",
    "github_pat_",
    "xoxb-",
    "AKIA",
)


def _reject_secret_like(content: str) -> None:
    lowered = content.lower()
    for pat in _SECRET_PATTERNS:
        if pat.lower() in lowered:
            raise ValueError(
                f"content appears to contain a secret (matched {pat!r}); "
                "refusing to store. See ADR-0009."
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


# Source types are part of the public schema; re-export for callers/tests.
__all__ = [
    "PENFIELD_TOOL_SCHEMAS",
    "SOURCE_TYPES",
    "TOOL_NAMES",
    "dispatch",
]
