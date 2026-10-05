import test from 'node:test';
import assert from 'node:assert/strict';

import {
  renderServiceCard,
  groupServices,
  renderGroupLabel,
  renderGroupHeader,
} from '../../static/js/services.js';

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

test('groupServices splits consecutive runs of equal group and preserves order', () => {
  const services = [
    { name: 'grafana', group: 'monitoring' },
    { name: 'prometheus', group: 'monitoring' },
    { name: 'jellyfin', group: 'media' },
    { name: 'bookmark', group: '' },
  ];
  const groups = groupServices(services);
  assert.deepEqual(
    groups.map(g => [g.group, g.items.map(i => i.name)]),
    [
      ['monitoring', ['grafana', 'prometheus']],
      ['media', ['jellyfin']],
      ['', ['bookmark']],
    ],
  );
});

test('groupServices treats a missing group field as ungrouped', () => {
  const groups = groupServices([{ name: 'grafana' }]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].group, '');
});

test('groupServices does not merge two non-adjacent runs of the same group name', () => {
  // The server is the sorting authority; groupServices only partitions
  // consecutive runs, it never re-sorts or merges non-adjacent groups.
  const services = [
    { name: 'a', group: 'x' },
    { name: 'b', group: 'y' },
    { name: 'c', group: 'x' },
  ];
  const groups = groupServices(services);
  assert.equal(groups.length, 3);
});

test('renderGroupLabel escapes a hostile group name', () => {
  const html = renderGroupLabel('<img src=x onerror=alert(1)>');
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img'));
});

test('renderGroupHeader renders a header for a named group', () => {
  const html = renderGroupHeader('monitoring');
  assert.ok(html.includes('svc-group-label'));
  assert.ok(html.includes('monitoring'));
});

test('renderGroupHeader produces no header for the ungrouped run', () => {
  assert.equal(renderGroupHeader(''), '');
});

test('a single unnamed group produces no header in refreshServices-style rendering', () => {
  const groups = groupServices([{ name: 'grafana', group: '' }]);
  assert.equal(renderGroupHeader(groups[0].group), '');
});

test('an ungrouped run alongside named groups produces no header for that run', () => {
  // Regression: a Compose host with named stacks plus a standalone container
  // must not render a blank <h3> for the ungrouped run.
  const services = [
    { name: 'grafana', group: 'monitoring' },
    { name: 'jellyfin', group: 'media' },
    { name: 'standalone', group: '' },
  ];
  const groups = groupServices(services);
  const labels = groups.map(({ group }) => renderGroupHeader(group));
  assert.equal(labels[labels.length - 1], '');
});
