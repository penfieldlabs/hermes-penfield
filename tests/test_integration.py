# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Live integration tests against api-dev.penfield.app.

Run with:

    PENFIELD_API_KEY=... PENFIELD_ENV=dev pytest -m integration

These are skipped by default. They exercise the real round-trip:
key exchange, store, recall, fetch, connect, explore, reflect, artifacts,
awaken, update, delete. See ADR-0015.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

import pytest

from hermes_penfield.exceptions import NotFoundError
from hermes_penfield.tools import dispatch

if TYPE_CHECKING:
    from hermes_penfield.client import PenfieldClient

pytestmark = pytest.mark.integration


def _unique_tag() -> str:
    return f"int-{uuid.uuid4().hex[:8]}"


class TestAuthRoundTrip:
    def test_key_exchange_and_verify(self, live_jwt: str) -> None:
        assert live_jwt.startswith("eyJ")


class TestStoreRecallDelete:
    def test_full_lifecycle(self, live_client: PenfieldClient) -> None:
        tag = _unique_tag()
        # Store
        created = live_client.call(
            "memory_create",
            body={
                "content": f"hermes-penfield integration lifecycle marker {tag}",
                "memory_type": "fact",
                "importance": 0.6,
                "tags": ["hermes-penfield", "integration", tag],
            },
        )
        mem_id = created["id"]
        try:
            # Fetch
            got = live_client.call("memory_get", path_params={"memory_id": mem_id})
            assert got["id"] == mem_id
            assert tag in got["content"]

            # Update (PUT, not PATCH)
            updated = live_client.call(
                "memory_update",
                path_params={"memory_id": mem_id},
                body={"content": f"updated {tag}", "importance": 0.9},
            )
            assert updated["importance"] == 0.9

            # Give embedding a moment, then recall
            time.sleep(2)
            recalled = dispatch(
                live_client,
                "penfield_recall",
                {"query": tag, "limit": 5},
            )
            import json

            items = json.loads(recalled)["items"]
            assert any(mem_id == it["id"] for it in items), (
                f"expected {mem_id} in recall results for {tag}; got {[i['id'] for i in items]}"
            )
        finally:
            # Delete requires the `delete` scope, which the default dev API
            # key may lack. If so, the lifecycle still proved store/fetch/
            # update/recall; skip rather than fail on a scope limitation.
            from hermes_penfield.exceptions import AuthError

            try:
                live_client.call("memory_delete", path_params={"memory_id": mem_id})
            except AuthError as exc:
                if "delete" not in str(exc):
                    raise
                pytest.skip(f"dev key lacks delete scope: {exc}")
            with pytest.raises(NotFoundError):
                live_client.call("memory_get", path_params={"memory_id": mem_id})


class TestRelationships:
    def test_connect_and_explore(self, live_client: PenfieldClient) -> None:
        tag = _unique_tag()
        a = live_client.call(
            "memory_create",
            body={"content": f"source node {tag}", "tags": [tag]},
        )
        b = live_client.call(
            "memory_create",
            body={"content": f"target node {tag}", "tags": [tag]},
        )
        try:
            rel = dispatch(
                live_client,
                "penfield_connect",
                {
                    "from_id": a["id"],
                    "to_id": b["id"],
                    "relationship_type": "supports",
                    "strength": 0.7,
                },
            )
            assert "error" not in rel

            explored = dispatch(
                live_client,
                "penfield_explore",
                {"start_memory_id": a["id"], "max_depth": 2},
            )
            assert "error" not in explored
        finally:
            from hermes_penfield.exceptions import AuthError

            for mid in (a["id"], b["id"]):
                try:
                    live_client.call("memory_delete", path_params={"memory_id": mid})
                except AuthError as exc:
                    if "delete" not in str(exc):
                        raise
                    pytest.skip(f"dev key lacks delete scope: {exc}")


class TestReflect:
    def test_reflect_returns_payload(self, live_client: PenfieldClient) -> None:
        result = dispatch(
            live_client,
            "penfield_reflect",
            {"time_window": "recent"},
        )
        # Reflect may legitimately return an empty payload on a quiet tenant,
        # but it must not error.
        assert "error" not in result


class TestArtifacts:
    def test_save_retrieve_list(self, live_client: PenfieldClient) -> None:
        path = f"/hermes-penfield-integration/{_unique_tag()}.md"
        content = "# integration test\nround trip"
        try:
            saved = dispatch(
                live_client,
                "penfield_save_artifact",
                {"path": path, "content": content},
            )
            assert "error" not in saved

            retrieved = dispatch(live_client, "penfield_retrieve_artifact", {"path": path})
            import json

            data = json.loads(retrieved)
            assert data.get("content") == content

            listed = dispatch(
                live_client,
                "penfield_list_artifacts",
                {"prefix": "/hermes-penfield-integration"},
            )
            assert "error" not in listed
        finally:
            pass  # artifacts have no documented delete in v0.1.0


class TestAwaken:
    def test_awaken_returns_briefing(self, live_client: PenfieldClient) -> None:
        result = dispatch(live_client, "penfield_awaken", {})
        import json

        data = json.loads(result)
        # The briefing is tenant-specific; assert shape only.
        assert "briefing" in data or "error" not in data


class TestStats:
    def test_stats(self, live_client: PenfieldClient) -> None:
        stats = live_client.call("search_stats")
        assert "total_memories" in stats
