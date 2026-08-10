/**
 * Plugins module — fetches plugin data and renders panels.
 * Supports both default rendering and custom JS from plugins.
 */

import { escapeHtml } from './escape.js';
import { renderPanelSpec } from './panel.js';

let pluginRenderers = {};
let jsLoaded = false;

/**
 * Load custom plugin JS renderers from /api/plugins/js
 */
async function loadPluginJS() {
  if (jsLoaded) return;
  try {
    const r = await fetch('/api/plugins/js');
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
  const statusColor = {
    ok: 'var(--green)',
    warn: 'var(--amber)',
    error: 'var(--red)',
    disabled: 'var(--text-dim)',
    pending: 'var(--text-dim)',
  }[plugin.status] || 'var(--text-dim)';

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

  return `<div class="svc" style="cursor:default">
    <div class="svc-header">
      <span class="svc-icon">${escapeHtml(plugin.icon || '🔌')}</span>
      <div class="svc-name">${escapeHtml(plugin.name)}</div>
      <div style="margin-left:auto;width:6px;height:6px;border-radius:50%;background:${statusColor}"></div>
    </div>
    <div class="svc-desc">${escapeHtml(plugin.summary)}</div>
    ${innerHtml}
  </div>`;
}

/**
 * Fetch and render all active plugins
 */
export async function refreshPlugins() {
  await loadPluginJS();

  try {
    const r = await fetch('/api/plugins');
    if (!r.ok) return;
    const data = await r.json();
    const plugins = data.plugins || [];

    const section = document.getElementById('plugins-section');
    const grid = document.getElementById('plugins-grid');

    if (!plugins.length) {
      section.style.display = 'none';
      return;
    }

    section.style.display = '';
    grid.innerHTML = plugins.map(renderPluginCard).join('');
  } catch (e) {
    console.error('[buoy:plugins] refresh error:', e);
  }
}
