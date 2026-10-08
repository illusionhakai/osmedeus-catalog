import { test, expect } from "@playwright/test";
import { mocks } from "./mocks";


test("login, browse, filter, refresh, select and download", async ({ page }) => {
  await mocks(page);
  await page.goto("/login");
  await page.getByLabel("Username", { exact: true }).fill("operator");
  await page.getByLabel("Password", { exact: true }).fill("test-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.goto("/bug-bounty");
  await expect(page.getByRole("heading", { name: "Bug Bounty", exact: true })).toBeVisible();
  await page.getByLabel("Search programs").fill("Example");
  const filtered = page.waitForRequest(r => r.url().includes("/programs?") && r.url().includes("source=chaos") && r.url().includes("platform=hackerone"));
  await page.getByLabel("Source", { exact: true }).selectOption("chaos");
  await page.getByLabel("Platform", { exact: true }).selectOption("hackerone");
  await filtered;
  await page.getByRole("button", { name: "Example program", exact: true }).click();
  await page.getByLabel("Select example.com (in_scope)", { exact: true }).check();
  await page.getByText("Export tools", { exact: true }).click();
  await page.getByRole("button", { name: "Preview selected inputs" }).click();
  await page.getByText("Exports and delivery", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "Export preview" })).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download ZIP" }).click();
  expect((await download).suggestedFilename()).toMatch(/^catalog-.*\.zip$/);
  await page.getByRole("button", { name: "Send preview to Osmedeus" }).click();
  await expect(page.getByText(/Ready in Osmedeus/)).toBeVisible({ timeout: 10000 });
  await expect(page.getByLabel("domains engine path")).toHaveValue("/engine/data/uploads/catalog-targets.txt");
  await expect(page.getByRole("button", { name: "Send preview to Osmedeus" })).toBeDisabled();
  const receipt = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download delivery receipt" }).click();
  expect((await receipt).suggestedFilename()).toMatch(/^delivery-.*\.json$/);
  await page.getByRole("button", { name: "Refresh sources" }).click();
  await expect(page.getByText(/Refresh .*running/)).toBeVisible();
  await expect(page.getByText(/Refresh .*completed/)).toBeVisible({ timeout: 20000 });
});

test("catalog outage does not remove Osmedeus navigation", async ({ page }) => {
  await mocks(page);
  await page.addInitScript(() => localStorage.setItem("osmedeus_token", "catalog-test-token"));
  await page.route("**/catalog-api/v1/**", route => route.fulfill({ status: 503, json: { detail: "Catalog offline" } }));
  await page.goto("/bug-bounty");
  await expect(page.getByRole("heading", { name: "Catalog unavailable" })).toBeVisible();
  const scans = page.getByRole("link", { name: "Scans", exact: true }).first();
  await expect(scans).toBeVisible();
  await scans.click();
  await expect(page).toHaveURL(/\/scans$/);
  await expect(page.getByRole("heading", { name: "Catalog unavailable" })).toHaveCount(0);
});
