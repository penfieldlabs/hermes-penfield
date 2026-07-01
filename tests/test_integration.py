# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Live integration tests against api-dev.penfield.app.

⚠️  These tests WRITE to a real Penfield tenant (create/update/delete
    memories, relationships, artifacts). They are dev-only by design.

RUN (dev only — they will skip otherwise):

    PENFIELD_API_KEY=... PENFIELD_ENV=dev pytest -m integration

FAILSAFES :

1. ``PENFIELD_ENV`` MUST be ``dev``. Prod is refused at three layers:
   the session gate, the ``live_jwt`` fixture, and the ``live_client``
   fixture (which is hard-pinned to ``Environment.DEV`` + dev URL).
2. ``PENFIELD_API_KEY`` must be set.
3. Tests that create memories gate on ``can_delete`` and skip ENTIRELY
   (before creating anything) if the key can't clean up — no orphan junk.
4. Every created memory carries the ``hermes-penfield-int-*`` tag so a
   bulk purge is always one search away.

If you re-run these against a tenant you care about, expect transient
memories during the run. Lifecycle/relationship tests clean up after
themselves when the key has ``delete`` scope; otherwise they skip.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

import pytest
from penfield.exceptions import NotFoundError
from penfield.tools import dispatch

if TYPE_CHECKING:
    from penfield.client import PenfieldClient

pytestmark = pytest.mark.integration


# Every memory created by these tests is tagged with this prefix so a bulk
# purge is always one search away. Never commit pollution without a tag
# that makes it findable.
_INTEGRATION_TAG_PREFIX = "hermes-penfield-int"


def _unique_tag() -> str:
    return f"{_INTEGRATION_TAG_PREFIX}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def can_delete(live_client: PenfieldClient) -> bool:
    """Probe whether the dev key has the `delete` scope.

    Does this *without creating trash*: we attempt to delete a known-bogus
    UUID. A 404 means we have scope (memory just didn't exist); a 403 means
    we don't. Polluting tests gate on this — **no creation without
    guaranteed cleanup.**
    """
    from penfield.exceptions import APIError, AuthError

    bogus = "00000000-0000-0000-0000-000000000000"
    try:
        live_client.call("memory_delete", path_params={"memory_id": bogus})
    except NotFoundError:
        return True
    except AuthError as exc:
        if "delete" in str(exc).lower() or "scope" in str(exc).lower():
            return False
        raise
    except APIError:
        return False
    return True


class TestAuthRoundTrip:
    def test_key_exchange_and_verify(self, live_jwt: str) -> None:
        assert live_jwt.startswith("eyJ")


class TestStoreRecallDelete:
    def test_full_lifecycle(self, live_client: PenfieldClient, can_delete: bool) -> None:
        # No creation without guaranteed cleanup. If the dev key can't
        # delete, running this would leave permanent trash in the tenant —
        # so skip the whole test, not just the teardown.
        if not can_delete:
            pytest.skip("dev key lacks delete scope; skipping to avoid pollution")
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
            # Guaranteed cleanup: the can_delete gate passed.
            live_client.call("memory_delete", path_params={"memory_id": mem_id})
            with pytest.raises(NotFoundError):
                live_client.call("memory_get", path_params={"memory_id": mem_id})


class TestRelationships:
    def test_connect_and_explore(self, live_client: PenfieldClient, can_delete: bool) -> None:
        if not can_delete:
            pytest.skip("dev key lacks delete scope; skipping to avoid pollution")
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
                    "from_memory": a["id"],
                    "to_memory": b["id"],
                    "relationship_type": "supports",
                    "strength": 0.7,
                },
            )
            assert "error" not in rel

            explored = dispatch(
                live_client,
                "penfield_explore",
                {"start_memory": a["id"], "max_depth": 2},
            )
            assert "error" not in explored
        finally:
            # Guaranteed cleanup because the can_delete gate passed.
            for mid in (a["id"], b["id"]):
                live_client.call("memory_delete", path_params={"memory_id": mid})


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
            pass  # artifacts have no documented delete in v0.2.0


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
