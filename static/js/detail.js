/**
 * Detail module — expandable panels for CPU, memory, disk, containers.
 */

import { authedFetch } from './auth.js';
import { escapeHtml } from './escape.js';
import { openLogViewer } from './logs.js';
import { apiUrl } from './paths.js';
import { formatRate } from './format.js';

let currentDetail = null;
let buoyConfig = null;

/**
 * detail.js is initialized before buoy.js finishes fetching /api/config
 * (see initDetail() in buoy.js's init()), so the log viewer — which needs
 * auth/logs/features to build its ticket + tail-depth flow — is handed the
 * config once it's available rather than fetching its own copy.
 */
export function setDetailConfig(config) {
  buoyConfig = config;
}

export function initDetail() {
  document.querySelectorAll('.gauge[data-detail]').forEach(gauge => {
    gauge.addEventListener('click', () => openDetail(gauge.dataset.detail));
    gauge.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(gauge.dataset.detail); }
    });
  });

  // Delegated listener: detail-close buttons are re-rendered into
  // #detail-content on every openDetail() call, so bind once on the
  // stable panel ancestor rather than after each render.
  document.getElementById('detail-panel')?.addEventListener('click', (e) => {
    if (e.target.closest('.detail-close')) closeDetail();
  });
}

function closeDetail() {
  const panel = document.getElementById('detail-panel');
  panel.classList.remove('open');
  document.querySelectorAll('.gauge.expanded').forEach(g => g.classList.remove('expanded'));
  currentDetail = null;
}

async function openDetail(type) {
  const panel = document.getElementById('detail-panel');
  const content = document.getElementById('detail-content');

  if (currentDetail === type) { closeDetail(); return; }

  document.querySelectorAll('.gauge.expanded').forEach(g => g.classList.remove('expanded'));
  const gauge = document.querySelector(`.gauge[data-detail="${type}"]`);
  if (gauge) gauge.classList.add('expanded');

  content.innerHTML = '<div style="padding:1rem;color:var(--text-dim);font-size:0.7rem">Loading...</div>';
  panel.classList.add('open');
  currentDetail = type;

  if (type === 'containers') {
    content.innerHTML = renderContainersDetail();
    return;
  }

  try {
    const r = await fetch(apiUrl('stats/detail'));
    if (!r.ok) throw new Error('Failed');
    const d = await r.json();

    switch (type) {
      case 'cpu': content.innerHTML = renderCpuDetail(d); break;
      case 'memory': content.innerHTML = renderMemoryDetail(d); break;
      case 'disk': content.innerHTML = renderDiskDetail(d); break;
      case 'network': content.innerHTML = renderNetworkDetail(d); break;
      case 'gpu': content.innerHTML = renderGpuDetail(d); break;
      default: content.innerHTML = '';
    }
  } catch (e) {
    content.innerHTML = '<div style="padding:1rem;color:var(--red);font-size:0.7rem">Failed to load details</div>';
  }
}

function renderCpuDetail(d) {
  const cpu = d.cpu || {};
  const loadColor = cpu.load_1 > (cpu.cores || 1) ? 'warn' : '';
  let html = `
    <div class="detail-header">
      <div class="detail-title">CPU — ${escapeHtml(cpu.model || 'unknown')}</div>
      <button class="detail-close">&#10005; close</button>
    </div>
    <div class="detail-grid">
      <div class="detail-stat"><div class="ds-label">Cores</div><div class="ds-value">${cpu.cores || 0}</div></div>
      <div class="detail-stat"><div class="ds-label">Load 1m</div><div class="ds-value ${loadColor}">${cpu.load_1 || 0}</div></div>
      <div class="detail-stat"><div class="ds-label">Load 5m</div><div class="ds-value">${cpu.load_5 || 0}</div></div>
      <div class="detail-stat"><div class="ds-label">Load 15m</div><div class="ds-value">${cpu.load_15 || 0}</div></div>
    </div>`;

  if (cpu.top_processes?.length) {
    html += `<div class="section-sub">Top processes (by CPU)</div>
    <table class="process-table"><thead><tr><th>PID</th><th>CPU%</th><th>MEM%</th><th>Command</th></tr></thead><tbody>`;
    cpu.top_processes.forEach(p => {
      html += `<tr><td>${escapeHtml(p.pid)}</td><td>${escapeHtml(p.cpu)}%</td><td>${escapeHtml(p.mem)}%</td><td>${escapeHtml(p.cmd)}</td></tr>`;
    });
    html += `</tbody></table>`;
  }
  return html;
}

