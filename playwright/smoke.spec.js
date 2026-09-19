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

test('live log viewer streams, follow-toggles, and filters (OSS-1551)', async ({ page }) => {
  const pageErrors = [];
  const consoleErrors = [];
  page.on('pageerror', (err) => pageErrors.push(err.message));
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  await page.goto('/');
  await page.click('.gauge[data-detail="containers"]');
  await page.click('.ctr[data-ctr-name]');
  await page.click('.ctr-btn-logs');

  const viewer = page.locator('.ctr-logs');
  await expect(viewer).toBeVisible();

  const lines = page.locator('.ctr-log-line');
  await expect(lines.first()).toBeVisible({ timeout: 5000 });
  const initialCount = await lines.count();

  // Demo mode emits ~1 synthetic line/sec — following should grow the buffer.
  await expect
    .poll(async () => lines.count(), { timeout: 5000 })
    .toBeGreaterThan(initialCount);

  // Toggling follow off stops growth.
  await page.click('.ctr-logs-follow');
  const pausedCount = await lines.count();
  await page.waitForTimeout(2200);
  expect(await lines.count()).toBe(pausedCount);

  // Filtering hides non-matching lines without removing them from the DOM.
  // Demo lines share a common date-stamp prefix, so filter on the
  // monotonically-increasing "demo log line N" marker instead — it's
  // unique to the first line and won't match any other.
  const firstLineText = await lines.first().textContent();
  const marker = firstLineText.match(/demo log line \d+/)[0];
  await page.fill('.ctr-logs-filter', marker);
  await expect
    .poll(async () => page.locator('.ctr-log-line.hidden').count())
    .toBeGreaterThan(0);

  await page.click('.ctr-logs-close');
  await expect(viewer).toHaveCount(0);

  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});

test('plugin card opens a detail dialog; Esc closes it and restores focus (BUG-330)', async ({ page }) => {
  const pageErrors = [];
  const consoleErrors = [];
  page.on('pageerror', (err) => pageErrors.push(err.message));
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  await page.goto('/');

  const firstCard = page.locator('#plugins-grid .svc').first();
  await expect(firstCard).toBeVisible();
  const cardId = await firstCard.getAttribute('data-plugin-id');

  await firstCard.click();
  const dialog = page.locator('#plugin-detail');
  await expect(dialog).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(firstCard).toBeFocused();

  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});

test('plugin card opens its dialog via Enter and Space (BUG-330)', async ({ page }) => {
  await page.goto('/');

  const cards = page.locator('#plugins-grid .svc');
  await expect(cards.first()).toBeVisible();
  const dialog = page.locator('#plugin-detail');

  await cards.nth(0).focus();
  await page.keyboard.press('Enter');
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();

  await cards.nth(1).focus();
  await page.keyboard.press(' ');
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
});

test('deep link #plugin=github opens the Github detail on load and clears on close (BUG-330)', async ({ page }) => {
  await page.goto('/#plugin=github');

  const dialog = page.locator('#plugin-detail');
  await expect(dialog).toBeVisible();
  await expect(page.locator('#plugin-detail-title')).toHaveText(/github/i);

  await page.locator('.plugin-dialog-close').click();
  await expect(dialog).toBeHidden();
  await expect.poll(() => page.evaluate(() => location.hash)).toBe('');
});

test('plugin detail dialog shows health/config and refresh-now works in demo mode (OSS-2718)', async ({ page }) => {
  const pageErrors = [];
  const consoleErrors = [];
  page.on('pageerror', (err) => pageErrors.push(err.message));
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  await page.goto('/#plugin=github');

  const dialog = page.locator('#plugin-detail');
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('.plugin-health')).toBeVisible();
  await expect(dialog.locator('.plugin-config')).toBeVisible();

  const refreshBtn = dialog.locator('.plugin-refresh-btn');
  await expect(refreshBtn).toBeVisible();
  await refreshBtn.click();
  await expect(refreshBtn).toHaveText(/refreshed|refreshing/i);

  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
