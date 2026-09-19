/**
 * Plugins module — fetches plugin data and renders panels.
 * Supports both default rendering and custom JS from plugins.
 */

import { authedFetch } from './auth.js';
import { escapeHtml } from './escape.js';
import { renderPanelSpec } from './panel.js';
import { apiUrl } from './paths.js';

let pluginRenderers = {};
let jsLoaded = false;
let latestById = new Map();
// id -> last GET /api/plugins/{id} payload (health/config/manifest). Populated
// by openPluginDetail()'s fetch and refreshPluginNow(); the 60s list poll
// (refreshPlugins -> syncOpenPluginDetail) never touches this, so the
// health/config section survives that poll instead of being wiped back down
// to the card-level list payload.
let detailById = new Map();

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
  const panelSpec = plugin.detail_panel ?? plugin.panel;
  const bodyHtml = Array.isArray(panelSpec) && panelSpec.length
    ? renderPanelSpec(panelSpec)
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

/**
 * Health section: last success (absolute + relative), last attempt, collect
 * duration, failure count, full error text, an explicit timeout note when
 * health.timed_out, and the effective/manifest/override/floor refresh
 * interval breakdown from _resolve_interval. Returns '' when the payload has
 * no health block yet (cached-list rendering before the detail fetch lands).
 */
export function pluginHealthHtml(plugin) {
  const health = plugin.health;
  if (!health) return '';

  const successAbs = health.last_collect_at ? new Date(health.last_collect_at * 1000).toLocaleString() : '';
  const successAgo = formatAgo(health.last_collect_at);
  const attemptAbs = health.last_attempt_at ? new Date(health.last_attempt_at * 1000).toLocaleString() : '';
  const attemptAgo = formatAgo(health.last_attempt_at);
  const duration = health.last_collect_duration_ms != null ? `${health.last_collect_duration_ms} ms` : '—';

  const manifest = plugin.manifest || {};
  const intervalParts = [
    `manifest default ${manifest.refresh_interval ?? '—'}s`,
    `config override ${manifest.refresh_interval_override ?? 'none'}`,
    `global floor ${manifest.plugins_interval_floor ?? '—'}s`,
  ];
  const intervalLine = `${manifest.effective_refresh_interval ?? '—'}s (${intervalParts.join(' · ')})`;

  const rows = [
    ['Last success', successAbs ? `${successAbs} (${successAgo || 'just now'})` : 'never'],
    ['Last attempt', attemptAbs ? `${attemptAbs} (${attemptAgo || 'just now'})` : 'never'],
    ['Last duration', duration],
    ['Consecutive failures', String(health.consecutive_failures || 0)],
    ['Refresh interval', intervalLine],
  ];
  const rowsHtml = rows
    .map(([label, value]) => `<tr><td class="plugin-kv-label">${escapeHtml(label)}</td><td class="plugin-kv-value">${escapeHtml(value)}</td></tr>`)
    .join('');

  let errorHtml = '';
  if (health.last_error) {
    const timeoutNote = health.timed_out
      ? `<div class="plugin-health-timeout">timed out after ${escapeHtml(String(health.timeout_seconds))}s</div>`
      : '';
    errorHtml = `<div class="plugin-health-error">${escapeHtml(health.last_error)}</div>${timeoutNote}`;
  }

  return `<div class="plugin-health">
    <h3 class="plugin-section-title">Health</h3>
    <table class="plugin-kv">${rowsHtml}</table>
    ${errorHtml}
  </div>`;
}

/**
 * Manifest & config section: identity fields plus one row per config_schema
 * key (effective value, secret-redacted, origin), and the full disabled-by-
 * config-validation error list when the plugin is disabled. Returns '' when
 * the payload has no manifest block yet.
 */
export function pluginConfigHtml(plugin) {
  const manifest = plugin.manifest;
  if (!manifest) return '';

  const metaRows = [
    ['ID', manifest.id],
    ['Version', manifest.version],
    ['Source', manifest.source],
    ['Description', manifest.description],
  ].filter(([, value]) => value);
  const metaHtml = metaRows.length
    ? `<table class="plugin-kv">${metaRows.map(([label, value]) => `<tr><td class="plugin-kv-label">${escapeHtml(label)}</td><td class="plugin-kv-value">${escapeHtml(value)}</td></tr>`).join('')}</table>`
    : '';

  const configRows = plugin.config || [];
  const configHtml = configRows.length
    ? `<table class="plugin-config-table">
        <thead><tr><th>Key</th><th>Value</th><th>Source</th></tr></thead>
        <tbody>${configRows
          .map(row => `<tr>
            <td>${escapeHtml(row.key)}${row.required ? ' <span title="required">*</span>' : ''}</td>
            <td>${row.value === null || row.value === undefined || row.value === '' ? '<span class="plugin-config-unset">unset</span>' : escapeHtml(row.value)}</td>
            <td class="plugin-config-source">${escapeHtml(row.source)}</td>
          </tr>`)
          .join('')}</tbody>
      </table>`
    : '';

  let errorsHtml = '';
  if (plugin.disabled && plugin.config_errors?.length) {
    errorsHtml = `<div class="plugin-config-errors">${plugin.config_errors.map(err => `<div>${escapeHtml(err)}</div>`).join('')}</div>`;
  }

  return `<div class="plugin-config">
    <h3 class="plugin-section-title">Manifest &amp; config</h3>
    ${metaHtml}
    ${configHtml}
    ${errorsHtml}
  </div>`;
}

