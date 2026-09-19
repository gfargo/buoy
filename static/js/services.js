/**
 * Services module — renders local service cards.
 */

import { escapeHtml, safeUrl } from './escape.js';
import { apiUrl } from './paths.js';

// Mirrors the semantic status→color mapping in panel.js's STATUS_COLOR.
const STATUS_COLOR = {
  ok: 'var(--green)',
  warn: 'var(--amber)',
  error: 'var(--red)',
};

export function renderServiceCard(s) {
  const dotColor = s.status ? STATUS_COLOR[s.status] : null;
  const dot = dotColor
    ? `<div class="dot" style="background:${dotColor};box-shadow:0 0 6px ${dotColor}"></div>`
    : `<div class="dot"></div>`;
  const iconEl = s.icon ? `<span class="svc-icon">${escapeHtml(s.icon)}</span>` : '';
  // Render both icon and status dot when both are present, so a health-checked
  // entry isn't statusless just because it also set a display icon.
  const header = s.icon && s.status ? `${iconEl}${dot}` : s.icon ? iconEl : dot;
  const displayUrl = s.url ? s.url.replace(/^https?:\/\//, '') : '';
  const href = s.url || '#';
  return `<a class="svc" href="${escapeHtml(safeUrl(href))}" ${s.url ? '' : 'data-no-url="1"'}>
    <div class="svc-header">${header}<div class="svc-name">${escapeHtml(s.name)}</div></div>
    <div class="svc-desc">${escapeHtml(s.desc)}</div>
    ${displayUrl ? `<div class="svc-url">${escapeHtml(displayUrl)}</div>` : ''}
  </a>`;
}

export async function refreshServices(config) {
  try {
    const r = await fetch(apiUrl('services'));
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

    localEl.innerHTML = services.map(renderServiceCard).join('');

    localEl.querySelectorAll('.svc[data-no-url="1"]').forEach(a => {
      a.addEventListener('click', (e) => e.preventDefault());
    });

    // Store network entries for fleet module
    window._networkEntries = data.network || [];
  } catch (e) {
    console.error('[buoy] services error:', e);
  }
}
