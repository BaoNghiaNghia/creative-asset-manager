// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { DesignStageImage } from "./DesignStageImage";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

describe("Design gallery progressive image", () => {
  let host: HTMLDivElement | null = null;
  let root: Root | null = null;

  async function mount() {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => root?.render(
      <DesignStageImage originalUrl="/private/original"
        thumbnailUrl="/private/thumb" alt="Thiết kế mặt trước" />,
    ));
    return host!;
  }

  afterEach(async () => {
    if (root) await act(async () => root?.unmount());
    root = null;
    host?.remove();
    host = null;
    vi.useRealTimers();
  });

  it("uses a compact loading status, then shows cached thumbnail before the full image", async () => {
    const mounted = await mount();
    const gallery = mounted.querySelector<HTMLElement>(".etsy-design-gallery-artwork")!;
    const full = mounted.querySelector<HTMLImageElement>(".etsy-design-gallery-artwork-original")!;
    const thumbnail = mounted.querySelector<HTMLImageElement>(".etsy-design-gallery-artwork-thumb")!;
    expect(full.getAttribute("src")).toBe("/private/original");
    expect(thumbnail.getAttribute("src")).toBe("/private/thumb");
    expect(gallery.dataset.previewQuality).toBe("loading");
    expect(mounted.querySelector(".etsy-image-skeleton")).toBeNull();
    expect(mounted.querySelector(".etsy-design-gallery-image-loading")?.textContent)
      .toContain("Đang mở thiết kế");
    await act(async () => thumbnail.dispatchEvent(new Event("load")));
    expect(gallery.dataset.previewQuality).toBe("thumbnail");
    expect(mounted.querySelector(".etsy-design-gallery-image-loading")).toBeNull();
    expect(mounted.querySelector(".etsy-design-gallery-image-footnote")?.textContent)
      .toContain("Đang tải ảnh chất lượng cao");
    await act(async () => full.dispatchEvent(new Event("load")));
    expect(gallery.dataset.previewQuality).toBe("original");
    expect(mounted.querySelector(".etsy-design-gallery-image-footnote")).toBeNull();
    expect(full.dataset.renderState).toBe("ready");
  });

  it("preserves thumbnail on original error and only retries on operator request", async () => {
    const mounted = await mount();
    const original = mounted.querySelector<HTMLImageElement>(".etsy-design-gallery-artwork-original")!;
    const thumbnail = mounted.querySelector<HTMLImageElement>(".etsy-design-gallery-artwork-thumb")!;
    await act(async () => thumbnail.dispatchEvent(new Event("load")));
    await act(async () => original.dispatchEvent(new Event("error")));
    const gallery = mounted.querySelector<HTMLElement>(".etsy-design-gallery-artwork")!;
    expect(gallery.dataset.previewQuality).toBe("thumbnail");
    expect(mounted.querySelector(".etsy-design-gallery-image-fallback")?.textContent)
      .toContain("Đang hiển thị bản xem trước");
    const retry = mounted.querySelector<HTMLButtonElement>('[aria-label="Thử tải lại ảnh gốc"]')!;
    expect(retry).toBeTruthy();
    await act(async () => retry.click());
    expect(gallery.dataset.previewQuality).toBe("thumbnail");
    expect(mounted.querySelector<HTMLImageElement>(".etsy-design-gallery-artwork-original")
      ?.getAttribute("src")).toBe("/private/original?retry=1");
    expect(mounted.querySelector<HTMLImageElement>(".etsy-design-gallery-artwork-thumb"))
      .toBe(thumbnail);
    expect(mounted.querySelector(".etsy-design-gallery-image-footnote")).not.toBeNull();
  });

  it("does not block the user if both preview URLs fail", async () => {
    const mounted = await mount();
    await act(async () => mounted.querySelector<HTMLImageElement>(
      ".etsy-design-gallery-artwork-thumb",
    )?.dispatchEvent(new Event("error")));
    await act(async () => mounted.querySelector<HTMLImageElement>(
      ".etsy-design-gallery-artwork-original",
    )?.dispatchEvent(new Event("error")));
    expect(mounted.querySelector<HTMLElement>(".etsy-design-gallery-artwork")
      ?.dataset.previewQuality).toBe("error");
    expect(mounted.querySelector(".etsy-design-gallery-image-fallback.is-empty")?.textContent)
      .toContain("Chưa thể tải ảnh thiết kế");
    expect(mounted.querySelector(".etsy-image-skeleton")).toBeNull();
  });

  it("shows slow-load notice without hiding an already visible thumbnail", async () => {
    vi.useFakeTimers();
    const mounted = await mount();
    await act(async () => mounted.querySelector<HTMLImageElement>(
      ".etsy-design-gallery-artwork-thumb",
    )?.dispatchEvent(new Event("load")));
    await act(async () => vi.advanceTimersByTime(12_500));
    expect(mounted.querySelector(".etsy-design-gallery-image-footnote")?.textContent)
      .toContain("vẫn xem được bản thu nhỏ");
    expect(mounted.querySelector<HTMLElement>(".etsy-design-gallery-artwork")
      ?.dataset.previewQuality).toBe("thumbnail");
  });
});
