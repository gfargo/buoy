/**
 * Fleet module — polls /api/fleet for peer node stats and renders the network grid.
 */

export async function refreshFleet(config) {
  const peers = config.network?.peers || [];
  const currentHostname = document.getElementById('hostname')?.textContent;

  // Filter to other nodes only (by name, matching server-side self-node exclusion)
  const otherNodes = peers.filter(p => p.name !== currentHostname);
  if (!otherNodes.length) {
    document.getElementById('network-label').style.display = 'none';
    document.getElementById('network-grid').innerHTML = '';
    return;
  }

  document.getElementById('network-label').style.display = '';

  // Fetch server-measured fleet stats (includes server-side latency_ms per peer)
  let nodes = [];
  try {
    const r = await fetch('/api/fleet');
    if (r.ok) {
      const data = await r.json();
      // Filter out self-node and map to display list
      nodes = (data.peers || []).filter(p => !p.self && p.name !== currentHostname);
    }
  } catch {
    // On error, fall back to showing all peers as offline
    nodes = otherNodes.map(p => ({ name: p.name, tier: p.tier, online: false }));
  }

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
    const latMs = (n.latency_ms != null && n.latency_ms >= 0) ? n.latency_ms : null;
    const latSpan = latMs !== null
      ? ` <span class="fn-latency ${latencyClass(latMs)}">${latMs}ms</span>`
      : '';
    return `<a class="fleet-node" href="${n.url}">
      <div class="fn-dot"></div>
      <div class="fn-name">${n.name} <span style="font-weight:300;font-size:0.6rem;color:var(--text-dim)">${n.tier || ''}</span>${latSpan}</div>
      <div class="fn-stats">
        <span>CPU <span class="fn-val">${d.cpu || 0}%</span></span>
        <span>MEM <span class="fn-val">${memPct}%</span></span>
        <span>&#127777; <span class="fn-val">${d.temp || 0}&deg;</span></span>
        <span>&#11043; <span class="fn-val">${d.containers || 0}</span></span>
        <span>&uarr; <span class="fn-val">${formatUptime(d.uptime_h || 0, d.uptime_m || 0)}</span></span>
      </div>
    </a>`;
  }).join('');
}

function formatUptime(h, m) {
  if (h > 24) return Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
  return h + 'h ' + m + 'm';
}

function latencyClass(ms) {
  if (ms < 50) return 'lat-good';
  if (ms < 200) return 'lat-warn';
  return 'lat-bad';
}
