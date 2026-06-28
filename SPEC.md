# Buoy — Full Rebuild Spec

> **Project:** Open-source homelab node dashboard  
> **Name:** **Buoy**  
> **Repo:** `gfargo/buoy`  
> **Status:** Planning  
> **Date:** 2026-06-24  

## 1. Vision

A lightweight, configurable, per-node system dashboard for homelabs and small infrastructure. Deploy one container per host — it auto-discovers local Docker services, shows system vitals, connects to peer nodes for fleet overview, and integrates with common self-hosted tools. Zero dependencies beyond Docker.

**Target audience:** Homelab enthusiasts, self-hosters, small-team infrastructure operators.

**Tagline:** *A floating indicator of your infrastructure's health.*

**Design principles:**
- Single container, zero external dependencies (no database, no build step for users)
- Beautiful by default (dark terminal aesthetic), customizable via config
- Works standalone on one node, shines across a fleet
- Progressive disclosure: simple at first glance, deep on drill-down
- Plugin-friendly: extend without forking

---

## 2. Architecture

### 2.1 High-Level

```
┌─────────────────────────────────────────────────────┐
│  Browser (index.html)                                │
│  ┌─────────┐ ┌──────────┐ ┌────────┐ ┌──────────┐  │
│  │ Gauges  │ │ Services │ │ Fleet  │ │ Plugins  │  │
│  └────┬────┘ └────┬─────┘ └───┬────┘ └────┬─────┘  │
│       └───────────┴────────────┴───────────┘        │
│                        │ HTTP/WS                     │
└────────────────────────┼────────────────────────────┘
                         │
┌────────────────────────┼────────────────────────────┐
│  Hub Server (Python)   │                             │
│  ┌─────────┐ ┌────────┴───────┐ ┌───────────────┐  │
│  │  Config │ │  API Router    │ │  Plugin Host  │  │
│  │  Loader │ │  /api/stats    │ │  (dynamic)    │  │
│  └────┬────┘ │  /api/services │ └───────┬───────┘  │
│       │      │  /api/fleet    │         │           │
│       │      │  /ws           │         │           │
│       │      └────────────────┘         │           │
│  ┌────┴──────────────────────────────────┴───────┐  │
│  │          Collectors (async)                    │  │
│  │  system │ docker │ nvme │ network │ plugins   │  │
│  └───────────────────────────────────────────────┘  │
│       │                                              │
│  ┌────┴──────────────────────────────────────────┐  │
│  │  Optional: SQLite ring buffer (24h history)   │  │
│  └───────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
         │              │              │
    Docker Socket   /proc, /sys    Peer Nodes
```

### 2.2 Technology Choices

| Layer | Current (v1) | v2 Choice | Rationale |
|-------|-------------|-----------|-----------|
| Server | `http.server` (stdlib) | **Starlette + uvicorn** | Async, WebSocket native, middleware, lightweight (~3MB) |
| Frontend | Single `index.html` (1080 lines) | **Separate HTML/CSS/JS** (no build step) | Maintainable, cacheable, contributor-friendly |
| Config | env vars + services.json | **Single `hub.yaml`** + env override | One file to understand, schema-validated |
| Metrics | bash scripts (`stats.sh`) | **Python collectors** (with bash fallback) | Cross-platform, testable, no shell injection surface |
| Real-time | HTTP polling (5s) | **WebSocket** + HTTP fallback | Efficient, instant updates |
| Storage | None (in-memory sparklines) | **Optional SQLite** ring buffer | 24h trends without external DB |
| Plugins | N/A | **Python plugin protocol** | Drop-in `.py` files or pip packages |
| Auth | None | **Optional token/basic auth** | Secure by default for destructive ops |

### 2.3 File Structure (v2)

```
buoy/
├── buoy.yaml.example         # Reference config (fully commented)
├── Dockerfile
├── docker-compose.yml        # One-command start for users
├── pyproject.toml            # Package metadata + deps
├── LICENSE                   # MIT
├── README.md                 # Public-facing, with screenshots
├── CONTRIBUTING.md
├── CHANGELOG.md
├── src/
│   └── buoy/
│       ├── __init__.py
│       ├── __main__.py       # Entry point
│       ├── server.py         # Starlette app, routes, WebSocket
│       ├── config.py         # YAML loader + validation + defaults
│       ├── auth.py           # Optional auth middleware
│       ├── collectors/
│       │   ├── __init__.py
│       │   ├── system.py     # CPU, memory, temp, uptime
│       │   ├── docker.py     # Container discovery, stats, logs
│       │   ├── disk.py       # Mounts, NVMe SMART, I/O
│       │   ├── network.py    # Peer latency, fleet polling
│       │   └── certs.py      # TLS cert expiry
│       ├── plugins/
│       │   ├── __init__.py
│       │   ├── loader.py     # Plugin discovery + lifecycle
│       │   ├── protocol.py   # Base class / interface
│       │   └── builtin/      # Shipped plugins (GitHub, UptimeKuma, etc.)
│       │       ├── github.py
│       │       ├── uptime_kuma.py
│       │       ├── loki.py
│       │       ├── plane.py
│       │       ├── actual_budget.py
│       │       └── prometheus_exporter.py
│       ├── storage.py        # Optional SQLite ring buffer
│       └── demo.py           # Mock data generator for demo mode
├── static/
│   ├── index.html            # Shell HTML only
│   ├── css/
│   │   ├── buoy.css          # Core styles
│   │   └── themes/
│   │       ├── terminal.css  # Default (current dark theme)
│   │       └── light.css     # Light alternative
│   └── js/
│       ├── buoy.js           # Main app logic
│       ├── gauges.js         # Gauge rendering + sparklines
│       ├── fleet.js          # Fleet/network panel
│       ├── services.js       # Service cards
│       ├── detail.js         # Expandable detail panels
│       ├── plugins.js        # Plugin panel renderer
│       └── ws.js             # WebSocket client with reconnect
├── plugins/                  # User plugin directory (mounted volume)
│   └── example_plugin.py
├── tests/
│   ├── test_config.py
│   ├── test_collectors.py
│   ├── test_plugins.py
│   └── test_auth.py
└── docs/
    ├── configuration.md
    ├── plugins.md
    ├── deployment.md
    └── screenshots/
```

