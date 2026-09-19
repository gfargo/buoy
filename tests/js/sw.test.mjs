// Unit tests for the pure allowlist/path helpers in static/js/sw.js.
// The service worker's self.addEventListener(...) registration is guarded
// (see the `typeof self !== 'undefined'` check in sw.js) so this module is
// importable under plain Node without a ServiceWorkerGlobalScope.
import test from 'node:test';
import assert from 'node:assert/strict';

import { shouldCacheApiPath, isShellPath, CACHEABLE_API_PATHS } from '../../static/js/sw.js';

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
