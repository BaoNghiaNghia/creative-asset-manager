// @vitest-environment jsdom
import {afterEach, describe, expect, it} from "vitest";
import {act} from "react";
import {createRoot, type Root} from "react-dom/client";
import {DesignCell} from "./DesignCell";
import {C1717_SMS_COLORS} from "./c1717SmsColors";
import type {DesignAsset, Listing} from "./api";

(globalThis as typeof globalThis & {IS_REACT_ACT_ENVIRONMENT: boolean})
  .IS_REACT_ACT_ENVIRONMENT = true;

const make = (id: string, colors?: string[]): DesignAsset => ({
  id, job_id: "colormatch-job", listing_key: "listing-1",
  position: id === "b" ? "left_sleeve" : "front",
  variant: 1, method: "imagegen", status: "needs_review", source: "mapped",
  output_sha256: id === "a" ? "a".repeat(64) : "b".repeat(64),
  thumbnail_url: "/thumb/" + id, original_url: "/original/" + id,
  print_ready: false, rights_status: "unverified",
  manual_qa_required: true,
  ...(colors ? { color_match: {
    version: "colormatch-pixel-v1" as const,
    output_sha256: id === "a" ? "a".repeat(64) : "b".repeat(64),
    recommended: colors,
    method: "thumbnail_pixel_contrast" as const,
  }} : {}),
});