---

## 3. Configuration System

### 3.1 `hub.yaml` Schema

```yaml
# buoy.yaml — single config file for the buoy dashboard
# All fields have sensible defaults. Minimal config is just `node.name`.

node:
  name: compass                    # Required: node hostname/display name
  tier: "Tier 1B"                  # Optional: label shown in header
  role: "Project Management"       # Optional: description

network:
  tailnet_domain: tailb82ead.ts.net  # If set, enables Tailscale-aware URLs
  listen_port: 8090
  # Fleet peers — other hub instances to poll for fleet overview
  peers:
    - name: harbor
      url: https://harbor.tailb82ead.ts.net
      tier: "Tier 1A"
    - name: watch
      url: https://watch.tailb82ead.ts.net
      tier: "Tier 2"

services:
  # Containers to hide from the dashboard
  hidden:
    - "plane-plane-db-1"
    - "plane-plane-redis-1"
  # Display overrides for discovered containers
  overrides:
    grafana:
      name: Grafana
      icon: "\U0001F4CA"
      desc: "Dashboards & Logs"
      port: 3000
    octoprint:
      name: OctoPrint
      icon: "\U0001F5A8"
      desc: "3D Printer Management"
      port: 5050

theme:
  preset: terminal          # terminal | light | custom
  # Or provide custom CSS variables:
  # custom:
  #   bg: "#0a0c0f"
  #   surface: "#12151a"
  #   accent: "#e8a838"

auth:
  enabled: false
  # When enabled, protects destructive APIs (restart, logs)
  # Read-only APIs (stats, services) remain public
  type: token              # token | basic
  token: ""               # Set via HUB_AUTH_TOKEN env var (preferred)
  # basic:
  #   username: admin
  #   password: ""        # Set via HUB_AUTH_PASSWORD env var

features:
  websocket: true          # Real-time updates via WebSocket
  history: false           # SQLite ring buffer for 24h trends
  demo_mode: false         # Serve mock data (for screenshots/testing)
  night_mode: auto         # auto | always | never
  keyboard_shortcuts: true

refresh:
  stats_interval: 5        # seconds
  services_interval: 30
  fleet_interval: 15
  plugins_interval: 60

plugins:
  enabled: true
  directory: /plugins      # Mount point for user plugins
  builtin:
    github:
      enabled: false
      token: ""            # Or HUB_GITHUB_TOKEN env var
    uptime_kuma:
      enabled: false
      url: ""
    loki:
      enabled: false
      url: ""
    plane:
      enabled: false
      api_key: ""
      url: ""
      workspace: ""
      project: ""
    actual_budget:
      enabled: false
      url: ""
```

### 3.2 Config Resolution Order

1. `buoy.yaml` (or path in `BUOY_CONFIG` env var)
2. Environment variables override any YAML value (prefix: `BUOY_`)
3. CLI flags override everything (for one-off testing)

**Env var mapping:** `BUOY_NODE_NAME=compass`, `BUOY_NETWORK_LISTEN_PORT=9090`, `BUOY_AUTH_TOKEN=secret123`

### 3.3 Validation

On startup, the config is validated against a JSON Schema. Unknown keys warn but don't fail (forward compatibility). Missing required fields (`node.name`) cause a clear error message with the fix.

### 3.4 Minimal vs Full Config

**Minimal (just get it running):**
```yaml
node:
  name: myserver
```

Everything else uses sensible defaults: port 8090, no auth, no plugins, no fleet peers, terminal theme.

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

services:
  hidden: ["redis", "postgres"]
  overrides:
    grafana:
      name: Grafana
      icon: "\U0001F4CA"
      port: 3000

plugins:
  builtin:
    github:
      enabled: true
```

---

## 4. Plugin System

### 4.1 Plugin Protocol

Plugins are Python files (or packages) that implement a simple interface:

```python
from buoy.plugins.protocol import Plugin, PluginManifest, PanelData

