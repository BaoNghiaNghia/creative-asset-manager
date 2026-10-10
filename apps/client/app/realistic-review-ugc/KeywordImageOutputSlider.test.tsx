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
    expect(host.textContent).toContain("8 images");
    expect(host.querySelector(".rrugc-keyword-output-image")?.tagName).toBe("BUTTON");
    expect(host.querySelector(".rrugc-keyword-output-image")?.hasAttribute("href")).toBe(false);
    await act(async () => host.querySelectorAll<HTMLButtonElement>(".rrugc-keyword-output-image")[2].click());
    expect(onOpenVersion).toHaveBeenCalledTimes(1);
    expect(onOpenVersion).toHaveBeenCalledWith(6);
    expect(host.querySelectorAll('img[loading="lazy"]')).toHaveLength(8);
    expect(host.querySelectorAll(".rrugc-keyword-output-nav button")).toHaveLength(2);
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

  it("preserves partial results on failed runs without reloading the Skill", async () => {
    await act(async () => root.render(<KeywordImageOutputSlider row={{ ...row, status: "failed", saved_output_count: 2 }} onOpenVersion={onOpenVersion} />));
    await act(async () => notifyVisibility?.(true));
    expect(host.textContent).toContain("Partial");
    expect(host.querySelector(".rrugc-keyword-output-track")).not.toBeNull();
  });

  it("does not call the output API for unstarted jobs", async () => {
    await act(async () => root.render(<KeywordImageOutputSlider row={{
      ...row, status: "not_run", job_id: null, saved_output_count: 0, output_url: null,
    }} onOpenVersion={onOpenVersion} />));
    expect(listGenerationOutputVersions).not.toHaveBeenCalled();
    expect(host.textContent).toContain("No images yet");
  });
});