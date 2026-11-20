# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""A local HTTP stand-in for the Penfield REST API (v2).

It speaks real HTTP on a loopback port, so it works for code running in
another process (Hermes loads plugins in a separate plugin host when
``plugins.isolation`` is ``host``). Response shapes follow the public API
reference at github.com/penfieldlabs/docs.

The fake is deliberately small: enough behaviour for the plugin's request
paths (auth, memories, hybrid search, relationships, reflect, artifacts,
awakening) plus fault injection for outage tests.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

API_PREFIX = "/api/v2"

#: Source types the live search endpoint filters on. The API documents more
#: (web_scrape, reflection, checkpoint_recall), but a ``source_types`` list
#: holding one of those, or any unknown value, returns no results at all.
SOURCE_TYPES = frozenset(
    {"direct_input", "document_upload", "conversation", "api_import", "checkpoint"}
)

#: Time windows ``POST /analysis/reflect`` accepts. Anything else is a 422.
TIME_WINDOWS = ("recent", "today", "week", "month")

DEFAULT_BRIEFING = "=== AWAKENING BRIEFING ===\nYou are a test persona."


@dataclass
class Recorded:
    """One request the fake received."""

    method: str
    path: str  # relative to /api/v2, without the query string
    query: dict[str, str]
    body: Any
    authorization: str
    user_agent: str
    at: float = field(default_factory=time.time)  # wall clock, comparable across processes


