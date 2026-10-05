// Locks formatRate's documented boundary behavior — the 1024-scale
// thresholds and the negative/null clamp — so a unit-boundary regression
// shows up here instead of only being caught indirectly through the
// Playwright demo-mode assertion.
import test from 'node:test';
import assert from 'node:assert/strict';

import { formatRate, formatRatePair } from '../../static/js/format.js';

test('formatRate stays in B/s below the 1024 threshold', () => {
  assert.deepEqual(formatRate(0), { value: '0', unit: 'B/s' });
  assert.deepEqual(formatRate(512), { value: '512', unit: 'B/s' });
  assert.deepEqual(formatRate(1023), { value: '1023', unit: 'B/s' });
});

test('formatRate crosses to KB/s at 1024', () => {
  assert.deepEqual(formatRate(1024), { value: '1.0', unit: 'KB/s' });
  assert.deepEqual(formatRate(2048), { value: '2.0', unit: 'KB/s' });
});

test('formatRate crosses to MB/s at 1024^2', () => {
  assert.deepEqual(formatRate(1024 ** 2), { value: '1.0', unit: 'MB/s' });
  assert.deepEqual(formatRate(5242880), { value: '5.0', unit: 'MB/s' });
});

test('formatRate crosses to GB/s at 1024^3', () => {
  assert.deepEqual(formatRate(1024 ** 3), { value: '1.0', unit: 'GB/s' });
});

test('formatRate clamps negative values to zero', () => {
  assert.deepEqual(formatRate(-100), { value: '0', unit: 'B/s' });
});

test('formatRate treats null/undefined as zero', () => {
  assert.deepEqual(formatRate(null), { value: '0', unit: 'B/s' });
  assert.deepEqual(formatRate(undefined), { value: '0', unit: 'B/s' });
});

test('formatRatePair scales both values to the larger one\'s unit', () => {
  assert.deepEqual(formatRatePair(2048, 5242880), { rxValue: '0.0', txValue: '5.0', unit: 'MB/s' });
});

test('formatRatePair uses B/s when both values are small', () => {
  assert.deepEqual(formatRatePair(100, 200), { rxValue: '100', txValue: '200', unit: 'B/s' });
});

test('formatRatePair clamps negative/null values to zero', () => {
  assert.deepEqual(formatRatePair(-100, null), { rxValue: '0', txValue: '0', unit: 'B/s' });
});
