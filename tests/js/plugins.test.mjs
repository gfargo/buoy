// Covers the plugin detail dialog's rendering path: pluginDetailBodyHtml must
// use the same trusted escaping renderers as the card (renderPanelSpec /
// renderDefaultPlugin) and never the legacy `new Function` custom-JS path.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import { pluginDetailBodyHtml } from '../../static/js/plugins.js';

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

test('grid keydown listener skips nested links, same as the click listener', async () => {
  const source = await readFile(new URL('../../static/js/plugins.js', import.meta.url), 'utf8');

  const keydownStart = source.indexOf("grid.addEventListener('keydown'");
  assert.notEqual(keydownStart, -1, 'expected a keydown listener on the grid');
  const keydownEnd = source.indexOf('});', keydownStart);
  const keydownBody = source.slice(keydownStart, keydownEnd);

  // Without this guard, tabbing to a panel-rendered <a> (e.g. a PR list item)
  // and pressing Enter opens the detail dialog instead of following the link.
  const linkGuardIndex = keydownBody.indexOf("closest('a')");
  const keyCheckIndex = keydownBody.search(/e\.key !== 'Enter'/);
  assert.ok(linkGuardIndex !== -1, 'keydown listener must skip clicks on <a> targets');
  assert.ok(linkGuardIndex < keyCheckIndex, 'link guard must run before the Enter/Space check');
});
