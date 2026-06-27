/**
 * Fleet module — polls peer nodes and renders the network grid.
 */

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
      const t0 = performance.now();
      try {
        const r = await fetch(node.url + '/api/stats', { signal: controller.signal });
        const latency = Math.round(performance.now() - t0);
        clearTimeout(timeout);
        if (!r.ok) return { ...node, online: false };
        const data = await r.json();
        return { ...node, online: true, data, latency };
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
    return `<a class="fleet-node" href="${n.url}">
      <div class="fn-dot"></div>
      <div class="fn-name">${n.name} <span style="font-weight:300;font-size:0.6rem;color:var(--text-dim)">${n.tier || ''}</span> <span class="fn-latency ${latencyClass(n.latency)}">${n.latency}ms</span></div>
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
