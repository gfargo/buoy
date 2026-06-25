# Plugin Development Guide

Buoy's plugin system lets you add custom integrations without modifying core code. Plugins are Python files that implement a simple async interface.

## Quick Example

```python
from buoy.plugins.protocol import Plugin, PluginManifest, PanelData

class WeatherPlugin(Plugin):
    manifest = PluginManifest(
        id="weather",
        name="Weather",
        icon="🌤️",
        description="Current weather conditions",
        version="1.0.0",
        config_schema={"api_key": {"type": "string", "required": True}},
        refresh_interval=300,  # seconds
    )

    async def collect(self) -> PanelData:
        # Your data-fetching logic here
        return PanelData(
            status="ok",
            summary="72°F, Sunny",
            detail={"temp": 72, "condition": "Sunny", "humidity": 45},
        )
```

Save this as `weather.py` in your plugins directory and mount it into the container.

## Plugin Protocol

Every plugin must subclass `Plugin` and implement:

### Required

- **`manifest`** — a `PluginManifest` instance describing the plugin
- **`collect()`** — an async method that returns `PanelData`

### Optional

- **`setup()`** — async, called once on startup (connection pooling, auth checks)
- **`teardown()`** — async, called on shutdown (cleanup)
- **`frontend_js()`** — returns custom JavaScript for rendering the plugin panel
- **`configure(config)`** — called with the plugin's config dict from `buoy.yaml`

## PluginManifest

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Unique identifier (used in API + config) |
| `name` | str | Display name |
| `icon` | str | Emoji icon |
| `description` | str | Short description |
| `version` | str | Plugin version |
| `config_schema` | dict | Schema describing required/optional config fields |
| `refresh_interval` | int | How often `collect()` is called (seconds) |

## PanelData

| Field | Type | Description |
|-------|------|-------------|
| `status` | str | `"ok"`, `"warn"`, `"error"`, or `"disabled"` |
| `summary` | str | Short text shown in compact view |
| `detail` | dict | Arbitrary data passed to the frontend renderer |

## Custom Frontend Rendering

By default, plugins render as a card with icon, name, summary, and key-value pairs from `detail`. For richer UIs, implement `frontend_js()`:

```python
def frontend_js(self) -> str:
    return """
function render_weather(data) {
  return `<div style="font-size:2rem">${data.detail.temp}°F</div>
          <div style="color:var(--text-dim)">${data.detail.condition}</div>`;
}
"""
```

The function name must be `render_{manifest.id}`. It receives the full plugin data object and returns an HTML string.

## Configuration

Plugins receive their config via the `configure()` method. Config comes from `buoy.yaml`:

```yaml
plugins:
  builtin:
    weather:
      enabled: true
      api_key: "your-key-here"
```

Access config in your plugin:
```python
async def collect(self) -> PanelData:
    api_key = self.config.get("api_key", "")
    # ...
```

## User Plugins vs Built-in

| | User Plugins | Built-in Plugins |
|---|---|---|
| Location | `/plugins/*.py` (mounted volume) | `src/buoy/plugins/builtin/` |
| Config | Not yet supported | Via `plugins.builtin.<id>` in YAML |
| Lifecycle | Auto-discovered on startup | Loaded if `enabled: true` in config |
| Distribution | Drop-in file | Ships with buoy |

## Built-in Plugins

| Plugin | ID | What it shows |
|--------|----|---------------|
| GitHub | `github` | Notifications + open PRs |
| UptimeKuma | `uptime_kuma` | Service health badges |
| Loki | `loki` | Recent error log entries |
| Plane | `plane` | Sprint/cycle progress |
| Backup Status | `backup_status` | Backup health + freshness |
| Cron Health | `cron_health` | Recent cron job runs |
| Prometheus | `prometheus_exporter` | `/metrics` endpoint |

## Error Handling

Plugins are fully isolated:
- If `collect()` raises an exception, other plugins are unaffected
- If `collect()` takes longer than 30s, it times out
- Failed plugins show `status: "error"` in their panel
- Errors are logged to stdout

## Testing Plugins

```python
import pytest
from your_plugin import WeatherPlugin

@pytest.mark.asyncio
async def test_weather_collect():
    plugin = WeatherPlugin()
    plugin.configure({"api_key": "test-key"})
    await plugin.setup()
    
    data = await plugin.collect()
    assert data.status == "ok"
    assert "temp" in data.detail
```
