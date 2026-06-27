"""Tests for Buoy plugin loader/manager."""

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig
from buoy.plugins.loader import PluginManager
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

# =============================================================================
# Config helpers
# =============================================================================


@dataclass
class PluginEntry:
    enabled: bool = False
    settings: dict = field(default_factory=dict)


@dataclass
class PluginsConfig:
    enabled: bool = True
    directory: str = "/plugins"
    builtin: dict = field(default_factory=dict)


def _make_config(plugins_enabled=True, builtin=None):
    config = BuoyConfig()
    config.node = NodeConfig(name="test")
    config.network = NetworkConfig()
    config.features = FeaturesConfig()
    config.plugins = PluginsConfig(
        enabled=plugins_enabled,
        builtin=builtin or {},
    )
    return config


# =============================================================================
# Test plugin for discovery
# =============================================================================


class FakePlugin(Plugin):
    """A fake plugin for testing the loader."""

    manifest = PluginManifest(id="fake", name="Fake Plugin", refresh_interval=60)

    async def collect(self) -> PanelData:
        return PanelData(status="ok", summary="Fake data")


class AnotherFakePlugin(Plugin):
    """Another fake plugin with frontend JS."""

    manifest = PluginManifest(id="another", name="Another", refresh_interval=30)

    async def collect(self) -> PanelData:
        return PanelData(status="warn", summary="Warning")

    def frontend_js(self) -> str | None:
        return "function render_another(data) { return '<p>hi</p>'; }"


class FailingPlugin(Plugin):
    """A plugin whose collect() always raises."""

    manifest = PluginManifest(id="failing", name="Failing", refresh_interval=10)

    async def collect(self) -> PanelData:
        raise RuntimeError("Something broke")


# =============================================================================
# PluginManager basics
# =============================================================================


class TestPluginManagerInit:
    """Test basic manager construction and properties."""

    def test_empty_manager(self):
        config = _make_config()
        mgr = PluginManager(config)
        assert mgr.plugins == {}
        assert mgr.latest_data == {}

    @pytest.mark.asyncio
    async def test_disabled_plugins_skips_start(self):
        config = _make_config(plugins_enabled=False)
        mgr = PluginManager(config)
        await mgr.start()
        assert mgr.plugins == {}


class TestFindPluginClass:
    """Test the static _find_plugin_class helper."""

    def test_finds_subclass(self):
        import types

        module = types.ModuleType("test_mod")
        module.FakePlugin = FakePlugin
        module.Plugin = Plugin  # Should be skipped (it's the base)

        result = PluginManager._find_plugin_class(module)
        assert result is FakePlugin

    def test_returns_none_for_no_plugins(self):
        import types

        module = types.ModuleType("empty_mod")
        module.SomeClass = str  # Not a Plugin subclass

        result = PluginManager._find_plugin_class(module)
        assert result is None

    def test_ignores_base_plugin(self):
        import types

        module = types.ModuleType("base_only")
        module.Plugin = Plugin

        result = PluginManager._find_plugin_class(module)
        assert result is None


# =============================================================================
# Built-in plugin loading
# =============================================================================


class TestLoadBuiltins:
    """Test loading built-in plugins from config."""

    @pytest.mark.asyncio
    async def test_loads_enabled_builtin(self):
        config = _make_config(
            builtin={"github": PluginEntry(enabled=True, settings={"token": "ghp_test"})}
        )
        mgr = PluginManager(config)

        # Patch _load_user_plugins to avoid filesystem access
        with patch.object(mgr, "_load_user_plugins", new_callable=AsyncMock):
            await mgr.start()

        assert "github" in mgr.plugins

    @pytest.mark.asyncio
    async def test_skips_disabled_builtin(self):
        config = _make_config(
            builtin={"github": PluginEntry(enabled=False, settings={"token": "ghp_test"})}
        )
        mgr = PluginManager(config)

        with patch.object(mgr, "_load_user_plugins", new_callable=AsyncMock):
            await mgr.start()

        assert "github" not in mgr.plugins

    @pytest.mark.asyncio
    async def test_skips_unconfigured_builtin(self):
        config = _make_config(builtin={})
        mgr = PluginManager(config)

        with patch.object(mgr, "_load_user_plugins", new_callable=AsyncMock):
            await mgr.start()

        assert len(mgr.plugins) == 0


# =============================================================================
# User plugin loading
# =============================================================================


