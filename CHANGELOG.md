# Changelog

All notable changes to Buoy are documented here.

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

## Unreleased

_Nothing yet._
