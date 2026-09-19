/**
 * Plugins module — fetches plugin data and renders panels.
 * Supports both default rendering and custom JS from plugins.
 */

import { escapeHtml } from './escape.js';
import { renderPanelSpec } from './panel.js';
import { apiUrl } from './paths.js';

let pluginRenderers = {};
let jsLoaded = false;
let latestById = new Map();

const PLUGIN_STATUS_COLOR = {
  ok: 'var(--green)',
  warn: 'var(--amber)',
  error: 'var(--red)',
  disabled: 'var(--text-dim)',
  pending: 'var(--text-dim)',
};

/**
 * Convert an epoch-seconds timestamp into a human-readable "ago" string
 * (e.g. "3m ago", "2h ago"). Returns '' when missing or in the future
 * (guards against clock skew between server and browser).
 */
function formatAgo(epochSeconds) {
  if (!epochSeconds) return '';
  const ms = Date.now() - epochSeconds * 1000;
  if (ms < 0) return 'just now';
  const minutes = Math.floor(ms / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}

/**
 * Load custom plugin JS renderers from /api/plugins/js
 */
async function loadPluginJS() {
  if (jsLoaded) return;
  try {
    const r = await fetch(apiUrl('plugins/js'));
    if (!r.ok) return;
    const js = await r.text();
    if (js.trim()) {
      // Execute the JS to register render functions globally
      const names = (js.match(/function render_\w+/g) || [])
        .map(m => { const name = m.replace('function ', ''); return `"${name}": ${name}`; })
        .join(',');
      const fn = new Function(js + '\nreturn { ' + names + ' };');
      const renderers = fn();
      Object.assign(pluginRenderers, renderers);
    }
    jsLoaded = true;
  } catch (e) {
    console.warn('[buoy:plugins] Failed to load plugin JS:', e);
  }
}

/**
 * Default plugin card renderer (for plugins without custom JS)
 */
function renderDefaultPlugin(plugin) {
  let detailHtml = '';
  if (plugin.detail && typeof plugin.detail === 'object') {
    const entries = Object.entries(plugin.detail).filter(([k, v]) => k !== 'error' && typeof v !== 'object');
    if (entries.length) {
      detailHtml = '<div style="margin-top:0.4rem;font-size:0.5rem;color:var(--text-dim)">' +
        entries.map(([k, v]) => `${escapeHtml(k)}: <span style="color:var(--text)">${escapeHtml(v)}</span>`).join(' · ') +
        '</div>';
    }
    if (plugin.detail.error) {
      detailHtml += `<div style="margin-top:0.3rem;font-size:0.5rem;color:var(--red)">${escapeHtml(plugin.detail.error)}</div>`;
    }
  }
  return detailHtml;
}

/**
 * Render a single plugin card
 */
function renderPluginCard(plugin) {
  const statusColor = PLUGIN_STATUS_COLOR[plugin.status] || 'var(--text-dim)';

  // Prefer the declarative panel spec (trusted, escaping renderer). Fall back
  // to legacy custom JS (new Function, deprecated) only when a plugin still
  // ships frontend_js() instead, then to the generic key-value renderer.
  let innerHtml;
  if (Array.isArray(plugin.panel) && plugin.panel.length) {
    innerHtml = renderPanelSpec(plugin.panel);
  } else {
    const renderFn = pluginRenderers[`render_${plugin.id}`];
    if (renderFn) {
      try {
        innerHtml = renderFn(plugin);
      } catch (e) {
        innerHtml = renderDefaultPlugin(plugin);
      }
    } else {
      innerHtml = renderDefaultPlugin(plugin);
    }
  }

  const ago = formatAgo(plugin.last_collect_at);
  const agoHtml = ago
    ? `<div style="margin-top:0.3rem;font-size:0.5rem;color:var(--text-dim)">updated ${ago}</div>`
    : '';

  let errorHtml = '';
  if (plugin.consecutive_failures || plugin.last_error) {
    const failCount = plugin.consecutive_failures
      ? ` (${plugin.consecutive_failures})`
      : '';
    errorHtml = `<div style="margin-top:0.3rem;font-size:0.5rem;color:var(--red)">⚠ ${escapeHtml(plugin.last_error || 'failing')}${failCount}</div>`;
  }

  return `<div class="svc" role="button" tabindex="0" aria-haspopup="dialog" data-plugin-id="${escapeHtml(plugin.id)}">
    <div class="svc-header">
      <span class="svc-icon">${escapeHtml(plugin.icon || '🔌')}</span>
      <div class="svc-name">${escapeHtml(plugin.name)}</div>
      <div style="margin-left:auto;width:6px;height:6px;border-radius:50%;background:${statusColor}"></div>
    </div>
    <div class="svc-desc">${escapeHtml(plugin.summary)}</div>
    ${innerHtml}
    ${agoHtml}
    ${errorHtml}
    <span class="expand-hint" aria-hidden="true">&#9662; detail</span>
  </div>`;
}

/**
 * Detail body HTML for the plugin dialog — same trusted rendering path as
 * the card (renderPanelSpec / renderDefaultPlugin), never the legacy custom
 * JS renderers (the deprecated `new Function` path), since the dialog has
 * no try/catch boundary around arbitrary plugin code.
 */
export function pluginDetailBodyHtml(plugin) {
  const bodyHtml = Array.isArray(plugin.panel) && plugin.panel.length
    ? renderPanelSpec(plugin.panel)
    : renderDefaultPlugin(plugin);

  let errorHtml = '';
  if (plugin.consecutive_failures || plugin.last_error) {
    const failCount = plugin.consecutive_failures
      ? ` (${plugin.consecutive_failures})`
      : '';
    errorHtml = `<div style="margin-top:0.4rem;font-size:0.6rem;color:var(--red)">⚠ ${escapeHtml(plugin.last_error || 'failing')}${failCount}</div>`;
  }

  if (!bodyHtml && !errorHtml) {
    return '<div style="font-size:0.65rem;color:var(--text-dim)">No detail data</div>';
  }
  return `${bodyHtml}${errorHtml}`;
}

function pluginDialogEls() {
  return {
    dialog: document.getElementById('plugin-detail'),
    icon: document.getElementById('plugin-detail-icon'),
    title: document.getElementById('plugin-detail-title'),
    dot: document.getElementById('plugin-detail-dot'),
    summary: document.getElementById('plugin-detail-summary'),
    ago: document.getElementById('plugin-detail-ago'),
    body: document.getElementById('plugin-detail-body'),
  };
}

function paintPluginDialogHeader(els, plugin) {
  els.icon.textContent = plugin.icon || '🔌';
  els.title.textContent = plugin.name;
  els.dot.style.background = PLUGIN_STATUS_COLOR[plugin.status] || 'var(--text-dim)';
  els.summary.textContent = plugin.summary || '';
  const ago = formatAgo(plugin.last_collect_at);
  els.ago.textContent = ago ? `updated ${ago}` : '';
}

function focusPluginCard(id) {
  document.querySelector(`#plugins-grid .svc[data-plugin-id="${CSS.escape(id)}"]`)?.focus();
}

function clearPluginHash() {
  if (location.hash.startsWith('#plugin=')) {
    history.replaceState(null, '', location.pathname + location.search);
  }
}

export function openPluginDetail(id) {
  if (!id) return;
  const plugin = latestById.get(id);
  if (!plugin) return;

  const els = pluginDialogEls();
  els.dialog.dataset.pluginId = id;
  paintPluginDialogHeader(els, plugin);
  els.body.innerHTML = pluginDetailBodyHtml(plugin);
  els.body.scrollTop = 0;
  els.dialog.showModal();
  location.hash = `#plugin=${encodeURIComponent(id)}`;
}

function closePluginDetail() {
  const { dialog } = pluginDialogEls();
  if (dialog.open) dialog.close();
}

/**
 * Re-render the open dialog's body from a fresh payload without closing it,
 * stealing focus, or resetting scroll position. If the plugin has vanished
 * from the payload (disabled at runtime, etc.) the stale content is kept
 * rather than closing the dialog out from under the user.
 */
function syncOpenPluginDetail() {
  const els = pluginDialogEls();
  if (!els.dialog.open) return;
  const id = els.dialog.dataset.pluginId;
  const plugin = id && latestById.get(id);
  if (!plugin) return;

  paintPluginDialogHeader(els, plugin);
  const nextHtml = pluginDetailBodyHtml(plugin);
  if (nextHtml !== els.body.innerHTML) {
    const scrollTop = els.body.scrollTop;
    els.body.innerHTML = nextHtml;
    els.body.scrollTop = scrollTop;
  }
}

/**
 * Wire the plugin card grid + detail dialog. The grid is a stable ancestor
 * (only its innerHTML is replaced on refresh), so listeners are delegated
 * onto it rather than re-bound per card.
 */
export function initPluginDetail() {
  const grid = document.getElementById('plugins-grid');
  const { dialog } = pluginDialogEls();
  if (!grid || !dialog) return;

  grid.addEventListener('click', (e) => {
    // A card's panel content can include real links (e.g. a PR list) — let
    // those navigate instead of also opening the detail dialog underneath.
    if (e.target.closest('a')) return;
    const card = e.target.closest('.svc[data-plugin-id]');
    if (card) openPluginDetail(card.dataset.pluginId);
  });
  grid.addEventListener('keydown', (e) => {
    // Same rationale as the click listener above: don't hijack Enter on a
    // nested panel link (e.g. a PR list item) before it can navigate.
    if (e.target.closest('a')) return;
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const card = e.target.closest('.svc[data-plugin-id]');
    if (card) { e.preventDefault(); openPluginDetail(card.dataset.pluginId); }
  });

  dialog.addEventListener('click', (e) => {
    if (e.target === dialog || e.target.closest('.plugin-dialog-close')) closePluginDetail();
  });
  // Native Esc fires 'cancel' then 'close'; don't preventDefault() cancel or
  // Esc stops closing the dialog. Do the hash/focus cleanup on 'close' so it
  // also covers the backdrop/✕ paths (dialog.close() fires 'close' too).
  dialog.addEventListener('close', () => {
    const id = dialog.dataset.pluginId;
    clearPluginHash();
    if (id) focusPluginCard(id);
  });
}

/**
 * Open the plugin named by a `#plugin=<id>` deep link, if any. Call once
 * plugins have rendered (so the card + latestById lookup exist). No-op
 * silently when the hash is absent or doesn't match a known plugin.
 */
export function openPluginDetailFromHash() {
  const match = /^#plugin=(.+)$/.exec(location.hash);
  if (!match) return;
  const id = decodeURIComponent(match[1]);
  if (latestById.has(id)) openPluginDetail(id);
}

/**
 * Fetch and render all active plugins
 */
export async function refreshPlugins() {
  await loadPluginJS();

  try {
    const r = await fetch(apiUrl('plugins'));
    if (!r.ok) return;
    const data = await r.json();
    const plugins = data.plugins || [];

    const section = document.getElementById('plugins-section');
    const grid = document.getElementById('plugins-grid');

    latestById = new Map(plugins.map(p => [p.id, p]));

    if (!plugins.length) {
      section.style.display = 'none';
      return;
    }

    section.style.display = '';
    grid.innerHTML = plugins.map(renderPluginCard).join('');
    syncOpenPluginDetail();
  } catch (e) {
    console.error('[buoy:plugins] refresh error:', e);
  }
}
