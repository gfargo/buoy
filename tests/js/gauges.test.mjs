// Locks the memory-gauge placeholder behavior for BUG-33: mem_used/mem_total
// are null when the platform's metrics are unavailable (e.g. running
// `python -m buoy` locally on macOS), and a template literal must not render
// the literal text "null/null" in that case.
import test from 'node:test';
import assert from 'node:assert/strict';

import { formatMemUsage } from '../../static/js/gauges.js';

test('formatMemUsage renders used/total when both are numbers', () => {
  assert.equal(formatMemUsage(2.1, 8.0), '2.1/8');
  assert.equal(formatMemUsage(0, 0), '0/0');
});

test('formatMemUsage falls back to a placeholder when either value is null', () => {
  assert.equal(formatMemUsage(null, null), '--/--');
  assert.equal(formatMemUsage(null, 8.0), '--/--');
  assert.equal(formatMemUsage(2.1, null), '--/--');
});

test('formatMemUsage falls back to a placeholder when either value is undefined', () => {
  assert.equal(formatMemUsage(undefined, undefined), '--/--');
});
