// @vitest-environment jsdom
import {afterEach, describe, expect, it} from "vitest";
import {act} from "react";
import {createRoot, type Root} from "react-dom/client";
import {DesignCell} from "./DesignCell";
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

    const originalSrc = dialog.querySelector(".etsy-design-gallery-stage img")?.getAttribute("src");
    await act(async () => dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-recommended .etsy-design-gallery-background-pepper",
    )?.click());
    expect(dialog.dataset.previewBackground).toBe("pepper");
    expect(dialog.querySelector(".etsy-design-gallery-stage img")?.getAttribute("src"))
      .toBe(originalSrc);

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

  it("preserves full garment picker and checkerboard without any second image download", async () => {
    const dialog = await render([make("a", ["ivory", "pepper", "bay", "blue-jean"])]);
    const picture = dialog.querySelector<HTMLImageElement>(".etsy-design-gallery-stage img");
    const source = picture?.src;
    await act(async () => dialog.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-all-colors-toggle",
    )?.click());
    expect(dialog.querySelectorAll(".etsy-design-gallery-background-swatch")).toHaveLength(27);
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
    expect(dialog.querySelectorAll(".etsy-design-gallery-background-swatch")).toHaveLength(27);
    expect(dialog.querySelector(".etsy-design-gallery-colormatch-heading")).toBeNull();
  });
});