class GitHubPlugin(Plugin):
    """Shows GitHub notifications and open PRs."""

    manifest = PluginManifest(
        id="github",
        name="GitHub",
        icon="🐙",
        description="Notifications & open PRs",
        version="1.0.0",
        config_schema={
            "token": {"type": "string", "required": True, "env": "BUOY_GITHUB_TOKEN"},
        },
        refresh_interval=300,  # seconds
    )

    async def collect(self) -> PanelData:
        """Called on each refresh cycle. Returns data for the frontend."""
        # self.config has validated plugin config
        token = self.config["token"]
        # ... fetch data ...
        return PanelData(
            status="ok",  # ok | warn | error | disabled
            summary="3 notifications",  # Shown in compact view
            detail={  # Passed to frontend renderer
                "notifications": [...],
                "open_prs": [...],
            }
        )

    def frontend_js(self) -> str | None:
        """Optional: return JS that renders this plugin's panel.
        If None, uses the default key-value renderer."""
        return """
        function renderGithub(data) {
          // Custom rendering logic
          return '<div class="gh-card">...</div>';
        }
        """
```

### 4.2 Plugin Lifecycle

1. **Discovery:** On startup, scan `plugins/` directory + `buoy.plugins.builtin` package
2. **Validation:** Check manifest, validate config against schema
3. **Init:** Call `plugin.setup()` (async) — for connection pooling, auth checks
4. **Collect loop:** Every `refresh_interval` seconds, call `plugin.collect()`
5. **Shutdown:** Call `plugin.teardown()` on graceful stop

### 4.3 Built-in Plugins (ship with hub)

| Plugin | What it does | Config needed |
|--------|-------------|---------------|
| `github` | Notifications + open PRs | `token` |
| `uptime_kuma` | Service health badges | `url` |
| `loki` | Recent error log entries | `url` |
| `plane` | Sprint/cycle progress bar | `api_key`, `url`, `workspace`, `project` |
| `actual_budget` | Monthly burn rate | `url`, `password` (pending) |
| `docker_updates` | Image freshness / available updates | (none — reads Docker socket) |
| `cert_expiry` | TLS certificate days remaining | (none — reads host certs) |
| `cron_health` | Recent cron job runs | (none — reads journald) |
| `backup_status` | Backup health + age | `backup_dir` |
| `immich` | Photo/video counts and storage usage | `url`, `api_key` |
| `prometheus_exporter` | Exposes `/metrics` in Prometheus text format | (none — re-uses collector data) |
| `systemd_health` | Systemd unit health via `systemctl is-active` | `units` (list of unit names) |

### 4.4 Frontend Plugin Rendering

Plugins can either:
- **Default renderer:** Plugin returns structured `PanelData` → hub renders a standard card with icon, summary, and key-value detail grid
- **Custom renderer:** Plugin provides a `frontend_js()` method → hub injects the JS and calls the render function with the plugin's data

This keeps the barrier low (no JS needed for simple plugins) while allowing rich custom UIs.

---

## 5. API Design

### 5.1 Core Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | No | Dashboard HTML |
| GET | `/api/config` | No | Public config subset (node name, theme, features, peer names) |
| GET | `/api/stats` | No | System vitals (CPU, RAM, disk, temp, containers, uptime) |
| GET | `/api/stats/detail` | No | Extended metrics (per-core, top processes, mount details) |
| GET | `/api/services` | No | Discovered local services + network links |
| GET | `/api/fleet` | No | Aggregated peer node stats |
| GET | `/api/plugins` | No | All plugin panel data |
| GET | `/api/plugins/{id}` | No | Single plugin data |
| GET | `/api/history/{metric}` | No | 24h time-series (if history enabled) |
| GET | `/api/health` | No | Health check with dependency status |
| WS | `/ws` | No | Real-time stats + plugin updates |

### 5.2 Protected Endpoints (require auth when enabled)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/container/{name}` | Container detail (inspect + resource usage) |
| GET | `/api/container/{name}/logs` | Last N lines of container stdout/stderr |
| POST | `/api/container/{name}/restart` | Restart a container |
| POST | `/api/container/{name}/stop` | Stop a container |

### 5.3 WebSocket Protocol

```jsonc
// Client → Server (subscribe to channels)
{ "type": "subscribe", "channels": ["stats", "fleet", "plugins"] }

// Server → Client (push updates)
{ "type": "stats", "data": { "cpu": 12, "mem_used": 4.2, ... } }
{ "type": "fleet", "data": { "peers": [...] } }
{ "type": "plugin", "id": "github", "data": { "summary": "3 notifications", ... } }

// Server → Client (alerts)
{ "type": "alert", "level": "warn", "message": "CPU > 90% for 5m", "metric": "cpu" }
```

### 5.4 Demo Mode

When `features.demo_mode: true`, all collectors return realistic mock data. The demo:
- Simulates 3 nodes with varying health
- Shows fake containers (nginx, postgres, redis, grafana)
- Plugins return sample data
- Stats fluctuate realistically over time (sinusoidal + noise)
- Container restart "works" (returns success after 2s delay)

