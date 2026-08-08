"""Tests for Buoy plugin loader/manager."""

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig, RefreshConfig
from buoy.plugins.loader import PluginManager, resolve_plugin_env
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
    """Test the static _find_plugin_class helper.

    Tests load real .py files via importlib so that class.__module__ matches
    the module's __name__, which is required by the fixed implementation.
    """

    @pytest.fixture(autouse=True)
    def _sys_state_cleanup(self):
        """Track sys.modules/sys.path mutations made by loaded test modules and undo them.

        Generated modules register themselves in sys.modules under a fixed
        name, and some also do their own sys.path.insert(0, tmp_path) to
        import a sibling module. Left in place, those leak into later tests
        (or a re-run of the same test) since pytest reuses the process.
        """
        self._loaded_names: list[str] = []
        self._added_paths: list[str] = []
        yield
        import sys

        for name in self._loaded_names:
            sys.modules.pop(name, None)
        for path in self._added_paths:
            if path in sys.path:
                sys.path.remove(path)

    def _load_module(self, tmp_path, name: str, src: str):
        """Write *src* to a .py file in tmp_path and return the loaded module."""
        import importlib.util
        import sys

        py_file = tmp_path / f"{name}.py"
        py_file.write_text(src)
        sys.modules.pop(name, None)  # guarantee a fresh load, even on re-runs
        spec = importlib.util.spec_from_file_location(name, py_file)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        self._loaded_names.append(name)
        spec.loader.exec_module(mod)
        return mod

    def test_finds_subclass(self, tmp_path):
        """A module that defines a single Plugin subclass returns that class."""
        src = (
            "from buoy.plugins.protocol import PanelData, Plugin, PluginManifest\n"
            "class MyPlugin(Plugin):\n"
            "    manifest = PluginManifest(id='my', name='My')\n"
            "    async def collect(self) -> PanelData:\n"
            "        return PanelData(status='ok', summary='')\n"
        )
        mod = self._load_module(tmp_path, "test_finds_subclass_mod", src)
        result = PluginManager._find_plugin_class(mod)
        assert result is mod.MyPlugin

    def test_returns_none_for_no_plugins(self, tmp_path):
        """A module with no Plugin subclass returns None."""
        src = "class SomeClass:\n    pass\n"
        mod = self._load_module(tmp_path, "test_returns_none_mod", src)
        result = PluginManager._find_plugin_class(mod)
        assert result is None

    def test_ignores_base_plugin(self, tmp_path):
        """A module that only contains the base Plugin class returns None."""
        src = "from buoy.plugins.protocol import Plugin\n"
        mod = self._load_module(tmp_path, "test_ignores_base_mod", src)
        result = PluginManager._find_plugin_class(mod)
        assert result is None

    # ------------------------------------------------------------------
    # Regression tests for the bug described in OSS-1306 / buoy#95
    # ------------------------------------------------------------------

    def test_ignores_imported_concrete_plugin(self, tmp_path):
        """A module that imports another concrete plugin must not return it.

        Regression: inspect.getmembers returns all visible names, including
        imported ones. Without the __module__ guard, alphabetically-first
        imported class would win. Here 'AlphaPlugin' sorts before 'ZebraPlugin'
        but must NOT be returned because it is defined elsewhere.
        """
        # Build the module that owns AlphaPlugin (the "elsewhere").
        alpha_src = (
            "from buoy.plugins.protocol import PanelData, Plugin, PluginManifest\n"
            "class AlphaPlugin(Plugin):\n"
            "    manifest = PluginManifest(id='alpha', name='Alpha')\n"
            "    async def collect(self) -> PanelData:\n"
            "        return PanelData(status='ok', summary='')\n"
        )
        alpha_py = tmp_path / "alpha_plugin_mod.py"
        alpha_py.write_text(alpha_src)
        # The zebra module below inserts tmp_path onto sys.path and imports
        # alpha_plugin_mod normally, registering both as process-wide state.
        self._added_paths.append(str(tmp_path))
        self._loaded_names.append("alpha_plugin_mod")

        # Build the module under test: imports AlphaPlugin AND defines ZebraPlugin.
        zebra_src = (
            "import sys\n"
            f"sys.path.insert(0, {str(tmp_path)!r})\n"
            "from alpha_plugin_mod import AlphaPlugin  # imported — must be ignored\n"
            "from buoy.plugins.protocol import PanelData, Plugin, PluginManifest\n"
            "class ZebraPlugin(Plugin):\n"
            "    manifest = PluginManifest(id='zebra', name='Zebra')\n"
            "    async def collect(self) -> PanelData:\n"
            "        return PanelData(status='ok', summary='')\n"
        )
        mod = self._load_module(tmp_path, "zebra_plugin_mod", zebra_src)
        result = PluginManager._find_plugin_class(mod)
        assert result is mod.ZebraPlugin
        assert result.__name__ == "ZebraPlugin"

    def test_prefers_manifest_bearing_class_over_shared_base(self, tmp_path):
        """A module with an intermediate base (no manifest) and a concrete plugin.

        The intermediate base class subclasses Plugin but does not declare its
        own manifest. The concrete plugin does. _find_plugin_class must return
        the concrete plugin regardless of alphabetical ordering.
        """
        src = (
            "from buoy.plugins.protocol import PanelData, Plugin, PluginManifest\n"
            # '_AardvarkBase' sorts before 'RealPlugin' — must NOT win.
            "class _AardvarkBase(Plugin):\n"
            "    '''Shared base — no own manifest.'''\n"
            "    async def collect(self) -> PanelData:\n"
            "        return PanelData(status='ok', summary='')\n"
            "class RealPlugin(_AardvarkBase):\n"
            "    manifest = PluginManifest(id='real', name='Real')\n"
            "    async def collect(self) -> PanelData:\n"
            "        return PanelData(status='ok', summary='')\n"
        )
        mod = self._load_module(tmp_path, "shared_base_mod", src)
        result = PluginManager._find_plugin_class(mod)
        assert result is mod.RealPlugin

    def test_returns_none_when_only_imported_plugin_present(self, tmp_path):
        """A module that only imports a plugin but defines none returns None."""
        # Build the module that owns OtherPlugin.
        other_src = (
            "from buoy.plugins.protocol import PanelData, Plugin, PluginManifest\n"
            "class OtherPlugin(Plugin):\n"
            "    manifest = PluginManifest(id='other', name='Other')\n"
            "    async def collect(self) -> PanelData:\n"
            "        return PanelData(status='ok', summary='')\n"
        )
        other_py = tmp_path / "other_plugin_mod.py"
        other_py.write_text(other_src)
        # The module below inserts tmp_path onto sys.path and imports
        # other_plugin_mod normally, registering both as process-wide state.
        self._added_paths.append(str(tmp_path))
        self._loaded_names.append("other_plugin_mod")

        # Module under test: only imports, defines nothing.
        import_only_src = (
            "import sys\n"
            f"sys.path.insert(0, {str(tmp_path)!r})\n"
            "from other_plugin_mod import OtherPlugin  # noqa: F401\n"
        )
        mod = self._load_module(tmp_path, "import_only_mod", import_only_src)
        result = PluginManager._find_plugin_class(mod)
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

    @pytest.mark.asyncio
    async def test_collect_all_now_includes_pending(self):
        config = _make_config()
        mgr = PluginManager(config)

        # "another" has never completed a collect(), so it's missing from _latest_data
        mgr._plugins = {"fake": FakePlugin(), "another": AnotherFakePlugin()}
        mgr._latest_data = {"fake": PanelData(status="ok", summary="Fake data")}

        result = await mgr.collect_all_now()

        assert "fake" in result
        assert result["fake"]["status"] == "ok"
        assert "another" in result
        assert result["another"]["name"] == "Another"
        assert result["another"]["status"] == "pending"
        assert result["another"]["detail"] == {}


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


