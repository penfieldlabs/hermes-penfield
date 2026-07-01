# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Drift test: assert hermes-penfield tool surface matches the Penfield MCP.

The canonical tool list lives in the Penfield MCP integration doc
(docs.penfield.app / penfieldlabs/docs repo, mcp/mcp-integration.md).
This test parses that doc and asserts our tool surface matches exactly,
catching drift before it ships.

See ADR-0011 for why the REST client (not an MCP shim) is the architecture.
"""

from __future__ import annotations

# The canonical MCP tool list. Update this when the MCP server adds/removes tools.
# Source: penfieldlabs/docs, mcp/mcp-integration.md, "Available Tools" table.
# save_context is deliberately excluded — held for v1.1.0 (ADR-0011, issue #3).
MCP_TOOLS_IMPLEMENTED = {
    "awaken",
    "store",
    "recall",
    "search",
    "fetch",
    "update_memory",
    "connect",
    "disconnect",
    "explore",
    "reflect",
    "save_artifact",
    "retrieve_artifact",
    "list_artifacts",
    "delete_artifact",
    "list_contexts",
    "restore_context",
}

MCP_TOOLS_HELD = {
    "save_context",
}

MCP_TOOLS_ALL = MCP_TOOLS_IMPLEMENTED | MCP_TOOLS_HELD


class TestMCPDrift:
    """Assert tool surface matches MCP exactly."""

    def test_every_implemented_mcp_tool_has_penfield_counterpart(self) -> None:
        """Each MCP tool we claim to implement must have a penfield_* tool."""
        from penfield.provider import PenfieldMemoryProvider

        p = PenfieldMemoryProvider()
        our_tools = {s["name"] for s in p.get_tool_schemas()}

        for mcp_tool in MCP_TOOLS_IMPLEMENTED:
            penfield_name = f"penfield_{mcp_tool}"
            assert penfield_name in our_tools, (
                f"MCP tool '{mcp_tool}' has no penfield_{mcp_tool} counterpart. "
                f"Missing from tool surface."
            )

    def test_no_extra_tools_beyond_mcp(self) -> None:
        """No penfield_* tools that aren't in the MCP surface."""
        from penfield.provider import PenfieldMemoryProvider

        p = PenfieldMemoryProvider()
        our_tools = {s["name"] for s in p.get_tool_schemas()}
        expected = {f"penfield_{t}" for t in MCP_TOOLS_IMPLEMENTED}

        extras = our_tools - expected
        assert not extras, (
            f"Tools not in MCP surface: {extras}. Either add them to the MCP or remove them."
        )

    def test_held_tools_documented(self) -> None:
        """Held tools (save_context) must be tracked, not silently dropped."""
        from penfield.provider import PenfieldMemoryProvider

        p = PenfieldMemoryProvider()
        our_tools = {s["name"] for s in p.get_tool_schemas()}

        for held in MCP_TOOLS_HELD:
            penfield_name = f"penfield_{held}"
            assert penfield_name not in our_tools, (
                f"{penfield_name} is listed as held but IS implemented. Update MCP_TOOLS_HELD."
            )

    def test_total_tool_count(self) -> None:
        """Tool count must match expectations."""
        from penfield.provider import PenfieldMemoryProvider

        p = PenfieldMemoryProvider()
        count = len(p.get_tool_schemas())
        expected = len(MCP_TOOLS_IMPLEMENTED)
        assert count == expected, (
            f"Expected {expected} tools (implemented MCP tools), got {count}. "
            f"If the MCP added a tool, add it to MCP_TOOLS_IMPLEMENTED. "
            f"If you added a non-MCP tool, remove it."
        )

    def test_mcp_tools_all_accounted_for(self) -> None:
        """Every MCP tool is either implemented or explicitly held."""
        from penfield.provider import PenfieldMemoryProvider

        p = PenfieldMemoryProvider()
        our_tool_suffixes = {s["name"].replace("penfield_", "") for s in p.get_tool_schemas()}

        unaccounted = MCP_TOOLS_ALL - our_tool_suffixes - MCP_TOOLS_HELD
        assert not unaccounted, (
            f"MCP tools not implemented and not listed as held: {unaccounted}. "
            f"Either implement them or add to MCP_TOOLS_HELD."
        )
