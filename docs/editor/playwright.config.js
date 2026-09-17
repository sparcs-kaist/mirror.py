import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.js',
  fullyParallel: true,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:8765/html/',
    acceptDownloads: true,
    trace: 'on-first-retry',
  },
  webServer: {
    command: 'python -m http.server 8765 --bind 127.0.0.1 --directory ../_build',
    url: 'http://127.0.0.1:8765/html/guide/config-editor.html',
    reuseExistingServer: !process.env.CI,
    stderr: 'ignore',
    timeout: 15_000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'], permissions: ['clipboard-read', 'clipboard-write'] } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'], launchOptions: { firefoxUserPrefs: { 'dom.events.testing.asyncClipboard': true } } } },
  ],
});
