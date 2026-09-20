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
const RATE_UNITS = ['B/s', 'KB/s', 'MB/s', 'GB/s'];

function rateUnitIndex(v) {
  if (v < 1024) return 0;
  if (v < 1024 ** 2) return 1;
  if (v < 1024 ** 3) return 2;
  return 3;
}

export function formatRate(bytesPerSec) {
  const v = Math.max(0, bytesPerSec || 0);
  const i = rateUnitIndex(v);
  const scaled = v / 1024 ** i;
  return { value: scaled.toFixed(i === 0 ? 0 : 1), unit: RATE_UNITS[i] };
}

/**
 * Format a pair of rx/tx throughput values against a single shared unit —
 * the unit the larger of the two would use on its own — so a gauge showing
 * both never has to display two different units side by side (ambiguous
 * about which value they belong to, and prone to overflowing the gauge's
 * value column).
 *
 * Example: formatRatePair(2048, 5242880) -> { rxValue: '0.0', txValue: '5.0', unit: 'MB/s' }
 */
export function formatRatePair(rxBytesPerSec, txBytesPerSec) {
  const rx = Math.max(0, rxBytesPerSec || 0);
  const tx = Math.max(0, txBytesPerSec || 0);
  const i = rateUnitIndex(Math.max(rx, tx));
  const divisor = 1024 ** i;
  const decimals = i === 0 ? 0 : 1;
  return {
    rxValue: (rx / divisor).toFixed(decimals),
    txValue: (tx / divisor).toFixed(decimals),
    unit: RATE_UNITS[i],
  };
}
