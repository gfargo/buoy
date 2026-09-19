// Covers the plugin detail dialog's rendering path: pluginDetailBodyHtml must
// use the same trusted escaping renderers as the card (renderPanelSpec /
// renderDefaultPlugin) and never the legacy `new Function` custom-JS path.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import { pluginDetailBodyHtml, pluginHealthHtml, pluginConfigHtml, pluginRefreshHtml } from '../../static/js/plugins.js';

test('pluginDetailBodyHtml renders the panel spec when present', () => {
  const html = pluginDetailBodyHtml({
    id: 'demo',
    panel: [{ type: 'text', value: 'hello', status: 'ok' }],
  });
  assert.ok(html.includes('hello'));
});

test('pluginDetailBodyHtml falls back to the key/value dump when panel is absent', () => {
  const html = pluginDetailBodyHtml({
    id: 'demo',
    panel: [],
    detail: { region: 'us-east' },
  });
  assert.ok(html.includes('region'));
  assert.ok(html.includes('us-east'));
});

test('pluginDetailBodyHtml escapes hostile summary/detail/panel values', () => {
  const html = pluginDetailBodyHtml({
    id: 'demo',
    panel: [{ type: 'text', value: '<img src=x onerror=alert(1)>', status: 'ok' }],
    last_error: '<script>alert(1)</script>',
    consecutive_failures: 2,
  });
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;img'));
  assert.ok(html.includes('&lt;script&gt;'));
});

test('pluginDetailBodyHtml escapes hostile key/value detail entries', () => {
  const html = pluginDetailBodyHtml({
    id: 'demo',
    panel: [],
    detail: { '<b>key</b>': '<script>alert(1)</script>' },
  });
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;script&gt;'));
});

test('pluginDetailBodyHtml shows a placeholder when there is no panel or detail data', () => {
  const html = pluginDetailBodyHtml({ id: 'demo', panel: [], detail: null });
  assert.notEqual(html, '');
  assert.ok(html.includes('No detail data'));
});

