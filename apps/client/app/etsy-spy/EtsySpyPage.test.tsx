// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { EtsySpyPage } from "./EtsySpyPage";

vi.mock("../components/WorkspacePageHeader", () => ({
  WorkspacePageHeader: ({ actions }: { actions: React.ReactNode }) =>
    <header>CAM Etsy Spy {actions}</header>,
  WorkspaceBackToAssets: () => <a href="/">Back</a>,
}));
vi.mock("../../features/access_management", () => ({
  fetchAccessIdentity: vi.fn(async () => ({ permissions: ["etsy_spy.read"] })),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const observed: string[] = [];
const summary = {
  keywords: 208, listings: 50, sessions: 5, search: 34, shop: 16,
  trademarks: 2, tm_pending: 1, errors: 1, warnings: 3,
  online_agents: 0, agent_count: 1, source_status: "offline",
  last_synced_at: "2026-11-06T00:10:00Z",
  last_inventory_at: "2026-11-06T00:09:00Z",
  last_seen_at: "2026-11-06T00:10:00Z",
};
const listing = {
  sessionId: "one", keyword: "Rudolph", sourceKind: "search",
  sourceKinds: ["search", "shop"],
  listingKey: "123456789", pageTitle: "Rudolph Shirt", url: "https://www.etsy.com/listing/123456789",
  badge: "Bestseller", searchBadge: "Bestseller",
  bought24h: null, views24h: 20, inCarts: 25, demandScore: 0,
  observedAt: "2026-11-06T00:00:00Z", tm: "LIVE", tmStatus: "complete",
  tmSource: "TMHunt", tmCheckedAt: "2026-11-06T00:00:00Z",
  tmSourceAsOf: "2026-07-24T00:00:00Z",
  tmMatches: [{ markText: "Rudolph", serialNumber: "12345678", liveStatus: "LIVE" }],
};
function json(value: unknown) {
  return new Response(JSON.stringify(value), {status: 200,
    headers: {"content-type": "application/json"}});
}
function installFetch() {
  observed.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    observed.push(url);
    if (url.includes("/summary")) return json(summary);
    if (url.includes("/sessions")) return json({ items: [
      { id: "one", keyword: "Rudolph", status: "completed" },
    ]});
    if (url.includes("/agents")) return json({items: []});
    if (url.includes("/trademarks?")) {
      await new Promise(resolve => setTimeout(resolve, 35));
      return json({
      items: [
        { checkId: "null-evidence", keyword: "No cached marks", title: "Shirt",
          kind: "product", status: "pending", risk: null, source: "Windows",
          matched: null, liveCount: 0, deadCount: 0, unknownCount: 0 },
        { checkId: "missing-evidence", keyword: "Legacy TM entry",
          kind: "product", status: "unavailable", risk: "unverified", source: "Windows",
          liveCount: 0, deadCount: 0, unknownCount: 0 },
        { checkId: "valid-evidence", keyword: "Rudolph",
          kind: "product", status: "complete", risk: "LIVE", source: "Windows",
          matched: [{ markText: "Rudolph", serialNumber: "12345678", liveStatus: "LIVE" }],
          liveCount: 1, deadCount: 0, unknownCount: 0, checkedAt: "2026-11-06T00:00:00Z" },
      ],
      page: 1, page_size: 50, total: 3, pages: 1, source_status: "offline",
      });
    }
    if (url.includes("/listings")) return json({items: [listing], page: 1, page_size: 50,
      total: 1, pages: 1, last_synced_at: summary.last_synced_at, source_status: "offline"});
    return json({items: [], page:1,page_size:50,total:0,pages:0,
      last_synced_at:summary.last_synced_at,source_status:"offline"});
  }));
}
let root: Root | null = null;
let container: HTMLDivElement | null = null;
async function start() {
  installFetch();
  window.history.replaceState(null, "", "/etsy-spy");
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<EtsySpyPage />);
    await new Promise(resolve => setTimeout(resolve, 45));
  });
}
afterEach(async () => {
  await act(async () => {root?.unmount();});
  container?.remove(); container=null; root=null;
  vi.unstubAllGlobals();
});

