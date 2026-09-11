// Locks the memory-gauge placeholder behavior for BUG-33: mem_used/mem_total
// are null when the platform's metrics are unavailable (e.g. running
// `python -m buoy` locally on macOS), and a template literal must not render
// the literal text "null/null" in that case.
//
// Also locks the active-alerts banner (BUG-12): /api/stats' `alerts` array
// was previously ignored entirely, so loading the page (or the WebSocket
// reconnecting) while an alert was already active showed no indication at
// all — alerts only ever appeared as transient WebSocket toasts.
import test from 'node:test';
import assert from 'node:assert/strict';

import { activeAlertsHtml, formatMemUsage } from '../../static/js/gauges.js';

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

test('activeAlertsHtml renders nothing for no alerts', () => {
  assert.equal(activeAlertsHtml([]), '');
  assert.equal(activeAlertsHtml(null), '');
  assert.equal(activeAlertsHtml(undefined), '');
});

test('activeAlertsHtml renders a warn alert without the crit class', () => {
  const html = activeAlertsHtml([{ metric: 'disk', level: 'warn', message: 'DISK warn: 82 (threshold: 80)' }]);
  assert.ok(html.includes('active-alert-item"'));
  assert.ok(!html.includes('active-alert-item crit'));
  assert.ok(html.includes('△'));
  assert.ok(html.includes('DISK warn: 82 (threshold: 80)'));
});

test('activeAlertsHtml renders a crit alert with the crit class and icon', () => {
  const html = activeAlertsHtml([{ metric: 'disk', level: 'crit', message: 'DISK crit: 95 (threshold: 90)' }]);
  assert.ok(html.includes('active-alert-item crit'));
  assert.ok(html.includes('⚠'));
});

test('activeAlertsHtml renders multiple alerts', () => {
  const html = activeAlertsHtml([
    { metric: 'disk', level: 'crit', message: 'DISK crit: 95 (threshold: 90)' },
    { metric: 'cpu', level: 'warn', message: 'CPU warn: 85 (threshold: 80)' },
  ]);
  assert.ok(html.includes('DISK crit'));
  assert.ok(html.includes('CPU warn'));
});

test('activeAlertsHtml escapes a hostile message instead of injecting markup', () => {
  const html = activeAlertsHtml([
    { metric: 'disk', level: 'warn', message: '<img src=x onerror=alert(1)>' },
  ]);
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img'));
});