test('plugins.js reaches renderPanelSpec/renderDefaultPlugin, never the legacy custom-JS renderers, for the detail path', async () => {
  const source = await readFile(new URL('../../static/js/plugins.js', import.meta.url), 'utf8');

  // The legacy `new Function` custom-JS renderer lookup must appear exactly
  // once in the module, and only inside the card renderer — never reachable
  // from pluginDetailBodyHtml / the dialog.
  const lookups = source.match(/pluginRenderers\[/g) || [];
  assert.equal(lookups.length, 1);

  const cardStart = source.indexOf('function renderPluginCard');
  const detailStart = source.indexOf('export function pluginDetailBodyHtml');
  assert.ok(cardStart !== -1 && detailStart !== -1);
  const lookupIndex = source.indexOf('pluginRenderers[');
  assert.ok(lookupIndex > cardStart && lookupIndex < detailStart, 'lookup must live in renderPluginCard, before pluginDetailBodyHtml');

  const detailBody = source.slice(detailStart);
  assert.equal(detailBody.includes('pluginRenderers'), false);
});

test('plugin card markup is clickable, not the old cursor:default styling', async () => {
  const source = await readFile(new URL('../../static/js/plugins.js', import.meta.url), 'utf8');
  assert.equal(source.includes('cursor:default'), false);
  assert.match(source, /aria-haspopup="dialog"/);
  assert.match(source, /role="button"/);
  assert.match(source, /data-plugin-id/);
});

test('pluginDetailBodyHtml prefers detail_panel over panel when both are present', () => {
  const html = pluginDetailBodyHtml({
    id: 'demo',
    panel: [{ type: 'text', value: 'capped', status: 'ok' }],
    detail_panel: [{ type: 'text', value: 'full detail', status: 'ok' }],
  });
  assert.ok(html.includes('full detail'));
  assert.ok(!html.includes('capped'));
});

test('pluginHealthHtml returns empty string when the payload has no health block', () => {
  assert.equal(pluginHealthHtml({ id: 'demo' }), '');
});

test('pluginHealthHtml renders success/attempt/duration/failures and the interval breakdown', () => {
  const html = pluginHealthHtml({
    id: 'demo',
    health: {
      last_collect_at: 1700000000,
      last_attempt_at: 1700000005,
      last_collect_duration_ms: 42,
      last_error: null,
      consecutive_failures: 0,
      timed_out: false,
      timeout_seconds: 30,
    },
    manifest: {
      refresh_interval: 60,
      refresh_interval_override: 90,
      plugins_interval_floor: 30,
      effective_refresh_interval: 90,
    },
  });
  assert.ok(html.includes('42 ms'));
  assert.ok(html.includes('90s'));
  assert.ok(html.includes('manifest default 60s'));
  assert.ok(html.includes('config override 90'));
  assert.ok(html.includes('global floor 30s'));
});

test('pluginHealthHtml shows the explicit timed-out note when health.timed_out', () => {
  const html = pluginHealthHtml({
    id: 'demo',
    health: {
      last_collect_at: null,
      last_attempt_at: 1700000000,
      last_collect_duration_ms: 30001,
      last_error: 'collect timed out',
      consecutive_failures: 3,
      timed_out: true,
      timeout_seconds: 30,
    },
    manifest: {},
  });
  assert.ok(html.includes('timed out after 30s'));
  assert.ok(html.includes('collect timed out'));
});

test('pluginHealthHtml escapes a hostile last_error', () => {
  const html = pluginHealthHtml({
    id: 'demo',
    health: {
      last_collect_at: null,
      last_attempt_at: null,
      last_collect_duration_ms: null,
      last_error: '<script>alert(1)</script>',
      consecutive_failures: 1,
      timed_out: false,
      timeout_seconds: 30,
    },
    manifest: {},
  });
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;script&gt;'));
});

test('pluginConfigHtml returns empty string when the payload has no manifest block', () => {
  assert.equal(pluginConfigHtml({ id: 'demo' }), '');
});

test('pluginConfigHtml renders manifest identity fields and config rows', () => {
  const html = pluginConfigHtml({
    id: 'demo',
    manifest: { id: 'github', version: '1.0.0', source: 'builtin', description: 'GitHub plugin' },
    config: [
      { key: 'token', value: '***REDACTED***', secret: true, source: 'yaml', required: true, type: 'string' },
      { key: 'timeout', value: 30, secret: false, source: 'default', required: false, type: 'integer' },
    ],
    disabled: false,
    config_errors: [],
  });
  assert.ok(html.includes('github'));
  assert.ok(html.includes('builtin'));
  assert.ok(html.includes('token'));
  assert.ok(html.includes('***REDACTED***'));
  assert.ok(html.includes('yaml'));
  assert.ok(html.includes('timeout'));
  assert.ok(html.includes('default'));
});

test('pluginConfigHtml escapes hostile config keys/values/sources', () => {
  const html = pluginConfigHtml({
    id: 'demo',
    manifest: { id: 'demo' },
    config: [
      {
        key: '<img src=x onerror=alert(1)>',
        value: '<script>alert(1)</script>',
        secret: false,
        source: '<b>yaml</b>',
        required: false,
      },
    ],
    disabled: false,
    config_errors: [],
  });
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('<b>yaml</b>'));
  assert.ok(html.includes('&lt;img'));
  assert.ok(html.includes('&lt;script&gt;'));
});

test('pluginConfigHtml shows the full config_errors list only when disabled', () => {
  const disabledHtml = pluginConfigHtml({
    id: 'demo',
    manifest: { id: 'demo' },
    config: [],
    disabled: true,
    config_errors: ["demo: missing required field 'token'", "demo: invalid value for 'port'"],
  });
  assert.ok(disabledHtml.includes('missing required field'));
  assert.ok(disabledHtml.includes('invalid value'));

  const enabledHtml = pluginConfigHtml({
    id: 'demo',
    manifest: { id: 'demo' },
    config: [],
    disabled: false,
    config_errors: ["demo: missing required field 'token'"],
  });
  assert.ok(!enabledHtml.includes('missing required field'));
});

test('pluginConfigHtml marks an unset value distinctly instead of leaving it blank', () => {
  const html = pluginConfigHtml({
    id: 'demo',
    manifest: { id: 'demo' },
    config: [{ key: 'nickname', value: null, secret: false, source: 'unset', required: false }],
    disabled: false,
    config_errors: [],
  });
  assert.ok(html.includes('unset'));
});

test('pluginRefreshHtml renders a non-destructive refresh button', () => {
  const html = pluginRefreshHtml();
  assert.match(html, /class="plugin-refresh-btn"/);
  assert.ok(!html.includes('confirm'));
});
