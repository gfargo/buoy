// Locks the pure helpers behind the live log viewer (static/js/logs.js):
// case-insensitive filtering, the DOM line-cap mirror, and WS URL
// composition under a reverse-proxy base path (see paths.js#wsUrl).
import test from 'node:test';
import assert from 'node:assert/strict';

// wsUrl() (paths.js) resolves against `location`, which isn't a Node
// global — stub it before importing so module-level BASE resolution and
// logsWsUrl() calls both see a consistent origin.
globalThis.location = { href: 'http://localhost/', protocol: 'http:' };

const { matchesFilter, trimBuffer, logsWsUrl } = await import('../../static/js/logs.js');

test('matchesFilter: empty query matches everything', () => {
  assert.equal(matchesFilter('anything at all', ''), true);
  assert.equal(matchesFilter('anything at all', '   '), true);
});

test('matchesFilter: case-insensitive substring match', () => {
  assert.equal(matchesFilter('Request FAILED with 500', 'failed'), true);
  assert.equal(matchesFilter('Request FAILED with 500', 'FAILED'), true);
});

test('matchesFilter: no match returns false', () => {
  assert.equal(matchesFilter('all good here', 'error'), false);
});

test('matchesFilter: handles null/undefined line gracefully', () => {
  assert.equal(matchesFilter(null, 'x'), false);
  assert.equal(matchesFilter(undefined, ''), true);
});

test('matchesFilter: query is not treated as a regex', () => {
  assert.equal(matchesFilter('price is $5.00', '$5.00'), true);
  assert.equal(matchesFilter('anything', '.*'), false);
});

test('trimBuffer: no-op under the cap', () => {
  const lines = ['a', 'b', 'c'];
  assert.deepEqual(trimBuffer(lines, 10), lines);
});

test('trimBuffer: drops from the front (oldest first) over the cap', () => {
  const lines = ['1', '2', '3', '4', '5'];
  assert.deepEqual(trimBuffer(lines, 3), ['3', '4', '5']);
});

test('trimBuffer: exact-size input is unchanged', () => {
  const lines = ['a', 'b'];
  assert.deepEqual(trimBuffer(lines, 2), lines);
});

test('logsWsUrl: composes container name, tail, and protocol without a base path', () => {
  const url = logsWsUrl('grafana', { tail: 100 });
  assert.equal(url, 'ws://localhost/ws/logs/grafana?tail=100');
});

test('logsWsUrl: encodes the container name', () => {
  const url = logsWsUrl('my container', { tail: 50 });
  assert.match(url, /\/ws\/logs\/my%20container\?/);
});

test('logsWsUrl: includes the ticket param when provided', () => {
  const url = logsWsUrl('grafana', { tail: 100, ticket: 'abc123' });
  assert.equal(url, 'ws://localhost/ws/logs/grafana?tail=100&ticket=abc123');
});

test('logsWsUrl: omits params that are not provided', () => {
  const url = logsWsUrl('grafana', {});
  assert.equal(url, 'ws://localhost/ws/logs/grafana');
});

test('logsWsUrl: uses wss when the page is https', () => {
  globalThis.location = { href: 'https://buoy.example.ts.net/', protocol: 'https:' };
  const url = logsWsUrl('grafana', { tail: 100 });
  assert.match(url, /^wss:\/\//);
  globalThis.location = { href: 'http://localhost/', protocol: 'http:' };
});
