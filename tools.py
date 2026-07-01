# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Agent tool schemas and dispatch — mirrors the Penfield MCP surface.

17 tools (full MCP surface; see ADR-0011). Each maps 1:1 to an
MCP tool with matching parameter names.
"""

from __future__ import annotations

import json
import re as _re
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
            "Load the user's personality briefing and system instructions. "
            "Call this at the start of a new session. No parameters needed.\n"
            "Example: penfield_awaken()\n"
            "Note: In Hermes, this runs automatically via system_prompt_block."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    # ----------------------------------------------------------------- store
    {
        "name": "penfield_store",
        "description": (
            "Store a new memory. Memory type is auto-detected from content. "
            "Use for decisions, preferences, facts, corrections, and context "
            "worth persisting across sessions.\n"
            'Example: penfield_store(content="User prefers concise responses")\n'
            "IMPORTANT: Omit optional fields (tags, importance) if not needed. "
            "Do not pass empty strings or empty arrays. Do not store secrets."
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
            "Hybrid search (BM25 + vector + graph) across all memories. "
            "Primary tool for retrieving prior context, decisions, or facts.\n"
            'Example: penfield_recall(query="user preferences", limit=5)\n'
            "IMPORTANT: Omit optional fields if not filtering. Do not pass "
            "empty strings for source_type. source_type accepts 'memory' or "
            "'document' only (omit for all). tags is an array of tag names."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 4000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                "source_type": {
                    "type": "string",
                    "description": "'memory' or 'document'. Omit for all.",
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "start_date": {"type": "string", "description": "ISO 8601 date. Omit if unused."},
                "end_date": {"type": "string", "description": "ISO 8601 date. Omit if unused."},
            },
            "required": ["query"],
        },
    },
    # ---------------------------------------------------------------- search
    {
        "name": "penfield_search",
        "description": (
            "Semantic search for fuzzy concept matching. Simpler than recall: "
            "just query and limit. Use when you don't have exact terms.\n"
            'Example: penfield_search(query="async programming patterns")'
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
        "description": (
            "Get a single memory by its UUID. Use when you have a specific "
            "memory ID from a previous store or search result.\n"
            'Example: penfield_fetch(id="550e8400-e29b-41d4-a716-446655440000")'
        ),
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string", "format": "uuid"}},
            "required": ["id"],
        },
    },
    # -------------------------------------------------------- update_memory
    {
        "name": "penfield_update_memory",
        "description": (
            "Update an existing memory's content, importance, or tags. "
            "Only provided fields are changed.\n"
            "Example: penfield_update_memory(memory_id=...  content=...)\n"
            "IMPORTANT: Omit optional fields you don't want to change. "
            "Do not pass empty values."
        ),
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
            "Create a typed relationship between two memories to build the "
            "knowledge graph. Both IDs must be valid memory UUIDs.\n"
            'Example: penfield_connect(from_memory="uuid-1", '
            'to_memory="uuid-2", relationship_type="supports")\n'
            "Valid types: supports, contradicts, follows, precedes, "
            "depends_on, references, supersedes, updates, parent_of, "
            "child_of, sibling_of, composed_of, part_of, causes, "
            "influenced_by, prerequisite_for, implements, documents, "
            "tests, example_of, responds_to, inspired_by, evolution_of.\n"
            "IMPORTANT: Omit strength if not setting it."
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
        "description": (
            "Remove a relationship between two memories. Both IDs required.\n"
            'Example: penfield_disconnect(from_memory="uuid-1", '
            'to_memory="uuid-2")'
        ),
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
        "description": (
            "Traverse the knowledge graph from a starting memory. Shows "
            "connected memories and their relationship types.\n"
            'Example: penfield_explore(start_memory="uuid", max_depth=3)\n'
            "IMPORTANT: Omit relationship_types if exploring all. "
            "Do not pass empty arrays."
        ),
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
        "description": (
            "Analyze memory patterns and generate insights over a time window. "
            "Use periodically to surface themes and active topics.\n"
            'Example: penfield_reflect(time_window="recent")\n'
            "IMPORTANT: Omit optional fields if not filtering. "
            "Do not pass empty strings for dates."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "time_window": {
                    "type": "string",
                    "enum": ["recent", "today", "week"],
                    "default": "recent",
                },
                "include_documents": {"type": "boolean", "default": False},
                "start_date": {"type": "string", "description": "ISO 8601 date. Omit if unused."},
                "end_date": {"type": "string", "description": "ISO 8601 date. Omit if unused."},
            },
        },
    },
    # ---------------------------------------------------------- save_artifact
    {
        "name": "penfield_save_artifact",
        "description": (
            "Save a file artifact (document, notes, code). Artifacts are NOT "
            "searchable via recall/search — use store for searchable memories.\n"
            'Example: penfield_save_artifact(path="/project/notes.md", '
            'content="# Notes\\nImportant info")'
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "Must start with / and include a filename.",
                },
            },
            "required": ["content", "path"],
        },
    },
    # ----------------------------------------------------- retrieve_artifact
    {
        "name": "penfield_retrieve_artifact",
        "description": (
            "Get a stored artifact by its path. Returns full content.\n"
            'Example: penfield_retrieve_artifact(path="/project/notes.md")'
        ),
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    # --------------------------------------------------------- list_artifacts
    {
        "name": "penfield_list_artifacts",
        "description": (
            "List stored files under a directory prefix.\n"
            'Example: penfield_list_artifacts(prefix="/project/")\n'
            "IMPORTANT: Omit prefix for root listing. Default is '/'."
        ),
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
        "description": (
            "Delete a stored artifact by path. Permanent.\n"
            'Example: penfield_delete_artifact(path="/project/old-notes.md")'
        ),
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    # --------------------------------------------------------- save_context
    {
        "name": "penfield_save_context",
        "description": (
            "Create a checkpoint of cognitive state for session handoffs. "
            "The description becomes the checkpoint summary and drives "
            "which memories get linked. Include memory_id: UUID references "
            "to link specific memories.\n"
            'Example: penfield_save_context(name="API Investigation", '
            'description="Found auth bug in memory_id: 550e8400...")\n'
            "IMPORTANT: Names must be unique per tenant. Include a date "
            "or session identifier to avoid collisions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Checkpoint name. Must be unique per tenant.",
                },
                "description": {
                    "type": "string",
                    "description": "What was discussed. Can reference via memory_id: UUID.",
                },
            },
            "required": ["name"],
        },
    },
    # ---------------------------------------------------------- list_contexts
    {
        "name": "penfield_list_contexts",
        "description": (
            "List saved context checkpoints. Use to browse or find prior "
            "session handoff points.\n"
            "Example: penfield_list_contexts(limit=10)\n"
            "IMPORTANT: Omit optional fields if not filtering."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "name_pattern": {
                    "type": "string",
                    "description": "Case-insensitive substring filter. Omit for all.",
                },
                "include_descriptions": {"type": "boolean", "default": False},
            },
        },
    },
    # ------------------------------------------------------- restore_context
    {
        "name": "penfield_restore_context",
        "description": (
            "Restore a saved context checkpoint by exact name. Returns the "
            "checkpoint and the specific memories that were saved with it. "
            "Special case: name='awakening' loads personality config.\n"
            'Example: penfield_restore_context(name="API Investigation")'
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
    # Sanitize: strip empty-string optionals LLMs send instead of omitting
    required: set[str] = set()
    for schema in PENFIELD_TOOL_SCHEMAS:
        if schema["name"] == tool_name:
            required = set(schema.get("parameters", {}).get("required", []))
            break
    sanitized = {k: v for k, v in args.items() if k in required or v not in ("", [], {}, None)}
    result = _HANDLERS[tool_name](client, sanitized)
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


# ---------------------------------------------------------------------------
# save_context — reimplements MCP server enrichment via REST
# See docs/save-context-research.md for the verified behavior.
# ---------------------------------------------------------------------------


_MEMORY_ID_PATTERN = _re.compile(r"memory_id:\s*([0-9a-f-]{36})", _re.IGNORECASE)
_MEMORY_PHRASE_PATTERN = _re.compile(r"""memory:\s*["']([^"']+)["']""", _re.IGNORECASE)


def _parse_memory_refs(description: str) -> tuple[list[str], list[str]]:
    """Extract memory_id UUIDs and memory: 'phrase' references from text.

    Returns (uuid_refs, phrase_refs).
    """
    uuids = _MEMORY_ID_PATTERN.findall(description)
    phrases = _MEMORY_PHRASE_PATTERN.findall(description)
    return uuids, phrases


@_tool("penfield_save_context")
def _save_context(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    """Create a checkpoint of cognitive state.

    Mirrors the MCP save_context tool: creates a checkpoint-type memory
    with a structured JSON content containing the name, description, recent
    memory IDs, and resolved references. Enforces name uniqueness.
    """
    name = args["name"]
    description = args.get("description", "")

    # 1. Check name uniqueness: query existing checkpoints for exact name
    existing = client.call(
        "memory_list",
        query={
            "memory_type": "checkpoint",
            "per_page": 100,
        },
    )
    if isinstance(existing, dict):
        for item in existing.get("items", []):
            try:
                content = json.loads(item.get("content", "{}"))
                if content.get("checkpoint_name") == name:
                    return {
                        "success": False,
                        "error": f"Context name '{name}' already exists. Use a unique name.",
                        "error_code": "DUPLICATE_NAME",
                    }
            except (json.JSONDecodeError, TypeError):
                continue

    # 2. Gather related memories via hybrid search against the description
    # (matches MCP server behavior: search first 200 chars of description,
    # limit=30, min_score_threshold=0.25, fallback to recent 20 on error)
    search_query = description[:200] if description.strip() else name
    memory_ids: list[str] = []
    try:
        search_results = client.call(
            "search_hybrid",
            body={
                "query": search_query,
                "limit": 30,
                "min_score_threshold": 0.25,
            },
        )
        if isinstance(search_results, dict):
            for item in search_results.get("items", []):
                score = item.get("score", 0)
                if isinstance(score, (int, float)) and score >= 0.25:
                    mid = item.get("id")
                    if mid:
                        memory_ids.append(mid)
    except Exception:
        # Fallback: grab 20 most recent memories (error handling only)
        recent = client.call(
            "memory_list",
            query={
                "sort": "-created_at",
                "per_page": 20,
            },
        )
        if isinstance(recent, dict):
            for item in recent.get("items", []):
                mid = item.get("id")
                if mid:
                    memory_ids.append(mid)

    # 3. Parse references from description
    uuid_refs, phrase_refs = _parse_memory_refs(description)
    refs_extracted = len(uuid_refs) + len(phrase_refs)

    # 4. Resolve references and deduplicate against search results
    seen = set(memory_ids)
    referenced_memories: list[str] = []
    for mid in uuid_refs:
        if mid not in seen:
            seen.add(mid)
            referenced_memories.append(mid)

    for phrase in phrase_refs:
        try:
            results = client.call(
                "search_hybrid",
                body={
                    "query": phrase,
                    "limit": 1,
                    "min_score_threshold": 0.25,
                },
            )
            if isinstance(results, dict):
                items = results.get("items", [])
                if items and items[0].get("score", 0) >= 0.25:
                    mid = items[0].get("id")
                    if mid and mid not in seen:
                        seen.add(mid)
                        referenced_memories.append(mid)
        except Exception:
            pass  # best-effort; silently skip unresolved

    refs_resolved = len(referenced_memories)

    # 5. Create the checkpoint memory
    content = json.dumps(
        {
            "checkpoint_name": name,
            "description": description,
            "memory_count": len(memory_ids),
            "memory_ids": memory_ids,
            "referenced_memories": referenced_memories,
        }
    )

    result = client.call(
        "memory_create",
        body={
            "content": content,
            "memory_type": "checkpoint",
            "importance": 0.9,
            "tags": ["checkpoint", "context", name],
        },
    )

    context_id = result.get("id") if isinstance(result, dict) else None

    return {
        "success": True,
        "message": f"Context '{name}' saved",
        "context_id": context_id,
        "memories_included": len(memory_ids),
        "memory_ids": memory_ids,
        "name": name,
        "references_extracted": refs_extracted,
        "references_resolved": refs_resolved,
        "referenced_memories": referenced_memories,
    }


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
        filtered: list[Any] = []
        for it in items:
            try:
                cname = json.loads(it.get("content", "{}")).get("checkpoint_name", "")
            except (json.JSONDecodeError, TypeError):
                cname = ""
            if name_lower in cname.lower():
                filtered.append(it)
        items = filtered

    # Format to MCP-like context shape
    contexts = []
    for it in items:
        # Parse the JSON content to extract checkpoint_name
        try:
            content = json.loads(it.get("content", "{}"))
            ctx_name = content.get("checkpoint_name", str(it.get("content", ""))[:100])
            ctx_count = content.get("memory_count", 0)
        except (json.JSONDecodeError, TypeError):
            ctx_name = str(it.get("content", ""))[:100]
            ctx_count = 0
        ctx = {
            "id": it.get("id"),
            "name": ctx_name,
            "memory_count": ctx_count,
            "created": it.get("created_at"),
        }
        if include_descs:
            try:
                ctx["description"] = json.loads(it.get("content", "{}")).get(
                    "description", it.get("content", "")
                )
            except (json.JSONDecodeError, TypeError):
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

    Special case: name='awakening' loads personality config.
    Otherwise: find the checkpoint by name, parse its saved memory_ids,
    and fetch those specific memories — not a fresh search.
    """
    name = args["name"]
    limit = int(args.get("limit", 20))

    # Special case: awakening loads personality config
    if name.lower() == "awakening":
        return client.call("personality_awakening")

    # 1. Find the checkpoint by exact name match (not fuzzy search)
    checkpoint_id = None
    page = 1
    while page <= 10:  # paginate up to 1000 checkpoints
        resp = client.call(
            "memory_list",
            query={
                "memory_type": "checkpoint",
                "per_page": 100,
                "page": page,
            },
        )
        items = resp.get("items", []) if isinstance(resp, dict) else []
        if not items:
            break
        for item in items:
            try:
                c = json.loads(item.get("content", "{}"))
                if c.get("checkpoint_name") == name:
                    checkpoint_id = item.get("id")
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        if checkpoint_id:
            break
        page += 1

    if not checkpoint_id:
        return {"error": f"No checkpoint found with name '{name}'"}

    # 2. Fetch the full checkpoint to get its saved memory_ids
    checkpoint = client.call("memory_get", path_params={"memory_id": checkpoint_id})
    content: dict[str, Any] = {}
    try:
        content = json.loads(checkpoint.get("content", "{}"))
        saved_ids = content.get("memory_ids", [])
        referenced = content.get("referenced_memories", [])
        description = content.get("description", "")
    except (json.JSONDecodeError, TypeError):
        saved_ids = []
        referenced = []
        description = checkpoint.get("content", "")

    # 3. Fetch the actual saved memories (not a fresh search)
    memories = []
    # No artificial cap — restore the full saved set. The limit parameter
    # controls max memories to return, but defaults high enough to cover
    # typical checkpoints. Caller can lower it for large sets.
    effective_limit = max(limit, len(saved_ids + referenced))
    fetch_ids = (saved_ids + referenced)[:effective_limit]
    for mid in fetch_ids:
        try:
            mem = client.call("memory_get", path_params={"memory_id": mid})
            memories.append(
                {
                    "id": mem.get("id"),
                    "content": str(mem.get("content", ""))[:200],
                    "memory_type": mem.get("memory_type"),
                    "created_at": mem.get("created_at"),
                }
            )
        except Exception:
            pass  # skip deleted/inaccessible memories

    return {
        "context_id": checkpoint_id,
        "name": content.get("checkpoint_name", name),
        "description": description,
        "memories": memories,
        "count": len(memories),
        "saved_count": len(saved_ids),
        "truncated": len(saved_ids + referenced) > limit,
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
    "tm_pf_",
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
