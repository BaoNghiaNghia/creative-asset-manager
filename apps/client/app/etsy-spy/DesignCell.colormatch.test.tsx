// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { DesignCell } from "./DesignCell";
import { C1717_SMS_COLORS } from "./c1717SmsColors";
import type { DesignAsset, Listing } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const SHA = "a".repeat(64);
const OTHER_SHA = "b".repeat(64);
const TOP_TEN = [
  "white", "banana", "butter", "chambray", "ivory",
  "island-reef", "blossom", "hydrangea", "bay", "sandstone",
];

const make = (id: string, colors: string[], version: "colormatch-sms-c1717-v10" | "colormatch-sms-c1717-v9" | "colormatch-sms-c1717-v8" | "colormatch-pixel-v1" = "colormatch-sms-c1717-v10"): DesignAsset => ({
  id, job_id: "color-job", listing_key: "listing-1",
  position: id === "b" ? "left_sleeve" : "front",
  variant: 1, method: "imagegen", status: "needs_review", source: "mapped",
  output_sha256: id === "b" ? OTHER_SHA : SHA,
  thumbnail_url: "/thumb/" + id, original_url: "/original/" + id,
  print_ready: false, rights_status: "unverified", manual_qa_required: true,
  color_match: {
    version, output_sha256: id === "b" ? OTHER_SHA : SHA,
    recommended: colors,
    eligible_colors: colors,
    review_colors: [],
    unsafe_colors: ["black", "true-navy", "neon-pink"],
    method: version === "colormatch-sms-c1717-v10"
      ? "role_aware_salience_ciede2000_v10"
      : version === "colormatch-sms-c1717-v9"
      ? "design_region_alpha_quality_first"
      : version === "colormatch-sms-c1717-v8"
        ? "design_region_alpha_sales_prior_ten" : "thumbnail_pixel_contrast",
  },
});

