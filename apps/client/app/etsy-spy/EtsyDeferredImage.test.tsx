// @vitest-environment jsdom
import { act } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { EtsyDeferredImage } from "./EtsyDeferredImage";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type FakeEntry = Pick<IntersectionObserverEntry, "target" | "isIntersecting">;
class FakeObserver {
  static instances: FakeObserver[] = [];
  callback: IntersectionObserverCallback;
  targets = new Set<Element>();
  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
    FakeObserver.instances.push(this);
  }
  observe = vi.fn((target: Element) => { this.targets.add(target); });
  unobserve = vi.fn((target: Element) => { this.targets.delete(target); });
  disconnect = vi.fn(() => { this.targets.clear(); });
  emit(target: Element, isIntersecting = true) {
    this.callback([{ target, isIntersecting } as FakeEntry as IntersectionObserverEntry], this as unknown as IntersectionObserver);
  }
}
describe("Etsy deferred thumbnail loading", () => {
  const nativeObserver = globalThis.IntersectionObserver;
  let root: Root | null = null;
  let host: HTMLElement | null = null;
  beforeAll(() => {
    vi.stubGlobal("IntersectionObserver", FakeObserver);
  });
  afterAll(() => {
    if (nativeObserver) vi.stubGlobal("IntersectionObserver", nativeObserver);
    else vi.unstubAllGlobals();
  });
  afterEach(async () => {
    if (root) await act(async () => root?.unmount());
    root = null;
    host?.remove();
    host = null;
    FakeObserver.instances.length = 0;
  });
  async function mount(src: string, eager = false) {
    host = document.createElement("a");
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => {
      root?.render(<EtsyDeferredImage src={src} eager={eager} alt="" width={68} height={68} />);
    });
    return host.querySelector<HTMLImageElement>("img")!;
  }

  it("does not emit src before the thumbnail enters the near viewport", async () => {
    const img = await mount("/img/a");
    expect(img.getAttribute("src")).toBeNull();
    expect(img.dataset.imageState).toBe("deferred");
    expect(host?.querySelector(".etsy-image-skeleton")?.getAttribute("data-busy")).toBe("false");
    const observer = FakeObserver.instances.at(-1)!;
    expect(observer.observe).toHaveBeenCalledWith(img);
    await act(async () => observer.emit(img, false));
    expect(img.getAttribute("src")).toBeNull();
    await act(async () => observer.emit(img));
    expect(img.getAttribute("src")).toBe("/img/a");
    expect(img.getAttribute("loading")).toBe("lazy");
    expect(img.getAttribute("decoding")).toBe("async");
    expect(host?.querySelector(".etsy-image-skeleton")?.getAttribute("data-busy")).toBe("true");
    await act(async () => img.dispatchEvent(new Event("load")));
    expect(host?.querySelector(".etsy-image-skeleton")).toBeNull();
    expect(img.dataset.imageState).toBe("ready");
  });

  it("does not carry a loaded image into the next URL and allows a new lazy request", async () => {
    const img = await mount("/img/a");
    await act(async () => FakeObserver.instances.at(-1)?.emit(img));
    await act(async () => img.dispatchEvent(new Event("load")));
    await act(async () => root?.render(<EtsyDeferredImage src="/img/b" alt="" width={68} height={68} />));
    const changed = host?.querySelector<HTMLImageElement>("img")!;
    expect(changed.getAttribute("src")).toBeNull();
    expect(changed.dataset.imageState).toBe("deferred");
    await act(async () => FakeObserver.instances.at(-1)?.emit(changed));
    expect(changed.getAttribute("src")).toBe("/img/b");
  });

  it("renders the image error marker but leaves the parent clickable", async () => {
    const img = await mount("/thumb/unavailable");
    await act(async () => FakeObserver.instances.at(-1)?.emit(img));
    await act(async () => img.dispatchEvent(new Event("error")));
    expect(img.dataset.imageState).toBe("error");
    expect(img.getAttribute("src")).toBeNull();
    expect(host?.querySelector(".etsy-image-skeleton")).toBeNull();
    expect(host?.querySelector(".etsy-image-error")).not.toBeNull();
    expect(host?.querySelector("a")).toBeNull(); // no nested links
  });

  it("prioritizes an explicitly opened Design image without waiting for IntersectionObserver", async () => {
    const img = await mount("/original/a", true);
    expect(img.getAttribute("src")).toBe("/original/a");
    expect(img.getAttribute("loading")).toBe("eager");
    expect(FakeObserver.instances).toHaveLength(0);
  });

  it("shares one observer across multiple thumbnails instead of one observer per image", async () => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => root?.render(<>
      <EtsyDeferredImage src="/thumb/a" alt="" />
      <EtsyDeferredImage src="/thumb/b" alt="" />
    </>));
    expect(FakeObserver.instances).toHaveLength(1);
    expect(FakeObserver.instances[0].observe).toHaveBeenCalledTimes(2);
    expect(host.querySelectorAll("img[src]")).toHaveLength(0);
  });
});
