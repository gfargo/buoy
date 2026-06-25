/**
 * Services module — renders local service cards.
 */

export async function refreshServices(config) {
  try {
    const r = await fetch('/api/services');
    if (!r.ok) return;
    const data = await r.json();

    // Tailscale badge
    if (data.tailscale) {
      const badge = document.getElementById('access-badge');
      if (badge) badge.style.display = '';
    }

    // Local services
    const localEl = document.getElementById('services-local');
    const services = data.local || [];

    if (services.length === 0) {
      localEl.innerHTML = '<div style="color:var(--text-dim);font-size:0.65rem;padding:0.5rem">No services discovered</div>';
      return;
    }

    localEl.innerHTML = services.map(s => {
      const icon = s.icon ? `<span class="svc-icon">${s.icon}</span>` : `<div class="dot"></div>`;
      const displayUrl = s.url ? s.url.replace(/^https?:\/\//, '') : '';
      const href = s.url || '#';
      return `<a class="svc" href="${href}" ${s.url ? '' : 'onclick="return false"'}>
        <div class="svc-header">${icon}<div class="svc-name">${s.name}</div></div>
        <div class="svc-desc">${s.desc}</div>
        ${displayUrl ? `<div class="svc-url">${displayUrl}</div>` : ''}
      </a>`;
    }).join('');

    // Store network entries for fleet module
    window._networkEntries = data.network || [];
  } catch (e) {
    console.error('[buoy] services error:', e);
  }
}