This lets users evaluate the dashboard without any real infrastructure.

---

## 6. Frontend Design

### 6.1 Layout (unchanged conceptually, cleaner implementation)

```
┌──────────────────────────────────────────────────────┐
│  [hostname]  [tier badge]  [access badge]   up Xd Xh │  ← header
├──────────────────────────────────────────────────────┤
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐  │
│  │ CPU │ │ MEM │ │DISK │ │TEMP │ │CTRS │ │NVMe │  │  ← gauges (clickable)
│  └─────┘ └─────┘ └─────┘ └─────┘ └─────┘ └─────┘  │
├──────────────────────────────────────────────────────┤
│  [expandable detail panel — CPU/MEM/DISK/containers] │  ← detail (accordion)
├──────────────────────────────────────────────────────┤
│  This Node                                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐             │  ← service cards
│  │ Grafana  │ │  Plane   │ │ Joplin   │  ...        │
│  └──────────┘ └──────────┘ └──────────┘             │
├──────────────────────────────────────────────────────┤
│  Network                                              │
│  ┌───────────────────┐ ┌───────────────────┐         │  ← fleet nodes
│  │ harbor (1A) ● ... │ │ watch (2) ● ...   │         │
│  └───────────────────┘ └───────────────────┘         │
├──────────────────────────────────────────────────────┤
│  Plugins                                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐             │  ← plugin panels
│  │ GitHub   │ │  Sprint  │ │  Certs   │  ...        │
│  └──────────┘ └──────────┘ └──────────┘             │
├──────────────────────────────────────────────────────┤
│  [footer: model, build info, version]                 │
└──────────────────────────────────────────────────────┘
```

### 6.2 Frontend Architecture

**No build step.** Vanilla JS modules (`<script type="module">`), CSS variables for theming, semantic HTML. This means:
- Users can inspect/modify without toolchain knowledge
- No node_modules, no webpack, no transpilation
- Works in any modern browser (ES2020+)
- CSS custom properties make theming trivial

**Module breakdown:**
- `hub.js` — App initialization, config fetch, orchestrates modules
- `ws.js` — WebSocket connection with auto-reconnect + exponential backoff
- `gauges.js` — Renders gauge cards, sparklines, handles detail panel expansion
- `fleet.js` — Polls peer nodes, renders fleet grid
- `services.js` — Renders service cards with Tailscale-aware URLs
- `detail.js` — Expandable panels (CPU breakdown, memory detail, disk mounts, container list)
- `plugins.js` — Renders plugin panels (both default and custom renderers)
- `theme.js` — Theme switching, night mode auto-detection

### 6.3 Responsive Breakpoints

| Width | Layout |
|-------|--------|
| > 1200px | Full layout, 4-6 gauge columns |
| 768–1200px | 3-4 gauge columns, 2-col service grid |
| < 768px | 2-3 gauge columns, single-col services, collapsible sections |
| < 480px | Stacked layout, essential gauges only, swipe for more |

### 6.4 Accessibility

- Semantic HTML (header, main, section, nav)
- ARIA labels on interactive elements
- Keyboard navigable (Tab, Enter, Escape)
- Color contrast meets WCAG AA (dark theme verified)
- Reduced motion preference respected (`prefers-reduced-motion`)
- Screen reader announcements for alert state changes

---

## 7. Security Model

### 7.1 Threat Model

Hub is designed for **private networks** (home LAN, Tailscale, VPN). It is NOT designed to be internet-facing. The threat model assumes:
- Attacker has network access to the hub port
- Docker socket access = root equivalent (known Docker limitation)
- Container logs may contain secrets

### 7.2 Security Controls

| Control | Default | Notes |
|---------|---------|-------|
| Auth for read-only APIs | Disabled | Stats, services are informational |
| Auth for destructive APIs | Enabled when auth configured | Restart, stop, logs |
| Input sanitization | Always | Container names validated against `[a-zA-Z0-9_.-]` |
| Rate limiting | 60 req/min per IP | On destructive endpoints |
| CORS | Same-origin only | Configurable for fleet cross-node |
| CSP headers | Strict | No inline scripts in v2 (external JS files) |
| Docker socket | Read-only mount option | `docker.sock:/var/run/docker.sock:ro` in compose |

### 7.3 Auth Implementation

When `auth.enabled: true`:

```yaml
auth:
  enabled: true
  type: token
  token: ${HUB_AUTH_TOKEN}  # env var reference
```

- Protected endpoints require `Authorization: Bearer <token>` header
- The frontend stores the token in `localStorage` after initial prompt
- A simple login overlay appears if token is missing/invalid
- Read-only endpoints remain public (so fleet polling works without auth exchange)

### 7.4 Container Name Validation

```python
import re
CONTAINER_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')

def validate_container_name(name: str) -> bool:
    return bool(CONTAINER_NAME_RE.match(name)) and len(name) <= 128
```

All container operations validate the name before passing to `subprocess`.

---

## 8. Collectors (Metrics Engine)

