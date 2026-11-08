// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { EtsySpyPage, vietnamCalendarDay, recentObservedDays } from "./EtsySpyPage";
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
let registeredImageAgents: Array<{id: string; name: string; active: boolean; last_seen_at: string | null}> = [];
let savedWinKeywords: string[] = [];
let parentKeywords: Array<{keyword: string; keywordKey: string; priority: number;
  source: "manual" | "observed"; occurrences: number}> = [];
let pickedKeys = new Set<string>();
let laterSeedKeys = new Set<string>();
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
  thumbnailUrl: "https://i.etsystatic.com/123/il_170x135.primary.jpg",
  fullImageUrl: "https://i.etsystatic.com/123/il_fullxfull.primary.jpg",
  badge: "Bestseller", searchBadge: "Bestseller",
  keywordSms: ["mama christian shirt", "mama christian hoodie", "mama christian sweatshirt"],
  keywordSmsQuery: "mama christian", keywordSmsStatus: "ready",
  bought24h: null, views24h: 20, inCarts: 20, demandScore: 0,
  observedAt: "2026-11-06T00:00:00Z", tm: "LIVE", tmStatus: "complete",
  tmSource: "TMHunt", tmCheckedAt: "2026-11-06T00:00:00Z",
  tmSourceAsOf: "2026-07-24T00:00:00Z",
  tmMatches: [{ markText: "Rudolph", serialNumber: "12345678", liveStatus: "LIVE" }],
};
const popularNoMatch = {
  ...listing, listingKey: "111", pageTitle: "Popular sample", badge: "Popular now",
  searchBadge: "Popular now", tm: "no_match", tmMatches: [], inCarts: 25,
  thumbnailUrl: null, fullImageUrl: null,
  keywordSms: [], keywordSmsStatus: "no_suggestions",
};
const deadListing = {
  ...listing, listingKey: "222", pageTitle: "Dead trademark sample", tm: "DEAD", inCarts: null,
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
  observed.length = 0; blockedTerms = []; savedWinKeywords = [];
  registeredImageAgents = [];
  pickedKeys = new Set<string>(); laterSeedKeys = new Set<string>();
  parentKeywords = [{keyword: "A Root Seed", keywordKey: "a root seed",
    priority: 50, source: "observed", occurrences: 1}];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    observed.push(url);
    if (url.endsWith("/parent-keywords") && ["POST", "PUT"].includes(init?.method || "")) {
      const {keyword, priority} = JSON.parse(String(init?.body)) as {
        keyword: string; priority: number;
      };
      const clean = keyword.trim().replace(/\\s+/g, " ");
      const key = clean.toLowerCase();
      const exists = parentKeywords.find(value => value.keywordKey === key);
      if (exists) exists.priority = priority;
      else parentKeywords.push({
        keyword: clean, keywordKey: key, priority, source: "manual", occurrences: 0,
      });
      parentKeywords.sort((a, b) => b.priority - a.priority);
      return json({items: [...parentKeywords],count:parentKeywords.length,revision:"fake"});
    }
    if (url.endsWith("/parent-keywords"))
      return json({items: [...parentKeywords],count:parentKeywords.length,revision:"fake"});
    if (url.endsWith("/win-keywords") && init?.method === "POST") {
      const { keywords } = JSON.parse(String(init.body)) as { keywords: string[] };
      for (const raw of keywords) {
        const value = raw.trim().replace(/\\s+/g, " ");
        if (!savedWinKeywords.some(item => item.toLowerCase() === value.toLowerCase()))
          savedWinKeywords.push(value);
      }
      return json({ keywords: [...savedWinKeywords], count: savedWinKeywords.length });
    }
    if (url.endsWith("/win-keywords") && init?.method === "DELETE") {
      const { keyword } = JSON.parse(String(init.body)) as { keyword: string };
      savedWinKeywords = savedWinKeywords.filter(value => value.toLowerCase() !== keyword.toLowerCase());
      return json({ keywords: [...savedWinKeywords], count: savedWinKeywords.length });
    }
    if (url.endsWith("/win-keywords"))
      return json({ keywords: [...savedWinKeywords], count: savedWinKeywords.length });
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
    if (url.includes("/listings/") && url.endsWith("/later") && init?.method === "PUT") {
      const id = url.split("/listings/")[1]?.split("/")[0] || "";
      const body = JSON.parse(String(init.body)) as { later: boolean };
      if (body.later) laterSeedKeys.add(id); else laterSeedKeys.delete(id);
      return json({listingKey: id, later: body.later});
    }
    if (url.includes("/listings/") && url.endsWith("/pick") && init?.method === "PUT") {
      const id = url.split("/listings/")[1]?.split("/")[0] || "";
      const body = JSON.parse(String(init.body)) as { picked: boolean };
      if (body.picked) pickedKeys.add(id); else pickedKeys.delete(id);
      return json({listingKey: id, picked: body.picked});
    }
    if (url.endsWith("/listings/observed-days")) {
      const days = recentObservedDays(vietnamCalendarDay());
      return json({ timezone: "Asia/Ho_Chi_Minh", start_hour: 6,
        items: days.map((day, index) => ({
          day, count: index === 0 ? 3 : index === 1 ? 2 : 0,
          pickedCount: index === 0 ? 1 : index === 1 ? 4 : 0,
        })),
      });
    }
    if (url.includes("/summary")) return json({
      ...summary, picked: pickedKeys.size, later: [...laterSeedKeys].filter(id => !pickedKeys.has(id)).length,
      listings: summary.listings - pickedKeys.size - [...laterSeedKeys].filter(id => !pickedKeys.has(id)).length,
    });
    if (url.includes("/sessions")) return json({ items: [
      { id: "one", keyword: "Rudolph", status: "completed" },
    ]});
    if (url.endsWith("/image-agents") && init?.method === "POST") {
      const {name} = JSON.parse(String(init.body)) as {name: string};
      const agent_id = "11111111-1111-4111-8111-111111111111";
      registeredImageAgents.push({id: agent_id,
        name: "CAM Image Agent / " + name, active: true, last_seen_at: null});
      return json({agent_id, token: "A".repeat(70)});
    }
    if (url.endsWith("/image-agents")) return json({items: [...registeredImageAgents]});
    if (url.endsWith("/image-agents/overview")) return json({
      enabled:false, daily_cap:200, claimed_today:0, remaining_today:200,
      queued:0, processing:0, completed_today:0, needs_review_today:0,
      needs_ai_today:0, failed_today:0, timezone:"Asia/Ho_Chi_Minh",
      storage:{ready:false,reason:"storage_not_provisioned",
        used_bytes:0, quota_bytes:2147483648, free_bytes:null},
    });
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
    if (url.includes("/listings?")) {
      const pickState = new URL(url, "http://localhost").searchParams.get("pick_state") || "eligible";
      const visible = [listing, popularNoMatch, deadListing].filter(x =>
        pickState === "picked" ? pickedKeys.has(x.listingKey)
        : pickState === "later" ? laterSeedKeys.has(x.listingKey) && !pickedKeys.has(x.listingKey)
        : !pickedKeys.has(x.listingKey) && !laterSeedKeys.has(x.listingKey)).map(x =>
          ({ ...x, picked: pickedKeys.has(x.listingKey),
             later: laterSeedKeys.has(x.listingKey), laterManual: laterSeedKeys.has(x.listingKey),
             laterReason: laterSeedKeys.has(x.listingKey) ? "Được chọn Later" : null }));
      return json({items: visible, page: 1, page_size: 50,
        total: visible.length, pages: 1, last_synced_at: summary.last_synced_at,
        source_status: "offline"});
    }
    return json({items: [], page:1,page_size:50,total:0,pages:0,
      last_synced_at:summary.last_synced_at,source_status:"offline"});
  }));
}
let root: Root | null = null;
let container: HTMLDivElement | null = null;
async function start() {
  // jsdom does not ship ResizeObserver, while the real browser provides it.
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
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
  it("renders thumbnail CDN URL, full image link and fallback without storing image files", async () => {
    await start();
    const first = container?.querySelector(".etsy-product-row") as HTMLTableRowElement;
    const img = first?.querySelector("img.etsy-product-thumbnail") as HTMLImageElement;
    expect(img?.getAttribute("src")).toBe("https://i.etsystatic.com/123/il_170x135.primary.jpg");
    const anchor = first?.querySelector(".etsy-product-thumbnail-link") as HTMLAnchorElement;
    expect(anchor?.href).toBe("https://i.etsystatic.com/123/il_fullxfull.primary.jpg");
    expect(anchor?.rel).toContain("noopener");
    expect(img?.getAttribute("loading")).toBe("lazy");
    const noImageRow = Array.from(container?.querySelectorAll(".etsy-product-row") || [])
      .find(row => row.textContent?.includes("Popular sample"));
    expect(noImageRow?.querySelector(".etsy-product-glyph")?.textContent).toBe("E");
    await act(async () => { img.dispatchEvent(new Event("error")); });
    expect(first?.querySelector("img.etsy-product-thumbnail")).toBeNull();
    expect(first?.querySelector(".etsy-product-glyph")?.textContent).toBe("E");
  });

  it("shows a 15% larger hover preview anchored bottom-left of the viewport, not the navbar", async () => {
    await start();
    const first = container?.querySelector<HTMLTableRowElement>(".etsy-product-row");
    const thumb = first?.querySelector<HTMLImageElement>(".etsy-product-thumbnail");
    expect(thumb?.getAttribute("width")).toBe("68");
    expect(thumb?.getAttribute("height")).toBe("68");
    expect(document.body.querySelector(".inventory-desktop-sidebar")).toBeNull();
    expect(document.body.querySelector(".etsy-product-hover-preview")).toBeNull();

    // The overlay must appear even when the desktop navbar is absent.
    await act(async () => {
      first?.dispatchEvent(new MouseEvent("pointerover", {
        bubbles: true, clientX: 580, clientY: 120,
      }));
    });
    let preview = document.body.querySelector<HTMLDivElement>(".etsy-product-hover-preview");
    expect(preview?.dataset.listingKey).toBe(listing.listingKey);
    expect(preview?.querySelector<HTMLImageElement>("img")?.src)
      .toBe("https://i.etsystatic.com/123/il_fullxfull.primary.jpg");
    expect(preview?.querySelector("img")?.getAttribute("alt")).toBe(listing.pageTitle);
    expect(preview?.style.left).toBe("16px");
    expect(preview?.style.bottom).toBe("16px");
    expect(preview?.style.width).toBe("285px"); // 248px * 1.15, rounded
    expect(preview?.style.maxHeight).toBe("506px"); // 440px * 1.15
    expect(preview?.querySelector<HTMLImageElement>("img")?.style.width).toBe("285px");
    expect(preview?.querySelector<HTMLImageElement>("img")?.style.maxHeight).toBe("414px");
    expect(preview?.style.top).toBe("");
    expect(container?.querySelector(".etsy-product-hover-preview")).toBeNull();

    await act(async () => { window.dispatchEvent(new Event("scroll")); });
    expect(document.body.querySelector(".etsy-product-hover-preview")).toBeNull();

    // A sidebar with a full-height menu must not suppress the floating modal.
    const sidebar = document.createElement("aside");
    sidebar.className = "inventory-desktop-sidebar";
    const nav = document.createElement("nav");
    nav.className = "workspace-navigation";
    sidebar.appendChild(nav);
    document.body.appendChild(sidebar);
    try {
      await act(async () => {
        first?.dispatchEvent(new MouseEvent("pointerover", {
          bubbles: true, clientX: 510, clientY: 160,
        }));
      });
      preview = document.body.querySelector<HTMLDivElement>(".etsy-product-hover-preview");
      expect(preview?.style.left).toBe("16px");
      expect(preview?.style.bottom).toBe("16px");
      expect(preview?.style.width).toBe("285px");
      await act(async () => { window.dispatchEvent(new Event("scroll")); });
    } finally {
      sidebar.remove();
    }

    const noImage = Array.from(container?.querySelectorAll<HTMLTableRowElement>(".etsy-product-row") || [])
      .find(row => row.textContent?.includes("Popular sample"));
    await act(async () => {
      noImage?.dispatchEvent(new MouseEvent("pointerover", {
        bubbles: true, clientX: 640, clientY: 180,
      }));
    });
    expect(document.body.querySelector(".etsy-product-hover-preview")).toBeNull();
  });

  it("separates the two-line title clamp from the padded tooltip footer", async () => {
    const originalTitle = listing.pageTitle;
    const longTitle = "Tis the Season Gingham Sweatshirt, Embroidered Santa Wreath Christmas Crewneck, Cozy Vintage Holiday Pullover";
    listing.pageTitle = longTitle;
    try {
      await start();
      const first = container?.querySelector<HTMLTableRowElement>(".etsy-product-row");
      await act(async () => {
        first?.dispatchEvent(new MouseEvent("pointerover", { bubbles: true }));
      });
      const preview = document.body.querySelector(".etsy-product-hover-preview");
      const paddedFooter = preview?.querySelector(".etsy-product-preview-title");
      const twoLineText = paddedFooter?.querySelector(".etsy-product-preview-title-text");
      expect(twoLineText?.textContent).toBe(longTitle);
      expect(paddedFooter?.children).toHaveLength(1);
      expect(twoLineText?.children).toHaveLength(0);
      expect(preview?.querySelector("img")?.getAttribute("alt")).toBe(longTitle);
    } finally {
      listing.pageTitle = originalTitle;
    }
  });

  it("keeps the Etsy preview inside a short or narrow viewport", async () => {
    await start();
    vi.stubGlobal("innerWidth", 270);
    vi.stubGlobal("innerHeight", 320);
    const first = container?.querySelector<HTMLTableRowElement>(".etsy-product-row");
    await act(async () => {
      first?.dispatchEvent(new MouseEvent("pointerover", { bubbles: true }));
    });
    const preview = document.body.querySelector<HTMLDivElement>(".etsy-product-hover-preview");
    expect(preview?.style.left).toBe("16px");
    expect(preview?.style.bottom).toBe("16px");
    expect(preview?.style.width).toBe("238px");
    expect(preview?.style.maxHeight).toBe("288px");
    expect(preview?.querySelector<HTMLImageElement>("img")?.style.maxHeight).toBe("206px");
  });

  it("displays offline last known data with distinct Etsy badge and TM status", async () => {
    await start();
    const html = container?.innerHTML || "";
    expect(html).toContain("Agent offline");
    expect(html).toContain("Rudolph Shirt");
    expect(html).toContain("Bestseller");
    expect(html).toContain("Keyword SMS");
    const headings = Array.from(container?.querySelectorAll(".etsy-data-table.is-products th") || [])
      .map(x => (x.textContent || "").trim().replace(/\s*[↑↓]$/, ""));
    expect(headings).toEqual([
      "Product / Etsy", "Keyword SMS", "Etsy Badge + TM",
      "Bought / 24h", "Views / 24h", "Carts", "Observed", "Action",
    ]);
    expect(container?.querySelectorAll(".etsy-product-row")[0]?.querySelectorAll("td")).toHaveLength(8);
    expect(container?.querySelector(".etsy-data-table.is-products .etsy-keyword-cell")).toBeNull();
    expect(headings).not.toContain("Keyword");
    expect(headings).not.toContain("Source");
    expect(html).toContain("mama christian shirt");
    const smsList = container?.querySelector(".etsy-sms-list");
    expect(smsList?.textContent).toBe(
      "mama christian shirt, mama christian hoodie, mama christian sweatshirt");
    expect(smsList?.parentElement?.getAttribute("title")).toBe(smsList?.textContent);
    expect(container?.querySelector(".etsy-sms-expand")).toBeNull();
    expect(html).toContain("LIVE");
    expect(html).toContain("GMT+7");
    expect(html).not.toContain("Listings reconciled");
    expect(html).not.toContain("Search + Shop");
    expect(html).not.toContain("Safe");
    const pill = Array.from(container?.querySelectorAll("button") || [])
      .find(button => button.textContent === "LIVE");
    expect(pill?.disabled).toBe(false);
    await act(async () => {pill?.click();});
    expect(container?.textContent).toContain("12345678");
    expect(container?.querySelector("a[href*='tsdr.uspto.gov']"))
      .toHaveProperty("rel", "noopener noreferrer");
  });

  it("renders Carts 20+ without changing numeric values or other rows", async () => {
    await start();
    const cells = Array.from(container?.querySelectorAll(".etsy-product-row td:nth-child(6)") || []);
    expect(cells.map(cell => cell.textContent)).toEqual(["20+", "25", "—"]);
    expect(listing.inCarts).toBe(20);
    expect(popularNoMatch.inCarts).toBe(25);
    expect(deadListing.inCarts).toBeNull();
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
    expect(container?.querySelectorAll(".etsy-stats .etsy-stat-icon svg")).toHaveLength(6);
    const cards = Array.from(container?.querySelectorAll(".etsy-stats .etsy-stat") || []);
    expect(cards[1].classList.contains("etsy-stat-picked")).toBe(true);
    expect(cards[2].classList.contains("etsy-stat-later")).toBe(true);
    expect(cards[3].classList.contains("etsy-stat-blacklist")).toBe(true);
    expect(container?.querySelector(".etsy-blacklist-chips")).toBeNull();
    expect(container?.querySelectorAll(".etsy-stage-nav [role='tab']")).toHaveLength(4);
    expect(container?.querySelector(".etsy-stage-card .etsy-stage-heading h2")?.textContent).toBe("Product Intelligence");
    expect(container?.querySelector(".etsy-stat-total[aria-pressed='true']")).toBeTruthy();
    expect(container?.querySelectorAll(".etsy-observed-day")).toHaveLength(10);
    expect(container?.querySelector(".etsy-pager select[aria-label='Rows per page']")).toBeTruthy();
    expect(container?.querySelector(".etsy-product-glyph")?.textContent).toBe("E");
    expect(container?.querySelector(".etsy-action-cell a[href*='etsy.com/listing']")).toBeTruthy();
    expect(container?.querySelector(".etsy-title-meta a[href*='etsy.com/listing']")).toBeTruthy();
    expect(container?.querySelector(".etsy-time")?.textContent).toContain("6/11/26");
    expect(container?.querySelector(".etsy-search-box input[aria-label='Search Spy records']")).toBeTruthy();
    expect(container?.querySelector(".etsy-table-wrap th button[aria-label='Sort by Carts']")).toBeTruthy();
    const cartsCells = container?.querySelectorAll(".etsy-product-row td:nth-child(6)");
    expect(cartsCells).toHaveLength(3);
    expect(Array.from(cartsCells || [], cell => cell.textContent)).toEqual(["20+", "25", "—"]);
    const today = vietnamCalendarDay();
    expect(observed.some(url => url.includes("/listings?") &&
      url.includes("observed_day=" + today))).toBe(true);
    expect(container?.querySelector(".etsy-observed-day[aria-pressed='true']")?.getAttribute(
      "data-observed-day")).toBe(today);
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
  it("shows ten compact 06:00 observation buckets and filters Eligible listings by date", async () => {
    await start();
    const days = Array.from(container?.querySelectorAll<HTMLButtonElement>(
      ".etsy-observed-day") || []);
    const today = vietnamCalendarDay();
    const expectedDays = recentObservedDays(today);
    expect(days).toHaveLength(10);
    expect(days.every(day => !day.textContent?.includes("06:00–06:00"))).toBe(true);
    expect(days.every(day => day.querySelector(".etsy-observed-day-eligible"))).toBe(true);
    expect(days.map(day => day.dataset.observedDay)).toEqual(expectedDays);
    expect(days[0].textContent).toContain("Hôm nay");
    expect(days[1].textContent).toContain("Hôm qua");
    expect(days[2].textContent).toContain(expectedDays[2].slice(8, 10) + "/" + expectedDays[2].slice(5, 7));
    // The date stays on top; one compact row below puts Available at
    // the left and Picked at the right without changing either count.
    expect(days.every(day => day.children.length === 2)).toBe(true);
    expect(days.every(day => day.firstElementChild?.classList.contains("etsy-observed-day-label"))).toBe(true);
    expect(days.every(day => Array.from(
      day.querySelector(".etsy-observed-day-metrics")?.children ?? [],
      child => child.classList[0],
    ).join(",") === "etsy-observed-day-eligible,etsy-observed-day-pick")).toBe(true);
    expect(days[0].querySelector(".etsy-observed-day-eligible")?.textContent).toBe("3Khả dụng");
    expect(days[0].querySelector(".etsy-observed-day-pick")?.textContent).toBe("1Picked");
    expect(days[1].querySelector(".etsy-observed-day-pick")?.textContent).toBe("4Picked");
    expect(days[2].querySelector(".etsy-observed-day-pick")?.textContent).toBe("0Picked");
    expect(days[0].getAttribute("aria-label")).toContain("1 sản phẩm đã Pick");
    expect(days[0].getAttribute("aria-pressed")).toBe("true");
    expect(observed.some(url => url.includes("/listings?") &&
      url.includes("observed_day=" + today))).toBe(true);

    await act(async () => {
      days[1].click();
      await new Promise(resolve => setTimeout(resolve, 55));
    });
    expect(days[1].getAttribute("aria-pressed")).toBe("true");
    expect(days[0].getAttribute("aria-pressed")).toBe("false");
    expect(new URLSearchParams(window.location.search).get("observed_day")).toBe(expectedDays[1]);
    expect(observed.some(url => url.includes("/listings?") &&
      url.includes("observed_day=" + expectedDays[1]))).toBe(true);

    // Top Eligible listings card must still provide the unfiltered historical list.
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-total")?.click();
      await new Promise(resolve => setTimeout(resolve, 45));
    });
    expect(new URLSearchParams(window.location.search).get("observed_day")).toBe("all");
    expect(container?.querySelector(".etsy-observed-day[aria-pressed='true']")).toBeNull();
    const totalUrls = observed.filter(url => url.includes("/listings?"));
    expect(totalUrls.at(-1)).not.toContain("observed_day");

    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-picked")?.click();
      await new Promise(resolve => setTimeout(resolve, 50));
    });
    const pickedFetches = observed.filter(url => url.includes("/listings?") &&
      url.includes("pick_state=picked"));
    expect(pickedFetches.length).toBeGreaterThan(0);
    expect(pickedFetches.every(url => !url.includes("observed_day="))).toBe(true);
    await act(async () => {
      days[0].click();
      await new Promise(resolve => setTimeout(resolve, 50));
    });
    expect(days[0].getAttribute("aria-pressed")).toBe("true");
    expect(container?.querySelector(".etsy-stat-total[aria-pressed='true']")).toBeTruthy();
    expect(new URLSearchParams(window.location.search).get("observed_day")).toBe(today);
  });

  it("computes Vietnamese calendar days independently from browser timezone", () => {
    expect(vietnamCalendarDay(new Date("2026-11-06T16:59:59Z"))).toBe("2026-11-06");
    expect(vietnamCalendarDay(new Date("2026-11-06T17:00:00Z"))).toBe("2026-11-07");
    expect(recentObservedDays("2026-11-07").slice(0, 4)).toEqual([
      "2026-11-07", "2026-11-06", "2026-11-05", "2026-11-04",
    ]);
  });

  it("keeps six compact KPI cards, including Later after Picked", async () => {
    await start();
    const cards = Array.from(container?.querySelectorAll(".etsy-stats .etsy-stat") || []);
    expect(cards).toHaveLength(6);
    expect(cards.map(card => card.textContent?.split(/[0-9]/)[0]?.trim())).toEqual([
      "Eligible listings", "Picked", "Later", "Blacklist", "Win Keyword", "Keyword nodes",
    ]);
    expect(container?.querySelector(".etsy-stat-shop")).toBeNull();
    expect(container?.querySelector(".etsy-stat-pending")).toBeNull();
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-picked")?.click();
      await new Promise(resolve => setTimeout(resolve, 45));
    });
    expect(observed.some(url => url.includes("/listings?") &&
      url.includes("pick_state=picked"))).toBe(true);
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-keywords")?.click();
      await new Promise(resolve => setTimeout(resolve, 45));
    });
    expect(observed.some(url => url.includes("/keywords?"))).toBe(true);
    expect(container?.querySelector(".etsy-agents")?.tagName).toBe("DETAILS");
  });

  it("does not crash the Trademark tab for null or omitted matched evidence", async () => {
    await start();
    const trademark = Array.from(container?.querySelectorAll(".etsy-stage-nav [role='tab']") || [])
      .find(item => item.textContent?.includes("Trademark")) as HTMLButtonElement;
    // The former crash happened on the first render after switching tabs:
    // Product rows were briefly rendered as Trademark rows before refetch.
    await act(async () => { trademark.click(); });
    // Either a skeleton or the final Trademark rows may be visible when
    // the request resolves quickly; stale Product rows must never be shown.
    expect(container?.querySelector(".etsy-data-table.is-products")).toBeNull();
    expect(Boolean(container?.querySelector(".etsy-skeleton") ||
      container?.querySelector(".etsy-data-table.is-trademarks"))).toBe(true);
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
    const modeButtons = Array.from(container?.querySelectorAll<HTMLButtonElement>(
      ".etsy-map-mode-switch button") || []);
    expect(modeButtons.map(button => button.textContent)).toEqual(["Mindmap", "Tree"]);
    expect(modeButtons[0].getAttribute("aria-pressed")).toBe("true");
    const layout = container?.querySelector(".etsy-keyword-grid");
    expect(layout?.children).toHaveLength(2);
    expect(layout?.firstElementChild?.classList.contains("etsy-parent-keywords")).toBe(true);
    expect(layout?.lastElementChild?.classList.contains("etsy-keyword-workspace")).toBe(true);
    expect(container?.querySelectorAll(".etsy-keyword-metric")).toHaveLength(4);
    await act(async () => { modeButtons[1].click(); await new Promise(resolve => setTimeout(resolve, 35)); });
    expect(modeButtons[1].getAttribute("aria-pressed")).toBe("true");
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

  it("adds parent keywords and edits the actual scheduler priority from Keyword Tree", async () => {
    vi.mocked(fetchAccessIdentity).mockResolvedValue({
      permissions: ["etsy_spy.read", "etsy_spy.manage"],
    } as never);
    await start();
    const tab = Array.from(container?.querySelectorAll<HTMLButtonElement>(
      ".etsy-stage-nav [role='tab']") || []).find(item =>
      item.textContent?.includes("Keyword Tree"))!;
    await act(async () => { tab.click(); await new Promise(resolve => setTimeout(resolve, 100)); });
    expect(container?.querySelector(".etsy-parent-keywords")).toBeTruthy();
    expect(container?.textContent).toContain("A Root Seed");
    const input = container?.querySelector<HTMLInputElement>(
      'input[aria-label="New parent keyword"]')!;
    const priority = container?.querySelector<HTMLInputElement>(
      'input[aria-label="New parent priority"]')!;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    await act(async () => {
      setter.call(input, "  Halloween   ghost shirt ");
      input.dispatchEvent(new Event("input", {bubbles:true}));
      setter.call(priority, "85");
      priority.dispatchEvent(new Event("input", {bubbles:true}));
    });
    await act(async () => {
      container?.querySelector<HTMLFormElement>(".etsy-parent-create")?.dispatchEvent(
        new Event("submit", {bubbles:true,cancelable:true}));
      await new Promise(resolve => setTimeout(resolve, 35));
    });
    expect(parentKeywords.find(k => k.keywordKey === "halloween ghost shirt")?.priority).toBe(85);
    expect(container?.textContent).toContain("Halloween ghost shirt");
    const rowPriority = container?.querySelector<HTMLInputElement>(
      'input[aria-label="Priority for A Root Seed"]')!;
    await act(async () => {
      setter.call(rowPriority, "98");
      rowPriority.dispatchEvent(new Event("input", {bubbles:true}));
    });
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(
        'button[aria-label="Save priority for A Root Seed"]')?.click();
      await new Promise(resolve => setTimeout(resolve, 40));
    });
    expect(parentKeywords[0].keywordKey).toBe("a root seed");
    expect(parentKeywords[0].priority).toBe(98);
    const priorityMode = container?.querySelector(
      '[role="group"][aria-label="Priority mode for A Root Seed"]',
    );
    expect(priorityMode?.querySelectorAll('button[aria-pressed="true"]')).toHaveLength(1);
    const noPriority = Array.from(priorityMode?.querySelectorAll("button") || [])
      .find(button => button.textContent?.includes("Không ưu tiên")) as HTMLButtonElement;
    await act(async () => {
      noPriority.click();
      await new Promise(resolve => setTimeout(resolve, 40));
    });
    expect(parentKeywords.find(row => row.keywordKey === "a root seed")?.priority).toBe(0);
    expect(noPriority.getAttribute("aria-pressed")).toBe("true");
    const withPriority = Array.from(priorityMode?.querySelectorAll("button") || [])
      .find(button => button.textContent?.includes("Ưu tiên") &&
        !button.textContent?.includes("Không")) as HTMLButtonElement;
    await act(async () => {
      withPriority.click();
      await new Promise(resolve => setTimeout(resolve, 40));
    });
    expect(parentKeywords.find(row => row.keywordKey === "a root seed")?.priority).toBe(98);
    expect(withPriority.getAttribute("aria-pressed")).toBe("true");
    expect(observed.some(url => url.endsWith("/parent-keywords"))).toBe(true);
    expect(container?.querySelectorAll(".etsy-parent-row")).toHaveLength(2);
    expect(container?.querySelectorAll(".etsy-tree-row")).toHaveLength(0);
    expect(container?.textContent).toContain("Windows Agent will apply this priority");
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
    expect(container?.querySelector(".etsy-stat-blacklist strong")?.textContent).toBe("2");
    expect(container?.querySelector(".etsy-stat-blacklist[aria-pressed='true']")).toBeTruthy();
    expect(container?.querySelector(".etsy-blacklist-panel")).toBeTruthy();
    expect(container?.querySelector(".etsy-data-table.is-products")).toBeNull();
    expect(new URLSearchParams(window.location.search).get("panel")).toBe("blacklist");
    expect(container?.querySelector(".etsy-blacklist-panel .etsy-blacklist-chips")).toBeTruthy();
    const remove=container?.querySelector<HTMLButtonElement>(
      'button[aria-label="Remove blacklist rudolph"]');
    expect(remove).toBeTruthy();
    await act(async()=>{remove?.click(); await new Promise(r=>setTimeout(r,50));});
    expect(blockedTerms).toEqual(["cat"]);
    expect(container?.textContent).toContain("Blacklist (1)");
    expect(container?.querySelector(".etsy-stat-blacklist strong")?.textContent).toBe("1");
  });

  it("shows blacklist only within its card and filters phrases without searching listings", async () => {
    vi.mocked(fetchAccessIdentity).mockResolvedValue({
      permissions: ["etsy_spy.read", "etsy_spy.manage"],
    } as never);
    await start();
    expect(container?.querySelector(".etsy-blacklist-chips")).toBeNull();
    const input = container?.querySelector<HTMLInputElement>(
      'input[aria-label="Add blacklist phrases"]');
    const addSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    await act(async () => {
      addSetter.call(input, "disney, grinch, harry style");
      input?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      container?.querySelector<HTMLFormElement>(".etsy-blacklist-form")
        ?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await new Promise(resolve => setTimeout(resolve, 60));
    });
    expect(blockedTerms).toHaveLength(3);
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-total")?.click();
      await new Promise(resolve => setTimeout(resolve, 55));
    });
    expect(container?.querySelector(".etsy-blacklist-chips")).toBeNull();
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-blacklist")?.click();
      await new Promise(resolve => setTimeout(resolve, 55));
    });
    expect(container?.querySelector(".etsy-blacklist-panel")).toBeTruthy();
    expect(container?.querySelector(".etsy-stat-blacklist[aria-pressed='true']")).toBeTruthy();
    expect(container?.querySelectorAll(".etsy-blacklist-chip")).toHaveLength(3);
    expect(container?.querySelector(".etsy-search-filters")).toBeNull();
    expect(container?.querySelector(".etsy-pager")).toBeNull();
    expect(new URLSearchParams(window.location.search).get("panel")).toBe("blacklist");

    const search = container?.querySelector<HTMLInputElement>(
      'input[aria-label="Search Spy records"]');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    const queriesBefore = observed.filter(url => url.includes("/listings?")).length;
    await act(async () => {
      setter.call(search, "harry");
      search?.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise(resolve => setTimeout(resolve, 410));
    });
    expect(container?.querySelectorAll(".etsy-blacklist-chip")).toHaveLength(1);
    expect(container?.textContent).toContain("harry style");
    expect(observed.filter(url => url.includes("/listings?")).length).toBe(queriesBefore);
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-picked")?.click();
      await new Promise(resolve => setTimeout(resolve, 55));
    });
    expect(container?.querySelector(".etsy-blacklist-panel")).toBeNull();
    expect(container?.querySelector(".etsy-blacklist-chips")).toBeNull();
    expect(new URLSearchParams(window.location.search).has("panel")).toBe(false);
    expect(container?.querySelector(".etsy-stat-picked[aria-pressed='true']")).toBeTruthy();
  });

  it("saves Win Keywords as passive notes, never filters products or alters Blacklist", async () => {
    vi.mocked(fetchAccessIdentity).mockResolvedValue({
      permissions: ["etsy_spy.read", "etsy_spy.manage"],
    } as never);
    await start();
    const card = container?.querySelector<HTMLButtonElement>(".etsy-stat-win-keyword");
    expect(card).toBeTruthy();
    expect(card?.querySelector("strong")?.textContent).toBe("0");
    await act(async () => { card?.click(); await new Promise(resolve => setTimeout(resolve, 45)); });
    expect(card?.getAttribute("aria-pressed")).toBe("true");
    expect(new URLSearchParams(window.location.search).get("panel")).toBe("win-keywords");
    expect(container?.querySelector(".etsy-win-keyword-panel")).toBeTruthy();
    expect(container?.querySelector(".etsy-data-table.is-products")).toBeNull();
    expect(container?.querySelector<HTMLInputElement>('input[aria-label="Add blacklist phrases"]')).toBeNull();

    const input = container?.querySelector<HTMLInputElement>('input[aria-label="Add Win Keyword"]');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    const listingsBefore = observed.filter(url => url.includes("/listings?")).length;
    await act(async () => {
      setter.call(input, "  Popular Now! , Santa Claus / Rudolph ");
      input?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      container?.querySelector<HTMLFormElement>(".etsy-win-keyword-form")
        ?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await new Promise(resolve => setTimeout(resolve, 60));
    });
    expect(savedWinKeywords).toEqual(["Popular Now!", "Santa Claus / Rudolph"]);
    expect(card?.querySelector("strong")?.textContent).toBe("2");
    expect(container?.querySelectorAll(".etsy-win-keyword-panel .etsy-blacklist-chip")).toHaveLength(2);
    expect(blockedTerms).toEqual([]);
    expect(observed.filter(url => url.includes("/listings?")).length).toBe(listingsBefore);
    expect(observed.some(url => url.includes("/win-keywords"))).toBe(true);

    // Keyword notes survive navigation and never change the Eligible/Picked lists.
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-total")?.click();
      await new Promise(resolve => setTimeout(resolve, 45));
    });
    expect(container?.querySelectorAll(".etsy-product-row")).toHaveLength(3);
    await act(async () => {
      card?.click();
      await new Promise(resolve => setTimeout(resolve, 45));
    });
    expect(container?.querySelectorAll(".etsy-win-keyword-panel .etsy-blacklist-chip")).toHaveLength(2);
    const search = container?.querySelector<HTMLInputElement>('input[aria-label="Search Spy records"]');
    await act(async () => {
      setter.call(search, "Rudolph");
      search?.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise(resolve => setTimeout(resolve, 400));
    });
    expect(container?.querySelectorAll(".etsy-win-keyword-panel .etsy-blacklist-chip")).toHaveLength(1);
    expect(observed.filter(url => url.includes("/listings?")).length).toBe(listingsBefore + 1);

    const remove = container?.querySelector<HTMLButtonElement>(
      'button[aria-label="Remove Win Keyword Santa Claus / Rudolph"]');
    await act(async () => { remove?.click(); await new Promise(resolve => setTimeout(resolve, 50)); });
    expect(savedWinKeywords).toEqual(["Popular Now!"]);
    expect(card?.querySelector("strong")?.textContent).toBe("1");
    expect(blockedTerms).toEqual([]);
  });

  it("places Later after the Etsy link and moves a saved topic into the Later card", async () => {
    vi.mocked(fetchAccessIdentity).mockResolvedValue({
      permissions: ["etsy_spy.read", "etsy_spy.manage"],
    } as never);
    await start();
    const row = Array.from(container?.querySelectorAll(".etsy-product-row") || [])
      .find(item => item.textContent?.includes("Rudolph Shirt")) as HTMLTableRowElement;
    const actions = row?.querySelector(".etsy-row-actions") as HTMLElement;
    const openLink = actions?.querySelector<HTMLAnchorElement>('a[title="Open on Etsy"]');
    const laterBtn = actions?.querySelector<HTMLButtonElement>(".etsy-later-button");
    expect(openLink).toBeTruthy();
    expect(laterBtn?.textContent).toBe("Later");
    expect(laterBtn?.querySelector("svg")).toBeTruthy();
    expect(Array.from(actions.children).indexOf(laterBtn!) >
      Array.from(actions.children).indexOf(openLink!)).toBe(true);
    expect(container?.querySelector(".etsy-stat-later")).toBeTruthy();
    const statCards = Array.from(container?.querySelectorAll(".etsy-stats .etsy-stat") || []);
    expect(statCards.indexOf(container!.querySelector(".etsy-stat-later")!) -
      statCards.indexOf(container!.querySelector(".etsy-stat-picked")!)).toBe(1);

    await act(async () => { laterBtn?.click(); await new Promise(resolve => setTimeout(resolve, 75)); });
    expect(laterSeedKeys.has(listing.listingKey)).toBe(true);
    expect(container?.querySelectorAll(".etsy-product-row")).toHaveLength(2);
    expect(container?.querySelector(".etsy-stat-later strong")?.textContent).toBe("1");
    expect(observed.some(url => url.includes("/listings/123456789/later"))).toBe(true);

    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-later")?.click();
      await new Promise(resolve => setTimeout(resolve, 70));
    });
    expect(new URLSearchParams(window.location.search).get("pick_state")).toBe("later");
    expect(container?.querySelectorAll(".etsy-product-row")).toHaveLength(1);
    expect(container?.querySelector(".etsy-later-match-reason")?.textContent).toContain("Later mẫu");
    expect(container?.querySelector(".etsy-later-button.is-later")).toBeTruthy();

    await act(async () => {
      container?.querySelector<HTMLButtonElement>('button[aria-label="Remove Later seed Rudolph Shirt"]')?.click();
      await new Promise(resolve => setTimeout(resolve, 65));
    });
    expect(laterSeedKeys.size).toBe(0);
    expect(container?.querySelector(".etsy-stat-later strong")?.textContent).toBe("0");
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-total")?.click();
      await new Promise(resolve => setTimeout(resolve, 55));
    });
    expect(container?.querySelectorAll(".etsy-product-row")).toHaveLength(3);
  });

  it("moves a product with icon Pick into the Picked card and restores on Unpick", async () => {
    vi.mocked(fetchAccessIdentity).mockResolvedValue({
      permissions: ["etsy_spy.read", "etsy_spy.manage"],
    } as never);
    await start();
    expect(container?.querySelector(".etsy-stat-picked")).toBeTruthy();
    expect(container?.querySelector(".etsy-stat-picked strong")?.textContent).toBe("0");
    expect(container?.querySelectorAll(".etsy-product-row")).toHaveLength(3);
    const button = container?.querySelector<HTMLButtonElement>(
      'button[aria-label="Pick Rudolph Shirt"]');
    expect(button?.disabled).toBe(false);
    expect(button?.querySelector("svg")).toBeTruthy();
    expect(button?.querySelector("span")?.textContent).toBe("Pick");
    expect(button?.textContent).toBe("Pick");
    await act(async () => {
      button?.click();
      await new Promise(resolve => setTimeout(resolve, 65));
    });
    expect(pickedKeys.has(listing.listingKey)).toBe(true);
    expect(container?.querySelectorAll(".etsy-product-row")).toHaveLength(2);
    expect(container?.querySelector(".etsy-stat-picked strong")?.textContent).toBe("1");
    expect(observed.some(x => x.includes("/listings/123456789/pick"))).toBe(true);
    const picked = container?.querySelector<HTMLButtonElement>(".etsy-stat-picked");
    await act(async () => {
      picked?.click();
      await new Promise(resolve => setTimeout(resolve, 65));
    });
    expect(picked?.getAttribute("aria-pressed")).toBe("true");
    expect(container?.querySelector(".etsy-stat-picked[aria-pressed='true']")).toBeTruthy();
    expect(container?.querySelectorAll(".etsy-product-row")).toHaveLength(1);
    expect(container?.textContent).toContain("Rudolph Shirt");
    expect(observed.some(x => x.includes("pick_state=picked"))).toBe(true);
    const unpick = container?.querySelector<HTMLButtonElement>(
      'button[aria-label="Unpick Rudolph Shirt"]');
    expect(unpick?.getAttribute("aria-pressed")).toBe("true");
    expect(unpick?.querySelector("svg")).toBeTruthy();
    expect(unpick?.querySelector("span")?.textContent).toBe("Picked");
    expect(unpick?.textContent).toBe("Picked");
    await act(async () => {
      unpick?.click();
      await new Promise(resolve => setTimeout(resolve, 65));
    });
    expect(pickedKeys.size).toBe(0);
    expect(container?.querySelectorAll(".etsy-product-row")).toHaveLength(0);
    expect(container?.querySelector(".etsy-stat-picked strong")?.textContent).toBe("0");
    const eligible = container?.querySelector<HTMLButtonElement>(".etsy-stat-total");
    await act(async () => {
      eligible?.click();
      await new Promise(resolve => setTimeout(resolve, 60));
    });
    expect(container?.querySelectorAll(".etsy-product-row")).toHaveLength(3);
    const restoredPick = container?.querySelector<HTMLButtonElement>(
      'button[aria-label="Pick Rudolph Shirt"]');
    expect(restoredPick?.textContent).toBe("Pick");
    expect(restoredPick?.querySelector("svg")).toBeTruthy();
  });

  it("pairs Image Agent separately and copies disabled D: configuration", async () => {
    vi.mocked(fetchAccessIdentity).mockResolvedValue({
      permissions: ["etsy_spy.read", "etsy_spy.manage"],
    } as never);
    await start();
    const panel = container?.querySelector<HTMLDetailsElement>(
      'details[aria-label="Windows image extraction agents"]');
    expect(panel?.textContent).toContain("Extraction disabled");
    const pair = Array.from(panel?.querySelectorAll<HTMLButtonElement>("button") || [])
      .find(item => item.textContent?.trim() === "Pair Image Agent");
    await act(async () => { pair?.click(); });
    const create = Array.from(panel?.querySelectorAll<HTMLButtonElement>("button") || [])
      .find(item => item.textContent?.trim() === "Create Image Agent");
    expect(create).toBeTruthy();
    await act(async () => {
      create?.click();
      await new Promise(resolve => setTimeout(resolve, 70));
    });
    expect(registeredImageAgents).toHaveLength(1);
    expect(panel?.textContent).toContain("Token:");
    const writeText = vi.fn(async (_value: string) => {});
    Object.defineProperty(navigator, "clipboard", {
      configurable: true, value: { writeText },
    });
    const copy = Array.from(panel?.querySelectorAll<HTMLButtonElement>("button") || [])
      .find(item => item.textContent?.trim() === "Copy disabled Windows config");
    await act(async () => {copy?.click();});
    const config = JSON.parse(String(writeText.mock.calls[0]?.[0])) as {
      enabled: boolean; cam_origin: string; agent_id: string;
      tenant_id: string; token: string;
    };
    expect(config.enabled).toBe(false);
    expect(config.tenant_id).toBe("boostgame");
    expect(config.agent_id).toBe(registeredImageAgents[0].id);
    expect(config.token.length).toBe(70);
    expect(localStorage.length).toBe(0);
    expect(panel?.textContent).toContain("storage not provisioned");
  });

  it("disables Pick for users without manage permission", async () => {
    await start();
    expect(container?.querySelector<HTMLButtonElement>(
      'button[aria-label="Pick Rudolph Shirt"]')?.disabled).toBe(true);
  });
});