function renderMemoryDetail(d) {
  const m = d.memory || {};
  const usedPct = m.total_mb > 0 ? ((m.used_mb / m.total_mb) * 100).toFixed(0) : 0;
  let html = `
    <div class="detail-header">
      <div class="detail-title">Memory — ${(m.total_mb / 1024).toFixed(1)} GB total</div>
      <button class="detail-close">&#10005; close</button>
    </div>
    <div class="detail-grid">
      <div class="detail-stat"><div class="ds-label">Used</div><div class="ds-value">${((m.used_mb||0) / 1024).toFixed(1)} GB</div></div>
      <div class="detail-stat"><div class="ds-label">Cached</div><div class="ds-value">${((m.cached_mb||0) / 1024).toFixed(1)} GB</div></div>
      <div class="detail-stat"><div class="ds-label">Available</div><div class="ds-value">${((m.available_mb||0) / 1024).toFixed(1)} GB</div></div>
      <div class="detail-stat"><div class="ds-label">Swap</div><div class="ds-value">${m.swap_used_mb||0}/${m.swap_total_mb||0} MB</div></div>
      <div class="detail-stat"><div class="ds-label">Utilization</div><div class="ds-value ${usedPct > 85 ? 'crit' : usedPct > 70 ? 'warn' : ''}">${usedPct}%</div></div>
    </div>`;
  return html;
}

function renderDiskDetail(d) {
  const disk = d.disk || {};
  let html = `
    <div class="detail-header">
      <div class="detail-title">Disk — Mounted Filesystems</div>
      <button class="detail-close">&#10005; close</button>
    </div>`;

  if (disk.mounts?.length) {
    disk.mounts.forEach(mnt => {
      const cls = mnt.pct >= 90 ? 'mount-bar-fill crit' : mnt.pct >= 75 ? 'mount-bar-fill warn' : 'mount-bar-fill';
      html += `<div class="mount-row">
        <span class="mount-path">${escapeHtml(mnt.mount || mnt.fs)}</span>
        <div class="mount-bar"><div class="${cls}" style="width:${mnt.pct}%"></div></div>
        <span class="mount-info">${escapeHtml(mnt.used)}/${escapeHtml(mnt.size)} (${mnt.pct}%)</span>
      </div>`;
    });
  }
  return html;
}

// Non-running/unhealthy containers sort to the top so they're visible
// without scrolling. Whitelisted lookups only — never derive a CSS class
// from a raw Docker string (health/state values reach us from the daemon).
const _HEALTH_DOT_CLASS = { unhealthy: 'unhealthy', starting: 'starting', healthy: '' };
const _STATE_DOT_CLASS = { running: '' };

function _dotClass(c) {
  if (c.health && Object.hasOwn(_HEALTH_DOT_CLASS, c.health)) return _HEALTH_DOT_CLASS[c.health];
  return Object.hasOwn(_STATE_DOT_CLASS, c.state) ? _STATE_DOT_CLASS[c.state] : 'stopped';
}

function _sortPriority(c) {
  if (c.health === 'unhealthy') return 0;
  if (c.state !== 'running') return 1;
  if (c.health === 'starting') return 2;
  return 3;
}

/**
 * Order containers problem-first: unhealthy, then non-running, then
 * starting, then everything else. Stable within each group.
 */
export function sortContainers(containers) {
  return [...containers]
    .map((c, i) => [c, i])
    .sort((a, b) => _sortPriority(a[0]) - _sortPriority(b[0]) || a[1] - b[1])
    .map(([c]) => c);
}

/**
 * Render one container row. Every daemon-sourced string (name, status,
 * cpu/mem) goes through escapeHtml; the dot's CSS class comes only from the
 * whitelists above, never from string-concatenating `health`/`state` directly.
 */
