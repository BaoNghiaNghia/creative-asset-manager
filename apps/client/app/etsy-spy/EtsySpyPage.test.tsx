// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { EtsySpyPage } from "./EtsySpyPage";
import { fetchAccessIdentity } from "../../features/access_management";

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
let blockedTerms: string[] = [];
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
  keywordSms: ["mama christian shirt", "mama christian hoodie", "mama christian sweatshirt"],
  keywordSmsQuery: "mama christian", keywordSmsStatus: "ready",
  bought24h: null, views24h: 20, inCarts: 25, demandScore: 0,
  observedAt: "2026-11-06T00:00:00Z", tm: "LIVE", tmStatus: "complete",
  tmSource: "TMHunt", tmCheckedAt: "2026-11-06T00:00:00Z",
  tmSourceAsOf: "2026-07-24T00:00:00Z",
  tmMatches: [{ markText: "Rudolph", serialNumber: "12345678", liveStatus: "LIVE" }],
};
const popularNoMatch = {
  ...listing, listingKey: "111", pageTitle: "Popular sample", badge: "Popular now",
  searchBadge: "Popular now", tm: "no_match", tmMatches: [],
  keywordSms: [], keywordSmsStatus: "no_suggestions",
};
const deadListing = {
  ...listing, listingKey: "222", pageTitle: "Dead trademark sample", tm: "DEAD",
  tmMatches: [
    { markText: "Old name 1", serialNumber: "11111111", liveStatus: "DEAD" },
    { markText: "Old name 2", serialNumber: "22222222", liveStatus: "DEAD" },
  ],
};
const keywordRows = [
  { id: "root", parentId: null, sessionId: "one", keyword: "A Root Seed",
    depth: 0, priority: 99, status: "done", reason: "Starting phrase",
    evidence: { source: "Etsy" }, matchedCount: 0, sourceUrl: null },
  { id: "branch", parentId: "root", sessionId: "one", keyword: "Frog design",
    depth: 1, priority: 75, status: "pending", reason: null,
    evidence: null, matchedCount: 0, sourceUrl: null },
  { id: "leaf", parentId: "branch", sessionId: "one", keyword: "Moon shirt keyword",
    depth: 2, priority: 35, status: "pending", reason: null,
    evidence: null, matchedCount: 0, sourceUrl: null },
  { id: "orphan", parentId: "unsynced", sessionId: "one", keyword: "Missing source branch",
    depth: 3, priority: 44, status: "pending", reason: null,
    evidence: null, matchedCount: 0, sourceUrl: null },
];
function json(value: unknown) {
  return new Response(JSON.stringify(value), {status: 200,
    headers: {"content-type": "application/json"}});
}
function installFetch() {
  observed.length = 0; blockedTerms = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    observed.push(url);
    if (url.endsWith("/blacklist") && init?.method === "POST") {
      const input = JSON.parse(String(init.body)) as { phrases: string[] };
      blockedTerms = [...new Set([...blockedTerms, ...input.phrases.map(x => x.toLowerCase())])];
      return json({phrases: blockedTerms});
    }
    if (url.includes("/blacklist/") && init?.method === "DELETE") {
      blockedTerms = blockedTerms.filter(x => x !== decodeURIComponent(url.split("/").pop() || ""));
      return json({phrases: blockedTerms});
    }
    if (url.endsWith("/blacklist")) return json({phrases: blockedTerms});
    if (url.includes("/summary")) return json(summary);
    if (url.includes("/sessions")) return json({ items: [
      { id: "one", keyword: "Rudolph", status: "completed" },
    ]});
    if (url.includes("/agents")) return json({items: []});
    if (url.includes("/keywords?")) {
      const params = new URL(url, "http://localhost").searchParams;
      const page = Number(params.get("page") || 1);
      const size = Number(params.get("page_size") || 200);
      return json({
        items: keywordRows.slice((page - 1) * size, page * size),
        page, page_size: size, total: keywordRows.length,
        pages: Math.ceil(keywordRows.length / size),
        last_synced_at: summary.last_synced_at, source_status: "offline",
      });
    }
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
    if (url.includes("/listings")) return json({items: [listing, popularNoMatch, deadListing], page: 1, page_size: 50,
      total: 3, pages: 1, last_synced_at: summary.last_synced_at, source_status: "offline"});
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
  vi.mocked(fetchAccessIdentity).mockResolvedValue({permissions: ["etsy_spy.read"]} as never);
});