describe("ColorMatch quality-first two-section palette", () => {
  let host: HTMLDivElement | null = null;
  let root: Root | null = null;

  async function render(assets: DesignAsset[]) {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => root?.render(
      <DesignCell item={{
        listingKey: "listing-1", pageTitle: "Front and sleeve shirt", designs: assets,
      } as Listing} />,
    ));
    await act(async () => host?.querySelector<HTMLButtonElement>(".etsy-design-thumb")?.click());
    return document.body.querySelector<HTMLElement>(".etsy-design-gallery")!;
  }

  afterEach(async () => {
    if (root) await act(async () => root?.unmount());
    host?.remove();
    root = null;
    host = null;
    document.body.style.overflow = "";
    try { window.sessionStorage.clear(); } catch { /* optional in tests */ }
  });

  const buttons = (dialog: HTMLElement, label: string) =>
    Array.from(dialog.querySelectorAll<HTMLButtonElement>(
      `[role="group"][aria-label="${label}"] button`,
    ));

  it("hides the redundant per-position design heading but preserves the thumbnail", async () => {
    const dialog = await render([make("a", TOP_TEN), make("b", TOP_TEN)]);
    expect(dialog.querySelector(".etsy-design-gallery-variants .etsy-design-gallery-section-heading"))
      .toBeNull();
    expect(dialog.textContent).not.toContain("Thiết kế · Tay phải");
    expect(dialog.querySelectorAll(".etsy-design-gallery-position-list button")).toHaveLength(2);
    expect(dialog.querySelectorAll(".etsy-design-gallery-variants .etsy-design-gallery-list button"))
      .toHaveLength(1);
    await act(async () => dialog.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-position-list button",
    )[1]?.click());
    expect(dialog.textContent).not.toContain("Thiết kế · Tay phải");
    expect(dialog.querySelectorAll(".etsy-design-gallery-variants .etsy-design-gallery-list button"))
      .toHaveLength(1);
  });

  it("renders exactly two groups, ten recommended and every remaining color once", async () => {
    const dialog = await render([make("a", TOP_TEN)]);
    const top = buttons(dialog, "Màu phù hợp với thiết kế");
    const rest = buttons(dialog, "Các màu còn lại");
    const configured = C1717_SMS_COLORS.length + 4; // four legacy previews
    expect(top).toHaveLength(10);
    expect(rest).toHaveLength(configured - 10);
    expect(dialog.querySelectorAll(".etsy-design-gallery-background-swatch"))
      .toHaveLength(configured + 1); // checkerboard is separate
    expect(dialog.textContent).toContain("Màu phù hợp nhất");
    expect(dialog.textContent).toContain("Các màu còn lại");
    expect(dialog.querySelector(".etsy-design-gallery-all-colors-toggle")).toBeNull();
    const ids = [...top, ...rest].map(button => button.className);
    expect(new Set(ids).size).toBe(ids.length);
    expect(dialog.dataset.previewBackground).toBe("white");
    expect(buttons(dialog, "Nền trong suốt")).toHaveLength(1);
  });

  it("never pads a limited quality shortlist with unsafe colors", async () => {
    const dialog = await render([make("a", TOP_TEN.slice(0, 6))]);
    expect(buttons(dialog, "Màu phù hợp với thiết kế")).toHaveLength(6);
    expect(buttons(dialog, "Các màu còn lại")).toHaveLength(C1717_SMS_COLORS.length + 4 - 6);
    expect(dialog.querySelector(".etsy-design-gallery-recommended .etsy-design-gallery-background-black"))
      .toBeNull();
    const black = dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-other-colors .etsy-design-gallery-background-black",
    );
    expect(black?.dataset.readabilityRisk).toBe("true");
    expect(black?.title).toContain("Tương phản thấp");
    await act(async () => black?.click());
    expect(dialog.dataset.previewBackground).toBe("black");
    expect(dialog.querySelector(".etsy-design-gallery-contrast-warning")).toBeNull();
  });

  it("shows all available colors below when no background qualifies", async () => {
    const dialog = await render([make("a", [])]);
    expect(buttons(dialog, "Màu phù hợp với thiết kế")).toHaveLength(0);
    expect(buttons(dialog, "Các màu còn lại")).toHaveLength(C1717_SMS_COLORS.length + 4);
    expect(dialog.textContent).toContain("Không có màu đạt mức đề xuất");
    expect(dialog.dataset.previewBackground).toBe("transparent");
  });

  it("keeps per-design manual color choice without reloading original artwork", async () => {
    const dialog = await render([
      make("a", ["white", "ivory", "chambray"]),
      make("b", ["pepper", "denim", "graphite"]),
    ]);
    const original = dialog.querySelector<HTMLImageElement>(
      ".etsy-design-gallery-artwork-original",
    );
    const originalSrc = original?.getAttribute("src");
    await act(async () => dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-other-colors .etsy-design-gallery-background-yam",
    )?.click());
    expect(dialog.dataset.previewBackground).toBe("yam");
    expect(original?.getAttribute("src")).toBe(originalSrc);
    expect(dialog.querySelector(".etsy-design-gallery-artwork-original")).toBe(original);

    await act(async () => dialog.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-position-list button",
    )[1]?.click());
    expect(dialog.dataset.previewBackground).toBe("pepper");
    await act(async () => dialog.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-position-list button",
    )[0]?.click());
    expect(dialog.dataset.previewBackground).toBe("yam");
    const yam = dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-other-colors .etsy-design-gallery-background-yam",
    );
    expect(yam?.getAttribute("aria-pressed")).toBe("true");
    await act(async () => buttons(dialog, "Nền trong suốt")[0]?.click());
    expect(dialog.dataset.previewBackground).toBe("transparent");
  });

  it("shows V10 review candidates in the same upper palette with quiet disclosure", async () => {
    const asset = make("a", ["white", "chambray", "flo-blue"]);
    asset.color_match!.eligible_colors = ["white"];
    asset.color_match!.review_colors = ["chambray", "flo-blue"];
    const dialog = await render([asset]);
    const top = buttons(dialog, "Màu phù hợp với thiết kế");
    expect(top).toHaveLength(3);
    expect(top.filter(button => button.dataset.reviewPreview === "true")).toHaveLength(2);
    expect(dialog.textContent).toContain("2 màu để xem thử");
    expect(dialog.querySelector(".etsy-design-gallery-contrast-warning")).toBeNull();
    expect(dialog.querySelector(".etsy-design-gallery-recommended .etsy-design-gallery-background-flo-blue"))
      .not.toBeNull();
    expect(dialog.querySelector(".etsy-design-gallery-other-colors .etsy-design-gallery-background-flo-blue"))
      .toBeNull();
    expect(top[1].title).toContain("cần kiểm tra chữ");
  });

  it("legacy V8 only surfaces eligible colors, not forced review candidates", async () => {
    const asset = make("a", TOP_TEN, "colormatch-sms-c1717-v8");
    asset.color_match!.recommended = [
      "white", "ivory", "black", "true-navy", "neon-pink",
      "mustard", "red", "grey", "pepper", "denim",
    ];
    asset.color_match!.eligible_colors = ["white", "ivory", "butter", "bay"];
    asset.color_match!.review_colors = ["black", "true-navy", "neon-pink", "mustard", "red", "grey", "pepper", "denim"];
    const dialog = await render([asset]);
    expect(buttons(dialog, "Màu phù hợp với thiết kế")).toHaveLength(4);
    expect(dialog.querySelector(".etsy-design-gallery-recommended .etsy-design-gallery-background-black"))
      .toBeNull();
    expect(dialog.querySelector(".etsy-design-gallery-recommended .etsy-design-gallery-background-butter"))
      .not.toBeNull();
  });

  it("keeps all supplier-only colors and three unknown-HEX swatches in remaining", async () => {
    const dialog = await render([make("a", TOP_TEN)]);
    const remaining = buttons(dialog, "Các màu còn lại");
    const disabled = remaining.filter(button => button.disabled);
    expect(disabled.map(button => button.title).sort()).toEqual([
      "Grass · SMS chưa có HEX", "Mystic · SMS chưa có HEX",
      "Topaz Blue · SMS chưa có HEX",
    ]);
    for (const color of C1717_SMS_COLORS.filter(color => !color.setUp)) {
      expect(remaining.some(button => button.className.includes(
        `etsy-design-gallery-background-${color.id}`,
      ))).toBe(true);
    }
  });

  it("does not treat stale SHA recommendations as valid", async () => {
    const asset = make("a", TOP_TEN);
    asset.color_match!.output_sha256 = OTHER_SHA;
    const dialog = await render([asset]);
    expect(buttons(dialog, "Màu phù hợp với thiết kế")).toHaveLength(0);
    expect(buttons(dialog, "Các màu còn lại")).toHaveLength(C1717_SMS_COLORS.length + 4);
    expect(dialog.textContent).toContain("Chưa có dữ liệu gợi ý");
  });
});
