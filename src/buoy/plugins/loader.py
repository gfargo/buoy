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
import os
import pkgutil
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from buoy.plugins.protocol import PanelData, Plugin

if TYPE_CHECKING:
    from buoy.config import BuoyConfig


def resolve_plugin_env(
    plugin_id: str, schema: dict[str, Any], settings: dict[str, Any]
) -> dict[str, Any]:
    """Return settings merged with BUOY_PLUGIN_<ID>_<KEY> env overrides.

    Iterates declared schema keys (not env var names) to avoid underscore-splitting
    ambiguity (e.g. trigger_dev/api_key → BUOY_PLUGIN_TRIGGER_DEV_API_KEY is
    unambiguous because both sides are known).

    Precedence: canonical env var wins, then per-key 'env' hint in schema, then YAML.
    Only string values are written; secrets are always strings so no coercion needed.
    User plugins have no schema and are not affected (they call configure({}) directly).
    """
    result = dict(settings)
    plugin_prefix = f"BUOY_PLUGIN_{plugin_id.upper()}_"
    for key, meta in schema.items():
        canonical = f"{plugin_prefix}{key.upper()}"
        value = os.environ.get(canonical)
        if value is None and isinstance(meta, dict):
            hint = meta.get("env")
            if hint:
                value = os.environ.get(hint)
        if value is not None:
            result[key] = value
    return result


