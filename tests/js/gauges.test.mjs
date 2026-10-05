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

import { activeAlertsHtml, formatGpuSummary, formatMemUsage } from '../../static/js/gauges.js';

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

test('formatGpuSummary renders a placeholder when there are no GPUs', () => {
  assert.equal(formatGpuSummary([]), 'No GPU detected');
  assert.equal(formatGpuSummary(null), 'No GPU detected');
  assert.equal(formatGpuSummary(undefined), 'No GPU detected');
});

test('formatGpuSummary renders a single GPU', () => {
  const summary = formatGpuSummary([{ name: 'NVIDIA GeForce RTX 3060', util_pct: 42 }]);
  assert.equal(summary, 'NVIDIA GeForce RTX 3060 (42%)');
});

test('formatGpuSummary renders multiple GPUs joined by commas', () => {
  const summary = formatGpuSummary([
    { name: 'NVIDIA GeForce RTX 3060', util_pct: 42 },
    { name: 'AMD GPU (card1)', util_pct: 5 },
  ]);
  assert.equal(summary, 'NVIDIA GeForce RTX 3060 (42%), AMD GPU (card1) (5%)');
});

test('formatGpuSummary falls back to -- when utilization is null', () => {
  const summary = formatGpuSummary([{ name: 'Intel GPU (card0)', util_pct: null }]);
  assert.equal(summary, 'Intel GPU (card0) (--)');
});

test('formatGpuSummary escapes a hostile GPU name instead of injecting markup', () => {
  const summary = formatGpuSummary([{ name: '<img src=x onerror=alert(1)>', util_pct: 1 }]);
  assert.ok(!summary.includes('<img'));
  assert.ok(summary.includes('&lt;img'));
});
