# 🔔 Buoy

A lightweight, per-node system dashboard for homelabs and small infrastructure.

![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue)
![License MIT](https://img.shields.io/badge/license-MIT-green)
![Arch amd64+arm64](https://img.shields.io/badge/arch-amd64%20%2B%20arm64-orange)
![Docker](https://img.shields.io/badge/docker-ghcr.io%2Fgfargo%2Fbuoy-blue)
![No Build Step](https://img.shields.io/badge/frontend-no%20build%20step-purple)

---

## What it does

Deploy one container per host. Buoy auto-discovers your Docker services, shows system vitals, and connects to peer nodes for a fleet overview — your tailnet landing page.

- **System vitals** — CPU, RAM, disk, temperature, NVMe health, container count
- **Service discovery** — auto-finds running Docker containers; customize with display overrides
- **Fleet overview** — poll peer Buoy instances for a multi-node dashboard
- **Tailscale-aware** — links auto-switch between HTTPS tailnet URLs and localhost
- **Plugin system** — extend with GitHub, UptimeKuma, Loki, Prometheus, or your own plugins
- **Beautiful by default** — dark terminal theme with sparklines, expandable detail panels
- **Zero external dependencies** — no database, no build step, just Docker

## Quick Start

```bash
# 1. Get the example config
curl -o buoy.yaml https://raw.githubusercontent.com/gfargo/buoy/main/buoy.yaml.example

# 2. Set your node name (minimum required config)
sed -i 's/my-server/your-hostname/' buoy.yaml

# 3. Run it
docker compose up -d

# 4. Open http://localhost:8090
```

## Demo Mode

Try it without any infrastructure — no Docker socket, no host access needed:

```bash
docker run --rm -p 8090:8090 ghcr.io/gfargo/buoy:latest --demo
```

## Configuration

Buoy is configured via a single `buoy.yaml` file. See [`buoy.yaml.example`](./buoy.yaml.example) for the full reference.

**Minimal config:**
```yaml
node:
  name: my-server
```

**Typical homelab:**
```yaml
node:
  name: compass
  tier: "Tier 1B"

network:
  tailnet_domain: example.ts.net
  peers:
    - name: harbor
      url: https://harbor.example.ts.net
      tier: "Tier 1A"

services:
  hidden: ["redis", "postgres"]
  overrides:
    grafana:
      name: Grafana
      icon: "📊"
      port: 3000
```

Environment variables override any YAML value (prefix: `BUOY_`):
```bash
BUOY_NODE_NAME=harbor
BUOY_AUTH_TOKEN=my-secret
BUOY_FEATURES_DEMO_MODE=true
```

## Architecture

```
Browser ←→ Starlette (async Python) ←→ Collectors (system/docker/disk/network)
                ↕                              ↕
           WebSocket                    Docker CLI / /proc / /sys
```

- **Backend:** Starlette + uvicorn (async, WebSocket-native)
- **Frontend:** Vanilla JS modules (no build step, no framework)
- **Collectors:** Python async, reading `/proc` and Docker CLI
- **Config:** Single YAML file with env var overlay
- **Plugins:** Python protocol class — drop in a `.py` file

## Docker Compose

```yaml
services:
  buoy:
    image: ghcr.io/gfargo/buoy:latest
    container_name: buoy
    restart: unless-stopped
    ports:
      - "8090:8090"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./buoy.yaml:/config/buoy.yaml:ro
      - buoy-data:/data
    privileged: true
    pid: host

volumes:
  buoy-data:
```

> **Note:** `privileged` + `pid: host` enables full system metrics (temperature, all disk mounts, NVMe SMART). If you only need container stats, you can drop `privileged` and keep just `pid: host`.

## Plugins

Buoy ships with built-in plugins (disabled by default):

| Plugin | What it shows | Config needed |
|--------|---------------|---------------|
| GitHub | Notifications + open PRs | `token` |
| UptimeKuma | Service health badges | `url` |
| Loki | Recent error log entries | `url` |
| Plane | Sprint/cycle progress | `api_key`, `url` |
| Prometheus | `/metrics` endpoint | (none) |

**Custom plugins** are Python files dropped into the `/plugins` volume:

```python
from buoy.plugins.protocol import Plugin, PluginManifest, PanelData

class WeatherPlugin(Plugin):
    manifest = PluginManifest(id="weather", name="Weather", icon="🌤️")

    async def collect(self) -> PanelData:
        # Your logic here
        return PanelData(status="ok", summary="72°F, Sunny")
```

## Development

```bash
# Clone and install
git clone https://github.com/gfargo/buoy.git
cd buoy
pip install -e ".[dev]"

# Run locally (demo mode — works on macOS/Linux without Docker)
python -m buoy --demo

# Run tests
pytest

# Lint
ruff check src/ tests/
```

## Documentation

- [Configuration Reference](docs/configuration.md) — full YAML config guide
- [Plugin Development](docs/plugins.md) — create custom plugins
- [Deployment Guide](docs/deployment.md) — single node, fleet, reverse proxy patterns
- [Changelog](CHANGELOG.md) — release history
- [Contributing](CONTRIBUTING.md) — dev setup, PR process

## Roadmap

- [x] Phase 1: Foundation (config, collectors, frontend split, demo mode)
- [x] Phase 2: Real-time (WebSocket push, fleet polling)
- [x] Phase 3: Plugins (plugin system, built-in integrations)
- [x] Phase 4: Security (auth, rate limiting, security headers)
- [x] Phase 5: History (SQLite trends, alert engine, notifications)
- [x] Phase 6: Documentation & public launch

## License

MIT — see [LICENSE](./LICENSE).
