import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './playwright',
  fullyParallel: true,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:8090',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: [
    {
      command: 'python -m buoy --demo',
      url: 'http://127.0.0.1:8090/api/health',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: 'python -m buoy --demo --port 8091',
      url: 'http://127.0.0.1:8091/buoy/api/health',
      env: { BUOY_NETWORK_BASE_PATH: '/buoy' },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