class PluginManager:
    """Manages the full plugin lifecycle: discover → configure → run → teardown."""

    def __init__(self, config: BuoyConfig):
        self.config = config
        self._plugins: dict[str, Plugin] = {}
        self._latest_data: dict[str, PanelData] = {}
        self._tasks: list[asyncio.Task] = []
        # Per-plugin health: last_collect_at (epoch seconds of last *successful*
        # collect), last_error (cleared on success), consecutive_failures (reset
        # on success).
        self._health: dict[str, dict[str, Any]] = {}
        # id -> manifest display name for every builtin module discovered during
        # _load_builtins, regardless of whether it's configured/enabled or its
        # setup() later failed. Lets _configured_not_loaded show a real name
        # instead of reusing the config key.
        self._builtin_names: dict[str, str] = {}

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
        """Return current panel data for every registered plugin.

        Plugins that have not completed their first collect() yet are reported
        with status "pending" rather than omitted, so a slow or failing initial
        collect surfaces as a pending/errored card instead of disappearing.

        Also includes a stub entry (``loaded: False``) for every builtin that is
        configured+enabled but failed to load (import or setup() error), so a
        misconfigured plugin surfaces on the dashboard instead of silently
        vanishing.
        """
        result = {}
        for plugin_id, plugin in self._plugins.items():
            data = self._latest_data.get(plugin_id)
            if data is None:
                data = PanelData(status="pending", summary="Collecting…")
            health = self._health.get(plugin_id, {})
            result[plugin_id] = {
                "id": plugin_id,
                "name": plugin.manifest.name,
                "icon": plugin.manifest.icon,
                "status": data.status,
                "summary": data.summary,
                "detail": data.detail,
                "loaded": True,
                "last_collect_at": health.get("last_collect_at"),
                "last_error": health.get("last_error"),
                "consecutive_failures": health.get("consecutive_failures", 0),
            }

        for plugin_id, name in self._configured_not_loaded():
            result[plugin_id] = {
                "id": plugin_id,
                "name": name,
                "icon": "🔌",
                "status": "error",
                "summary": "Failed to load",
                "detail": {},
                "loaded": False,
                "last_collect_at": None,
                "last_error": None,
                "consecutive_failures": 0,
            }
        return result

    def _configured_not_loaded(self) -> list[tuple[str, str]]:
        """Return (id, name) for builtins that are enabled in config but not loaded.

        A builtin can be configured+enabled yet absent from self._plugins if its
        module failed to import or its setup() raised (see _load_builtins and
        start()). User plugins have no config entry and are always loaded when
        present, so they're unaffected by this check.

        Returns nothing when the plugin subsystem itself is globally disabled
        (``plugins.enabled: false``) — in that case ``start()`` never runs, so
        every configured builtin would otherwise show up mislabeled as "Failed
        to load" instead of intentionally off.
        """
        if not self.config.plugins.enabled:
            return []
        return [
            (plugin_id, self._builtin_names.get(plugin_id, plugin_id))
            for plugin_id, entry in self.config.plugins.builtin.items()
            if entry.enabled and plugin_id not in self._plugins
        ]

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
        """Load built-in plugins that are enabled in config.

        Discovers modules from the fixed in-repo buoy.plugins.builtin package only —
        never from a config/env/volume/user-writable path.
        """
        import buoy.plugins.builtin as _builtin_pkg

        for _, module_path, _ispkg in pkgutil.iter_modules(
            _builtin_pkg.__path__, _builtin_pkg.__name__ + "."
        ):
            # Skip private/dunder helper modules
            module_name = module_path.rsplit(".", 1)[-1]
            if module_name.startswith("_"):
                continue

            try:
                module = importlib.import_module(module_path)
                plugin_class = self._find_plugin_class(module)
                if plugin_class is None:
                    continue
                plugin_id = plugin_class.manifest.id
                self._builtin_names[plugin_id] = plugin_class.manifest.name
                entry = self.config.plugins.builtin.get(plugin_id)
                if not entry or not entry.enabled:
                    continue
                instance = plugin_class()
                settings = resolve_plugin_env(
                    plugin_id,
                    plugin_class.manifest.config_schema,
                    entry.settings,
                )
                instance.configure(settings)
                self._plugins[plugin_id] = instance
            except Exception as e:
                print(f"[buoy:plugins] Failed to load builtin '{module_path}': {e}")

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
        """Find the Plugin subclass a module defines.

        Only classes *defined* in ``module`` itself are considered (guarded by
        ``obj.__module__ == module.__name__``). This prevents picking up an
        imported concrete plugin or a shared base class from another module.

        Among the locally-defined candidates, a class that declares its own
        ``manifest`` in its ``__dict__`` is preferred — this is the explicit
        marker of a concrete plugin and distinguishes it from an intermediate
        base class that merely subclasses Plugin without providing a manifest.

        If a module defines more than one manifest-bearing plugin class,
        ``inspect.getmembers`` returns them in alphabetical-by-name order and
        the alphabetically first one is returned.  The one-plugin-per-module
        contract makes this edge case benign in practice.
        """
        module_name = getattr(module, "__name__", None)
        candidates = [
            obj
            for _name, obj in inspect.getmembers(module, inspect.isclass)
            if issubclass(obj, Plugin) and obj is not Plugin and obj.__module__ == module_name
        ]
        if not candidates:
            return None
        # Prefer a class that explicitly declares its own manifest (concrete
        # plugin), falling back to any candidate if none do.
        marked = [obj for obj in candidates if "manifest" in obj.__dict__]
        return (marked or candidates)[0]

    # ── Collection Loop ────────────────────────────────────────────────────────

    def _resolve_interval(self, plugin: Plugin) -> int:
        """Resolve the effective collection interval for a plugin.

        Uses the greater of the plugin's manifest interval and the global
        ``refresh.plugins_interval`` config value.  This lets the global value
        act as a floor that slows down collection (its documented purpose)
        without ever shortening intentionally long intervals such as the
        github plugin (300 s) or the prometheus_exporter sentinel (9999 s).
        """
        return max(plugin.manifest.refresh_interval, self.config.refresh.plugins_interval)

    async def _collect_loop(self, plugin_id: str, plugin: Plugin):
        """Run a plugin's collect() on its configured interval, with error isolation."""
        # Resolved once at loop start, not re-evaluated per iteration: config is
        # static for the process lifetime today. A future live-reload feature
        # would need to re-resolve this inside the loop to pick up changes.
        interval = self._resolve_interval(plugin)
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
            self._record_success(plugin_id)
        except TimeoutError:
            self._latest_data[plugin_id] = PanelData(
                status="error", summary="Timeout", detail={"error": "collect timed out"}
            )
            self._record_failure(plugin_id, "collect timed out")
        except Exception as e:
            self._latest_data[plugin_id] = PanelData(
                status="error", summary="Error", detail={"error": str(e)}
            )
            self._record_failure(plugin_id, str(e))

    def _record_success(self, plugin_id: str) -> None:
        self._health[plugin_id] = {
            "last_collect_at": time.time(),
            "last_error": None,
            "consecutive_failures": 0,
        }

    def _record_failure(self, plugin_id: str, error: str) -> None:
        health = self._health.setdefault(
            plugin_id, {"last_collect_at": None, "last_error": None, "consecutive_failures": 0}
        )
        health["last_error"] = error
        health["consecutive_failures"] += 1
