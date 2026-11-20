# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""A whole session driven through Hermes's MemoryManager, in both plugin modes."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from penfield.tools import PENFIELD_TOOL_SCHEMAS

from tests.support.fake_penfield import SOURCE_TYPES, FakePenfield

from .conftest import ISOLATION_MODES, build_home, calls_during, run_driver

#: Placed past the 200-character mark, where a search snippet has already ended.
TAIL_MARKER = "FULL-CONTENT-TAIL-MARKER"
SEEDED = (
    "We decided the launch checklist needs four steps: tests, docs, a tag and a publish step. "
    + "Background on how the launch checklist was agreed. " * 4
    + TAIL_MARKER
)


class Session:
    """One driver run plus the fake API it talked to."""

    def __init__(self, report: dict[str, Any], fake: FakePenfield, seeded_id: str) -> None:
        self.report, self.fake, self.seeded_id = report, fake, seeded_id

    def step(self, name: str) -> dict[str, Any]:
        entry: dict[str, Any] = self.report["steps"][name]
        return entry

    def tool(self, label: str) -> Any:
        return self.step(f"tool:{label}")["value"]


@pytest.fixture(scope="module", params=ISOLATION_MODES)
def session(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Session]:
    with FakePenfield() as fake:
        seeded_id = fake.seed_memory(SEEDED, tags=["launch"])
        home = build_home(
            tmp_path_factory.mktemp("session"),
            api_url=fake.url,
            isolation=request.param,
            settings={"pre_compress_save": True},
        )
        report = run_driver(home, "session", timeout=300)
        assert report.get("resolved"), f"provider did not resolve: {report}"
        yield Session(report, fake, seeded_id)


def test_every_step_completes(session: Session) -> None:
    failed = {name: s["error"] for name, s in session.report["steps"].items() if not s["ok"]}
    assert failed == {}


def test_no_hook_fails_silently(session: Session) -> None:
    assert session.report["swallowed_failures"] == []


def test_briefing_is_in_the_system_prompt(session: Session) -> None:
    assert session.fake.briefing in session.step("system_prompt")["value"]
    assert session.step("system_prompt_again")["value"] == session.step("system_prompt")["value"]


def test_briefing_is_fetched_once_per_session(session: Session) -> None:
    fetches = [
        r
        for step in ("initialize", "system_prompt", "system_prompt_again")
        for r in calls_during(session.fake, session.report, step)
        if r.path == "/personality/awakening"
    ]
    assert len(fetches) == 1


def test_all_tools_are_exposed(session: Session) -> None:
    expected = sorted(s["name"] for s in PENFIELD_TOOL_SCHEMAS)
    assert session.step("tool_schemas")["value"] == expected


def test_auto_recall_injects_the_full_memory(session: Session) -> None:
    text = session.step("prefetch")["value"]
    assert session.seeded_id in text, "auto-recall must show an id the agent can fetch"
    assert "launch checklist needs four steps" in text


def test_recall_indicator_reports_what_was_injected(session: Session) -> None:
    assert "Penfield" in session.step("describe_recall")["value"]
    assert "recalled 1 memory" in session.step("describe_recall")["value"]


def test_recall_indicator_is_cleared_when_nothing_matches(session: Session) -> None:
    assert session.step("prefetch_no_match")["value"] == ""
    assert session.step("describe_recall_no_match")["value"] == ""


def test_recall_returns_full_content(session: Session) -> None:
    result = session.tool("recall")
    hit = next(item for item in result["items"] if item["id"] == session.seeded_id)
    assert TAIL_MARKER in hit["content"]
    assert hit["tags"] == ["launch"]
    assert hit["created_at"]


def test_recall_filtered_to_memories_sends_source_types_the_api_filters_on(
    session: Session,
) -> None:
    result = session.tool("recall_memories_only")
    assert "error" not in result
    (request,) = [
        r
        for r in calls_during(session.fake, session.report, "tool:recall_memories_only")
        if r.path == "/search/hybrid"
    ]
    assert set(request.body["source_types"]) <= SOURCE_TYPES
    assert "document_upload" not in request.body["source_types"]


def test_store_accepts_ordinary_prose(session: Session) -> None:
    """Words such as "task-list" and "risk-adjusted" are not credentials."""
    assert session.tool("store_prose").get("id"), session.tool("store_prose")


@pytest.mark.parametrize(
    "label",
    [
        "store",
        "store_second",
        "search",
        "fetch",
        "update_memory",
        "connect",
        "explore",
        "disconnect",
        "reflect",
        "reflect_named_window",
        "save_artifact",
        "list_artifacts",
        "retrieve_artifact",
        "delete_artifact",
        "save_context",
        "list_contexts",
        "restore_context",
        "awaken",
    ],
)
def test_tool_round_trip(session: Session, label: str) -> None:
    result = session.tool(label)
    assert isinstance(result, dict), result
    assert "error" not in result, result


def test_a_day_window_reaches_the_api_as_a_start_date(session: Session) -> None:
    (request,) = [
        r
        for r in calls_during(session.fake, session.report, "tool:reflect")
        if r.path == "/analysis/reflect"
    ]
    assert set(request.body) == {"start_date"}


def test_a_bad_argument_is_refused_with_the_accepted_values(session: Session) -> None:
    error = session.tool("reflect_invalid")["error"]
    assert "recent, today, week, month" in error


def test_artifact_listing_uses_the_directory_and_name_filter(session: Session) -> None:
    listing = session.tool("list_artifacts")
    assert [f["name"] for f in listing["files"]] == ["notes.md"]
    (request,) = session.fake.calls("GET", "/artifacts/list")
    assert request.query == {"prefix": "/it/", "name_pattern": "notes"}


def test_missing_argument_is_explained_to_the_model(session: Session) -> None:
    error = session.tool("missing_argument")["error"]
    assert "content" in error
    assert "required" in error.lower()


def test_requests_identify_the_plugin(session: Session) -> None:
    agents = {r.user_agent for r in session.fake.calls()}
    assert all(a.startswith("hermes-penfield/") for a in agents), agents