describe("per-design ColorMatch background recommendations", () => {
  let host: HTMLDivElement | null = null;
  let root: Root | null = null;

  async function render(assets: DesignAsset[]) {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => root?.render(
      <DesignCell item={{
        listingKey: "listing-1", pageTitle: "Two-placement shirt", designs: assets,
      } as Listing} />,
    ));
    await act(async () =>
      host?.querySelector<HTMLButtonElement>(".etsy-design-thumb")?.click(),
    );
    return document.body.querySelector<HTMLElement>(".etsy-design-gallery")!;
  }

  afterEach(async () => {
    if (root) await act(async () => root?.unmount());
    host?.remove();
    root = null;
    host = null;
    document.body.style.overflow = "";
    try { window.sessionStorage.clear(); } catch { /* jsdom opaque origin */ }
  });

  it("uses cached suggestions instantly and keeps manual overrides for each design", async () => {
    const dialog = await render([
      make("a", ["ivory", "pepper", "bay", "blue-jean"]),
      make("b", ["blue-jean", "ivory", "pepper", "mustard"]),
    ]);
    expect(dialog.dataset.previewBackground).toBe("ivory");
    expect(dialog.querySelectorAll(".etsy-design-gallery-recommended button")).toHaveLength(5);
    expect(dialog.querySelector(".etsy-design-gallery-colormatch-heading")?.textContent)
      .toContain("Đề xuất");
    expect(dialog.querySelectorAll(".etsy-design-gallery-background-swatch")).toHaveLength(5);
    expect(dialog.querySelector<HTMLButtonElement>(".etsy-design-gallery-all-colors-toggle")
      ?.getAttribute("aria-expanded")).toBe("false");

    const originalImage = dialog.querySelector<HTMLImageElement>(
      ".etsy-design-gallery-artwork-original",
    );
    const previewImage = dialog.querySelector<HTMLImageElement>(
      ".etsy-design-gallery-artwork-thumb",
    );
    const originalSrc = originalImage?.getAttribute("src");
    await act(async () => previewImage?.dispatchEvent(new Event("load")));
    expect(dialog.querySelector<HTMLElement>(".etsy-design-gallery-artwork")
      ?.dataset.previewQuality).toBe("thumbnail");
    await act(async () => dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-recommended .etsy-design-gallery-background-pepper",
    )?.click());
    expect(dialog.dataset.previewBackground).toBe("pepper");
    expect(dialog.querySelector(".etsy-design-gallery-artwork-original"))
      .toBe(originalImage);
    expect(dialog.querySelector(".etsy-design-gallery-artwork-thumb"))
      .toBe(previewImage);
    expect(originalImage?.getAttribute("src")).toBe(originalSrc);
    expect(dialog.querySelector<HTMLElement>(".etsy-design-gallery-artwork")
      ?.dataset.previewQuality).toBe("thumbnail");

    await act(async () => dialog.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-position-list button",
    )[1]?.click());
    expect(dialog.dataset.previewBackground).toBe("blue-jean");

    await act(async () => dialog.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-position-list button",
    )[0]?.click());
    expect(dialog.dataset.previewBackground).toBe("pepper");

    await act(async () => dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-close",
    )?.click());
    await act(async () =>
      host?.querySelector<HTMLButtonElement>(".etsy-design-thumb")?.click(),
    );
    expect(document.body.querySelector<HTMLElement>(".etsy-design-gallery")
      ?.dataset.previewBackground).toBe("pepper");
  });

  it("renders ten ranked V2 backgrounds in three accessible groups", async () => {
    const colors = [
      "black", "sage", "denim", "crimson", "grey",
      "flo-blue", "watermelon", "terracotta", "bay", "chalky-mint",
    ];
    const updated = make("a", colors);
    updated.color_match!.version = "colormatch-pixel-v2";
    const dialog = await render([updated]);
    expect(dialog.dataset.previewBackground).toBe("black");
    expect(dialog.querySelectorAll(".etsy-design-gallery-recommended button")).toHaveLength(11);
    expect(dialog.querySelectorAll(".etsy-design-gallery-recommendation-group")).toHaveLength(3);
    expect(dialog.querySelector('[aria-label="Phù hợp nhất"]')?.querySelectorAll("button")).toHaveLength(3);
    expect(dialog.querySelector('[aria-label="Gợi ý thêm"]')?.querySelectorAll("button")).toHaveLength(4);
    expect(dialog.querySelector('[aria-label="Lựa chọn khác"]')?.querySelectorAll("button")).toHaveLength(3);
    await act(async () => dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-background-denim",
    )?.click());
    expect(dialog.dataset.previewBackground).toBe("denim");
  });

  it("preserves full garment picker and checkerboard without any second image download", async () => {
    const dialog = await render([make("a", ["ivory", "pepper", "bay", "blue-jean"])]);
    const picture = dialog.querySelector<HTMLImageElement>(".etsy-design-gallery-stage img");
    const source = picture?.src;
    await act(async () => dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-all-colors-toggle",
    )?.click());
    expect(dialog.querySelectorAll(".etsy-design-gallery-background-swatch")).toHaveLength(77);
    const chosen = dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-background-white",
    );
    await act(async () => chosen?.click());
    expect(dialog.dataset.previewBackground).toBe("white");
    expect(picture?.src).toBe(source);
    await act(async () => dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-background-transparent",
    )?.click());
    expect(dialog.dataset.previewBackground).toBe("transparent");
    expect(picture?.src).toBe(source);
  });

  it("falls back to the full legacy palette for absent or stale SHA metadata", async () => {
    const a = make("a", ["ivory"]);
    a.color_match!.output_sha256 = "f".repeat(64);
    const dialog = await render([a]);
    expect(dialog.dataset.previewBackground).toBe("transparent");
    expect(dialog.querySelectorAll(".etsy-design-gallery-background-swatch")).toHaveLength(77);
    expect(dialog.querySelector(".etsy-design-gallery-colormatch-heading")).toBeNull();
  });
  it("recommends 7-10 V5 colors per design and switches ranks when changing artwork", async () => {
    const a = make("a", [
      "pepper", "blue-spruce", "blue-jean", "ivory", "white",
      "light-green", "grey", "true-navy", "denim", "espresso",
    ]);
    const b = make("b", [
      "ivory", "white", "butter", "blossom", "chambray", "bay", "sandstone",
    ]);
    a.color_match!.version = "colormatch-sms-c1717-v5";
    b.color_match!.version = "colormatch-sms-c1717-v5";
    const dialog = await render([a, b]);
    expect(dialog.dataset.previewBackground).toBe("pepper");
    expect(dialog.querySelectorAll(".etsy-design-gallery-recommended button")).toHaveLength(11);
    expect(dialog.querySelector(".etsy-design-gallery-colormatch-heading")?.textContent)
      .toContain("10 màu");
    expect(dialog.querySelector(".etsy-design-gallery-colormatch-heading")?.textContent)
      .toContain("SMS (60 ngày)");

    await act(async () => dialog.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-position-list button",
    )[1]?.click());
    expect(dialog.dataset.previewBackground).toBe("ivory");
    expect(dialog.querySelectorAll(".etsy-design-gallery-recommended button")).toHaveLength(8);
    expect(dialog.querySelector(".etsy-design-gallery-colormatch-heading")?.textContent)
      .toContain("7 màu");

    await act(async () => dialog.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-position-list button",
    )[0]?.click());
    expect(dialog.dataset.previewBackground).toBe("pepper");
    expect(dialog.querySelectorAll(".etsy-design-gallery-recommended button")).toHaveLength(11);
  });

  it("shows ten from legacy V4 and separates supplier-only colorways", async () => {
    const configured = C1717_SMS_COLORS.filter(color => color.setUp && color.color);
    const design = make("a", configured.slice(0, 16).map(color => color.id));
    design.color_match!.version = "colormatch-sms-c1717-v4";
    const dialog = await render([design]);

    expect(dialog.querySelectorAll(".etsy-design-gallery-recommended button")).toHaveLength(11);
    expect(dialog.querySelectorAll(".etsy-design-gallery-recommendation-group")).toHaveLength(3);
    expect(dialog.querySelector('[aria-label="Phù hợp nhất"]')?.querySelectorAll("button")).toHaveLength(3);
    expect(dialog.querySelector('[aria-label="Gợi ý thêm"]')?.querySelectorAll("button")).toHaveLength(4);
    expect(dialog.querySelector('[aria-label="Lựa chọn khác"]')?.querySelectorAll("button")).toHaveLength(3);

    await act(async () => dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-all-colors-toggle",
    )?.click());
    expect(dialog.querySelectorAll(".etsy-design-gallery-background-swatch")).toHaveLength(77);
    const supplierGroup = dialog.querySelector(
      '[role="group"][aria-label="Chỉ có tại supplier · chưa thiết lập SMS"]',
    );
    expect(supplierGroup?.querySelectorAll("button")).toHaveLength(16);
    const unavailable = Array.from(dialog.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-background-swatch:disabled",
    ));
    expect(unavailable.map(button => button.getAttribute("title")).sort())
      .toEqual([
        "Grass · SMS chưa có HEX", "Mystic · SMS chưa có HEX",
        "Topaz Blue · SMS chưa có HEX",
      ]);
  });

  it("V6 rejects dark typography backgrounds yet warns on deliberate manual override", async () => {
    const a = make("a", ["white", "ivory", "butter", "chambray", "ice-blue", "flo-blue", "orchid"]);
    a.color_match!.version = "colormatch-sms-c1717-v6";
    a.color_match!.method = "thumbnail_ink_readability_sales_prior";
    a.color_match!.unsafe_colors = ["black", "true-navy", "pepper"];
    a.color_match!.readability_check = "foreground_ink_connected_components_proxy_v1";
    a.color_match!.insufficient_safe_colors = false;
    const dialog = await render([a]);

    expect(dialog.dataset.previewBackground).toBe("white");
    expect(dialog.querySelector(".etsy-design-gallery-colormatch-heading")?.textContent)
      .toContain("lọc chữ chìm trước");
    expect(dialog.querySelector(".etsy-design-gallery-contrast-warning")).toBeNull();

    await act(async () => dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-all-colors-toggle",
    )?.click());
    const navy = dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-background-true-navy",
    );
    expect(navy?.dataset.readabilityRisk).toBe("true");
    expect(navy?.title).toContain("chữ/chi tiết mực có thể bị chìm");
    await act(async () => navy?.click());
    expect(dialog.dataset.previewBackground).toBe("true-navy");
    expect(dialog.querySelector(".etsy-design-gallery-contrast-warning")?.textContent)
      .toContain("Chữ có thể bị chìm");

    await act(async () => dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-background-white",
    )?.click());
    expect(dialog.dataset.previewBackground).toBe("white");
    expect(dialog.querySelector(".etsy-design-gallery-contrast-warning")).toBeNull();
  });

  it("V6 displays no invented recommendations when all safe backgrounds fail", async () => {
    const a = make("a", []);
    a.color_match!.version = "colormatch-sms-c1717-v6";
    a.color_match!.method = "thumbnail_ink_readability_sales_prior";
    a.color_match!.unsafe_colors = ["black", "true-navy", "pepper"];
    a.color_match!.readability_check = "foreground_ink_connected_components_proxy_v1";
    a.color_match!.insufficient_safe_colors = true;
    const dialog = await render([a]);

    expect(dialog.dataset.previewBackground).toBe("transparent");
    expect(dialog.querySelector(".etsy-design-gallery-colormatch-heading")).toBeNull();
    expect(dialog.textContent).toContain("Không có nền áo đủ an toàn");
    const navy = dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-background-true-navy",
    );
    await act(async () => navy?.click());
    expect(dialog.querySelector(".etsy-design-gallery-contrast-warning")?.textContent)
      .toContain("Chữ có thể bị chìm");
  });

});