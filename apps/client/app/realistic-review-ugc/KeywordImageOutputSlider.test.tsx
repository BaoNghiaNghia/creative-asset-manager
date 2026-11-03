// @vitest-environment jsdom
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { KeywordImageOutputSlider } from "./KeywordImageOutputSlider";
import { listGenerationOutputVersions } from "./api";
import type { KeywordImageRow } from "./types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("./api", async importOriginal => ({
  ...(await importOriginal<typeof import("./api")>()),
  listGenerationOutputVersions: vi.fn(async () => ({ stage: "stage1", job_id: "job-1",
    versions: Array.from({ length: 8 }, (_, i) => ({
      version: 8 - i, url: "/api/v1/image/" + (8 - i), width: 512, height: 512,
      created_at: "2026-10-10T00:00:00Z", output_name: "output_" + (8 - i) + ".png",
      selected_for_products_stage2: i === 1,
    })),
  })),
}));

const row: KeywordImageRow = {
  keyword_id: "key-1", keyword: "BEACH PLEASE", search_volume: 10, source_image_url: null,
  status: "completed", job_id: "job-1", skill_name: "redesign",
  skill_version: null, retry_count: 0, saved_output_count: 8,
  attempt_count: 1, max_attempts: 1, error_code: null,
  error_message: null, output_url: "/api/v1/image/latest", updated_at: null,
};

