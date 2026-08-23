"""Tests that demo mode stubs every plugin instead of making real outbound calls.

BUG-40 / OSS-1304: --demo with plugins enabled used to call setup()/collect()
for real, making outbound network/subprocess/MQTT calls and rendering error
cards. These tests assert the loader never reaches a plugin's I/O in demo
mode, and that every built-in plugin's demo_data() renders cleanly.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import urllib.request
from dataclasses import dataclass, field

import pytest

from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig
from buoy.demo import DEMO_PLUGIN_IDS
from buoy.plugins.loader import PluginManager
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

# =============================================================================
# Config helpers (mirrors tests/test_loader.py's local stubs)
# =============================================================================


@dataclass
class PluginEntry:
    enabled: bool = True
    refresh_interval: int | None = None
    settings: dict = field(default_factory=dict)


@dataclass
class PluginsConfig:
    enabled: bool = True
    directory: str = "/plugins"
    builtin: dict = field(default_factory=dict)
    user: dict = field(default_factory=dict)


def _make_demo_config(builtin=None):
    config = BuoyConfig()
    config.node = NodeConfig(name="test")
    config.network = NetworkConfig()
    config.features = FeaturesConfig(demo_mode=True)
    config.plugins = PluginsConfig(enabled=True, builtin=builtin or {})
    return config


def _discover_builtin_classes_with_manifest():
    import importlib
    import pkgutil

    import buoy.plugins.builtin as builtin_pkg

    classes = []
    for _, module_path, _ispkg in pkgutil.iter_modules(
        builtin_pkg.__path__, builtin_pkg.__name__ + "."
    ):
        module_name = module_path.rsplit(".", 1)[-1]
        if module_name.startswith("_"):
            continue
        module = importlib.import_module(module_path)
        plugin_class = PluginManager._find_plugin_class(module)
        if plugin_class is not None:
            classes.append({"_class": plugin_class, "id": plugin_class.manifest.id})
    return classes


# =============================================================================
# No-I/O guarantee
# =============================================================================


class _BoomError(RuntimeError):
    pass


def _raise(*_a, **_k):
    raise _BoomError("outbound I/O attempted in demo mode")


class TestDemoModeNoIO:
    @pytest.mark.asyncio
    async def test_start_and_collect_never_touch_the_network_or_subprocess(self, monkeypatch):
        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        monkeypatch.setattr(socket, "socket", _raise)
        monkeypatch.setattr(socket, "create_connection", _raise)
        monkeypatch.setattr(subprocess, "run", _raise)

        async def _boom_subprocess(*_a, **_k):
            raise _BoomError("subprocess exec attempted in demo mode")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom_subprocess)

        ids = [c["id"] for c in _discover_builtin_classes_with_manifest()]
        assert ids, "expected at least one builtin plugin id"
        builtin = {pid: PluginEntry(enabled=True) for pid in ids}
        config = _make_demo_config(builtin=builtin)

        mgr = PluginManager(config)
        await mgr.start()

        results = await mgr.collect_all_now()
        assert len(results) == len(ids)
        for plugin_id, panel in results.items():
            assert panel["loaded"] is True, f"{plugin_id} failed to load in demo mode"
            assert panel["status"] != "error", f"{plugin_id} rendered an error card in demo mode"

        await mgr.stop()

    @pytest.mark.asyncio
    async def test_setup_collect_teardown_never_called(self, monkeypatch):
        calls = []

        class NoisyPlugin(Plugin):
            manifest = PluginManifest(id="noisy", name="Noisy", refresh_interval=5)

            async def setup(self) -> None:
                calls.append("setup")
                raise _BoomError("setup called in demo mode")

            async def collect(self) -> PanelData:
                calls.append("collect")
                raise _BoomError("collect called in demo mode")

            async def teardown(self) -> None:
                calls.append("teardown")
                raise _BoomError("teardown called in demo mode")

        config = _make_demo_config(builtin={"noisy": PluginEntry(enabled=True)})
        mgr = PluginManager(config)
        mgr._iter_builtin_classes = lambda: iter([NoisyPlugin])

        await mgr.start()
        assert calls == []
        assert mgr._tasks == []
        assert "noisy" in mgr.plugins

        data = mgr.latest_data["noisy"]
        assert data.status != "error"

        await mgr.stop()
        assert calls == []


# =============================================================================
# Every builtin's demo_data() renders cleanly
# =============================================================================


_ALL_BUILTIN_CLASSES = [c["_class"] for c in _discover_builtin_classes_with_manifest()]


class TestBuiltinDemoData:
    @pytest.mark.parametrize("plugin_class", _ALL_BUILTIN_CLASSES, ids=lambda c: c.manifest.id)
    def test_demo_data_renders_without_raising(self, plugin_class):
        instance = plugin_class()
        data = instance.demo_data()

        assert isinstance(data, PanelData)
        assert data.status in {"ok", "warn", "error", "disabled", "pending", "unavailable"}
        assert data.status != "error"
        assert data.summary

        panel = instance.render(data)
        assert panel is None or isinstance(panel, list)


# =============================================================================
# Curated defaults / operator override
# =============================================================================


class TestDemoDefaults:
    @pytest.mark.asyncio
    async def test_empty_config_gets_curated_defaults(self):
        config = _make_demo_config(builtin={})
        mgr = PluginManager(config)
        await mgr.start()
        assert set(DEMO_PLUGIN_IDS) <= set(mgr.plugins)
        await mgr.stop()

    @pytest.mark.asyncio
    async def test_real_config_selection_is_respected(self):
        config = _make_demo_config(
            builtin={"loki": PluginEntry(enabled=True, settings={"url": ""})}
        )
        mgr = PluginManager(config)
        await mgr.start()
        assert set(mgr.plugins) == {"loki"}
        await mgr.stop()


# =============================================================================
# Required-field bypass
# =============================================================================


class TestDemoConfigBypass:
    @pytest.mark.asyncio
    async def test_missing_required_field_still_registers_in_demo_mode(self):
        config = _make_demo_config(builtin={"github": PluginEntry(enabled=True, settings={})})
        mgr = PluginManager(config)
        await mgr.start()

        assert "github" in mgr.plugins
        data = mgr.latest_data["github"]
        assert data.status != "disabled"
        assert "Config error" not in data.summary
        await mgr.stop()
