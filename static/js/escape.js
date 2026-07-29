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
  const url = String(v ?? '');
  if (/^(https?|mailto):/i.test(url)) return url;
  if (!/^[a-z][a-z0-9+.-]*:/i.test(url)) return url; // relative / no scheme
  return '#';
}