class TestLoadUserPlugins:
    """Test loading plugins from a directory."""

    @pytest.mark.asyncio
    async def test_loads_plugin_from_file(self, tmp_path):
        plugin_file = tmp_path / "my_plugin.py"
        plugin_file.write_text("""
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

class MyCustomPlugin(Plugin):
    manifest = PluginManifest(id="my_custom", name="My Custom")

    async def collect(self):
        return PanelData(status="ok", summary="Custom works")
""")

        config = _make_config(builtin={})
        config.plugins.directory = str(tmp_path)
        mgr = PluginManager(config)

        await mgr._load_user_plugins()

        assert "my_custom" in mgr.plugins

    @pytest.mark.asyncio
    async def test_ignores_underscore_files(self, tmp_path):
        (tmp_path / "__init__.py").write_text("# nothing")
        (tmp_path / "_private.py").write_text("class Foo: pass")

        config = _make_config(builtin={})
        config.plugins.directory = str(tmp_path)
        mgr = PluginManager(config)

        await mgr._load_user_plugins()
        assert len(mgr.plugins) == 0

    @pytest.mark.asyncio
    async def test_handles_broken_plugin_gracefully(self, tmp_path):
        plugin_file = tmp_path / "broken.py"
        plugin_file.write_text("raise SyntaxError('oops')")

        config = _make_config(builtin={})
        config.plugins.directory = str(tmp_path)
        mgr = PluginManager(config)

        # Should not raise
        await mgr._load_user_plugins()
        assert len(mgr.plugins) == 0

    @pytest.mark.asyncio
    async def test_nonexistent_directory_is_fine(self):
        config = _make_config(builtin={})
        config.plugins.directory = "/nonexistent/plugins/dir"
        mgr = PluginManager(config)

        await mgr._load_user_plugins()
        assert len(mgr.plugins) == 0


# =============================================================================
# Collection and data access
# =============================================================================


class TestPluginCollection:
    """Test _safe_collect and collect_all_now."""

    @pytest.mark.asyncio
    async def test_safe_collect_stores_data(self):
        config = _make_config()
        mgr = PluginManager(config)
        plugin = FakePlugin()

        await mgr._safe_collect("fake", plugin)

        assert "fake" in mgr.latest_data
        assert mgr.latest_data["fake"].status == "ok"
        assert mgr.latest_data["fake"].summary == "Fake data"

    @pytest.mark.asyncio
    async def test_safe_collect_handles_exception(self):
        config = _make_config()
        mgr = PluginManager(config)
        plugin = FailingPlugin()

        await mgr._safe_collect("failing", plugin)

        assert "failing" in mgr.latest_data
        assert mgr.latest_data["failing"].status == "error"
        assert "Something broke" in mgr.latest_data["failing"].detail["error"]

    @pytest.mark.asyncio
    async def test_safe_collect_handles_timeout(self):
        config = _make_config()
        mgr = PluginManager(config)

        class SlowPlugin(Plugin):
            manifest = PluginManifest(id="slow", name="Slow")

            async def collect(self):
                await asyncio.sleep(100)
                return PanelData()

        plugin = SlowPlugin()

        # Patch wait_for timeout to be very short
        with patch("buoy.plugins.loader.asyncio.wait_for", side_effect=TimeoutError()):
            await mgr._safe_collect("slow", plugin)

        assert mgr.latest_data["slow"].status == "error"
        assert "Timeout" in mgr.latest_data["slow"].summary

    @pytest.mark.asyncio
    async def test_collect_all_now(self):
        config = _make_config()
        mgr = PluginManager(config)

        # Manually inject plugins and data
        mgr._plugins = {"fake": FakePlugin(), "another": AnotherFakePlugin()}
        mgr._latest_data = {
            "fake": PanelData(status="ok", summary="Fake data"),
            "another": PanelData(status="warn", summary="Warning"),
        }

        result = await mgr.collect_all_now()

        assert "fake" in result
        assert result["fake"]["name"] == "Fake Plugin"
        assert result["fake"]["status"] == "ok"
        assert "another" in result
        assert result["another"]["icon"] == ""


# =============================================================================
# Frontend JS
# =============================================================================


class TestPluginFrontendJs:
    """Test get_plugin_frontend_js."""

    def test_returns_js_from_plugins_that_have_it(self):
        config = _make_config()
        mgr = PluginManager(config)
        mgr._plugins = {"fake": FakePlugin(), "another": AnotherFakePlugin()}

        result = mgr.get_plugin_frontend_js()

        assert "fake" not in result  # FakePlugin returns None
        assert "another" in result
        assert "render_another" in result["another"]

    def test_empty_when_no_plugins(self):
        config = _make_config()
        mgr = PluginManager(config)
        assert mgr.get_plugin_frontend_js() == {}


# =============================================================================
# Lifecycle (stop)
# =============================================================================


class TestPluginManagerStop:
    """Test teardown and task cancellation."""

    @pytest.mark.asyncio
    async def test_stop_calls_teardown(self):
        config = _make_config()
        mgr = PluginManager(config)

        plugin = FakePlugin()
        plugin.teardown = AsyncMock()
        mgr._plugins = {"fake": plugin}

        await mgr.stop()
        plugin.teardown.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self):
        config = _make_config()
        mgr = PluginManager(config)

        mock_task = MagicMock()
        mgr._tasks = [mock_task]

        await mgr.stop()
        mock_task.cancel.assert_called_once()
        assert mgr._tasks == []

    @pytest.mark.asyncio
    async def test_stop_handles_teardown_exception(self):
        config = _make_config()
        mgr = PluginManager(config)

        plugin = FakePlugin()
        plugin.teardown = AsyncMock(side_effect=Exception("teardown failed"))
        mgr._plugins = {"fake": plugin}

        # Should not raise
        await mgr.stop()
