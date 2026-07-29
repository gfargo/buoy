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
  // Browsers strip ASCII tab/newline/CR anywhere in a URL and trim leading/
  // trailing C0 controls + space before parsing its scheme, so an unfiltered
  // check can be bypassed by e.g. "java\tscript:alert(1)" or leading
  // whitespace. Normalize the same way before testing.
  const normalized = raw.replace(/[\t\n\r]/g, '').replace(/^[\x00-\x20]+|[\x00-\x20]+$/g, '');
  if (/^(https?|mailto):/i.test(normalized)) return normalized;
  if (!/^[a-z][a-z0-9+.-]*:/i.test(normalized)) return normalized; // relative / no scheme
  return '#';
}
