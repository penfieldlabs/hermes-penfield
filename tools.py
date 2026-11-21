# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Agent tool schemas and dispatch, matching the Penfield MCP surface.

17 tools, one for each tool of Penfield's MCP server, with the same
parameter names. ``tests/test_surface.py`` compares the two and lists the
few parameters the REST API cannot serve.
"""

from __future__ import annotations

import json
import math
import re as _re
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .constants import (
    DOCUMENT_SOURCE_TYPE,
    MEMORY_SOURCE_TYPES,
    RELATIONSHIP_TYPES,
    SEMANTIC_SEARCH_WEIGHTS,
    TAG_MAX_CHARS,
    TAG_MIN_CHARS,
    TAGS_PER_MEMORY,
)
from .exceptions import NotFoundError, PenfieldError

if TYPE_CHECKING:
    from .client import PenfieldClient

_RELATIONSHIP_TYPE_ENUM = sorted(RELATIONSHIP_TYPES)

#: Longest memory content a search or restore result carries per memory. The
#: API allows 10,000 characters; longer results are cut and flagged, and the
#: agent can read the rest with ``penfield_fetch``.
RECALL_CONTENT_MAX_CHARS = 2000

#: Time windows ``penfield_reflect`` accepts. The API knows the first four
#: by name; the day counts are sent to it as a start date.
REFLECT_WINDOWS = ("recent", "today", "week", "month", "1d", "7d", "30d", "90d")
_DAY_WINDOWS = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}

#: A checkpoint description shorter than this many words is no use as a
#: search query, so the most recent memories are linked instead.
_DEFAULT_SEARCH_LIMIT = 10
_MIN_QUERY_WORDS = 3
_RECENT_FALLBACK = 20
#: Lowest search score at which a checkpoint links a memory.
_LINK_SCORE = 0.25

_PAGE_SIZE = 100  # the API's maximum page size
_MAX_CHECKPOINT_PAGES = 100
_FETCH_WORKERS = 5
_UUID_PATTERN = _re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", _re.IGNORECASE
)


def _enum_str(values: tuple[str, ...]) -> dict[str, list[str]]:
    return {"enum": sorted(values)}


#: What a tool that writes tags tells the agent about them.
TAGS_NOTE = (
    f"Up to {TAGS_PER_MEMORY} tags. Penfield stores a tag in lower case, turns spaces and "
    "underscores into hyphens and removes every other character that is not a-z, 0-9 or a "
    f"hyphen. A tag left with fewer than {TAG_MIN_CHARS} or more than {TAG_MAX_CHARS} "
    "characters is refused."
)

# ---------------------------------------------------------------------------
# Tool schemas: the Penfield MCP tool list, with a penfield_ prefix
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
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": TAGS_PER_MEMORY,
                    "description": TAGS_NOTE,
                },
                "importance": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["content"],
        },
    },
    # ---------------------------------------------------------------- recall
    {
        "name": "penfield_recall",
        "description": (
            "Hybrid search (keywords, meaning and graph links) across all "
            "memories. Primary tool for retrieving prior context, decisions, "
            "or facts, and the one to use for names, ids and exact phrases.\n"
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
            "Semantic search: ranks memories by meaning far more than by "
            "shared words. Use it when you do not know how a memory was "
            "worded, or when penfield_recall finds nothing useful. Takes a "
            "query and a limit only.\n"
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
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": TAGS_PER_MEMORY,
                    "description": TAGS_NOTE,
                },
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
                    "enum": list(REFLECT_WINDOWS),
                    "default": "recent",
                    "description": (
                        "'recent' is the last 50 memories; 'today', 'week' and 'month' "
                        "look back that far; '1d', '7d', '30d' and '90d' count days "
                        "back from now."
                    ),
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
            "searchable via recall/search. Use store for searchable memories.\n"
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
            "List the folders and files directly inside a directory (not recursive).\n"
            'Example: penfield_list_artifacts(path_prefix="/project/")\n'
            "IMPORTANT: Omit path_prefix to list the root."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path_prefix": {"type": "string", "default": "/"},
                "name_pattern": {
                    "type": "string",
                    "description": (
                        "Case-insensitive substring filter on file and folder names. Omit for all."
                    ),
                },
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
            "The description is the handoff narrative and also the search "
            "that selects which memories get linked; a description under "
            "three words links the 20 most recent memories instead. Link "
            "specific memories with 'memory_id: UUID' (reliable) or "
            "\"memory: 'a phrase'\" (best effort); the result lists any "
            "reference that did not resolve.\n"
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
_PARAMETERS: dict[str, dict[str, Any]] = {
    t["name"]: t.get("parameters", {}) for t in PENFIELD_TOOL_SCHEMAS
}


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
    """Dispatch a tool call and return a JSON string result.

    Raises:
        ValueError: If the tool is unknown or a required argument is missing.
            The message is written for the model that made the call.
    """
    if tool_name not in _HANDLERS:
        raise ValueError(f"unknown tool {tool_name!r}")
    if not isinstance(args, dict):
        raise ValueError(f"{tool_name}: arguments must be a JSON object")
    parameters = _PARAMETERS[tool_name]
    # Models send "" or null for optionals they mean to omit; drop those.
    sanitized = {k: v for k, v in args.items() if v not in ("", [], {}, None)}
    for key in parameters.get("required", []):
        if key not in sanitized:
            raise ValueError(f"{tool_name}: missing required argument {key!r}")
    properties = parameters.get("properties", {})
    for key, value in sanitized.items():
        if key in properties:
            sanitized[key] = _as_declared(key, value, str(properties[key].get("type")))
    result = _HANDLERS[tool_name](client, sanitized)
    return json.dumps(result, default=_json_default)


def _as_declared(name: str, value: Any, kind: str) -> Any:
    """Return ``value`` as the kind the tool's schema declares for ``name``.

    A value that is plainly meant (``"3"`` for a number, ``"a, b"`` for a
    list of tags) is taken as such. Anything else is refused by name, so the
    model reads which argument to correct instead of a Python error.
    """
    if kind == "string":
        if isinstance(value, str) and _is_text(value):
            return value
        raise ValueError(f"{name} must be text")
    if kind == "integer":
        number = _as_number(value)
        if number is None or number != int(number):
            raise ValueError(f"{name} must be a whole number")
        return int(number)
    if kind == "number":
        number = _as_number(value)
        if number is None:
            raise ValueError(f"{name} must be a number")
        return number
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise ValueError(f"{name} must be true or false")
    if kind == "array":
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list) and all(isinstance(i, str) and _is_text(i) for i in value):
            return value
        raise ValueError(f"{name} must be a list of text")
    return value


def _as_number(value: Any) -> float | None:
    """Return ``value`` as a number, or ``None`` if it is not one.

    Infinity and not-a-number are no use to any tool and do not survive
    the trip to the API as JSON; they count as not a number.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if isinstance(value, int) or (isinstance(value, float) and math.isfinite(value)):
        return value
    return None


