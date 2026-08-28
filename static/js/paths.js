/**
 * Path helpers — build asset/API/WebSocket URLs relative to the configured
 * reverse-proxy base path (see network.base_path in buoy.yaml).
 *
 * The base is read once from a <meta name="buoy-base-path"> tag that
 * server.py templates into index.html at request time.
 */

/**
 * Join a base path and a relative path, collapsing slashes at the seam.
 * Pure function (no DOM/location access) so it can be unit-tested directly.
 *
 * @param {string} base
 * @param {string} rest
 * @returns {string}
 */
export function join(base, rest) {
  const cleanBase = (base || '').replace(/\/+$/, '');
  const cleanRest = (rest || '').replace(/^\/+/, '');
  return `${cleanBase}/${cleanRest}`;
}

// Guarded so this module stays importable under Node (tests/js/*.test.mjs
// exercise the pure join() helper above without a DOM present).
const BASE = typeof document !== 'undefined'
  ? (document.querySelector('meta[name="buoy-base-path"]')?.content || '').replace(/\/+$/, '')
  : '';

export function basePath() {
  return BASE;
}

export function apiUrl(p) {
  return join(BASE, `api/${p.replace(/^\/+/, '')}`);
}

export function staticUrl(p) {
  return join(BASE, `static/${p.replace(/^\/+/, '')}`);
}

export function wsUrl(p) {
  const u = new URL(join(BASE, p), location.href);
  u.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return u.toString();
}

if (typeof window !== 'undefined') {
  window.buoyUrl = { apiUrl, staticUrl, basePath };
}
