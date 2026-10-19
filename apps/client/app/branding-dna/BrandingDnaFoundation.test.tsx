// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BrandingDnaFoundation } from "./BrandingDnaFoundation";

afterEach(() => vi.unstubAllGlobals());

describe("Branding DNA operator queue health", () => {
  it("shows tenant-scoped queue counts without creating executable scan controls", async () => {
    const shopId = "11111111-1111-4111-8111-111111111111";
    const queue = {
      staged: 2, pending: 1, leased: 1, retry: 3, blocked: 1,
      completed: 4, cancelled: 0, expired_leases: 1, due_retries: 2,
    };
    const responses: Record<string, object> = {
      "/api/v1/branding-dna/status": {
        phase: "worker_control_plane", shop_count: 1, registered_workers: 1,
        scanner_ready: false, scan_enabled: false, blockers: [],
      },
      "/api/v1/branding-dna/shops": {
        items: [{
          id: shopId, display_name: "Hats", platform: "etsy", region: "US",
          storefront_url: "https://www.etsy.com/shop/Hats", access_mode: "authorized",
          status: "draft", created_at: "2026-10-19T00:00:00Z",
        }],
      },
      "/api/v1/branding-dna/shops/access-declarations": { items: [] },
      "/api/v1/branding-dna/shops/access-reviews": { items: [] },
      "/api/v1/branding-dna/shops/scan-readiness": {
        version: 1, scan_enabled: false, ready_count: 0,
        shop_count: 1, blocked_count: 1, staged_tasks: 2, queue_totals: queue,
        items: [{
          shop_id: shopId, display_name: "Hats", platform: "etsy", status: "blocked",
          staged_tasks: 2, queue,
          declaration_relationship: null, declaration_status: null,
          blockers: ["scan_task_dispatch_not_deployed"],
        }],
      },
    };
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const request = vi.fn(async (input: string | URL) => {
      const pathname = new URL(String(input), "https://cam.example").pathname;
      const data = responses[pathname];
      return new Response(JSON.stringify(data ?? {detail:"not found"}), {
        status: data ? 200 : 404,
        headers: {"Content-Type":"application/json"},
      });
    });
    vi.stubGlobal("fetch", request);

    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => {
      root.render(<BrandingDnaFoundation />);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(container.textContent).toContain("2 task đã chuẩn bị");
    expect(container.textContent).toContain("3 retry (2 đến hạn)");
    expect(container.textContent).toContain("1 lease quá hạn");
    expect(container.textContent).toContain("Dispatch đang khóa");
    expect(container.textContent).toContain("Scan disabled");
    expect(container.textContent).not.toContain("Start Scan");
    expect(request).toHaveBeenCalledTimes(5);
    await act(async () => root.unmount());
  });
});
