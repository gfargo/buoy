/**
 * Gauges module — renders gauge cards, sparklines, and bar fills.
 */

const SPARK_MAX = 30;
const tempHistory = [];
const diskHistory = [];

export function initGauges() {
  // Gauges are rendered server-side in index.html; this module updates values.
}

export function updateGauges(data) {
  // Hostname + header
  const hostnameEl = document.getElementById('hostname');
  if (hostnameEl && data.hostname) {
    hostnameEl.textContent = data.hostname;
    document.title = `${data.hostname} — buoy`;
  }

  const tierTag = document.getElementById('tier-tag');
  if (tierTag && data.hostname) {
    // Config-driven tier is shown; fallback to node name
    tierTag.textContent = tierTag.dataset.tier || data.hostname;
  }

  // CPU
  setGauge('cpu', data.cpu, '%');
  setBar('cpu-bar', data.cpu, 70, 90);

  // Memory
  const memEl = document.getElementById('mem');
  if (memEl) memEl.textContent = `${data.mem_used}/${data.mem_total}`;
  const memPct = data.mem_total > 0 ? ((data.mem_used / data.mem_total) * 100).toFixed(0) : 0;
  setBar('mem-bar', memPct, 75, 90);

  // Temperature
  setGauge('temp', data.temp, '°C');
  setBar('temp-bar', Math.min((data.temp || 0) / 85 * 100, 100), 65, 80);
  if (data.temp > 0) {
    tempHistory.push(data.temp);
    if (tempHistory.length > SPARK_MAX) tempHistory.shift();
    const color = data.temp >= 80 ? 'var(--red)' : data.temp >= 65 ? 'var(--amber)' : 'var(--cyan)';
    renderSparkline('temp-sparkline', tempHistory, 30, 85, color);
  }

  // Disk
  setGauge('disk', data.disk_pct, '%');
  setBar('disk-bar', data.disk_pct, 70, 85);
  if (data.disk_pct > 0) {
    diskHistory.push(data.disk_pct);
    if (diskHistory.length > SPARK_MAX) diskHistory.shift();
    const color = data.disk_pct >= 85 ? 'var(--red)' : data.disk_pct >= 70 ? 'var(--amber)' : 'var(--cyan)';
    renderSparkline('disk-sparkline', diskHistory, 0, 100, color);
  }

  // Containers
  setGauge('containers', data.containers, '');

  // Uptime
  const uptimeEl = document.getElementById('uptime');
  if (uptimeEl) uptimeEl.textContent = formatUptime(data.uptime_h || 0, data.uptime_m || 0);

  // NVMe
  if (data.nvme && data.nvme.temp > 0) {
    show('nvme-panel');
    show('nvme-temp-gauge');
    setGauge('nvme-temp', data.nvme.temp, '°C');
    setBar('nvme-temp-bar', Math.min(data.nvme.temp / 70 * 100, 100), 55, 70);
    setText('nvme-wear', data.nvme.wear_pct + '%');
    setText('nvme-hours', (data.nvme.power_hours || 0).toLocaleString() + ' hrs');
    setText('nvme-read', data.nvme.read || '--');
    setText('nvme-written', data.nvme.written || '--');
    const badge = document.getElementById('nvme-badge');
    if (badge) {
      const wear = data.nvme.wear_pct || 0;
      badge.textContent = wear >= 90 ? 'Critical' : wear >= 70 ? 'Warning' : 'Healthy';
      badge.className = 'health-badge' + (wear >= 90 ? ' crit' : wear >= 70 ? ' warn' : '');
    }
  }

  // Tailscale badge
  if (data.tailscale) {
    show('access-badge');
  }

  // Footer
  setText('footer-model', data.model || '');

  // Store containers for detail panel
  window._latestContainers = data.containers_list || [];
}

// ── Helpers ─────────────────────────────────────────────────

function setGauge(id, value, _unit) {
  const el = document.getElementById(id);
  if (el) el.textContent = value ?? '--';
}

function setBar(id, pct, warnAt, critAt) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.width = `${pct}%`;
  el.className = pct >= critAt ? 'bar-fill crit' : pct >= warnAt ? 'bar-fill warn' : 'bar-fill';
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function show(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = '';
}

function formatUptime(h, m) {
  if (h > 24) return Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
  return h + 'h ' + m + 'm';
}

export function renderSparkline(containerId, values, minVal, maxVal, color) {
  const container = document.getElementById(containerId);
  if (!container || values.length < 2) return;
  const w = 120, h = 24;
  const range = maxVal - minVal || 1;
  const points = values.map((v, i) => {
    const x = (i / (SPARK_MAX - 1)) * w;
    const y = h - ((v - minVal) / range) * (h - 2) - 1;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${points}" style="stroke:${color}"/></svg>`;
}
