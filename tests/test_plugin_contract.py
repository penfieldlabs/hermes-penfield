# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Regression tests for the Hermes plugin contract.

These pin the integration seam that ADR-0007 originally got wrong. They
exist because v0.1.0 shipped a ``register()`` that couldn't load and an
``on_pre_compress`` that silently dropped its return value — both caught
only by diffing against real Hermes source after the fact. The mock
fixtures here mirror what Hermes' plugin loader and MemoryManager
actually do.
"""

from __future__ import annotations

from typing import Any

import pytest

import hermes_penfield
from hermes_penfield import register


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
        from hermes_penfield.provider import PenfieldMemoryProvider

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

    def test_returns_digest_string(self, tmp_path: Any) -> None:
        from hermes_penfield.provider import PenfieldMemoryProvider

        p = PenfieldMemoryProvider()
        p.initialize("sess-abc", hermes_home=str(tmp_path))

        # Stub the store dispatch so no network call is made.
        import hermes_penfield.provider as prov

        prov.dispatch = lambda c, n, a: "{}"  # type: ignore[assignment]
        try:
            result = p.on_pre_compress([{"role": "user", "content": "remember this thread"}])
        finally:
            import importlib

            importlib.reload(prov)
        assert isinstance(result, str)
        assert "remember this thread" in result

    def test_returns_empty_string_when_disabled(self, tmp_path: Any) -> None:
        from hermes_penfield.provider import PenfieldMemoryProvider

        p = PenfieldMemoryProvider()
        p.initialize("sess", hermes_home=str(tmp_path))
        p._config.pre_compress_save = False  # type: ignore[union-attr]
        result = p.on_pre_compress([{"role": "user", "content": "x"}])
        assert result == ""


class TestOnMemoryWriteMetadata:
    """ABC: on_memory_write(action, target, content, metadata=None)."""

    def test_accepts_metadata_kwarg(self, tmp_path: Any) -> None:
        from hermes_penfield.provider import PenfieldMemoryProvider

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
        from hermes_penfield.provider import PenfieldMemoryProvider

        p = PenfieldMemoryProvider()
        p.initialize("sess", hermes_home=str(tmp_path))
        # No metadata arg → must not raise.
        p.on_memory_write("add", "memory", "x")


class TestToolSchemaShape:
    """ABC docstring: schema is {name, description, parameters} (not input_schema)."""

    def test_schemas_use_parameters_not_input_schema(self) -> None:
        from hermes_penfield.provider import PenfieldMemoryProvider

        p = PenfieldMemoryProvider()
        for schema in p.get_tool_schemas():
            assert "parameters" in schema, f"{schema['name']} missing 'parameters'"
            assert "input_schema" not in schema, f"{schema['name']} uses deprecated 'input_schema'"

    def test_every_schema_has_name_and_description(self) -> None:
        from hermes_penfield.provider import PenfieldMemoryProvider

        p = PenfieldMemoryProvider()
        for schema in p.get_tool_schemas():
            assert schema["name"].startswith("penfield_")
            assert isinstance(schema["description"], str) and schema["description"]


class TestPublicSurface:
    def test_register_is_exported(self) -> None:
        assert hasattr(hermes_penfield, "register")


class TestDirectoryDiscovery:
    """The REAL load path: directory scan, not pip entry point.

    Pins the lesson from ADR-0014 — Hermes discovers memory providers from
    $HERMES_HOME/plugins/<name>/ via a text scan for 'register_memory_provider',
    then calls register(ctx) on the loaded module. v0.1.0 shipped a pip
    entry point that never registered. These tests simulate the real loader.
    """

    def test_plugin_dir_shim_is_recognized_by_text_scan(self) -> None:
        """Hermes' _is_memory_provider_dir does a literal substring scan."""
        import pathlib

        shim = pathlib.Path(__file__).resolve().parent.parent / "plugin_dir" / "__init__.py"
        source = shim.read_text(errors="replace")[:8192]
        # Verbatim replica of plugins/memory/__init__.py:_is_memory_provider_dir
        assert "register_memory_provider" in source or "MemoryProvider" in source

    def test_plugin_dir_shim_registers_via_collector(self, tmp_path: Any) -> None:
        """Simulate the directory loader: load shim, call register(ctx)."""
        import importlib.util
        import pathlib

        shim = pathlib.Path(__file__).resolve().parent.parent / "plugin_dir" / "__init__.py"
        spec = importlib.util.spec_from_file_location("penfield_shim_test", shim)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        class Collector:
            def __init__(self) -> None:
                self.provider = None

            def register_memory_provider(self, provider: Any) -> None:
                self.provider = provider

        ctx = Collector()
        mod.register(ctx)
        from hermes_penfield.provider import PenfieldMemoryProvider

        assert isinstance(ctx.provider, PenfieldMemoryProvider)

    def test_install_copies_shim_to_hermes_home(self, tmp_path: Any) -> None:
        """`hermes-penfield install` drops a recognizable shim into HERMES_HOME."""
        import importlib.util

        import hermes_penfield.cli as cli

        rc = cli.main(["install", "--hermes-home", str(tmp_path)])
        assert rc == 0
        installed = tmp_path / "plugins" / "penfield" / "__init__.py"
        assert installed.exists()
        # The installed copy must be recognizable + functional.
        source = installed.read_text(errors="replace")[:8192]
        assert "register_memory_provider" in source
        spec = importlib.util.spec_from_file_location("inst_test", installed)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        captured: dict[str, Any] = {}

        class C:
            def register_memory_provider(self, p: Any) -> None:
                captured["provider"] = p

        mod.register(C())
        assert captured["provider"] is not None

    def test_install_refuses_without_force(self, tmp_path: Any) -> None:
        import hermes_penfield.cli as cli

        cli.main(["install", "--hermes-home", str(tmp_path)])
        # Second install without --force should fail.
        rc = cli.main(["install", "--hermes-home", str(tmp_path)])
        assert rc != 0

    def test_no_pip_entry_point_advertised(self) -> None:
        """ADR-0014: a pip entry point would silently never register.

        Assert pyproject does NOT declare hermes_agent.plugins (the general
        plugin entry-point group), since the general PluginContext has no
        register_memory_provider.
        """
        import pathlib

        import tomllib

        pp = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
        data = tomllib.loads(pp.read_text())
        # project.entry-points must not contain hermes_agent.plugins
        eps = data.get("project", {}).get("entry-points", {}) or {}
        assert "hermes_agent.plugins" not in eps, (
            "pip entry point would silently never register; use directory install"
        )