/**
 * Refresh-now button. Non-destructive (unlike restartContainer()), so no
 * confirm step — one click fires the request. Delegated click handling in
 * initPluginDetail() reads the plugin id from dialog.dataset.pluginId,
 * since #plugin-detail-body's innerHTML is replaced on every render.
 * Omitted for plugins disabled by config validation, since POST /collect
 * always 400s for them and the config-errors list above already explains
 * why — a button that can only fail isn't useful.
 */
export function pluginRefreshHtml(plugin) {
  if (plugin.disabled) return '';
  return '<div class="plugin-refresh"><button type="button" class="plugin-refresh-btn">&#8635; refresh now</button></div>';
}

function pluginDetailFullBodyHtml(plugin) {
  return pluginDetailBodyHtml(plugin) + pluginHealthHtml(plugin) + pluginConfigHtml(plugin) + pluginRefreshHtml(plugin);
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

/**
 * Open the detail dialog, painting instantly from the cached list entry,
 * then fetching GET /api/plugins/{id} for the health/config/manifest
 * section. If the dialog has moved on to a different plugin (or closed) by
 * the time the fetch resolves, or the fetch fails, the cached rendering is
 * left in place rather than clobbered.
 */
export async function openPluginDetail(id) {
  if (!id) return;
  const plugin = latestById.get(id);
  if (!plugin) return;

  const els = pluginDialogEls();
  els.dialog.dataset.pluginId = id;
  paintPluginDialogHeader(els, plugin);
  els.body.innerHTML = pluginDetailFullBodyHtml(detailById.get(id) || plugin);
  els.body.scrollTop = 0;
  els.dialog.showModal();
  location.hash = `#plugin=${encodeURIComponent(id)}`;

  try {
    const r = await fetch(apiUrl(`plugins/${encodeURIComponent(id)}`));
    if (!r.ok) return;
    const detail = await r.json();
    if (els.dialog.dataset.pluginId !== id) return; // dialog moved on while fetching
    detailById.set(id, detail);
    paintPluginDialogHeader(els, detail);
    els.body.innerHTML = pluginDetailFullBodyHtml(detail);
  } catch (e) {
    console.warn('[buoy:plugins] detail fetch failed:', e);
  }
}

function closePluginDetail() {
  const { dialog } = pluginDialogEls();
  if (dialog.open) dialog.close();
}

/**
 * Re-render the open dialog on the 60s list poll without closing it,
 * stealing focus, or resetting scroll position. The header repaints from
 * the fresh list entry (status dot, "updated Xm ago"), but the body renders
 * from the cached detailById entry (falling back to the list entry before
 * the first detail fetch lands) so the health/config section isn't wiped
 * back down to card-level data every poll. If the plugin has vanished from
 * the payload (disabled at runtime, etc.) the stale content is kept rather
 * than closing the dialog out from under the user.
 */
function syncOpenPluginDetail() {
  const els = pluginDialogEls();
  if (!els.dialog.open) return;
  const id = els.dialog.dataset.pluginId;
  const plugin = id && latestById.get(id);
  if (!plugin) return;

  paintPluginDialogHeader(els, plugin);
  const nextHtml = pluginDetailFullBodyHtml(detailById.get(id) || plugin);
  if (nextHtml !== els.body.innerHTML) {
    const scrollTop = els.body.scrollTop;
    els.body.innerHTML = nextHtml;
    els.body.scrollTop = scrollTop;
  }
}

/**
 * Refresh-now: POST /api/plugins/{id}/collect and re-render the dialog body
 * from the fresh detail payload it returns — no separate GET round-trip.
 * Non-destructive, so unlike restartContainer() there's no confirm step.
 */
async function refreshPluginNow(id, btn) {
  if (!id) return;
  btn.textContent = 'refreshing...';
  btn.disabled = true;
  btn.classList.remove('success', 'error');

  const reset = () => {
    btn.textContent = '↻ refresh now';
    btn.classList.remove('success', 'error');
    btn.disabled = false;
  };

  try {
    const r = await authedFetch(apiUrl(`plugins/${encodeURIComponent(id)}/collect`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    if (r.status === 409) throw new Error('already running');
    if (r.status === 429) throw new Error('rate limited');
    if (r.status === 401) throw new Error('auth required');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);

    const data = await r.json();
    if (!data.demo) {
      detailById.set(id, data);
      const els = pluginDialogEls();
      if (els.dialog.dataset.pluginId === id) {
        paintPluginDialogHeader(els, data);
        els.body.innerHTML = pluginDetailFullBodyHtml(data);
      }
    }

    const freshBtn = document.querySelector('.plugin-refresh-btn') || btn;
    freshBtn.textContent = '✓ refreshed';
    freshBtn.classList.add('success');
    freshBtn.disabled = true;
    setTimeout(() => {
      freshBtn.textContent = '↻ refresh now';
      freshBtn.classList.remove('success');
      freshBtn.disabled = false;
    }, 3000);
  } catch (e) {
    btn.textContent = `✗ ${e.message}`;
    btn.classList.add('error');
    setTimeout(reset, 3000);
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
    if (e.target === dialog || e.target.closest('.plugin-dialog-close')) { closePluginDetail(); return; }
    const refreshBtn = e.target.closest('.plugin-refresh-btn');
    if (refreshBtn) refreshPluginNow(dialog.dataset.pluginId, refreshBtn);
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
