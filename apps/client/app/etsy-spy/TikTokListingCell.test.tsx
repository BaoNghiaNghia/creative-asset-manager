// @vitest-environment jsdom
import {afterEach, describe, expect, it, vi} from "vitest";
import {act} from "react";
import {createRoot, type Root} from "react-dom/client";
import {canQueueTikTokDraft, formatTikTokDraft, TikTokListingCell} from "./TikTokListingCell";
import type {Listing, TikTokListingDraft} from "./api";

(globalThis as typeof globalThis & {IS_REACT_ACT_ENVIRONMENT: boolean}).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let mount: HTMLElement | null = null;

function product(status: string): Listing {
  return {
    listingKey: "1234567890",
    pageTitle: "Mountain Sunset Sweatshirt",
    picked: true,
    tiktokListing: {
      status, draft_only: true,
      title_preview: status === "needs_fact_check" ? "Mountain Sunset Sweatshirt" : null,
    },
  } as Listing;
}
const draft: TikTokListingDraft = {
  title: "Mountain Sunset Sweatshirt for Outdoor Fans",
  description: "Outdoor art with the mountain at sunset and a retro-inspired color palette. Pick this design for a thoughtful present or casual day. Please compare the selected garment measurements before ordering and follow the product care label for washing.",
  highlights: ["Mountain artwork", "Outdoor gift", "Casual shirt"],
  search_terms: ["mountain sweatshirt", "sunset art"],
  qa_status: "needs_fact_check", keyword_source: "unverified", ytrends_status: "unavailable",
  warnings: ["Confirm garment material before publishing"], published: false,
  metrics: {
    title_characters: 46, description_characters: 230, description_words: 39,
    highlights_count: 3, highlights_characters_total: 45,
    search_terms_count: 2, search_terms_characters_total: 27,
    search_terms_joined_characters: 29,
  },
};
function response(data: unknown) {
  return new Response(JSON.stringify(data), {
    status: 200, headers: {"content-type": "application/json"},
  });
}
async function render(status: string, enabled = true, canManage = true,
  evidence?: TikTokListingDraft["ytrends_evidence"]) {
  mount = document.createElement("div");
  document.body.appendChild(mount);
  root = createRoot(mount);
  const onQueued = vi.fn();
  const clipboardWrite = vi.fn(async (_text: string) => undefined);
  let regenerated = false;
  Object.defineProperty(navigator, "clipboard", {
    configurable: true, value: {writeText: clipboardWrite},
  });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const uri = String(input);
    if (uri.endsWith("/tiktok-listing") && (!init || init.method !== "POST")) {
      return response({
        status: regenerated ? "pending" : status, enabled, draft_only: true,
        ytrends_status: "unavailable",
        can_regenerate: !regenerated && status === "needs_fact_check",
        regeneration_count: regenerated ? 1 : 0,
        previous_draft: regenerated ? draft : undefined,
        previous_job_id: regenerated ? "parent-job-id" : undefined,
        error_code: status === "failed" ? "temporary_model_error" : null,
        result: !regenerated && status === "needs_fact_check" ? {
          ...draft,
          ytrends_status: evidence ? "connected" : "unavailable",
          ytrends_evidence: evidence,
        } : undefined,
      });
    }
    if (uri.endsWith("/tiktok-listing/regenerate") && init?.method === "POST") {
      regenerated = true;
      return response({status: "pending", draft_only: true, regeneration_count: 1});
    }
    if (uri.endsWith("/tiktok-listing/ytrends/refresh") && init?.method === "POST") {
      return response({
        status: "needs_fact_check", enabled: true, draft_only: true,
        result: {
          ...draft, ytrends_status: "connected",
          ytrends_evidence: {
            status: "connected", source: "trends.ytuong.ai", marketplace: "etsy",
            query: "oh holy night", as_of_date: "2026-11-12",
            median_data_age_days: 78, total_listings: 36, total_sellers: 31,
            dataset_note: "Indexed Etsy subset; not TikTok Shop search data.",
          },
        },
      });
    }
    if ((uri.endsWith("/tiktok-listing/queue") || uri.endsWith("/tiktok-listing/retry")) &&
        init?.method === "POST") {
      return response({status: "pending", draft_only: true});
    }
    throw Error("Unexpected request: " + uri);
  });
  vi.stubGlobal("fetch", fetchMock);
  await act(async () => {
    root!.render(<table><tbody><tr><TikTokListingCell item={product(status)}
      canManage={canManage} optimizerEnabled={enabled} onQueued={onQueued}/></tr></tbody></table>);
  });
  return {fetchMock, onQueued, clipboardWrite};
}
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = null; mount?.remove(); mount = null;
  document.body.querySelectorAll(".etsy-tiktok-dialog-backdrop").forEach(n => n.remove());
  document.body.style.overflow = "";
  delete (navigator as unknown as {clipboard?: Clipboard}).clipboard;
  vi.unstubAllGlobals();
});

