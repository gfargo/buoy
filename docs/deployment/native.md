# Native install (pip + systemd)

Run Buoy directly on the host instead of in a container. This is the
**least-privileged** way to get full metrics: a native process already has
real access to `/proc`, `/sys`, and (via group membership) the Docker
socket, so it needs none of the `privileged: true` / `pid: host` flags the
[Docker Compose](../../README.md#docker-compose) path requires. See the
[privilege matrix](./privilege-matrix.md) for the full breakdown.

> **PyPI status:** Buoy is not yet published to PyPI (tracked upstream).
> Install from source/git for now; switch to `pip install buoy` once a
> release is published.

## 1. Host prerequisites

Buoy's Python dependencies (Starlette, uvicorn, PyYAML, httpx) are installed
by pip automatically. A few metrics also shell out to host binaries — same
tools the [Dockerfile](../../Dockerfile) installs into the container image:

| Binary | Used for | Package (Debian/Ubuntu) |
|---|---|---|
| `docker` | Service discovery, container stats/logs/restart | `docker-ce-cli` or Docker Engine |
| `ps` | Top-processes list | `procps` |
| `smartctl` | NVMe/SATA SMART health | `smartmontools` |
| `ip` | (reserved for future network collectors) | `iproute2` |

All of these are optional — the corresponding collector degrades to an
empty/zero result rather than crashing if a binary is missing. Install what
you need; skip the rest.

```bash
sudo apt-get install -y procps smartmontools iproute2
# Docker Engine (for service discovery): https://docs.docker.com/engine/install/
```

Python 3.12+ is required (`requires-python = ">=3.12"` in `pyproject.toml`).

## 2. Install Buoy into a venv

```bash
sudo mkdir -p /opt/buoy
sudo python3 -m venv /opt/buoy/venv
sudo /opt/buoy/venv/bin/pip install git+https://github.com/gfargo/buoy.git
# Or, from a local checkout:
#   sudo /opt/buoy/venv/bin/pip install /path/to/buoy
```

This installs the `buoy` console script (`/opt/buoy/venv/bin/buoy`) along
with the packaged frontend assets — the wheel bundles `static/` into
`buoy/static` at build time (`pyproject.toml`'s
`[tool.hatch.build.targets.wheel.force-include]`), so no separate frontend
build step is needed. `tests/test_wheel_packaging.py` guards this in CI.

## 3. Config and data directories

```bash
sudo mkdir -p /etc/buoy /var/lib/buoy
sudo curl -o /etc/buoy/buoy.yaml \
  https://raw.githubusercontent.com/gfargo/buoy/main/buoy.yaml.example
sudo sed -i 's/my-server/your-hostname/' /etc/buoy/buoy.yaml
```

Buoy looks for a config file in this order: `--config` flag →
`$BUOY_CONFIG` env var → `./buoy.yaml` / `./buoy.yml` in the working
directory → `/config/buoy.yaml` / `/config/buoy.yml` (see
`src/buoy/config.py`). None of those defaults point at `/etc/buoy`, so the
[systemd unit](../../deploy/systemd/buoy.service) always passes
`--config /etc/buoy/buoy.yaml` explicitly rather than relying on discovery.

**History storage:** if `features.history: true` is set, Buoy writes a
SQLite ring buffer to `/data/buoy.db` when `/data` exists, otherwise to
`buoy.db` in the process's current working directory
(`src/buoy/storage.py`). The bundled unit sets `WorkingDirectory=/var/lib/buoy`
and `StateDirectory=buoy` so history lands there predictably — it does
**not** create `/data`. If you'd rather match the container layout exactly,
create `/data` yourself and grant the service user write access instead.

## 4. Install and start the systemd unit

```bash
sudo cp deploy/systemd/buoy.service /etc/systemd/system/buoy.service
sudo systemctl daemon-reload
sudo systemctl enable --now buoy
sudo systemctl status buoy
```

The unit ([`deploy/systemd/buoy.service`](../../deploy/systemd/buoy.service)):

- Runs as a dynamically-allocated unprivileged user (`DynamicUser=yes`),
  with `SupplementaryGroups=docker` so it can reach
  `/var/run/docker.sock` without running as root. Remove that line if you
  don't want Docker-based service discovery.
- Sets `WorkingDirectory=/var/lib/buoy` and `StateDirectory=buoy` (creates
  `/var/lib/buoy` mode `0750`, owned by the dynamic user) for history
  storage.
- Applies standard hardening (`ProtectSystem=strict`, `NoNewPrivileges=yes`,
  `PrivateTmp=yes`, etc.) — none of it blocks reading `/proc` or `/sys`,
  which are outside `ProtectSystem`'s scope.
- Reads secrets (e.g. `BUOY_AUTH_TOKEN=...`) from `/etc/buoy/buoy.env` if
  present (`EnvironmentFile=-/etc/buoy/buoy.env` — the leading `-` makes a
  missing file non-fatal).

Verify the unit is well-formed before installing it, if `systemd-analyze`
is available:

```bash
systemd-analyze verify deploy/systemd/buoy.service
```

## 5. Verify

```bash
curl http://localhost:8090/api/health
```

Open `http://<host>:8090` in a browser. To try it without any host access
first, run the demo mode: `buoy --demo` (see the
[Quick Start](../../README.md#quick-start) for the container equivalent).

## Updating

```bash
sudo /opt/buoy/venv/bin/pip install --upgrade git+https://github.com/gfargo/buoy.git
sudo systemctl restart buoy
```
