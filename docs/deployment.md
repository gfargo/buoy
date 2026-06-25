# Deployment Guide

## Single Node

The simplest setup — one buoy instance on one machine:

```bash
curl -o buoy.yaml https://raw.githubusercontent.com/gfargo/buoy/main/buoy.yaml.example
# Edit buoy.yaml: set node.name
docker compose up -d
```

Access at `http://your-server:8090`.

## Multi-Node Fleet

Deploy buoy on each node. Each instance polls the others for the fleet overview.

**Node 1 (compass):**
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
    - name: watch
      url: https://watch.example.ts.net
      tier: "Tier 2"
```

**Node 2 (harbor):**
```yaml
node:
  name: harbor
  tier: "Tier 1A"
network:
  tailnet_domain: example.ts.net
  peers:
    - name: compass
      url: https://compass.example.ts.net
      tier: "Tier 1B"
    - name: watch
      url: https://watch.example.ts.net
      tier: "Tier 2"
```

Each node shows its own vitals + a fleet grid with live stats from peers.

## Behind a Reverse Proxy

### Caddy

```caddyfile
your-domain.com {
    reverse_proxy localhost:8090
}
```

### Traefik (Docker labels)

```yaml
services:
  buoy:
    image: ghcr.io/gfargo/buoy:latest
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.buoy.rule=Host(`buoy.your-domain.com`)"
      - "traefik.http.services.buoy.loadbalancer.server.port=8090"
```

### Nginx

```nginx
server {
    listen 443 ssl;
    server_name buoy.your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

> **Important:** Include the WebSocket upgrade headers for real-time features.

## With Tailscale

Buoy is Tailscale-aware. When accessed via a `*.ts.net` hostname, all generated service links use HTTPS tailnet URLs automatically.

```yaml
network:
  tailnet_domain: tailb82ead.ts.net
```

Combine with Tailscale HTTPS:
```bash
# On the host
tailscale cert your-node.tailb82ead.ts.net
# Configure Caddy to use the cert
```

## Privilege Levels

| Mode | What works | Docker flags |
|------|-----------|------|
| Full (recommended) | Everything — CPU, temp, all disks, NVMe SMART, cron logs | `privileged: true`, `pid: host` |
| Medium | Containers, CPU, memory, root disk | `pid: host` only |
| Minimal | Container stats only | Docker socket mount only |
| Demo | Mock data, no host access | None |

## Resource Usage

Buoy is lightweight:
- **Memory:** ~30-50MB RSS
- **CPU:** <1% average (spikes briefly during stats collection)
- **Disk:** ~14MB/day if history is enabled (auto-pruned)
- **Network:** ~1KB per peer poll per cycle

## Upgrading

```bash
docker compose pull
docker compose up -d
```

Buoy uses a single SQLite file for history — no migrations needed. Config format is backward-compatible within major versions.

## Environment Variables

For container orchestrators that prefer env vars over files:

```bash
docker run -d \
  -p 8090:8090 \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -e BUOY_NODE_NAME=my-server \
  -e BUOY_FEATURES_HISTORY=true \
  -e BUOY_AUTH_TOKEN=my-secret \
  --privileged --pid=host \
  ghcr.io/gfargo/buoy:latest
```
