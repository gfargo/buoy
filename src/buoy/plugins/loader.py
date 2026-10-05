"""Plugin loader — discovers, validates, and manages plugin lifecycle.

Scans, in order (later sources win on id collision):
1. buoy.plugins.builtin package (shipped with buoy)
2. buoy.plugins entry points (pip-installed third-party packages)
3. User plugin directory (configurable, default /plugins)

Each plugin is validated against its manifest, configured from buoy.yaml,
and scheduled on its own refresh interval with error isolation.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import importlib.util
import inspect
import logging
import os
import pkgutil
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from buoy.config import PluginEntry
from buoy.demo import DEMO_PLUGIN_IDS
from buoy.plugins.protocol import PanelData, Plugin
from buoy.redaction import is_secret_key

if TYPE_CHECKING:
    from buoy.config import BuoyConfig

logger = logging.getLogger("buoy.plugins")

ENTRY_POINT_GROUP = "buoy.plugins"

# Wall-clock cap on a single collect() call, enforced by _safe_collect. Shared
# with the detail payload (health.timeout_seconds) so the frontend's "timed
# out after Ns" note can never drift from the value actually enforced here.
COLLECT_TIMEOUT = 30


def _env_origin(plugin_id: str, key: str, meta: dict[str, Any] | None) -> str | None:
    """Return the env var name that would override *key* for *plugin_id*, if any is set.

    Checks the canonical ``BUOY_PLUGIN_<ID>_<KEY>`` var first, then the
    schema's per-key ``env`` hint — same precedence ``resolve_plugin_env``
    applies, so the two can't diverge. Returns None when neither is set,
    regardless of whether either name is declared.
    """
    canonical = f"BUOY_PLUGIN_{plugin_id.upper()}_{key.upper()}"
    if os.environ.get(canonical) is not None:
        return canonical
    if isinstance(meta, dict):
        hint = meta.get("env")
        if hint and os.environ.get(hint) is not None:
            return hint
    return None


def resolve_plugin_env(
    plugin_id: str, schema: dict[str, Any], settings: dict[str, Any]
) -> dict[str, Any]:
    """Return settings merged with BUOY_PLUGIN_<ID>_<KEY> env overrides.

    Iterates declared schema keys (not env var names) to avoid underscore-splitting
    ambiguity (e.g. trigger_dev/api_key → BUOY_PLUGIN_TRIGGER_DEV_API_KEY is
    unambiguous because both sides are known).

    Precedence: canonical env var wins, then per-key 'env' hint in schema, then YAML.
    Only string values are written; secrets are always strings so no coercion needed.
    Used by built-in, entry-point, and user plugins alike; a plugin with no
    declared config_schema is simply unaffected by env overrides.
    """
    result = dict(settings)
    for key, meta in schema.items():
        origin = _env_origin(plugin_id, key, meta if isinstance(meta, dict) else None)
        if origin is not None:
            result[key] = os.environ.get(origin)
    return result


def _coerce(value: Any, declared_type: str | None) -> Any:
    """Coerce *value* to *declared_type*.

    Supports the 5 type names used across builtin manifests. Idempotent on
    already-correct YAML-native values (e.g. a real ``bool``/``int`` passes
    through unchanged rather than being re-stringified). Unknown/missing
    types are left untouched. Raises ValueError/TypeError on failure, which
    callers collect as a validation error rather than letting propagate.
    """
    if declared_type in ("boolean", "bool"):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)
    if declared_type == "integer":
        return int(value)
    if declared_type == "number":
        return float(value)
    if declared_type == "array":
        if isinstance(value, list):
            return list(value)
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        raise TypeError(f"cannot coerce {value!r} to array")
    if declared_type == "string":
        return value if isinstance(value, str) else str(value)
    return value


def validate_plugin_config(
    plugin_id: str, schema: dict[str, Any], settings: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Apply defaults, coerce types, and check required fields against *schema*.

    Runs once at registration, after env overlay (resolve_plugin_env) and
    before instance.configure(). Fixes BUG-35 (env/YAML strings like "true"
    or "30" not becoming bool/int) and turns a missing required field into an
    actionable disabled-card message instead of a runtime error deep in
    collect(). Plugins with an empty schema pass through untouched.

    *plugin_id* prefixes each error message so the disabled card stays
    unambiguous when a config includes multiple plugins that share a key name.

    Returns (coerced_settings, errors). A non-empty errors list means the
    plugin should be registered but not started (see _register_config_gated_plugin).
    """
    result = dict(settings)
    errors: list[str] = []

    for key, meta in schema.items():
        if not isinstance(meta, dict):
            continue

        if key not in result and "default" in meta:
            default = meta["default"]
            result[key] = list(default) if isinstance(default, list) else default

        if key in result and result[key] is not None:
            declared_type = meta.get("type")
            try:
                result[key] = _coerce(result[key], declared_type)
            except (ValueError, TypeError):
                errors.append(f"{plugin_id}: invalid value for '{key}': expected {declared_type}")
                continue

        if meta.get("required"):
            value = result.get(key)
            is_empty_collection = isinstance(value, (list, dict, tuple)) and not value
            if value is None or value == "" or is_empty_collection:
                errors.append(f"{plugin_id}: missing required field '{key}'")

    return result, errors


