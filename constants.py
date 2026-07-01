# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Authoritative constants for the Penfield v2 API.

These values are transcribed from the public Penfield docs
(``github.com/penfieldlabs/docs``) and verified against the live
``api-dev.penfield.app`` contract during v0.2.0 development. Keeping them
in one module means the client, the tool schemas, and the tests share a
single source of truth — there is exactly one place to change when an
endpoint moves.


"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------
# The user confirmed the convention during v0.2.0 development:
#   prod -> bare hosts, dev -> *-dev hosts. See ADR-0010.
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
    # Memories (spec: POST /store, GET/PATCH /memories/{id})
    "memory_create": ("POST", "/memories"),
    "memory_get": ("GET", "/memories/{memory_id}"),
    "memory_list": ("GET", "/memories"),
    "memory_update": ("PUT", "/memories/{memory_id}"),  # NOT PATCH — API contract
    "memory_delete": ("DELETE", "/memories/{memory_id}"),
    # Search (spec: POST /recall, POST /search)
    "search_hybrid": ("POST", "/search/hybrid"),
    "search_vector": ("POST", "/search/vector"),
    "search_stats": ("GET", "/search/stats"),
    # Relationships (spec: POST /connect, POST /explore)
    "relationship_create": ("POST", "/relationships"),
    "relationship_get": ("GET", "/relationships/{relationship_id}"),
    "relationship_list": ("GET", "/relationships"),
    "relationship_update": ("PATCH", "/relationships/{relationship_id}"),
    "relationship_delete": ("DELETE", "/relationships/{relationship_id}"),
    "relationship_traverse": ("POST", "/relationships/traverse"),
    "relationship_delete_between": ("DELETE", "/relationships/between"),
    # Analysis (spec: POST /reflect)
    "analysis_reflect": ("POST", "/analysis/reflect"),
    "analysis_summarize": ("POST", "/analysis/summarize"),
    "analysis_insights": ("GET", "/analysis/insights"),
    # Artifacts (spec: GET /artifacts/{id} — real API is path-keyed, not id-keyed)
    "artifact_save": ("POST", "/artifacts"),
    "artifact_retrieve": ("GET", "/artifacts"),  # ?path=
    "artifact_list": ("GET", "/artifacts/list"),  # ?prefix=
    "artifact_delete": ("DELETE", "/artifacts"),  # ?path=
    # Personality / awakening (spec: POST /awaken — real is GET personality/awakening)
    "personality_awakening": ("GET", "/personality/awakening"),
}

# ---------------------------------------------------------------------------
# Rate limiting / retry
# ---------------------------------------------------------------------------
# Unofficial observed limit on the Penfield API. Client-side sliding window
# enforces it conservatively; see ADR-0002.
DEFAULT_MAX_RPM = 250
BACKOFF_BASE = 1.0
BACKOFF_MAX = 60.0
BACKOFF_FACTOR = 2.0
MAX_RETRIES = 5
REQUEST_TIMEOUT = 30.0

# Refresh access tokens a little before they expire to avoid 401 races.
REFRESH_SKEW_SECONDS = 300.0

USER_AGENT = "hermes-penfield/0.2.0"

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

SOURCE_TYPES = (
    "direct_input",
    "document_upload",
    "web_scrape",
    "api_import",
    "conversation",
    "reflection",
    "checkpoint",
    "checkpoint_recall",
)

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
