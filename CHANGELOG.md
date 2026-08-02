# Changelog

All notable changes to Buoy are documented here.

## Unreleased

### Added
- Per-peer latency history sparkline in fleet view (#44)
- Favicon set for buoy dashboard (#20)
- WebSocket reconnect status banner (#46)
- Keyboard shortcuts for panel navigation (#47)
- Improved mobile responsive breakpoints (#48)
- 24h uptime history sparkline per container (#49)
- systemd_health built-in plugin for service health monitoring (#50)
- System journal error count gauge (#45)
- Internet speedtest tracker plugin (#51)
- SnapRAID parity status plugin (#53)
- Tailscale network status plugin (#54)
- Immich photo library stats plugin (#56)
- Jellyfin media server status plugin (#57)
- Proxmox VE status plugin (#58)
- WireGuard tunnel status plugin (#59)
- Portainer remote container stats plugin (#60)
- Generic SMART disk health plugin (SATA + NVMe) (#61)
- Actual Budget monthly summary plugin (#62)
- Docker image update checker with per-container badges (#63)
- Auto-discover built-in plugins via pkgutil (#64)
- dns_filter plugin for Pi-hole / AdGuard Home (#65)
- Trigger.dev task run status plugin (#66)
- TLS certificate expiry tracking (cert_expiry plugin) (#69)
- `--dev` flag for hot-reload and debug logging (#68)
- Auth-protected `/api/config/debug` endpoint (#67)
- Env-based plugin secrets (`BUOY_PLUGIN_<ID>_<KEY>`) (#72)
- Cross-node alert forwarding in fleet view (#74)
- Theme: persist toggle, `prefers-color-scheme` detection, and new presets (#216)

### Fixed
- `Plugin.config` moved to per-instance `__init__` to prevent shared state (#207)
- `theme.custom` CSS variable overrides now applied at page load (#150)
- Rate limiting now always active, independent of `auth.enabled` (#213)
- Doubled `%` on container CPU stat in detail panel (#149)
- Auth now fails closed when enabled without credentials (SEC-1) (#214)
- Escaped untrusted strings before `innerHTML` (SEC-3, stored XSS) (#212)
- Same-origin CORS by default; opt-in allowlist for fleet peers (#211)
- `/api/config/debug` gated independently of `auth.enabled` (#208)
- Prometheus collector: escape label values and use exact uptime from `/proc/uptime` (#209)
- Plugin refresh: honour `refresh.plugins_interval` as server-side collection floor (#210)
- `speedtest-cli` made an optional dependency (#151)
- Docker: cache container list and reuse collector across `/api/stats` (#220)
- Frontend: correct `formatUptime` boundary and extract shared util (#218)
- Alerts: webhook dispatch reads URL from `config.alerts` not `plugins.builtin` (#217)

## [2.1.0] - 2026-06-27

### Added
- Container detail panel with metadata, logs, and restart action (#1)
- Deploy info footer showing version, build date, and git SHA (#8)
- Tailscale ping for peer latency measurement with HTTP fallback (#15)
- Fleet node cards now show service link pills (#16)

## [2.0.3] - 2026-06-27

### Fixed
- Operator precedence bug in `loadPluginJS` breaking custom plugin renderers (fixed in two passes)

## [2.0.2] - 2026-06-26

### Fixed
- NVMe SMART collection now uses `nsenter -t 1 -m` to access host smartctl from within containers (#2, PR #12)
- Removed `os.path.exists('/dev/nvme0n1')` guard that always blocked NVMe collection in Docker
- Health badge in gauges.js reflects actual wear level (Healthy / Warning ≥70% / Critical ≥90%)
- Added `.health-badge.crit` CSS style for critical NVMe state
- Static directory path resolution for Docker installs (`/app/static` fallback)

### Changed
- Design spec added as reference document for open issues #1–#11

## [2.0.1] - 2026-06-25

### Fixed
- Dockerfile build order: copy `src/` before `pip install` (hatchling needs `__init__.py` for version)
- Include `README.md` in Docker build context for hatchling metadata
- Resolved CI lint failures (E402, long lines, ruff format)

### Changed
- CI: build only linux/amd64 in CI for speed; multi-arch reserved for releases
- Release workflow: trigger on `v*` tags with semver Docker tags
- Documentation moved to GitHub wiki; roadmap removed from README
- Ruff config: ignore E501 (inline JS) and F823 (module globals)

## [2.0.0] - 2026-06-25

Initial public release. Complete rewrite from the internal "hub" dashboard.

### Added

**Core:**
- Starlette async server with WebSocket support
- Single `buoy.yaml` config file with environment variable overlay
- Multi-arch Docker image (amd64 + arm64)
- Demo mode (`--demo`) for zero-infrastructure evaluation

**Collectors:**
- System: CPU, memory, temperature, uptime, device model
- Docker: container discovery, stats, inspect, logs, restart
- Disk: mount info (with nsenter for containers), NVMe SMART data
- Network: fleet peer polling via httpx

**Frontend:**
- Modular vanilla JS (ES modules, no build step)
- Terminal dark theme + light theme (CSS custom properties)
- Expandable detail panels (CPU breakdown, memory, disk mounts, containers)
- Sparklines for temperature and disk trends
- Night mode (auto/always/never)
- Keyboard shortcuts (1-4 for panels, Escape to close)
- Responsive layout (desktop, tablet, mobile)
- Accessibility: semantic HTML, ARIA labels, keyboard navigation

**Plugin System:**
- Python plugin protocol (base class, manifest, PanelData)
- Plugin loader with auto-discovery (builtin + user directory)
- Per-plugin refresh intervals with error isolation
- Custom frontend JS injection for rich plugin UIs
- 7 built-in plugins:
  - GitHub (notifications + open PRs)
  - UptimeKuma (service health badges)
  - Loki (recent error logs)
  - Plane (sprint/cycle progress)
  - Backup Status (backup freshness + health)
  - Cron Health (recent cron job runs)
  - Prometheus Exporter (`/metrics` endpoint)

**Security:**
- Optional token/basic auth for destructive endpoints
- Rate limiting (60 req/min per IP on protected paths)
- Container name validation (prevents injection)
- Security headers (X-Content-Type-Options, X-Frame-Options, Referrer-Policy)

**History + Alerts:**
- SQLite ring buffer (24h retention, auto-prune, WAL mode)
- History API (`/api/history/{metric}?period=1h|6h|24h`)
- Alert engine with duration-aware threshold detection
- WebSocket push notifications (toast UI)
- Optional webhook dispatch (Discord/Slack/generic)

**Service Discovery:**
- Auto-discovers running Docker containers
- Tailscale-aware URL generation (HTTPS when accessed via .ts.net)
- Configurable hidden list + display overrides

**Documentation:**
- Full configuration reference
- Plugin development guide
- Deployment patterns (single node, fleet, reverse proxy)
- Contributing guide