describe("Etsy Spy native CAM dashboard", () => {
  it("displays offline last known data with distinct Etsy badge and TM status", async () => {
    await start();
    const html = container?.innerHTML || "";
    expect(html).toContain("Agent offline");
    expect(html).toContain("Rudolph Shirt");
    expect(html).toContain("Bestseller");
    expect(html).toContain("LIVE");
    expect(html).toContain("GMT+7");
    expect(html).toContain("Listings reconciled");
    expect(html).toContain("Search + Shop");
    expect(html).not.toContain("Safe");
    const pill = Array.from(container?.querySelectorAll("button") || [])
      .find(button => button.textContent === "LIVE");
    expect(pill?.disabled).toBe(false);
    await act(async () => {pill?.click();});
    expect(container?.textContent).toContain("12345678");
    expect(container?.querySelector("a[href*='tsdr.uspto.gov']"))
      .toHaveProperty("rel", "noopener noreferrer");
  });

  it("keeps the white, compact table controls accessible without losing product actions", async () => {
    await start();
    expect(container?.querySelectorAll(".etsy-stats .etsy-stat-icon svg")).toHaveLength(7);
    expect(container?.querySelectorAll(".etsy-stage-nav [role='tab']")).toHaveLength(4);
    expect(container?.querySelector(".etsy-stage-card .etsy-stage-heading h2")?.textContent).toBe("Product Intelligence");
    expect(container?.querySelector(".etsy-stat-total[aria-pressed='true']")).toBeTruthy();
    expect(container?.querySelector(".etsy-search-filters .etsy-actions select")).toBeTruthy();
    expect(container?.querySelector(".etsy-pager select[aria-label='Rows per page']")).toBeTruthy();
    expect(container?.querySelector(".etsy-product-glyph")?.textContent).toBe("E");
    expect(container?.querySelector(".etsy-action-cell a[href*='etsy.com/listing']")).toBeTruthy();
    expect(container?.querySelector(".etsy-title-meta a[href*='etsy.com/listing']")).toBeTruthy();
    expect(container?.querySelector(".etsy-time")?.textContent).toContain("6/11/26");
    expect(container?.querySelector(".etsy-search-box input[aria-label='Search Spy records']")).toBeTruthy();
    expect(container?.querySelector(".etsy-table-wrap th button[aria-label='Sort by Carts']")).toBeTruthy();
    const source = container?.querySelector("select[aria-label='Source']") as HTMLSelectElement;
    await act(async () => {
      source.value = "shop";
      source.dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise(resolve => setTimeout(resolve, 35));
    });
    expect(observed.some(url => url.includes("/listings?") && url.includes("source=shop"))).toBe(true);
    const clear = Array.from(container?.querySelectorAll("button") || [])
      .find(button => button.textContent?.includes("Clear filters")) as HTMLButtonElement;
    expect(clear).toBeTruthy();
    await act(async () => {
      clear.click();
      await new Promise(resolve => setTimeout(resolve, 35));
    });
    expect(source.value).toBe("");
  });

  it("sorts on the backend, retains URL query params and separates pages", async () => {
    await start();
    const sort = container?.querySelector("button[aria-label='Sort by Carts']") as HTMLButtonElement;
    expect(sort).toBeTruthy();
    await act(async () => {sort.click(); await new Promise(resolve => setTimeout(resolve, 30));});
    expect(observed.some(url => /\/listings\?.*sort=inCarts/.test(url))).toBe(true);
    expect(new URLSearchParams(window.location.search).get("sort")).toBe("inCarts");
    const keyword = Array.from(container?.querySelectorAll("[role='tab']") || [])
      .find(el => el.textContent?.includes("Keyword Tree")) as HTMLButtonElement;
    await act(async () => {keyword.click(); await new Promise(resolve => setTimeout(resolve, 30));});
    expect(observed.some(url => url.includes("/keywords?"))).toBe(true);
  });
  it("uses Stage 0-style KPI cards to jump into real server-side filters", async () => {
    await start();
    const shop = container?.querySelector(".etsy-stat-shop") as HTMLButtonElement;
    await act(async () => {
      shop.click();
      await new Promise(resolve => setTimeout(resolve, 35));
    });
    expect(shop.getAttribute("aria-pressed")).toBe("true");
    expect(observed.some(url => url.includes("/listings?") && url.includes("source=shop"))).toBe(true);
    const pending = container?.querySelector(".etsy-stat-pending") as HTMLButtonElement;
    await act(async () => {
      pending.click();
      await new Promise(resolve => setTimeout(resolve, 35));
    });
    expect(observed.some(url => url.includes("/listings?") && url.includes("tm=pending"))).toBe(true);
    const keywords = Array.from(container?.querySelectorAll(".etsy-stage-nav [role='tab']") || [])
      .find(item => item.textContent?.includes("Keyword Tree")) as HTMLButtonElement;
    await act(async () => {
      keywords.click();
      await new Promise(resolve => setTimeout(resolve, 35));
    });
    expect(observed.some(url => url.includes("/keywords?"))).toBe(true);
    expect(keywords.getAttribute("aria-selected")).toBe("true");
    expect(container?.querySelector(".etsy-agents")?.tagName).toBe("DETAILS");
    expect((container?.querySelector(".etsy-agents") as HTMLDetailsElement).open).toBe(false);
  });

  it("does not crash the Trademark tab for null or omitted matched evidence", async () => {
    await start();
    const trademark = Array.from(container?.querySelectorAll(".etsy-stage-nav [role='tab']") || [])
      .find(item => item.textContent?.includes("Trademark")) as HTMLButtonElement;
    // The former crash happened on the first render after switching tabs:
    // Product rows were briefly rendered as Trademark rows before refetch.
    await act(async () => { trademark.click(); });
    expect(container?.querySelector(".etsy-skeleton")).toBeTruthy();
    expect(container?.querySelector(".etsy-data-table.is-trademarks")).toBeNull();
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 65)); });
    expect(observed.some(url => url.includes("/trademarks?"))).toBe(true);
    expect(trademark.getAttribute("aria-selected")).toBe("true");
    expect(container?.textContent).toContain("No cached marks");
    expect(container?.textContent).toContain("Legacy TM entry");
    expect(container?.textContent).toContain("Rudolph");
    expect(container?.querySelectorAll(".etsy-data-table.is-trademarks tbody tr")).toHaveLength(3);
    // Only the row with actual cached marks may offer the evidence expansion.
    const expand = container?.querySelectorAll(".etsy-data-table.is-trademarks .etsy-details-trigger") || [];
    expect(expand).toHaveLength(1);
    await act(async () => {(expand[0] as HTMLButtonElement).click();});
    expect(container?.querySelector("a[href*='tsdr.uspto.gov']"))
      .toHaveProperty("rel", "noopener noreferrer");
    expect(container?.querySelectorAll(".etsy-tm-counts")).toHaveLength(3);
  });

  it("keeps compact tabs keyboard-navigable with one active tab", async () => {
    await start();
    const tabs = Array.from(container?.querySelectorAll<HTMLButtonElement>(".etsy-stage-nav [role='tab']") || []);
    expect(tabs).toHaveLength(4);
    expect(tabs[0].tabIndex).toBe(0);
    expect(tabs[1].tabIndex).toBe(-1);
    await act(async () => {
      tabs[0].focus();
      tabs[0].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
      await new Promise(resolve => setTimeout(resolve, 35));
    });
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
    expect(tabs[0].tabIndex).toBe(-1);
    expect(tabs[1].tabIndex).toBe(0);
    expect(document.activeElement).toBe(tabs[1]);
  });
});
