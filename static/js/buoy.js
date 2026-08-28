/**
 * Buoy — main application module.
 * Fetches config, initializes sub-modules, manages refresh loops.
 */

import { initAuth } from './auth.js';
import { initGauges, updateGauges } from './gauges.js';
import { initDetail } from './detail.js';
import { refreshServices } from './services.js';
import { refreshFleet } from './fleet.js';
import { refreshPlugins } from './plugins.js';
import { connectWebSocket, isWebSocketOpen } from './ws.js';
import { apiUrl, staticUrl } from './paths.js';

let config = null;

async function fetchConfig() {
  try {
    const r = await fetch(apiUrl('config'));
    if (r.ok) return await r.json();
  } catch (e) { console.warn('[buoy] config fetch failed:', e); }
  // Fallback defaults
  return {
    node: { name: 'buoy', tier: '', role: '' },
    network: { tailnet_domain: '', peers: [] },
    theme: { preset: 'terminal' },
    auth: { enabled: false, type: null },
    features: { websocket: true, night_mode: 'auto', keyboard_shortcuts: true },
    refresh: { stats_interval: 5, services_interval: 30, fleet_interval: 15 },
  };
}

async function refreshStats() {
  try {
    const r = await fetch(apiUrl('stats'));
    if (!r.ok) return;
    const data = await r.json();
    updateGauges(data);
  } catch (e) { console.error('[buoy] stats error:', e); }
}

async function fetchDeployInfo() {
  try {
    const r = await fetch(apiUrl('deploy-info'));
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
        const current = presetNameFromHref(sheet.href);
        let next;
        if (current === 'light') {
          // Restore whichever dark-family preset was active before we
          // switched to light, instead of always landing on terminal.
          next = 'terminal';
          try { next = localStorage.getItem('buoy-theme-dark') || 'terminal'; } catch (_) { /* ignore */ }
        } else {
          // Remember the dark-family preset so toggling back from light
          // restores it (nord/solarized/high-contrast/terminal), rather
          // than always collapsing to 'light'.
          try { localStorage.setItem('buoy-theme-dark', current); } catch (_) { /* ignore */ }
          next = 'light';
        }
        sheet.href = presetHref(next);
        try { localStorage.setItem('buoy-theme', next); } catch (_) { /* ignore */ }
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

/**
 * Map of known preset names to their stylesheet paths.
 * Extend here when adding new presets; the rest of the code picks them up
 * automatically via presetHref().
 */
const PRESET_FILES = {
  terminal:      staticUrl('css/themes/terminal.css'),
  light:         staticUrl('css/themes/light.css'),
  solarized:     staticUrl('css/themes/solarized.css'),
  nord:          staticUrl('css/themes/nord.css'),
  'high-contrast': staticUrl('css/themes/high-contrast.css'),
};

/**
 * Return the stylesheet href for a given preset name.
 * Falls back to terminal for unknown/undefined presets so we never 404.
 *
 * @param {string} name
 * @returns {string}
 */
function presetHref(name) {
  return PRESET_FILES[name] || PRESET_FILES.terminal;
}

/**
 * Reverse lookup: given a (possibly absolute) stylesheet href, return the
 * preset name it belongs to. Falls back to 'terminal' if it doesn't match
 * a known preset path.
 *
 * @param {string} href
 * @returns {string}
 */
function presetNameFromHref(href) {
  for (const [name, path] of Object.entries(PRESET_FILES)) {
    if (href.includes(path)) return name;
  }
  return 'terminal';
}

/**
 * Determine which theme preset to apply on page load.
 *
 * Precedence (highest → lowest):
 *   1. User's persisted localStorage choice ('buoy-theme')
 *   2. Explicit preset in config (when it's a known preset)
 *   3. OS prefers-color-scheme (dark→terminal, light→light)
 *   4. terminal (hard default)
 *
 * @param {{ preset?: string }} themeConfig
 * @returns {string} preset name
 */
function resolveInitialTheme(themeConfig) {
  // 1. Persisted user choice wins
  try {
    const stored = localStorage.getItem('buoy-theme');
    if (stored && PRESET_FILES[stored]) return stored;
  } catch (_) { /* localStorage unavailable (private mode / sandboxed) */ }

  // 2. Explicit config preset (only when it's a known preset)
  const cfgPreset = themeConfig && themeConfig.preset;
  if (cfgPreset && PRESET_FILES[cfgPreset]) return cfgPreset;

  // 3. OS colour scheme preference
  try {
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
      return 'light';
    }
  } catch (_) { /* matchMedia not available */ }

  // 4. Hard default
  return 'terminal';
}

/**
 * Apply theme.custom key/value pairs as CSS custom properties on <html>.
 * Each key becomes `--<key>` so e.g. { bg: "#ff0000" } sets `--bg`.
 * Inline styles win over stylesheet :root declarations by specificity,
 * so this overlay works regardless of which preset is active.
 *
 * @param {Record<string, string>|null|undefined} custom
 */
function applyCustomTheme(custom) {
  if (!custom || typeof custom !== 'object') return;
  const root = document.documentElement;
  for (const [key, value] of Object.entries(custom)) {
    if (value != null && value !== '') {
      root.style.setProperty(`--${key}`, String(value));
    }
  }
}

async function init() {
  config = await fetchConfig();
  initAuth(config.auth);

  // Apply theme: resolve preset via persisted choice / config / OS preference,
  // then swap the stylesheet if it differs from the default terminal.css that
  // index.html already loaded.
  const themeSheet = document.getElementById('theme-stylesheet');
  const resolvedPreset = resolveInitialTheme(config.theme);
  const resolvedHref = presetHref(resolvedPreset);
  if (!themeSheet.href.endsWith(resolvedHref)) {
    themeSheet.href = resolvedHref;
  }

  // Apply custom CSS variable overrides (theme.custom in buoy.yaml).
  // Called after the preset stylesheet swap so inline vars take precedence.
  applyCustomTheme(config.theme && config.theme.custom);

  // Apply node.tier to the tier badge so gauges.js can read it via data-tier.
  // Also set textContent immediately so the badge shows the tier on first
  // render rather than showing "--" until the first /api/stats response.
  const tierTag = document.getElementById('tier-tag');
  if (tierTag && config.node && config.node.tier) {
    tierTag.dataset.tier = config.node.tier;
    tierTag.textContent = config.node.tier;
  }

  // Apply node.role (e.g. "Database Server") as a descriptive label next
  // to the hostname. Hidden entirely when unset since it's optional.
  const roleTag = document.getElementById('node-role');
  if (roleTag && config.node && config.node.role) {
    roleTag.textContent = config.node.role;
    roleTag.style.display = '';
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
  // Stats polling is suppressed while the WebSocket is open (it already
  // pushes stats updates); polling resumes as a fallback once it's closed.
  setInterval(() => {
    if (!config.features.websocket || !isWebSocketOpen()) refreshStats();
  }, config.refresh.stats_interval * 1000);
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
