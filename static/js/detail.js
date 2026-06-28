/**
 * Detail module — expandable panels for CPU, memory, disk, containers.
 */

let currentDetail = null;

export function initDetail() {
  document.querySelectorAll('.gauge[data-detail]').forEach(gauge => {
    gauge.addEventListener('click', () => openDetail(gauge.dataset.detail));
    gauge.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(gauge.dataset.detail); }
    });
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
    const r = await fetch('/api/stats/detail');
    if (!r.ok) throw new Error('Failed');
    const d = await r.json();

    switch (type) {
      case 'cpu': content.innerHTML = renderCpuDetail(d); break;
      case 'memory': content.innerHTML = renderMemoryDetail(d); break;
      case 'disk': content.innerHTML = renderDiskDetail(d); break;
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
      <div class="detail-title">CPU — ${cpu.model || 'unknown'}</div>
      <button class="detail-close" onclick="this.closest('.detail-panel').classList.remove('open');document.querySelectorAll('.gauge.expanded').forEach(g=>g.classList.remove('expanded'))">&#10005; close</button>
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
      html += `<tr><td>${p.pid}</td><td>${p.cpu}%</td><td>${p.mem}%</td><td>${p.cmd}</td></tr>`;
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
      <button class="detail-close" onclick="this.closest('.detail-panel').classList.remove('open');document.querySelectorAll('.gauge.expanded').forEach(g=>g.classList.remove('expanded'))">&#10005; close</button>
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
      <button class="detail-close" onclick="this.closest('.detail-panel').classList.remove('open');document.querySelectorAll('.gauge.expanded').forEach(g=>g.classList.remove('expanded'))">&#10005; close</button>
    </div>`;

  if (disk.mounts?.length) {
    disk.mounts.forEach(mnt => {
      const cls = mnt.pct >= 90 ? 'mount-bar-fill crit' : mnt.pct >= 75 ? 'mount-bar-fill warn' : 'mount-bar-fill';
      html += `<div class="mount-row">
        <span class="mount-path">${mnt.mount || mnt.fs}</span>
        <div class="mount-bar"><div class="${cls}" style="width:${mnt.pct}%"></div></div>
        <span class="mount-info">${mnt.used}/${mnt.size} (${mnt.pct}%)</span>
      </div>`;
    });
  }
  return html;
}

function renderContainersDetail() {
  const containers = window._latestContainers || [];
  let html = `
    <div class="detail-header">
      <div class="detail-title">Running Containers (${containers.length})</div>
      <button class="detail-close" onclick="this.closest('.detail-panel').classList.remove('open');document.querySelectorAll('.gauge.expanded').forEach(g=>g.classList.remove('expanded'))">&#10005; close</button>
    </div>`;

  if (containers.length) {
    html += `<div class="container-grid">`;
    containers.forEach(c => {
      const badge = _updateBadge(c.update_status);
      html += `<div class="ctr" onclick="window._buoyInspectContainer('${c.name}')"><div class="dot-sm"></div><div class="ctr-name">${c.name}</div><div class="ctr-uptime" data-ctr="${c.name}"></div>${badge}</div>`;
    });
    html += `</div>`;
    html += `<div id="container-inspect-panel"></div>`;

    // Fire off history fetches after the DOM settles
    setTimeout(() => {
      document.querySelectorAll('.ctr-uptime[data-ctr]').forEach(el => {
        loadContainerHistory(el.dataset.ctr, el);
      });
    }, 0);
  } else {
    html += `<div style="color:var(--text-dim);font-size:0.7rem">No containers running</div>`;
  }
  return html;
}

/**
 * Fetch 24h history for a container and render an uptime bar into el.
 */
async function loadContainerHistory(name, el) {
  try {
    const r = await fetch(`/api/container/${encodeURIComponent(name)}/history?hours=24`);
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
  panel.innerHTML = `<div class="ctr-inspect loading"><span class="ctr-inspect-text">Loading ${name}...</span></div>`;

  try {
    const r = await fetch(`/api/container/${encodeURIComponent(name)}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();

    panel.innerHTML = renderContainerInspect(d, name);
  } catch (e) {
    panel.innerHTML = `<div class="ctr-inspect error"><span class="ctr-inspect-text">Failed to load: ${e.message}</span></div>`;
  }
}