class TestRealEntryPoint:
    """Exercises the ACTUAL CLI binary via subprocess, not main().

    All four review rounds failed because tests called main([...]) directly
    while the real user-facing entry point was broken (round 4: the binary
    didn't exist). These tests invoke the documented commands as a user
    would, so a missing/wrong entry point fails here before it can ship.
    """

    def test_console_script_runs_version(self) -> None:
        import subprocess

        result = subprocess.run(
            ["hermes-penfield", "version"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == hermes_penfield.__version__

    def test_python_dash_m_runs_version(self) -> None:
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "hermes_penfield", "version"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == hermes_penfield.__version__

    def test_cli_dash_m_module_runs_version(self) -> None:
        """python -m hermes_penfield.cli — was a silent no-op before the
        __main__ guard was added."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "hermes_penfield.cli", "version"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == hermes_penfield.__version__

    def test_console_script_install_works(self, tmp_path: Any) -> None:
        """The documented install command, run as a real subprocess."""
        import subprocess

        result = subprocess.run(
            ["hermes-penfield", "install", "--hermes-home", str(tmp_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "plugins" / "penfield" / "__init__.py").exists()

    def test_pyproject_declares_console_script(self) -> None:
        import pathlib

        import tomllib

        pp = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
        data = tomllib.loads(pp.read_text())
        scripts = data.get("project", {}).get("scripts", {}) or {}
        assert scripts.get("hermes-penfield") == "hermes_penfield.cli:main", (
            "console script missing — documented commands won't exist"
        )