class FakePenfield:
    """In-memory Penfield API served over loopback HTTP."""

    def __init__(self) -> None:
        self.briefing = DEFAULT_BRIEFING
        self.memories: dict[str, dict[str, Any]] = {}
        self.relationships: list[dict[str, Any]] = []
        self.artifacts: dict[str, str] = {}
        self.requests: list[Recorded] = []
        #: ``{(method, path): [status, ...]}`` consumed one status per request.
        self.fail_next: dict[tuple[str, str], list[int]] = {}
        #: ``"errors"`` answers 503 and ``"hang"`` never answers; auth endpoints
        #: keep working either way, so the plugin is logged in when the API fails.
        self.outage: str | None = None
        #: API keys without the ``delete`` permission cannot delete memories.
        self.allow_memory_delete = True
        #: API keys the server refuses, as it does a revoked or mistyped one.
        self.rejected_api_keys: set[str] = set()
        self._release = threading.Event()
        #: Refresh tokens that are still valid (rotation invalidates old ones).
        self.valid_refresh_tokens: set[str] = set()
        self.valid_access_tokens: set[str] = set()
        self._lock = threading.Lock()
        self._seq = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> FakePenfield:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:  # keep test output quiet
                return

            def _handle(self) -> None:
                fake._serve(self)

            # http.server dispatches on these exact method names.
            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle  # noqa: N815

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._release.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> FakePenfield:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    @property
    def url(self) -> str:
        """API base URL, suitable for ``PENFIELD_URL``."""
        assert self._server is not None, "start() the fake first"
        return f"http://127.0.0.1:{self._server.server_address[1]}{API_PREFIX}"

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------
    def seed_memory(
        self,
        content: str,
        *,
        memory_type: str = "fact",
        tags: list[str] | None = None,
        importance: float = 0.5,
        source_type: str = "direct_input",
        memory_id: str | None = None,
        created_at: str | None = None,
        about: list[str] | None = None,
    ) -> str:
        """Insert a memory directly and return its id.

        ``about`` lists words the memory is about without containing them:
        the fake's stand-in for what an embedding would pick up.
        """
        with self._lock:
            mem = self._insert(
                {
                    "content": content,
                    "memory_type": memory_type,
                    "tags": tags or [],
                    "importance": importance,
                    "source_type": source_type,
                },
                memory_id=memory_id,
            )
            if created_at:
                mem["created_at"] = mem["updated_at"] = created_at
            mem["_about"] = [w.lower() for w in about or []]
            return str(mem["id"])

    def seed_document(self, filename: str, chunks: list[str]) -> list[str]:
        """Insert an uploaded document as the API stores it: one memory per chunk."""
        document_id = str(uuid.uuid4())
        ids = []
        with self._lock:
            for index, text in enumerate(chunks):
                mem = self._insert(
                    {
                        "content": text,
                        "importance": 0.7,
                        "source_type": "document_upload",
                        "metadata": {"filename": filename, "document_id": document_id},
                    }
                )
                mem["_chunk"] = (index, len(chunks), document_id)
                ids.append(str(mem["id"]))
        return ids

    def seed_checkpoint(self, name: str, *, description: str = "", memory_ids: list[str]) -> str:
        """Insert a context checkpoint in the format the API stores them."""
        payload = {
            "checkpoint_name": name,
            "description": description,
            "memory_count": len(memory_ids),
            "memory_ids": memory_ids,
            "referenced_memories": [],
        }
        return self.seed_memory(
            json.dumps(payload), memory_type="checkpoint", tags=["checkpoint", "context", name]
        )

    def calls(self, method: str | None = None, path: str | None = None) -> list[Recorded]:
        """Recorded requests, optionally filtered by method and exact path."""
        with self._lock:
            return [
                r
                for r in self.requests
                if (method is None or r.method == method) and (path is None or r.path == path)
            ]

    def reset_calls(self) -> None:
        with self._lock:
            self.requests.clear()

    # ------------------------------------------------------------------
    # Request handling
    # ------------------------------------------------------------------
    def _serve(self, h: BaseHTTPRequestHandler) -> None:
        split = urlsplit(h.path)
        path = split.path[len(API_PREFIX) :] if split.path.startswith(API_PREFIX) else split.path
        query = {k: v[0] for k, v in parse_qs(split.query).items()}
        length = int(h.headers.get("Content-Length") or 0)
        raw = h.rfile.read(length) if length else b""
        try:
            body: Any = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = raw.decode("utf-8", "replace")
        rec = Recorded(
            method=h.command,
            path=path,
            query=query,
            body=body,
            authorization=h.headers.get("Authorization", ""),
            user_agent=h.headers.get("User-Agent", ""),
        )
        with self._lock:
            self.requests.append(rec)
            outage = None if path.startswith("/auth/") else self.outage
        if outage == "hang":
            self._release.wait(timeout=300)
            h.close_connection = True
            return
        with self._lock:
            forced = [503] if outage == "errors" else self.fail_next.get((rec.method, path))
            status_override = forced.pop(0) if forced else None
            if status_override is not None:
                status, payload = status_override, _error(status_override, "injected failure")
            else:
                status, payload = self._route(rec)
        data = b"" if status == 204 else json.dumps(payload).encode()
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    def _route(self, r: Recorded) -> tuple[int, dict[str, Any]]:
        m, p = r.method, r.path
        if (m, p) == ("POST", "/auth/token"):
            key = r.authorization.removeprefix("Bearer ")
            if not key.startswith("tm_") or key in self.rejected_api_keys:
                return 401, _error(401, "invalid API key")
            return 200, _ok(self._issue_tokens())
        if (m, p) == ("POST", "/auth/refresh"):
            token = (r.body or {}).get("refresh_token") if isinstance(r.body, dict) else None
            if token not in self.valid_refresh_tokens:
                return 401, _error(401, "invalid refresh token")
            self.valid_refresh_tokens.discard(token)
            return 200, _ok(self._issue_tokens())
        if r.authorization.removeprefix("Bearer ") not in self.valid_access_tokens:
            return 401, _error(401, "invalid or expired token")

        if (m, p) == ("GET", "/personality/awakening"):
            return 200, _ok({"briefing": self.briefing})
        if (m, p) == ("POST", "/memories"):
            return self._create_memory(r.body or {})
        if (m, p) == ("GET", "/memories"):
            return 200, _ok(self._list_memories(r.query))
        if p.startswith("/memories/"):
            return self._memory_by_id(m, p.split("/", 2)[2], r.body or {})
        if (m, p) == ("POST", "/search/hybrid"):
            return self._search(r.body or {})
        if (m, p) == ("GET", "/search/stats"):
            return 200, _ok(
                {
                    "total_memories": len(self.memories),
                    "total_relationships": len(self.relationships),
                }
            )
        if (m, p) == ("POST", "/relationships"):
            return self._create_relationship(r.body or {})
        if (m, p) == ("DELETE", "/relationships/between"):
            before = len(self.relationships)
            self.relationships = [
                x
                for x in self.relationships
                if not (
                    x["from_id"] == r.query.get("from_id") and x["to_id"] == r.query.get("to_id")
                )
            ]
            return 200, _ok({"deleted": before - len(self.relationships)})
        if (m, p) == ("POST", "/relationships/traverse"):
            return 200, _ok(self._traverse(r.body or {}))
        if (m, p) == ("POST", "/analysis/reflect"):
            return self._reflect(r.body or {})
        if p == "/artifacts" or p == "/artifacts/list":
            return self._artifacts(m, p, r)
        return 404, _error(404, f"no route for {m} {p}")

    # -- auth -----------------------------------------------------------
    def _issue_tokens(self) -> dict[str, Any]:
        self._seq += 1
        access, refresh = f"access-{self._seq}", f"refresh-{self._seq}"
        self.valid_access_tokens.add(access)
        self.valid_refresh_tokens.add(refresh)
        return {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": 86400,
            "tenant_id": "pf_test",
            "refresh_token": refresh,
        }

    # -- memories -------------------------------------------------------
    def _insert(self, fields: dict[str, Any], *, memory_id: str | None = None) -> dict[str, Any]:
        self._seq += 1
        mid = memory_id or str(uuid.UUID(int=self._seq))
        # Monotonic, ISO-sortable timestamps keep ordering deterministic.
        stamp = f"2026-01-01T00:{self._seq // 60 % 60:02d}:{self._seq % 60:02d}.{self._seq:06d}Z"
        mem = {
            "id": mid,
            "content": fields.get("content", ""),
            "memory_type": fields.get("memory_type") or "fact",
            "importance": fields.get("importance", 0.5),
            "confidence": 0.8,
            "source_type": fields.get("source_type") or "direct_input",
            "tags": list(fields.get("tags") or []),
            "created_at": stamp,
            "updated_at": stamp,
            "metadata": dict(fields.get("metadata") or {}),
            "embedding_status": "completed",
            "relationships": [],
            "_order": self._seq,
        }
        self.memories[mid] = mem
        return mem

    def _create_memory(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        content = body.get("content")
        if not isinstance(content, str) or not content:
            return 422, _invalid("content", "Field required")
        if len(content) > 10_000:
            return 422, _invalid("content", "String should have at most 10000 characters")
        return 201, _ok(_public(self._insert(body)))

    def _memory_by_id(
        self, method: str, memory_id: str, body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        mem = self.memories.get(memory_id)
        if mem is None:
            return 404, _error(404, "memory not found")
        if method == "GET":
            return 200, _ok(_public(mem))
        if method == "PUT":
            for key in ("content", "importance", "tags"):
                if key in body:
                    mem[key] = body[key]
            return 200, _ok(_public(mem))
        if method == "DELETE":
            if not self.allow_memory_delete:
                return 403, _error(403, "API key lacks the delete permission")
            del self.memories[memory_id]
            return 204, {}
        return 405, _error(405, "method not allowed")

    def _list_memories(self, query: dict[str, str]) -> dict[str, Any]:
        per_page = min(int(query.get("per_page", 20)), 100)
        page = max(int(query.get("page", 1)), 1)
        rows = sorted(self.memories.values(), key=lambda x: x["_order"], reverse=True)
        if query.get("memory_type"):
            rows = [x for x in rows if x["memory_type"] == query["memory_type"]]
        total = len(rows)
        pages = max((total + per_page - 1) // per_page, 1)
        chunk = rows[(page - 1) * per_page : page * per_page]
        return {
            "items": [_public(x) for x in chunk],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": pages,
                "has_next": page < pages,
                "has_prev": page > 1,
            },
        }

    # -- search ---------------------------------------------------------
    def _search(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        query = str(body.get("query") or "")
        if not query:
            return 422, _invalid("query", "Field required")
        source_types = body.get("source_types")
        if source_types is not None and not isinstance(source_types, list):
            return 422, _invalid("source_types", "Input should be a valid list")
        if body.get("tags") is not None and not isinstance(body["tags"], str):
            return 422, _invalid("tags", "Input should be a valid string")
        words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2}
        wanted_tags = {t for t in str(body.get("tags") or "").split(",") if t}
        threshold = float(body.get("min_score_threshold") or 0.0)
        start, end = str(body.get("start_date") or ""), str(body.get("end_date") or "")
        # The live score is 0.4 x keywords (relative to the best keyword hit of
        # this search) + 0.4 x meaning + 0.2 x graph, unless the request sets
        # its own weights. The fake has no graph signal.
        bm25_weight = float(body.get("bm25_weight", 0.4))
        vector_weight = float(body.get("vector_weight", 0.4))
        graph_weight = float(body.get("graph_weight", round(1 - bm25_weight - vector_weight, 6)))
        candidates = []
        # One value the API does not know empties the whole result, silently.
        searchable = source_types is None or all(s in SOURCE_TYPES for s in source_types)
        for mem in self.memories.values() if searchable else ():
            if source_types is not None and mem["source_type"] not in source_types:
                continue
            if wanted_tags and not wanted_tags & set(mem["tags"]):
                continue
            if (start and mem["created_at"] < start) or (end and mem["created_at"][:10] > end):
                continue
            text = mem["content"].lower()
            keyword = sum(1 for w in words if w in text) / max(len(words), 1)
            meaning = sum(1 for w in words if w in text or w in mem.get("_about", ())) / max(
                len(words), 1
            )
            if keyword or meaning:
                candidates.append((keyword, meaning, mem))
        best_keyword = max((keyword for keyword, _, _ in candidates), default=0.0)
        scored = []
        for keyword, meaning, mem in candidates:
            relative = keyword / best_keyword if best_keyword else 0.0
            score = round(bm25_weight * relative + vector_weight * meaning, 3)
            if score >= threshold:
                scored.append((score, mem["_order"], mem))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        limit = int(body.get("limit") or 20)
        items = [self._search_item(mem, score) for score, _order, mem in scored[:limit]]
        weights = {"bm25": bm25_weight, "vector": vector_weight, "graph": graph_weight}
        return 200, _ok(
            {
                "items": items,
                "query": query,
                "analyzed_query": {"original": query, "weights": weights},
            }
        )

    def _search_item(self, mem: dict[str, Any], score: float) -> dict[str, Any]:
        """One search hit in the live API's shape."""
        item = {
            "id": mem["id"],
            "content": mem["content"],
            "snippet": mem["content"][:200],
            "score": score,
            "score_breakdown": {"final_score": score},
            "metadata": {
                "memory_type": mem["memory_type"],
                "importance": mem["importance"],
                "highlight": None,
            },
            "source_type": mem["source_type"],
            "created_at": mem["created_at"],
            "tags": mem["tags"],
            "content_with_context": None,
            "chunk_index": None,
            "total_chunks": None,
        }
        if "_chunk" in mem:
            index, total, document_id = mem["_chunk"]
            siblings = sorted(
                (
                    m
                    for m in self.memories.values()
                    if m.get("_chunk", (0, 0, ""))[2] == document_id
                ),
                key=lambda m: m["_chunk"][0],
            )
            nearby = siblings[max(index - 2, 0) : index + 3]
            item["metadata"].update(mem["metadata"])
            item["content_with_context"] = "\n\n".join(m["content"] for m in nearby)
            item["chunk_index"], item["total_chunks"] = index, total
        return item

    # -- graph ----------------------------------------------------------
    def _create_relationship(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        for key in ("from_id", "to_id", "relationship_type"):
            if not body.get(key):
                return 422, _invalid(key, "Field required")
        if body["from_id"] not in self.memories or body["to_id"] not in self.memories:
            return 404, _error(404, "memory not found")
        self._seq += 1
        rel = {
            "id": str(uuid.UUID(int=self._seq)),
            "from_id": body["from_id"],
            "to_id": body["to_id"],
            "relationship_type": body["relationship_type"],
            "direction_type": "DIRECTED",
            "strength": body.get("strength", 0.5),
        }
        self.relationships.append(rel)
        return 201, _ok(rel)

    def _traverse(self, body: dict[str, Any]) -> dict[str, Any]:
        start = body.get("start_memory_id")
        paths = [
            {
                "nodes": [rel["from_id"], rel["to_id"]],
                "relationships": [rel["id"]],
                "total_score": rel["strength"],
                "path_metadata": {"depth": 1, "start_node": start, "direction": "ANY"},
            }
            for rel in self.relationships
            if start in (rel["from_id"], rel["to_id"])
        ]
        return {
            "paths": paths,
            "visited_chunks": sorted({n for path in paths for n in path["nodes"]}),
            "max_depth_reached": 1 if paths else 0,
            "total_paths_found": len(paths),
        }

    def _reflect(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        window = body.get("time_window", "recent")
        if window not in TIME_WINDOWS:
            return 422, _invalid(
                "time_window", "Input should be 'recent', 'today', 'week' or 'month'"
            )
        start, end = str(body.get("start_date") or ""), str(body.get("end_date") or "")
        rows = [
            m
            for m in sorted(self.memories.values(), key=lambda x: x["_order"], reverse=True)
            if not (start and m["created_at"] < start) and not (end and m["created_at"][:10] > end)
        ][:50]
        return 200, _ok(
            {
                "time_window": window,
                "memories": [_public(x) for x in rows],
                "active_topics": [],
                "memory_clusters": [],
                "emerging_patterns": [],
                "relationship_insights": {},
                "contexts": [],
                "statistics": {
                    "total_memories_analyzed": len(rows),
                    "memories_returned": len(rows),
                },
            }
        )

    # -- artifacts ------------------------------------------------------
    def _artifacts(self, method: str, path: str, r: Recorded) -> tuple[int, dict[str, Any]]:
        if path == "/artifacts/list":
            prefix = (r.query.get("prefix") or "/").rstrip("/") + "/"
            pattern = (r.query.get("name_pattern") or "").lower()
            folders: set[str] = set()
            files = []
            for full, content in sorted(self.artifacts.items()):
                if not full.startswith(prefix):
                    continue
                head, _, rest = full[len(prefix) :].partition("/")
                if pattern and pattern not in head.lower():
                    continue
                if rest:
                    folders.add(head)
                else:
                    files.append(
                        {
                            "name": head,
                            "size": len(content),
                            "content_type": "text/markdown",
                            "last_modified": "2026-01-01T00:00:00+00:00",
                        }
                    )
            return 200, _ok(
                {
                    "success": True,
                    "path": prefix.rstrip("/") or "/",
                    "folders": sorted(folders),
                    "files": files,
                }
            )
        if method == "POST":
            body = r.body or {}
            self.artifacts[body["path"]] = body["content"]
            return 200, _ok(
                {
                    "success": True,
                    "message": "Artifact saved successfully",
                    "path": body["path"],
                    "artifact_id": str(uuid.uuid4()),
                    "content_type": "text/markdown",
                    "size": len(body["content"]),
                    "storage_path": f"artifacts{body['path']}",
                }
            )
        target = r.query.get("path", "")
        if target not in self.artifacts:
            return 404, _error(404, "artifact not found")
        if method == "GET":
            return 200, _ok(
                {
                    "success": True,
                    "content": self.artifacts[target],
                    "path": target,
                    "content_type": "text/markdown",
                    "size": len(self.artifacts[target]),
                }
            )
        if method == "DELETE":
            del self.artifacts[target]
            return 200, _ok({"message": f"Artifact deleted: {target}"})
        return 405, _error(405, "method not allowed")


def _public(mem: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in mem.items() if not k.startswith("_")}


def _ok(data: Any) -> dict[str, Any]:
    return {"status": "success", "data": data, "meta": {"version": "2.0.0", "request_id": "test"}}


def _invalid(field: str, message: str) -> dict[str, Any]:
    """A 422 body in the live API's shape: the useful text is per field."""
    return {
        "status": "error",
        "error": {
            "code": "VAL_VALIDATION_FAILED",
            "message": "Request validation failed",
            "details": {"validation_errors": [{"field": field, "message": message}]},
            "field_errors": {field: [message]},
        },
        "meta": {"version": "2.0.0", "request_id": "test"},
    }


def _error(status: int, message: str) -> dict[str, Any]:
    return {
        "status": "error",
        "error": {"code": f"HTTP_{status}", "message": message},
        "meta": {"request_id": "test"},
    }


class Blackhole:
    """A listening socket that accepts connections and never answers.

    Clients connect successfully and then wait for a response until their
    own timeout fires, which is how a hung or overloaded API looks.
    """

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(64)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._sock.getsockname()[1]}{API_PREFIX}"

    def close(self) -> None:
        self._sock.close()

    def __enter__(self) -> Blackhole:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def refused_url() -> str:
    """An API base URL on a loopback port nothing listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}{API_PREFIX}"