export function containerRowHtml(c) {
  const badge = _updateBadge(c.update_status);
  const cpu = c.cpu_pct ?? '--';
  const mem = c.mem_usage ?? '--';
  // History is only fetched for running containers (see renderContainersDetail) —
  // on a host with many one-shot/exited containers, firing one
  // /api/container/<name>/history request per row on open would self-inflict
  // 429s against the shared 60/60s rate limit. Omit data-ctr for the rest so
  // the fetch loop's selector skips them entirely.
  const uptimeAttr = c.state === 'running' ? ` data-ctr="${escapeHtml(c.name)}"` : '';
  return `<div class="ctr" data-ctr-name="${escapeHtml(c.name)}">` +
    `<div class="dot-sm ${_dotClass(c)}"></div>` +
    `<div class="ctr-name">${escapeHtml(c.name)}</div>` +
    `<div class="ctr-status" title="${escapeHtml(c.status || '')}">${escapeHtml(c.status || '')}</div>` +
    `<div class="ctr-metrics">${escapeHtml(cpu)} &middot; ${escapeHtml(mem)}</div>` +
    `<div class="ctr-uptime"${uptimeAttr}></div>${badge}</div>`;
}

function _containersHeaderText(containers) {
  const running = containers.filter(c => c.state === 'running').length;
  return `Containers (${running} running / ${containers.length} total)`;
}

function renderNetworkDetail(d) {
  const net = d.net || {};
  const interfaces = net.interfaces || [];
  let html = `
    <div class="detail-header">
      <div class="detail-title">Network — Interfaces${net.primary ? ` (primary: ${escapeHtml(net.primary)})` : ''}</div>
      <button class="detail-close">&#10005; close</button>
    </div>`;

  if (interfaces.length) {
    html += `<table class="process-table"><thead><tr><th>Interface</th><th>Down</th><th>Up</th><th>Rx Err/Drop</th><th>Tx Err/Drop</th></tr></thead><tbody>`;
    interfaces.forEach((iface) => {
      const rx = formatRate(iface.rx_bytes_per_sec);
      const tx = formatRate(iface.tx_bytes_per_sec);
      const hasErrors = (iface.rx_errors || 0) + (iface.tx_errors || 0) + (iface.rx_dropped || 0) + (iface.tx_dropped || 0) > 0;
      const errCls = hasErrors ? ' class="warn"' : '';
      html += `<tr><td>${escapeHtml(iface.name)}</td><td>&#8595; ${rx.value} ${rx.unit}</td><td>&#8593; ${tx.value} ${tx.unit}</td><td${errCls}>${iface.rx_errors || 0}/${iface.rx_dropped || 0}</td><td${errCls}>${iface.tx_errors || 0}/${iface.tx_dropped || 0}</td></tr>`;
    });
    html += `</tbody></table>`;
  } else {
    html += `<div style="color:var(--text-dim);font-size:0.7rem">No interfaces reporting</div>`;
  }
  return html;
}

function renderGpuDetail(d) {
  const gpuDetail = d.gpu || {};
  const gpus = gpuDetail.gpus || [];
  let html = `
    <div class="detail-header">
      <div class="detail-title">GPU</div>
      <button class="detail-close">&#10005; close</button>
    </div>`;

  if (!gpus.length) {
    html += `<div style="color:var(--text-dim);font-size:0.7rem">No GPU detected</div>`;
    return html;
  }

  gpus.forEach((g) => {
    const util = g.util_pct == null ? '--' : `${g.util_pct}%`;
    const mem = g.mem_used_mb == null && g.mem_total_mb == null
      ? '--'
      : `${g.mem_used_mb ?? '--'}/${g.mem_total_mb ?? '--'} MB`;
    const temp = g.temp == null ? '--' : `${g.temp}&deg;C`;
    const power = g.power_w == null ? '--' : `${g.power_w}/${g.power_limit_w ?? '--'} W`;

    html += `<div class="gpu-panel">
      <div class="section-sub">${escapeHtml(g.name || g.vendor)}</div>
      <div class="detail-grid gpu-grid">
        <div class="detail-stat"><div class="ds-label">Utilization</div><div class="ds-value">${util}</div></div>
        <div class="detail-stat"><div class="ds-label">Memory</div><div class="ds-value">${mem}</div></div>
        <div class="detail-stat"><div class="ds-label">Temperature</div><div class="ds-value">${temp}</div></div>
        <div class="detail-stat"><div class="ds-label">Power</div><div class="ds-value">${power}</div></div>
      </div>`;
    if (g.util_note) {
      html += `<div class="gpu-note">${escapeHtml(g.util_note)}</div>`;
    }
    html += `</div>`;
  });

  if (gpuDetail.processes?.length) {
    html += `<div class="section-sub">Processes</div>
    <table class="process-table"><thead><tr><th>PID</th><th>Name</th><th>Memory</th></tr></thead><tbody>`;
    gpuDetail.processes.forEach((p) => {
      html += `<tr><td>${escapeHtml(p.pid)}</td><td>${escapeHtml(p.name)}</td><td>${escapeHtml(p.mem_mb)} MB</td></tr>`;
    });
    html += `</tbody></table>`;
  }

  return html;
}

