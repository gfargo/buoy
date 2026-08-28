// Locks the fleet-grid latency badge thresholds so the color coding stays
// in sync with the issue spec (green <50ms, amber <200ms, red >=200ms).
import test from 'node:test';
import assert from 'node:assert/strict';

import { latencyClass } from '../../static/js/fleet.js';

test('latencyClass classifies low latency as good', () => {
  assert.equal(latencyClass(12), 'lat-good');
  assert.equal(latencyClass(49), 'lat-good');
});

test('latencyClass classifies the 50ms boundary as warn', () => {
  assert.equal(latencyClass(50), 'lat-warn');
  assert.equal(latencyClass(199), 'lat-warn');
});

test('latencyClass classifies the 200ms boundary and above as bad', () => {
  assert.equal(latencyClass(200), 'lat-bad');
  assert.equal(latencyClass(9999), 'lat-bad');
});

test('latencyClass returns null for invalid input', () => {
  assert.equal(latencyClass(-1), null);
  assert.equal(latencyClass(undefined), null);
  assert.equal(latencyClass(NaN), null);
  assert.equal(latencyClass('12'), null);
});
