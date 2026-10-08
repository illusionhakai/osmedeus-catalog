import { test, expect, Page } from "@playwright/test";
import { mocks } from "./mocks";

const org = "00000000-0000-0000-0000-000000000001";
async function setup(page: Page) {
  await mocks(page);
  await page.addInitScript(() => localStorage.setItem("osmedeus_token", "catalog-test-token"));
  await page.route("**/osm/api/orgs", route => route.fulfill({ json: { data: [{ uuid: org, name: "Fixture org", is_default: true }] } }));
  await page.route("**/catalog-api/v1/programs?**", route => {
    const url = new URL(route.request().url()); const second = url.searchParams.get("offset") === "50";
    expect(url.searchParams.get("scannable_only")).toBe("true");
    return route.fulfill({ json: { items: [{ id: second ? 51 : 1, name: second ? "Second program" : "First program", platform: "fixture", sources: "chaos", matching_asset_count: 3, domain_count: 2, wildcard_count: 1 }], total: 51, has_more: !second } });
  });
  const state = { starts: 0, previews: [] as Record<string, unknown>[], status: "prepared", exists: false, changed: false, itemStatus: "running", mixed: false, failPreview: false, delayPreview: false, timeoutPreview: false, releasePreview: null as (() => void) | null };
  function batch() {
    const request = state.previews.at(-1) || {};
    const kinds = (state.mixed ? ["domains", "ips", "cidrs", "discovery-roots"] : ["domains"]).filter(k => k !== "discovery-roots" || request.convert_wildcards !== false);
    const items = kinds.map((kind, index) => ({ id: `item-${index + 1}`, program_name: "First program", kind,
      workflow: { name: kind === "domains" ? request.scan_mode === "full" ? "domain-standard" : "enum-subdomain" : kind === "discovery-roots" ? "enum-subdomain" : "cidr", kind: kind === "discovery-roots" || (kind === "domains" && request.scan_mode !== "full") ? "module" : "flow", params: {} },
      target_count: kind === "domains" ? 2 : 1, accepted_count: state.starts ? 2 : undefined, job_id: state.starts ? "job-1" : undefined,
      status: state.starts ? state.itemStatus : "pending", runs: state.starts ? [{ run_uuid: "run-1", target: "one.example", status: "running", total_steps: 2, completed_steps: 1 }] : [], attempts: [] }));
    return { id: "batch-1", export_id: "a".repeat(32), created_at: "2026-10-05T12:00:00Z", status: state.status, catalog_changed: state.changed, credentials_available: true, cancel_requested: state.status === "cancel_requested", organization: { name: "Fixture org" }, request, history: [], delivery: state.starts ? { status: "ready" } : null,
      manifest: { created_at: "2026-10-05T12:00:00Z", source_snapshots: {}, programs: [{ id: 1, name: "First program", counts: Object.fromEntries(items.map(i => [i.kind, i.target_count])), policy_links: [], conversions: kinds.includes("discovery-roots") ? [{ from: "*.wild.example", to: "wild.example", apex_authorized: false }] : [], omitted: [{ value: "denied.example", reason: "out_of_scope" }], exclusions: [{ value: "denied.example" }] }] }, items };
  }
  await page.route("**/catalog-api/v1/scan-batches**", async route => {
    const url = new URL(route.request().url());
    if (route.request().method() === "GET") {
      const { manifest: _manifest, request: _request, history: _history, ...summary } = batch();
      return route.fulfill({ json: url.pathname.endsWith("/scan-batches") ? { items: state.exists ? [summary] : [] } : batch() });
    }
    if (url.pathname.endsWith("/scan-batches")) {
      if (state.timeoutPreview) return route.fulfill({ status: 504, contentType: "text/html", body: "Gateway timeout" });
      if (state.failPreview) return route.fulfill({ status: 422, json: { detail: "domains workflow enum-subdomain: Engine returned HTTP 404" } });
      state.previews.push(route.request().postDataJSON()); state.exists = true; state.status = "prepared";
      if (state.delayPreview) await new Promise<void>(resolve => { state.releasePreview = resolve; });
    } else {
      expect(route.request().postDataJSON().confirmed).toBe(true);
      if (url.pathname.endsWith("/start")) { state.starts++; state.status = "running"; }
      if (url.pathname.endsWith("/cancel")) state.status = "cancel_requested";
      if (url.pathname.endsWith("/skip")) { expect(route.request().postDataJSON().acknowledge_uncertain).toBe(true); state.itemStatus = "skipped"; }
    }
    return route.fulfill({ status: 201, json: batch() });
  });
  await page.goto("/bug-bounty"); return state;
}