function renderContainersDetail() {
  const containers = sortContainers(window._latestContainers || []);
  let html = `
    <div class="detail-header">
      <div class="detail-title">${_containersHeaderText(containers)}</div>
      <button class="detail-close">&#10005; close</button>
    </div>`;

  if (containers.length) {
    html += `<div class="container-grid">${containers.map(containerRowHtml).join('')}</div>`;
    html += `<div id="container-inspect-panel"></div>`;

    // Fire off history fetches after the DOM settles (skip when the server
    // has told us history is disabled — the endpoint 404s in that case).
    setTimeout(() => {
      if (buoyConfig?.features?.history !== false) {
        document.querySelectorAll('.ctr-uptime[data-ctr]').forEach(el => {
          loadContainerHistory(el.dataset.ctr, el);
        });
      }
      document.querySelectorAll('.ctr[data-ctr-name]').forEach(el => {
        el.addEventListener('click', () => inspectContainer(el.dataset.ctrName));
      });
    }, 0);
  } else {
    html += `<div style="color:var(--text-dim);font-size:0.7rem">No containers found</div>`;
  }
  return html;
}

function _applyRowUpdate(row, c) {
  const dot = row.querySelector('.dot-sm');
  if (dot) dot.className = `dot-sm ${_dotClass(c)}`;
  const statusEl = row.querySelector('.ctr-status');
  if (statusEl) {
    statusEl.textContent = c.status || '';
    statusEl.title = c.status || '';
  }
  const metricsEl = row.querySelector('.ctr-metrics');
  if (metricsEl) metricsEl.textContent = `${c.cpu_pct ?? '--'} · ${c.mem_usage ?? '--'}`;
}

/**
 * Called on every stats tick (WebSocket or poll) while the containers panel
 * is open. Updates existing rows in place (dot/status/cpu/mem) instead of
 * re-rendering — a full re-render every 5s would wipe the uptime bars and
 * re-fire one history fetch per container. When the set of names changes,
 * rows are added/removed in place rather than blowing away
 * `#container-inspect-panel` (and any open logs view) with a full
 * `content.innerHTML` replace. New rows are appended rather than re-sorted
 * into position, which is an acceptable tradeoff to keep an open panel alive.
 */
export function refreshContainersPanel(containers) {
  if (currentDetail !== 'containers') return;
  const content = document.getElementById('detail-content');
  if (!content) return;

  const sorted = sortContainers(containers || []);
  const grid = content.querySelector('.container-grid');

  if (!grid || !sorted.length) {
    content.innerHTML = renderContainersDetail();
    return;
  }

  const title = content.querySelector('.detail-title');
  if (title) title.textContent = _containersHeaderText(sorted);

  const rows = Array.from(grid.querySelectorAll('.ctr[data-ctr-name]'));
  const existingNames = new Set(rows.map(el => el.dataset.ctrName));
  const newNames = new Set(sorted.map(c => c.name));

  rows.forEach(row => {
    if (!newNames.has(row.dataset.ctrName)) row.remove();
  });

  const byName = new Map(sorted.map(c => [c.name, c]));
  grid.querySelectorAll('.ctr[data-ctr-name]').forEach(row => {
    const c = byName.get(row.dataset.ctrName);
    if (c) _applyRowUpdate(row, c);
  });

  sorted.forEach(c => {
    if (existingNames.has(c.name)) return;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = containerRowHtml(c);
    const rowEl = wrapper.firstElementChild;
    grid.appendChild(rowEl);
    rowEl.addEventListener('click', () => inspectContainer(rowEl.dataset.ctrName));
    const uptimeEl = rowEl.querySelector('.ctr-uptime[data-ctr]');
    if (uptimeEl) loadContainerHistory(uptimeEl.dataset.ctr, uptimeEl);
  });
}

/**
 * Fetch 24h history for a container and render an uptime bar into el.
 */
