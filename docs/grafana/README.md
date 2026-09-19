# Grafana dashboard + Prometheus rules

A pre-built dashboard and rule files for scraping buoy's `/metrics`
endpoint with Prometheus and visualizing it in Grafana.

- [`buoy.json`](./buoy.json) — importable Grafana dashboard.
- [`recording-rules.yml`](./recording-rules.yml) — derived series (e.g. memory used %).
- [`alert-rules.yml`](./alert-rules.yml) — alerts mirroring buoy's own in-app thresholds (`DEFAULT_THRESHOLDS` in `src/buoy/alerts.py`).

Tested against Grafana 10/11 (`schemaVersion: 39`); Prometheus 2.x.

## 1. Enable the exporter

The `/metrics` route only exists when both the plugin system and the
`prometheus_exporter` plugin are enabled — otherwise it 404s. In
`buoy.yaml`:

```yaml
plugins:
  enabled: true
  builtin:
    prometheus_exporter:
      enabled: true
```

## 2. Scrape it with Prometheus

```yaml
scrape_configs:
  - job_name: buoy
    static_configs:
      - targets: ["my-server:8090"]
```

**Sub-path deployments:** if `network.base_path` is set in `buoy.yaml` (e.g.
behind a reverse proxy at `/buoy`), the route is mounted under that prefix
too, so scrape it at `metrics_path: /buoy/metrics` instead of the default
`/metrics`.

**Auth:** `/metrics` is one of buoy's `PROTECTED_PATHS`, so it's always
rate-limited, and it's also auth-gated whenever `auth.enabled: true`. Add
the matching credentials to the scrape config:

```yaml
scrape_configs:
  - job_name: buoy
    static_configs:
      - targets: ["my-server:8090"]
    # auth.type: token
    authorization:
      credentials: "<your-token>"
    # auth.type: basic
    # basic_auth:
    #   username: "<user>"
    #   password: "<pass>"
```

**Rate limit:** protected paths (including `/metrics`) are capped at 60
requests/minute per source IP. A single Prometheus instance scraping at a
normal 15s interval (4 req/min) is well under that, but if several
Prometheus/Thanos/Grafana Agent instances scrape through the same proxy IP,
they share that budget — space out scrape intervals or scrape directly
per-instance if you see 429s.

**Multi-node:** the `host` label on every metric is buoy's configured
`node.name` (`buoy.yaml`'s `node.name`), not the OS hostname. Give each node
a distinct `node.name`, or their series will collide on the same `host`
label — Prometheus's own `instance` label (from the scrape target) is a
reliable tiebreaker if you forget.

## 3. Import the dashboard

Grafana → Dashboards → New → Import → upload `buoy.json` (or paste its
contents) → Import.

The dashboard uses a datasource template variable rather than Grafana's
`__inputs` prompt, so the import screen only asks for a name/folder/UID —
it will not prompt you to pick a datasource. After import, use the
dashboard's own `Datasource` variable (top left) to select your Prometheus
datasource; panels show "No data" until it's set.

The `host` variable, populated from
`label_values(buoy_uptime_seconds, host)`, defaults to "All".

A few panels (CPU, memory, temperature, NVMe) show "No data" on hosts where
the underlying collector can't read that metric (e.g. no CPU temperature
sensor, or an NVMe-only SMART value on a SATA-only host) — buoy omits those
metric lines entirely rather than emit a placeholder, matching the
exporter's behavior.

## 4. Load the rule files

```yaml
rule_files:
  - "recording-rules.yml"
  - "alert-rules.yml"
```

The recording rules are additive — the dashboard queries the raw metrics
directly and works without them loaded. The alert thresholds match buoy's
own alert engine exactly; if you change `DEFAULT_THRESHOLDS` in
`src/buoy/alerts.py`, update `alert-rules.yml` to match.
