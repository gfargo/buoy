import test from 'node:test';
import assert from 'node:assert/strict';

import { renderServiceCard } from '../../static/js/services.js';

test('renderServiceCard escapes hostile name, desc, and url', () => {
  const html = renderServiceCard({
    name: '<script>alert(1)</script>',
    desc: '"><img src=x onerror=alert(1)>',
    icon: '',
    url: 'https://example.com/"><script>',
    status: null,
  });
  assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;script&gt;'));
});

test('renderServiceCard routes a javascript: url through safeUrl to href="#"', () => {
  const html = renderServiceCard({
    name: 'evil',
    desc: '',
    icon: '',
    url: 'javascript:alert(1)',
    status: null,
  });
  assert.ok(html.includes('href="#"'));
});

test('renderServiceCard maps status "ok" to the green variable', () => {
  const html = renderServiceCard({ name: 'NAS', desc: '', icon: '', url: 'https://nas.local', status: 'ok' });
  assert.ok(html.includes('var(--green)'));
});

test('renderServiceCard maps status "error" to the red variable', () => {
  const html = renderServiceCard({ name: 'NAS', desc: '', icon: '', url: 'https://nas.local', status: 'error' });
  assert.ok(html.includes('var(--red)'));
});

test('renderServiceCard falls back to the plain dot for an unrecognized status', () => {
  const html = renderServiceCard({
    name: 'NAS',
    desc: '',
    icon: '',
    url: 'https://nas.local',
    status: '<script>alert(1)</script>',
  });
  assert.ok(!html.includes('var(--'));
  assert.ok(!html.includes('<script>'));
});

test('renderServiceCard with no status keeps the default dot (no inline color)', () => {
  const html = renderServiceCard({ name: 'grafana', desc: '', icon: '', url: 'https://grafana.local', status: null });
  assert.ok(html.includes('<div class="dot"></div>'));
});

test('renderServiceCard renders both icon and status dot when both are present', () => {
  const html = renderServiceCard({ name: 'NAS', desc: '', icon: '💾', url: 'https://nas.local', status: 'ok' });
  assert.ok(html.includes('svc-icon'));
  assert.ok(html.includes('var(--green)'));
});

test('renderServiceCard marks urlless entries as unclickable', () => {
  const html = renderServiceCard({ name: 'worker', desc: '', icon: '', url: '', status: null });
  assert.ok(html.includes('data-no-url="1"'));
  assert.ok(html.includes('href="#"'));
});