### 8.1 System Collector

Replaces `stats.sh`. Pure Python, no shell:

```python
# Reads /proc/stat, /proc/meminfo, /proc/uptime, /sys/class/thermal/
# Falls back gracefully on non-Linux (returns zeros with a warning)
class SystemCollector:
    async def collect(self) -> SystemStats:
        return SystemStats(
            cpu_percent=read_cpu_percent(),
            memory=read_memory(),
            temperature=read_temperature(),
            uptime_seconds=read_uptime(),
            hostname=config.node.name,
            model=read_device_model(),
        )
```

**Cross-platform behavior:**
- Linux: full metrics from `/proc` and `/sys`
- macOS: partial metrics via `psutil`-like fallbacks (for local dev)
- Demo mode: synthetic data

### 8.2 Docker Collector

Replaces the Docker CLI subprocess calls. Options:
- **Option A:** Continue using Docker CLI (simpler, current approach)
- **Option B:** Use Docker Engine API directly via HTTP over unix socket

**Decision: Option A for v2.0** — CLI is simpler, well-tested, and the async subprocess overhead is negligible at 5s intervals. Can migrate to API later if needed.

Improvements over v1:
- Async subprocess calls (non-blocking)
- Container name validation before any Docker command
- Structured error handling (not bare try/except)
- Configurable timeout per operation
- Cache container list (5s TTL) to avoid redundant `docker ps` calls

### 8.3 Disk Collector

Replaces the `nsenter` + `df` pattern:

```python
class DiskCollector:
    async def collect(self) -> DiskStats:
        # If running in container with pid:host, use nsenter for host-level view
        # If running on host directly, use os.statvfs or shutil.disk_usage
        mounts = await get_mount_info()
        nvme = await get_nvme_smart() if has_nvme() else None
        io = await get_disk_io()
        return DiskStats(mounts=mounts, nvme=nvme, io=io)
```

### 8.4 Network Collector

Fleet polling + latency measurement:

```python
class NetworkCollector:
    async def collect(self) -> NetworkStats:
        peers = await asyncio.gather(*[
            poll_peer(peer) for peer in config.network.peers
        ])
        latency = await measure_latency(config.network.peers)
        return NetworkStats(peers=peers, latency=latency)

    async def poll_peer(self, peer: PeerConfig) -> PeerStatus:
        """Fetch /api/stats from a peer hub instance."""
        async with httpx.AsyncClient(timeout=4) as client:
            r = await client.get(f"{peer.url}/api/stats")
            return PeerStatus(name=peer.name, online=True, data=r.json())
```

### 8.5 Collector Scheduler

All collectors run on independent intervals via an async scheduler:

```python
class CollectorScheduler:
    def __init__(self, config):
        self.collectors = {
            "system": (SystemCollector(), config.refresh.stats_interval),
            "docker": (DockerCollector(), config.refresh.stats_interval),
            "disk": (DiskCollector(), config.refresh.stats_interval),
            "network": (NetworkCollector(), config.refresh.fleet_interval),
            "certs": (CertsCollector(), 3600),  # hourly
        }
        self.latest = {}  # Most recent result per collector
        self.history = RingBuffer(size=8640)  # 24h at 10s intervals

    async def run(self):
        """Start all collector loops."""
        tasks = [self._loop(name, coll, interval) 
                 for name, (coll, interval) in self.collectors.items()]
        await asyncio.gather(*tasks)
```

---

## 9. Storage (Optional)

### 9.1 SQLite Ring Buffer

When `features.history: true`, hub stores metrics in a local SQLite database for 24h trend display:

```sql
CREATE TABLE metrics (
    ts INTEGER NOT NULL,        -- Unix timestamp
    collector TEXT NOT NULL,     -- 'system', 'docker', etc.
    data TEXT NOT NULL           -- JSON blob
);
CREATE INDEX idx_metrics_ts ON metrics(ts);

-- Automatic cleanup: delete rows older than 24h on each insert batch
```

**Size estimate:** ~10KB per minute × 1440 min/day = ~14MB/day (compresses well). The ring buffer auto-prunes on each write cycle.

**Location:** `/data/hub.db` (mounted volume) or in-memory if no volume is mounted.

### 9.2 History API

```
GET /api/history/cpu?period=1h      → last hour of CPU data points
GET /api/history/memory?period=24h  → last 24h of memory data
GET /api/history/temp?period=6h     → temperature trend
```

Returns arrays of `[timestamp, value]` pairs suitable for sparkline rendering.

---

## 10. Docker & Deployment

### 10.1 Dockerfile (v2)

```dockerfile
FROM python:3.12-slim

# System deps for host introspection
RUN apt-get update && apt-get install -y --no-install-recommends \
    procps smartmontools iproute2 \
    && ARCH=$(uname -m) \
    && case "$ARCH" in \
         x86_64)  DOCKER_ARCH="x86_64" ;; \
         aarch64) DOCKER_ARCH="aarch64" ;; \
         *)       echo "Unsupported: $ARCH" && exit 1 ;; \
       esac \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${DOCKER_ARCH}/docker-27.5.1.tgz" \
    | tar xz --strip-components=1 -C /usr/local/bin docker/docker \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY src/ ./src/
COPY static/ ./static/
COPY buoy.yaml.example ./buoy.yaml.example

# Plugin directory
RUN mkdir -p /plugins /data

EXPOSE 8090
VOLUME ["/plugins", "/data"]

ENTRYPOINT ["python", "-m", "buoy"]
CMD ["--config", "/config/buoy.yaml"]
```

