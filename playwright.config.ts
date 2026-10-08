import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests/browser",
  use: { baseURL: "http://127.0.0.1:8099", headless: true },
  webServer: {
    command: "python3 scripts/serve_test_ui.py",
    url: "http://127.0.0.1:8099/login",
    reuseExistingServer: false,
  },
});