let host: HTMLDivElement;
let root: Root;
let notifyVisibility: ((visible: boolean) => void) | null = null;
const onOpenVersion = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  notifyVisibility = null;
  vi.stubGlobal("IntersectionObserver", class {
    constructor(private readonly callback: IntersectionObserverCallback) {
      notifyVisibility = (visible: boolean) => this.callback(
        [{ isIntersecting: visible } as IntersectionObserverEntry],
        this as unknown as IntersectionObserver,
      );
    }
    observe() {}
    disconnect() {}
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("Stage 1 draggable image strip", () => {
  it("loads only when the row is visible and keeps all images in a four-slot slider", async () => {
    await act(async () => root.render(<KeywordImageOutputSlider row={row} onOpenVersion={onOpenVersion} />));
    expect(listGenerationOutputVersions).not.toHaveBeenCalled();
    await act(async () => notifyVisibility?.(true));
    expect(listGenerationOutputVersions).toHaveBeenCalledWith("stage1", "job-1", expect.anything());
    expect(host.querySelectorAll(".rrugc-keyword-output-image")).toHaveLength(8);
    expect(host.querySelectorAll(".rrugc-keyword-output-track")).toHaveLength(1);
    expect(host.textContent).not.toContain("8 images");
    expect(host.querySelector(".rrugc-keyword-output-footer")).toBeNull();
    expect(host.querySelector(".rrugc-keyword-output-track")?.getAttribute("aria-label")).toContain("8 generated images");
    expect(host.querySelector(".rrugc-keyword-output-image")?.tagName).toBe("BUTTON");
    expect(host.querySelector(".rrugc-keyword-output-image")?.hasAttribute("href")).toBe(false);
    await act(async () => host.querySelectorAll<HTMLButtonElement>(".rrugc-keyword-output-image")[2].click());
    expect(onOpenVersion).toHaveBeenCalledTimes(1);
    expect(onOpenVersion).toHaveBeenCalledWith(6);
    expect(host.querySelectorAll('img[loading="lazy"]')).toHaveLength(8);
    const gallery = host.querySelector(".rrugc-keyword-output-gallery");
    expect(gallery).not.toBeNull();
    const track = host.querySelector<HTMLElement>(".rrugc-keyword-output-track")!;
    Object.defineProperty(track, "clientWidth", { configurable: true, value: 240 });
    Object.defineProperty(track, "scrollWidth", { configurable: true, value: 600 });
    await act(async () => track.dispatchEvent(new Event("scroll", { bubbles: true })));
    expect(Array.from(gallery!.children).map(item => item.classList.contains("rrugc-keyword-output-arrow")
      ? item.classList.contains("is-prev") ? "previous" : "next"
      : "track")).toEqual(["track", "next"]);
    expect(host.querySelectorAll(".rrugc-keyword-output-arrow")).toHaveLength(1);
    expect(host.querySelector(".rrugc-keyword-output-nav")).toBeNull();
  });


  it("defers out-of-view output bytes and reuses the Blueprint WebP thumbnail variant", async () => {
    await act(async () => root.render(<KeywordImageOutputSlider row={row} onOpenVersion={onOpenVersion} />));
    await act(async () => notifyVisibility?.(true));
    const cards = Array.from(host.querySelectorAll<HTMLImageElement>(".rrugc-keyword-output-image img"));
    expect(cards).toHaveLength(8);
    expect(cards[0].getAttribute("src")).toContain("thumbnail=true&size=224&preview_format=webp");
    expect(cards[0].getAttribute("src")).not.toContain("artwork=stage1-source-alpha-v2");
    // All cards are still selectable, but only nearby ones fetch image bytes.
    expect(cards.slice(3).some(img => !img.getAttribute("src"))).toBe(true);
  });

  it("shows gold-picked thumbnails from saved selections and updates just the changed design", async () => {
    await act(async () => root.render(<KeywordImageOutputSlider row={row} onOpenVersion={onOpenVersion} />));
    await act(async () => notifyVisibility?.(true));
    const images = () => host.querySelectorAll<HTMLButtonElement>(".rrugc-keyword-output-image");
    expect(images()[0].classList.contains("is-stage2-picked")).toBe(false);
    expect(images()[1].classList.contains("is-stage2-picked")).toBe(true);
    expect(images()[1].querySelector(".rrugc-keyword-output-picked-mark")?.textContent).toBe("✓");
    expect(images()[1].getAttribute("aria-label")).toContain("Picked for Products Stage 2");
    await act(async () => root.render(<KeywordImageOutputSlider
      row={row} onOpenVersion={onOpenVersion} pickedOverrides={{ 7: false, 6: true }} />));
    expect(images()[1].classList.contains("is-stage2-picked")).toBe(false);
    expect(images()[1].querySelector(".rrugc-keyword-output-picked-mark")).toBeNull();
    expect(images()[2].classList.contains("is-stage2-picked")).toBe(true);
    expect(images()[2].getAttribute("title")).toContain("Picked for Products Stage 2");
    expect(listGenerationOutputVersions).toHaveBeenCalledTimes(1);
  });

  it("removes edge buttons from the DOM so thumbnails reclaim their space", async () => {
    await act(async () => root.render(<KeywordImageOutputSlider row={row} onOpenVersion={onOpenVersion} />));
    await act(async () => notifyVisibility?.(true));
    const track = host.querySelector<HTMLElement>(".rrugc-keyword-output-track")!;
    const arrows = () => ({
      prev: host.querySelector<HTMLButtonElement>(".rrugc-keyword-output-arrow.is-prev"),
      next: host.querySelector<HTMLButtonElement>(".rrugc-keyword-output-arrow.is-next"),
    });
    const childNames = () => Array.from(host.querySelector(".rrugc-keyword-output-gallery")!.children)
      .map(item => item.classList.contains("rrugc-keyword-output-track") ? "track"
        : item.classList.contains("is-prev") ? "previous" : "next");
    Object.defineProperty(track, "clientWidth", { configurable: true, value: 240 });
    Object.defineProperty(track, "scrollWidth", { configurable: true, value: 600 });
    Object.defineProperty(track, "scrollBy", { configurable: true, value: ({ left }: { left: number }) => {
      track.scrollLeft = Math.min(360, Math.max(0, track.scrollLeft + left));
      track.dispatchEvent(new Event("scroll", { bubbles: true }));
    } });
    await act(async () => track.dispatchEvent(new Event("scroll", { bubbles: true })));
    expect(arrows().prev).toBeNull();
    expect(arrows().next).not.toBeNull();
    expect(childNames()).toEqual(["track", "next"]);
    await act(async () => arrows().next!.click());
    expect(track.scrollLeft).toBe(240);
    expect(arrows().prev).not.toBeNull();
    expect(arrows().next).not.toBeNull();
    expect(childNames()).toEqual(["previous", "track", "next"]);
    await act(async () => arrows().next!.click());
    expect(track.scrollLeft).toBe(360);
    expect(arrows().next).toBeNull();
    expect(childNames()).toEqual(["previous", "track"]);
    await act(async () => arrows().prev!.click());
    expect(track.scrollLeft).toBe(120);
    expect(childNames()).toEqual(["previous", "track", "next"]);
    await act(async () => arrows().prev!.click());
    expect(track.scrollLeft).toBe(0);
    expect(childNames()).toEqual(["track", "next"]);
    expect(arrows().prev).toBeNull();
  });

  it("hides both arrows when all images fit the visible output area", async () => {
    await act(async () => root.render(<KeywordImageOutputSlider row={row} onOpenVersion={onOpenVersion} />));
    await act(async () => notifyVisibility?.(true));
    const track = host.querySelector<HTMLElement>(".rrugc-keyword-output-track")!;
    Object.defineProperty(track, "clientWidth", { configurable: true, value: 420 });
    Object.defineProperty(track, "scrollWidth", { configurable: true, value: 420 });
    await act(async () => track.dispatchEvent(new Event("scroll", { bubbles: true })));
    expect(host.querySelector(".rrugc-keyword-output-arrow.is-prev")).toBeNull();
    expect(host.querySelector(".rrugc-keyword-output-arrow.is-next")).toBeNull();
    expect(Array.from(host.querySelector(".rrugc-keyword-output-gallery")!.children))
      .toEqual([track]);
  });

  it("enables the next arrow when wide thumbnails finish loading after initial layout", async () => {
    await act(async () => root.render(<KeywordImageOutputSlider row={row} onOpenVersion={onOpenVersion} />));
    await act(async () => notifyVisibility?.(true));
    const track = host.querySelector<HTMLElement>(".rrugc-keyword-output-track")!;
    Object.defineProperty(track, "clientWidth", { configurable: true, value: 240 });
    let contentWidth = 240;
    Object.defineProperty(track, "scrollWidth", { configurable: true, get: () => contentWidth });
    await act(async () => track.dispatchEvent(new Event("scroll", { bubbles: true })));
    expect(host.querySelector(".rrugc-keyword-output-arrow.is-next")).toBeNull();
    // The image now occupies its natural 16:9 width, increasing scrollWidth.
    contentWidth = 420;
    await act(async () => {
      track.querySelector<HTMLImageElement>("img")!.dispatchEvent(new Event("load", { bubbles: false }));
    });
    expect(host.querySelector<HTMLButtonElement>(".rrugc-keyword-output-arrow.is-next")).not.toBeNull();
  });

  it("supports mouse drag-scrolling without opening thumbnails by mistake", async () => {
    await act(async () => root.render(<KeywordImageOutputSlider row={row} onOpenVersion={onOpenVersion} />));
    await act(async () => notifyVisibility?.(true));
    const track = host.querySelector<HTMLElement>(".rrugc-keyword-output-track")!;
    Object.defineProperty(track, "clientWidth", { configurable: true, value: 240 });
    Object.defineProperty(track, "scrollWidth", { configurable: true, value: 800 });
    Object.defineProperty(track, "hasPointerCapture", { configurable: true, value: () => false });
    Object.defineProperty(track, "setPointerCapture", { configurable: true, value: () => undefined });
    const pointer = (type: string, clientX: number, buttons: number) => {
      const event = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperties(event, {
        pointerType: { value: "mouse" }, pointerId: { value: 1 },
        button: { value: 0 }, buttons: { value: buttons }, clientX: { value: clientX },
      });
      track.dispatchEvent(event);
    };
    await act(async () => {
      pointer("pointerdown", 110, 1);
      pointer("pointermove", 30, 1);
    });
    expect(track.scrollLeft).toBe(80);
    const thumbnail = track.querySelector<HTMLButtonElement>(".rrugc-keyword-output-image")!;
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    await act(async () => thumbnail.dispatchEvent(click));
    expect(click.defaultPrevented).toBe(true);
    expect(onOpenVersion).not.toHaveBeenCalled();
  });

  it("shows a clear tile fallback for 503 images and retries after storage reconnects", async () => {
    await act(async () => root.render(<KeywordImageOutputSlider row={row} onOpenVersion={onOpenVersion} />));
    await act(async () => notifyVisibility?.(true));
    const image = host.querySelector<HTMLImageElement>(".rrugc-keyword-output-image img")!;
    await act(async () => image.dispatchEvent(new Event("error")));
    expect(host.textContent).toContain("Tạm không tải được ảnh");
    expect(host.querySelector<HTMLButtonElement>(".rrugc-keyword-output-retry")).not.toBeNull();
    await act(async () => document.dispatchEvent(new Event("rrugc-media-storage-restored")));
    expect(host.querySelectorAll(".rrugc-output-image-failed")).toHaveLength(0);
    expect(host.querySelector<HTMLImageElement>(".rrugc-keyword-output-image img")?.src).toContain("retry=1");
  });

  it("preserves partial results on failed runs without reloading the Skill", async () => {
    await act(async () => root.render(<KeywordImageOutputSlider row={{ ...row, status: "failed", saved_output_count: 2 }} onOpenVersion={onOpenVersion} />));
    await act(async () => notifyVisibility?.(true));
    expect(host.textContent).not.toContain("Partial");
    expect(host.querySelector(".rrugc-keyword-output-footer")).toBeNull();
    expect(host.querySelector(".rrugc-keyword-output-track")).not.toBeNull();
  });

  it("uses three rotating dots for queued/running output without hiding saved versions", async () => {
    for (const status of ["queued", "running"] as const) {
      await act(async () => root.render(<KeywordImageOutputSlider row={{
        ...row, status, saved_output_count: 0, output_url: null,
      }} onOpenVersion={onOpenVersion} />));
      const spinner = host.querySelector(".rrugc-output-activity");
      expect(spinner).not.toBeNull();
      expect(spinner?.getAttribute("aria-label")).toBe(status === "queued" ? "Output waiting in queue" : "Generating output");
      expect(spinner?.querySelectorAll(".rrugc-output-activity-orbit > span")).toHaveLength(3);
    }
    expect(listGenerationOutputVersions).not.toHaveBeenCalled();
    await act(async () => root.render(<KeywordImageOutputSlider row={{ ...row, status: "running" }} onOpenVersion={onOpenVersion} />));
    await act(async () => notifyVisibility?.(true));
    expect(host.querySelector(".rrugc-keyword-output-track")).not.toBeNull();
    expect(host.querySelector(".rrugc-output-activity.is-compact")).not.toBeNull();
  });

  it("does not call the output API for unstarted jobs", async () => {
    await act(async () => root.render(<KeywordImageOutputSlider row={{
      ...row, status: "not_run", job_id: null, saved_output_count: 0, output_url: null,
    }} onOpenVersion={onOpenVersion} />));
    expect(listGenerationOutputVersions).not.toHaveBeenCalled();
    expect(host.textContent).toContain("No images yet");
  });
});