# =============================================================================
# resolve_plugin_env unit tests
# =============================================================================


class TestResolvePluginEnv:
    """Unit tests for the resolve_plugin_env helper."""

    def test_fills_empty_yaml_value(self, monkeypatch):
        monkeypatch.setenv("BUOY_PLUGIN_PLANE_API_KEY", "tok-123")
        schema = {"api_key": {"type": "string", "required": True}}
        result = resolve_plugin_env("plane", schema, {"api_key": ""})
        assert result["api_key"] == "tok-123"

    def test_underscore_plugin_id(self, monkeypatch):
        """trigger_dev → TRIGGER_DEV in env var name."""
        monkeypatch.setenv("BUOY_PLUGIN_TRIGGER_DEV_API_KEY", "tr_live_xxx")
        schema = {"api_key": {"type": "string"}}
        result = resolve_plugin_env("trigger_dev", schema, {})
        assert result["api_key"] == "tr_live_xxx"

    def test_underscore_key(self, monkeypatch):
        """token_secret → TOKEN_SECRET in env var name."""
        monkeypatch.setenv("BUOY_PLUGIN_PROXMOX_TOKEN_SECRET", "pve-secret")
        schema = {"token_secret": {"type": "string"}}
        result = resolve_plugin_env("proxmox", schema, {})
        assert result["token_secret"] == "pve-secret"

    def test_env_absent_settings_unchanged(self):
        schema = {"api_key": {"type": "string"}}
        original = {"api_key": "from-yaml", "url": "http://example.com"}
        result = resolve_plugin_env("plane", schema, original)
        assert result == original

    def test_env_overrides_nonempty_yaml(self, monkeypatch):
        """Env present wins even when YAML has a non-empty value."""
        monkeypatch.setenv("BUOY_PLUGIN_PLANE_API_KEY", "env-key")
        schema = {"api_key": {"type": "string"}}
        result = resolve_plugin_env("plane", schema, {"api_key": "yaml-key"})
        assert result["api_key"] == "env-key"

    def test_env_hint_in_schema_honoured(self, monkeypatch):
        """Per-key 'env' hint (e.g. BUOY_GITHUB_TOKEN) is also checked."""
        monkeypatch.setenv("BUOY_GITHUB_TOKEN", "ghp_legacy")
        schema = {"token": {"type": "string", "env": "BUOY_GITHUB_TOKEN"}}
        result = resolve_plugin_env("github", schema, {"token": ""})
        assert result["token"] == "ghp_legacy"

    def test_canonical_takes_priority_over_hint(self, monkeypatch):
        """Canonical form wins when both canonical and hint env vars are set."""
        monkeypatch.setenv("BUOY_PLUGIN_GITHUB_TOKEN", "canonical-tok")
        monkeypatch.setenv("BUOY_GITHUB_TOKEN", "hint-tok")
        schema = {"token": {"type": "string", "env": "BUOY_GITHUB_TOKEN"}}
        result = resolve_plugin_env("github", schema, {})
        assert result["token"] == "canonical-tok"

    def test_plugin_absent_from_yaml_creates_entry(self, monkeypatch):
        """Env var populates a key even when the settings dict was empty."""
        monkeypatch.setenv("BUOY_PLUGIN_GITHUB_TOKEN", "ghp_new")
        schema = {"token": {"type": "string"}}
        result = resolve_plugin_env("github", schema, {})
        assert result["token"] == "ghp_new"

    def test_returns_copy_not_mutating_original(self, monkeypatch):
        """resolve_plugin_env returns a new dict; the input is not mutated."""
        monkeypatch.setenv("BUOY_PLUGIN_PLANE_API_KEY", "env-key")
        schema = {"api_key": {"type": "string"}}
        original = {"api_key": ""}
        result = resolve_plugin_env("plane", schema, original)
        assert original["api_key"] == ""
        assert result["api_key"] == "env-key"

    def test_key_not_in_schema_not_injected(self, monkeypatch):
        """Keys absent from schema are never injected, even if a matching env var exists."""
        monkeypatch.setenv("BUOY_PLUGIN_PLANE_MYSTERY_KEY", "val")
        schema = {"api_key": {"type": "string"}}
        result = resolve_plugin_env("plane", schema, {})
        assert "mystery_key" not in result


