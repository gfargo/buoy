// Locks the containers-panel rendering added for OSS-1550 / buoy#192:
// per-container state/health/cpu/mem in the containers detail panel.
import test from 'node:test';
import assert from 'node:assert/strict';

import { sortContainers, containerRowHtml } from '../../static/js/detail.js';

test('sortContainers puts unhealthy first, then non-running, then starting, then the rest', () => {
  const containers = [
    { name: 'ok', state: 'running', health: null },
    { name: 'stopped', state: 'exited', health: null },
    { name: 'sick', state: 'running', health: 'unhealthy' },
    { name: 'booting', state: 'running', health: 'starting' },
  ];
  const sorted = sortContainers(containers).map(c => c.name);
  assert.deepEqual(sorted, ['sick', 'stopped', 'booting', 'ok']);
});

test('sortContainers is stable within a priority group', () => {
  const containers = [
    { name: 'a', state: 'running', health: null },
    { name: 'b', state: 'running', health: null },
    { name: 'c', state: 'running', health: null },
  ];
  const sorted = sortContainers(containers).map(c => c.name);
  assert.deepEqual(sorted, ['a', 'b', 'c']);
});

test('sortContainers does not mutate the input array', () => {
  const containers = [
    { name: 'stopped', state: 'exited', health: null },
    { name: 'ok', state: 'running', health: null },
  ];
  const copy = [...containers];
  sortContainers(containers);
  assert.deepEqual(containers, copy);
});

test('containerRowHtml escapes a hostile container name and status', () => {
  const html = containerRowHtml({
    name: '<img src=x onerror=alert(1)>',
    state: 'running',
    health: null,
    status: '<script>alert(2)</script>',
    cpu_pct: '1.00%',
    mem_usage: '10MiB / 8GiB',
  });
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;img'));
  assert.ok(html.includes('&lt;script&gt;'));
});

test('containerRowHtml never interpolates health/state into the dot class directly', () => {
  const html = containerRowHtml({
    name: 'evil',
    state: 'running" onmouseover="alert(1)',
    health: 'unhealthy" onclick="alert(1)',
    status: 'Up',
  });
  assert.ok(!html.includes('onmouseover'));
  assert.ok(!html.includes('onclick'));
});

test('containerRowHtml renders a placeholder for missing cpu/mem, not "undefined"', () => {
  const html = containerRowHtml({ name: 'grafana', state: 'running', health: null, status: 'Up' });
  assert.ok(!html.includes('undefined'));
  assert.ok(html.includes('--'));
});

test('containerRowHtml marks an unhealthy container with the unhealthy dot class', () => {
  const html = containerRowHtml({ name: 'redis', state: 'running', health: 'unhealthy', status: 'Up (unhealthy)' });
  assert.ok(html.includes('dot-sm unhealthy'));
});

test('containerRowHtml marks a non-running container with the stopped dot class', () => {
  const html = containerRowHtml({ name: 'old-job', state: 'exited', health: null, status: 'Exited (0) 3 hours ago' });
  assert.ok(html.includes('dot-sm stopped'));
});

test('containerRowHtml renders the running healthy state with no extra dot class', () => {
  const html = containerRowHtml({ name: 'grafana', state: 'running', health: 'healthy', status: 'Up (healthy)' });
  assert.ok(html.includes('dot-sm "'));
});
