# Privilege / metrics matrix

Buoy's [Docker Compose](../../README.md#docker-compose) example runs with
`privileged: true` and `pid: host` to unlock the fullest possible set of
host metrics. Neither is required to run Buoy at all — each only gates
specific collectors and plugins, which degrade gracefully (to an empty
list, a zero value, or a disabled plugin) when the access isn't available.
This page maps exactly what each capability buys you, so you can pick the
lowest privilege level that still shows what you care about.

The [native/systemd install](./native.md) sidesteps this trade-off
entirely: a process running directly on the host already has real
`/proc`, `/sys`, `ps`, and (via group membership) Docker socket access,
with none of the container privilege flags below.

## Capability → metric mapping

| Capability | Enables | Lost without it | Source |
|---|---|---|---|
| Docker socket mount (`/var/run/docker.sock`) + `docker` CLI in the container | Service discovery, container stats/inspect/logs/restart | Services list stays empty; `DockerCollector.is_available()` returns false and every call degrades to `[]`/`{}` | `src/buoy/collectors/docker.py` |
| `pid: host` (or running natively on the host) | `ps aux` sees host processes for the "top processes" panel | `ps` only sees processes inside the container's own PID namespace | `src/buoy/collectors/system.py` (`_top_processes_by`) |
| `privileged: true` (or `CAP_SYS_ADMIN` + device access) + `pid: host` | `nsenter -t 1 -m` to reach the host's mount namespace for the full mount list, and to reach `/dev/nvme0n1` for NVMe SMART data via `smartctl` | Mount list falls back to the container's own root filesystem only (one entry, not the host's real mounts); NVMe SMART section is omitted entirely (`nvme` key absent from `/api/stats`) | `src/buoy/collectors/disk.py` (`_nsenter_mounts`, `_nvme_smart`) |
| Host `/sys` visibility (implied by `privileged`, or native) | CPU temperature reading from `/sys/class/thermal/thermal_zone0/temp` | Temperature reports as `0` | `src/buoy/collectors/system.py` (`_read_temperature`) |
| Linux host / container (vs. macOS/Windows) | CPU %, memory, uptime, device model from `/proc` | All of `cpu`, `mem_used`, `mem_total`, `uptime_*` report as `0`/`0.0`; `model` falls back to `platform.system() + platform.machine()` | `src/buoy/collectors/system.py` (`_fallback_stats`) |
| `privileged` + `pid: host` (nsenter into host PID 1) | Plugins that read host-only state: `tailscale` (peer status), `wireguard` (tunnel stats), `smart_disk` (SATA/NVMe health), `cron_health` (cron logs), `journal_errors` (journald), `systemd_health` (unit status) | Those plugins can't reach host state from inside an unprivileged/non-`pid:host` container and report unavailable/empty | `buoy.yaml.example` (each plugin's comment notes this requirement) |

## Recommended tiers

### Tier 1 — Full (privileged + pid: host)

The Docker Compose default. Every metric above is available: temperature,
full host mount list, NVMe SMART, host top-processes, and all
host-introspection plugins.

```yaml
privileged: true
pid: host
```

Trade-off: the container can see and interact with the full host process
tree and device list. Only use this on hosts you fully trust the Buoy
config/plugins on (matches the note already in `docker-compose.yml`).

### Tier 2 — Container-stats-only (`pid: host`, no `privileged`)

Keeps host process visibility (top-processes panel, plugins that just need
`pid: host` without `nsenter`-ing into device files) but drops the ability
to reach host mounts and `/dev/nvme0n1`.

```yaml
pid: host
# privileged: true   ← omitted
```

You get: Docker service discovery, host top-processes, container stats.
You lose: full host mount list (falls back to the container's own root
mount), NVMe SMART health. Temperature also requires `privileged` in
practice, since `/sys/class/thermal` isn't reliably exposed by `pid: host`
alone — verify on your kernel/container runtime.

### Tier 3 — Minimal / unprivileged

No `privileged`, no `pid: host` — just the Docker socket mount for service
discovery, or nothing at all (demo mode).

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:ro
# no privileged, no pid: host
```

You get: service discovery, container stats/logs/restart, root-filesystem
disk usage (via `shutil.disk_usage("/")`, not the host's real usage).
You lose: temperature, full host mount list, NVMe SMART, host
top-processes, and every host-introspection plugin (`tailscale`,
`wireguard`, `smart_disk`, `cron_health`, `journal_errors`,
`systemd_health`).

### Tier 0 — Native / systemd (recommended when you control the host)

Running the [native install](./native.md) directly on the host gives you
everything in Tier 1 — real `/proc`, `/sys`, `ps`, and Docker socket group
membership — **without any container privilege escalation at all**, because
there's no container boundary to cross. This is the best option whenever
you're comfortable running Buoy as a host process rather than in a
container.

## Kubernetes equivalents

See [kubernetes.md](./kubernetes.md) for the manifest/Helm settings that
map to these tiers (`securityContext.privileged`, `hostPID`,
`hostPath` mount of the Docker socket).