# =============================================================================
# Integration: env vars injected at load time
# =============================================================================


class TestPluginEnvInjection:
    """Integration tests: env vars reach plugin.config via _load_builtins."""

    @pytest.mark.asyncio
    async def test_env_var_fills_plane_api_key(self, monkeypatch):
        monkeypatch.setenv("BUOY_PLUGIN_PLANE_API_KEY", "live-key-xyz")
        config = _make_config(
            builtin={
                "plane": PluginEntry(
                    enabled=True,
                    settings={
                        "api_key": "",
                        "url": "https://plane.example.com",
                        "workspace": "w",
                        "project": "p",
                    },
                )
            }
        )
        mgr = PluginManager(config)
        with patch.object(mgr, "_load_user_plugins", new_callable=AsyncMock):
            await mgr.start()

        assert "plane" in mgr.plugins
        assert mgr.plugins["plane"].config["api_key"] == "live-key-xyz"

    @pytest.mark.asyncio
    async def test_yaml_value_preserved_when_no_env(self):
        config = _make_config(
            builtin={
                "plane": PluginEntry(
                    enabled=True,
                    settings={
                        "api_key": "yaml-key",
                        "url": "https://plane.example.com",
                        "workspace": "w",
                        "project": "p",
                    },
                )
            }
        )
        mgr = PluginManager(config)
        with patch.object(mgr, "_load_user_plugins", new_callable=AsyncMock):
            await mgr.start()

        assert mgr.plugins["plane"].config["api_key"] == "yaml-key"


