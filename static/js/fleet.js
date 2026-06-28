/**
 * Fleet module — polls peer nodes and renders the network grid.
 */

import { renderSparkline } from './gauges.js';

export async function refreshFleet(config) {
  const peers = config.network?.peers || [];
  const currentHostname = document.getElementById('hostname')?.textContent;

  // Filter to other nodes only
  const otherNodes = peers.filter(p => p.name !== currentHostname);
  if (!otherNodes.length) {
    document.getElementById('network-label').style.display = 'none';
    document.getElementById('network-grid').innerHTML = '';
    return;
  }

  document.getElementById('network-label').style.display = '';

  // Fetch live stats from each peer
  const results = await Promise.allSettled(
    otherNodes.map(async node => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 4000);
      try {
        const r = await fetch(node.url + '/api/stats', { signal: controller.signal });
        clearTimeout(timeout);
        if (!r.ok) return { ...node, online: false };
        const data = await r.json();
        return { ...node, online: true, data };
      } catch {
        clearTimeout(timeout);
        return { ...node, online: false };
      }
    })
  );

  const nodes = results.map(r => r.status === 'fulfilled' ? r.value : { online: false });
  const grid = document.getElementById('network-grid');

  grid.innerHTML = nodes.map(n => {
    if (!n.online) {
      return `<div class="fleet-node offline">
        <div class="fn-dot"></div>
        <div class="fn-name">${n.name} <span style="font-weight:300;font-size:0.6rem;color:var(--text-dim)">${n.tier || ''}</span></div>
        <div class="fn-stats"><span>offline</span></div>
      </div>`;
    }

    const d = n.data;
    const memPct = d.mem_total > 0 ? ((d.mem_used / d.mem_total) * 100).toFixed(0) : 0;
    const services = d.top_services || [];
    const pillRow = services.length
      ? `<div class="fn-services">${services.map(s =>
          `<a class="fn-svc" href="${s.url}" title="${s.name}">${s.icon ? s.icon + ' ' : ''}${s.name}</a>`
        ).join('')}</div>`
      : '';
    const peerKey = encodeURIComponent(n.name);
    return `<div class="fleet-node" data-peer="${n.name}">
      <div class="fn-dot"></div>
      <a class="fn-name fn-name-link" href="${n.url}">${n.name} <span style="font-weight:300;font-size:0.6rem;color:var(--text-dim)">${n.tier || ''}</span></a>
      <div class="fn-stats">
        <span>CPU <span class="fn-val">${d.cpu || 0}%</span></span>
        <span>MEM <span class="fn-val">${memPct}%</span></span>
        <span>&#127777; <span class="fn-val">${d.temp || 0}&deg;</span></span>
        <span>&#11043; <span class="fn-val">${d.containers || 0}</span></span>
        <span>&uarr; <span class="fn-val">${formatUptime(d.uptime_h || 0, d.uptime_m || 0)}</span></span>
        <span id="fn-latency-${peerKey}" class="fn-latency-wrap"></span>
      </div>
      <div id="fn-latency-spark-${peerKey}" class="sparkline fn-latency-spark"></div>
      ${pillRow}
    </div>`;
  }).join('');

  // Fetch latency history for each online node (best-effort, non-blocking)
  for (const n of nodes) {
    if (!n.online || !n.name) continue;
    const peerKey = encodeURIComponent(n.name);
    fetchLatencyHistory(n.name, peerKey);
  }
}

async function fetchLatencyHistory(peerName, peerKey) {
  try {
    const r = await fetch(`/api/fleet/${encodeURIComponent(peerName)}/latency-history?hours=6`);
    if (!r.ok) return;
    const { data } = await r.json();
    if (!data || data.length < 2) return;

    // Update current latency label (last value)
    const latEl = document.getElementById(`fn-latency-${peerKey}`);
    if (latEl) {
      const lastMs = data[data.length - 1][1];
      latEl.textContent = `${lastMs}ms`;
    }

    // Render sparkline using the exported helper
    const values = data.map(([, ms]) => ms);
    const maxVal = Math.max(...values);
    // renderSparkline looks up by ID; target our sparkline container
    const sparkEl = document.getElementById(`fn-latency-spark-${peerKey}`);
    if (!sparkEl || values.length < 2) return;
    const w = 80, h = 16;
    const range = maxVal || 1;
    const n = values.length;
    const points = values.map((v, i) => {
      const x = (i / (n - 1)) * w;
      const y = h - (v / range) * (h - 2) - 1;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    const color = maxVal > 150 ? 'var(--red)' : maxVal > 50 ? 'var(--amber)' : 'var(--cyan)';
    sparkEl.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${points}" style="stroke:${color}"/></svg>`;
  } catch {
    // history disabled or network error — degrade silently
  }
}

function formatUptime(h, m) {
  if (h > 24) return Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
  return h + 'h ' + m + 'm';
}