const send = (page: Page) => page.getByRole("button", { name: "Send to scan", exact: true });
const launch = (page: Page) => page.getByRole("button", { name: "Launch scans", exact: true });
const selectAll = (page: Page) => page.getByRole("button", { name: "Select all matching programs" }).click();

test("one menu and two actions launch cross-page selection, then restore progress and cancellation", async ({ page }) => {
  const state = await setup(page); const control = page.getByRole("region", { name: "Scan control center" });
  await expect(control.getByRole("combobox")).toHaveCount(1); await expect(control.getByRole("checkbox")).toHaveCount(0);
  await expect(control.getByRole("button")).toHaveCount(1); await expect(page.getByLabel("Scan mode")).toHaveValue("automatic");
  await page.getByLabel("Select program First program", { exact: true }).check();
  await page.getByRole("button", { name: "Next programs" }).click();
  await page.getByLabel("Select program Second program", { exact: true }).check();
  await expect(page.getByText("2 programs selected across pages")).toBeVisible(); await send(page).click();
  await expect(page.getByRole("heading", { name: "Review scans" })).toBeVisible();
  expect(state.previews[0]).toMatchObject({ scan_mode: "automatic", org_uuid: org, convert_wildcards: true, parallelism: 1, target_concurrency: 1, selection: { mode: "ids", program_ids: [1, 51], asset_ids: [] } });
  expect(state.previews[0]).not.toHaveProperty("workflows"); expect(state.starts).toBe(0);
  await expect(control.getByRole("button")).toHaveCount(2); await expect(control.getByRole("checkbox")).toHaveCount(0);
  await expect(launch(page)).toBeEnabled(); await launch(page).click();
  await expect(page.getByText("Batch status: running", { exact: true })).toBeVisible();
  await expect(page.getByText(/Delivery: ready/)).toBeVisible(); await expect(page.getByText(/1\/2 run records observed/)).toBeVisible();
  expect(state.starts).toBe(1); await page.reload();
  await expect(page.getByText("Batch status: running", { exact: true })).toBeVisible();
  page.once("dialog", dialog => dialog.accept()); await page.getByRole("button", { name: "Cancel batch", exact: true }).click();
  await expect(page.getByText(/Late runs are checked/)).toBeVisible();
  await expect(page.getByText("Batch status: cancel_requested", { exact: true })).toBeVisible();
});

test("all-filtered selection includes discovery by default and filter changes invalidate review", async ({ page }) => {
  const state = await setup(page); state.mixed = true;
  await page.getByLabel("Asset type").selectOption("wildcards");
  const filtered = page.waitForRequest(r => r.url().includes("/programs?") && r.url().includes("scope_text=example"));
  await page.getByLabel("Filter targets").fill("example"); await filtered; await selectAll(page); await send(page).click();
  await expect(page.getByRole("heading", { name: "Review scans" })).toBeVisible();
  expect(state.previews[0].selection).toMatchObject({ mode: "filtered", program_ids: [], filters: { scope: "in_scope", kind: "wildcards", scope_text: "example" } });
  expect(state.previews[0].convert_wildcards).toBe(true);
  await expect(page.getByRole("table", { name: "Workflow routing" }).getByText("Wildcard discovery")).toBeVisible();
  await page.getByLabel("Filter targets").fill("different.example");
  await expect(page.getByText("0 programs selected across pages")).toBeVisible(); await expect(launch(page)).toBeDisabled();
  expect(state.starts).toBe(0);
});

