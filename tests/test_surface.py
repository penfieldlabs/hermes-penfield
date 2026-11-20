# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""The plugin's tools against a snapshot of Penfield's own tool list.

``tests/data/penfield_mcp_tools.json`` is what the Penfield MCP server
reports. An agent that knows Penfield from another client should find the
same tools and the same parameters here, under a ``penfield_`` prefix.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from penfield.tools import PENFIELD_TOOL_SCHEMAS

SNAPSHOT = json.loads(
    (Path(__file__).parent / "data" / "penfield_mcp_tools.json").read_text(encoding="utf-8")
)
UPSTREAM: dict[str, dict[str, Any]] = {t["name"]: t["inputSchema"] for t in SNAPSHOT["tools"]}
OURS: dict[str, dict[str, Any]] = {
    t["name"].removeprefix("penfield_"): t["parameters"] for t in PENFIELD_TOOL_SCHEMAS
}
DOCUMENTED: dict[str, list[str]] = {
    k: v for k, v in SNAPSHOT["documented_values"].items() if not k.startswith("_")
}


#: Parameters Penfield's MCP tools take that the REST API behind this plugin
#: does not act on. Offering them would promise the agent something that
#: silently does nothing.
NOT_AVAILABLE_OVER_REST: dict[str, set[str]] = {
    # POST /search/hybrid ignores include_inactive (checked on the live API).
    "recall": {"include_inactive"},
    "search": {"include_inactive"},
}


def _type_of(schema: dict[str, Any]) -> str:
    """The JSON type of a parameter, ignoring "or null" for optional ones."""
    if "type" in schema:
        return str(schema["type"])
    (only,) = [s for s in schema["anyOf"] if s.get("type") != "null"]
    return str(only["type"])


def _properties(schema: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = schema.get("properties", {})
    return properties


def _upstream(tool: str) -> dict[str, Any]:
    """The MCP tool's parameters, minus those the REST API cannot serve."""
    skipped = NOT_AVAILABLE_OVER_REST.get(tool, set())
    return {k: v for k, v in _properties(UPSTREAM[tool]).items() if k not in skipped}


def test_every_tool_name_has_the_prefix() -> None:
    assert all(t["name"].startswith("penfield_") for t in PENFIELD_TOOL_SCHEMAS)


def test_same_set_of_tools() -> None:
    assert sorted(OURS) == sorted(UPSTREAM)


@pytest.mark.parametrize("tool", sorted(UPSTREAM))
def test_same_parameter_names(tool: str) -> None:
    assert sorted(_properties(OURS[tool])) == sorted(_upstream(tool))


@pytest.mark.parametrize("tool", sorted(UPSTREAM))
def test_same_required_parameters(tool: str) -> None:
    assert sorted(OURS[tool].get("required", [])) == sorted(UPSTREAM[tool].get("required", []))


@pytest.mark.parametrize("tool", sorted(UPSTREAM))
def test_same_parameter_types(tool: str) -> None:
    ours, theirs = _properties(OURS[tool]), _upstream(tool)
    assert {k: _type_of(v) for k, v in ours.items()} == {k: _type_of(v) for k, v in theirs.items()}


@pytest.mark.parametrize("tool", sorted(UPSTREAM))
def test_same_defaults(tool: str) -> None:
    theirs = {
        name: spec["default"]
        for name, spec in _upstream(tool).items()
        if spec.get("default") is not None
    }
    ours = {name: _properties(OURS[tool])[name].get("default") for name in theirs}
    assert ours == theirs


def test_every_listed_gap_is_a_real_upstream_parameter() -> None:
    for tool, names in NOT_AVAILABLE_OVER_REST.items():
        assert names <= set(_properties(UPSTREAM[tool])), tool
        assert not names & set(_properties(OURS[tool])), tool


@pytest.mark.parametrize("parameter", sorted(DOCUMENTED))
def test_documented_values_are_accepted(parameter: str) -> None:
    tool, name = parameter.split(".")
    spec = _properties(OURS[tool])[name]
    accepted = spec.get("enum")
    if accepted is None:
        # No enum here either: the values must at least be named for the model.
        text = spec.get("description", "") + next(
            t["description"] for t in PENFIELD_TOOL_SCHEMAS if t["name"] == f"penfield_{tool}"
        )
        assert all(value in text for value in DOCUMENTED[parameter])
    else:
        assert sorted(accepted) == sorted(DOCUMENTED[parameter])


def test_schemas_are_in_the_shape_hermes_expects() -> None:
    for tool in PENFIELD_TOOL_SCHEMAS:
        assert set(tool) == {"name", "description", "parameters"}
        assert tool["parameters"]["type"] == "object"
        assert tool["description"].strip()
