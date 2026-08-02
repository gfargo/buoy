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
