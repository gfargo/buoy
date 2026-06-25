/**
 * WebSocket module — connects to /ws with auto-reconnect and exponential backoff.
 */

let ws = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_DELAY = 30000;
let onMessageCallback = null;

export function connectWebSocket(onMessage) {
  onMessageCallback = onMessage;
  _connect();
}

function _connect() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${protocol}//${location.host}/ws`;

  try {
    ws = new WebSocket(url);
  } catch (e) {
    _scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    reconnectAttempts = 0;
    // Subscribe to stats channel
    ws.send(JSON.stringify({ type: 'subscribe', channels: ['stats'] }));
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'alert' || data.type === 'alert_resolved') {
        showAlertToast(data);
      }
      if (onMessageCallback) onMessageCallback(data);
    } catch (e) {
      // ignore parse errors
    }
  };

  ws.onclose = () => {
    _scheduleReconnect();
  };

  ws.onerror = () => {
    ws?.close();
  };
}

function _scheduleReconnect() {
  reconnectAttempts++;
  const delay = Math.min(1000 * Math.pow(2, reconnectAttempts - 1), MAX_RECONNECT_DELAY);
  setTimeout(_connect, delay);
}

export function sendMessage(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  }
}

function showAlertToast(alert) {
  // Create toast container if it doesn't exist
  let container = document.getElementById('alert-toasts');
  if (!container) {
    container = document.createElement('div');
    container.id = 'alert-toasts';
    container.style.cssText = 'position:fixed;top:1rem;right:1rem;z-index:9999;display:flex;flex-direction:column;gap:0.5rem;max-width:320px';
    document.body.appendChild(container);
  }

  const isResolved = alert.type === 'alert_resolved';
  const color = isResolved ? 'var(--green)' : alert.level === 'crit' ? 'var(--red)' : 'var(--amber)';
  const icon = isResolved ? '✓' : alert.level === 'crit' ? '⚠' : '△';

  const toast = document.createElement('div');
  toast.style.cssText = `background:var(--surface);border:1px solid ${color};border-radius:6px;padding:0.75rem 1rem;font-size:0.65rem;color:var(--text);animation:fadeIn 0.3s ease-out;display:flex;align-items:center;gap:0.5rem;box-shadow:0 4px 12px rgba(0,0,0,0.3)`;
  toast.innerHTML = `<span style="color:${color};font-size:0.9rem">${icon}</span><span>${alert.message}</span>`;

  container.appendChild(toast);

  // Auto-remove after 8 seconds
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s';
    setTimeout(() => toast.remove(), 300);
  }, 8000);
}
