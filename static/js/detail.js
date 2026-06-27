/**
 * Detail module — expandable panels for CPU, memory, disk, containers.
 */

let currentDetail = null;
let _openCtrName = null;
let _authToken = null;

async function authedFetch(url, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (_authToken) headers['Authorization'] = `Bearer ${_authToken}`;
  let r = await fetch(url, { ...opts, headers });
  if (r.status === 401) {
    const tok = prompt('Authentication required. Enter token:');
    if (!tok) return r;
    _authToken = tok;
    headers['Authorization'] = `Bearer ${tok}`;
    r = await fetch(url, { ...opts, headers });
    if (r.status === 401) { _authToken = null; }
  }
  return r;
}

function formatStarted(iso) {
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function imageAgeDays(iso) {
  try {
    const diff = Date.now() - new Date(iso).getTime();
    return Math.floor(diff / 86400000);
  } catch { return '?'; }
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

export function initDetail() {
  document.querySelectorAll('.gauge[data-detail]').forEach(gauge => {
    gauge.addEventListener('click', () => openDetail(gauge.dataset.detail));
    gauge.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(gauge.dataset.detail); }
    });
  });

  document.getElementById('detail-content').addEventListener('click', e => {
    const ctr = e.target.closest('.ctr[data-name]');
    if (ctr) { e.stopPropagation(); toggleContainerDetail(ctr.dataset.name); return; }
    const btn = e.target.closest('[data-ctr-action]');
    if (btn) { e.stopPropagation(); handleCtrAction(btn); }
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
  _openCtrName = null;
  let html = `
    <div class="detail-header">
      <div class="detail-title">Running Containers (${containers.length})</div>
      <button class="detail-close" onclick="this.closest('.detail-panel').classList.remove('open');document.querySelectorAll('.gauge.expanded').forEach(g=>g.classList.remove('expanded'))">&#10005; close</button>
    </div>`;

  if (containers.length) {
    html += `<div class="container-grid">`;
    containers.forEach(c => {
      html += `<div class="ctr" data-name="${escHtml(c.name)}" role="button" tabindex="0"><div class="dot-sm"></div><div class="ctr-name">${escHtml(c.name)}</div></div><div class="ctr-detail" id="ctr-slot-${escHtml(c.name)}"></div>`;
    });
    html += `</div>`;
  } else {
    html += `<div style="color:var(--text-dim);font-size:0.7rem">No containers running</div>`;
  }
  return html;
}

async function toggleContainerDetail(name) {
  const slot = document.getElementById(`ctr-slot-${name}`);
  if (!slot) return;
  if (_openCtrName === name) {
    slot.innerHTML = '';
    _openCtrName = null;
    return;
  }
  if (_openCtrName) {
    const prev = document.getElementById(`ctr-slot-${_openCtrName}`);
    if (prev) prev.innerHTML = '';
  }
  _openCtrName = name;
  slot.innerHTML = `<div class="ctr-card-loading">Loading…</div>`;
  try {
    const r = await authedFetch('/api/container/' + encodeURIComponent(name));
    if (r.status === 401) { slot.innerHTML = `<div class="ctr-card-err">Authentication failed</div>`; return; }
    const d = await r.json();
    if (d.error) { slot.innerHTML = `<div class="ctr-card-err">${escHtml(d.error)}</div>`; return; }
    slot.innerHTML = renderContainerCard(d);
  } catch (e) {
    slot.innerHTML = `<div class="ctr-card-err">Failed to load</div>`;
  }
}

function renderContainerCard(d) {
  const res = d.resources || {};
  const stats = [
    ['Status', d.status || '—'],
    ['Started', formatStarted(d.started)],
    ['Image', d.image || '—'],
    ['Image Age', d.image_created ? imageAgeDays(d.image_created) + ' days' : '—'],
    ['Restarts', d.restart_count ?? '—'],
    ['CPU', res.cpu_pct || '—'],
    ['Memory', res.mem_usage || '—'],
    ['Net I/O', res.net_io || '—'],
    ['Block I/O', res.block_io || '—'],
  ];
  let html = `<div class="ctr-card">`;
  html += `<div class="detail-grid">` + stats.map(([l, v]) =>
    `<div class="detail-stat"><div class="ds-label">${escHtml(l)}</div><div class="ds-value">${escHtml(String(v))}</div></div>`
  ).join('') + `</div>`;
  html += `<div class="ctr-actions">
    <button class="ctr-btn" data-ctr-action="restart" data-name="${escHtml(d.name)}">↻ restart</button>
    <button class="ctr-btn" data-ctr-action="logs" data-name="${escHtml(d.name)}">⊞ logs</button>
  </div>
  <div class="ctr-logs" id="ctr-logs-${escHtml(d.name)}"></div>
  </div>`;
  return html;
}

async function handleCtrAction(btn) {
  const action = btn.dataset.ctrAction;
  const name = btn.dataset.name;
  if (action === 'restart') {
    btn.disabled = true;
    btn.textContent = '…';
    try {
      const r = await authedFetch('/api/container/' + encodeURIComponent(name) + '/restart', { method: 'POST' });
      const d = await r.json();
      btn.textContent = d.success ? '✓ restarted' : '✗ failed';
    } catch { btn.textContent = '✗ error'; }
  } else if (action === 'logs') {
    const logsEl = document.getElementById(`ctr-logs-${name}`);
    if (!logsEl) return;
    btn.disabled = true;
    try {
      const r = await authedFetch('/api/container/' + encodeURIComponent(name) + '/logs');
      const d = await r.json();
      logsEl.innerHTML = `<pre class="ctr-log-pre">${escHtml((d.lines || []).join('\n'))}</pre>`;
    } catch { logsEl.innerHTML = `<div class="ctr-card-err">Failed to load logs</div>`; }
    btn.disabled = false;
  }
}

// Expose closeDetail globally for inline onclick handlers
window._buoyCloseDetail = closeDetail;
