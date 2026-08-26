import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import {
    authorizationValue,
    createAuthClient,
} from '../../static/js/auth.js';

const ORIGIN = 'https://buoy.example/dashboard';
const response = (status) => ({ status });

function makeClient({ responses = [200], promptCredentials = async () => null } = {}) {
  const calls = [];
  const fetchImpl = async (input, options) => {
    calls.push({ input, options });
    return response(responses.shift() ?? 200);
  };
  const client = createAuthClient({
    fetchImpl,
    promptCredentials,
    getLocationHref: () => ORIGIN,
  });
  return { client, calls };
}

test('disabled auth is transparent and never prompts', async () => {
  let prompts = 0;
  const { client, calls } = makeClient({
    responses: [401],
    promptCredentials: async () => { prompts += 1; return { token: 'unused' }; },
  });
  const controller = new AbortController();
  const options = {
    method: 'POST',
    headers: new Headers({ 'X-Request-ID': 'abc' }),
    body: '{}',
    signal: controller.signal,
  };
  client.init({ enabled: false, type: 'token' });

  const result = await client.fetch('/api/container/demo/restart', options);

  assert.equal(result.status, 401);
  assert.equal(calls.length, 1);
  assert.strictEqual(calls[0].options, options);
  assert.equal(prompts, 0);
});

test('credentials are never attached to a cross-origin request', async () => {
  let prompts = 0;
  const { client, calls } = makeClient({
    responses: [401],
    promptCredentials: async () => { prompts += 1; return { token: 'secret' }; },
  });
  client.init({ enabled: true, type: 'token' });

  const result = await client.fetch('https://peer.example/api/container/demo');

  assert.equal(result.status, 401);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options, undefined);
  assert.equal(prompts, 0);
});

test('token mode retries with a Bearer credential', async () => {
  const { client, calls } = makeClient({
    responses: [401, 200],
    promptCredentials: async () => ({ token: 'token-value' }),
  });
  client.init({ enabled: true, type: 'token' });

  const result = await client.fetch('/api/container/demo');

  assert.equal(result.status, 200);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].options.headers.get('Authorization'), 'Bearer token-value');
});

test('Basic mode encodes UTF-8 username and password', async () => {
  const credentials = { username: 'björn', password: 'päss:秘密' };
  const expected = `Basic ${Buffer.from('björn:päss:秘密', 'utf8').toString('base64')}`;
  assert.equal(authorizationValue('basic', credentials), expected);

  const { client, calls } = makeClient({
    responses: [401, 200],
    promptCredentials: async () => credentials,
  });
  client.init({ enabled: true, type: 'basic' });

  await client.fetch('/api/container/demo');

  assert.equal(calls[1].options.headers.get('Authorization'), expected);
});

test('retry preserves existing Headers and request options', async () => {
  const controller = new AbortController();
  const body = JSON.stringify({ action: 'restart' });
  const options = {
    method: 'POST',
    headers: new Headers({
      'Content-Type': 'application/json',
      'X-Request-ID': 'request-123',
    }),
    body,
    signal: controller.signal,
  };
  const { client, calls } = makeClient({
    responses: [401, 200],
    promptCredentials: async () => ({ token: 'secret' }),
  });
  client.init({ enabled: true, type: 'token' });

  await client.fetch('/api/container/demo/restart', options);

  const retry = calls[1].options;
  assert.equal(retry.method, 'POST');
  assert.equal(retry.body, body);
  assert.strictEqual(retry.signal, controller.signal);
  assert.equal(retry.headers.get('Content-Type'), 'application/json');
  assert.equal(retry.headers.get('X-Request-ID'), 'request-123');
  assert.equal(retry.headers.get('Authorization'), 'Bearer secret');
});

test('a 401 gets exactly one credential retry and no loop', async () => {
  let prompts = 0;
  const { client, calls } = makeClient({
    responses: [401, 401, 200],
    promptCredentials: async () => { prompts += 1; return { token: 'rejected' }; },
  });
  client.init({ enabled: true, type: 'token' });

  const result = await client.fetch('/api/container/demo');

  assert.equal(result.status, 401);
  assert.equal(calls.length, 2);
  assert.equal(prompts, 1);
});

