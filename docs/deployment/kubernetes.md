# Kubernetes

Two ways to run Buoy on Kubernetes: plain manifests under
[`deploy/k8s/`](../../deploy/k8s/), or the Helm chart under
[`deploy/helm/buoy/`](../../deploy/helm/buoy/). Both offer the same two
workload shapes — pick based on how much host access you want to grant. See
[the privilege matrix](./privilege-matrix.md) for exactly what each level
costs you in lost metrics.

- **`Deployment`** ([`deployment.yaml`](../../deploy/k8s/deployment.yaml)) —
  unprivileged, no `hostPID`. Mounts only the Docker socket for service
  discovery. Maps to privilege-matrix Tier 3. **Recommended default.**
- **`DaemonSet`** ([`daemonset.yaml`](../../deploy/k8s/daemonset.yaml)) —
  one pod per node, `securityContext.privileged: true` + `hostPID: true`,
  matching the "one Buoy per host" model from the
  [README](../../README.md) and the `docker-compose.yml` full-metrics
  example. Maps to privilege-matrix Tier 1.

Both translate the Compose example's `docker-compose.yml` settings
(`privileged`, `pid: host`, the `/var/run/docker.sock` mount, the
`buoy.yaml` config mount, `BUOY_AUTH_TOKEN`) into Kubernetes primitives:
`securityContext.privileged`, `hostPID`, a `hostPath` volume for the Docker
socket, a `ConfigMap` for `buoy.yaml`, and a `Secret` for the auth token.

## Plain manifests

```bash
kubectl apply -f deploy/k8s/configmap.yaml
kubectl apply -f deploy/k8s/service.yaml
kubectl apply -f deploy/k8s/deployment.yaml   # or daemonset.yaml for full metrics
```

Edit [`configmap.yaml`](../../deploy/k8s/configmap.yaml) first — at minimum
set `node.name` (or rely on `BUOY_NODE_NAME`, which the DaemonSet manifest
already sets from `spec.nodeName` per pod). For an auth token, create a
Secret named `buoy-auth` with a `token` key before applying (both manifests
reference it as `optional: true`, so it's fine to skip):

```bash
kubectl create secret generic buoy-auth --from-literal=token=<your-token>
```

Validate before applying, if `kubeconform` or `kubectl --dry-run` is
available:

```bash
kubectl apply --dry-run=client -f deploy/k8s/
# or: kubeconform -strict deploy/k8s/*.yaml
```

## Helm chart

```bash
helm install buoy deploy/helm/buoy \
  --set config="node:\n  name: my-node" \
  --set auth.token=<your-token>
```

Or point `--values` at your own file — `values.yaml` documents every
option. Key settings:

| Value | Default | Effect |
|---|---|---|
| `workloadKind` | `deployment` | `deployment` (unprivileged) or `daemonset` (full metrics, one per node — automatically sets `privileged` + `hostPID`) |
| `privileged` | `false` | Force `securityContext.privileged: true` on a `deployment` workload too (not needed for `daemonset`, which always sets it) |
| `hostPID` | `false` | Force `hostPID: true` on a `deployment` workload (not needed for `daemonset`) |
| `config` | `node:\n  name: my-node` | Raw `buoy.yaml` contents, rendered into a ConfigMap |
| `auth.token` | `""` | Creates a Secret and wires `BUOY_AUTH_TOKEN`; leave empty to run without auth |
| `dockerSocket.enabled` | `true` | Mount `/var/run/docker.sock` for service discovery |
| `persistence.enabled` | `false` | PVC for `/data` (history storage) on `deployment`; `daemonset` always uses `emptyDir` per node |

Lint and render before installing:

```bash
helm lint deploy/helm/buoy
helm template buoy deploy/helm/buoy --set workloadKind=daemonset | kubeconform -strict
```

## Networking

Buoy listens on port 8090 (`Service` exposes it as `http`). Put an Ingress
or your existing reverse proxy (Traefik, Caddy, nginx) in front — none is
included here since ingress setup is cluster-specific. `/api/health` is a
safe liveness/readiness path (unauthenticated, matches the probes in both
manifests and the Helm template).

## Data persistence

Same caveat as the [native install](./native.md#3-config-and-data-directories):
Buoy's SQLite history store only uses `/data` if that path exists
(`src/buoy/storage.py`), so `/data` must be a real mount — both the plain
manifests and the Helm chart mount an `emptyDir` (or a `PersistentVolumeClaim`
when `persistence.enabled: true` in Helm) at `/data` for this reason. Without
a `PersistentVolumeClaim`, history resets on every pod restart.
