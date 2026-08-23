// Locks the URL-joining behaviour used by static/js/paths.js so a reverse
// proxy base_path never produces double slashes or a missing prefix.
import test from 'node:test';
import assert from 'node:assert/strict';

import { join } from '../../static/js/paths.js';

test('join with empty base', () => {
  assert.equal(join('', 'api/health'), '/api/health');
});

test('join with a configured base path', () => {
  assert.equal(join('/buoy', 'api/health'), '/buoy/api/health');
});

test('join collapses a trailing slash on base', () => {
  assert.equal(join('/buoy/', 'api/health'), '/buoy/api/health');
});

test('join collapses a leading slash on rest', () => {
  assert.equal(join('/buoy', '/api/health'), '/buoy/api/health');
});

test('join collapses duplicate slashes at the seam', () => {
  assert.equal(join('/buoy//', '//api/health'), '/buoy/api/health');
});

test('join with multi-segment base path', () => {
  assert.equal(join('/a/b', 'static/js/buoy.js'), '/a/b/static/js/buoy.js');
});