# =============================================================================
# _resolve_interval: global plugins_interval floor logic
# =============================================================================


def _make_config_with_refresh(plugins_interval: int):
    """Build a minimal BuoyConfig with the given refresh.plugins_interval."""
    config = _make_config()
    config.refresh = RefreshConfig(plugins_interval=plugins_interval)
    return config


class TestResolveInterval:
    """Unit tests for PluginManager._resolve_interval.

    The effective interval = max(manifest.refresh_interval, config.refresh.plugins_interval).
    """

    def test_equal_manifest_and_config(self):
        """manifest=60, config=60 → 60 (no change)."""
        config = _make_config_with_refresh(plugins_interval=60)
        mgr = PluginManager(config)
        plugin = FakePlugin()  # manifest.refresh_interval = 60
        assert mgr._resolve_interval(plugin) == 60

    def test_config_floor_raises_interval(self):
        """manifest=30, config=60 → 60 (config slows it down)."""
        config = _make_config_with_refresh(plugins_interval=60)
        mgr = PluginManager(config)
        plugin = AnotherFakePlugin()  # manifest.refresh_interval = 30
        assert mgr._resolve_interval(plugin) == 60

    def test_long_manifest_protected_from_config(self):
        """manifest=300 (github-style), config=60 → 300 (manifest protected)."""

        class SlowApiPlugin(Plugin):
            manifest = PluginManifest(id="slow_api", name="Slow", refresh_interval=300)

            async def collect(self) -> PanelData:
                return PanelData(status="ok", summary="ok")

        config = _make_config_with_refresh(plugins_interval=60)
        mgr = PluginManager(config)
        assert mgr._resolve_interval(SlowApiPlugin()) == 300

    def test_sentinel_interval_preserved(self):
        """manifest=9999 (prometheus sentinel), config=60 → 9999 (sentinel untouched)."""

        class SentinelPlugin(Plugin):
            manifest = PluginManifest(id="sentinel", name="Sentinel", refresh_interval=9999)

            async def collect(self) -> PanelData:
                return PanelData(status="ok", summary="ok")

        config = _make_config_with_refresh(plugins_interval=60)
        mgr = PluginManager(config)
        assert mgr._resolve_interval(SentinelPlugin()) == 9999

    def test_large_config_slows_all_plugins(self):
        """manifest=60, config=300 → 300 (operator deliberately slowed everything)."""
        config = _make_config_with_refresh(plugins_interval=300)
        mgr = PluginManager(config)
        plugin = FakePlugin()  # manifest.refresh_interval = 60
        assert mgr._resolve_interval(plugin) == 300

    def test_default_config_preserves_manifest(self):
        """Default RefreshConfig (plugins_interval=60) does not affect manifest=60."""
        config = _make_config()  # no explicit refresh — uses BuoyConfig default
        config.refresh = RefreshConfig()  # default plugins_interval=60
        mgr = PluginManager(config)
        plugin = FakePlugin()  # manifest.refresh_interval = 60
        assert mgr._resolve_interval(plugin) == 60
