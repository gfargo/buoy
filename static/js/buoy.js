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

function initKeyboardShortcuts() {
  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    switch (e.key) {
      case '1': document.querySelector('.gauge[data-detail="cpu"]')?.click(); break;
      case '2': document.querySelector('.gauge[data-detail="memory"]')?.click(); break;
      case '3': document.querySelector('.gauge[data-detail="disk"]')?.click(); break;
      case '4': document.querySelector('.gauge[data-detail="containers"]')?.click(); break;
      case 'Escape':
        document.getElementById('detail-panel')?.classList.remove('open');
        document.querySelectorAll('.gauge.expanded').forEach(g => g.classList.remove('expanded'));
        break;
    }
  });
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