test('rejected cached credentials are cleared and re-prompted once', async () => {
  const supplied = [{ token: 'first' }, { token: 'second' }];
  let prompts = 0;
  const { client, calls } = makeClient({
    responses: [401, 200, 401, 200],
    promptCredentials: async () => supplied[prompts++],
  });
  client.init({ enabled: true, type: 'token' });

  await client.fetch('/api/container/demo');
  await client.fetch('/api/container/demo');

  assert.equal(prompts, 2);
  assert.equal(calls[1].options.headers.get('Authorization'), 'Bearer first');
  assert.equal(calls[2].options.headers.get('Authorization'), 'Bearer first');
  assert.equal(calls[3].options.headers.get('Authorization'), 'Bearer second');
});

test('concurrent 401 responses share one in-flight credential prompt', async () => {
  let resolvePrompt;
  let prompts = 0;
  const promptCredentials = () => {
    prompts += 1;
    return new Promise((resolve) => { resolvePrompt = resolve; });
  };
  const { client, calls } = makeClient({
    responses: [401, 401, 200, 200],
    promptCredentials,
  });
  client.init({ enabled: true, type: 'token' });

  const first = client.fetch('/api/container/one');
  const second = client.fetch('/api/container/two');
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(prompts, 1);
  resolvePrompt({ token: 'shared' });

  const results = await Promise.all([first, second]);
  assert.deepEqual(results.map((item) => item.status), [200, 200]);
  assert.equal(calls.length, 4);
  assert.equal(calls[2].options.headers.get('Authorization'), 'Bearer shared');
  assert.equal(calls[3].options.headers.get('Authorization'), 'Bearer shared');
});

test('cancelled credential prompt returns the original 401 without retrying', async () => {
  const { client, calls } = makeClient({
    responses: [401, 200],
    promptCredentials: async () => null,
  });
  client.init({ enabled: true, type: 'token' });

  const result = await client.fetch('/api/container/demo');

  assert.equal(result.status, 401);
  assert.equal(calls.length, 1);
});

test('credential state is memory-only, isolated, and not persisted by source', async () => {
  const first = makeClient({
    responses: [401, 200],
    promptCredentials: async () => ({ token: 'memory-only' }),
  });
  first.client.init({ enabled: true, type: 'token' });
  await first.client.fetch('/api/container/demo');

  const second = makeClient({
    responses: [200],
    promptCredentials: async () => null,
  });
  second.client.init({ enabled: true, type: 'token' });
  await second.client.fetch('/api/container/demo');
  assert.equal(second.calls[0].options, undefined);

  const source = await readFile(new URL('../../static/js/auth.js', import.meta.url), 'utf8');
  for (const forbidden of ['localStorage', 'sessionStorage', 'document.cookie', 'searchParams', 'console.', 'window.']) {
    assert.equal(source.includes(forbidden), false, `auth module must not contain ${forbidden}`);
  }
});

test('only protected detail calls use auth; fleet and WebSockets remain untouched', async () => {
  const detail = await readFile(new URL('../../static/js/detail.js', import.meta.url), 'utf8');
  const buoy = await readFile(new URL('../../static/js/buoy.js', import.meta.url), 'utf8');
  const fleet = await readFile(new URL('../../static/js/fleet.js', import.meta.url), 'utf8');
  const websocket = await readFile(new URL('../../static/js/ws.js', import.meta.url), 'utf8');

  assert.equal((detail.match(/authedFetch\(/g) || []).length, 4);
  assert.match(detail, /import \{ authedFetch \} from '\.\/auth\.js';/);
  assert.match(buoy, /import \{ initAuth \} from '\.\/auth\.js';/);
  assert.match(buoy, /initAuth\(config\.auth\)/);
  assert.equal(fleet.includes('authedFetch'), false);
  assert.equal(websocket.includes('authedFetch'), false);
  assert.equal(websocket.includes('Authorization'), false);
});
