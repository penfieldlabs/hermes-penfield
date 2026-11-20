# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Constants for the Penfield v2 API: hosts, endpoints, limits and enums.

Transcribed from the public Penfield API docs
(``github.com/penfieldlabs/docs``). The client, the tool schemas and the
tests all read them from here, so an endpoint that moves is changed in
one place.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------
# prod uses the bare hosts, dev the *-dev hosts. See ADR-0003.
PROD_HOSTS = {
    "api": "api.penfield.app",
    "auth": "auth.penfield.app",
    "portal": "portal.penfield.app",
    "mcp": "mcp.penfield.app",
}
DEV_HOSTS = {
    "api": "api-dev.penfield.app",
    "auth": "auth-dev.penfield.app",
    "portal": "portal-dev.penfield.app",
    "mcp": "mcp-dev.penfield.app",
}

# API path prefix. All real endpoints live under /api/v2.
API_PREFIX = "/api/v2"

# ---------------------------------------------------------------------------
# Endpoints (real contract). Method + path only; bodies live in tools.py.
# ---------------------------------------------------------------------------
# Each entry documents the HTTP method and the path relative to API_PREFIX.
# Paths may contain {placeholders} for path params.
ENDPOINTS: dict[str, tuple[str, str]] = {
    # Auth
    "auth_token": ("POST", "/auth/token"),  # API-key -> JWT exchange
    "auth_refresh": ("POST", "/auth/refresh"),  # refresh API-key-derived tokens
    "auth_verify": ("GET", "/auth/verify"),
    # Memories
    "memory_create": ("POST", "/memories"),
    "memory_get": ("GET", "/memories/{memory_id}"),
    "memory_list": ("GET", "/memories"),
    "memory_update": ("PUT", "/memories/{memory_id}"),  # PUT, not PATCH
    "memory_delete": ("DELETE", "/memories/{memory_id}"),
    # Search
    "search_hybrid": ("POST", "/search/hybrid"),
    "search_vector": ("POST", "/search/vector"),
    "search_stats": ("GET", "/search/stats"),
    # Relationships
    "relationship_create": ("POST", "/relationships"),
    "relationship_get": ("GET", "/relationships/{relationship_id}"),
    "relationship_list": ("GET", "/relationships"),
    "relationship_update": ("PATCH", "/relationships/{relationship_id}"),
    "relationship_delete": ("DELETE", "/relationships/{relationship_id}"),
    "relationship_traverse": ("POST", "/relationships/traverse"),
    "relationship_delete_between": ("DELETE", "/relationships/between"),
    # Analysis
    "analysis_reflect": ("POST", "/analysis/reflect"),
    "analysis_summarize": ("POST", "/analysis/summarize"),
    "analysis_insights": ("GET", "/analysis/insights"),
    # Artifacts (keyed by path, not by id)
    "artifact_save": ("POST", "/artifacts"),
    "artifact_retrieve": ("GET", "/artifacts"),  # ?path=
    "artifact_list": ("GET", "/artifacts/list"),  # ?prefix=
    "artifact_delete": ("DELETE", "/artifacts"),  # ?path=
    # Personality / awakening
    "personality_awakening": ("GET", "/personality/awakening"),
}

# ---------------------------------------------------------------------------
# Rate limiting, timeouts and retry
# ---------------------------------------------------------------------------
# Unofficial observed limit on the Penfield API. Client-side sliding window
# enforces it conservatively; see ADR-0002.
DEFAULT_MAX_RPM = 250
BACKOFF_BASE = 1.0
BACKOFF_MAX = 60.0
BACKOFF_FACTOR = 2.0
# A tool call is something the agent waits on, so the budget is short: one
# request plus at most two retries.
MAX_RETRIES = 2
REQUEST_TIMEOUT = 15.0

# Three hooks run while the user waits, and Hermes puts no time limit on
# them. Each does its work on a background thread and stops waiting at a
# deadline, whatever the request is doing. See ADR-0013.
#
# The briefing: Hermes builds the system prompt once and keeps it for the
# whole session, so a briefing that misses the build is missing for good.
# It is worth a longer wait, once, and a second attempt.
BRIEFING_TIMEOUT = 5.0
BRIEFING_DEADLINE = 6.0
# Auto-recall: runs before every turn, so it gets one short attempt.
RECALL_TIMEOUT = 2.5
RECALL_DEADLINE = 2.5
# The checkpoint before a compression: the compression itself takes many
# seconds, and the checkpoint keeps saving in the background if it is late.
CHECKPOINT_DEADLINE = 8.0
# `hermes memory setup` checks a new API key while the user waits at a prompt.
SETUP_CHECK_TIMEOUT = 10.0

#: Where a user creates an API key.
API_KEYS_URL = "https://portal.penfield.app/settings/api-keys"

# Requests that create something new on every call. Repeating one after a
# timeout or a 5xx can store the same thing twice, so these are never
# retried on an ambiguous failure. Every other endpoint is a read or an
# overwrite and is safe to repeat.
WRITE_ONCE_ENDPOINTS = frozenset({"memory_create", "relationship_create"})

# Refresh access tokens a little before they expire to avoid 401 races.
REFRESH_SKEW_SECONDS = 300.0

# The plugin version. plugin.yaml carries the same value for Hermes; a test
# keeps the two in step.
VERSION = "0.3.0"
USER_AGENT = f"hermes-penfield/{VERSION}"

# ---------------------------------------------------------------------------
# Enums (transcribed from docs/api/schemas/enums.md)
# ---------------------------------------------------------------------------
MEMORY_TYPES = (
    "fact",
    "insight",
    "conversation",
    "correction",
    "reference",
    "task",
    "checkpoint",
    "identity_core",
    "personality_trait",
    "relationship",
    "strategy",
)

# Source types to filter a search by. The API documents more values
# (web_scrape, reflection, checkpoint_recall), but a search whose
# ``source_types`` list holds one of those, or any value the API does not
# know, returns no results at all and no error. These are the ones that
# filter correctly, checked against the live API.
DOCUMENT_SOURCE_TYPE = "document_upload"

# How the hybrid search weighs its three signals. The API's own default is
# even between keywords and meaning; penfield_search leans on meaning, which
# is what sets it apart from penfield_recall. Each set sums to 1.
SEMANTIC_SEARCH_WEIGHTS = {"bm25_weight": 0.1, "vector_weight": 0.7, "graph_weight": 0.2}
MEMORY_SOURCE_TYPES = ("direct_input", "conversation", "api_import", "checkpoint")

# 24 relationship types (docs verified working as of 2026-01-22).
RELATIONSHIP_TYPES = (
    "supersedes",
    "updates",
    "evolution_of",
    "supports",
    "contradicts",
    "disputes",
    "parent_of",
    "child_of",
    "sibling_of",
    "composed_of",
    "part_of",
    "causes",
    "influenced_by",
    "prerequisite_for",
    "implements",
    "documents",
    "tests",
    "example_of",
    "responds_to",
    "references",
    "inspired_by",
    "follows",
    "precedes",
    "depends_on",
)

DIRECTION_TYPES = ("DIRECTED", "BIDIRECTIONAL", "INVERSE_PAIRED")

# memory_type values that are managed via /personality, not /memories.
PROTECTED_MEMORY_TYPES = frozenset({"identity_core", "personality_trait"})
