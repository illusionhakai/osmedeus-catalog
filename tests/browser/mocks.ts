import { expect, Page } from "@playwright/test";

export async function mocks(page: Page) {
  await page.route("**/osm/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/runs")) expect(route.request().method()).toBe("GET");
    if (url.pathname.endsWith("/login")) return route.fulfill({ json: { token: "catalog-test-token" } });
    if (url.pathname.endsWith("/orgs")) return route.fulfill({ json: { data: [] } });
    return route.fulfill({ json: { data: [], pagination: { total: 0 }, stats: {} } });
  });
  await page.route("**/server-info", route => route.fulfill({ json: { version: "v5.1.1" } }));
  let refreshed = false;
  let jobReads = 0;
  let delivered = false;
  let deliveryReads = 0;
  await page.route("**/catalog-api/v1/**", async route => {
    const url = new URL(route.request().url());
    expect(route.request().headers().authorization).toBe("Bearer catalog-test-token");
    const p = { id: 1, name: "Example program", platform: "hackerone", sources: "chaos", policy_links: [{ url: "https://example.com/policy", source: "current" }] };
    const manifest = { export_id: "a".repeat(32), created_at: "2026-10-05T12:00:00Z", programs: [{ id: 1, name: p.name, counts: { domains: 1 }, omitted: [], conversions: [] }] };
    const path = url.pathname;
    if (path.endsWith("/workflows")) return route.fulfill({ json: { data: [], pagination: { total: 0 } } });
    if (path.endsWith("/scan-batches")) return route.fulfill({ json: { items: [] } });
    if (path.endsWith("/deliver")) { delivered = true; return route.fulfill({ status: 202, json: { export_id: manifest.export_id, status: "uploading" } }); }
    if (path.endsWith("/deliveries")) return route.fulfill({ json: { items: delivered ? [{ export_id: manifest.export_id, status: ++deliveryReads > 1 ? "ready" : "uploading", total_files: 1, files: [{ member: "program-1/domains.txt", program_name: p.name, kind: "domains", is_target_file: true, lines: 1, path: "/engine/data/uploads/catalog-targets.txt", cli_template: "osmedeus run -f YOUR_REVIEWED_WORKFLOW -T /engine/data/uploads/catalog-targets.txt" }] }] : [] } });
    if (path.endsWith("/download")) return route.fulfill({ contentType: "application/zip", body: Buffer.from("PKfixture") });
    if (path.endsWith("/exports")) return route.fulfill({ status: 201, json: manifest });
    if (path.endsWith("/refresh")) { refreshed = true; return route.fulfill({ status: 202, json: { job_id: "job" } }); }
    if (path.endsWith("/jobs")) return route.fulfill({ json: { items: refreshed ? [{ id: "job", status: ++jobReads > 2 ? "completed" : "running", created_at: "2026-10-05T12:00:00Z" }] : [] } });
    if (path.endsWith("/overview")) return route.fulfill({ json: { sources: [{ name: "chaos", last_success: "2026-10-05T12:00:00Z", latest: { status: "completed" } }], facets: { platforms: [{ value: "hackerone" }] } } });
    if (path.endsWith("/schedule")) return route.fulfill({ json: { enabled: true, time: "03:00", timezone: "America/Sao_Paulo", include_vdp: false } });
    if (path.endsWith("/programs/1")) return route.fulfill({ json: p });
    if (path.endsWith("/programs")) {
      expect(url.searchParams.get("scannable_only")).toBe("true");
      return route.fulfill({ json: { items: [p], total: 1, has_more: false } });
    }
    if (path.endsWith("/assets")) return route.fulfill({ json: { items: [{ id: 1, raw_value: "example.com", asset_type: "domain", scope: "in_scope", is_wildcard: false }], total: 1, has_more: false } });
    return route.fulfill({ status: 404, json: {} });
  });
}
