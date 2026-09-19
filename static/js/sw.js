/**
 * Buoy service worker — offline app shell + last-known API values.
 *
 * Templated server-side (see sw_js() in src/buoy/server.py): the three
 * placeholder constants below are substituted with real values before this
 * file is served from /sw.js (and <base_path>/sw.js).
 */

const VERSION = '__BUOY_VERSION__';
const BASE = '__BUOY_BASE__';
const PWA_ENABLED = '__BUOY_PWA_ENABLED__' === 'true';

const SHELL_CACHE = `buoy-shell-${VERSION}`;
const DATA_CACHE = `buoy-data-${VERSION}`;
const CURRENT_CACHES = new Set([SHELL_CACHE, DATA_CACHE]);

// Prefix a root-relative path with the configured reverse-proxy base path.
function withBase(path) {
  return `${BASE}${path}`;
}

const SHELL_PATHS = [
  '/',
  '/static/js/buoy.js',
  '/static/js/auth.js',
  '/static/js/detail.js',
  '/static/js/escape.js',
  '/static/js/fleet.js',
  '/static/js/format.js',
  '/static/js/gauges.js',
  '/static/js/panel.js',
  '/static/js/paths.js',
  '/static/js/plugins.js',
  '/static/js/services.js',
  '/static/js/ws.js',
  '/static/css/buoy.css',
  '/static/css/themes/terminal.css',
  '/static/css/themes/light.css',
  '/static/css/themes/solarized.css',
  '/static/css/themes/nord.css',
  '/static/css/themes/high-contrast.css',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon-maskable-512.png',
];

const SHELL_URLS = SHELL_PATHS.map(withBase);

// Fonts aren't base-path-rewritten in buoy.css (they're hardcoded to
// /static/fonts/...), so under a non-stripping proxy they 404 today —
// a pre-existing bug, out of scope here. Precache them best-effort via
// Promise.allSettled so one 404 doesn't reject the whole shell install
// (unlike SHELL_URLS below, which are all known-good same-origin paths).
const FONT_URLS = ['/static/fonts/jetbrains-mono.woff2', '/static/fonts/outfit.woff2'].map(
  withBase
);

// GET-only API reads safe to serve stale while the network is unreachable.
// Never includes /api/container/*, /api/config/debug, or /metrics.
const CACHEABLE_API_PATHS = [
  'config',
  'stats',
  'stats/detail',
  'services',
  'fleet',
  'plugins',
  'plugins/js',
  'deploy-info',
  'health',
];

/**
 * Return true when `pathname` is a same-origin GET API read this worker is
 * allowed to cache-and-fall-back for. Exported as a pure function so
 * tests/js/sw.test.mjs can exercise the allowlist without a SW environment.
 *
 * @param {string} pathname
 * @param {string} base
 * @returns {boolean}
 */
function shouldCacheApiPath(pathname, base) {
  const prefix = `${base}/api/`;
  if (!pathname.startsWith(prefix)) return false;
  const rest = pathname.slice(prefix.length);
  return CACHEABLE_API_PATHS.includes(rest);
}

/**
 * Return true when `pathname` is the app shell root or a precached static
 * asset for the given base path.
 *
 * @param {string} pathname
 * @param {string} base
 * @returns {boolean}
 */
function isShellPath(pathname, base) {
  return pathname === `${base}/` || pathname === base || SHELL_PATHS.map((p) => `${base}${p}`).includes(pathname);
}

// Guarded so this module stays importable under Node (tests/js/sw.test.mjs
// exercises the pure helpers above without a ServiceWorkerGlobalScope
// present — mirrors the `document` guard in paths.js).
if (typeof self !== 'undefined' && typeof self.addEventListener === 'function') {
  if (!PWA_ENABLED) {
    // Tombstone: disabling features.pwa doesn't un-install an already
    // registered worker (a 404 only unregisters on the browser's next
    // update check), so ship a worker whose only job is to clean up after
    // itself and step aside.
    self.addEventListener('install', () => {
      self.skipWaiting();
    });

    self.addEventListener('activate', (event) => {
      event.waitUntil(
        (async () => {
          const names = await caches.keys();
          await Promise.all(
            names.filter((n) => n.startsWith('buoy-')).map((n) => caches.delete(n))
          );
          await self.registration.unregister();
          const clients = await self.clients.matchAll({ type: 'window' });
          clients.forEach((client) => client.navigate(client.url));
        })()
      );
    });
  } else {
    self.addEventListener('install', (event) => {
      event.waitUntil(
        (async () => {
          const cache = await caches.open(SHELL_CACHE);
          await cache.addAll(SHELL_URLS);
          await Promise.allSettled(FONT_URLS.map((url) => cache.add(url)));
        })().then(() => self.skipWaiting())
      );
    });

    self.addEventListener('activate', (event) => {
      event.waitUntil(
        (async () => {
          const names = await caches.keys();
          await Promise.all(
            names
              .filter((n) => n.startsWith('buoy-') && !CURRENT_CACHES.has(n))
              .map((n) => caches.delete(n))
          );
          await self.clients.claim();
        })()
      );
    });

    self.addEventListener('fetch', (event) => {
      const { request } = event;
      if (request.method !== 'GET') return;

      const url = new URL(request.url);
      if (url.origin !== self.location.origin) return;

      if (request.mode === 'navigate') {
        event.respondWith(networkFirstShell(request));
        return;
      }

      if (shouldCacheApiPath(url.pathname, BASE)) {
        event.respondWith(networkFirstData(request));
        return;
      }

      if (isShellPath(url.pathname, BASE)) {
        event.respondWith(cacheFirstShell(request));
      }
    });
  }
}

async function networkFirstShell(request) {
  try {
    return await fetch(request);
  } catch (err) {
    const cache = await caches.open(SHELL_CACHE);
    const cached = await cache.match(withBase('/'));
    if (cached) return cached;
    throw err;
  }
}

async function cacheFirstShell(request) {
  const cache = await caches.open(SHELL_CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) await cache.put(request, response.clone());
  return response;
}

async function networkFirstData(request) {
  const cache = await caches.open(DATA_CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) await cache.put(request, response.clone());
    return response;
  } catch (err) {
    const cached = await cache.match(request);
    if (!cached) throw err;
    const headers = new Headers(cached.headers);
    headers.set('X-Buoy-Cached-At', String(Date.now()));
    return new Response(await cached.clone().blob(), {
      status: cached.status,
      statusText: cached.statusText,
      headers,
    });
  }
}

// Registered as a classic script (see buoy.js), so this file must not
// contain a top-level `import`/`export` — that's a SyntaxError under the
// classic-script grammar and silently kills registration everywhere (the
// rejection is swallowed by the empty .catch() in buoy.js). Expose the pure
// helpers on `self` instead; tests/js/sw.test.mjs loads this file's source
// and evaluates it in a sandboxed `self` to read them back off.
if (typeof self !== 'undefined') {
  self.shouldCacheApiPath = shouldCacheApiPath;
  self.isShellPath = isShellPath;
  self.CACHEABLE_API_PATHS = CACHEABLE_API_PATHS;
}
