// Locks the escaping guarantee documented in static/js/panel.js: every value
// routed through renderPanelSpec must be neutralized before it reaches
// innerHTML, no matter what a plugin (trusted or not) hands it.
import test from 'node:test';
import assert from 'node:assert/strict';

import { renderPanelSpec } from '../../static/js/panel.js';
import { escapeHtml, safeUrl } from '../../static/js/escape.js';

test('escapeHtml neutralizes script tags and quotes', () => {
  const out = escapeHtml('<script>alert(1)</script>');
  assert.ok(!out.includes('<script>'));
  assert.equal(out, '&lt;script&gt;alert(1)&lt;/script&gt;');
  assert.equal(escapeHtml(`"'&`), '&quot;&#39;&amp;');
});

test('safeUrl rejects javascript: and its obfuscated variants', () => {
  assert.equal(safeUrl('javascript:alert(1)'), '#');
  assert.equal(safeUrl('java\tscript:alert(1)'), '#');
  assert.equal(safeUrl('  javascript:alert(1)'), '#');
  assert.equal(safeUrl('//evil.com'), '#');
  assert.equal(safeUrl('data:text/html,<script>alert(1)</script>'), '#');
});

test('safeUrl allows http(s), mailto, and relative URLs through', () => {
  assert.equal(safeUrl('https://example.com/pr/1'), 'https://example.com/pr/1');
  assert.equal(safeUrl('mailto:a@example.com'), 'mailto:a@example.com');
  assert.equal(safeUrl('/dashboard'), '/dashboard');
});

test('renderPanelSpec escapes a hostile text block', () => {
  const html = renderPanelSpec([{ type: 'text', value: '<img src=x onerror=alert(1)>' }]);
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img'));
});

test('renderPanelSpec escapes hostile table cell values and attributes', () => {
  const html = renderPanelSpec([
    {
      type: 'table',
      columns: ['Peer'],
      rows: [[{ value: '"><script>alert(1)</script>', status: null, truncate: false }]],
    },
  ]);
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(html.includes('&quot;&gt;'));
});

test('renderPanelSpec routes list item hrefs through safeUrl and escapes the label', () => {
  const html = renderPanelSpec([
    {
      type: 'list',
      items: [{ primary: '<b>title</b>', secondary: '', status: null, href: 'javascript:alert(1)' }],
    },
  ]);
  assert.ok(!html.includes('<b>title</b>'));
  assert.ok(html.includes('&lt;b&gt;title&lt;/b&gt;'));
  assert.ok(html.includes('href="#"'));
});

test('renderPanelSpec skips unknown block types instead of throwing', () => {
  assert.equal(renderPanelSpec([{ type: 'made-up-block' }]), '');
});
