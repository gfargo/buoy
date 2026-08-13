import { test, expect } from '@playwright/test';

test('dashboard loads with no console/page errors and renders gauges', async ({ page }) => {
  const pageErrors = [];
  const consoleErrors = [];

  page.on('pageerror', (err) => pageErrors.push(err.message));
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  await page.goto('/');

  await expect(page).toHaveTitle(/buoy/);

  const gauges = page.locator('.gauge');
  await expect(gauges.first()).toBeVisible();
  expect(await gauges.count()).toBeGreaterThan(0);

  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