async function loadContainerHistory(name, el) {
  try {
    const r = await authedFetch(apiUrl(`container/${encodeURIComponent(name)}/history?hours=24`));
    if (!r.ok) return; // history disabled or not found — leave empty
    const d = await r.json();
    el.innerHTML = renderUptimeBar(d.samples || [], d.hours || 24);
  } catch (_) {
    // silently skip — history may not be enabled
  }
}

/**
 * Render a segmented uptime bar from container history samples.
 * Green = running, red = stopped/exited, grey = no data.
 * Red tick marks appear where restart_count increased.
 */
function renderUptimeBar(samples, hours) {
  const BUCKETS = 48; // one segment per 30min over 24h
  const bucketMs = (hours * 3600 * 1000) / BUCKETS;
  const now = Date.now();
  const start = now - hours * 3600 * 1000;

  // Build bucket array: null = no data, 'running' | other = status
  const buckets = new Array(BUCKETS).fill(null);
  const restartTicks = new Set();

  let prevRestart = null;
  for (const s of samples) {
    const idx = Math.floor((s.ts * 1000 - start) / bucketMs);
    if (idx >= 0 && idx < BUCKETS) {
      buckets[idx] = s.status;
    }
    if (prevRestart !== null && s.restart_count > prevRestart && idx >= 0 && idx < BUCKETS) {
      restartTicks.add(idx);
    }
    prevRestart = s.restart_count;
  }

  let html = '<div class="ctr-uptime-bar" title="24h uptime history">';
  for (let i = 0; i < BUCKETS; i++) {
    const status = buckets[i];
    const cls = status === null ? 'seg-nodata' : status === 'running' ? 'seg-up' : 'seg-down';
    const tick = restartTicks.has(i) ? ' seg-restart' : '';
    html += `<div class="ctr-uptime-seg ${cls}${tick}"></div>`;
  }
  html += '</div>';
  return html;
}

/**
 * Convert an ISO timestamp into a human-readable age string (e.g. "3 days").
 * Returns '' when the timestamp is missing, unparseable, or in the future
 * (guards against the Docker zero-time sentinel and skewed inputs).
 */
function formatAge(iso) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const days = Math.floor((Date.now() - then) / 86400000);
  if (days < 0) return '';
  if (days === 0) return 'today';
  return days === 1 ? '1 day' : `${days} days`;
}

/**
 * Fetch and display detailed info for a single container.
 */
async function inspectContainer(name) {
  const panel = document.getElementById('container-inspect-panel');
  if (!panel) return;

  // Toggle off if same container clicked again
  if (panel.dataset.active === name) {
    panel.innerHTML = '';
    panel.dataset.active = '';
    return;
  }

  panel.dataset.active = name;
  panel.innerHTML = `<div class="ctr-inspect loading"><span class="ctr-inspect-text">Loading ${escapeHtml(name)}...</span></div>`;

  try {
    const r = await authedFetch(apiUrl(`container/${encodeURIComponent(name)}`));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();

    panel.innerHTML = renderContainerInspect(d, name);
    panel.querySelector('.ctr-btn-restart')?.addEventListener('click', function () { restartContainer(name, this); });
    panel.querySelector('.ctr-btn-logs')?.addEventListener('click', () => showContainerLogs(name));
    panel.querySelector('.ctr-inspect-close')?.addEventListener('click', () => {
      panel.innerHTML = '';
      panel.dataset.active = '';
    });
  } catch (e) {
    panel.innerHTML = `<div class="ctr-inspect error"><span class="ctr-inspect-text">Failed to load: ${escapeHtml(e.message)}</span></div>`;
  }
}

