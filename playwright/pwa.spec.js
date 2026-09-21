import { test, expect } from '@playwright/test';

// PWA installability + offline shell (OSS-1540 / buoy#202). Runs against
// both demo instances declared in playwright.config.js: :8090 at the root
// and :8091 under BUOY_NETWORK_BASE_PATH=/buoy, so base-path rewriting of
// the manifest link and service worker scope is exercised too.

test('manifest is linked and parses at the root', async ({ page, request }) => {
  await page.goto('/');
  const href = await page.locator('link[rel=manifest]').getAttribute('href');
  expect(href).toBe('/manifest.webmanifest');

  const res = await request.get('/manifest.webmanifest');
  expect(res.ok()).toBeTruthy();
  expect(res.headers()['content-type']).toContain('manifest');
  const manifest = await res.json();
  expect(manifest.display).toBe('standalone');
  expect(manifest.start_url).toBe('/');
  expect(manifest.icons.some((i) => i.purpose === 'maskable')).toBe(true);
});

test('manifest is linked and parses under a base path', async ({ page, request }) => {
  await page.goto('http://127.0.0.1:8091/buoy/');
  const href = await page.locator('link[rel=manifest]').getAttribute('href');
  expect(href).toBe('/buoy/manifest.webmanifest');

  const res = await request.get('http://127.0.0.1:8091/buoy/manifest.webmanifest');
  expect(res.ok()).toBeTruthy();
  const manifest = await res.json();
  expect(manifest.start_url).toBe('/buoy/');
  expect(manifest.scope).toBe('/buoy/');
});

test('service worker activates and serves the shell offline', async ({ page, context }) => {
  await page.goto('/');
  await page.waitForFunction(() => navigator.serviceWorker.controller);
  await page.reload();
  await page.waitForFunction(() => navigator.serviceWorker.controller);

  await context.setOffline(true);
  await page.reload();

  await expect(page).toHaveTitle(/buoy/);
  await expect(page.locator('.gauge').first()).toBeVisible();

  await context.setOffline(false);
});