### 10.2 Docker Compose (user-facing)

```yaml
# docker-compose.yml — ready to use, just copy buoy.yaml.example → buoy.yaml
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
      - buoy-data:/data          # Optional: for 24h history
      - ./plugins:/plugins:ro    # Optional: custom plugins
    environment:
      - BUOY_AUTH_TOKEN=${BUOY_AUTH_TOKEN:-}
    # Required for full host metrics (temp, disk mounts, NVMe)
    privileged: true
    pid: host
    # Reduce privileges if you only need container stats:
    # privileged: false
    # pid: host  # still needed for nsenter

volumes:
  buoy-data:
```

### 10.3 Quick Start (README copy)

```bash
# 1. Get the config
curl -o buoy.yaml https://raw.githubusercontent.com/gfargo/buoy/main/buoy.yaml.example

# 2. Edit the config (at minimum, set node.name)
$EDITOR buoy.yaml

# 3. Run it
docker compose up -d

# 4. Open http://localhost:8090
```

### 10.4 Demo Mode (zero-config trial)

```bash
docker run --rm -p 8090:8090 ghcr.io/gfargo/buoy:latest --demo
```

No config, no Docker socket, no privileged mode. Shows a realistic dashboard with fake data.

---

## 11. Testing Strategy

### 11.1 Test Categories

| Category | Framework | Coverage Target |
|----------|-----------|-----------------|
| Unit tests | pytest | Config parsing, validation, collectors (mocked) |
| Integration tests | pytest + testcontainers | Docker collector against real containers |
| Frontend tests | Playwright (optional, later) | Critical paths: gauge render, detail expand |
| Smoke test | Shell script | `docker run`, hit `/api/health`, verify 200 |

### 11.2 Key Test Scenarios

**Config:**
- Minimal config (just `node.name`) → all defaults applied
- Full config → all values respected
- Invalid config → clear error message
- Env var override → takes precedence over YAML
- Missing file → generates default + warns

**Collectors:**
- `/proc` not available (macOS) → returns zeros, no crash
- Docker socket missing → container stats empty, everything else works
- Peer node offline → shown as offline, no error propagation
- NVMe not present → NVMe panel hidden

**Plugins:**
- Plugin with missing config → disabled with warning
- Plugin raises exception in collect → other plugins unaffected
- Plugin custom JS → injected correctly in page

**Security:**
- Container name with path traversal (`../../etc/passwd`) → rejected
- Auth required but not provided → 401
- Auth provided correctly → 200
- Rate limit exceeded → 429

### 11.3 CI Pipeline

```yaml
# .github/workflows/ci.yml
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e ".[dev]"
      - run: pytest --cov=hub --cov-report=term-missing
      - run: ruff check src/ tests/
      - run: ruff format --check src/ tests/

  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64,linux/arm64
          push: false  # Just verify it builds
```

---

## 12. Migration Path (v1 → v2)

### 12.1 For Current Setup (personal)

1. The v2 `hub.yaml` replaces `services.json` + `.env`
2. OLED pages stay in the repo but are clearly marked as separate (`oled/` directory with own README)
3. The infra repo's `stacks/hub/` continues to work — just swap the image tag and add `hub.yaml` mount
4. Fleet nodes update independently (v2 is backward-compatible with v1 `/api/stats` format)

### 12.2 For Open Source Users

- No migration needed — they start fresh with `hub.yaml.example`
- The README focuses on the quick-start path
- Advanced features (plugins, history, auth) are opt-in via config

### 12.3 Breaking Changes from v1

| What | v1 | v2 | Migration |
|------|----|----|-----------|
| Config | `.env` + `services.json` | `hub.yaml` | Manual translation (one-time) |
| API: services | `/api/services` returns flat list | Returns `{local, network, plugins}` | Frontend handles both during transition |
| Container detail | Hardcoded peer list in JS | Comes from `/api/config` | Automatic |
| Auth | None | Optional (off by default) | No action if not enabling |

---

## 13. Open Source Readiness Checklist

### 13.1 Repository Prep

- [ ] Remove all hardcoded personal data (tailnet domain, project IDs, GitHub username)
- [ ] Add MIT LICENSE
- [ ] Write public README with screenshots, quick-start, feature list
- [ ] Add CONTRIBUTING.md (dev setup, PR process, architecture overview)
- [ ] Add CHANGELOG.md (start from v2.0.0)
- [ ] Add CODE_OF_CONDUCT.md
- [ ] Set up GitHub issue templates (bug report, feature request)
- [ ] Set up GitHub Discussions for Q&A
- [ ] Create release workflow (tag → build → push to GHCR + create GitHub Release)

