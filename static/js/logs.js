/**
 * Live container log viewer — WebSocket-backed `docker logs --follow`
 * streaming with configurable tail depth, a follow toggle, and
 * client-side search/filter. Falls back to the one-shot
 * `/api/container/{name}/logs` snapshot when streaming is disabled or the
 * socket fails to connect.
 */

import { authedFetch } from './auth.js';
import { escapeHtml } from './escape.js';
import { apiUrl, wsUrl } from './paths.js';

const MAX_DOM_LINES = 2000;
const SCROLL_BOTTOM_THRESHOLD = 20;
const TAIL_OPTIONS = [50, 100, 250, 500, 1000];
const RECONNECT_MAX_DELAY = 15000;

/**
 * Case-insensitive substring match. An empty/whitespace query matches
 * everything (no filter applied).
 */
export function matchesFilter(line, query) {
  const q = String(query ?? '').trim();
  if (!q) return true;
  return String(line ?? '').toLowerCase().includes(q.toLowerCase());
}

/**
 * Cap an array to at most `max` entries, dropping from the front (oldest
 * first) — mirrors the DOM line cap applied in appendLine() below.
 */
export function trimBuffer(lines, max) {
  if (!Array.isArray(lines) || lines.length <= max) return lines;
  return lines.slice(lines.length - max);
}

/**
 * Build the WebSocket URL for a container's live log stream, honoring the
 * configured base path (via paths.js#wsUrl) and optional tail/ticket params.
 */
export function logsWsUrl(name, { tail, ticket } = {}) {
  const params = new URLSearchParams();
  if (tail != null) params.set('tail', String(tail));
  if (ticket) params.set('ticket', ticket);
  const qs = params.toString();
  return wsUrl(`ws/logs/${encodeURIComponent(name)}`) + (qs ? `?${qs}` : '');
}

function tailChoicesFor(defaultTail, maxTail) {
  const choices = TAIL_OPTIONS.filter(t => t <= maxTail);
  if (!choices.includes(defaultTail) && defaultTail <= maxTail) choices.push(defaultTail);
  choices.sort((a, b) => a - b);
  return choices;
}

/**
 * Open (or, if already open, close) a live log viewer for `name`, appended
 * into `panel`. `config` is the `/api/config` payload (auth + logs + features).
 */
