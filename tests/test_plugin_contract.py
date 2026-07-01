# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Regression tests for the Hermes plugin contract.

These pin the integration seam that pinned during development. They
exist because v0.1.0 shipped a ``register()`` that couldn't load and an
``on_pre_compress`` that silently dropped its return value — both caught
only by diffing against real Hermes source after the fact. The mock
fixtures here mirror what Hermes' plugin loader and MemoryManager
actually do.
"""

from __future__ import annotations

from typing import Any

import pytest
from penfield import register


class _RecordingCtx:
    """Stand-in for hermes_cli.plugins.PluginContext.

    Real loader calls ``register(ctx)``; ctx exposes
    ``register_memory_provider(instance)``. We record the instance.
    """

    def __init__(self) -> None:
        self.registered: list[Any] = []

    def register_memory_provider(self, instance: Any) -> None:
        self.registered.append(instance)


class TestRegisterContract:
    """The single most important contract: does the plugin load?"""

    def test_register_takes_ctx_and_returns_none(self) -> None:
        ctx = _RecordingCtx()
        result = register(ctx)
        assert result is None, "register() must return None; Hermes ignores the value"

    def test_register_calls_register_memory_provider(self) -> None:
        ctx = _RecordingCtx()
        register(ctx)
        assert len(ctx.registered) == 1

    def test_registered_instance_is_the_provider(self) -> None:
        ctx = _RecordingCtx()
        register(ctx)
        from penfield.provider import PenfieldMemoryProvider

        assert isinstance(ctx.registered[0], PenfieldMemoryProvider)

    def test_register_signature_accepts_single_positional_arg(self) -> None:
        """v0.1.0 took zero args → TypeError at load. Must accept one."""
        import inspect

        sig = inspect.signature(register)
        assert len(sig.parameters) == 1, (
            f"register must take exactly 1 param (ctx); got {len(sig.parameters)}"
        )

    def test_calling_register_with_no_args_raises(self) -> None:
        """The old broken contract. Documents why the fix matters."""
        with pytest.raises(TypeError, match="missing 1 required positional argument"):
            register()  # type: ignore[call-arg]


class TestPreCompressReturnsDigest:
    """ABC: on_pre_compress -> str. v0.1.0 returned None (silent drop)."""

    def test_returns_empty_string(self, tmp_path: Any) -> None:
        from penfield.provider import PenfieldMemoryProvider

        p = PenfieldMemoryProvider()
        p.initialize("sess-abc", hermes_home=str(tmp_path))

        import penfield.provider as prov

        original = prov.dispatch
        prov.dispatch = lambda c, n, a: "{}"  # type: ignore[assignment]
        try:
            result = p.on_pre_compress([{"role": "user", "content": "remember this thread"}])
        finally:
            prov.dispatch = original  # type: ignore[assignment]
        assert result == ""

    def test_returns_empty_string_when_disabled(self, tmp_path: Any) -> None:
        from penfield.provider import PenfieldMemoryProvider

        p = PenfieldMemoryProvider()
        p.initialize("sess", hermes_home=str(tmp_path))
        p._config.pre_compress_save = False  # type: ignore[union-attr]
        result = p.on_pre_compress([{"role": "user", "content": "x"}])
        assert result == ""


class TestOnMemoryWriteMetadata:
    """ABC: on_memory_write(action, target, content, metadata=None)."""

    def test_accepts_metadata_kwarg(self, tmp_path: Any) -> None:
        from penfield.provider import PenfieldMemoryProvider

        p = PenfieldMemoryProvider()
        p.initialize("sess", hermes_home=str(tmp_path))
        p._config.mirror_builtin_writes = True  # type: ignore[union-attr]

        captured: dict[str, Any] = {}

        class StubClient:
            def call(self, name: str, *, body: Any = None, **kw: Any) -> Any:
                captured["body"] = body
                return {"id": "x"}

        p._client = StubClient()  # type: ignore[assignment]
        # Must not raise; metadata is accepted and surfaced into tags.
        p.on_memory_write(
            "add",
            "memory",
            "a note",
            metadata={"write_origin": "memory_tool", "session_id": "s1"},
        )
        tags = captured["body"]["tags"]
        assert "origin:memory_tool" in tags or any("memory_tool" in t for t in tags)

    def test_metadata_defaults_to_none(self, tmp_path: Any) -> None:
        from penfield.provider import PenfieldMemoryProvider

        p = PenfieldMemoryProvider()
        p.initialize("sess", hermes_home=str(tmp_path))
        # No metadata arg → must not raise.
        p.on_memory_write("add", "memory", "x")


class TestToolSchemaShape:
    """ABC docstring: schema is {name, description, parameters} (not parameters)."""

    def test_schemas_use_parameters_not_input_schema(self) -> None:
        from penfield.provider import PenfieldMemoryProvider

        p = PenfieldMemoryProvider()
        for schema in p.get_tool_schemas():
            assert "parameters" in schema, f"{schema['name']} missing 'parameters'"
            assert "input_schema" not in schema, f"{schema['name']} uses deprecated 'input_schema'"

    def test_every_schema_has_name_and_description(self) -> None:
        from penfield.provider import PenfieldMemoryProvider

        p = PenfieldMemoryProvider()
        for schema in p.get_tool_schemas():
            assert schema["name"].startswith("penfield_")
            assert isinstance(schema["description"], str) and schema["description"]


class TestPublicSurface:
    def test_register_is_exported(self) -> None:

        import penfield

        assert hasattr(penfield, "register")


class TestDirectoryDiscovery:
    """The REAL load path: directory scan, not pip entry point.

    Pins the lesson from ADR-0010 — Hermes discovers memory providers from
    $HERMES_HOME/plugins/<name>/ via a text scan for 'register_memory_provider',
    then calls register(ctx) on the loaded module. v0.1.0 shipped a pip
    entry point that never registered. These tests simulate the real loader.
    """

    def test_plugin_is_recognized_by_text_scan(self) -> None:
        """Hermes' _is_memory_provider_dir does a literal substring scan."""
        import pathlib

        shim = pathlib.Path(__file__).resolve().parent.parent / "__init__.py"
        source = shim.read_text(errors="replace")[:8192]
        # Verbatim replica of plugins/memory/__init__.py:_is_memory_provider_dir
        assert "register_memory_provider" in source or "MemoryProvider" in source

    def test_plugin_is_recognized_registers_via_collector(self) -> None:
        """Simulate the directory loader: import the package, call register(ctx)."""

        class Collector:
            def __init__(self) -> None:
                self.provider = None

            def register_memory_provider(self, provider: Any) -> None:
                self.provider = provider

        ctx = Collector()
        import penfield

        penfield.register(ctx)
        from penfield.provider import PenfieldMemoryProvider

        assert isinstance(ctx.provider, PenfieldMemoryProvider)

    def test_no_pip_entry_point_advertised(self) -> None:
        """ADR-0010: a pip entry point would silently never register.

        Assert pyproject does NOT declare hermes_agent.plugins (the general
        plugin entry-point group), since the general PluginContext has no
        register_memory_provider.
        """
        import pathlib

        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib  # Python 3.10 compat

        pp = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
        data = tomllib.loads(pp.read_text())
        # project.entry-points must not contain hermes_agent.plugins
        eps = data.get("project", {}).get("entry-points", {}) or {}
        assert "hermes_agent.plugins" not in eps, (
            "pip entry point would silently never register; use directory install"
        )