describe("Etsy Spy native CAM dashboard", () => {
  it("displays offline last known data with distinct Etsy badge and TM status", async () => {
    await start();
    const html = container?.innerHTML || "";
    expect(html).toContain("Agent offline");
    expect(html).toContain("Rudolph Shirt");
    expect(html).toContain("Bestseller");
    expect(html).toContain("Keyword SMS");
    const headings = Array.from(container?.querySelectorAll(".etsy-data-table.is-products th") || []).map(x => x.textContent?.trim());
    expect(headings.indexOf("Keyword SMS")).toBe(headings.indexOf("Etsy Badge + TM") - 1);
    expect(html).toContain("mama christian shirt");
    const expandSms = container?.querySelector(".etsy-sms-expand") as HTMLButtonElement;
    expect(expandSms?.textContent).toBe("+2");
    await act(async () => {expandSms.click();});
    expect(container?.textContent).toContain("mama christian sweatshirt");
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

  it("renders reference-style badges with clear TM meanings and no false safety claim", async () => {
    await start();
    const badgeStacks = container?.querySelectorAll(".etsy-product-row .etsy-badge-stack");
    expect(badgeStacks).toHaveLength(3);
    expect(badgeStacks?.[0]?.querySelector(".etsy-demand.bestseller")?.textContent).toBe("Bestseller");
    expect(badgeStacks?.[1]?.querySelector(".etsy-demand.popular")?.textContent).toBe("Popular now");
    expect(badgeStacks?.[1]?.querySelector(".tm-no-match")?.textContent).toBe("TM: No match*");
    expect(badgeStacks?.[1]?.querySelector(".tm-no-match")?.tagName).toBe("SPAN");
    expect(badgeStacks?.[1]?.querySelector(".etsy-tm-disclaimer")?.textContent).toContain(
      "does not mean trademark clearance");
    const deadBadge = badgeStacks?.[2]?.querySelector(".tm-dead");
    expect(deadBadge?.textContent).toBe("TM DEAD · 2");
    expect(deadBadge?.tagName).toBe("BUTTON");
    await act(async () => { (deadBadge as HTMLButtonElement).click(); });
    expect(container?.textContent).toContain("11111111");
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

  it("renders and toggles the full keyword hierarchy with unsynced parent groups", async () => {
    await start();
    const tab = Array.from(container?.querySelectorAll(".etsy-stage-nav [role='tab']") || [])
      .find(el => el.textContent?.includes("Keyword Tree")) as HTMLButtonElement;
    await act(async () => { tab.click(); await new Promise(resolve => setTimeout(resolve, 70)); });
    expect(observed.some(url => url.includes("/keywords?") && url.includes("page_size=200"))).toBe(true);
    expect(container?.querySelector(".etsy-data-table.is-keyword-tree")).toBeTruthy();
    expect(container?.querySelector(".etsy-pager")).toBeNull();
    expect(container?.textContent).toContain("Parent not synced");
    expect(container?.textContent).toContain("A Root Seed");
    expect(container?.textContent).toContain("Frog design");
    expect(container?.textContent).not.toContain("Moon shirt keyword");
    const allButtons = Array.from(container?.querySelectorAll<HTMLButtonElement>(".etsy-tree-actions button") || []);
    const collapseAll = allButtons.find(el => el.textContent === "Collapse all")!;
    await act(async () => { collapseAll.click(); });
    expect(container?.textContent).not.toContain("Frog design");
    const rootToggle = container?.querySelector('button[aria-label="Expand A Root Seed"]') as HTMLButtonElement;
    await act(async () => { rootToggle.click(); });
    expect(container?.textContent).toContain("Frog design");
    const branchToggle = container?.querySelector('button[aria-label="Expand Frog design"]') as HTMLButtonElement;
    await act(async () => { branchToggle.click(); });
    expect(container?.textContent).toContain("Moon shirt keyword");
    const expandAll = allButtons.find(el => el.textContent === "Expand all")!;
    await act(async () => { expandAll.click(); });
    expect(container?.querySelectorAll(".etsy-tree-row")).toHaveLength(5);
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

  it("adds and removes persistent blacklist phrases using Enter", async () => {
    vi.mocked(fetchAccessIdentity).mockResolvedValue({
      permissions: ["etsy_spy.read", "etsy_spy.manage"]
    } as never);
    await start();
    const input = container?.querySelector<HTMLInputElement>(
      'input[aria-label="Add blacklist phrases"]');
    expect(input).toBeTruthy();
    const setter=Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype, "value")!.set!;
    await act(async () => {
      setter.call(input,"Rudolph, cat");
      input?.dispatchEvent(new Event("input",{bubbles:true}));
    });
    await act(async () => {
      container?.querySelector<HTMLFormElement>(".etsy-blacklist-form")
        ?.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));
      await new Promise(resolve=>setTimeout(resolve,50));
    });
    expect(blockedTerms).toEqual(["rudolph","cat"]);
    expect(container?.textContent).toContain("Blacklist (2)");
    expect(observed.filter(x=>x.includes("/listings?")).length).toBeGreaterThan(1);
    const remove=container?.querySelector<HTMLButtonElement>(
      'button[aria-label="Remove blacklist rudolph"]');
    expect(remove).toBeTruthy();
    await act(async()=>{remove?.click(); await new Promise(r=>setTimeout(r,50));});
    expect(blockedTerms).toEqual(["cat"]);
    expect(container?.textContent).toContain("Blacklist (1)");
  });
});