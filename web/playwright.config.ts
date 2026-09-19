import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  outputDir: './test-results',
  reporter: 'line',
  use: {
    baseURL: 'http://127.0.0.1:4173',
    channel: 'chrome',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: '..\\.venv\\Scripts\\python.exe -m uvicorn tests.e2e_app:app --app-dir .. --host 127.0.0.1 --port 8000',
      url: 'http://127.0.0.1:8000/health',
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 4173',
      url: 'http://127.0.0.1:4173',
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'mobile', use: { ...devices['iPhone 13'], browserName: 'chromium' } },
  ],
})