class PluginManager:
    """Manages the full plugin lifecycle: discover → configure → run → teardown."""

    def __init__(self, config: BuoyConfig):
        self.config = config
        self._demo = config.features.demo_mode
        self._plugins: dict[str, Plugin] = {}
        self._latest_data: dict[str, PanelData] = {}
        self._tasks: list[asyncio.Task] = []
        self._disabled_ids: set[str] = set()
        # Per-plugin health: last_collect_at (epoch seconds of last *successful*
        # collect), last_error (cleared on success), consecutive_failures (reset
        # on success).
        self._health: dict[str, dict[str, Any]] = {}
        # id -> manifest display name for every builtin module discovered during
        # _load_builtins, regardless of whether it's configured/enabled or its
        # setup() later failed. Lets _configured_not_loaded show a real name
        # instead of reusing the config key.
        self._builtin_names: dict[str, str] = {}
        # id -> "builtin" | "entrypoint" | "dir", recorded at registration time.
        self._plugin_sources: dict[str, str] = {}
        # ids with a collect() currently in flight (scheduled loop or a manual
        # refresh-now), so a second collect_once() call can report "busy"
        # instead of running concurrently with itself.
        self._collecting: set[str] = set()
        # id -> config validation errors, mirrored here (also stored in the
        # "disabled" PanelData.detail) so the detail payload can show the
        # full list without re-deriving it from PanelData.
        self._config_errors: dict[str, list[str]] = {}

    @property
    def plugins(self) -> dict[str, Plugin]:
        return self._plugins

    @property
    def latest_data(self) -> dict[str, PanelData]:
        return self._latest_data

    async def start(self):
        """Discover, configure, setup, and start all plugins.

        In demo mode, no plugin ever runs setup()/collect() — every plugin is
        seeded from demo_data() instead, so `--demo` never makes a real
        outbound call. See _seed_demo_data.
        """
        if not self.config.plugins.enabled:
            return

        if self._demo:
            self._inject_demo_defaults()

        # 1. Discover built-in plugins
        await self._load_builtins()

        # 2. Discover plugins registered via the buoy.plugins entry-point group
        await self._load_entrypoint_plugins()

        # 3. Discover user plugins from directory
        await self._load_user_plugins()

        if self._demo:
            self._seed_demo_data()
            logger.info(
                "%d plugin(s) active (demo mode): %s",
                len(self._plugins),
                ", ".join(self._plugins.keys()),
            )
            return

        # 4. Setup all configured plugins (skip those disabled by config validation)
        for plugin_id, plugin in list(self._plugins.items()):
            if plugin_id in self._disabled_ids:
                continue
            try:
                await plugin.setup()
            except Exception as e:
                logger.warning("%s setup failed: %s", plugin_id, e, exc_info=True)
                # setup() may acquire resources before failing. Give the plugin a
                # chance to roll them back before removing the only reference that
                # stop() would otherwise use for teardown.
                try:
                    await plugin.teardown()
                except Exception:
                    logger.debug("%s rollback teardown failed", plugin_id, exc_info=True)
                del self._plugins[plugin_id]
                self._plugin_sources.pop(plugin_id, None)

        # 5. Start collection loops (skip those disabled by config validation)
        for plugin_id, plugin in self._plugins.items():
            if plugin_id in self._disabled_ids:
                continue
            task = asyncio.create_task(self._collect_loop(plugin_id, plugin))
            self._tasks.append(task)

        logger.info("%d plugin(s) active: %s", len(self._plugins), ", ".join(self._plugins.keys()))

    def _inject_demo_defaults(self) -> None:
        """Enable a curated set of builtins when demo mode has none configured.

        Only kicks in when the operator hasn't enabled any builtin themselves
        (e.g. a bare `docker run ... --demo`) — a demo run over a real config
        that does enable plugins shows exactly those, stubbed, and respects
        the operator's selection.
        """
        if any(entry.enabled for entry in self.config.plugins.builtin.values()):
            return
        for plugin_id in DEMO_PLUGIN_IDS:
            self.config.plugins.builtin.setdefault(plugin_id, PluginEntry(enabled=True))

    def _seed_demo_data(self) -> None:
        """Populate latest_data from each registered plugin's demo_data(), no I/O."""
        for plugin_id, plugin in self._plugins.items():
            if plugin_id in self._disabled_ids:
                continue
            try:
                data = plugin.demo_data()
            except Exception as e:
                logger.warning("%s demo_data() failed: %s", plugin_id, e, exc_info=True)
                data = Plugin.demo_data(plugin)
            self._latest_data[plugin_id] = data
            self._record_success(plugin_id)

    async def stop(self):
        """Stop collection tasks before tearing down their plugin resources."""
        tasks = self._tasks[:]
        for task in tasks:
            task.cancel()
        awaitables = [task for task in tasks if inspect.isawaitable(task)]
        if awaitables:
            await asyncio.gather(*awaitables, return_exceptions=True)
        self._tasks.clear()

        if self._demo:
            # Nothing was set up (setup() is skipped in demo mode), so there's
            # nothing to tear down.
            return

        for plugin_id, plugin in self._plugins.items():
            try:
                await plugin.teardown()
            except Exception:
                logger.debug("%s teardown failed", plugin_id, exc_info=True)

    def get_plugin_payload(self, plugin_id: str, *, detail: bool = False) -> dict[str, Any] | None:
        """Return the panel payload for one registered plugin, or None if unknown.

        Shared by ``collect_all_now()`` (list view, ``detail=False``) and the
        ``GET /api/plugins/{id}`` route (``detail=True``). With ``detail=True``,
        adds ``detail_panel`` (from ``render_detail()``, falling back to
        ``panel`` and logging on exception, mirroring the ``render()``
        handling below) and ``manifest``. Keeping these keys out of the
        non-detail payload is what keeps the 60s poll payload small.
        """
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            return None
        data = self._latest_data.get(plugin_id)
        if data is None:
            data = PanelData(status="pending", summary="Collecting…")
        try:
            panel = plugin.render(data)
        except Exception as e:
            logger.warning("%s render() failed: %s", plugin_id, e, exc_info=True)
            panel = None
        health = self._health.get(plugin_id, {})
        payload = {
            "id": plugin_id,
            "name": plugin.manifest.name,
            "icon": plugin.manifest.icon,
            "status": data.status,
            "summary": data.summary,
            "detail": data.detail,
            "panel": panel,
            "loaded": True,
            "last_collect_at": health.get("last_collect_at"),
            "last_error": health.get("last_error"),
            "consecutive_failures": health.get("consecutive_failures", 0),
        }
        if detail:
            try:
                detail_panel = plugin.render_detail(data)
            except Exception as e:
                logger.warning("%s render_detail() failed: %s", plugin_id, e, exc_info=True)
                detail_panel = panel
            entry = self.config.plugins.user.get(plugin_id) or self.config.plugins.builtin.get(
                plugin_id
            )
            payload["detail_panel"] = detail_panel
            payload["manifest"] = {
                "id": plugin_id,
                "name": plugin.manifest.name,
                "icon": plugin.manifest.icon,
                "description": plugin.manifest.description,
                "version": plugin.manifest.version,
                "source": self._plugin_sources.get(plugin_id),
                "refresh_interval": plugin.manifest.refresh_interval,
                "refresh_interval_override": entry.refresh_interval if entry else None,
                "plugins_interval_floor": self.config.refresh.plugins_interval,
                "effective_refresh_interval": self._resolve_interval(plugin),
            }
            payload["health"] = {
                "last_collect_at": health.get("last_collect_at"),
                "last_attempt_at": health.get("last_attempt_at"),
                "last_collect_duration_ms": health.get("last_collect_duration_ms"),
                "last_error": health.get("last_error"),
                "consecutive_failures": health.get("consecutive_failures", 0),
                "timed_out": health.get("last_error") == "collect timed out",
                "timeout_seconds": COLLECT_TIMEOUT,
            }
            payload["config"] = self._config_rows(plugin_id, plugin)
            payload["config_errors"] = self._config_errors.get(plugin_id, [])
            payload["disabled"] = plugin_id in self._disabled_ids
        return payload

    def _config_rows(self, plugin_id: str, plugin: Plugin) -> list[dict[str, Any]]:
        """Return one row per ``config_schema`` key: effective value, origin, secrecy.

        "Effective value" is the coerced value from ``plugin.config`` (post
        env-overlay, post validate_plugin_config), not the raw YAML — this is
        what the plugin actually uses. Secret values are redacted regardless
        of origin. ``source`` mirrors the precedence resolve_plugin_env
        applies: canonical env var > per-key env hint > YAML > schema default
        > unset.
        """
        entry = self.config.plugins.user.get(plugin_id) or self.config.plugins.builtin.get(
            plugin_id
        )
        rows = []
        for key, meta in (plugin.manifest.config_schema or {}).items():
            meta = meta if isinstance(meta, dict) else {}
            value = (plugin.config or {}).get(key)
            secret = is_secret_key(key, meta)
            env_origin = _env_origin(plugin_id, key, meta)
            if env_origin:
                source = f"env:{env_origin}"
            elif entry and key in entry.settings:
                source = "yaml"
            elif "default" in meta:
                source = "default"
            else:
                source = "unset"
            rows.append(
                {
                    "key": key,
                    "value": "***REDACTED***" if secret and value not in (None, "") else value,
                    "secret": secret,
                    "source": source,
                    "required": bool(meta.get("required")),
                    "type": meta.get("type"),
                }
            )
        return rows

    def _not_loaded_stub(
        self, plugin_id: str, name: str, *, detail: bool = False
    ) -> dict[str, Any]:
        """Return the ``loaded: False`` stub for a configured-but-not-loaded builtin.

        No plugin instance exists in this case, so detail mode gets ``None``
        for ``detail_panel``/``manifest`` rather than guessed values.
        """
        stub = {
            "id": plugin_id,
            "name": name,
            "icon": "🔌",
            "status": "error",
            "summary": "Failed to load",
            "detail": {},
            "panel": None,
            "loaded": False,
            "last_collect_at": None,
            "last_error": None,
            "consecutive_failures": 0,
        }
        if detail:
            stub["detail_panel"] = None
            stub["manifest"] = None
            stub["health"] = None
            stub["config"] = []
            stub["config_errors"] = []
            stub["disabled"] = False
        return stub

    def get_plugin_or_stub_payload(
        self, plugin_id: str, *, detail: bool = False
    ) -> dict[str, Any] | None:
        """Return a plugin's payload, its not-loaded stub, or None if unknown.

        Used by the ``GET /api/plugins/{id}`` route, which needs the same
        "loaded vs. configured-but-not-loaded vs. unknown" resolution that
        ``collect_all_now()`` applies across all plugins, but for one id.
        """
        payload = self.get_plugin_payload(plugin_id, detail=detail)
        if payload is not None:
            return payload
        for stub_id, name in self._configured_not_loaded():
            if stub_id == plugin_id:
                return self._not_loaded_stub(stub_id, name, detail=detail)
        return None

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
        for plugin_id in self._plugins:
            result[plugin_id] = self.get_plugin_payload(plugin_id)

        for plugin_id, name in self._configured_not_loaded():
            result[plugin_id] = self._not_loaded_stub(plugin_id, name)
        return result

    def health_summary(self) -> dict[str, Any]:
        """Aggregate per-plugin status for the unauthenticated ``/api/health`` endpoint.

        Reuses the same status bookkeeping ``collect_all_now()`` already
        exposes per plugin (``_health``, ``_disabled_ids``,
        ``_configured_not_loaded()``) rather than running any new collection.
        ``last_error`` is already public via ``GET /api/plugins/{id}``, but is
        truncated here to keep this summary compact.
        """
        counts = {"ok": 0, "error": 0, "disabled": 0, "not_loaded": 0}
        entries: list[dict[str, Any]] = []

        for plugin_id, plugin in self._plugins.items():
            health = self._health.get(plugin_id, {})
            last_error = health.get("last_error")
            if plugin_id in self._disabled_ids:
                status = "disabled"
            elif last_error:
                status = "error"
            else:
                status = "ok"
            counts[status] += 1
            entries.append(
                {
                    "id": plugin_id,
                    "name": plugin.manifest.name,
                    "status": status,
                    "last_error": last_error[:200] if last_error else None,
                }
            )

        for plugin_id, name in self._configured_not_loaded():
            counts["not_loaded"] += 1
            entries.append(
                {"id": plugin_id, "name": name, "status": "not_loaded", "last_error": None}
            )

        return {"total": len(entries), **counts, "entries": entries}

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
        """Return custom frontend JS, isolating failures to one plugin."""
        result = {}
        for plugin_id, plugin in self._plugins.items():
            try:
                js = plugin.frontend_js()
            except Exception as e:
                logger.warning("%s frontend_js() failed: %s", plugin_id, e, exc_info=True)
                continue
            if js:
                result[plugin_id] = js
        return result

    # ── Discovery ──────────────────────────────────────────────────────────────

    async def _load_builtins(self):
        """Load built-in plugins that are enabled in config.

        Discovers modules from the fixed in-repo buoy.plugins.builtin package only —
        never from a config/env/volume/user-writable path.
        """
        for plugin_class in self._iter_builtin_classes():
            try:
                self._builtin_names[plugin_class.manifest.id] = plugin_class.manifest.name
                self._register_config_gated_plugin(plugin_class, source="builtin")
            except Exception as e:
                logger.warning(
                    "Failed to register builtin '%s': %s",
                    plugin_class.manifest.id,
                    e,
                    exc_info=True,
                )

    async def _load_entrypoint_plugins(self):
        """Load plugins registered under the ``buoy.plugins`` entry-point group.

        Lets a pip-installed third-party package register a plugin without
        touching the in-repo ``buoy.plugins.builtin`` package or the user
        plugin directory. Subject to the same enable gate and env overlay as
        built-ins (``config.plugins.builtin.<id>.enabled``).
        """
        for plugin_class in self._iter_entrypoint_classes():
            try:
                self._register_config_gated_plugin(plugin_class, source="entrypoint")
            except Exception as e:
                logger.warning(
                    "Failed to register entry point plugin '%s': %s",
                    plugin_class.manifest.id,
                    e,
                    exc_info=True,
                )

    async def _load_user_plugins(self):
        """Load user plugins from the plugins directory."""
        plugin_dir = Path(self.config.plugins.directory)
        for plugin_class in self._iter_dir_classes(plugin_dir):
            try:
                plugin_id = plugin_class.manifest.id
                entry = self.config.plugins.user.get(plugin_id)
                if entry and not entry.enabled:
                    continue
                instance = plugin_class()
                settings = resolve_plugin_env(
                    plugin_id,
                    plugin_class.manifest.config_schema,
                    entry.settings if entry else {},
                )
                settings, errors = validate_plugin_config(
                    plugin_id, plugin_class.manifest.config_schema, settings
                )
                # Do not mutate collision/disabled state until the replacement
                # plugin has accepted its configuration. A failed override must
                # leave the previously registered plugin intact and active.
                instance.configure(settings)
                self._warn_on_collision(plugin_id, source="dir")
                if errors and self._demo:
                    logger.debug(
                        "%s: ignoring config errors in demo mode: %s",
                        plugin_id,
                        "; ".join(errors),
                    )
                    self._disabled_ids.discard(plugin_id)
                    self._latest_data.pop(plugin_id, None)
                    self._config_errors.pop(plugin_id, None)
                elif errors:
                    self._disabled_ids.add(plugin_id)
                    self._latest_data[plugin_id] = PanelData(
                        status="disabled",
                        summary=f"Config error: {'; '.join(errors)}",
                        detail={"errors": errors},
                    )
                    self._config_errors[plugin_id] = errors
                else:
                    self._disabled_ids.discard(plugin_id)
                    self._latest_data.pop(plugin_id, None)
                    self._config_errors.pop(plugin_id, None)
                self._plugins[plugin_id] = instance
                self._plugin_sources[plugin_id] = "dir"
            except Exception as e:
                logger.warning(
                    "Failed to register user plugin '%s': %s",
                    plugin_class.manifest.id,
                    e,
                    exc_info=True,
                )

    _SOURCE_LABELS = {"builtin": "builtin", "entrypoint": "entry point", "dir": "user directory"}

    def _warn_on_collision(self, plugin_id: str, source: str) -> None:
        """Log when a plugin id from *source* overwrites an already-registered one.

        Precedence is defined by load order in ``start()``: builtin < entry
        point < user directory — later sources win, and this makes the
        override visible instead of silently swapping plugins. *source* is
        one of the canonical tokens (``builtin``/``entrypoint``/``dir``);
        mapped to a human label here so the log wording doesn't change.
        """
        if plugin_id in self._plugins:
            logger.info(
                "'%s' from %s overrides a previously loaded plugin with the same id",
                plugin_id,
                self._SOURCE_LABELS.get(source, source),
            )

    def _register_config_gated_plugin(self, plugin_class: type[Plugin], source: str) -> bool:
        """Instantiate, configure, and register *plugin_class* if enabled in config.

        Shared by built-ins and entry points, which both gate on
        ``config.plugins.builtin.<id>.enabled`` and resolve settings through
        the same env overlay. Returns True if the plugin was registered.
        """
        plugin_id = plugin_class.manifest.id
        entry = self.config.plugins.builtin.get(plugin_id)
        if not entry or not entry.enabled:
            return False
        instance = plugin_class()
        settings = resolve_plugin_env(
            plugin_id,
            plugin_class.manifest.config_schema,
            entry.settings,
        )
        settings, errors = validate_plugin_config(
            plugin_id, plugin_class.manifest.config_schema, settings
        )
        self._warn_on_collision(plugin_id, source=source)
        if errors and self._demo:
            # Required fields (API tokens, URLs, ...) are irrelevant in demo
            # mode since collect() never runs — log and register anyway so
            # the card renders demo_data() instead of a config-error card.
            logger.debug(
                "%s: ignoring config errors in demo mode: %s", plugin_id, "; ".join(errors)
            )
            self._disabled_ids.discard(plugin_id)
            self._latest_data.pop(plugin_id, None)
            self._config_errors.pop(plugin_id, None)
        elif errors:
            self._disabled_ids.add(plugin_id)
            self._latest_data[plugin_id] = PanelData(
                status="disabled",
                summary=f"Config error: {'; '.join(errors)}",
                detail={"errors": errors},
            )
            self._config_errors[plugin_id] = errors
        else:
            # A later source (e.g. entry point) can override an earlier one
            # (e.g. builtin) that was disabled — clear any stale disabled
            # state so the overriding plugin isn't silently skipped.
            self._disabled_ids.discard(plugin_id)
            self._latest_data.pop(plugin_id, None)
            self._config_errors.pop(plugin_id, None)
        instance.configure(settings)
        self._plugins[plugin_id] = instance
        self._plugin_sources[plugin_id] = source
        return True

    @staticmethod
    def _iter_builtin_classes():
        """Yield each Plugin subclass found in buoy.plugins.builtin, import errors isolated."""
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
                plugin_class = PluginManager._find_plugin_class(module)
                if plugin_class is not None:
                    yield plugin_class
            except Exception as e:
                logger.warning("Failed to load builtin '%s': %s", module_path, e, exc_info=True)

    @staticmethod
    def _iter_entrypoint_classes():
        """Yield each Plugin subclass registered under the buoy.plugins entry-point group."""
        try:
            eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
        except Exception as e:
            logger.warning(
                "Failed to enumerate '%s' entry points: %s", ENTRY_POINT_GROUP, e, exc_info=True
            )
            return

        for ep in eps:
            try:
                obj = ep.load()
                if inspect.isclass(obj) and issubclass(obj, Plugin):
                    plugin_class = obj
                else:
                    plugin_class = PluginManager._find_plugin_class(obj)
                if plugin_class is None:
                    logger.warning("Entry point '%s' did not resolve to a Plugin subclass", ep.name)
                    continue
                yield plugin_class
            except Exception as e:
                logger.warning("Failed to load entry point '%s': %s", ep.name, e, exc_info=True)

    @staticmethod
    def _iter_dir_classes(plugin_dir: Path):
        """Yield each Plugin subclass found in *.py files under plugin_dir."""
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

                plugin_class = PluginManager._find_plugin_class(module)
                if plugin_class is not None:
                    yield plugin_class
            except Exception as e:
                logger.warning(
                    "Failed to load user plugin '%s': %s", py_file.name, e, exc_info=True
                )

    @classmethod
    def discover_all(cls, config: BuoyConfig) -> list[dict[str, Any]]:
        """Enumerate every discoverable plugin without starting collection loops.

        Scans the same three sources as ``start()`` (builtin, entry point,
        user directory) but only imports/instantiates classes long enough to
        read their manifest — no ``setup()``/``collect()`` is called. Used by
        the ``buoy plugin list``/``info`` CLI, which needs to inspect
        available plugins without booting the async plugin lifecycle.

        Returns a list of dicts (one per unique plugin id, later sources win
        on collision, mirroring ``start()``'s builtin < entrypoint < dir
        precedence): id, name, icon, description, version, config_schema,
        refresh_interval, refresh_interval_override, source ("builtin" |
        "entrypoint" | "dir"), enabled.
        """
        seen: dict[str, dict[str, Any]] = {}

        def _record(plugin_class: type[Plugin], source: str) -> None:
            plugin_id = plugin_class.manifest.id
            if source == "dir":
                entry = config.plugins.user.get(plugin_id)
                enabled = entry.enabled if entry else True
            else:
                entry = config.plugins.builtin.get(plugin_id)
                enabled = bool(entry and entry.enabled)
            override = entry.refresh_interval if entry else None
            if plugin_id in seen:
                logger.info(
                    "'%s' from %s overrides a previously discovered plugin with the same id",
                    plugin_id,
                    source,
                )
            manifest = plugin_class.manifest
            seen[plugin_id] = {
                "id": plugin_id,
                "name": manifest.name,
                "icon": manifest.icon,
                "description": manifest.description,
                "version": manifest.version,
                "config_schema": manifest.config_schema,
                "refresh_interval": manifest.refresh_interval,
                "refresh_interval_override": override,
                "source": source,
                "enabled": enabled,
            }

        for plugin_class in cls._iter_builtin_classes():
            _record(plugin_class, "builtin")
        for plugin_class in cls._iter_entrypoint_classes():
            _record(plugin_class, "entrypoint")
        for plugin_class in cls._iter_dir_classes(Path(config.plugins.directory)):
            _record(plugin_class, "dir")

        return list(seen.values())

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

        The base interval is the plugin's manifest interval, unless an
        operator has set a per-plugin ``refresh_interval`` override under
        ``plugins.builtin.<id>`` or ``plugins.user.<id>`` in config, in which
        case that override wins. The greater of that base and the global
        ``refresh.plugins_interval`` config value is then used. This lets the
        global value act as a floor that slows down collection (its documented
        purpose) without ever shortening intentionally long intervals such as
        the github plugin (300 s) or the prometheus_exporter sentinel (9999 s).
        """
        entry = self.config.plugins.user.get(plugin.manifest.id)
        if entry is None:
            entry = self.config.plugins.builtin.get(plugin.manifest.id)
        base = (
            entry.refresh_interval
            if entry and entry.refresh_interval is not None
            else plugin.manifest.refresh_interval
        )
        return max(base, self.config.refresh.plugins_interval)

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
        """Collect from a plugin, catching all exceptions.

        Marks *plugin_id* as in-flight for the duration of the call so a
        concurrent ``collect_once()`` (manual refresh-now) can report "busy"
        instead of racing this collect. The ``finally`` also covers
        ``asyncio.CancelledError`` when ``stop()`` cancels the owning task
        mid-collect.
        """
        attempt_at = time.time()
        started = time.perf_counter()
        self._collecting.add(plugin_id)
        try:
            collect_task = asyncio.create_task(plugin.collect())
            try:
                data = await asyncio.wait_for(collect_task, timeout=COLLECT_TIMEOUT)
                duration_ms = round((time.perf_counter() - started) * 1000)
                self._latest_data[plugin_id] = data
                self._record_success(plugin_id, attempt_at=attempt_at, duration_ms=duration_ms)
            except TimeoutError:
                collect_task.cancel()
                await asyncio.gather(collect_task, return_exceptions=True)
                duration_ms = round((time.perf_counter() - started) * 1000)
                self._latest_data[plugin_id] = PanelData(
                    status="error", summary="Timeout", detail={"error": "collect timed out"}
                )
                self._record_failure(
                    plugin_id,
                    "collect timed out",
                    attempt_at=attempt_at,
                    duration_ms=duration_ms,
                )
                logger.debug("%s collect() timed out", plugin_id)
            except Exception as e:
                duration_ms = round((time.perf_counter() - started) * 1000)
                self._latest_data[plugin_id] = PanelData(
                    status="error", summary="Error", detail={"error": str(e)}
                )
                self._record_failure(
                    plugin_id, str(e), attempt_at=attempt_at, duration_ms=duration_ms
                )
                logger.debug("%s collect() failed: %s", plugin_id, e, exc_info=True)
        finally:
            self._collecting.discard(plugin_id)

    async def collect_once(self, plugin_id: str) -> str:
        """Run one out-of-band collect for *plugin_id*, outside the scheduled loop.

        Returns "ok" | "unknown" | "disabled" | "demo" | "busy". The
        ``plugin_id in self._collecting`` check below and the add to that set
        at the top of ``_safe_collect`` have no ``await`` between them, so a
        concurrent call can't slip in and start a second collect in this
        single-threaded event loop.
        """
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            return "unknown"
        if plugin_id in self._disabled_ids:
            return "disabled"
        if self._demo:
            return "demo"
        if plugin_id in self._collecting:
            return "busy"
        await self._safe_collect(plugin_id, plugin)
        return "ok"

    def _record_success(
        self,
        plugin_id: str,
        *,
        attempt_at: float | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self._health[plugin_id] = {
            "last_collect_at": time.time(),
            "last_attempt_at": attempt_at if attempt_at is not None else time.time(),
            "last_collect_duration_ms": duration_ms,
            "last_error": None,
            "consecutive_failures": 0,
        }

    def _record_failure(
        self,
        plugin_id: str,
        error: str,
        *,
        attempt_at: float | None = None,
        duration_ms: int | None = None,
    ) -> None:
        health = self._health.setdefault(
            plugin_id,
            {
                "last_collect_at": None,
                "last_attempt_at": None,
                "last_collect_duration_ms": None,
                "last_error": None,
                "consecutive_failures": 0,
            },
        )
        health["last_attempt_at"] = attempt_at if attempt_at is not None else time.time()
        health["last_collect_duration_ms"] = duration_ms
        health["last_error"] = error
        health["consecutive_failures"] += 1