### 13.2 Documentation

- [ ] `docs/configuration.md` — full config reference with examples
- [ ] `docs/plugins.md` — plugin development guide with examples
- [ ] `docs/deployment.md` — deployment patterns (single node, fleet, with Caddy/Traefik)
- [ ] `docs/screenshots/` — high-quality screenshots of all states (normal, warn, dark, light, demo)

### 13.3 Branding / Naming

**Name:** `buoy`

Nautical theme — consistent with the platform's node naming convention (harbor, compass, watch). The name signals:
- A floating indicator of system status
- Something you check to see if conditions are safe
- Lightweight, visible, always present

**Repository:** `gfargo/buoy`  
**Package:** `buoy`  
**Docker image:** `ghcr.io/gfargo/buoy`  
**CLI:** `buoy` (or `python -m buoy`)

### 13.4 OLED Pages — Separate Project

The `oled-pages/` directory (Pironman5 OLED orchestrator v3) is **not part of buoy**. It is:
- Different hardware target (SSD1306 128×64 OLED on Pi 5 case)
- Different runtime (pironman5's Python venv, systemd service)
- Different deploy mechanism (SSH + sudo script)
- Zero shared code with the web dashboard

**Action:** Split to `gfargo/pironman5-oled` (or similar) before public launch. The buoy repo ships clean — web dashboard only.

### 13.5 GHCR Image

- **Image:** `ghcr.io/gfargo/buoy`
- Tags: `latest`, `v2.0.0`, short SHA

---

## 14. Implementation Phases

### Phase 1: Foundation (Target: working replacement of v1)

**Goal:** Feature parity with v1, but with clean architecture.

| Task | Description | Estimate |
|------|-------------|----------|
| 1.1 | Project scaffolding (pyproject.toml, src layout, static dir) | 1h |
| 1.2 | Config system (hub.yaml loader, validation, env override) | 3h |
| 1.3 | Starlette server with basic routing | 2h |
| 1.4 | System collector (Python, replaces stats.sh) | 2h |
| 1.5 | Docker collector (container list, stats, inspect) | 2h |
| 1.6 | Disk collector (mounts, NVMe SMART) | 1h |
| 1.7 | Service discovery (from Docker + config overrides) | 2h |
| 1.8 | Frontend split (HTML/CSS/JS separate files) | 3h |
| 1.9 | Frontend: gauges + detail panels | 3h |
| 1.10 | Frontend: services + fleet grid | 2h |
| 1.11 | Demo mode (mock data) | 2h |
| 1.12 | Dockerfile + docker-compose.yml | 1h |
| 1.13 | Basic tests (config, collectors) | 2h |
| **Total Phase 1** | | **~25h** |

### Phase 2: Real-time + Fleet

**Goal:** WebSocket, fleet polling, network features.

| Task | Description | Estimate |
|------|-------------|----------|
| 2.1 | WebSocket server (Starlette WebSocket route) | 2h |
| 2.2 | WebSocket client (JS with reconnect + backoff) | 2h |
| 2.3 | Network collector (peer polling via httpx) | 2h |
| 2.4 | Latency measurement (tailscale ping or HTTP timing) | 1h |
| 2.5 | Fleet grid frontend (live peer stats) | 2h |
| 2.6 | Cert expiry collector | 1h |
| **Total Phase 2** | | **~10h** |

### Phase 3: Plugins

**Goal:** Plugin system + migrate existing integrations.

| Task | Description | Estimate |
|------|-------------|----------|
| 3.1 | Plugin protocol (base class, manifest, PanelData) | 2h |
| 3.2 | Plugin loader (discovery, validation, lifecycle) | 3h |
| 3.3 | Plugin scheduler (independent refresh cycles) | 1h |
| 3.4 | Frontend plugin renderer (default + custom JS) | 3h |
| 3.5 | Built-in: GitHub plugin | 1h |
| 3.6 | Built-in: UptimeKuma plugin | 1h |
| 3.7 | Built-in: Loki plugin | 1h |
| 3.8 | Built-in: Plane/sprint plugin | 1h |
| 3.9 | Built-in: cert_expiry plugin | 0.5h |
| 3.10 | Built-in: cron_health plugin | 0.5h |
| 3.11 | Built-in: backup_status plugin | 0.5h |
| 3.12 | Plugin documentation + example plugin | 2h |
| **Total Phase 3** | | **~17h** |

### Phase 4: Security + Polish

**Goal:** Auth, rate limiting, accessibility, themes.

| Task | Description | Estimate |
|------|-------------|----------|
| 4.1 | Auth middleware (token + basic) | 2h |
| 4.2 | Input validation + rate limiting | 1h |
| 4.3 | Security headers (CSP, CORS, X-Frame-Options) | 1h |
| 4.4 | Light theme CSS | 1h |
| 4.5 | Theme switching (JS + config) | 1h |
| 4.6 | Accessibility audit + fixes | 2h |
| 4.7 | Mobile responsive improvements | 2h |
| 4.8 | Keyboard shortcuts (documented) | 0.5h |
| 4.9 | Night mode (auto/manual) | 0.5h |
| **Total Phase 4** | | **~11h** |

### Phase 5: History + Alerts

**Goal:** Optional persistence, trend charts, notifications.

| Task | Description | Estimate |
|------|-------------|----------|
| 5.1 | SQLite ring buffer implementation | 2h |
| 5.2 | History API endpoints | 1h |
| 5.3 | Frontend: mini trend charts (24h) | 3h |
| 5.4 | Alert engine (threshold detection) | 2h |
| 5.5 | Alert rendering (WebSocket push → UI toast) | 1h |
| 5.6 | Optional: webhook alerts (Discord, Slack, email) | 2h |
| **Total Phase 5** | | **~11h** |

### Phase 6: Open Source Launch

**Goal:** Public-ready repository.

| Task | Description | Estimate |
|------|-------------|----------|
| 6.1 | Final README with screenshots | 2h |
| 6.2 | CONTRIBUTING.md + issue templates | 1h |
| 6.3 | Full docs (config, plugins, deployment) | 3h |
| 6.4 | Release workflow (CI → GHCR + GitHub Release) | 1h |
| 6.5 | CHANGELOG.md (v2.0.0) | 0.5h |
| 6.6 | Demo hosted instance (optional — Railway/Fly.io) | 2h |
| 6.7 | Product Hunt / Reddit / HN launch prep | 1h |
| **Total Phase 6** | | **~10h** |

### Total Estimated Effort

| Phase | Hours |
|-------|-------|
| Phase 1: Foundation | ~25h |
| Phase 2: Real-time + Fleet | ~10h |
| Phase 3: Plugins | ~17h |
| Phase 4: Security + Polish | ~11h |
| Phase 5: History + Alerts | ~11h |
| Phase 6: Open Source Launch | ~10h |
| **Grand Total** | **~84h** |

---

## 15. Decisions Log

| # | Decision | Choice | Rationale | Date |
|---|----------|--------|-----------|------|
| 1 | Server framework | Starlette | Async, WebSocket native, lightweight, no magic | 2026-06-24 |
| 2 | Frontend approach | Vanilla JS modules (no build step) | Zero friction for contributors, no toolchain | 2026-06-24 |
| 3 | Config format | YAML with env override | Human-friendly, widely understood, Helm/k8s precedent | 2026-06-24 |
| 4 | Plugin approach | Python protocol class + optional custom JS | Low barrier, testable, type-safe | 2026-06-24 |
| 5 | Docker integration | CLI subprocess (not API socket) | Simpler, proven in v1, migrate later if needed | 2026-06-24 |
| 6 | Auth | Optional token-based, off by default | Homelabs are trusted networks; don't force complexity | 2026-06-24 |
| 7 | Naming | **Buoy** | Unique, searchable, nautical-consistent (harbor/compass/watch) | 2026-06-24 |
| 9 | OLED pages | Split to own repo (`gfargo/pironman5-oled`) | Unrelated runtime, hardware, deploy — zero shared code with web dashboard | 2026-06-24 |
| 10 | Prometheus export | Plugin (`prometheus_exporter`) | Perfect plugin use-case; zero overhead if unused; re-uses collector data | 2026-06-24 |
| 8 | History storage | SQLite, opt-in | Zero deps, embedded, auto-prunes, tiny footprint | 2026-06-24 |

---

## 16. Open Questions

1. ~~**Naming:** Should we rename to `buoy`?~~ **Resolved: Yes — `buoy`.**

2. ~~**OLED pages:** Keep or split?~~ **Resolved: Split to `gfargo/pironman5-oled`.**

3. **Python version floor:** 3.11 (for `tomllib` if we ever switch to TOML) or 3.12 (for better typing)? Docker base image is 3.12 either way.

4. **Frontend framework later?** Could migrate to Lit, Preact, or Svelte for the frontend if complexity grows. For v2.0, vanilla JS is correct. Revisit at v2.5+ if plugin custom UI gets complex.

5. **Multi-user?** Current design is single-user (one dashboard per node). Is there a use case for multi-tenant buoy? Probably not — keep it simple.

6. ~~**Metrics export?** Should buoy expose `/metrics`?~~ **Resolved: Yes, as a built-in plugin (`prometheus_exporter`).**

---

## Appendix A: Competitive Landscape

| Project | What it does | Gap hub fills |
|---------|-------------|---------------|
| **Homepage** (gethomepage.dev) | Service dashboard with widgets | No per-node system vitals, no fleet view |
| **Dashy** | Configurable dashboard | Heavy (Vue SPA), no system metrics |
| **Glances** | System monitoring | Single-node only, no service discovery, no fleet |
| **Netdata** | Full monitoring suite | Massive footprint, overkill for homelab overview |
| **Homarr** | App dashboard | No system metrics, no fleet concept |
| **Flame** | Bookmark dashboard | Static links only |

**Buoy's niche:** The intersection of system monitoring (Glances) + service dashboard (Homepage) + fleet overview (custom) — in a single <100MB container with zero external dependencies.
