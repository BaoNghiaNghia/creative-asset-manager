// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { DesignCell } from "./DesignCell";
import type { Listing, DesignAsset } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;
const SHA = "a".repeat(64);
const JOB = "11111111-1111-4111-8111-111111111111";
const make = (position: string, id: string, eligible: string[]): DesignAsset => ({
  position, id, job_id: JOB, listing_key: "123456789",
  output_sha256: SHA, status: "needs_review", source: "mapped",
  method: "imagegen", variant: 1,
  thumbnail_url: "/thumb/" + id, original_url: "/original/" + id,
  print_ready: false, rights_status: "unverified", manual_qa_required: true,
  color_match: {
    version: "colormatch-sms-c1717-v7", output_sha256: SHA,
    recommended: eligible.slice(0, 10),
    eligible_colors: eligible,
    unsafe_colors: ["true-navy", "black"],
    method: "design_region_alpha_sales_prior",
    readability_check: "region_ink_alpha_composite_v2",
    analysis: {
      resolution: [512, 512], source: "medium", regions: [],
      text_regions: 2, microtext_regions: 1,
      faded_ink: true, print_review_required: true,
    },
  },
});

describe("V7 Design ColorMatch production UX", () => {
  let host: HTMLElement | null = null;
  let root: Root | null = null;
  const calls: Array<{ url: string; init: RequestInit }> = [];
  afterEach(async () => {
    if (root) await act(async () => root!.unmount());
    host?.remove();
    host = null;
    root = null;
    calls.splice(0);
    vi.unstubAllGlobals();
  });
  async function mount(assets: DesignAsset[]) {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
      calls.push({url, init});
      return {
        ok: true, status: 200,
        headers: new Headers(),
        json: async () => (init?.method === "POST"
          ? {recorded: true} : {latest: {}, counts: {}}),
      };
    }));
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => root?.render(
      <DesignCell item={{
        listingKey: "123456789", pageTitle: "Multi-position design", designs: assets,
      } as Listing} />,
    ));
    await act(async () => host?.querySelector<HTMLButtonElement>(".etsy-design-thumb")?.click());
    return document.body.querySelector<HTMLElement>(".etsy-design-gallery")!;
  }

  it("applies one safe color across front and sleeve, but not across alternate variants", async () => {
    const front = make("front", "a".repeat(24), ["white", "ivory", "butter"]);
    const alternate = make("front", "c".repeat(24), ["ivory", "pepper"]);
    const sleeve = make("left_sleeve", "b".repeat(24), ["ivory", "white", "pepper"]);
    const dialog = await mount([front, alternate, sleeve]);
    expect(dialog.textContent).toContain("Màu chung cho toàn sản phẩm");
    expect(dialog.querySelectorAll('[aria-label="Màu áo phù hợp cho toàn sản phẩm"] button'))
      .toHaveLength(2);
    await act(async () => dialog.querySelector<HTMLButtonElement>(
      '[aria-label="Màu chung White"]')?.click());
    expect(dialog.dataset.previewBackground).toBe("white");
    await act(async () => dialog.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-position-list button")[1]?.click());
    expect(dialog.dataset.previewBackground).toBe("white");
    await act(async () => dialog.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-position-list button")[0]?.click());
    expect(dialog.dataset.previewBackground).toBe("white");
  });

  it("shows method-specific print advice and only records explicit choices", async () => {
    const dialog = await mount([make("front", "a".repeat(24), ["white", "ivory"])]);
    const selector = dialog.querySelector<HTMLSelectElement>(
      "#etsy-colormatch-print-method",
    )!;
    expect(dialog.textContent).toContain("Chưa xác định phương pháp in");
    expect(calls.filter(x => x.init?.method === "POST")).toHaveLength(0);
    await act(async () => {
      selector.value = "dtg";
      selector.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(dialog.textContent).toContain("DTG: mực lót trắng");
    await act(async () => dialog.querySelectorAll<HTMLButtonElement>(
      ".etsy-colormatch-feedback-actions button")[0]?.click());
    expect(calls.filter(x => x.init?.method === "POST")).toHaveLength(1);
    const body = JSON.parse(calls.find(x => x.init?.method === "POST")!.init.body as string);
    expect(body).toMatchObject({
      action: "selected", color_id: "white",
      output_sha256: SHA, print_method: "dtg", scope: "design",
    });
    expect(dialog.textContent).toContain("Đã lưu màu đã chọn");
    await act(async () => dialog.querySelectorAll<HTMLButtonElement>(
      ".etsy-colormatch-feedback-actions button")[1]?.click());
    expect(calls.filter(x => x.init?.method === "POST")).toHaveLength(2);
    expect(dialog.dataset.previewBackground).toBe("ivory");
    expect(dialog.querySelector('.etsy-design-gallery-recommended .etsy-design-gallery-background-white')).toBeNull();
    expect(dialog.textContent).toContain("đã loại khỏi nhóm tự động gợi ý");
  });

  it("warns about faded source and preserves original artwork for any background", async () => {
    const dialog = await mount([make("front", "a".repeat(24), ["white", "ivory"])]);
    expect(dialog.textContent).toContain("Artwork có nét phai");
    const originalSrc = dialog.querySelector<HTMLImageElement>(
      ".etsy-design-gallery-artwork-original")?.getAttribute("src");
    await act(async () => dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-all-colors-toggle")?.click());
    await act(async () => dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-background-true-navy")?.click());
    expect(dialog.querySelector(".etsy-design-gallery-contrast-warning")?.textContent)
      .toContain("Chữ có thể bị chìm");
    expect(dialog.querySelector<HTMLImageElement>(
      ".etsy-design-gallery-artwork-original")?.getAttribute("src")).toBe(originalSrc);
  });
});
