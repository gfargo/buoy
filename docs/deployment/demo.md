# Hosted demo

A public instance of `--demo` mode runs at
**[buoy-demo.fly.dev](https://buoy-demo.fly.dev)** — no signup, no config,
nothing to install. It's the same image published to
`ghcr.io/gfargo/buoy:latest`, redeployed automatically after every release.

## Why it's safe to expose publicly

`--demo` replaces every collector and plugin call with synthetic data before
the app ever binds a socket (`on_startup`,
[`src/buoy/server.py:1118`](../../src/buoy/server.py)) — there's no code path
from a request on this instance back to a real host, Docker daemon, or
network:

- **No Docker socket, no host mounts, no privileged flags.** The container
  runs exactly as `docker run ghcr.io/gfargo/buoy:latest --demo` — see
  [Demo Mode in the README](../../README.md#demo-mode).
- **Containers are fake.** `DemoDockerCollector` in
  [`src/buoy/demo.py`](../../src/buoy/demo.py) returns a fixed list of
  made-up containers; `restart_container()` is a stub that always reports
  success without touching anything (`src/buoy/demo.py:257`), and
  `stream_logs()` yields generated log lines forever instead of following a
  real process (`src/buoy/demo.py:236`).
- **Plugins never run.** In demo mode a plugin's `setup()`/`collect()` are
  never called, so `--demo` never makes a real outbound call
  (`src/buoy/plugins/loader.py:201`); clicking a plugin's refresh button
  returns `{"demo": true}` instead of collecting
  (`src/buoy/server.py:569`). Only the curated `DEMO_PLUGIN_IDS` set
  (`src/buoy/demo.py:68`) is auto-enabled — notably **not**
  `prometheus_exporter`, so this instance has no `/metrics` endpoint.
- **The debug endpoint stays locked.** `/api/config/debug` requires
  `auth.token` to be set and returns 403 otherwise
  (`src/buoy/server.py:164`); the hosted instance sets no token, so it's
  unreachable there.

## What's running

[`deploy/demo/fly.toml`](../../deploy/demo/fly.toml) deploys the published
`ghcr.io/gfargo/buoy:latest` image on [Fly.io](https://fly.io), overriding
the container's default command with `--demo` the same way CI's smoke test
does (`docker run ... buoy:smoke --demo`, see `.github/workflows/ci.yml`).
Single machine, `min_machines_running = 0` so it suspends when idle and
wakes on the next request — demo data is synthetic and per-process anyway
(`src/buoy/demo.py`'s `_START_TIME`/`random` state), so there's no benefit
to keeping more than one machine warm at once.

## Redeploying

A `demo` job in [`.github/workflows/release.yml`](../../.github/workflows/release.yml)
redeploys automatically after every image publish. To do it by hand:

```bash
flyctl deploy -c deploy/demo/fly.toml --image ghcr.io/gfargo/buoy:latest
```

To roll back to a known-good release instead of `:latest`, deploy a specific
tag or digest:

```bash
flyctl deploy -c deploy/demo/fly.toml --image ghcr.io/gfargo/buoy:<tag-or-sha>
```

## Running your own

### Fly.io

```bash
fly launch --no-deploy --name <your-app> --image ghcr.io/gfargo/buoy:latest
fly deploy -c deploy/demo/fly.toml --image ghcr.io/gfargo/buoy:latest
```

Set `FLY_API_TOKEN` (`fly tokens create deploy -a <your-app>`) if deploying
from CI rather than your own machine.

### Railway

Railway injects the listen port via a `$PORT` environment variable rather
than a fixed port, and the Dockerfile's `HEALTHCHECK` is hardcoded to 8090 —
point Buoy's own listener at Railway's port with the existing env override
(`_apply_env_overrides`, [`src/buoy/config.py:240`](../../src/buoy/config.py)):

```bash
BUOY_NETWORK_LISTEN_PORT=$PORT
```

Deploy the same `ghcr.io/gfargo/buoy:latest` image with the container start
command overridden to `--demo`, and set `BUOY_NODE_NAME` so
`/api/health`'s `hostname` field isn't empty
(`src/buoy/server.py:101`).