function renderContainerInspect(d, name) {
  const status = d.status || 'unknown';
  const statusDot = status === 'running' ? 'green' : status === 'exited' ? 'red' : 'amber';
  const image = d.image || '';
  const started = d.started_at ? new Date(d.started_at).toLocaleString() : 'N/A';
  const restarts = d.restart_count ?? 0;
  const res = d.resources || {};
  const cpu = res.cpu_pct != null ? `${res.cpu_pct}%` : 'N/A';
  const mem = res.mem_usage || 'N/A';
  const netIO = res.net_io || 'N/A';
  const blockIO = res.block_io || 'N/A';
  const imageAge = d.image_age || '';

  // Find update_status from the containers list (already in ws data)
  const ctrData = (window._latestContainers || []).find(c => c.name === name);
  const updateStatus = ctrData?.update_status;

  return `<div class="ctr-inspect">
    <div class="ctr-inspect-header">
      <div class="ctr-inspect-name"><div class="dot-sm" style="background:var(--${statusDot})"></div>${name}</div>
      <button class="ctr-inspect-close" onclick="document.getElementById('container-inspect-panel').innerHTML='';document.getElementById('container-inspect-panel').dataset.active=''">&#10005;</button>
    </div>
    <div class="ctr-inspect-grid">
      <div class="ctr-stat"><span class="ctr-stat-label">Status</span><span class="ctr-stat-value">${status}</span></div>
      <div class="ctr-stat"><span class="ctr-stat-label">Started</span><span class="ctr-stat-value">${started}</span></div>
      <div class="ctr-stat"><span class="ctr-stat-label">Image</span><span class="ctr-stat-value ctr-image">${image}</span></div>
      ${imageAge ? `<div class="ctr-stat"><span class="ctr-stat-label">Image Age</span><span class="ctr-stat-value">${imageAge}</span></div>` : ''}
      ${updateStatus ? `<div class="ctr-stat"><span class="ctr-stat-label">Updates</span><span class="ctr-stat-value">${_updateBadge(updateStatus)}</span></div>` : ''}
      <div class="ctr-stat"><span class="ctr-stat-label">Restarts</span><span class="ctr-stat-value">${restarts}</span></div>
      <div class="ctr-stat"><span class="ctr-stat-label">CPU</span><span class="ctr-stat-value">${cpu}</span></div>
      <div class="ctr-stat"><span class="ctr-stat-label">Memory</span><span class="ctr-stat-value">${mem}</span></div>
      <div class="ctr-stat"><span class="ctr-stat-label">Net I/O</span><span class="ctr-stat-value">${netIO}</span></div>
      <div class="ctr-stat"><span class="ctr-stat-label">Block I/O</span><span class="ctr-stat-value">${blockIO}</span></div>
    </div>
    <div class="ctr-inspect-actions">
      <button class="ctr-btn ctr-btn-restart" onclick="window._buoyRestartContainer('${name}', this)">↻ restart</button>
      <button class="ctr-btn ctr-btn-logs" onclick="window._buoyContainerLogs('${name}')">⊞ logs</button>
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
    btn.textContent = '⚠ click again to confirm';
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
    const r = await fetch(`/api/container/${encodeURIComponent(name)}/restart`, { method: 'POST' });
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
 * Show recent logs for a container inline.
 */
async function showContainerLogs(name) {
  const panel = document.getElementById('container-inspect-panel');
  if (!panel) return;

  try {
    const r = await fetch(`/api/container/${encodeURIComponent(name)}/logs`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();

    const lines = (d.lines || []).join('\n');
    const existing = panel.querySelector('.ctr-logs');
    if (existing) { existing.remove(); return; }

    const logsDiv = document.createElement('div');
    logsDiv.className = 'ctr-logs';
    logsDiv.innerHTML = `<div class="ctr-logs-header">Logs — ${name} (last ${d.lines?.length || 0} lines)<button class="ctr-logs-close" onclick="this.closest('.ctr-logs').remove()">&#10005;</button></div><pre class="ctr-logs-pre">${escapeHtml(lines)}</pre>`;
    panel.appendChild(logsDiv);
  } catch (e) {
    // Silently fail
  }
}

function escapeHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Expose functions globally for inline onclick handlers
window._buoyCloseDetail = closeDetail;
window._buoyInspectContainer = inspectContainer;
window._buoyRestartContainer = restartContainer;
window._buoyContainerLogs = showContainerLogs;
