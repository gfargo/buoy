"""Plugin loader — discovers, validates, and manages plugin lifecycle.

Scans:
1. buoy.plugins.builtin package (shipped with buoy)
2. User plugin directory (configurable, default /plugins)

Each plugin is validated against its manifest, configured from buoy.yaml,
and scheduled on its own refresh interval with error isolation.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from buoy.plugins.protocol import PanelData, Plugin

if TYPE_CHECKING:
    from buoy.config import BuoyConfig


class PluginManager:
    """Manages the full plugin lifecycle: discover → configure → run → teardown."""

    def __init__(self, config: BuoyConfig):
        self.config = config
        self._plugins: dict[str, Plugin] = {}
        self._latest_data: dict[str, PanelData] = {}
        self._tasks: list[asyncio.Task] = []

    @property
    def plugins(self) -> dict[str, Plugin]:
        return self._plugins

    @property
    def latest_data(self) -> dict[str, PanelData]:
        return self._latest_data

    async def start(self):
        """Discover, configure, setup, and start all plugins."""
        if not self.config.plugins.enabled:
            return

        # 1. Discover built-in plugins
        await self._load_builtins()

        # 2. Discover user plugins from directory
        await self._load_user_plugins()

        # 3. Setup all configured plugins
        for plugin_id, plugin in list(self._plugins.items()):
            try:
                await plugin.setup()
            except Exception as e:
                print(f"[buoy:plugins] {plugin_id} setup failed: {e}")
                del self._plugins[plugin_id]

        # 4. Start collection loops
        for plugin_id, plugin in self._plugins.items():
            task = asyncio.create_task(self._collect_loop(plugin_id, plugin))
            self._tasks.append(task)

        print(
            f"[buoy:plugins] {len(self._plugins)} plugin(s) active: {', '.join(self._plugins.keys())}"
        )

    async def stop(self):
        """Teardown all plugins and cancel tasks."""
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

        for plugin_id, plugin in self._plugins.items():
            try:
                await plugin.teardown()
            except Exception:
                pass

    async def collect_all_now(self) -> dict[str, dict]:
        """Force-collect all plugins and return their data (for API response)."""
        result = {}
        for plugin_id, plugin in self._plugins.items():
            if plugin_id in self._latest_data:
                data = self._latest_data[plugin_id]
                result[plugin_id] = {
                    "id": plugin_id,
                    "name": plugin.manifest.name,
                    "icon": plugin.manifest.icon,
                    "status": data.status,
                    "summary": data.summary,
                    "detail": data.detail,
                }
        return result

    def get_plugin_frontend_js(self) -> dict[str, str]:
        """Return custom frontend JS for plugins that provide it."""
        result = {}
        for plugin_id, plugin in self._plugins.items():
            js = plugin.frontend_js()
            if js:
                result[plugin_id] = js
        return result

    # ── Discovery ──────────────────────────────────────────────────────────────

    async def _load_builtins(self):
        """Load built-in plugins that are enabled in config."""
        builtin_map = {
            "github": "buoy.plugins.builtin.github",
            "uptime_kuma": "buoy.plugins.builtin.uptime_kuma",
            "loki": "buoy.plugins.builtin.loki",
            "plane": "buoy.plugins.builtin.plane",
            "backup_status": "buoy.plugins.builtin.backup_status",
            "cron_health": "buoy.plugins.builtin.cron_health",
            "prometheus_exporter": "buoy.plugins.builtin.prometheus_exporter",
            "speedtest": "buoy.plugins.builtin.speedtest",
        }

        for plugin_id, module_path in builtin_map.items():
            entry = self.config.plugins.builtin.get(plugin_id)
            if not entry or not entry.enabled:
                continue

            try:
                module = importlib.import_module(module_path)
                plugin_class = self._find_plugin_class(module)
                if plugin_class:
                    instance = plugin_class()
                    instance.configure(entry.settings)
                    self._plugins[plugin_id] = instance
            except Exception as e:
                print(f"[buoy:plugins] Failed to load builtin '{plugin_id}': {e}")

    async def _load_user_plugins(self):
        """Load user plugins from the plugins directory."""
        plugin_dir = Path(self.config.plugins.directory)
        if not plugin_dir.exists():
            return

        for py_file in plugin_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue

            module_name = f"buoy_user_plugin_{py_file.stem}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if not spec or not spec.loader:
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)

                plugin_class = self._find_plugin_class(module)
                if plugin_class:
                    instance = plugin_class()
                    # User plugins don't have config entries (yet)
                    instance.configure({})
                    plugin_id = instance.manifest.id
                    self._plugins[plugin_id] = instance
            except Exception as e:
                print(f"[buoy:plugins] Failed to load user plugin '{py_file.name}': {e}")

    @staticmethod
    def _find_plugin_class(module) -> type[Plugin] | None:
        """Find the first Plugin subclass in a module."""
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Plugin) and obj is not Plugin:
                return obj
        return None

    # ── Collection Loop ────────────────────────────────────────────────────────

    async def _collect_loop(self, plugin_id: str, plugin: Plugin):
        """Run a plugin's collect() on its configured interval, with error isolation."""
        interval = plugin.manifest.refresh_interval
        # Initial collect immediately
        await self._safe_collect(plugin_id, plugin)

        while True:
            await asyncio.sleep(interval)
            await self._safe_collect(plugin_id, plugin)

    async def _safe_collect(self, plugin_id: str, plugin: Plugin):
        """Collect from a plugin, catching all exceptions."""
        try:
            data = await asyncio.wait_for(plugin.collect(), timeout=30)
            self._latest_data[plugin_id] = data
        except TimeoutError:
            self._latest_data[plugin_id] = PanelData(
                status="error", summary="Timeout", detail={"error": "collect timed out"}
            )
        except Exception as e:
            self._latest_data[plugin_id] = PanelData(
                status="error", summary="Error", detail={"error": str(e)}
            )
