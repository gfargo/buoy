/**
 * Shared escaping helpers — used before interpolating any untrusted string
 * into innerHTML (text or attribute context).
 */

export function escapeHtml(v) {
  return String(v)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Only allow relative URLs or http(s)/mailto schemes; anything else
 * (javascript:, data:, etc.) is replaced with '#'.
 */
export function safeUrl(v) {
  const raw = String(v ?? '');
  // Browsers strip ASCII tab/newline/CR anywhere in a URL, trim leading/
  // trailing C0 controls + space, and treat backslashes the same as forward
  // slashes before parsing its scheme/authority, so an unfiltered check can
  // be bypassed by e.g. "java\tscript:alert(1)", leading whitespace, or
  // "/\evil.com" (normalizes to "//evil.com"). Normalize the same way first.
  const normalized = raw
    .replace(/[\t\n\r]/g, '')
    .replace(/^[\x00-\x20]+|[\x00-\x20]+$/g, '')
    .replace(/\\/g, '/');
  if (/^(https?|mailto):/i.test(normalized)) return normalized;
  if (/^\/\//.test(normalized)) return '#'; // protocol-relative — resolves to an arbitrary host
  if (!/^[a-z][a-z0-9+.-]*:/i.test(normalized)) return normalized; // relative / no scheme
  return '#';
}
