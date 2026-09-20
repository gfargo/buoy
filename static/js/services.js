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

/**
 * Split an already-ordered service list into consecutive group runs. The
 * server is the sorting authority (services.py's _sort_services) — this
 * just partitions its output, it never re-sorts.
 */
export function groupServices(services) {
  const groups = [];
  for (const s of services) {
    const group = s.group || '';
    const last = groups[groups.length - 1];
    if (last && last.group === group) {
      last.items.push(s);
    } else {
      groups.push({ group, items: [s] });
    }
  }
  return groups;
}

export function renderGroupLabel(group) {
  return `<h3 class="svc-group-label">${escapeHtml(group)}</h3>`;
}

/**
 * The group header to render above a run, or '' for the ungrouped run — a
 * single unnamed group (or an ungrouped run alongside named ones) must not
 * render a blank <h3>.
 */
export function renderGroupHeader(group) {
  return group !== '' ? renderGroupLabel(group) : '';
}

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
      const dockerDown = window._buoyHealth?.subsystems?.docker?.status === 'unavailable';
      const message = dockerDown
        ? 'Docker socket unavailable — no services discovered'
        : 'No services discovered';
      localEl.innerHTML = `<div style="color:var(--text-dim);font-size:0.65rem;padding:0.5rem">${message}</div>`;
      return;
    }

    const groups = groupServices(services);
    localEl.innerHTML = groups
      .map(({ group, items }) => renderGroupHeader(group) + items.map(renderServiceCard).join(''))
      .join('');

    localEl.querySelectorAll('.svc[data-no-url="1"]').forEach(a => {
      a.addEventListener('click', (e) => e.preventDefault());
    });

    // Store network entries for fleet module
    window._networkEntries = data.network || [];
  } catch (e) {
    console.error('[buoy] services error:', e);
  }
}