test("full recon changes domain routing but keeps wildcard roots discovery-only; optional opt-out", async ({ page }) => {
  const state = await setup(page); state.mixed = true; await selectAll(page); await send(page).click();
  const routing = page.getByRole("table", { name: "Workflow routing" });
  await expect(routing.getByRole("row").filter({ hasText: "Domains / subdomains" })).toContainText("enum-subdomain");
  await page.getByLabel("Scan mode").selectOption("full"); await expect(launch(page)).toBeDisabled(); await send(page).click();
  await expect(routing.getByRole("row").filter({ hasText: "Domains / subdomains" })).toContainText("domain-standard");
  await expect(routing.getByRole("row").filter({ hasText: "Wildcard discovery" })).toContainText("enum-subdomain");
  await expect(routing.getByRole("row").filter({ hasText: "IP addresses" })).toContainText("cidr");
  await expect(routing.getByRole("row").filter({ hasText: "CIDRs" })).toContainText("cidr"); expect(state.starts).toBe(0);
  await page.getByText("Advanced settings", { exact: true }).click(); await page.getByLabel("Include wildcard discovery", { exact: true }).uncheck();
  await expect(launch(page)).toBeDisabled(); await send(page).click(); await expect(routing.getByText("Wildcard discovery")).toHaveCount(0);
});

test("catalog changes block launch and uncertain submissions require explicit resolution", async ({ page }) => {
  const state = await setup(page); await selectAll(page); await send(page).click();
  await expect(page.getByRole("heading", { name: "Review scans" })).toBeVisible(); state.changed = true;
  await expect(page.getByRole("status")).toContainText("catalog changed", { timeout: 10000 }); await expect(launch(page)).toBeDisabled();
  state.starts = 1; state.status = "paused"; state.itemStatus = "uncertain";
  await expect(page.getByRole("button", { name: "Skip item" })).toBeVisible({ timeout: 10000 });
  page.once("dialog", dialog => { expect(dialog.message()).toContain("untracked scan"); void dialog.accept(); });
  await page.getByRole("button", { name: "Skip item" }).click(); await expect(page.getByText(/skipped · 2 reviewed targets/)).toBeVisible();
});

test("missing workflow blocks preparation without starting scans", async ({ page }) => {
  const state = await setup(page); state.failPreview = true; await selectAll(page); await send(page).click();
  await expect(page.getByRole("region", { name: "Scan control center" }).getByRole("alert")).toContainText("enum-subdomain"); await expect(launch(page)).toHaveCount(0); expect(state.starts).toBe(0);
});

test("selection changes during preparation cannot approve stale targets", async ({ page }) => {
  const state = await setup(page); state.delayPreview = true; await selectAll(page);
  const preparing = page.waitForRequest(r => r.method() === "POST" && r.url().endsWith("/scan-batches"));
  await send(page).click(); await preparing; await page.getByRole("button", { name: "Clear program selection" }).click();
  await expect(page.getByRole("button", { name: "Preparing review…", exact: true })).toBeDisabled();
  await expect(page.getByRole("status")).toContainText("Preparing selected targets");
  state.releasePreview!();
  await expect(page.getByRole("heading", { name: "Review scans" })).toBeVisible(); await expect(launch(page)).toBeDisabled(); expect(state.starts).toBe(0);
});

test("gateway timeout is visible and preparation remains retryable without scans", async ({ page }) => {
  const state = await setup(page); state.timeoutPreview = true; await selectAll(page); await send(page).click();
  await expect(page.getByRole("region", { name: "Scan control center" }).getByRole("alert")).toContainText("review timed out");
  await expect(send(page)).toBeEnabled(); await expect(launch(page)).toHaveCount(0); expect(state.starts).toBe(0);
  state.timeoutPreview = false; await send(page).click();
  await expect(page.getByRole("heading", { name: "Review scans" })).toBeVisible(); expect(state.starts).toBe(0);
});

test("active organization is used and changing it invalidates the review", async ({ page }) => {
  const state = await setup(page); await selectAll(page); await send(page).click();
  await expect(page.getByRole("heading", { name: "Review scans" })).toBeVisible();
  await page.evaluate(() => {
    localStorage.setItem("osmedeus_active_org", JSON.stringify({ uuid: "11111111-1111-1111-1111-111111111111", name: "Selected org" }));
    window.dispatchEvent(new CustomEvent("osmedeus-active-org-changed"));
  });
  await expect(launch(page)).toBeDisabled(); await send(page).click();
  expect(state.previews.at(-1)?.org_uuid).toBe("11111111-1111-1111-1111-111111111111"); expect(state.starts).toBe(0);
});
