/**
 * Shared display formatting helpers.
 */

/**
 * Format uptime hours + minutes into a human-readable string.
 * Uses day notation once uptime reaches 24 h exactly (>= 24).
 *
 * Examples:
 *   formatUptime(0, 5)   → "0h 5m"
 *   formatUptime(23, 59) → "23h 59m"
 *   formatUptime(24, 0)  → "1d 0h"
 *   formatUptime(48, 30) → "2d 0h"
 */
export function formatUptime(h, m) {
  if (h >= 24) return Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
  return h + 'h ' + m + 'm';
}

/**
 * Format a bytes/sec throughput value into a compact {value, unit} pair,
 * scaling B/s -> KB/s -> MB/s -> GB/s. Reports bytes (not bits) throughout,
 * matching NIC counters in /proc/net/dev — not the bits/sec convention used
 * on NIC spec sheets.
 *
 * Examples:
 *   formatRate(512)        -> { value: '512', unit: 'B/s' }
 *   formatRate(2048)       -> { value: '2.0', unit: 'KB/s' }
 *   formatRate(5242880)    -> { value: '5.0', unit: 'MB/s' }
 */
export function formatRate(bytesPerSec) {
  const v = Math.max(0, bytesPerSec || 0);
  if (v < 1024) return { value: v.toFixed(0), unit: 'B/s' };
  if (v < 1024 ** 2) return { value: (v / 1024).toFixed(1), unit: 'KB/s' };
  if (v < 1024 ** 3) return { value: (v / 1024 ** 2).toFixed(1), unit: 'MB/s' };
  return { value: (v / 1024 ** 3).toFixed(1), unit: 'GB/s' };
}