describe("Picked TikTok Shop listing column", () => {
  it("uses one status preview and keeps all copy fields in an accessible dialog", async () => {
    const {fetchMock, clipboardWrite} = await render("needs_fact_check");
    const open = document.querySelector<HTMLButtonElement>(".etsy-tiktok-listing-open");
    expect(open?.textContent).toContain("Cần duyệt");
    expect(open?.textContent).toContain("Mountain Sunset Sweatshirt");
    expect(document.querySelector(".etsy-tiktok-queue-action")).toBeNull();
    await act(async () => {open?.click();});
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    expect(dialog?.textContent).toContain("Product Title");
    expect(dialog?.textContent).toContain("Product Description");
    expect(dialog?.textContent).toContain("Product Highlights");
    expect(dialog?.textContent).toContain("Search Terms");
    expect(dialog?.textContent).toContain("YTrends");
    expect(dialog?.textContent).toContain("Cần kiểm tra");
    expect(dialog?.textContent).toContain("Confirm garment material");
    expect(dialog?.querySelectorAll(".etsy-tiktok-copy-title button")).toHaveLength(4);
    expect(dialog?.querySelector<HTMLButtonElement>(".etsy-tiktok-dialog-header button"))
      .toBe(document.activeElement);
    const all = dialog?.querySelector<HTMLButtonElement>(".etsy-tiktok-copy-all");
    await act(async () => {all?.click();});
    expect(clipboardWrite).toHaveBeenCalledWith(formatTikTokDraft(draft));
    expect(fetchMock.mock.calls.filter(call => call[1]?.method === "POST")).toHaveLength(0);
    expect(dialog?.textContent).not.toContain("Published");
    await act(async () => {window.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape"}));});
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(open);
  });
  it("shows dated Etsy-only YTrends evidence without TikTok volume claims", async () => {
    await render("needs_fact_check", true, true, {
      status: "connected", source: "trends.ytuong.ai", marketplace: "etsy",
      query: "oh holy night", as_of_date: "2026-11-12",
      median_data_age_days: 78, total_listings: 36, total_sellers: 31,
      competition_level: "medium", opportunity_score: 54.5,
      dataset_note: "Indexed Etsy subset; not TikTok Shop search data.",
    });
    await act(async () => {
      document.querySelector<HTMLButtonElement>(".etsy-tiktok-listing-open")?.click();
    });
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    const research = dialog?.querySelector(".etsy-tiktok-research");
    expect(research?.textContent).toContain("oh holy night");
    expect(research?.textContent).toContain("2026-11-12");
    expect(research?.textContent).toContain("78 ngày");
    expect(research?.textContent).toContain("36 listings");
    expect(research?.textContent).toContain("31 sellers");
    expect(dialog?.textContent).toContain("không phải số liệu TikTok Shop");
    expect(dialog?.textContent).toContain("Có dữ liệu");
  });

  it("refreshes YTrends for an existing draft without Gemini or listing queue", async () => {
    const {fetchMock, onQueued} = await render("needs_fact_check");
    await act(async () => {
      document.querySelector<HTMLButtonElement>(".etsy-tiktok-listing-open")?.click();
    });
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    const refresh = Array.from(dialog?.querySelectorAll<HTMLButtonElement>("button") || [])
      .find(button => button.textContent?.includes("Cập nhật YTrends"));
    expect(refresh).toBeDefined();
    expect(fetchMock.mock.calls.filter(c => c[1]?.method === "POST")).toHaveLength(0);
    await act(async () => {refresh?.click();});
    expect(fetchMock.mock.calls.filter(c =>
      String(c[0]).endsWith("/tiktok-listing/ytrends/refresh") &&
      c[1]?.method === "POST")).toHaveLength(1);
    expect(dialog?.textContent).toContain("36 listings");
    expect(onQueued).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.some(c =>
      String(c[0]).endsWith("/tiktok-listing/queue"))).toBe(false);
  });

  it("re-generates a completed draft with YTrends and preserves the previous copy", async () => {
    const {fetchMock, onQueued} = await render("needs_fact_check");
    await act(async () => {
      document.querySelector<HTMLButtonElement>(".etsy-tiktok-listing-open")?.click();
    });
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    const regeneration = Array.from(dialog?.querySelectorAll<HTMLButtonElement>("button") || [])
      .find(button => button.textContent?.includes("Tạo lại với YTrends"));
    expect(regeneration).toBeDefined();
    expect(fetchMock.mock.calls.filter(c => c[1]?.method === "POST")).toHaveLength(0);
    await act(async () => {regeneration?.click();});
    expect(fetchMock.mock.calls.filter(c =>
      String(c[0]).endsWith("/tiktok-listing/regenerate") &&
      c[1]?.method === "POST")).toHaveLength(1);
    expect(dialog?.textContent).toContain("Đang chờ");
    expect(dialog?.textContent).toContain("Đang xem bản nháp trước");
    expect(dialog?.textContent).toContain(draft.title);
    expect(dialog?.textContent).not.toContain("Tạo lại với YTrends");
    expect(dialog?.textContent).toContain("Phiên bản mới đang chạy YTrends và Gemini");
    expect(onQueued).not.toHaveBeenCalled();
    await act(async () => {
      document.querySelector<HTMLButtonElement>(".etsy-tiktok-dialog-header button")?.click();
    });
    expect(onQueued).toHaveBeenCalledTimes(1);
  });

  it("queues from the Picked row on explicit click without opening the dialog", async () => {
    const {fetchMock, onQueued} = await render("not_queued");
    expect(document.querySelector(".etsy-tiktok-queue-action")?.textContent).toBe("Tạo SEO");
    expect(fetchMock.mock.calls.filter(call => call[1]?.method === "POST")).toHaveLength(0);
    await act(async () => {
      document.querySelector<HTMLButtonElement>(".etsy-tiktok-queue-action")?.click();
    });
    expect(fetchMock.mock.calls.filter(call =>
      String(call[0]).endsWith("/tiktok-listing/queue") &&
        call[1]?.method === "POST")).toHaveLength(1);
    expect(onQueued).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });
  it("offers explicit failed-job retry, preserving the open modal until close", async () => {
    const {fetchMock, onQueued} = await render("failed");
    await act(async () => {
      document.querySelector<HTMLButtonElement>(".etsy-tiktok-listing-open")?.click();
    });
    const retry = Array.from(document.querySelectorAll<HTMLButtonElement>(
      ".etsy-tiktok-dialog-actions button")).find(button =>
      button.textContent?.includes("Retry failed draft"));
    expect(retry).toBeDefined();
    await act(async () => {retry?.click();});
    expect(fetchMock.mock.calls.some(call =>
      String(call[0]).endsWith("/tiktok-listing/retry") &&
        call[1]?.method === "POST")).toBe(true);
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    expect(onQueued).not.toHaveBeenCalled();
    await act(async () => {
      document.querySelector<HTMLButtonElement>(
        ".etsy-tiktok-dialog-header button")?.click();
    });
    expect(onQueued).toHaveBeenCalledTimes(1);
  });
  it("hides all queue actions when the worker is disabled or user is read-only", async () => {
    await render("not_queued", false);
    expect(document.querySelector(".etsy-tiktok-queue-action")).toBeNull();
    await act(async () => {
      document.querySelector<HTMLButtonElement>(".etsy-tiktok-listing-open")?.click();
    });
    expect(document.querySelector('[role="dialog"]')?.textContent).not.toContain("Run listing skill");
  });
  it("permits only never-queued or failed status for paid manual dispatch", () => {
    expect(canQueueTikTokDraft("not_queued")).toBe(true);
    expect(canQueueTikTokDraft("failed")).toBe(true);
    for (const status of ["pending", "retry", "processing", "needs_fact_check", "cancelled"]) {
      expect(canQueueTikTokDraft(status)).toBe(false);
    }
  });
});
