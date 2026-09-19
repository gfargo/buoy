// Unit tests for the pure allowlist/path helpers in static/js/sw.js.
//
// sw.js is registered via navigator.serviceWorker.register() *without*
// { type: 'module' } (see buoy.js), so the browser parses it as a classic
// script — a top-level import/export there is a SyntaxError that silently
// kills registration. To catch that class of bug, load the file's source
// and run it through vm.Script the same way: a classic script, not an ES
// module. This both verifies it parses as a classic script and gives us a
// `self` sandbox to read the exposed helpers back off (see the
// `self.shouldCacheApiPath = ...` block at the bottom of sw.js).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const swPath = fileURLToPath(new URL('../../static/js/sw.js', import.meta.url));
const swSource = readFileSync(swPath, 'utf8')
  .replace('__BUOY_VERSION__', 'test')
  .replace('__BUOY_BASE__', '')
  .replace('__BUOY_PWA_ENABLED__', 'true');

const sandbox = {};
sandbox.self = sandbox;
vm.createContext(sandbox);
new vm.Script(swSource, { filename: 'sw.js' }).runInContext(sandbox);

const { shouldCacheApiPath, isShellPath, CACHEABLE_API_PATHS } = sandbox;

test('shouldCacheApiPath allows allowlisted GET reads with no base path', () => {
  assert.equal(shouldCacheApiPath('/api/stats', ''), true);
  assert.equal(shouldCacheApiPath('/api/config', ''), true);
  assert.equal(shouldCacheApiPath('/api/health', ''), true);
});

test('shouldCacheApiPath allows allowlisted GET reads under a base path', () => {
  assert.equal(shouldCacheApiPath('/buoy/api/stats', '/buoy'), true);
  assert.equal(shouldCacheApiPath('/buoy/api/plugins/js', '/buoy'), true);
});

test('shouldCacheApiPath rejects container endpoints', () => {
  assert.equal(shouldCacheApiPath('/api/container/foo/logs', ''), false);
  assert.equal(shouldCacheApiPath('/api/container/foo', ''), false);
  assert.equal(shouldCacheApiPath('/api/container/foo/restart', ''), false);
});

test('shouldCacheApiPath rejects the debug config endpoint', () => {
  assert.equal(shouldCacheApiPath('/api/config/debug', ''), false);
});

test('shouldCacheApiPath rejects /metrics', () => {
  assert.equal(shouldCacheApiPath('/metrics', ''), false);
});

test('shouldCacheApiPath rejects a base-path mismatch', () => {
  // A path under a different base than the one configured must not match —
  // this would otherwise let one instance's SW cache another proxy's data.
  assert.equal(shouldCacheApiPath('/api/stats', '/buoy'), false);
});

test('CACHEABLE_API_PATHS never includes container or debug routes', () => {
  for (const entry of CACHEABLE_API_PATHS) {
    assert.ok(!entry.startsWith('container'));
    assert.notEqual(entry, 'config/debug');
  }
});

test('isShellPath matches the root and precached assets', () => {
  assert.equal(isShellPath('/', ''), true);
  assert.equal(isShellPath('/static/js/buoy.js', ''), true);
  assert.equal(isShellPath('/static/icons/icon-512.png', ''), true);
});

test('isShellPath matches under a base path and rejects the root without it', () => {
  assert.equal(isShellPath('/buoy/', '/buoy'), true);
  assert.equal(isShellPath('/buoy/static/js/buoy.js', '/buoy'), true);
  assert.equal(isShellPath('/static/js/buoy.js', '/buoy'), false);
});

test('isShellPath rejects an unrelated path', () => {
  assert.equal(isShellPath('/api/stats', ''), false);
});

test('sw.js parses as a classic script (no top-level import/export)', () => {
  // Regression test: navigator.serviceWorker.register() in buoy.js registers
  // /sw.js without { type: 'module' }, so a top-level import/export here is
  // a SyntaxError in every browser and registration silently fails. The
  // module-level vm.Script call above already proves this by construction —
  // this test just documents the invariant and fails loudly (instead of
  // failing the whole file to load) if it's ever violated again.
  assert.doesNotMatch(swSource, /^\s*(import|export)\b/m);
});
