import test from 'node:test';
import assert from 'node:assert/strict';

import { countIssues, healthBadgeHtml, healthDetailHtml } from '../../static/js/health.js';

test('healthBadgeHtml is empty when nothing is degraded', () => {
  const health = {
    subsystems: { docker: { status: 'ok' }, nsenter: { status: 'not_applicable' } },
    plugins: { error: 0, not_loaded: 0 },
  };
  assert.equal(healthBadgeHtml(health), '');
  assert.equal(countIssues(health), 0);
});

test('healthBadgeHtml counts unavailable subsystems and errored/not-loaded plugins', () => {
  const health = {
    subsystems: { docker: { status: 'unavailable' }, smartctl: { status: 'ok' } },
    plugins: { error: 1, not_loaded: 1 },
  };
  assert.equal(countIssues(health), 3);
  assert.equal(healthBadgeHtml(health), '⚠ 3 degraded');
});

test('countIssues and healthBadgeHtml handle a missing health object', () => {
  assert.equal(countIssues(null), 0);
  assert.equal(countIssues(undefined), 0);
});

test('healthDetailHtml escapes a hostile subsystem impact string', () => {
  const html = healthDetailHtml({
    subsystems: {
      docker: { status: 'unavailable', impact: '<script>alert(1)</script>' },
    },
    plugins: { entries: [] },
  });
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;script&gt;'));
});

test('healthDetailHtml escapes a hostile plugin name and last_error', () => {
  const html = healthDetailHtml({
    subsystems: {},
    plugins: {
      entries: [
        {
          id: 'evil',
          name: '<img src=x onerror=alert(1)>',
          status: 'error',
          last_error: '"><script>alert(2)</script>',
        },
      ],
    },
  });
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('<script>alert(2)'));
  assert.ok(html.includes('&lt;img'));
});

test('healthDetailHtml omits ok plugins from the listing', () => {
  const html = healthDetailHtml({
    subsystems: {},
    plugins: {
      entries: [{ id: 'good', name: 'Good Plugin', status: 'ok', last_error: null }],
    },
  });
  assert.ok(!html.includes('Good Plugin'));
  assert.ok(html.includes('All plugins ok'));
});

test('healthDetailHtml lists every subsystem regardless of status', () => {
  const html = healthDetailHtml({
    subsystems: {
      docker: { status: 'ok', impact: '' },
      nsenter: { status: 'not_applicable', impact: '' },
    },
    plugins: { entries: [] },
  });
  assert.ok(html.includes('docker'));
  assert.ok(html.includes('nsenter'));
});