export function openLogViewer(name, panel, config) {
  const existing = panel.querySelector('.ctr-logs');
  if (existing) { existing.remove(); return; }

  const defaultTail = config?.logs?.default_tail || 100;
  const maxTail = config?.logs?.max_tail || 1000;
  const tailChoices = tailChoicesFor(defaultTail, maxTail);
  const streamingEnabled = config?.features?.log_streaming !== false;

  const el = document.createElement('div');
  el.className = 'ctr-logs';
  el.innerHTML = `
    <div class="ctr-logs-header">
      <span class="ctr-logs-title">Logs — ${escapeHtml(name)}</span>
      <div class="ctr-logs-toolbar">
        <select class="ctr-logs-tail" aria-label="Tail depth">
          ${tailChoices.map(t => `<option value="${t}"${t === defaultTail ? ' selected' : ''}>${t} lines</option>`).join('')}
        </select>
        <input class="ctr-logs-filter" type="text" placeholder="filter…" aria-label="Filter logs">
        <button class="ctr-logs-follow" type="button" aria-pressed="true">⏸ pause</button>
        <button class="ctr-logs-clear" type="button">clear</button>
        <button class="ctr-logs-close" type="button" aria-label="Close logs">&#10005;</button>
      </div>
    </div>
    <div class="ctr-logs-status"></div>
    <div class="ctr-logs-body" role="log" aria-live="polite"></div>`;
  panel.appendChild(el);

  const body = el.querySelector('.ctr-logs-body');
  const status = el.querySelector('.ctr-logs-status');
  const tailSelect = el.querySelector('.ctr-logs-tail');
  const filterInput = el.querySelector('.ctr-logs-filter');
  const followBtn = el.querySelector('.ctr-logs-follow');
  const clearBtn = el.querySelector('.ctr-logs-clear');
  const closeBtn = el.querySelector('.ctr-logs-close');

  let ws = null;
  let following = true;
  let closed = false;
  let filterQuery = '';
  let reconnectAttempts = 0;
  let reconnectTimer = null;

  const setStatus = (text) => { status.textContent = text; };
  const isPinnedToBottom = () =>
    body.scrollHeight - body.scrollTop - body.clientHeight < SCROLL_BOTTOM_THRESHOLD;

  function appendLine(stream, text) {
    const pinned = isPinnedToBottom();
    const lineEl = document.createElement('div');
    lineEl.className = stream === 'stderr' ? 'ctr-log-line ctr-log-line-err' : 'ctr-log-line';
    lineEl.textContent = text;
    if (!matchesFilter(text, filterQuery)) lineEl.classList.add('hidden');
    body.appendChild(lineEl);
    while (body.children.length > MAX_DOM_LINES) body.removeChild(body.firstChild);
    if (pinned) body.scrollTop = body.scrollHeight;
  }

  function applyFilter() {
    for (const lineEl of body.children) {
      lineEl.classList.toggle('hidden', !matchesFilter(lineEl.textContent, filterQuery));
    }
  }

  function clearReconnect() {
    if (reconnectTimer !== null) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  }

  function disconnect() {
    clearReconnect();
    if (ws) { const s = ws; ws = null; s.onclose = null; s.close(); }
  }

  async function fallbackToSnapshot() {
    try {
      const tail = Number(tailSelect.value) || defaultTail;
      const r = await authedFetch(apiUrl(`container/${encodeURIComponent(name)}/logs?tail=${tail}`));
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      for (const line of d.lines || []) appendLine('stdout', line);
      setStatus('showing last snapshot (streaming unavailable)');
    } catch (e) {
      setStatus('failed to load logs');
    }
  }

  async function connect() {
    if (closed || !following) return;
    setStatus('connecting…');

    let ticket = null;
    if (config?.auth?.enabled) {
      try {
        const r = await authedFetch(apiUrl(`container/${encodeURIComponent(name)}/logs/ticket`));
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        ticket = (await r.json()).ticket;
      } catch (e) {
        setStatus('failed to authenticate — showing last snapshot');
        await fallbackToSnapshot();
        return;
      }
    }
    if (closed || !following) return;

    const tail = Number(tailSelect.value) || defaultTail;
    try {
      ws = new WebSocket(logsWsUrl(name, { tail, ticket }));
    } catch (e) {
      await fallbackToSnapshot();
      return;
    }

    ws.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch (e) { return; }
      if (msg.type === 'log_start') {
        reconnectAttempts = 0;
        setStatus(`streaming (tail ${msg.tail})`);
      } else if (msg.type === 'log') {
        for (const item of msg.lines || []) appendLine(item.stream, item.line);
      } else if (msg.type === 'log_dropped') {
        setStatus(`⚠ ${msg.count} line(s) dropped — client too slow`);
      } else if (msg.type === 'log_end') {
        setStatus(`stream ended (${msg.reason || 'closed'})`);
      }
    };
    ws.onerror = () => { ws?.close(); };
    ws.onclose = () => {
      ws = null;
      if (closed || !following) return;
      reconnectAttempts++;
      const delay = Math.min(1000 * 2 ** (reconnectAttempts - 1), RECONNECT_MAX_DELAY);
      setStatus('disconnected — reconnecting…');
      reconnectTimer = setTimeout(connect, delay);
    };
  }

  function toggleFollow() {
    following = !following;
    followBtn.textContent = following ? '⏸ pause' : '▶ resume';
    followBtn.setAttribute('aria-pressed', String(following));
    if (following) { reconnectAttempts = 0; connect(); } else { disconnect(); setStatus('paused'); }
  }

  tailSelect.addEventListener('change', () => {
    if (following) { disconnect(); reconnectAttempts = 0; connect(); }
  });
  filterInput.addEventListener('input', () => {
    filterQuery = filterInput.value;
    applyFilter();
  });
  followBtn.addEventListener('click', toggleFollow);
  clearBtn.addEventListener('click', () => { body.innerHTML = ''; });
  closeBtn.addEventListener('click', () => {
    closed = true;
    disconnect();
    el.remove();
  });

  if (streamingEnabled) {
    connect();
  } else {
    fallbackToSnapshot();
  }
}
