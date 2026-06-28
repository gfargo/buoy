/**
 * Buoy — main application module.
 * Fetches config, initializes sub-modules, manages refresh loops.
 */

import { initGauges, updateGauges } from './gauges.js';
import { initDetail } from './detail.js';
import { refreshServices } from './services.js';
import { refreshFleet } from './fleet.js';
import { refreshPlugins } from './plugins.js';
import { connectWebSocket } from './ws.js';

let config = null;

async function fetchConfig() {
  try {
    const r = await fetch('/api/config');
    if (r.ok) return await r.json();
  } catch (e) { console.warn('[buoy] config fetch failed:', e); }
  // Fallback defaults
  return {
    node: { name: 'buoy', tier: '', role: '' },
    network: { tailnet_domain: '', peers: [] },
    theme: { preset: 'terminal' },
    features: { websocket: true, night_mode: 'auto', keyboard_shortcuts: true },
    refresh: { stats_interval: 5, services_interval: 30, fleet_interval: 15 },
  };
}

async function refreshStats() {
  try {
    const r = await fetch('/api/stats');
    if (!r.ok) return;
    const data = await r.json();
    updateGauges(data);
  } catch (e) { console.error('[buoy] stats error:', e); }
}

async function fetchDeployInfo() {
  try {
    const r = await fetch('/api/deploy-info');
    if (!r.ok) return;
    const d = await r.json();

    const versionEl = document.getElementById('footer-version');
    const deployEl = document.getElementById('footer-deploy');

    if (versionEl && d.version) {
      versionEl.textContent = `buoy v${d.version}`;
    }

    if (deployEl) {
      const parts = [];
      if (d.container_started) {
        const dt = new Date(d.container_started);
        parts.push(`built ${dt.toLocaleDateString()}`);
      }
      if (d.git_head) {
        const sha = d.git_head.split(' ')[0];
        parts.push(`sha-${sha}`);
      }
      deployEl.textContent = parts.join(' · ');
    }
  } catch (e) { /* best-effort */ }
}

function applyNightMode(mode) {
  if (mode === 'always') {
    document.body.classList.add('night-mode');
  } else if (mode === 'never') {
    document.body.classList.remove('night-mode');
  } else {
    // auto: 10pm–6am
    const hour = new Date().getHours();
    document.body.classList.toggle('night-mode', hour >= 22 || hour < 6);
  }
}

const SHORTCUTS = [
  { key: 'r', desc: 'Force refresh stats' },
  { key: 't', desc: 'Toggle light/dark theme' },
  { key: 'f', desc: 'Focus fleet section' },
  { key: '1–4', desc: 'Open gauge detail panel' },
  { key: 'Esc', desc: 'Close detail panel / help' },
  { key: '?', desc: 'Show this help' },
];

function showShortcutHelp() {
  let overlay = document.getElementById('kb-help-overlay');
  if (overlay) { overlay.remove(); return; }
  overlay = document.createElement('div');
  overlay.id = 'kb-help-overlay';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-label', 'Keyboard shortcuts');
  overlay.innerHTML = `
    <div class="kb-help-box">
      <div class="kb-help-title">Keyboard Shortcuts</div>
      <dl class="kb-help-list">
        ${SHORTCUTS.map(s => `<div class="kb-row"><dt><kbd>${s.key}</kbd></dt><dd>${s.desc}</dd></div>`).join('')}
      </dl>
      <button class="kb-help-close" aria-label="Close">✕</button>
    </div>`;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  overlay.querySelector('.kb-help-close').addEventListener('click', () => overlay.remove());
  document.body.appendChild(overlay);
  overlay.querySelector('.kb-help-close').focus();
}

function initKeyboardShortcuts() {
  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    switch (e.key) {
      case 'r': refreshStats(); break;
      case 't': {
        const sheet = document.getElementById('theme-stylesheet');
        const isLight = sheet.href.includes('light.css');
        sheet.href = isLight ? '/static/css/themes/terminal.css' : '/static/css/themes/light.css';
        break;
      }
      case 'f':
        document.querySelector('[aria-label="Network fleet"]')?.scrollIntoView({ behavior: 'smooth' });
        break;
      case '1': document.querySelector('.gauge[data-detail="cpu"]')?.click(); break;
      case '2': document.querySelector('.gauge[data-detail="memory"]')?.click(); break;
      case '3': document.querySelector('.gauge[data-detail="disk"]')?.click(); break;
      case '4': document.querySelector('.gauge[data-detail="containers"]')?.click(); break;
      case 'Escape': {
        const helpOverlay = document.getElementById('kb-help-overlay');
        if (helpOverlay) { helpOverlay.remove(); break; }
        document.getElementById('detail-panel')?.classList.remove('open');
        document.querySelectorAll('.gauge.expanded').forEach(g => g.classList.remove('expanded'));
        break;
      }
      case '?': showShortcutHelp(); break;
    }
  });

  // Wire up the footer ? button
  document.getElementById('kb-help-btn')?.addEventListener('click', showShortcutHelp);
}

async function init() {
  config = await fetchConfig();

  // Apply theme
  const themeSheet = document.getElementById('theme-stylesheet');
  if (config.theme.preset === 'light') {
    themeSheet.href = '/static/css/themes/light.css';
  }

  // Night mode
  applyNightMode(config.features.night_mode);
  setInterval(() => applyNightMode(config.features.night_mode), 60000);

  // Keyboard shortcuts
  if (config.features.keyboard_shortcuts) {
    initKeyboardShortcuts();
  }

  // Initialize modules
  initGauges();
  initDetail();

  // Initial data fetch
  await refreshStats();
  await refreshServices(config);
  await refreshFleet(config);
  await refreshPlugins();
  await fetchDeployInfo();

  // Refresh loops
  setInterval(refreshStats, config.refresh.stats_interval * 1000);
  setInterval(() => refreshServices(config), config.refresh.services_interval * 1000);
  setInterval(() => refreshFleet(config), config.refresh.fleet_interval * 1000);
  setInterval(refreshPlugins, (config.refresh.plugins_interval || 60) * 1000);

  // WebSocket (optional, for real-time push)
  if (config.features.websocket) {
    connectWebSocket((data) => {
      if (data.type === 'stats') updateGauges(data.data);
    });
  }
}

init();

export { config };
