# Configuration Reference

Buoy is configured via a single `buoy.yaml` file. All fields have sensible defaults — the minimum required config is just your node name.

## Config Resolution

1. **buoy.yaml** file (from `--config` flag, `BUOY_CONFIG` env, `./buoy.yaml`, or `/config/buoy.yaml`)
2. **Environment variables** override any YAML value (prefix: `BUOY_`)
3. **CLI flags** override everything (`--port`, `--demo`)

## Sections

### node

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `name` | string | `"buoy"` | Node hostname / display name (shown in header) |
| `tier` | string | `""` | Label shown as badge (e.g., "Tier 1A", "prod") |
| `role` | string | `""` | Node description (e.g., "Database Server") |

```yaml
node:
  name: compass
  tier: "Tier 1B"
  role: "Project Management"
```

### network

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `listen_port` | int | `8090` | Port buoy listens on |
| `tailnet_domain` | string | `""` | Tailscale domain for HTTPS URL generation |
| `peers` | list | `[]` | Other buoy instances for fleet overview |

Each peer has:
| Key | Type | Description |
|-----|------|-------------|
| `name` | string | Peer node name |
| `url` | string | Full URL to the peer's buoy instance |
| `tier` | string | Optional tier label |

```yaml
network:
  listen_port: 8090
  tailnet_domain: example.ts.net
  peers:
    - name: harbor
      url: https://harbor.example.ts.net
      tier: "Tier 1A"
```

### services

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `hidden` | list[str] | `[]` | Container names to exclude from dashboard |
| `overrides` | dict | `{}` | Display customization per container |

Each override has:
| Key | Type | Description |
|-----|------|-------------|
| `name` | string | Display name |
| `icon` | string | Emoji icon |
| `desc` | string | Short description |
| `port` | int | Caddy/external port for URL generation |
| `path` | string | URL path suffix |

```yaml
services:
  hidden:
    - "redis"
    - "my-app-db-1"
  overrides:
    grafana:
      name: Grafana
      icon: "📊"
      desc: "Dashboards & Logs"
      port: 3000
```

### theme

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `preset` | string | `"terminal"` | Theme: `terminal` (dark) or `light` |
| `custom` | dict | `{}` | CSS variable overrides |

```yaml
theme:
  preset: terminal
  # custom:
  #   bg: "#0a0c0f"
  #   accent: "#e8a838"
```

### auth

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `false` | Enable auth for destructive endpoints |
| `type` | string | `"token"` | Auth type: `token` or `basic` |
| `token` | string | `""` | Bearer token (prefer `BUOY_AUTH_TOKEN` env var) |
| `username` | string | `""` | Basic auth username |
| `password` | string | `""` | Basic auth password (prefer `BUOY_AUTH_PASSWORD` env) |

Protected endpoints (when auth enabled):
- `GET /api/container/{name}` — container detail
- `GET /api/container/{name}/logs` — container logs
- `POST /api/container/{name}/restart` — restart container

Read-only endpoints remain public (required for fleet polling).

### features

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `websocket` | bool | `true` | Real-time updates via WebSocket |
| `history` | bool | `false` | SQLite ring buffer for 24h trends |
| `demo_mode` | bool | `false` | Serve mock data (no Docker needed) |
| `night_mode` | string | `"auto"` | `auto` (10pm-6am), `always`, or `never` |
| `keyboard_shortcuts` | bool | `true` | 1-4 for gauges, Escape to close |

### refresh

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `stats_interval` | int | `5` | System vitals polling (seconds) |
| `services_interval` | int | `30` | Service discovery refresh (seconds) |
| `fleet_interval` | int | `15` | Peer node polling (seconds) |
| `plugins_interval` | int | `60` | Plugin data refresh (seconds) |

### plugins

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Master switch for plugin system |
| `directory` | string | `"/plugins"` | User plugin directory (mount point) |
| `builtin` | dict | `{}` | Built-in plugin configuration |

See [plugins.md](plugins.md) for details on each built-in plugin.

## Environment Variable Mapping

| Env Var | Overrides |
|---------|-----------|
| `BUOY_CONFIG` | Config file path |
| `BUOY_NODE_NAME` | `node.name` |
| `BUOY_NODE_TIER` | `node.tier` |
| `BUOY_NETWORK_LISTEN_PORT` | `network.listen_port` |
| `BUOY_NETWORK_TAILNET_DOMAIN` | `network.tailnet_domain` |
| `BUOY_AUTH_ENABLED` | `auth.enabled` |
| `BUOY_AUTH_TOKEN` | `auth.token` |
| `BUOY_AUTH_TYPE` | `auth.type` |
| `BUOY_THEME_PRESET` | `theme.preset` |
| `BUOY_FEATURES_DEMO_MODE` | `features.demo_mode` |
| `BUOY_FEATURES_WEBSOCKET` | `features.websocket` |
| `BUOY_FEATURES_HISTORY` | `features.history` |
