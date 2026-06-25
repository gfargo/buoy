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
      html += `<div class="ctr"><div class="dot-sm"></div><div class="ctr-name">${c.name}</div></div>`;
    });
    html += `</div>`;
  } else {
    html += `<div style="color:var(--text-dim);font-size:0.7rem">No containers running</div>`;
  }
  return html;
}

// Expose closeDetail globally for inline onclick handlers
window._buoyCloseDetail = closeDetail;