function renderContainerInspect(d, name) {
  const status = d.status || 'unknown';
  const statusDot = status === 'running' ? 'green' : status === 'exited' ? 'red' : 'amber';
  const image = d.image || '';
  const startedDate = d.started ? new Date(d.started) : null;
  const started = startedDate && !Number.isNaN(startedDate.getTime()) ? startedDate.toLocaleString() : 'N/A';
  const restarts = d.restart_count ?? 0;
  const res = d.resources || {};
  const cpu = res.cpu_pct || 'N/A';
  const mem = res.mem_usage || 'N/A';
  const netIO = res.net_io || 'N/A';
  const blockIO = res.block_io || 'N/A';
  const imageAge = formatAge(d.image_created);

  // Find update_status/health from the containers list (already in ws data)
  const ctrData = (window._latestContainers || []).find(c => c.name === name);
  const updateStatus = ctrData?.update_status;
  const health = ctrData?.health;

  return `<div class="ctr-inspect">
    <div class="ctr-inspect-header">
      <div class="ctr-inspect-name"><div class="dot-sm" style="background:var(--${statusDot})"></div>${escapeHtml(name)}</div>
      <button class="ctr-inspect-close">&#10005;</button>
    </div>
    <div class="ctr-inspect-grid">
      <div class="ctr-stat"><span class="ctr-stat-label">Status</span><span class="ctr-stat-value">${escapeHtml(status)}</span></div>
      ${health ? `<div class="ctr-stat"><span class="ctr-stat-label">Health</span><span class="ctr-stat-value">${escapeHtml(health)}</span></div>` : ''}
      <div class="ctr-stat"><span class="ctr-stat-label">Started</span><span class="ctr-stat-value">${escapeHtml(started)}</span></div>
      <div class="ctr-stat"><span class="ctr-stat-label">Image</span><span class="ctr-stat-value ctr-image">${escapeHtml(image)}</span></div>
      ${imageAge ? `<div class="ctr-stat"><span class="ctr-stat-label">Image Age</span><span class="ctr-stat-value">${escapeHtml(imageAge)}</span></div>` : ''}
      ${updateStatus ? `<div class="ctr-stat"><span class="ctr-stat-label">Updates</span><span class="ctr-stat-value">${_updateBadge(updateStatus)}</span></div>` : ''}
      <div class="ctr-stat"><span class="ctr-stat-label">Restarts</span><span class="ctr-stat-value">${escapeHtml(restarts)}</span></div>
      <div class="ctr-stat"><span class="ctr-stat-label">CPU</span><span class="ctr-stat-value">${escapeHtml(cpu)}</span></div>
      <div class="ctr-stat"><span class="ctr-stat-label">Memory</span><span class="ctr-stat-value">${escapeHtml(mem)}</span></div>
      <div class="ctr-stat"><span class="ctr-stat-label">Net I/O</span><span class="ctr-stat-value">${escapeHtml(netIO)}</span></div>
      <div class="ctr-stat"><span class="ctr-stat-label">Block I/O</span><span class="ctr-stat-value">${escapeHtml(blockIO)}</span></div>
    </div>
    <div class="ctr-inspect-actions">
      <button class="ctr-btn ctr-btn-restart">↻ restart</button>
      <button class="ctr-btn ctr-btn-logs">⊞ logs</button>
    </div>
  </div>`;
}

function _updateBadge(status) {
  if (!status || status === 'skipped') return '';
  const map = {
    up_to_date:       { icon: '✓', cls: 'ctr-update-badge up-to-date',       title: 'Up to date' },
    update_available: { icon: '↑', cls: 'ctr-update-badge update-available', title: 'Update available' },
    unknown:          { icon: '?', cls: 'ctr-update-badge unknown',           title: 'Update status unknown' },
  };
  const b = map[status];
  if (!b) return '';
  return `<span class="${b.cls}" title="${b.title}">${b.icon}</span>`;
}

/**
 * Confirm-before-restart pattern: first click shows warning, second click confirms.
 */
async function restartContainer(name, btn) {
  if (!btn.classList.contains('confirm')) {
    btn.textContent = '⚠ click again to confirm restart';
    btn.classList.add('confirm');
    setTimeout(() => {
      if (btn.classList.contains('confirm')) {
        btn.textContent = '↻ restart';
        btn.classList.remove('confirm');
      }
    }, 4000);
    return;
  }

  btn.textContent = 'restarting...';
  btn.disabled = true;
  btn.classList.remove('confirm');

  try {
    const r = await authedFetch(apiUrl(`container/${encodeURIComponent(name)}/restart`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    btn.textContent = '✓ restarted';
    btn.classList.add('success');
    setTimeout(() => {
      btn.textContent = '↻ restart';
      btn.classList.remove('success');
      btn.disabled = false;
    }, 3000);
  } catch (e) {
    btn.textContent = '✗ failed';
    btn.classList.add('error');
    setTimeout(() => {
      btn.textContent = '↻ restart';
      btn.classList.remove('error');
      btn.disabled = false;
    }, 3000);
  }
}

/**
 * Toggle a live log viewer for the container inline (WS streaming with a
 * one-shot fallback — see logs.js).
 */
function showContainerLogs(name) {
  const panel = document.getElementById('container-inspect-panel');
  if (!panel) return;
  openLogViewer(name, panel, buoyConfig);
}
