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

test('demo mode stubs plugins instead of erroring (BUG-40)', async ({ page, request }) => {
  const res = await request.get('/api/plugins');
  expect(res.ok()).toBeTruthy();
  const { plugins } = await res.json();

  expect(plugins.length).toBeGreaterThan(0);
  for (const plugin of plugins) {
    expect(plugin.status, `${plugin.id} should not be in an error state in demo mode`).not.toBe('error');
    expect(plugin.loaded, `${plugin.id} should have loaded in demo mode`).toBe(true);
  }

  await page.goto('/');
  const pluginCards = page.locator('#plugins-grid .svc');
  await expect(pluginCards.first()).toBeVisible();
  expect(await pluginCards.count()).toBeGreaterThan(0);
});
