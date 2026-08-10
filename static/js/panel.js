/**
 * Renders the declarative panel spec (see src/buoy/plugins/panel.py) into
 * HTML. This is the ONLY place plugin data is turned into markup — every
 * value is routed through escapeHtml()/safeUrl() so a malicious/untrusted
 * plugin payload (peer name, log line, PR title, ...) can never inject
 * markup or script into the page.
 *
 * Status is a semantic string ("ok" | "warn" | "error" | "dim" | "info" |
 * null) — the color mapping lives here so plugins never hard-code CSS.
 */

import { escapeHtml, safeUrl } from './escape.js';

const STATUS_COLOR = {
  ok: 'var(--green)',
  warn: 'var(--amber)',
  error: 'var(--red)',
  dim: 'var(--text-dim)',
  info: 'var(--cyan)',
};

function colorFor(status) {
  return STATUS_COLOR[status] || 'var(--text)';
}

function renderText(block) {
  return `<div style="font-size:0.6rem;color:${colorFor(block.status)}">${escapeHtml(block.value ?? '')}</div>`;
}

function renderKeyvalue(block) {
  const rows = block.rows || [];
  if (!rows.length) return '';
  const trs = rows
    .map(
      r =>
        `<tr><td style="padding:0.15rem 0.3rem;color:var(--text-dim)">${escapeHtml(r.label ?? '')}</td>` +
        `<td style="padding:0.15rem 0.3rem;color:${colorFor(r.status)}">${escapeHtml(r.value ?? '')}</td></tr>`
    )
    .join('');
  return `<table style="width:100%;border-collapse:collapse;font-size:0.55rem">${trs}</table>`;
}

function renderTableCell(c) {
  const styles = ['padding:0.2rem 0.4rem', `color:${colorFor(c.status)}`];
  if (c.truncate) {
    styles.push('max-width:240px', 'overflow:hidden', 'text-overflow:ellipsis', 'white-space:nowrap');
  } else {
    styles.push('white-space:nowrap');
  }
  if (c.mono) {
    styles.push("font-family:'JetBrains Mono',monospace");
  }
  return `<td style="${styles.join(';')}">${escapeHtml(c.value ?? '')}</td>`;
}

function renderTable(block) {
  const columns = block.columns || [];
  const rows = block.rows || [];
  const thead =
    '<tr>' +
    columns
      .map(
        c =>
          `<th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">${escapeHtml(c)}</th>`
      )
      .join('') +
    '</tr>';
  const tbody = rows.map(row => '<tr>' + (row || []).map(renderTableCell).join('') + '</tr>').join('');
  return `<table style="width:100%;border-collapse:collapse;font-size:0.5rem">${thead}${tbody}</table>`;
}

function renderBadges(block) {
  const items = block.items || [];
  if (!items.length) return '';
  const chips = items
    .map(item => {
      const hasDot = item.dot !== false;
      const dot = hasDot
        ? `<div style="width:6px;height:6px;border-radius:50%;background:${colorFor(item.status)}"></div>`
        : '';
      // With a dot, the dot carries the status color and the label stays neutral.
      // Without one, the label itself is colored so status is still visible.
      const textColor = hasDot ? 'var(--text)' : colorFor(item.status);
      return `<div style="display:inline-flex;align-items:center;gap:0.25rem;font-size:0.5rem;padding:0.15rem 0.4rem;border:1px solid var(--border);border-radius:3px;color:${textColor}">${dot}${escapeHtml(item.label ?? '')}</div>`;
    })
    .join('');
  return `<div style="display:flex;flex-wrap:wrap;gap:0.3rem">${chips}</div>`;
}

function renderBar(block) {
  const pct = Math.max(0, Math.min(100, Number(block.pct) || 0));
  const color = colorFor(block.status ?? 'info');
  const label = block.label
    ? `<div style="font-size:0.55rem;color:var(--text-dim);margin-top:0.3rem">${escapeHtml(block.label)}</div>`
    : '';
  return `<div style="padding:0.3rem 0"><div style="height:6px;background:var(--border);border-radius:3px;overflow:hidden"><div style="height:100%;width:${pct}%;background:${color};border-radius:3px"></div></div>${label}</div>`;
}

function renderSparkline(block) {
  const values = (block.values || []).filter(v => typeof v === 'number' && !Number.isNaN(v));
  if (values.length < 2) return '';
  const max = Math.max(...values) || 1;
  const H = 20;
  const bw = Math.max(3, Math.floor(120 / values.length));
  const color = colorFor(block.status ?? 'info');
  const bars = values
    .map((v, i) => {
      const h = Math.max(2, Math.round((v / max) * H));
      const fill = i === values.length - 1 ? color : 'var(--border)';
      return `<rect x="${i * bw}" y="${H - h}" width="${bw - 1}" height="${h}" fill="${fill}"/>`;
    })
    .join('');
  return `<svg width="${values.length * bw}" height="${H}" style="display:block;margin-bottom:0.3rem">${bars}</svg>`;
}

function renderList(block) {
  const items = block.items || [];
  if (!items.length) return '';
  return items
    .map(item => {
      const color = colorFor(item.status);
      const primary = item.href
        ? `<a href="${escapeHtml(safeUrl(item.href))}" target="_blank" rel="noopener noreferrer" style="color:${color};text-decoration:none">${escapeHtml(item.primary ?? '')}</a>`
        : `<span style="color:${color}">${escapeHtml(item.primary ?? '')}</span>`;
      const secondary = item.secondary
        ? `<div style="color:var(--text-dim);font-size:0.45rem">${escapeHtml(item.secondary)}</div>`
        : '';
      return `<div style="font-size:0.55rem;padding:0.2rem 0;border-bottom:1px solid var(--border)">${primary}${secondary}</div>`;
    })
    .join('');
}

const RENDERERS = {
  text: renderText,
  keyvalue: renderKeyvalue,
  table: renderTable,
  badges: renderBadges,
  bar: renderBar,
  sparkline: renderSparkline,
  list: renderList,
};

/**
 * Render a plugin's panel spec (an array of block dicts) into a single HTML
 * string. Unknown block types are skipped rather than raising, so a plugin
 * built against a newer spec degrades gracefully on an older frontend.
 */
export function renderPanelSpec(blocks) {
  if (!Array.isArray(blocks) || !blocks.length) return '';
  return blocks
    .map(block => {
      const renderer = block && RENDERERS[block.type];
      if (!renderer) return '';
      try {
        return renderer(block);
      } catch (e) {
        return '';
      }
    })
    .join('');
}
