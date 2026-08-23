import { test, expect } from '@playwright/test';

// Regression test for buoy#102 / OSS-1299: serving buoy behind a reverse
// proxy at a sub-path (BUOY_NETWORK_BASE_PATH=/buoy) must not yield a blank
// page — every asset/API/WebSocket URL the frontend builds has to resolve
// under the prefix. The webServer in playwright.config.js runs a second
// demo instance on :8091 with the base path set for this test.
test('dashboard loads under a reverse-proxy base path with no console/page errors', async ({ page }) => {
  const pageErrors = [];
  const consoleErrors = [];

  page.on('pageerror', (err) => pageErrors.push(err.message));
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  await page.goto('http://127.0.0.1:8091/buoy/');

  await expect(page).toHaveTitle(/buoy/);

  const gauges = page.locator('.gauge');
  await expect(gauges.first()).toBeVisible();
  expect(await gauges.count()).toBeGreaterThan(0);

  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
