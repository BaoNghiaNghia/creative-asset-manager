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
    version: "colormatch-sms-c1717-v9", output_sha256: SHA,
    recommended: [...new Set([...eligible, ...[
      "butter", "white", "ivory", "pepper", "blue-jean", "sage", "bay",
      "chambray", "orchid", "denim", "watermelon", "mustard", "navy",
    ]])].slice(0, 10),
    review_colors: [],
    eligible_colors: eligible,
    unsafe_colors: ["true-navy", "black"],
    method: "design_region_alpha_quality_first",
    readability_check: "region_ink_alpha_composite_v3",
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

  it("applies one shared color across actual placements without a third swatch grid", async () => {
    const front = make("front", "a".repeat(24), ["white", "ivory", "butter"]);
    const alternate = make("front", "c".repeat(24), ["ivory", "pepper"]);
    const sleeve = make("left_sleeve", "b".repeat(24), ["ivory", "white", "pepper"]);
    const dialog = await mount([front, alternate, sleeve]);
    const toggle = dialog.querySelector<HTMLInputElement>(
      ".etsy-design-gallery-product-toggle input",
    )!;
    expect(toggle.disabled).toBe(false);
    await act(async () => toggle.click());
    expect(toggle.checked).toBe(true);
    expect(dialog.querySelectorAll(
      '[role="group"][aria-label="Màu phù hợp với thiết kế"] button',
    )).toHaveLength(2);
    await act(async () => dialog.querySelector<HTMLButtonElement>(
      '.etsy-design-gallery-recommended .etsy-design-gallery-background-white',
    )?.click());
    expect(dialog.dataset.previewBackground).toBe("white");
    await act(async () => dialog.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-position-list button")[1]?.click());
    expect(dialog.dataset.previewBackground).toBe("white");
    await act(async () => dialog.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-position-list button")[0]?.click());
    expect(dialog.dataset.previewBackground).toBe("white");
    expect(dialog.querySelector(".etsy-colormatch-product-scope")).toBeNull();
  });

  it("shows ten automatic suggestions without print-method or feedback UI", async () => {
    const dialog = await mount([make("front", "a".repeat(24), ["white", "ivory"])]);
    expect(dialog.querySelectorAll(".etsy-design-gallery-recommended button")).toHaveLength(2);
    expect(dialog.textContent).toContain("Màu phù hợp nhất");
    expect(dialog.querySelector("#etsy-colormatch-print-method")).toBeNull();
    expect(dialog.querySelector(".etsy-colormatch-print-settings")).toBeNull();
    expect(dialog.querySelector(".etsy-colormatch-feedback")).toBeNull();
    expect(dialog.textContent).not.toContain("Lưu màu đã chọn");
    expect(dialog.textContent).not.toContain("Không phù hợp");
    expect(calls).toHaveLength(0); // Removing the UI also removes feedback fetches.
    await act(async () => dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-background-ivory",
    )?.click());
    expect(dialog.dataset.previewBackground).toBe("ivory");
    expect(calls).toHaveLength(0);
  });

  it("flags low-contrast swatches without warning panel and preserves artwork", async () => {
    const dialog = await mount([make("front", "a".repeat(24), ["white", "ivory"])]);
    expect(dialog.querySelector(".etsy-colormatch-print-settings")).toBeNull();
    const originalSrc = dialog.querySelector<HTMLImageElement>(
      ".etsy-design-gallery-artwork-original")?.getAttribute("src");
    await act(async () => dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-background-true-navy")?.click());
    expect(dialog.querySelector(".etsy-design-gallery-contrast-warning")).toBeNull();
    expect(dialog.textContent).not.toContain("Comfort Colors C1717: 72 màu từ SMS");
    expect(dialog.querySelector<HTMLImageElement>(
      ".etsy-design-gallery-artwork-original")?.getAttribute("src")).toBe(originalSrc);
  });
});