# Buoy Audit — post-2.1.0

Repo state audited: `main` @ `a05ad90` (after #74). Tests: **384 pass**, `ruff check` + `ruff format --check` clean, coverage **73%**.

**131 numbered items.** Every **BUG** and **SEC** item was reproduced or confirmed against the code in this tree — evidence is quoted inline. Items are written to be pasted directly into GitHub issues.

**Contents**

| Section | Count |
|---|---|
| [Executive summary](#executive-summary) | — |
| [Cross-cutting themes](#cross-cutting-themes) | — |
| [Judgment calls & open questions](#judgment-calls--open-questions) | — |
| [Security](#security) | 11 |
| [Bugs — user-visible](#bugs--user-visible) | 12 |
| [Bugs — data & lifecycle](#bugs--data--lifecycle) | 11 |
| [Bugs — collectors & metric accuracy](#bugs--collectors--metric-accuracy) | 11 |
| [Bugs — plugin system](#bugs--plugin-system) | 6 |
| [Bugs — packaging, deploy, ops](#bugs--packaging-deploy-ops) | 10 |
| [Features — core](#features--core) | 23 |
| [Features — new plugins](#features--new-plugins) | 26 |
| [Features — plugin platform](#features--plugin-platform) | 7 |
| [Docs & project health](#docs--project-health) | 9 |
| [Housekeeping](#housekeeping) | 5 (incl. BUG-51) |

### Method

Nothing here is inferred from reading alone. The baseline was confirmed healthy first (`pytest -q`, `ruff check`, `ruff format --check`, `pytest --cov`), then each claim was checked by: booting the app through Starlette's `TestClient` and hitting the endpoints, calling `_build_config` / `AuthMiddleware` / `resolve_plugin_env` directly, building the wheel and listing its contents, and cross-referencing config keys, CSS custom properties, and server response keys against their frontend consumers. Scratch scripts were removed; the tree is clean apart from this file.

---

## Executive summary

### P0 — fix before the next release

| ID | Finding |
|---|---|
| **SEC-1** | `auth.enabled: true` with an empty token/password **fails open** — `_check_token` returns `True` when nothing is configured. Users who enable auth and forget the secret believe they're protected and aren't. |
| **SEC-2** | `CORS allow_origins=["*"]` + `allow_methods=["*"]` over an unauthenticated `POST /api/container/{name}/restart`. Any page a user visits can restart containers on any buoy their browser can reach. |
| **SEC-3** | Stored XSS in four render paths. Plugin summaries (Loki log lines, GitHub PR titles) and peer `top_services` go into `innerHTML` unescaped — including `href`, so `javascript:` executes. |
| **BUG-13** | `features.history: true` records **nothing** when `websocket: false`. Silent, total data loss for anyone who turns off the socket. |

### Features that ship but do not work

These matter disproportionately because the docs promise them, so nobody reports them as broken — they just quietly don't happen.

| ID | Advertised | Reality |
|---|---|---|
| **BUG-14** | "Optional webhook dispatch (Discord/Slack/generic)" — CHANGELOG 2.0.0 | `plugins.builtin.get("alerts")` returns a `PluginEntry`, so the URL is always `""`; every send raises into `except Exception: pass`. No `alerts` key exists in `buoy.yaml.example` either, so it was never configurable. |
| **BUG-41** | `pip install` + a `buoy` console script | The wheel contains **zero** static files — no `[tool.hatch.build]`, `static/` sits outside `src/`. `/` returns 500. Only the Docker image works. |
| **BUG-2** | `node.tier` in the README's own example | `gauges.js` reads `tierTag.dataset.tier`; nothing ever sets it. The badge always renders the hostname. |
| **BUG-6** | `theme.custom` in `buoy.yaml.example` + SPEC §3.1 | Served by `/api/config`, read by no JS file. |
| **BUG-3** | `node.role` ("description like 'Database Server'") | Zero references in `static/`. |
| **BUG-36** | `refresh.plugins_interval` | Server-side no-op; the loader uses `manifest.refresh_interval`. |
| **BUG-24** | SPEC §3.3: "validated against a JSON Schema, unknown keys warn" | No validation at all. `noed: {nmae: x}` yields a node called `buoy` with no diagnostic. |
| **BUG-43** | `/metrics` "if prometheus_exporter plugin is enabled" (its own docstring) | Always registered, unauthenticated, and runs a full collector pass. |
| **BUG-40** | SPEC §5.4: "Plugins return sample data" in demo mode | Collectors are stubbed, plugins are not — `--demo` makes real outbound API calls. |
| **PP-1** | The documented plugin extension story | User plugins get `configure({})`; they cannot receive a URL, token, or threshold. |

### Version drift

`pyproject.toml` **2.0.2** · `buoy.__version__` **2.1.0** · `/api/health` hardcoded **2.0.0-alpha.1**. The Dockerfile comment claims hatchling reads the version from `__init__.py`, but `pyproject` pins it statically, so they drift silently. CHANGELOG lists 2.0.0 *above* 2.0.2/2.0.1, has no 2.1.0 entry despite ~30 merged PRs, and still ends with "Unreleased — Nothing yet." See **BUG-1**, **DOC-2**.

### Measured gaps — quick reference

| Measurement | Value | Item |
|---|---|---|
| Builtin plugins shipping | **22** | — |
| …documented in README table | **10** | DOC-1 |
| …documented in `buoy.yaml.example` | **16** | DOC-1 |
| Subprocess timeouts that never `proc.kill()` | **17 of 17** | BUG-19 |
| Files importing `logging` | **0** (9 `print()`, 17 `except Exception: pass`) | BUG-47 |
| CSS custom properties referenced but undefined | **5** (used by 4 shipped plugins) | BUG-7 |
| `system.py` test coverage | **0%** (132 statements) | BUG-50 |
| `docker ps` invocations per `/api/stats` | **2** (SPEC §8.2 requires a 5 s cache) | BUG-9 |
| Stats collections per refresh interval | **2** (WS push + HTTP poll) | BUG-8 |
| Endpoints for the fully-built `/api/history` API used by the UI | **0** | FEAT-3 |

---

## Cross-cutting themes

Several items look independent in the list but are one piece of work. Filing them as separate issues is fine; scheduling them separately is not.

**1. `new Function()` + string HTML is the root of three problems.** Plugins ship JS that is `eval`'d and interpolates its own markup. That single design choice causes **SEC-3** (stored XSS), blocks **SEC-6** (no CSP is possible while `unsafe-eval` is required), and is why third-party plugins can never be safely distributed (**PP-5**). **PP-4** — a declarative panel spec rendered by trusted escaping code — is the fix for all three. Treat it as one thread, not three tickets; doing SEC-3 as spot-escaping now and PP-4 later means escaping the same data twice.

**2. Auth is half-built, and the half that exists is worse than none.** **SEC-1** (fails open), **SEC-5** (rate limiting only exists when auth is on), **SEC-4** (`/api/config/debug` public when auth is off), and **SEC-9** (no frontend token support at all, so enabling auth breaks the UI) are one story: the feature was never completed end to end. Shipping SEC-1's fix alone converts a false sense of security into a locked-out dashboard. Land the frontend login overlay in the same release.

**3. "Why is this panel empty?" has no answer today.** **BUG-47** (no logging), **BUG-24** (no config validation), **BUG-12**/**PP-7** (no error surfaced in the UI), **BUG-37** (failing plugins vanish rather than showing an error), **FEAT-12** (`is_available()` is dead code) and **BUG-33** (macOS shows zeros, not "unsupported") all produce the same user experience: silence. **FEAT-13** (`buoy doctor`) is the single highest-leverage item here and would deflect most support traffic.

**4. Metric accuracy is systematically optimistic on non-Pi hardware.** **BUG-28** (only `thermal_zone0` — usually wrong on x86), **BUG-26** (disk I/O hardcodes `nvme0n1|sda|mmcblk0`, so every VM reports 0), **BUG-27** (only `/dev/nvme0n1`), **BUG-25** (disk gauge measures the container, the table measures the host), **BUG-29** (memory ignores `SReclaimable`/`Shmem`, overstating usage), **BUG-32** (ignores cgroup quota). The project was clearly developed against Raspberry Pi hosts; anyone on a VM or x86 box sees wrong or zero values. **BUG-50** is why none of this was caught — `system.py` has no tests.

**5. Everything is priced for a Pi but paid twice.** **BUG-8** (double fetch) multiplies **BUG-9** (2× `docker ps`), **BUG-31** (100 ms CPU sample per request), and **BUG-22** (synchronous SQLite commit on the event loop). Fixing BUG-8 alone halves the cost of the other three.

---

## Judgment calls & open questions

These are recommendations rather than findings — they need your call, not a patch.

**BUG-51 is probably a bad merge, and that's the real issue.** PR #70 explicitly states it rewrote `refreshFleet()` to call `/api/fleet` once; the current `fleet.js` does the opposite. Something clobbered a merged change and no test caught it. Worth a quick `git log -p -- static/js/fleet.js` on the full history (this audit ran against a shallow clone and couldn't check) to see whether other merged work was lost the same way. If it was, that's a process issue outranking any individual bug here.

**#3 may not be safe to close.** Peer latency did ship (#19 → #44 → #70), so the issue looks done — but latency only renders when `features.history` is enabled, because the sparkline reads from the metric store. Either close it and file the history-independent case separately, or re-scope #3 to that gap. **#9** and **#31** are unambiguously delivered and can be closed outright. **#10** and **#11** remain valid.

**SPEC.md needs a decision, not an edit.** It still reads `Status: Planning` (dated 2026-06-24), lists 12 plugins against 22 shipping, describes a finished v1→v2 migration, and has an unchecked launch checklist that's largely done. It's also the cited reference for issues #1–#11, so deleting it orphans that context. Options: (a) extract the still-authoritative parts — threat model, plugin protocol, API contract — into `docs/architecture.md` and archive the rest, or (b) add a "historical, see docs/" banner. Option (a) is more work but the plugin protocol section is genuinely the best documentation in the repo and deserves to be live. See **DOC-6**.

**Where I'd point new feature work, if it's a choice between them.** **FEAT-3** (trend charts) is the best value-per-effort item in the repo: `/api/history/{metric}` is fully implemented, tested, and consumed by nothing. It's mostly frontend work on an API that already exists. After that, the two conspicuous *absences* for this audience are **FEAT-1** (GPU — nothing at all, and Jellyfin transcoding / Frigate / Immich ML / Ollama users all want it) and **FEAT-2** (NIC throughput — `network.py` is peer polling only, there is no bandwidth metric). On plugins, **PLG-1** (UPS/NUT) and **FEAT-15** (ZFS) are the biggest gaps relative to the audience, and **PLG-26** is arguably a bug: `dns_filter` targets Pi-hole v5's `admin/api.php`, which v6 removed, so current Pi-hole users just get `error`.

**Scope note on the plugin count.** 22 builtins with 6 undocumented suggests the plugin surface is growing faster than the docs and config example can absorb. The CI check proposed in **DOC-1** (assert every `manifest.id` appears in README and `buoy.yaml.example`) matters more than back-filling the six, because it stops the drift rather than resetting it.

---

## Security

### SEC-1 — `auth.enabled: true` with an empty credential fails **open**
`auth.py` `_check_token`/`_check_basic` both `return True  # No token configured = pass through`. Reproduced:

```
token auth, no token set, no header -> allowed = True
basic auth, no password set, no header -> allowed = True
```

Anyone who sets `auth.enabled: true` but forgets to supply `BUOY_AUTH_TOKEN` gets a dashboard they believe is protected and isn't. Should refuse to start (or log a loud error and deny all) when auth is enabled without a credential.
`priority: P0` · `labels: bug, security, backend`

### SEC-2 — Any origin can restart your containers (CORS `*` + no CSRF + auth off by default)
`server.py` sets `CORSMiddleware(allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])`, and `POST /api/container/{name}/restart` is unauthenticated in the default config. Reproduced on a default install:

```
POST /api/container/anything/restart (no auth) -> 200 {'success': True, ...}
CORS preflight from https://evil.example -> 200
   Access-Control-Allow-Origin: *
```

Any page a user visits can enumerate and restart containers on any buoy reachable from their browser (LAN/tailnet). SPEC §7.2 specifies *"CORS: Same-origin only"*. Fix: same-origin by default, opt-in origin allowlist for fleet, and never allow wildcard on state-changing methods.
`priority: P0` · `labels: bug, security, backend`

### SEC-3 — Stored XSS from plugin and peer data
Four render paths interpolate untrusted strings straight into `innerHTML` with no escaping:

- `plugins.js` — `plugin.name`, `plugin.summary`, and every `plugin.detail` value. Plugin summaries carry third-party text: GitHub PR titles, **Loki log lines**, Jellyfin session names, journal error messages.
- `fleet.js` — `s.name` / `s.url` from a **peer's** `top_services`, including `href="${s.url}"` (so `javascript:` URLs execute). Note the same file already defends against this for alerts: *"never inject peer message strings"* — service pills were missed.
- `services.js` — `s.name`, `s.desc`, `s.url`.
- `detail.js` — `image`, `status`, resource strings, and `onclick="window._buoyInspectContainer('${c.name}')"` (name injected into a JS string inside an HTML attribute).

`escapeHtml()` already exists in `detail.js` but is only used for log output. A malicious/compromised peer or any monitored app that can influence a log line or PR title gets JS execution in the dashboard — which is same-origin with the unauthenticated container-restart API (see SEC-2).
`priority: P0` · `labels: bug, security, frontend`

### SEC-4 — `/api/config/debug` is public when auth is disabled (the default)
The auth middleware is only installed `if config.auth.enabled:`, so on a default install:

```
GET /api/config/debug (no auth) -> 200
   keys: ['node','network','services','theme','auth','features','refresh','plugins']
```

Secrets are redacted, but peer URLs, plugin endpoints, basic-auth **usernames**, and the full feature/plugin topology are not. Either gate debug endpoints independently of `auth.enabled`, bind them to loopback, or require a token even when auth is otherwise off.
`priority: P1` · `labels: bug, security, backend`

### SEC-5 — Rate limiting only exists when auth is enabled
`_check_rate_limit` runs inside `AuthMiddleware.dispatch`, which is only mounted when `auth.enabled`. Default installs have **no** rate limit on restart/logs/inspect. SPEC §7.2 lists rate limiting as always-on for destructive endpoints.
`priority: P1` · `labels: bug, security, backend`

### SEC-6 — No CSP header
`SecurityHeadersMiddleware` sets `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` — confirmed no `Content-Security-Policy`. SPEC §7.2 requires a strict CSP. Blocked partly by plugins' `new Function()` renderer, so pair with PP-5.
`priority: P1` · `labels: bug, security, frontend`

### SEC-7 — Non-ASCII auth token returns 500 instead of 401
`hmac.compare_digest` on `str` requires ASCII. With `token: "pässwörd"`:

```
TypeError: comparing strings with non-ASCII characters is not supported
```

Compare on `.encode()`d bytes.
`priority: P2` · `labels: bug, security, backend`

### SEC-8 — Fleet polling hardcodes `verify=False`
`network.py` lines 49 and 112: `httpx.AsyncClient(timeout=4.0, verify=False)`. TLS verification is permanently disabled for all peer traffic with no way to turn it on — while plugins (`portainer`, `dns_filter`, `proxmox`) all expose a `verify_ssl` option. Add `network.verify_ssl` (default `true`) and a per-peer override.
`priority: P1` · `labels: bug, security, backend`

### SEC-9 — Enabling auth breaks the UI (no frontend auth support)
No file under `static/js/` references `Authorization` or `localStorage`. SPEC §7.3 specifies a token stored in `localStorage` behind a login overlay. Today, turning on auth means restart/logs/inspect silently 401 with no prompt and no error surfaced. (A `authedFetch()` helper with a token prompt was written in PR #18, which was closed unmerged — the merged PR #13 has no auth handling.)
`priority: P1` · `labels: bug, frontend, security`

### SEC-10 — Container hardening gaps
Dockerfile has no `USER` (runs as root) and no `HEALTHCHECK`; `docker-compose.yml` ships `privileged: true` with only a README note about dropping it. Add a documented least-privilege compose profile (cap-drop + specific caps, `docker.sock` read-only, no `privileged`) and state exactly which metrics each privilege level unlocks.
`priority: P2` · `labels: enhancement, security, docker`

### SEC-11 — No supply-chain / vulnerability automation
Missing: `SECURITY.md`, `.github/dependabot.yml`, CodeQL or `pip-audit` workflow, secret scanning config. Notable given the repo previously leaked a live Plane API token (#71).
`priority: P2` · `labels: security, ci`

---

## Bugs — user-visible

### BUG-1 — Three different version numbers reported
```
pyproject.toml [project].version   : 2.0.2
buoy.__version__ (/api/deploy-info): 2.1.0
/api/health hardcoded string       : 2.0.0-alpha.1
```
`/api/health` has a hardcoded `"version": "2.0.0-alpha.1"`. The footer shows 2.1.0, the wheel builds as 2.0.2. Also: the Dockerfile comment claims *"hatchling reads src/buoy/__init__.py for version"* but `pyproject.toml` pins it statically, so they drift silently. Pick one source of truth (`hatchling.version.source = "code"` or `importlib.metadata`) and have `/api/health` read it.
`priority: P1` · `labels: bug, backend`

### BUG-2 — `node.tier` is never displayed
`gauges.js`: `tierTag.textContent = tierTag.dataset.tier || data.hostname;` — nothing anywhere sets `data-tier`, confirmed by grep across `static/`. The tier badge therefore always renders the hostname, duplicating the `<h1>`. `node.tier` is in the README's "typical homelab" example, is returned by `/api/config`, and never reaches the DOM.
`priority: P1` · `labels: bug, frontend`

### BUG-3 — `node.role` is never displayed anywhere
Documented in `buoy.yaml.example` ("Optional: description like 'Database Server'") and returned by `/api/config`; zero references in `static/`.
`priority: P2` · `labels: bug, frontend`

### BUG-4 — Container detail "Started" is always N/A and "Image Age" never renders
Field-name mismatch. `DockerCollector.inspect_container` returns `status, started, image, restart_count, pid, image_created`. `detail.js` reads `d.started_at` and `d.image_age` — neither exists:

```
detail.js references d.started_at  -> True
detail.js references d.image_created -> False
```

So `started` → *"N/A"* and the Image Age row is never emitted. This is exactly the data #1/PR #13 set out to show.
`priority: P1` · `labels: bug, frontend`

### BUG-5 — Container CPU renders as `0.15%%`
`docker stats --format {{.CPUPerc}}` already includes the percent sign; `detail.js` does `${res.cpu_pct}%`. Same class of issue for `mem_pct`.
`priority: P2` · `labels: bug, frontend`

### BUG-6 — `theme.custom` is documented but does nothing
`/api/config` returns `theme.custom`; no file in `static/js/` reads it. Documented in `buoy.yaml.example` and SPEC §3.1 as a supported way to override CSS variables. Either apply the vars to `document.documentElement.style` or remove it from the docs.
`priority: P2` · `labels: bug, frontend`

### BUG-7 — Five CSS variables are used but never defined, breaking status colors in shipped plugins
Cross-referencing `var(--x)` usage against theme definitions (19 referenced, 14 defined):

| Undefined var | Used by |
|---|---|
| `--ok`, `--warn`, `--error` | `snapraid.py`, `systemd_health.py` |
| `--yellow` | `immich.py`, `smart_disk.py` |
| `--red-dim` | `buoy.css` |

Those plugins' status indicators resolve to an invalid value (transparent/inherited) in **both** themes. Fix by mapping to the real palette (`--green`/`--amber`/`--red`) and adding a CI check that every referenced custom property is defined.
`priority: P1` · `labels: bug, frontend, plugins`

### BUG-8 — Dashboard double-fetches stats (WebSocket push *and* HTTP polling)
`buoy.js` starts `setInterval(refreshStats, stats_interval*1000)` **and** `connectWebSocket(...)`, both feeding `updateGauges`. Every interval runs the full collector chain twice (each `/api/stats` also costs a 100 ms CPU sample and two `docker ps` calls — see BUG-9/BUG-19). Suppress polling while the socket is open; fall back on close.
`priority: P1` · `labels: bug, frontend, performance`

### BUG-9 — `/api/stats` shells out to `docker ps` twice per request
`api_stats` gathers `docker_coll.collect_summary()` *and* `top_services()`; `top_services` → `discover_services` constructs a **new** `DockerCollector` and calls `list_containers()` again. So each poll = 2× `docker ps`, and the collector's `_available` cache is discarded every call. SPEC §8.2 explicitly requires *"Cache container list (5s TTL) to avoid redundant `docker ps` calls"* — never implemented.
`priority: P1` · `labels: bug, backend, performance`

### BUG-10 — `services.hidden` only matches exact container names
`services.py`: `hidden = set(...)` then `if name in hidden`. The README's own example hides `"redis"` / `"postgres"`, but real Compose names look like `plane-plane-redis-1` — so the documented example doesn't work. No glob, prefix, regex, or label-based matching.
`priority: P1` · `labels: bug, docs, backend`

### BUG-11 — Uptime shows "24h 0m" instead of "1d 0h"
`formatUptime`: `if (h > 24)` should be `>= 24`. Duplicated verbatim in `gauges.js` and `fleet.js` (extract a shared util).
`priority: P3` · `labels: bug, frontend`

### BUG-12 — Active alerts aren't rendered on page load
`/api/stats` returns an `alerts` array (added in #74) but `updateGauges` ignores it — alerts only ever appear as transient WebSocket toasts. Load the page while an alert is active and there's no indication at all. `AlertEngine.alert_history` exists but no endpoint exposes it.
`priority: P2` · `labels: bug, frontend`

---

## Bugs — data & lifecycle

### BUG-13 — `features.history: true` silently records nothing if `websocket: false`
`MetricStore.record()` and `AlertEngine.evaluate()` each have exactly **one** call site, both inside `_stats_loop`, which is only started when websockets are on:

```python
if _config.features.websocket:
    asyncio.create_task(_stats_loop())
```

So `{websocket: false, history: true}` = empty database, no alerts, no container-state samples, no image-update decoration, and `/api/history/*` returns empty arrays forever — with no warning. Collection must be independent of transport.
`priority: P0` · `labels: bug, backend`

### BUG-14 — Webhook alerting is dead code
`alerts.py` does `webhook_url = self.config.plugins.builtin.get("alerts", None)`, which returns a `PluginEntry`, never a URL:

```
-> PluginEntry(enabled=True, settings={'url': 'https://hooks.example/x'})
isinstance(val, str) = False
```

It then passes `webhook_url if isinstance(webhook_url, str) else ""` to `urllib.request.Request`, which always raises and is swallowed by `except Exception: pass`. There is also **no `alerts` webhook key anywhere in `buoy.yaml.example`**, so it was never configurable. CHANGELOG 2.0.0 advertises *"Optional webhook dispatch (Discord/Slack/generic)"* — it has never worked.
`priority: P1` · `labels: bug, backend`

### BUG-15 — Alerts never escalate warn → crit
`_check_metric` ends with `if metric not in self._active_alerts: await self._fire_alert(...)`. Once a warn alert is active, crossing the critical threshold updates nothing — no new notification, and the stored alert keeps `level: "warn"` and its original value. Also affects the fleet badge severity from #74.
`priority: P1` · `labels: bug, backend`

### BUG-16 — Blocking HTTP inside the event loop for webhooks
`_send_webhooks` uses `urllib.request.urlopen(req, timeout=5)` in an async task — up to 5 s of frozen event loop per alert. `httpx` is already a dependency.
`priority: P2` · `labels: bug, backend`

### BUG-17 — `_parse_plugins` mutates the caller's config dict (`cfg.pop("enabled")`)
Building a config twice from the same raw dict loses all plugin enablement:

```
1st _build_config -> github.enabled = True
2nd _build_config -> github.enabled = False
raw dict now = {'token': 't'}
```

A latent footgun for config reload, `_factory()` under `--dev`, and any caller that inspects `raw` after building. Use `cfg.get("enabled", False)` + a filtered copy.
`priority: P2` · `labels: bug, backend`

### BUG-18 — No shutdown path: plugins never torn down, SQLite never closed
```
'on_shutdown' in server.py : False
PluginManager.stop() called anywhere : NEVER
MetricStore.close() called anywhere  : NEVER
```
`Plugin.teardown()` is part of the documented plugin lifecycle (SPEC §4.2) and never runs — background tasks like `SpeedtestPlugin._loop` are killed mid-flight and the WAL is never checkpointed. Starlette also warns on every startup:

> The on_startup and on_shutdown parameters are deprecated, and they will be removed on version 1.0. Use the lifespan parameter instead.

Migrating to `lifespan` fixes the deprecation and gives a natural home for teardown.
`priority: P1` · `labels: bug, backend`

### BUG-19 — Timed-out subprocesses are never killed (orphan accumulation)
17 call sites use `await asyncio.wait_for(proc.communicate(), timeout=...)`; **zero** call `proc.kill()` afterwards. On timeout the coroutine is abandoned while the child keeps running — and these are `nsenter`, `smartctl`, `docker stats`, `tailscale ping`, `ps`, some fired every 5 s. On a host where `nsenter` hangs, processes pile up until fork failure. Wrap in a helper that kills + reaps on timeout.
`priority: P1` · `labels: bug, backend`

### BUG-20 — `RuntimeError: Set changed size during iteration` in WebSocket broadcast
`broadcast_stats`/`broadcast_alert` iterate `_ws_clients` with an `await` inside the loop, while `ws_endpoint` calls `_ws_clients.discard(...)` from other tasks. Iterate over a snapshot (`list(_ws_clients)`).
`priority: P1` · `labels: bug, backend`

### BUG-21 — Two unbounded in-memory collections
- `AlertEngine._history.append(alert)` grows forever; only *read* as `[-50:]`. Cap on write.
- `auth.py` `_rate_limit: dict[str, list[float]]` never evicts IP keys — one entry per distinct client IP for process lifetime. Prune empty buckets.

`priority: P2` · `labels: bug, backend`

### BUG-22 — SQLite writes happen synchronously on the event loop, committing every 5 s
`MetricStore.record()` does `INSERT` + `commit()` per cycle on the loop thread; `query_latency` fetches *all* `collector='latency'` rows for the window and filters peers in Python instead of in SQL. Fine on NVMe, not on a Pi with an SD card. Also `prune()` is triggered by `int(loop.time()) % 500 < stats_interval`, which is arbitrary against a monotonic clock of unknown origin — it can prune twice in a row or skip.
`priority: P2` · `labels: bug, backend, performance`

### BUG-23 — Unguarded `int()` on env overrides crashes startup
`_apply_env_overrides` does `raw[section][key] = int(value)` with no try/except, so `BUOY_NETWORK_LISTEN_PORT=eighty-ninety` dies with a bare `ValueError` traceback instead of a config error. Related: `refresh.stats_interval`, `services_interval`, and `plugins_interval` have **no** env mappings at all despite the `BUOY_*` docs implying full coverage.
`priority: P2` · `labels: bug, backend`

---

## Bugs — collectors & metric accuracy

### BUG-24 — No config validation; typos are silently ignored
SPEC §3.3: *"the config is validated against a JSON Schema. Unknown keys warn but don't fail."* Not implemented — `_build_config` only `.get()`s known keys, so `noed: {nmae: x}` yields a node called `buoy` with no diagnostic. This is the single biggest support-burden item for a YAML-configured tool.
`priority: P1` · `labels: bug, backend, dx`

### BUG-25 — Disk gauge measures the container, mounts table measures the host
`_root_disk_percent()` uses `shutil.disk_usage("/")` (container rootfs) while `_all_mounts()` prefers `nsenter … df` (host). The headline gauge and the detail panel can legitimately disagree, and there's no way to point the gauge at a specific mount (e.g. `/mnt/storage`).
`priority: P1` · `labels: bug, backend`

### BUG-26 — Disk I/O only works on `nvme0n1`, `sda`, or `mmcblk0`
`_disk_io()` hardcodes `parts[2] in ("nvme0n1", "sda", "mmcblk0")`. Every VM (`vda`/`xvda`), second SATA disk (`sdb`), or additional NVMe reports `0 GB` read/write. Enumerate real block devices and sum, or make it configurable.
`priority: P1` · `labels: bug, backend`

### BUG-27 — NVMe panel hardcodes `/dev/nvme0n1`
`_nvme_smart()` only ever probes disk 0, so multi-NVMe hosts show one drive. (The `smart_disk` *plugin* does scan properly — the core gauge should reuse that logic instead of duplicating it.)
`priority: P2` · `labels: bug, backend`

### BUG-28 — Temperature reads only `thermal_zone0`
`/sys/class/thermal/thermal_zone0/temp` is the CPU on a Pi but is frequently `acpitz` or a wireless/battery zone on x86 — so the gauge is wrong or `0` on many hosts. Prefer `hwmon`/`coretemp`/`k10temp`, fall back to scanning zones by `type`, and render "n/a" instead of `0 °C` when unavailable.
`priority: P1` · `labels: bug, backend`

### BUG-29 — Memory "used" overstates usage
`used = MemTotal - MemFree - Buffers - Cached` ignores `SReclaimable` and `Shmem`, so buoy reports more used memory than `free`/`htop`. Use `MemTotal - MemAvailable` for the headline and keep the breakdown in detail.
`priority: P1` · `labels: bug, backend`

### BUG-30 — Memory detail has a permanently empty process list
`_read_memory_detail` returns `"top_processes": []` with the comment *"populated by `_top_processes_by("mem")`"* — nothing ever calls it for memory. The CPU panel has a process table; the memory panel silently has none.
`priority: P2` · `labels: bug, backend`

### BUG-31 — Every `/api/stats` blocks 100 ms sampling CPU
`_read_cpu()` reads `/proc/stat`, `await asyncio.sleep(0.1)`, reads again. That's a 100 ms floor on `/api/stats`, `/metrics`, and each stats-loop tick — paid twice per interval because of BUG-8. Keep the previous sample in the collector and compute the delta across cycles.
`priority: P2` · `labels: bug, backend, performance`

### BUG-32 — `os.cpu_count()` ignores cgroup CPU limits
Core count (and the `load_1 > cores` warn threshold in `detail.js`) reflects the host, not the container's quota. Read `cpu.max` / `cpu.cfs_quota_us`.
`priority: P3` · `labels: bug, backend`

### BUG-33 — Non-Linux returns all zeros instead of degrading
`SystemCollector._fallback_stats` returns `cpu: 0, mem_used: 0.0, temp: 0, uptime: 0` on macOS. SPEC §8.1 promised *"partial metrics via psutil-like fallbacks (for local dev)"*. A contributor running `python -m buoy` on a Mac sees a dashboard that looks broken. At minimum surface an explicit "metrics unavailable on this platform" state.
`priority: P2` · `labels: bug, backend, dx`

### BUG-34 — `measure_latency()` polls peers sequentially
A plain `for peer in peers:` loop with `await` inside — up to 6 s (`tailscale ping`) + 4 s (HTTP fallback) **per peer**, serially, inside `_latency_loop` which ticks every `fleet_interval` (default 15 s). With 3+ offline peers the loop overruns its own interval. `collect()` right above it already uses `asyncio.gather`.
`priority: P1` · `labels: bug, backend`

---

## Bugs — plugin system

### BUG-35 — Plugin env overrides are always strings, so booleans silently invert
`resolve_plugin_env` writes raw `os.environ` values with no coercion, though schemas declare `boolean`/`number` types. Reproduced:

```
BUOY_PLUGIN_PORTAINER_VERIFY_SSL=false
resolved = {'verify_ssl': 'false', 'endpoint_id': '3'}
verify_ssl is 'false' (str) -> `if not verify_ssl` is False => TLS verify stays ON
```

Any plugin knob that isn't a string is unsettable-or-wrong via env: `verify_ssl`, `endpoint_id`, `queue_warn_threshold`, `interval_hours`, `warn_days`, `critical_days`, `stale_seconds`, `sync_max_age_hours`. Coerce against `config_schema` types (#73 did this for the `BUOY_PLUGINS_BUILTIN_*` path — this is the parallel `BUOY_PLUGIN_*` path from #72).
`priority: P1` · `labels: bug, plugins, backend`

### BUG-36 — `refresh.plugins_interval` is a no-op
Documented in `buoy.yaml.example` as "Plugin data refresh", but `_collect_loop` uses `plugin.manifest.refresh_interval` exclusively; the config value only affects how often the *browser* re-fetches cached data. Either honour it as a global default/override or document it as frontend-only.
`priority: P2` · `labels: bug, plugins, docs`

### BUG-37 — `collect_all_now()` doesn't collect
Despite the name, it returns `_latest_data` and skips any plugin absent from that dict. A plugin whose first `collect()` is slow or failing is simply missing from `/api/plugins` (no card, no error) rather than showing as pending/errored.
`priority: P2` · `labels: bug, plugins`

### BUG-38 — `_find_plugin_class` can pick the wrong class
`inspect.getmembers` returns members sorted by name, and the filter is only `issubclass(obj, Plugin) and obj is not Plugin`. Any plugin module that imports another concrete plugin (or defines a shared base) loads the alphabetically-first class. Also add `obj.__module__ == module.__name__` and prefer an explicit marker.
`priority: P2` · `labels: bug, plugins`

### BUG-39 — `Plugin.config` is a mutable class attribute
`class Plugin: config: dict[str, Any] = {}` — shared across all subclasses until `configure()` rebinds it. Any plugin that mutates `self.config` before/without `configure()` writes into the shared default. Move to `__init__`.
`priority: P3` · `labels: bug, plugins`

### BUG-40 — Demo mode doesn't stub plugins
The plugin loader has no `demo_mode` awareness — collectors are stubbed, plugins are not. SPEC §5.4: *"Plugins return sample data."* Today `--demo` with any plugin enabled makes real outbound calls to GitHub/Immich/Proxmox/etc. and renders errors, which undermines the "screenshots and evaluation" use case.
`priority: P2` · `labels: bug, plugins, demo`

---

## Bugs — packaging, deploy, ops

### BUG-41 — `pip install buoy` ships no frontend
The built wheel contains **zero** static files:

```
wheel: buoy-2.0.2-py3-none-any.whl
static/ files in wheel: NONE
```

No `[tool.hatch.build]` section, and `static/` lives outside `src/`. The `buoy` console script is declared in `pyproject.toml` and `_resolve_static_dir()` falls back to a source-tree-relative path that doesn't exist in site-packages, so `/` returns *"index.html not found"* (500). Only the Docker image works, because the Dockerfile copies `static/` separately. Move assets under `src/buoy/static/` (or add `force-include`) and resolve via `importlib.resources`.
`priority: P1` · `labels: bug, packaging`

### BUG-42 — `speedtest-cli` is a hard dependency for an opt-in plugin
`pyproject.toml` lists `speedtest-cli>=2.1,<3.0` in top-level `dependencies`, so every install and the base image pay for a plugin that is disabled by default — and `speedtest-cli` has been effectively unmaintained since 2021 on Ookla's legacy endpoints. Move to `[project.optional-dependencies] speedtest`, or shell out to `librespeed-cli`/official `speedtest` if present and mark the plugin unavailable otherwise.
`priority: P1` · `labels: bug, packaging, plugins`

### BUG-43 — `/metrics` is always exposed, regardless of the plugin
`api_metrics` is registered unconditionally and its own docstring says *"if prometheus_exporter plugin is enabled"*. Confirmed serving with the plugin disabled:

```
GET /metrics -> 200
# HELP buoy_cpu_percent CPU usage percentage
```

It's also unauthenticated (not in `PROTECTED_PATHS`) and triggers a full collector run — an unmetered, un-rate-limited scrape endpoint on every install. Gate on the plugin and allow requiring auth.
`priority: P1` · `labels: bug, backend, plugins`

### BUG-44 — Prometheus output isn't escaped and loses uptime precision
`format_metrics` interpolates `host="{hostname}"` with no escaping of `"`, `\`, or newlines (a node name from config can produce an unparseable exposition), and `buoy_uptime_seconds` is reconstructed from `uptime_h*3600 + uptime_m*60` — minute-granularity — when `/proc/uptime` has the exact value.
`priority: P2` · `labels: bug, plugins`

### BUG-45 — Absolute paths break reverse-proxy sub-path hosting
Every asset and fetch is root-absolute (`/static/...`, `/api/...`, `/ws`). Serving buoy at `https://host/buoy/` behind Caddy/Traefik/NPM yields a blank page. Add a `network.base_path` (or derive from `<base href>` / `root_path`) and make the JS build URLs relative.
`priority: P1` · `labels: bug, frontend, backend`

### BUG-46 — No proxy-header handling: shared rate-limit bucket and broken tailnet detection
Rate limiting keys on `request.client.host` and Tailscale detection is `".ts.net" in request.headers.get("host", "")`. Behind any reverse proxy, every client collapses into one rate-limit bucket (trivial accidental DoS of your own UI) and the tailnet URL rewrite misfires when the proxy rewrites `Host`. Add uvicorn `--proxy-headers` / `ProxyHeadersMiddleware` with a trusted-proxy list.
`priority: P1` · `labels: bug, backend`

### BUG-47 — No logging: 9 `print()`s and 17 silent `except Exception: pass`
```
files importing logging: 0
print( calls          : 9
```
`_stats_loop`, `_latency_loop`, `_image_update_loop`, all the collectors and webhook dispatch swallow every exception with no record. When the dashboard shows zeros there is literally no diagnostic trail, and `--dev` only raises *uvicorn's* log level. Adopt `logging` with a configurable level, log caught exceptions at `debug`/`warning`, and keep `print` only for the startup banner.
`priority: P1` · `labels: bug, backend, dx`

### BUG-48 — Google Fonts fetched from a CDN
`index.html` preconnects to `fonts.googleapis.com` and loads JetBrains Mono + Outfit. That contradicts the README's *"Zero external dependencies"*, breaks typography on air-gapped/offline homelabs (a core audience), and leaks a request per page load. Self-host the two woff2 files in `static/fonts/`.
`priority: P1` · `labels: bug, frontend, privacy`

### BUG-49 — Container logs merge stdout+stderr and lose ordering
`get_logs` does `(stdout + "\n" + stderr).strip().split("\n")[-tail:]` — stderr is appended wholesale after stdout, so interleaving is destroyed and a chatty stderr can push all stdout out of the tail. `--timestamps` is requested but never parsed for sorting.
`priority: P2` · `labels: bug, backend`

### BUG-50 — Coverage blind spots in the highest-risk modules
```
src/buoy/collectors/system.py    0%    (132 statements, 0 covered)
src/buoy/__main__.py             0%
src/buoy/collectors/disk.py     26%
src/buoy/collectors/docker.py   39%
src/buoy/server.py              39%
TOTAL                           73%
```
`system.py` — every `/proc` parser, the CPU delta, the memory formula, temperature — has **no tests at all**, which is precisely where BUG-28/29/31 live. Add `/proc` fixture-based tests and a coverage floor in CI.
`priority: P1` · `labels: test, ci`

---

## Features — core

### FEAT-1 — GPU collector (NVIDIA / AMD / Intel)
No GPU support anywhere. Utilisation, VRAM, temperature, power, and per-process usage via `nvidia-smi --query-gpu`, `amdgpu` sysfs, `intel_gpu_top`. High demand: Jellyfin/Plex transcoding, Frigate, Immich ML, Ollama. Likely the single most requested missing metric for this audience.
`labels: enhancement, backend`

### FEAT-2 — Network interface throughput collector
There is no NIC bandwidth metric at all — `network.py` is purely peer polling. Add rx/tx rates per interface from `/proc/net/dev` (+ errors/drops), a gauge, and a sparkline.
`labels: enhancement, backend, frontend`

### FEAT-3 — Trend charts in the UI
`/api/history/{metric}?period=1h|6h|12h|24h` is fully implemented and **completely unused by the frontend** (SPEC Phase 5.3). Add 24 h charts in the gauge detail panels with a period selector — the highest-value-per-effort item in the repo.
`labels: enhancement, frontend`

### FEAT-4 — Configurable alert thresholds
`DEFAULT_THRESHOLDS` is a module constant with a comment saying *"can be overridden in config in the future"*. Add an `alerts:` config block (per-metric warn/crit/duration, enable/disable, per-mount disk rules, container-down and plugin-status alerts) plus hysteresis so a metric oscillating at the boundary doesn't flap.
`labels: enhancement, backend`

### FEAT-5 — Real notification channels
Once BUG-14 is fixed, ship first-class targets: Discord, Slack, ntfy, Gotify, Telegram, generic webhook, SMTP, and Apprise as an umbrella. Add per-channel severity filters, rate limiting/dedup, and a "test notification" button.
`labels: enhancement, backend`

### FEAT-6 — Alert history endpoint + UI
`AlertEngine.alert_history` exists and is unreachable. Add `GET /api/alerts` (active + recent) and a collapsible panel with a timeline.
`labels: enhancement, backend, frontend`

### FEAT-7 — Container start / stop / recreate
SPEC §5.2 lists `POST /api/container/{name}/stop`; only `restart` exists. Add start/stop, and a "pull + recreate" action to pair with the image-update badges from #63 (biggest workflow win: see update → apply it).
`labels: enhancement, backend, frontend`

### FEAT-8 — Live log streaming
Replace the static 30-line tail with WebSocket `docker logs --follow`, configurable tail depth, follow toggle, and client-side search/filter.
`labels: enhancement, backend, frontend`

### FEAT-9 — Per-container stats and health in the container list
`collect_summary` returns only `[{"name": ...}]`. Add health (`{{.State.Health.Status}}`), status, and per-container CPU/mem from a single batched `docker stats --no-stream`, so unhealthy/stopped containers are visible without clicking each one.
`labels: enhancement, backend, frontend`

### FEAT-10 — Static service entries and bookmarks
Services can *only* come from Docker discovery, so a NAS, router, printer, VM, or bare-metal service can't appear on the dashboard. Add a `services.static:` list (name/icon/desc/url/health-check) — table stakes versus Homepage/Dashy, which the README names as competitors.
`labels: enhancement, backend, frontend`

### FEAT-11 — Group and order services by Compose project
Read `com.docker.compose.project` / custom labels to group cards into stacks, with pinning and manual ordering.
`labels: enhancement, backend, frontend`

### FEAT-12 — Surface degraded/unavailable subsystems
`DockerCollector.is_available()` is dead code and nothing tells the user *why* a panel is empty. Add a health/capability summary (docker socket, nsenter, smartctl, /proc access, each plugin) to `/api/health` and a discreet UI indicator.
`labels: enhancement, backend, frontend`

### FEAT-13 — `buoy doctor` diagnostic command
One command that validates the config, probes the Docker socket, nsenter, smartctl, thermal zones, block devices, peers, and each enabled plugin, then prints pass/fail with the exact fix. Would deflect most "why is X empty" issues.
`labels: enhancement, dx, cli`

### FEAT-14 — Config schema, `buoy config print`, and generated docs
Companion to BUG-24: publish a JSON Schema (editor autocomplete via `# yaml-language-server: $schema=`), warn on unknown keys, and generate the config reference from the schema so docs can't drift.
`labels: enhancement, dx, docs`

### FEAT-15 — ZFS / btrfs / mdadm pool health
`snapraid` and `smart_disk` are covered, but ZFS is the most common homelab array and is entirely absent: pool state, scrub age, capacity, read/write/cksum errors, resilver progress.
`labels: enhancement, plugins`

### FEAT-16 — Multi-disk gauges and configurable primary mount
Let users choose which mount drives the disk gauge and pin extra mounts as their own gauges (pairs with BUG-25/26).
`labels: enhancement, backend, frontend`

### FEAT-17 — Remote Docker hosts, Swarm, and Podman
Support `DOCKER_HOST` (TCP+TLS/ssh) so one buoy can watch a socket-less host, and Podman (`podman` CLI / socket) — a large slice of the self-hosting audience the current hard `docker` dependency excludes.
`labels: enhancement, backend`

### FEAT-18 — History retention config + downsampling
`RETENTION_SECONDS = 86400` is hardcoded. Make retention configurable and add rollup tables (1 m → 5 m → 1 h) for 7/30-day trends without unbounded growth.
`labels: enhancement, backend`

### FEAT-19 — PWA / installable mobile app
Add a web manifest, maskable icons, and a service worker for offline shell + last-known values. A tailnet landing page is a phone-first surface.
`labels: enhancement, frontend`

### FEAT-20 — Theme polish
Persist the `t` toggle (currently lost on reload), honour `prefers-color-scheme`, apply `theme.custom` (BUG-6), add `body.night-mode` overrides to `light.css` (it has none), and ship one or two more presets (solarized, nord, high-contrast).
`labels: enhancement, frontend`

### FEAT-21 — Status-page / kiosk mode
A read-only public view (no container controls, no config, no debug endpoints) plus a kiosk/TV layout and an embeddable single-node widget for other dashboards.
`labels: enhancement, frontend, backend`

### FEAT-22 — Fleet depth
Make `/api/fleet` the UI's source of truth (see BUG-51 below), add a fleet rollup header (worst-of across nodes), per-node uptime/SLA %, an aggregated all-nodes container view, and optional Wake-on-LAN for peers.
`labels: enhancement, backend, frontend`

### FEAT-23 — OIDC / forward-auth support
Homelabbers front everything with Authelia/Authentik/Cloudflare Access. Support trusted-header auth (`Remote-User`) and/or OIDC so buoy can participate in existing SSO instead of its own token.
`labels: enhancement, security, backend`

---

## Features — new plugins

Grouped by audience value. Each follows the existing single-file builtin pattern (`src/buoy/plugins/builtin/<id>.py` + `frontend_js()` + tests + `buoy.yaml.example` entry).

**Tier 1 — broadest homelab appeal**

| ID | Plugin | Shows |
|---|---|---|
| PLG-1 | **UPS / NUT** | Battery %, load, runtime remaining, on-battery state, last transfer reason |
| PLG-2 | ***arr stack** (Sonarr/Radarr/Prowlarr/Bazarr) | Queue depth, wanted/missing, indexer + system health warnings |
| PLG-3 | **Download clients** (qBittorrent/Transmission/SABnzbd/NZBGet) | Active/queued, speeds, ratio, disk space |
| PLG-4 | **Home Assistant** | Entity/automation counts, unavailable entities, failed automations, HA version updates |
| PLG-5 | **Plex** | Sessions, transcoding, library counts (Jellyfin already exists) |
| PLG-6 | **Backup repos** (Restic/Borg/Kopia/Duplicati) | Last snapshot age, repo size, check status, per-target freshness |
| PLG-7 | **Reverse proxy** (Traefik/Caddy/NPM) | Router/route count, 5xx rate, cert status per host |
| PLG-8 | **Nextcloud** | Users, storage, app updates, background job status |

**Tier 2 — strong niches**

| ID | Plugin | Shows |
|---|---|---|
| PLG-9 | **Unifi Controller** | APs up, clients, WAN status, firmware updates |
| PLG-10 | **OPNsense / pfSense** | WAN state, gateway latency, firewall/IDS alerts |
| PLG-11 | **Frigate** | Camera states, detections/hr, GPU/TPU inference speed |
| PLG-12 | **Gitea / Forgejo** | Repos, open PRs, Actions queue/failures |
| PLG-13 | **Authentik / Authelia** | Active sessions, failed logins, locked accounts |
| PLG-14 | **Grafana / Alertmanager** | Firing alerts by severity |
| PLG-15 | **Syncthing** | Folder sync %, out-of-sync items, connected devices |
| PLG-16 | **Paperless-ngx** | Docs, inbox count, OCR queue |
| PLG-17 | **Ollama / LocalAI** | Loaded models, VRAM, request rate |
| PLG-18 | **k3s / Kubernetes** | Node ready state, pods pending/crashlooping, PVC usage |
| PLG-19 | **Vaultwarden** | Users, orgs, last backup |
| PLG-20 | **Fail2ban / CrowdSec** | Active bans, recent decisions, top offenders |
| PLG-21 | **Databases** (Postgres/MySQL/Redis) | Connections, replication lag, slow queries, memory/evictions |
| PLG-22 | **MinIO / S3** | Bucket count/size, quota headroom, healing state |
| PLG-23 | **Cloudflare Tunnel** | Connector health, active connections |
| PLG-24 | **n8n** | Executions, failures, waiting workflows |
| PLG-25 | **Audiobookshelf / Navidrome / Calibre-web / Mealie** | Library counts, active streams |

### PLG-26 — Pi-hole v6 support in `dns_filter`
The plugin targets Pi-hole **v5**'s `/admin/api.php?summaryRaw`, which v6 **removed** in favour of an authenticated REST API (`/api/auth` → `/api/stats/summary`). PR #65 flagged this as a follow-up and it was never filed. Anyone on a current Pi-hole gets `error`. Add v6 (session auth + new endpoints) with version auto-detection.
`priority: P1` · `labels: bug, plugins`

---

## Features — plugin platform

### PP-1 — User plugins can't be configured at all
`_load_user_plugins` calls `instance.configure({})` with a comment *"User plugins don't have config entries (yet)"*. So a dropped-in plugin has no way to receive a URL, token, or threshold — the documented extension story is unusable for anything needing config. Add `plugins.user.<id>: {...}` resolved through the same env-override path as builtins.
`priority: P1` · `labels: enhancement, plugins`

### PP-2 — Enforce `config_schema` (required fields, types, defaults)
`config_schema` is declared by every builtin and only ever used for env-key discovery. Validate it: apply defaults, coerce types (fixes BUG-35), and report missing required fields as a `disabled` card with an actionable message instead of a generic error.
`priority: P1` · `labels: enhancement, plugins`

### PP-3 — Expandable plugin cards
Already filed as **#11** and still open — rich plugins (cron table, Loki errors, GitHub notifications) are cramped in the fixed card. Keep.
`labels: enhancement, frontend, plugins`

### PP-4 — Declarative plugin rendering to replace `new Function()` + raw HTML
Today plugins ship JS strings that are `eval`'d and interpolate their own HTML — the root cause of SEC-3 and the blocker for a CSP (SEC-6). Introduce a small declarative panel spec (rows, badges, bars, tables, sparklines) rendered by trusted, escaping frontend code, with `frontend_js()` kept as a deprecated escape hatch. Unlocks: safe third-party plugins, a strict CSP, and consistent styling.

**Schedule this together with SEC-3 and SEC-6** — see [Cross-cutting themes §1](#cross-cutting-themes). Spot-escaping SEC-3 now and doing PP-4 later means escaping the same data twice and then deleting the first implementation.
`priority: P1` · `labels: enhancement, plugins, security, frontend`

### PP-5 — Plugin distribution via entry points + `buoy plugin` CLI
Support `importlib.metadata` entry points so plugins can be pip-installed, add `buoy plugin list/info/install`, and a curated registry in the wiki.
`labels: enhancement, plugins, dx`

### PP-6 — Per-plugin refresh interval override in config
`manifest.refresh_interval` is fixed at author time; operators should be able to tune it per instance (rate limits, slow endpoints). Pairs with BUG-36.
`labels: enhancement, plugins`

### PP-7 — Expose plugin health and last error
Include `last_collect_at`, `last_error`, `consecutive_failures`, and configured-vs-loaded state in `/api/plugins`, and show a "last updated Xm ago" + error affordance on each card, so a stale panel is distinguishable from a healthy one.
`labels: enhancement, plugins, frontend`

---

## Docs & project health

### DOC-1 — Built-in plugin docs cover a fraction of what ships
22 builtin plugins exist. Measured:

- **README table lists 10** — missing: `actual_budget`, `backup_status`, `cron_health`, `dns_filter`, `immich`, `journal_errors`, `prometheus_exporter`, `proxmox`, `speedtest`, `systemd_health`, `trigger_dev`, `uptime_kuma`, `wireguard`.
- **`buoy.yaml.example` documents 16** — missing entirely: `backup_status`, `cron_health`, `journal_errors`, `prometheus_exporter`, `speedtest`, `systemd_health`.

Six shipped plugins are undiscoverable without reading source. Add a CI check that every `manifest.id` appears in both files.
`priority: P1` · `labels: docs, ci`

### DOC-2 — CHANGELOG is out of order and missing the last release
`2.0.0` is listed **above** `2.0.2` and `2.0.1`; there is no `2.1.0` entry despite `__version__ = "2.1.0"` and ~30 merged PRs since 2.0.2 (plugins, image updates, container history, fleet alerts); "Unreleased — _Nothing yet._" trails the file. Reorder newest-first, write 2.1.0 from the merged PRs, and consider `release-drafter`/`git-cliff` so this can't drift.
`priority: P1` · `labels: docs`

### DOC-3 — No screenshots and no `docs/`
SPEC §13.2 calls for `docs/screenshots/` (normal, warn, dark, light, demo) and the README's feature claims ("beautiful by default") are unsupported by a single image. `docs/` doesn't exist and all doc links point at the wiki. Generate screenshots from `--demo` and keep authored docs in-repo (wiki can mirror).
`priority: P1` · `labels: docs`

### DOC-4 — Missing standard repo files
`CODE_OF_CONDUCT.md`, `SECURITY.md`, `.github/ISSUE_TEMPLATE/` (bug/feature/plugin-request), `.github/PULL_REQUEST_TEMPLATE.md`, `.github/dependabot.yml`, `.pre-commit-config.yaml` — all absent. Every one is an unchecked box in SPEC §13.1.
`priority: P2` · `labels: docs, ci`

### DOC-5 — CI gaps
Current CI runs pytest + ruff + an amd64 image build. Add: coverage floor (`--cov-fail-under`), arm64 build verification (release publishes arm64 that CI never builds), a Docker smoke test (`docker run --demo` → assert `/api/health` 200 and `/` serves HTML — would have caught BUG-41), a wheel-contents check, Playwright smoke tests against demo mode, CodeQL, and link checking.
`priority: P1` · `labels: ci, test`

### DOC-6 — SPEC.md is stale and now misleading
Header still says `Status: Planning`, dated 2026-06-24; §4.3 lists 12 plugins (22 ship); §14 phase estimates and the §13.1 launch checklist are unchecked though done; §12 describes a v1→v2 migration that's finished. It's cited as the reference doc for issues #1–#11. Either fold the still-relevant parts (threat model, plugin protocol, API contract) into `docs/architecture.md` and archive the rest, or add a banner marking it historical.
`priority: P2` · `labels: docs`

### DOC-7 — Hosted demo instance
SPEC §6.6. `--demo` needs no infrastructure, so a Fly.io/Railway deploy of demo mode plus a "Live demo" README badge is cheap and converts far better than a screenshot.
`labels: docs, enhancement`

### DOC-8 — Grafana dashboard JSON for the Prometheus exporter
The exporter ships without a dashboard. Provide `docs/grafana/buoy.json` (plus recording/alert rule examples) — a common ask for anything exposing `/metrics`.
`labels: docs, enhancement`

### DOC-9 — Non-Docker and orchestrated deployment paths
Only Docker Compose is documented. Add: a systemd unit + `pip install` path (depends on BUG-41), a Helm chart / plain k8s manifests, an Ansible role, and an explicit unprivileged deployment matrix stating which metrics each privilege level gives up.
`labels: docs, enhancement`

---

## Housekeeping

### BUG-51 — Regression: server-side fleet aggregation was merged, then lost
PR #70 ("server-side peer latency in fleet grid") states it *"rewrote `refreshFleet()` to call `/api/fleet` once and read server-provided `latency_ms`"*. Current `fleet.js` does the opposite — it iterates `otherNodes` and `fetch(node.url + '/api/stats')` from the **browser**, and only calls the backend for `/api/fleet/{peer}/latency-history`. So `/api/fleet` and `NetworkCollector.collect()` are unreachable from the UI, and the browser must reach every peer directly (breaks with per-peer auth, mixed content when buoy is HTTPS and a peer is HTTP, and any peer the browser can't route to). Likely clobbered by a later merge. Restore server-side aggregation and add a test asserting the UI's fleet data path.
`priority: P1` · `labels: bug, frontend, backend, regression`

### HK-1 — Close stale open issues
- **#3** (peer latency in fleet grid) — delivered by #19/#44/#70. Latency only renders when `features.history` is on; either close or re-scope to "show latency without history enabled".
- **#9** (confirm-before-restart) — implemented in `detail.js` `restartContainer()` via PR #13. Close.
- **#31** (`/api/config` for debugging) — implemented as `/api/config/debug` via PR #67. Close (and see SEC-4).

Still valid and open: **#10** (cron backup log tail), **#11** (expandable plugin cards).
`labels: housekeeping`

### HK-2 — Adopt labels used above
Existing labels: `bug`, `enhancement`, `frontend`, `backend`, `plugins`, `needs-verification`. Suggest adding: `security`, `docs`, `ci`, `test`, `packaging`, `performance`, `dx`, `regression`, `good first issue`, and `priority: P0…P3`.
`labels: housekeeping`

### HK-3 — Good first issues
Low-risk, well-bounded picks for new contributors: BUG-1 (version), BUG-2/3 (tier/role), BUG-5 (`%%`), BUG-7 (CSS vars), BUG-11 (uptime), BUG-21 (unbounded lists), DOC-1 (plugin docs), DOC-2 (changelog), DOC-4 (repo files).
`labels: housekeeping, good first issue`

### HK-4 — Audit history for other lost merges
BUG-51 shows a merged change (PR #70) being silently reverted by a later merge with no test to catch it. This audit ran against a **shallow clone** and could not check whether it happened elsewhere. Worth running `git log -p` over the frontend files touched by the most parallel-PR-heavy period (`static/js/fleet.js`, `detail.js`, `plugins.js`, `gauges.js` — roughly #44–#74, many of which were agent-generated and merged in bursts) to confirm nothing else was clobbered.

Two findings in this audit are consistent with lost work rather than never-written work: **SEC-9** (an `authedFetch()` with token prompt was written in PR #18, which was closed unmerged in favour of PR #13, which has no auth handling) and **BUG-4** (`detail.js` reads `d.started_at`/`d.image_age`, which look like field names from an earlier backend shape). If merges are being lost, that process gap outranks any single bug in this document.
`priority: P1` · `labels: housekeeping, ci`

---

## Suggested sequencing

Grouped so that items sharing a root cause land together (see [Cross-cutting themes](#cross-cutting-themes)).

### Wave 1 — stop the bleeding (2.1.1 / 2.2.0)

*Security, silent data loss, and the "advertised but broken" set. Nothing here is a feature; all of it is a promise the code doesn't keep.*

- **Auth, end to end (theme 2):** SEC-1 (fail-open), SEC-4 (public debug endpoint), SEC-5 (rate limiting), SEC-7 (non-ASCII 500), SEC-9 (frontend login overlay). Ship SEC-1 and SEC-9 in the *same* release or you convert a false sense of security into a locked-out dashboard.
- **Plugin rendering, once (theme 1):** SEC-3 + SEC-6 + PP-4 together.
- **Silent data loss:** BUG-13 (history needs websocket), BUG-14 (webhooks never worked), BUG-19 (orphaned subprocesses), BUG-20 (broadcast `RuntimeError`).
- **Broken distribution:** BUG-41 (empty wheel), BUG-42 (`speedtest-cli` hard dep), BUG-43 (`/metrics` always on).
- **Observability first (theme 3):** BUG-47 (adopt `logging`) — do this early; it makes everything after it debuggable.
- **Trivially visible:** BUG-1 (version drift), BUG-2 (tier), BUG-3 (role), BUG-4 (Started/Image Age), BUG-5 (`%%`), BUG-7 (undefined CSS vars), BUG-11 (uptime at 24h), SEC-2 (CORS/CSRF), SEC-8 (`verify=False`).
- **Process:** HK-4 (check for other lost merges) before building on top of `fleet.js`, plus BUG-51.

### Wave 2 — make the numbers trustworthy

*Theme 4 in full, plus the tooling that stops it recurring.*

- **Accuracy:** BUG-25 (container vs host disk), BUG-26 (disk I/O device list), BUG-27 (multi-NVMe), BUG-28 (thermal zone), BUG-29 (memory formula), BUG-32 (cgroup quota), BUG-33 (non-Linux degradation).
- **Cost (theme 5):** BUG-8 first (halves the rest), then BUG-9 (`docker ps` cache), BUG-31 (CPU sampling), BUG-22 (SQLite on the loop), BUG-34 (sequential peer latency).
- **Diagnosability (theme 3):** FEAT-13 (`buoy doctor`), BUG-24 + FEAT-14 (config schema), FEAT-12 (capability reporting), PP-7 (plugin health), BUG-37.
- **Guardrails:** BUG-50 (test `system.py`), DOC-5 (coverage floor, arm64, Docker smoke test, CSS-var check), DOC-1 (plugin doc CI check), DOC-2 (CHANGELOG).
- **Correctness cleanup, batchable:** BUG-17 (config dict mutation), BUG-18 (lifespan + teardown), BUG-30 (empty memory process list), BUG-44 (Prometheus escaping), BUG-48 (self-host fonts).

### Wave 3 — the features people actually ask for

*Ordered by value-per-effort, not by size.*

1. **FEAT-3 — trend charts.** The API is already built, tested, and unused. Highest return in the document.
2. **FEAT-1 — GPU collector.** The largest outright absence for this audience.
3. **FEAT-2 — NIC throughput.** The second largest.
4. **Alerting that works:** FEAT-4 (configurable thresholds), FEAT-5 (real channels), FEAT-6 (history + UI), BUG-12 (render on load), BUG-15 (warn→crit escalation), BUG-16 (blocking webhook I/O).
5. **Container workflow:** FEAT-7 (start/stop/recreate — pairs with the #63 update badges), FEAT-9 (per-container health/stats), FEAT-8 (live logs), BUG-49 (log ordering).
6. **Service model:** FEAT-10 (static/non-Docker entries — table stakes vs Homepage/Dashy), BUG-10 (`hidden` matching), FEAT-11 (Compose grouping).
7. **Plugins — tier 1:** PLG-26 (Pi-hole v6 — arguably a bug), PLG-1 (UPS/NUT), FEAT-15 (ZFS), PLG-2 (*arr), PLG-3 (download clients), PLG-4 (Home Assistant), PLG-5 (Plex), PLG-6 (backup repos), PLG-7 (reverse proxy), PLG-8 (Nextcloud).

**Plugins — tier 2 (PLG-9 … PLG-25) are deliberately unscheduled.** They're an open backlog and the ideal `good first issue` / external-contribution surface — each is a single self-contained file following the existing builtin pattern. Do **PP-1/PP-2/PP-4** (Wave 4) before soliciting them, so contributors write against a schema-validated, escaping-by-default plugin API rather than 17 more copies of the hand-rolled HTML pattern.

### Wave 4 — platform

- **Plugin platform:** PP-1 (user-plugin config), PP-2 (enforce `config_schema`, fixes BUG-35), PP-5 (entry points + CLI), PP-6, BUG-36, BUG-38, BUG-39, BUG-40 (demo stubs), PP-3 (#11).
- **Deployment reach:** FEAT-17 (remote Docker / Podman), BUG-45 (sub-path hosting), BUG-46 (proxy headers), DOC-9 (systemd / Helm / Ansible), SEC-10 (unprivileged profile).
- **Surface:** FEAT-19 (PWA), FEAT-20 (theme polish incl. BUG-6), FEAT-21 (status page), FEAT-22 (fleet depth), FEAT-23 (SSO / forward-auth).
- **Scale:** FEAT-18 (retention + downsampling), FEAT-16 (multi-disk gauges), BUG-21 (unbounded collections), BUG-23 (env coercion).
- **Project:** DOC-3 (screenshots), DOC-4 (repo files), DOC-6 (SPEC decision), DOC-7 (hosted demo), DOC-8 (Grafana dashboard), SEC-11 (Dependabot/CodeQL), HK-1/HK-2/HK-3.
