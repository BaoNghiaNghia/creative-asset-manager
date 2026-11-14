// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { EtsySpyPage, vietnamCalendarDay, recentObservedDays } from "./EtsySpyPage";
import { fetchAccessIdentity } from "../../features/access_management";
import type { DesignAsset } from "./api";

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
let smsProjection: { status: string; code?: string; sentAt?: string } | null = null;
let designProjection: DesignAsset[] = [];
let extractionProjection: { job_id: string; status: string; review_flags?: string[];
  upscale_factor?: number | null; rights_status: string; print_ready: boolean;
  candidate_ready?: boolean; candidate_method?: string | null } | null = null;
let laterSeedKeys = new Set<string>();
let newSpyListingSynced = false;
let simulatedListingPages = 1;
let extractionPilotOn = false;
let tiktokFeatureOn = false;
let tiktokStatuses: Record<string, string> = {};
let tiktokJobs: string[] = [];
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
  smsProjection = null; extractionProjection = null; designProjection = [];
  newSpyListingSynced = false; simulatedListingPages = 1; extractionPilotOn = false;
  tiktokFeatureOn = false; tiktokStatuses = {}; tiktokJobs = [];
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
    if (url.endsWith("/listings/bulk") && init?.method === "POST") {
      const {action, listingKeys} = JSON.parse(String(init.body)) as {
        action: "pick" | "later"; listingKeys: string[];
      };
      const appliedListingKeys: string[] = [];
      const skipped: Array<{listingKey: string; reason: string}> = [];
      for (const id of listingKeys) {
        if (action === "pick" && !pickedKeys.has(id) && !laterSeedKeys.has(id)) {
          pickedKeys.add(id); appliedListingKeys.push(id);
        } else if (action === "later" && !pickedKeys.has(id) && !laterSeedKeys.has(id)) {
          laterSeedKeys.add(id); appliedListingKeys.push(id);
        } else skipped.push({listingKey: id, reason: "already_decided"});
      }
      return json({ok: true, action, requested: listingKeys.length,
        applied: appliedListingKeys.length, appliedListingKeys,
        skippedCount: skipped.length, skipped});
    }
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
    if (url.includes("/listings/observed-day-groups?")) {
      const params = new URL(url, "http://localhost").searchParams;
      const day = params.get("day");
      const pick_state = params.get("pick_state") || "eligible";
      const days = recentObservedDays(vietnamCalendarDay());
      const items = pick_state === "picked"
        ? day === days[0] ? [{key: "christmas", label: "Christmas", count: 1}]
          : day === days[1] ? [{key: "books", label: "Books", count: 4}] : []
        : pick_state === "later"
          ? day === days[0] ? [{key: "animals", label: "Animals", count: 2}]
            : day === days[1] ? [{key: "family", label: "Family", count: 1}] : []
          : day === days[0] ? [
            {key: "christmas", label: "Christmas", count: newSpyListingSynced ? 3 : 2},
            {key: "thanksgiving", label: "Thanksgiving", count: 1},
          ] : day === days[1] ? [
            {key: "books", label: "Books", count: 2},
          ] : [];
      return json({day, pick_state, timezone: "Asia/Ho_Chi_Minh", start_hour: 6,
        total: items.reduce((sum, item) => sum + item.count, 0), items});
    }
    if (url.includes("/listings/observed-days?")) {
      const pick_state = new URL(url, "http://localhost").searchParams.get("pick_state") || "eligible";
      const days = recentObservedDays(vietnamCalendarDay());
      return json({pick_state, timezone: "Asia/Ho_Chi_Minh", start_hour: 6,
        items: days.map((day, index) => {
          const count = pick_state === "picked" ? (index === 0 ? 1 : index === 1 ? 4 : 0)
            : pick_state === "later" ? (index === 0 ? 2 : index === 1 ? 1 : 0)
            : index === 0 ? (newSpyListingSynced ? 4 : 3) : index === 1 ? 2 : 0;
          return {day, count, pickedCount: pick_state === "picked" ? count
            : pick_state === "eligible" ? (index === 0 ? 1 : index === 1 ? 4 : 0) : 0};
        }),
      });
    }
    if (url.includes("/summary")) return json({
      ...summary, picked: pickedKeys.size, picked_images: {
        checked: pickedKeys.size, complete: 0, missing: pickedKeys.size,
      }, later: [...laterSeedKeys].filter(id => !pickedKeys.has(id)).length,
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
      return json({agent_id, token: "A".repeat(70), tenant_id: "creative-assets-prod"});
    }
    if (url.endsWith("/image-agents")) return json({items: [...registeredImageAgents]});
    if (url.endsWith("/image-agents/overview")) return json({
      enabled:extractionPilotOn, daily_cap:extractionPilotOn ? 1 : 200,
      claimed_today:0, remaining_today:extractionPilotOn ? 1 : 200,
      queued:extractionPilotOn ? 246 : 0, processing:0, completed_today:0, needs_review_today:0,
      needs_ai_today:0, failed_today:0, timezone:"Asia/Ho_Chi_Minh",
      storage:{ready:extractionPilotOn,reason:"storage_not_provisioned",
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
    if (url.endsWith("/tiktok-listings/overview")) return json({enabled:tiktokFeatureOn,auto_on_pick:false,counts:{completed:0,pending:0,processing:0,failed:0}});
    if (url.includes("/tiktok-listing/") && init?.method === "POST") {
      const id = url.split("/listings/")[1]?.split("/")[0] || "";
      tiktokJobs.push(id); tiktokStatuses[id] = "pending";
      return json({status:"pending",draft_only:true});
    }
    if (url.includes("/listings?")) {
      const params = new URL(url, "http://localhost").searchParams;
      const pickState = params.get("pick_state") || "eligible";
      const pageNumber = Number(params.get("page") || 1);
      const newlySynced = {
        ...listing, listingKey: "333", pageTitle: "Merry Christmas Reindeer Tee",
        url: "https://www.etsy.com/listing/333/christmas-reindeer",
      };
      const visible = [
        listing, popularNoMatch, deadListing, ...(newSpyListingSynced ? [newlySynced] : []),
      ].filter(x =>
        pickState === "picked" ? pickedKeys.has(x.listingKey)
        : pickState === "later" ? laterSeedKeys.has(x.listingKey) && !pickedKeys.has(x.listingKey)
        : !pickedKeys.has(x.listingKey) && !laterSeedKeys.has(x.listingKey)).map(x =>
          ({ ...x, picked: pickedKeys.has(x.listingKey),
             smsPush: pickedKeys.has(x.listingKey) ? smsProjection : null,
             extraction: pickedKeys.has(x.listingKey) ? extractionProjection : null,
             designs: pickedKeys.has(x.listingKey) ? designProjection : [],
             tiktokListing: pickedKeys.has(x.listingKey) ? {status:tiktokStatuses[x.listingKey] || "not_queued",draft_only:true} : undefined,
             later: laterSeedKeys.has(x.listingKey), laterManual: laterSeedKeys.has(x.listingKey),
             laterReason: laterSeedKeys.has(x.listingKey) ? "Được chọn Later" : null }));
      return json({items: simulatedListingPages > 1 && pageNumber > simulatedListingPages
          ? [] : visible, page: pageNumber, page_size: Number(params.get("page_size") || 50),
        total: simulatedListingPages > 1 ? simulatedListingPages * 50 : visible.length,
        pages: simulatedListingPages, last_synced_at: summary.last_synced_at,
        source_status: "offline"});
    }
    return json({items: [], page:1,page_size:50,total:0,pages:0,
      last_synced_at:summary.last_synced_at,source_status:"offline"});
  }));
}
let root: Root | null = null;
let container: HTMLDivElement | null = null;
async function start({tiktokEnabled = false, initialUrl = "/etsy-spy", listingPages = 1}:
  {tiktokEnabled?: boolean; initialUrl?: string; listingPages?: number} = {}) {
  // jsdom does not ship ResizeObserver, while the real browser provides it.
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
  installFetch();
  simulatedListingPages = listingPages;
  tiktokFeatureOn = tiktokEnabled;
  window.history.replaceState(null, "", initialUrl);
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
  it("keeps date filters mounted while collapsing the adaptive explorer", async () => {
    await start({initialUrl: "/etsy-spy?tab=products&observed_day=all"});
    const toggle = container?.querySelector<HTMLButtonElement>(".etsy-facet-toggle");
    const details = container?.querySelector<HTMLElement>(".etsy-facet-details");
    expect(toggle?.getAttribute("aria-controls")).toBe("etsy-facet-details");
    expect(toggle?.getAttribute("aria-expanded")).toBe("false");
    expect(toggle?.textContent).toContain("Tất cả ngày");
    expect(details?.classList.contains("is-open")).toBe(false);
    const before = observed.filter(url => url.includes("/listings?")).length;
    await act(async () => { toggle?.click(); });
    expect(toggle?.getAttribute("aria-expanded")).toBe("true");
    expect(details?.classList.contains("is-open")).toBe(true);
    expect(details?.querySelectorAll(".etsy-observed-day").length).toBe(10);
    await act(async () => { toggle?.click(); });
    expect(toggle?.getAttribute("aria-expanded")).toBe("false");
    expect(details?.querySelectorAll(".etsy-observed-day").length).toBe(10);
    expect(observed.filter(url => url.includes("/listings?")).length).toBe(before);
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-picked")?.click();
      await new Promise(resolve => setTimeout(resolve, 50));
    });
    expect(toggle?.textContent).toContain("Tất cả ngày");
    expect(new URLSearchParams(window.location.search).get("pick_state")).toBe("picked");
  });

  it("offers sorting and jumping directly to the results on compact screens", async () => {
    await start({initialUrl: "/etsy-spy?tab=products&observed_day=all"});
    const sort = container?.querySelector<HTMLSelectElement>("#etsy-quick-sort");
    const direction = container?.querySelector<HTMLButtonElement>(".etsy-adaptive-sort-direction");
    expect(sort?.value).toBe("observedAt");
    expect(direction?.getAttribute("aria-label")).toBe("Sắp xếp tăng dần");
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!;
      setter.call(sort, "views24h");
      sort?.dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise(resolve => setTimeout(resolve, 50));
    });
    const params = new URLSearchParams(window.location.search);
    expect(params.get("sort")).toBe("views24h");
    expect(params.get("dir")).toBe("desc");
    await act(async () => { direction?.click(); });
    expect(new URLSearchParams(window.location.search).get("dir")).toBe("asc");
    const results = container?.querySelector<HTMLElement>("#etsy-product-results");
    const scroll = vi.fn();
    if (results) results.scrollIntoView = scroll;
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-adaptive-jump")?.click();
    });
    expect(scroll).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
  });

  it("shows table column navigation only when the desktop table overflows", async () => {
    await start({initialUrl: "/etsy-spy?tab=products&observed_day=all"});
    const viewport = container?.querySelector<HTMLDivElement>(".etsy-table-wrap");
    expect(viewport).toBeTruthy();
    Object.defineProperties(viewport!, {
      clientWidth: {configurable: true, value: 600},
      scrollWidth: {configurable: true, value: 1500},
      scrollLeft: {configurable: true, value: 0, writable: true},
    });
    await act(async () => { window.dispatchEvent(new Event("resize")); });
    const controls = container?.querySelector(".etsy-table-scroll-nav");
    expect(controls?.textContent).toContain("Còn cột bên phải");
    const left = controls?.querySelector<HTMLButtonElement>('button[aria-label="Cuộn bảng sang trái"]');
    const right = controls?.querySelector<HTMLButtonElement>('button[aria-label="Cuộn bảng sang phải"]');
    expect(left?.disabled).toBe(true);
    expect(right?.disabled).toBe(false);
    await act(async () => { right?.click(); });
    expect(viewport?.scrollLeft).toBeGreaterThan(0);
    expect(left?.disabled).toBe(false);
    await act(async () => {
      viewport!.scrollLeft = 900;
      viewport?.dispatchEvent(new Event("scroll", {bubbles: true}));
    });
    expect(right?.disabled).toBe(true);
    expect(container?.querySelector(".etsy-table-scroll-nav")?.textContent).toContain("Đã đến cột cuối");
    await act(async () => {
      Object.defineProperty(viewport!, "scrollWidth", {configurable: true, value: 600});
      viewport!.scrollLeft = 0;
      window.dispatchEvent(new Event("resize"));
    });
    expect(container?.querySelector(".etsy-table-scroll-nav")).toBeNull();
    expect(container?.querySelector(".etsy-product-row")).toBeTruthy();
  });

  it("jumps directly to a valid page without dropping the active day and tab", async () => {
    await start({ initialUrl: "/etsy-spy?tab=products&observed_day=all&page=2", listingPages: 12 });
    const input = container?.querySelector<HTMLInputElement>(".etsy-page-jump input");
    const go = container?.querySelector<HTMLButtonElement>(".etsy-page-jump button");
    expect(input?.value).toBe("2");
    expect(go?.disabled).toBe(true);
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    await act(async () => {
      setter.call(input, "7");
      input?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(go?.disabled).toBe(false);
    await act(async () => {
      go?.click();
      await new Promise(resolve => setTimeout(resolve, 55));
    });
    expect(new URLSearchParams(window.location.search).get("page")).toBe("7");
    expect(new URLSearchParams(window.location.search).get("observed_day")).toBe("all");
    expect(input?.value).toBe("7");
    await act(async () => {
      setter.call(input, "999");
      input?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(go?.disabled).toBe(true);
    await act(async () => { go?.click(); });
    expect(new URLSearchParams(window.location.search).get("page")).toBe("7");
    await act(async () => {
      container?.querySelector<HTMLButtonElement>('button[aria-label="Previous page"]')?.click();
      await new Promise(resolve => setTimeout(resolve, 50));
    });
    expect(new URLSearchParams(window.location.search).get("page")).toBe("6");
    expect(input?.value).toBe("6");
  });

  it("shows an accurate zero range for a page that has moved beyond the last page", async () => {
    await start({ initialUrl: "/etsy-spy?tab=products&observed_day=all&page=5", listingPages: 3 });
    expect(container?.querySelector(".etsy-page-range")?.textContent).toContain("0–0 / 150");
    expect(container?.querySelector(".etsy-empty-title")?.textContent)
      .toBe("Trang này không còn sản phẩm");
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-empty-action")?.click();
      await new Promise(resolve => setTimeout(resolve, 50));
    });
    expect(new URLSearchParams(window.location.search).get("page")).toBe("1");
    expect(container?.querySelector(".etsy-page-range")?.textContent).toContain("1–3 / 150");
  });

  it("recovers an empty filtered Picked page without losing its scope", async () => {
    await start({initialUrl: "/etsy-spy?tab=products&pick_state=picked&observed_day=2026-11-11&group=christmas&q=Santa"});
    expect(container?.querySelector(".etsy-empty-title")?.textContent)
      .toBe("Không tìm thấy sản phẩm phù hợp");
    const reset = container?.querySelector<HTMLButtonElement>(".etsy-empty-action");
    expect(reset?.textContent).toContain("Xóa lọc");
    expect(reset?.getAttribute("aria-label")).toContain("giữ nguyên danh mục");
    await act(async () => {
      reset?.click();
      await new Promise(resolve => setTimeout(resolve, 430));
    });
    const params = new URLSearchParams(window.location.search);
    expect(params.get("pick_state")).toBe("picked");
    expect(params.get("observed_day")).toBe("all");
    expect(params.get("q")).toBeNull();
    expect(container?.querySelector(".etsy-empty-title")?.textContent)
      .toBe("Chưa có sản phẩm Picked");
    const toEligible = container?.querySelector<HTMLButtonElement>(".etsy-empty-action");
    expect(toEligible?.textContent).toContain("Eligible");
    await act(async () => { toEligible?.click(); });
    expect(new URLSearchParams(window.location.search).get("pick_state") || "eligible").toBe("eligible");
    expect(container?.querySelector(".etsy-product-row")).toBeTruthy();
  });

  it("explains an empty Later list and offers navigation back to Eligible", async () => {
    await start({initialUrl: "/etsy-spy?tab=products&pick_state=later&observed_day=all"});
    expect(container?.querySelector(".etsy-empty-title")?.textContent)
      .toBe("Chưa có sản phẩm Later");
    expect(container?.querySelector(".etsy-empty-description")?.textContent)
      .toContain("Chuyển sản phẩm sang Later");
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-empty-action")?.click();
    });
    expect(new URLSearchParams(window.location.search).get("pick_state") || "eligible").toBe("eligible");
    expect(container?.querySelector(".etsy-product-row")).toBeTruthy();
  });

  it("explains applied Picked filters and clears day, group and search without changing the Picked scope", async () => {
    await start({initialUrl: "/etsy-spy?tab=products&pick_state=picked&observed_day=2026-11-11&group=christmas&q=Santa&page=3"});
    const context = container?.querySelector<HTMLElement>(".etsy-filter-context");
    expect(context?.textContent).toContain("Đang lọc");
    expect(context?.textContent).toContain("Ngày:");
    expect(context?.textContent).toContain("Nhóm:");
    expect(context?.textContent).toContain("Tìm: Santa");
    expect(container?.querySelector(".etsy-stat-picked[aria-pressed='true']")).toBeTruthy();
    await act(async () => {
      context?.querySelector<HTMLButtonElement>(".etsy-filter-reset")?.click();
      await new Promise(resolve => setTimeout(resolve, 450));
    });
    const params = new URLSearchParams(window.location.search);
    expect(params.get("pick_state")).toBe("picked");
    expect(params.get("observed_day")).toBe("all");
    expect(params.get("page")).toBe("1");
    expect(params.get("group")).toBeNull();
    expect(params.get("q")).toBeNull();
    expect(container?.querySelector(".etsy-filter-context")).toBeNull();
    expect(container?.querySelector(".etsy-stat-picked[aria-pressed='true']")).toBeTruthy();
  });

  it("cancels a pending search debounce when switching from Products to another tab", async () => {
    await start({initialUrl: "/etsy-spy?tab=products&q=Santa&observed_day=all"});
    await act(async () => {
      container?.querySelector<HTMLButtonElement>("#etsy-tab-keywords")?.click();
      await new Promise(resolve => setTimeout(resolve, 450));
    });
    const params = new URLSearchParams(window.location.search);
    expect(params.get("tab")).toBe("keywords");
    expect(params.get("q")).toBeNull();
    expect(container?.querySelector<HTMLInputElement>('input[aria-label="Search Spy records"]')?.value).toBe("");
  });

  it("shows the current Eligible day as a filter with a keyboard-accessible clear action", async () => {
    await start();
    const context = container?.querySelector<HTMLElement>(".etsy-filter-context");
    expect(context?.textContent).toContain("Ngày: Hôm nay");
    const clear = context?.querySelector<HTMLButtonElement>(".etsy-filter-reset");
    expect(clear?.getAttribute("aria-label")).toContain("giữ nguyên tab");
    expect(clear?.type).toBe("button");
  });

  it("preserves deep-linked pagination and avoids an unchanged mount-time search refresh", async () => {
    await start({ initialUrl: "/etsy-spy?tab=products&page=3&observed_day=all" });
    const initialQueries = observed.filter(url => url.includes("/listings?")).length;
    expect(new URLSearchParams(window.location.search).get("page")).toBe("3");
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 435)); });
    expect(new URLSearchParams(window.location.search).get("page")).toBe("3");
    expect(observed.filter(url => url.includes("/listings?"))).toHaveLength(initialQueries);
  });

  it("still resets pagination only after the operator types a different search", async () => {
    await start({ initialUrl: "/etsy-spy?tab=products&page=3&observed_day=all" });
    const initialQueries = observed.filter(url => url.includes("/listings?")).length;
    const search = container?.querySelector<HTMLInputElement>(
      'input[aria-label="Search Spy records"]');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    await act(async () => {
      setter.call(search, "Santa");
      search?.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise(resolve => setTimeout(resolve, 410));
    });
    const params = new URLSearchParams(window.location.search);
    expect(params.get("q")).toBe("Santa");
    expect(params.get("page")).toBe("1");
    expect(observed.filter(url => url.includes("/listings?")).length)
      .toBe(initialQueries + 1);
  });

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
    const headings = Array.from(container?.querySelectorAll(".etsy-data-table.is-products th:not(.etsy-select-header)") || [])
      .map(x => (x.textContent || "").trim().replace(/\s*[↑↓]$/, ""));
    expect(headings).toEqual([
      "Product / Etsy", "Keyword SMS", "Etsy Badge + TM",
      "Bought / 24h", "Views / 24h", "Carts", "Observed", "Action",
    ]);
    expect(container?.querySelectorAll(".etsy-product-row")[0]?.querySelectorAll("td")).toHaveLength(9);
    expect(container?.querySelector(".etsy-select-header input[type='checkbox']")).toBeTruthy();
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
    const cells = Array.from(container?.querySelectorAll(".etsy-product-row td:nth-child(7)") || []);
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
    const cartsCells = container?.querySelectorAll(".etsy-product-row td:nth-child(7)");
    expect(cartsCells).toHaveLength(3);
    expect(Array.from(cartsCells || [], cell => cell.textContent)).toEqual(["20+", "25", "—"]);
    const today = vietnamCalendarDay();
    expect(observed.some(url => url.includes("/listings?") &&
      url.includes("observed_day=" + today))).toBe(true);
    expect(container?.querySelector(".etsy-observed-day[aria-pressed='true']")?.getAttribute(
      "data-observed-day")).toBe(today);
  });

  it("shows exclusive daily topic badges and filters the selected day's listings", async () => {
    await start();
    const today = vietnamCalendarDay();
    const section = container?.querySelector(".etsy-day-groups");
    expect(section?.getAttribute("aria-label")).toContain(today);
    const todayBadges = Array.from(section?.querySelectorAll<HTMLButtonElement>(
      ".etsy-day-group") || []);
    expect(todayBadges.map(badge => badge.textContent)).toEqual([
      "Tất cả3", "Christmas2", "Thanksgiving1",
    ]);
    expect(observed.some(url => url.includes("/listings/observed-day-groups?day=" + today)))
      .toBe(true);
    await act(async () => {
      todayBadges[1].click();
      await new Promise(resolve => setTimeout(resolve, 40));
    });
    expect(new URLSearchParams(window.location.search).get("group")).toBe("christmas");
    expect(new URLSearchParams(window.location.search).get("observed_day")).toBe(today);
    expect(observed.some(url => url.includes("/listings?") && url.includes("group=christmas")))
      .toBe(true);
    expect(container?.querySelector<HTMLButtonElement>(
      ".etsy-day-group[aria-pressed='true']")?.textContent).toContain("Christmas");

    // Selecting a different date changes the category family, clears the
    // stale Christmas filter, and never reuses yesterday's badge totals.
    const yesterday = container?.querySelectorAll<HTMLButtonElement>(".etsy-observed-day")[1];
    await act(async () => {
      yesterday?.click();
      await new Promise(resolve => setTimeout(resolve, 55));
    });
    expect(new URLSearchParams(window.location.search).get("group")).toBeNull();
    expect(new URLSearchParams(window.location.search).get("observed_day"))
      .toBe(recentObservedDays(today)[1]);
    const after = Array.from(container?.querySelectorAll<HTMLButtonElement>(
      ".etsy-day-group") || []);
    expect(after.map(badge => badge.textContent)).toEqual(["Tất cả2", "Books2"]);
    await act(async () => {after[1].click(); await new Promise(r => setTimeout(r, 40));});
    expect(new URLSearchParams(window.location.search).get("group")).toBe("books");
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-total")?.click();
      await new Promise(resolve => setTimeout(resolve, 45));
    });
    expect(new URLSearchParams(window.location.search).get("group")).toBeNull();
  });

  it("automatically refreshes group counts and listings after a new Spy sync", async () => {
    await start();
    const initial = container?.querySelector(".etsy-day-groups-list");
    expect(initial?.textContent).toContain("Christmas2");
    const initialListFetches = observed.filter(url => url.includes("/listings?")).length;

    // A new qualified Etsy listing arrives in the backend while the
    // dashboard remains open. No manual refresh or categorization job.
    newSpyListingSynced = true;
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      await new Promise(resolve => setTimeout(resolve, 100));
    });
    expect(container?.querySelector(".etsy-day-groups-list")?.textContent)
      .toContain("Christmas3");
    expect(container?.querySelector<HTMLButtonElement>(
      ".etsy-observed-day")?.querySelector(".etsy-observed-day-eligible")?.textContent)
      .toBe("4Khả dụng");
    expect(container?.textContent).toContain("Merry Christmas Reindeer Tee");
    expect(observed.filter(url => url.includes("/listings?")).length)
      .toBeGreaterThan(initialListFetches);

    // A focus with identical server aggregates must not trigger another
    // expensive table fetch or disturb the current product filters.
    const afterUpdateFetches = observed.filter(url => url.includes("/listings?")).length;
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      await new Promise(resolve => setTimeout(resolve, 40));
    });
    expect(observed.filter(url => url.includes("/listings?")).length)
      .toBe(afterUpdateFetches);
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

    // Selecting the Eligible parent restores the screenshot default: Today + All groups.
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-total")?.click();
      await new Promise(resolve => setTimeout(resolve, 45));
    });
    expect(new URLSearchParams(window.location.search).get("observed_day")).toBe(today);
    expect(container?.querySelector(".etsy-observed-day[aria-pressed='true']")?.getAttribute(
      "data-observed-day")).toBe(today);

    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-picked")?.click();
      await new Promise(resolve => setTimeout(resolve, 50));
    });
    const pickedFetches = observed.filter(url => url.includes("/listings?") &&
      url.includes("pick_state=picked"));
    expect(pickedFetches.length).toBeGreaterThan(0);
    expect(pickedFetches.at(-1)).not.toContain("group=");
    expect(pickedFetches.at(-1)).not.toContain("observed_day=");
    expect(container?.querySelector(".etsy-observed-day[aria-pressed='true']")).toBeNull();
    expect(container?.querySelector(".etsy-day-groups-empty")?.textContent)
      .toContain("Chọn một ngày");
    // Selecting a different day is still supported within the Picked card.
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(
        '.etsy-observed-day[data-observed-day="' + expectedDays[1] + '"]')?.click();
      await new Promise(resolve => setTimeout(resolve, 50));
    });
    expect(new URLSearchParams(window.location.search).get("observed_day"))
      .toBe(expectedDays[1]);
    // Re-selecting Picked restores all dates, including historical completed PNGs.
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-picked")?.click();
      await new Promise(resolve => setTimeout(resolve, 50));
    });
    expect(container?.querySelector(".etsy-stat-picked[aria-pressed='true']")).toBeTruthy();
    expect(new URLSearchParams(window.location.search).get("pick_state")).toBe("picked");
    expect(new URLSearchParams(window.location.search).get("observed_day")).toBe("all");
    expect(new URLSearchParams(window.location.search).get("group")).toBeNull();
  });


  it("scopes every product subcard to Picked/Later and hides irrelevant product facets in keyword panels", async () => {
    await start();
    const today = vietnamCalendarDay();
    const yesterday = recentObservedDays(today)[1];
    // Default follows the requested screenshot: Eligible > Today > All groups.
    expect(container?.querySelector(".etsy-stat-total[aria-pressed='true']")).toBeTruthy();
    expect(container?.querySelector(".etsy-observed-day[aria-pressed='true']")?.getAttribute("data-observed-day"))
      .toBe(today);
    expect(container?.querySelector(".etsy-day-group[aria-pressed='true']")?.textContent)
      .toContain("Tất cả");

    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-picked")?.click();
      await new Promise(resolve => setTimeout(resolve, 60));
    });
    expect(observed.some(url => url.includes("/listings/observed-days?pick_state=picked"))).toBe(true);
    const pickedDay = container?.querySelector<HTMLButtonElement>(
      '.etsy-observed-day[data-pick-state="picked"][data-observed-day="' + yesterday + '"]');
    expect(pickedDay?.textContent).toBe("Hôm qua4Picked");
    expect(pickedDay?.querySelector(".etsy-observed-day-pick")).toBeNull();
    // Picked opens all historical days so previously produced PNGs are
    // visible; the day badges still permit narrowing to one selected day.
    expect(container?.querySelector(".etsy-observed-day[aria-pressed='true']")).toBeNull();
    expect(new URLSearchParams(window.location.search).get("observed_day")).toBe("all");
    expect(container?.querySelector(".etsy-day-groups-empty")?.textContent)
      .toContain("Chọn một ngày");
    expect(new URLSearchParams(window.location.search).get("group")).toBeNull();
    await act(async () => {
      pickedDay?.click();
      await new Promise(resolve => setTimeout(resolve, 55));
    });
    expect(observed.some(url => url.includes("/observed-day-groups?") &&
      url.includes("pick_state=picked") && url.includes("day=" + yesterday))).toBe(true);
    expect(container?.querySelector(".etsy-day-groups-list")?.textContent).toContain("Books4");
    const books = Array.from(container?.querySelectorAll<HTMLButtonElement>(".etsy-day-group") || [])
      .find(node => node.textContent?.includes("Books"));
    await act(async () => {
      books?.click();
      await new Promise(resolve => setTimeout(resolve, 40));
    });
    expect(observed.some(url => url.includes("/listings?") && url.includes("pick_state=picked") &&
      url.includes("observed_day=" + yesterday) && url.includes("group=books"))).toBe(true);
    expect(container?.querySelector(".etsy-stat-picked[aria-pressed='true']")).toBeTruthy();

    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-later")?.click();
      await new Promise(resolve => setTimeout(resolve, 55));
    });
    expect(container?.querySelector(".etsy-observed-day[data-pick-state=picked]")).toBeNull();
    expect(container?.querySelector(".etsy-observed-day[data-pick-state=later]")).toBeTruthy();
    expect(observed.some(url => url.includes("/observed-days?pick_state=later"))).toBe(true);
    expect(new URLSearchParams(window.location.search).get("group")).toBeNull();
    expect(new URLSearchParams(window.location.search).get("observed_day")).toBe(today);
    expect(container?.querySelector(
      '.etsy-observed-day[data-pick-state=later][aria-pressed=true]')?.getAttribute(
        "data-observed-day")).toBe(today);
    expect(container?.querySelector(".etsy-day-group[aria-pressed='true']")?.textContent)
      .toContain("Tất cả");
    expect(container?.querySelector(".etsy-day-groups-list")?.textContent).toContain("Animals2");
    const animals = Array.from(container?.querySelectorAll<HTMLButtonElement>(".etsy-day-group") || [])
      .find(node => node.textContent?.includes("Animals"));
    await act(async () => { animals?.click(); await new Promise(resolve => setTimeout(resolve, 45)); });
    expect(observed.some(url => url.includes("/listings?") && url.includes("pick_state=later") &&
      url.includes("observed_day=" + today) && url.includes("group=animals"))).toBe(true);

    // Switching between any KPI parents must reset both facets, not keep the
    // previously clicked Books / Animals badge or a prior observation date.
    for (const [card, pickState] of [
      [".etsy-stat-total", "eligible"],
      [".etsy-stat-picked", "picked"],
      [".etsy-stat-later", "later"],
    ] as const) {
      await act(async () => {
        container?.querySelector<HTMLButtonElement>(card)?.click();
        await new Promise(resolve => setTimeout(resolve, 65));
      });
      const params = new URLSearchParams(window.location.search);
      expect(params.get("pick_state") ?? "eligible").toBe(pickState);
      const expectedDay = pickState === "picked" ? "all" : today;
      expect(params.get("observed_day")).toBe(expectedDay);
      expect(params.get("group")).toBeNull();
      if (pickState === "picked") {
        expect(container?.querySelector(".etsy-observed-day[aria-pressed='true']")).toBeNull();
      } else {
        expect(container?.querySelector(".etsy-observed-day[aria-pressed='true']")?.getAttribute(
          "data-observed-day")).toBe(today);
        expect(container?.querySelector(".etsy-day-group[aria-pressed='true']")?.textContent)
          .toContain("Tất cả");
      }
      const listQueries = observed.filter(url => url.includes("/listings?"));
      if (pickState === "picked") {
        expect(listQueries.at(-1)).not.toContain("observed_day=");
      } else {
        expect(listQueries.at(-1)).toContain("observed_day=" + expectedDay);
      }
      expect(listQueries.at(-1)).not.toContain("group=");
    }

    for (const section of ["blacklist", "win-keyword", "keywords"] as const) {
      await act(async () => {
        container?.querySelector<HTMLButtonElement>(".etsy-stat-" + section)?.click();
        await new Promise(resolve => setTimeout(resolve, 45));
      });
      expect(container?.querySelector(".etsy-observed-day")).toBeNull();
      expect(container?.querySelector(".etsy-day-group")).toBeNull();
      if (section === "blacklist" || section === "win-keyword")
        expect(container?.querySelector(".etsy-facet-context")).toBeTruthy();
    }
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
    expect(cards[1].querySelector(".etsy-picked-image-status")).toBeNull();
    expect(cards.every(card => card.querySelectorAll("small").length === 1)).toBe(true);
    expect(cards[1].getAttribute("title")).toContain("URL ảnh: 0 thiếu · 0 đủ");
    extractionPilotOn = true;
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-refresh")?.click();
      await new Promise(resolve => setTimeout(resolve, 65));
    });
    const pickedCard = container?.querySelector<HTMLButtonElement>(".etsy-stat-picked");
    expect(pickedCard?.querySelector("small")?.textContent).toBe("Extract 0/1 · Q 246");
    expect(pickedCard?.querySelectorAll("small")).toHaveLength(1);
    expect(pickedCard?.getAttribute("title")).toContain("PNG 0 · AI 0");
    expect(pickedCard?.getAttribute("title")).toContain("URL ảnh: 0 thiếu · 0 đủ");
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
    expect(container?.querySelector(".etsy-agents")).toBeNull();
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

  it("shows only keywords and two-mode priority toggles while preserving the API contract", async () => {
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
    expect(container?.querySelector('input[type="number"]')).toBeNull();
    expect(container?.querySelector(".etsy-parent-priority-field")).toBeNull();
    expect(container?.textContent).not.toContain("Scout record(s)");
    expect(container?.textContent).not.toContain("2 priority modes");
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    await act(async () => {
      setter.call(input, "  Halloween   ghost shirt ");
      input.dispatchEvent(new Event("input", {bubbles:true}));
    });
    await act(async () => {
      container?.querySelector<HTMLFormElement>(".etsy-parent-create")?.dispatchEvent(
        new Event("submit", {bubbles:true,cancelable:true}));
      await new Promise(resolve => setTimeout(resolve, 35));
    });
    expect(parentKeywords.find(k => k.keywordKey === "halloween ghost shirt")?.priority).toBe(50);
    expect(container?.textContent).toContain("Halloween ghost shirt");
    expect(container?.querySelector('input[type="number"]')).toBeNull();
    expect(container?.querySelector(".etsy-parent-priority-field")).toBeNull();
    expect(parentKeywords[0].keywordKey).toBe("a root seed");
    expect(parentKeywords[0].priority).toBe(50);
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
    expect(parentKeywords.find(row => row.keywordKey === "a root seed")?.priority).toBe(50);
    expect(withPriority.getAttribute("aria-pressed")).toBe("true");
    expect(observed.some(url => url.endsWith("/parent-keywords"))).toBe(true);
    expect(container?.querySelectorAll(".etsy-parent-row")).toHaveLength(2);
    expect(container?.querySelectorAll(".etsy-tree-row")).toHaveLength(0);
    expect(container?.textContent).not.toContain("Windows Agent will apply this priority");
  });

  it("keeps compact tabs keyboard-navigable with one active tab", async () => {
    await start();
    const headerShell = container?.querySelector(".etsy-header-shell");
    expect(headerShell?.querySelector("header")).toBeTruthy();
    expect(headerShell?.querySelector(".etsy-stage-nav")).toBeTruthy();
    expect(headerShell?.querySelectorAll('[role="tab"]')).toHaveLength(4);
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

  it("selects multiple Eligible listings and submits one bulk Pick request", async () => {
    vi.mocked(fetchAccessIdentity).mockResolvedValue({
      permissions: ["etsy_spy.read", "etsy_spy.manage"],
    } as never);
    await start();
    const all = container?.querySelector<HTMLInputElement>(
      'input[aria-label="Chọn tất cả sản phẩm trên trang hiện tại"]');
    expect(all).toBeTruthy();
    expect(all?.checked).toBe(false);
    const rows = container?.querySelectorAll(".etsy-product-row");
    expect(rows).toHaveLength(3);
    const first = rows?.[0].querySelector<HTMLInputElement>(".etsy-row-checkbox");
    expect(first?.getAttribute("aria-label")).toContain("Rudolph Shirt");
    await act(async () => { first?.click(); });
    expect(container?.querySelector(".etsy-bulk-indicator strong")?.textContent).toBe("1 đã chọn");
    expect(all?.indeterminate).toBe(true);
    expect(rows?.[0].classList.contains("is-selected")).toBe(true);
    await act(async () => { all?.click(); });
    expect(container?.querySelector(".etsy-bulk-indicator strong")?.textContent).toBe("3 đã chọn");
    expect(all?.checked).toBe(true);
    expect(all?.indeterminate).toBe(false);
    const pick = container?.querySelector<HTMLButtonElement>(".etsy-bulk-actions .etsy-primary");
    expect(pick?.textContent).toContain("Pick (3)");
    await act(async () => {pick?.click(); await new Promise(resolve => setTimeout(resolve, 100));});
    expect(pickedKeys.size).toBe(3);
    expect(container?.querySelectorAll(".etsy-product-row")).toHaveLength(0);
    expect(observed.filter(x => x.includes("/listings/bulk"))).toHaveLength(1);
    expect(container?.querySelector(".etsy-bulk-feedback")?.textContent).toContain("3/3");
    expect(container?.querySelector(".etsy-stat-picked strong")?.textContent).toBe("3");
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-picked")?.click();
      await new Promise(resolve => setTimeout(resolve, 75));
    });
    expect(container?.querySelectorAll(".etsy-product-row")).toHaveLength(3);
    // Picked is not an Eligible multi-select surface; no accidental bulk re-Pick.
    expect(container?.querySelector(".etsy-bulk-actions")).toBeNull();
  });

  it("queues only eligible selected Picked rows and never dispatches completed drafts", async () => {
    vi.mocked(fetchAccessIdentity).mockResolvedValue({
      permissions: ["etsy_spy.read", "etsy_spy.manage"],
    } as never);
    await start({tiktokEnabled:true});
    pickedKeys = new Set(["123456789", "111", "222"]);
    tiktokStatuses = {"123456789":"needs_fact_check", "111":"not_queued", "222":"failed"};
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-picked")?.click();
      await new Promise(resolve => setTimeout(resolve, 100));
    });
    expect(container?.querySelectorAll(".etsy-product-row")).toHaveLength(3);
    const boxes = container?.querySelectorAll<HTMLInputElement>(".etsy-row-checkbox");
    expect(boxes).toHaveLength(3);
    expect(boxes?.[0].disabled).toBe(true);
    expect(boxes?.[1].disabled).toBe(false);
    expect(boxes?.[2].disabled).toBe(false);
    await act(async () => {
      container?.querySelector<HTMLInputElement>(
        'input[aria-label="Chọn tối đa 5 Picked để tạo TikTok SEO"]')?.click();
    });
    expect(container?.querySelector(".etsy-bulk-indicator strong")?.textContent).toBe("2 đã chọn");
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-bulk-actions .etsy-primary")?.click();
      await new Promise(resolve => setTimeout(resolve, 120));
    });
    expect(tiktokJobs).toEqual(["111", "222"]);
    expect(container?.querySelector(".etsy-bulk-feedback")?.textContent).toContain("2/2");
    expect(container?.querySelector(".etsy-bulk-actions")).toBeNull();
    expect(container?.querySelectorAll(".etsy-product-row")).toHaveLength(3);
    expect(container?.querySelector(".etsy-bulk-actions .etsy-bulk-later")).toBeNull();
  });

  it("bulk Later saves selected seeds, clears selection and respects view changes", async () => {
    vi.mocked(fetchAccessIdentity).mockResolvedValue({
      permissions: ["etsy_spy.read", "etsy_spy.manage"],
    } as never);
    await start();
    const checkboxes = container?.querySelectorAll<HTMLInputElement>(".etsy-row-checkbox");
    await act(async () => { checkboxes?.[1].click(); checkboxes?.[2].click(); });
    expect(container?.querySelector(".etsy-bulk-indicator strong")?.textContent).toBe("2 đã chọn");
    const later = container?.querySelector<HTMLButtonElement>(".etsy-bulk-actions .etsy-bulk-later");
    expect(later?.textContent).toContain("Later (2)");
    await act(async () => { later?.click(); await new Promise(resolve => setTimeout(resolve, 85)); });
    expect(laterSeedKeys.size).toBe(2);
    expect(pickedKeys.size).toBe(0);
    expect(container?.querySelectorAll(".etsy-product-row")).toHaveLength(1);
    expect(container?.querySelector(".etsy-bulk-feedback")?.textContent).toContain("2/2");
    expect(container?.querySelector(".etsy-bulk-indicator strong")?.textContent).toBe("Chọn sản phẩm");
    const only = container?.querySelector<HTMLInputElement>(".etsy-row-checkbox");
    await act(async () => { only?.click(); });
    expect(container?.querySelector(".etsy-bulk-indicator strong")?.textContent).toBe("1 đã chọn");
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-later")?.click();
      await new Promise(resolve => setTimeout(resolve, 80));
    });
    expect(container?.querySelector(".etsy-bulk-actions")).toBeNull();
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-total")?.click();
      await new Promise(resolve => setTimeout(resolve, 80));
    });
    expect(container?.querySelector(".etsy-bulk-indicator strong")?.textContent).toBe("Chọn sản phẩm");
  });

  it("uses one lightweight active Picked poll without refreshing the whole dashboard", async () => {
    await start({tiktokEnabled: true});
    pickedKeys.add(listing.listingKey);
    tiktokStatuses[listing.listingKey] = "processing";
    const intervalSpy = vi.spyOn(window, "setInterval");
    const timeoutSpy = vi.spyOn(window, "setTimeout");
    try {
      await act(async () => {
        container?.querySelector<HTMLButtonElement>(".etsy-stat-picked")?.click();
        await new Promise(resolve => setTimeout(resolve, 85));
      });
      const activeTick = intervalSpy.mock.calls.find(([, delay]) => delay === 15_000)?.[0];
      expect(typeof activeTick).toBe("function");
      // There must no longer be a broad setRefresh() timer every 15 seconds.
      expect(timeoutSpy.mock.calls.some(([, delay]) => delay === 15_000)).toBe(false);
      const summaryRequests = observed.filter(url => url.endsWith("/summary")).length;
      const listingRequests = observed.filter(url => url.includes("/listings?")).length;
      if (typeof activeTick === "function") {
        await act(async () => { await activeTick(); });
      }
      expect(observed.filter(url => url.endsWith("/summary"))).toHaveLength(summaryRequests);
      expect(observed.filter(url => url.includes("/listings?"))).toHaveLength(listingRequests + 1);
    } finally {
      intervalSpy.mockRestore();
      timeoutSpy.mockRestore();
    }
  });

  it("keeps an open TikTok SEO modal and table scroll during a slow refresh or Scout metadata update", async () => {
    await start({tiktokEnabled: true});
    pickedKeys.add(listing.listingKey);
    tiktokStatuses[listing.listingKey] = "pending";
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-picked")?.click();
      await new Promise(resolve => setTimeout(resolve, 90));
    });
    const trigger = container?.querySelector<HTMLButtonElement>(".etsy-tiktok-listing-open");
    expect(trigger).not.toBeNull();
    await act(async () => {
      trigger?.click();
      await new Promise(resolve => setTimeout(resolve, 35));
    });
    const dialog = document.body.querySelector<HTMLElement>(".etsy-tiktok-dialog");
    const table = container?.querySelector<HTMLElement>(".etsy-table-wrap");
    expect(dialog).not.toBeNull();
    expect(table).not.toBeNull();
    if (table) table.scrollTop = 140;

    const sourceFetch = globalThis.fetch;
    const oldKeyword = listing.keyword;
    const oldSession = listing.sessionId;
    let release: (() => void) | undefined;
    let delayNextTableFetch = true;
    let failNextTableFetch = false;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (failNextTableFetch && String(input).includes("/listings?")) {
        failNextTableFetch = false;
        return new Response("backend temporarily unavailable", { status: 503 });
      }
      if (delayNextTableFetch && String(input).includes("/listings?")) {
        delayNextTableFetch = false;
        await new Promise<void>(resolve => { release = resolve; });
      }
      return sourceFetch(input, init);
    }));
    try {
      // Scout may change keyword and session metadata for the SAME listing ID.
      listing.keyword = "new synced keyword";
      listing.sessionId = "fresh scout session";
      await act(async () => {
        container?.querySelector<HTMLButtonElement>(".etsy-refresh")?.click();
        await new Promise(resolve => setTimeout(resolve, 20));
      });
      expect(container?.querySelector(".etsy-skeleton")).toBeNull();
      expect(container?.querySelector(".etsy-table-wrap")).toBe(table);
      expect(document.body.querySelector(".etsy-tiktok-dialog")).toBe(dialog);
      await act(async () => {
        release?.();
        await new Promise(resolve => setTimeout(resolve, 75));
      });
      expect(document.body.querySelector(".etsy-tiktok-dialog")).toBe(dialog);
      expect(container?.querySelector(".etsy-table-wrap")).toBe(table);
      expect(table?.scrollTop).toBe(140);

      // A failed update must not replace the table with Loading or close
      // the modal; the next refresh will retry without losing local state.
      failNextTableFetch = true;
      await act(async () => {
        container?.querySelector<HTMLButtonElement>(".etsy-refresh")?.click();
        await new Promise(resolve => setTimeout(resolve, 70));
      });
      expect(document.body.querySelector(".etsy-tiktok-dialog")).toBe(dialog);
      expect(container?.querySelector(".etsy-table-wrap")).toBe(table);
      expect(container?.querySelector(".etsy-skeleton")).toBeNull();
    } finally {
      release?.();
      listing.keyword = oldKeyword;
      listing.sessionId = oldSession;
    }
  });

  it("preserves the Design gallery across a background Picked table refresh", async () => {
    await start();
    pickedKeys.add(listing.listingKey);
    const job = "00000000-1111-4222-8333-444444444444";
    designProjection = [{
      id: "design-a", job_id: job, listing_key: listing.listingKey,
      position: "front", variant: 1, method: "chatgpt", status: "needs_review",
      source: "mapped", output_sha256: "b".repeat(64),
      thumbnail_url: "/api/v1/etsy-spy/image-agents/designs/" + job + "/front/thumbnail",
      original_url: "/api/v1/etsy-spy/image-agents/designs/" + job + "/front/original",
      print_ready: false, rights_status: "unverified", manual_qa_required: true,
    }];
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-picked")?.click();
      await new Promise(resolve => setTimeout(resolve, 75));
    });
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-design-thumb")?.click();
    });
    const gallery = document.body.querySelector(".etsy-design-gallery");
    expect(gallery).not.toBeNull();
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-refresh")?.click();
      await new Promise(resolve => setTimeout(resolve, 80));
    });
    expect(document.body.querySelector(".etsy-design-gallery")).toBe(gallery);
  });

  it("shows multiple mapped Design PNGs per Picked product and a position gallery", async () => {
    await start();
    pickedKeys.add(listing.listingKey);
    const job = "00000000-1111-4222-8333-444444444444";
    designProjection = ["front", "back", "left_sleeve", "right_sleeve"].map((position, index) => ({
      id: "d".repeat(23) + index, job_id: job, listing_key: listing.listingKey,
      position, variant: 1, method: index ? "local_layered" : "fast_cpu",
      status: "needs_review", source: "mapped", output_sha256: "b".repeat(64),
      thumbnail_url: "/api/v1/etsy-spy/image-agents/designs/" + job +
        "/mock-" + index + "/thumbnail",
      original_url: "/api/v1/etsy-spy/image-agents/designs/" + job +
        "/mock-" + index + "/original",
      print_ready: false, rights_status: "unverified", manual_qa_required: true,
    }));
    extractionProjection = {job_id: job, status: "needs_ai",
      rights_status: "unverified", print_ready: false};
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-picked")?.click();
      await new Promise(resolve => setTimeout(resolve, 75));
    });
    expect(Array.from(container?.querySelectorAll(".etsy-data-table th") || [])
      .map(x => x.textContent)).toContain("Design");
    const cell = container?.querySelector<HTMLElement>(".etsy-design-cell");
    expect(cell?.querySelectorAll(".etsy-design-thumb")).toHaveLength(3);
    expect(cell?.querySelectorAll("img")).toHaveLength(3);
    expect(cell?.textContent?.trim()).toBe("");
    expect(cell?.querySelector(".etsy-design-open, .etsy-design-more, .etsy-design-review-label"))
      .toBeNull();
    // Same bottom-left body portal as Product / Etsy; mouse hover loads
    // the original PNG while preserving click-to-open multi-position gallery.
    const secondThumb = cell?.querySelectorAll<HTMLButtonElement>(".etsy-design-thumb")[1];
    await act(async () => {
      secondThumb?.dispatchEvent(new MouseEvent("pointerover", {bubbles: true}));
    });
    let hoveredPreview = document.body.querySelector<HTMLElement>(".etsy-design-hover-preview");
    expect(hoveredPreview?.dataset.listingKey).toBe(listing.listingKey);
    expect(hoveredPreview?.classList.contains("etsy-product-hover-preview")).toBe(true);
    expect(hoveredPreview?.querySelector("img")?.getAttribute("src")).toContain("mock-1/original");
    expect(hoveredPreview?.textContent).toContain("Ảnh Design 2/4");
    expect(document.body.querySelectorAll(".etsy-product-hover-preview")).toHaveLength(1);
    await act(async () => {
      hoveredPreview?.querySelector("img")?.dispatchEvent(new Event("error"));
    });
    hoveredPreview = document.body.querySelector<HTMLElement>(".etsy-design-hover-preview");
    expect(hoveredPreview?.querySelector("img")?.getAttribute("src")).toContain("mock-1/thumbnail");
    await act(async () => { window.dispatchEvent(new Event("scroll")); });
    expect(document.body.querySelector(".etsy-design-hover-preview")).toBeNull();

    await act(async () => {
      cell?.querySelectorAll<HTMLButtonElement>(".etsy-design-thumb")[2]?.click();
    });
    const gallery = document.body.querySelector<HTMLElement>(
      '[role="dialog"][aria-label*="Ảnh thiết kế sản phẩm"]');
    expect(gallery).not.toBeNull();
    expect(gallery?.querySelector(".etsy-design-gallery-header h2")?.textContent)
      .toBe("Thiết kế theo vị trí in");
    expect(gallery?.querySelectorAll(".etsy-design-gallery-position-list button"))
      .toHaveLength(4);
    expect(gallery?.querySelectorAll(".etsy-design-gallery-list button"))
      .toHaveLength(1);
    expect(gallery?.querySelector<HTMLImageElement>(".etsy-design-gallery-stage img")
      ?.getAttribute("src")).toContain("mock-2/original");
    await act(async () => {
      gallery?.querySelectorAll<HTMLButtonElement>(
        ".etsy-design-gallery-position-list button",
      )[3]?.click();
    });
    expect(gallery?.querySelector<HTMLImageElement>(".etsy-design-gallery-stage img")
      ?.getAttribute("src")).toContain("mock-3/original");
    expect(gallery?.querySelectorAll(".etsy-design-gallery-position-list button[aria-pressed='true']"))
      .toHaveLength(1);
    expect(gallery?.querySelectorAll(".etsy-design-gallery-list button[aria-pressed='true']"))
      .toHaveLength(1);
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowLeft"}));
    });
    expect(gallery?.querySelector<HTMLImageElement>(".etsy-design-gallery-stage img")
      ?.getAttribute("src")).toContain("mock-2/original");
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape"}));
    });
    expect(document.body.querySelector('[role="dialog"]')).toBeNull();
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-total")?.click();
      await new Promise(resolve => setTimeout(resolve, 50));
    });
    expect(container?.querySelector(".etsy-data-table th")?.textContent).not.toContain("Design");
  });

  it("keeps the Picked Design column blank when ImageGen has not produced a PNG", async () => {
    await start();
    pickedKeys.add(listing.listingKey);
    designProjection = [];
    extractionProjection = {
      job_id: "00000000-1111-4222-8333-444444444444",
      status: "needs_ai",
      rights_status: "unverified", print_ready: false,
    };
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-picked")?.click();
      await new Promise(resolve => setTimeout(resolve, 75));
    });
    const cell = container?.querySelector<HTMLElement>(".etsy-design-cell");
    expect(cell).not.toBeNull();
    expect(cell?.textContent?.trim()).toBe("");
    expect(cell?.querySelector(".etsy-design-empty")).not.toBeNull();
    expect(cell?.querySelector("img, button")).toBeNull();
  });

  it("shows honest QA evidence for picked Fast CPU PNG without print-ready claims", async () => {
    await start();
    pickedKeys.add(listing.listingKey);
    extractionProjection = {
      job_id: "00000000-1111-4222-8333-444444444444",
      status: "needs_review", review_flags: ["upscaled_preview", "low_native_resolution"],
      upscale_factor: 6.23, rights_status: "unverified", print_ready: false,
    };
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-picked")?.click();
      await new Promise(resolve => setTimeout(resolve, 65));
    });
    const warning = container?.querySelector<HTMLElement>(".etsy-extraction-qa");
    expect(warning?.textContent).toContain("Cần QA độ nét");
    expect(warning?.textContent).toContain("Phóng ×6.2");
    expect(warning?.getAttribute("title")).toContain("Thiết kế gốc có độ phân giải thấp");
    expect(warning?.getAttribute("title")).toContain("quyền thương mại chưa xác minh");
    const preview = container?.querySelector<HTMLAnchorElement>(".etsy-extraction-open");
    expect(preview?.getAttribute("href")).toBe(
      "/api/v1/etsy-spy/image-agents/assets/" + extractionProjection.job_id);
    expect(preview?.textContent).toContain("View PNG");

    extractionProjection = {...extractionProjection, status: "needs_ai"};
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-refresh")?.click();
      await new Promise(resolve => setTimeout(resolve, 65));
    });
    expect(container?.querySelector(".etsy-extraction-status")).toBeNull();
    expect(container?.querySelector(".etsy-extraction-qa")).toBeNull();
    expect(container?.querySelector(".etsy-extraction-open")).toBeNull();
    extractionProjection = {
      ...extractionProjection, candidate_ready: true,
      candidate_method: "colour_layer_preview_not_ai",
    };
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-refresh")?.click();
      await new Promise(resolve => setTimeout(resolve, 65));
    });
    expect(container?.querySelector(".etsy-extraction-status")).toBeNull();
    expect(container?.querySelector(".etsy-extraction-qa")?.textContent).toContain("PNG nháp");
    const review = container?.querySelector<HTMLAnchorElement>(".etsy-extraction-candidate");
    expect(review?.getAttribute("href")).toBe(
      "/api/v1/etsy-spy/image-agents/candidates/" + extractionProjection.job_id);
    expect(review?.textContent).toContain("Review draft PNG");
    expect(review?.getAttribute("title")).toContain("not print-ready");
  });

  it("shows SMS confirmation only when the Picked listing has a receipt", async () => {
    await start();
    pickedKeys.add(listing.listingKey);
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-stat-picked")?.click();
      await new Promise(resolve => setTimeout(resolve, 70));
    });
    expect(container?.querySelector(".etsy-sms-push-status")?.textContent)
      .toContain("SMS chưa đồng bộ");
    smsProjection = { status: "pending" };
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-refresh")?.click();
      await new Promise(resolve => setTimeout(resolve, 70));
    });
    expect(container?.querySelector(".etsy-sms-push-status")?.textContent)
      .toContain("Chờ SMS");
    smsProjection = { status: "sent", code: "307", sentAt: "2026-11-10T00:05:48Z" };
    await act(async () => {
      container?.querySelector<HTMLButtonElement>(".etsy-refresh")?.click();
      await new Promise(resolve => setTimeout(resolve, 70));
    });
    const badge = container?.querySelector(".etsy-sms-push-status.status-sent");
    expect(badge?.textContent?.trim()).toBe("SMS");
    expect(badge?.getAttribute("aria-label")).toContain("Đã có trên SMS");
    expect(badge?.getAttribute("title")).toContain("307");
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

  it("hides the four operational panels while keeping the products table and Picked flow", async () => {
    vi.mocked(fetchAccessIdentity).mockResolvedValue({
      permissions: ["etsy_spy.read", "etsy_spy.manage"],
    } as never);
    await start();
    expect(container?.querySelector(".etsy-product-row")).toBeTruthy();
    expect(container?.querySelectorAll(".etsy-extraction-backfill")).toHaveLength(0);
    expect(container?.querySelector('details[aria-label="Windows synchronization agents"]')).toBeNull();
    expect(container?.querySelector('details[aria-label="Windows image extraction agents"]')).toBeNull();
    const picked = container?.querySelector<HTMLButtonElement>(".etsy-stat-picked");
    await act(async () => {
      picked?.click();
      await new Promise(resolve => setTimeout(resolve, 70));
    });
    expect(container?.querySelectorAll(".etsy-extraction-backfill")).toHaveLength(0);
    expect(container?.textContent).not.toContain("Picked → Transparent PNG");
    expect(container?.textContent).not.toContain("TikTok Shop Listing Optimizer");
    expect(container?.textContent).not.toContain("Windows Sync Agent");
    expect(container?.textContent).not.toContain("Windows Image Agent");
    expect(container?.querySelector(".etsy-table-card")).toBeTruthy();
  });

  it("disables Pick for users without manage permission", async () => {
    await start();
    expect(container?.querySelector<HTMLButtonElement>(
      'button[aria-label="Pick Rudolph Shirt"]')?.disabled).toBe(true);
  });
});