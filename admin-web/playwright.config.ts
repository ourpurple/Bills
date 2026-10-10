import { defineConfig } from '@playwright/test'
const python =
  process.platform === 'win32' ? '..\\.venv\\Scripts\\python.exe' : '../.venv/bin/python'
export default defineConfig({
  testDir: './tests',
  workers: 1,
  timeout: 45000,
  expect: { timeout: 8000 },
  use: {
    baseURL: 'http://localhost:5173',
    browserName: 'chromium',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  reporter: [['list']],
  webServer: [
    {
      command: `"${python}" ../backend/scripts/e2e_server.py`,
      url: 'http://127.0.0.1:8000/health/ready',
      reuseExistingServer: false,
      timeout: 45000,
    },
    {
      command: 'npm run dev',
      url: 'http://localhost:5173',
      reuseExistingServer: false,
      timeout: 45000,
    },
  ],
})