def _is_text(value: str) -> bool:
    """False for a string that cannot be sent, such as half a surrogate pair."""
    try:
        value.encode()
    except UnicodeEncodeError:
        return False
    return True


def _json_default(obj: object) -> str:
    return str(obj)


# --- Handlers (parameter names match MCP exactly) ------------------------


@_tool("penfield_awaken")
def _awaken(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.call("personality_awakening")


def stored_tag(tag: str) -> str:
    """Return ``tag`` the way Penfield stores it.

    Lower case, spaces and underscores as hyphens, nothing but a-z, 0-9
    and hyphens, no hyphen at either end and never two in a row.
    """
    text = _re.sub(r"[ _]", "-", tag.strip().lower())
    text = _re.sub(r"[^a-z0-9-]", "", text)
    return _re.sub(r"-{2,}", "-", text).strip("-")


def _tag_problem(tag: str) -> str:
    """Say why Penfield would not store ``tag``, or return ``""`` if it would."""
    kept = len(stored_tag(tag))
    if kept > TAG_MAX_CHARS:
        return f"is too long: {kept} characters, and a tag can have at most {TAG_MAX_CHARS}"
    if kept < TAG_MIN_CHARS:
        return (
            "would not be stored: a tag keeps its letters a to z, its digits and its "
            f"hyphens, and at least {TAG_MIN_CHARS} characters have to remain"
        )
    return ""


def _tags_to_send(args: dict[str, Any]) -> list[str]:
    """Return the tags of a write, refusing what Penfield would not store.

    Penfield does not store a tag outside its limits, so the agent is told
    here, before anything is sent, and can correct it. A blank tag is what
    a model sends when it means none, and is left out.

    Raises:
        ValueError: Naming the first such tag by its position in the list,
            or saying that there are more tags than a memory can have.
    """
    tags = []
    for position, tag in enumerate(args.get("tags") or (), 1):
        if not tag.strip():
            continue
        problem = _tag_problem(tag)
        if problem:
            shown = tag if len(tag) <= 20 else f"{tag[:20]}..."
            raise ValueError(f"tag {position} ({shown!r}) {problem}")
        tags.append(tag)
    if len(tags) > TAGS_PER_MEMORY:
        raise ValueError(
            f"{len(tags)} tags were given and a memory can have at most {TAGS_PER_MEMORY}"
        )
    return tags


@_tool("penfield_store")
def _store(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"content": str(args["content"])}
    tags = _tags_to_send(args)
    if tags:
        body["tags"] = tags
    if "importance" in args:
        body["importance"] = args["importance"]
    return client.call("memory_create", body=body)


def _source_types(source_type: str) -> list[str]:
    """Translate the tool's ``source_type`` into the API's ``source_types``.

    The tool offers two filters, as Penfield's own tools do: ``memory``
    (everything that is not an uploaded document) and ``document``.
    """
    if source_type == "document":
        return [DOCUMENT_SOURCE_TYPE]
    if source_type == "memory":
        return list(MEMORY_SOURCE_TYPES)
    raise ValueError("source_type must be 'memory' or 'document'; omit it to search both")


def _search_body(args: dict[str, Any]) -> dict[str, Any]:
    """Build a hybrid-search request body from tool arguments."""
    body: dict[str, Any] = {"query": args["query"]}
    if "limit" in args:
        body["limit"] = int(args["limit"])
    if args.get("source_type"):
        body["source_types"] = _source_types(str(args["source_type"]))
    if "tags" in args:
        # The tool takes a list; the API takes a comma-separated string.
        tags = args["tags"]
        body["tags"] = ",".join(tags) if isinstance(tags, list) else str(tags)
    for key in ("start_date", "end_date", "min_score_threshold"):
        if key in args:
            body[key] = args[key]
    return body


def search_memories(
    client: PenfieldClient,
    args: dict[str, Any],
    *,
    max_chars: int = RECALL_CONTENT_MAX_CHARS,
    timeout: float | None = None,
    retries: int | None = None,
    weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Run a hybrid search and return agent-facing results.

    Shared by the recall and search tools and the provider's auto-recall,
    which passes a short ``timeout`` and ``retries=0`` because it runs
    before every turn. ``weights`` replaces the API's default balance
    between keywords, meaning and graph links.
    """
    options: dict[str, Any] = {}
    if timeout is not None:
        options["timeout"] = timeout
    if retries is not None:
        options["retries"] = retries
    body = _search_body(args)
    body.update(weights or {})
    items = _search_items(client, body, options)
    if not items and args.get("source_type") == "memory":
        # An empty answer can also mean the API has stopped accepting one of
        # the source types sent. Search again without them and leave the
        # documents out here.
        wanted = int(body.get("limit") or _DEFAULT_SEARCH_LIMIT)
        del body["source_types"]
        body["limit"] = min(wanted * 3, _PAGE_SIZE)
        found = _search_items(client, body, options)
        items = [it for it in found if it.get("source_type") != DOCUMENT_SOURCE_TYPE][:wanted]
    shaped = [_memory_view(it, max_chars=max_chars) for it in items]
    return {"items": shaped, "count": len(shaped)}


def _search_items(
    client: PenfieldClient, body: dict[str, Any], options: dict[str, Any]
) -> list[dict[str, Any]]:
    response = client.call("search_hybrid", body=body, **options)
    items = response.get("items", []) if isinstance(response, dict) else []
    return [it for it in items if isinstance(it, dict)]


@_tool("penfield_recall")
def _recall(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    return search_memories(client, args)


@_tool("penfield_search")
def _search(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    wanted: dict[str, Any] = {k: args[k] for k in ("query", "limit") if k in args}
    return search_memories(client, wanted, weights=SEMANTIC_SEARCH_WEIGHTS)


@_tool("penfield_fetch")
def _fetch(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.call("memory_get", path_params={"memory_id": args["id"]})


@_tool("penfield_update_memory")
def _update_memory(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    memory_id = args["memory_id"]
    body: dict[str, Any] = {k: args[k] for k in ("content", "importance") if k in args}
    tags = _tags_to_send(args)
    if tags:
        body["tags"] = tags
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
    window = args.get("time_window")
    if window is not None and window not in REFLECT_WINDOWS:
        raise ValueError(f"time_window must be one of {', '.join(REFLECT_WINDOWS)}")
    body: dict[str, Any] = {}
    for key in ("include_documents", "start_date", "end_date"):
        if args.get(key) is not None:
            body[key] = args[key]
    if window in _DAY_WINDOWS:
        # Explicit dates override any window, so only fill in a missing start.
        if "start_date" not in body and "end_date" not in body:
            since = datetime.now(UTC) - timedelta(days=_DAY_WINDOWS[window])
            body["start_date"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    elif window is not None:
        body["time_window"] = window
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
    # The tool uses the MCP parameter name; the API calls it ``prefix``.
    query: dict[str, Any] = {"prefix": args.get("path_prefix") or "/"}
    if args.get("name_pattern"):
        query["name_pattern"] = args["name_pattern"]
    return client.call("artifact_list", query=query)


# ---------------------------------------------------------------------------
# Context checkpoints. The REST API has no checkpoint endpoints, so these
# tools build them from memories and search. See ADR-0011.
# ---------------------------------------------------------------------------


_MEMORY_ID_PATTERN = _re.compile(r"memory_id:\s*([0-9a-f-]{36})", _re.IGNORECASE)
_MEMORY_PHRASE_PATTERN = _re.compile(r"""memory:\s*["']([^"']+)["']""", _re.IGNORECASE)


def _parse_memory_refs(description: str) -> tuple[list[str], list[str]]:
    """Extract memory_id UUIDs and memory: 'phrase' references from text.

    Returns (uuid_refs, phrase_refs).
    """
    uuids = [u.lower() for u in _MEMORY_ID_PATTERN.findall(description)]
    phrases = _MEMORY_PHRASE_PATTERN.findall(description)
    return uuids, phrases


@_tool("penfield_save_context")
def _save_context(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    return save_checkpoint(client, str(args["name"]), str(args.get("description", "")))


def save_checkpoint(
    client: PenfieldClient,
    name: str,
    description: str,
    *,
    unique: bool = True,
    extra_tags: Sequence[str] = (),
) -> dict[str, Any]:
    """Create a checkpoint of cognitive state.

    Does what Penfield's own ``save_context`` tool does, over the REST API:
    stores a checkpoint-type memory whose JSON content holds the name, the
    description, the memories that match the description, and the memories
    the description refers to explicitly.

    Args:
        client: API client.
        name: Checkpoint name, used later by restore_context.
        description: Narrative handoff. Also the search query that selects
            the memories the checkpoint links.
        unique: Refuse a name an existing checkpoint already uses. Callers
            that generate a fresh name every time can skip the lookup.
        extra_tags: Tags added to the standard checkpoint tags.
    """
    # 1. Names are unique across every checkpoint, however old.
    if unique:
        for _item, saved in _iter_checkpoints(client):
            if saved.get("checkpoint_name") == name:
                return {
                    "success": False,
                    "error": f"Context name '{name}' already exists. Use a unique name.",
                    "error_code": "DUPLICATE_NAME",
                }

    # 2. Link the memories the description is about. A description too short
    # to search on, or a search that fails, links the most recent ones.
    memory_ids: list[str] = []
    searched = False
    if len(description.split()) >= _MIN_QUERY_WORDS:
        try:
            memory_ids = _matching_ids(client, description[:200], limit=30)
            searched = True
        except PenfieldError:
            searched = False
    if not searched:
        recent = client.call(
            "memory_list", query={"sort": "-created_at", "per_page": _RECENT_FALLBACK}
        )
        items = recent.get("items", []) if isinstance(recent, dict) else []
        memory_ids = [str(it["id"]) for it in items if isinstance(it, dict) and it.get("id")]

    # 3. Resolve the references written into the description.
    uuid_refs, phrase_refs = _parse_memory_refs(description)
    seen = set(memory_ids)
    referenced_memories: list[str] = []
    unresolved: list[str] = []
    existing = {str(m.get("id")) for m in _fetch_memories(client, list(dict.fromkeys(uuid_refs)))}
    for memory_id in dict.fromkeys(uuid_refs):
        if memory_id not in existing:
            unresolved.append(f"memory_id: {memory_id}")
        elif memory_id not in seen:
            seen.add(memory_id)
            referenced_memories.append(memory_id)
    for phrase in dict.fromkeys(phrase_refs):
        try:
            match = _matching_ids(client, phrase, limit=1)
        except PenfieldError:
            match = []
        if not match:
            unresolved.append(f"memory: '{phrase}'")
        elif match[0] not in seen:
            seen.add(match[0])
            referenced_memories.append(match[0])
    refs_extracted = len(set(uuid_refs)) + len(set(phrase_refs))
    refs_resolved = refs_extracted - len(unresolved)

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

    # The name doubles as a tag where Penfield can store it as one.
    name_tag = [] if _tag_problem(name) else [name]
    result = client.call(
        "memory_create",
        body={
            "content": content,
            "memory_type": "checkpoint",
            "importance": 0.9,
            "tags": ["checkpoint", "context", *name_tag, *extra_tags],
            # Penfield's MCP server looks here to see whether a name is taken.
            # Without it a client connected through MCP could save a second
            # checkpoint under a name this plugin already used. Penfield
            # keeps the field and does not show it when the memory is read.
            "metadata": {"checkpoint_name": name},
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
        "unresolved_references": unresolved,
        "referenced_memories": referenced_memories,
    }


def _matching_ids(client: PenfieldClient, query: str, *, limit: int) -> list[str]:
    """Return ids of the memories that match ``query`` well enough to link."""
    found = client.call(
        "search_hybrid",
        body={"query": query, "limit": limit, "min_score_threshold": _LINK_SCORE},
    )
    items = found.get("items", []) if isinstance(found, dict) else []
    return [
        str(it["id"])
        for it in items
        if isinstance(it, dict)
        and it.get("id")
        and isinstance(it.get("score"), (int, float))
        and it["score"] >= _LINK_SCORE
    ]


@_tool("penfield_delete_artifact")
def _delete_artifact(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    client.call("artifact_delete", query={"path": args["path"]})
    return {"deleted": True, "path": args["path"]}


def _checkpoint_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Parse the JSON a checkpoint memory stores as its content."""
    try:
        parsed = json.loads(item.get("content") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _iter_checkpoints(client: PenfieldClient) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Yield ``(memory, payload)`` for every checkpoint, newest first.

    The API returns at most 100 memories per page, so this walks pages until
    the API reports no more.
    """
    for page in range(1, _MAX_CHECKPOINT_PAGES + 1):
        resp = client.call(
            "memory_list",
            query={"memory_type": "checkpoint", "per_page": _PAGE_SIZE, "page": page},
        )
        items = resp.get("items", []) if isinstance(resp, dict) else []
        for item in items:
            if isinstance(item, dict):
                yield item, _checkpoint_payload(item)
        pagination = resp.get("pagination", {}) if isinstance(resp, dict) else {}
        if not items or not pagination.get("has_next", len(items) == _PAGE_SIZE):
            return


@_tool("penfield_list_contexts")
def _list_contexts(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    """List saved contexts, newest first.

    The API has no name filter and pages by number, so the filter, ``offset``
    and ``total`` are applied here over the full list of checkpoints.
    """
    limit = max(int(args.get("limit", 20)), 1)
    offset = max(int(args.get("offset", 0)), 0)
    pattern = str(args.get("name_pattern", "")).lower()
    with_descriptions = bool(args.get("include_descriptions", False))

    matches = []
    for item, saved in _iter_checkpoints(client):
        name = str(saved.get("checkpoint_name") or str(item.get("content", ""))[:100])
        if pattern and pattern not in name.lower():
            continue
        context = {
            "id": item.get("id"),
            "name": name,
            "memory_count": saved.get("memory_count", 0),
            "created": item.get("created_at"),
        }
        if with_descriptions:
            context["description"] = saved.get("description", item.get("content", ""))
        matches.append(context)

    return {
        "contexts": matches[offset : offset + limit],
        "total": len(matches),
        "limit": limit,
        "offset": offset,
    }


def _find_checkpoint(client: PenfieldClient, name: str) -> dict[str, Any] | None:
    """Find a checkpoint memory by exact name, or by its id."""
    if _UUID_PATTERN.fullmatch(name):
        try:
            found = client.call("memory_get", path_params={"memory_id": name})
        except NotFoundError:
            found = None
        if isinstance(found, dict) and found.get("memory_type") == "checkpoint":
            return found
    for item, saved in _iter_checkpoints(client):
        if saved.get("checkpoint_name") == name:
            return item
    return None


def _fetch_memories(client: PenfieldClient, memory_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch memories by id, a few at a time, keeping the given order.

    Memories that no longer exist are left out.
    """

    def fetch(memory_id: str) -> dict[str, Any] | None:
        try:
            found = client.call("memory_get", path_params={"memory_id": memory_id})
        except PenfieldError:
            return None
        return found if isinstance(found, dict) else None

    if not memory_ids:
        return []
    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(memory_ids))) as pool:
        return [m for m in pool.map(fetch, memory_ids) if m is not None]


@_tool("penfield_restore_context")
def _restore_context(client: PenfieldClient, args: dict[str, Any]) -> dict[str, Any]:
    """Restore a saved context by name or id.

    Returns the saved description and the memories the checkpoint recorded,
    up to ``limit``. The name ``awakening`` returns the personality briefing.
    """
    name = str(args["name"])
    limit = max(int(args.get("limit", 20)), 1)

    if name.lower() == "awakening":
        return client.call("personality_awakening")

    checkpoint = _find_checkpoint(client, name)
    if checkpoint is None:
        return {"error": f"No checkpoint found with name '{name}'"}

    saved = _checkpoint_payload(checkpoint)
    saved_ids = [str(m) for m in saved.get("memory_ids") or []]
    referenced = [str(m) for m in saved.get("referenced_memories") or []]
    all_ids = list(dict.fromkeys(saved_ids + referenced))
    memories = [_memory_view(m) for m in _fetch_memories(client, all_ids[:limit])]

    return {
        "context_id": checkpoint.get("id"),
        "name": saved.get("checkpoint_name", name),
        "description": saved.get("description", checkpoint.get("content", "")),
        "memories": memories,
        "count": len(memories),
        "saved_count": len(all_ids),
        "truncated": len(all_ids) > limit,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _memory_view(
    memory: dict[str, Any], *, max_chars: int = RECALL_CONTENT_MAX_CHARS
) -> dict[str, Any]:
    """Shape one memory for the agent: whole content, capped, with its metadata.

    Accepts both a search hit (fields under ``metadata``) and a memory record
    (fields at the top level). Content longer than ``max_chars`` is cut and
    flagged so the agent knows to fetch the rest by id.
    """
    raw_meta = memory.get("metadata")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    is_document = memory.get("source_type") == DOCUMENT_SOURCE_TYPE
    # A document hit is one chunk; the API also sends it with its neighbours.
    content = str(
        (memory.get("content_with_context") if is_document else None)
        or memory.get("content")
        or memory.get("snippet")
        or ""
    )

    def field(name: str) -> Any:
        value = memory.get(name)
        return value if value is not None else meta.get(name)

    view: dict[str, Any] = {
        "id": memory.get("id"),
        "content": content[:max_chars],
        "memory_type": field("memory_type"),
        "importance": field("importance"),
        "tags": field("tags") or [],
        "created_at": field("created_at"),
    }
    if is_document:
        view["source_type"] = "document"
        for key in ("filename", "document_id", "chunk_index", "total_chunks"):
            if field(key) is not None:
                view[key] = field(key)
    if "score" in memory:
        view["score"] = memory["score"]
    if len(content) > max_chars:
        view["truncated"] = True
    return view
