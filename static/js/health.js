/**
 * Health module — discreet header badge + detail dialog for GET /api/health's
 * subsystem/plugin capability summary. Hidden entirely while everything is
 * healthy, per the "discreet" ask (an empty panel should explain itself, not
 * turn the whole dashboard into a wall of warnings).
 */

import { escapeHtml } from './escape.js';
import { apiUrl } from './paths.js';

const STATUS_COLOR = {
  ok: 'var(--green)',
  unavailable: 'var(--red)',
  error: 'var(--red)',
  disabled: 'var(--text-dim)',
  not_loaded: 'var(--amber)',
  not_applicable: 'var(--text-dim)',
};

function dot(status) {
  const color = STATUS_COLOR[status] || 'var(--text-dim)';
  return `<div class="dot" style="background:${color};box-shadow:0 0 6px ${color}"></div>`;
}

/** Count of subsystems/plugins that need attention. Pure — no DOM access. */
export function countIssues(health) {
  if (!health) return 0;
  const subsystems = health.subsystems || {};
  const subsystemIssues = Object.values(subsystems).filter((s) => s.status === 'unavailable').length;
  const plugins = health.plugins || {};
  return subsystemIssues + (plugins.error || 0) + (plugins.not_loaded || 0);
}

/** Badge label text, or '' when nothing is degraded (caller hides the badge). */
export function healthBadgeHtml(health) {
  const n = countIssues(health);
  return n > 0 ? `⚠ ${n} degraded` : '';
}

function subsystemRow(key, s) {
  const status = s && s.status ? s.status : 'unavailable';
  const impact = s && s.impact ? `<div class="health-row-impact">${escapeHtml(s.impact)}</div>` : '';
  return `<div class="health-row">
    ${dot(status)}
    <div class="health-row-name">${escapeHtml(key)}</div>
    <div class="health-row-status">${escapeHtml(status)}</div>
    ${impact}
  </div>`;
}

function pluginRow(p) {
  const err = p.last_error ? `<div class="health-row-impact">${escapeHtml(p.last_error)}</div>` : '';
  return `<div class="health-row">
    ${dot(p.status)}
    <div class="health-row-name">${escapeHtml(p.name)}</div>
    <div class="health-row-status">${escapeHtml(p.status)}</div>
    ${err}
  </div>`;
}

/** Full dialog body HTML listing every subsystem and any non-ok plugin. */
export function healthDetailHtml(health) {
  const subsystems = health.subsystems || {};
  const plugins = health.plugins || {};

  const subsystemRows = Object.entries(subsystems).map(([key, s]) => subsystemRow(key, s)).join('');
  const pluginRows = (plugins.entries || []).filter((p) => p.status !== 'ok').map(pluginRow).join('');

  return `<div class="health-section">
    <div class="health-section-title">Subsystems</div>
    ${subsystemRows || '<div class="health-row-empty">Nothing probed yet</div>'}
  </div>
  <div class="health-section">
    <div class="health-section-title">Plugins</div>
    ${pluginRows || '<div class="health-row-empty">All plugins ok</div>'}
  </div>`;
}

/** Fetch /api/health, update the badge, and (re-)render the dialog body. */
export async function refreshHealth() {
  try {
    const r = await fetch(apiUrl('health'));
    if (!r.ok) return;
    const data = await r.json();
    // Exposed for services.js to explain an empty local-services panel
    // ("Docker socket unavailable") without a second fetch.
    window._buoyHealth = data;

    const badge = document.getElementById('health-badge');
    if (badge) {
      const label = healthBadgeHtml(data);
      badge.textContent = label;
      badge.style.display = label ? '' : 'none';
    }

    const body = document.getElementById('health-detail-body');
    if (body) body.innerHTML = healthDetailHtml(data);
  } catch (e) {
    console.error('[buoy] health error:', e);
  }
}

/** Wire the badge click/keyboard and dialog close behavior. */
export function initHealthDetail() {
  const badge = document.getElementById('health-badge');
  const dialog = document.getElementById('health-detail');
  if (!badge || !dialog) return;

  badge.addEventListener('click', () => dialog.showModal());
  badge.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    dialog.showModal();
  });

  dialog.addEventListener('click', (e) => {
    if (e.target === dialog || e.target.closest('.plugin-dialog-close')) dialog.close();
  });
}
