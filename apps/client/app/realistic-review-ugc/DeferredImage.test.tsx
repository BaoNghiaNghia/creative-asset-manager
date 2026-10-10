// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DeferredImage } from "./DeferredImage";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => { document.body.replaceChildren(); vi.unstubAllGlobals(); });

describe("Shared Stage 0–5 modal image skeleton / lazy loading", () => {
  it("uses a loading skeleton and native lazy decode until image load fires", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<div role="dialog">
      <DeferredImage src="/api/v1/preview/1" alt="Concept 1" />
    </div>));
    const img = host.querySelector("img")!;
    expect(img.classList.contains("rrugc-deferred-img")).toBe(true);
    expect(img.classList.contains("is-loaded")).toBe(false);
    expect(img.getAttribute("loading")).toBe("lazy");
    expect(img.getAttribute("decoding")).toBe("async");
    await act(async () => img.dispatchEvent(new Event("load")));
    expect(img.classList.contains("is-loaded")).toBe(true);
    await act(async () => root.unmount());
  });

  it("retains decoded images when leaving the modal scroll viewport", async () => {
    let emit: ((isIntersecting: boolean) => void) | null = null;
    vi.stubGlobal("IntersectionObserver", class {
      constructor(callback: IntersectionObserverCallback) {
        emit = (value: boolean) => callback([{ isIntersecting: value } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
      }
      observe() {}
      disconnect() {}
      unobserve() {}
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<div role="dialog">
      <DeferredImage src="/api/v1/preview/stable" alt="Stable" />
    </div>));
    await act(async () => { emit?.(true); });
    const img = host.querySelector("img")!;
    expect(img.getAttribute("src")).toContain("stable");
    await act(async () => img.dispatchEvent(new Event("load")));
    await act(async () => { emit?.(false); });
    expect(img.getAttribute("src")).toContain("stable");
    expect(img.classList.contains("is-loaded")).toBe(true);
    await act(async () => root.unmount());
  });

  it("limits concurrent modal image requests to eight while keeping queued skeletons", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<div role="dialog">
      {Array.from({ length: 12 }, (_, i) =>
        <DeferredImage key={i} src={`/image/${i}.webp`} alt={`Image ${i}`} />)}
    </div>));
    const imgs = [...host.querySelectorAll("img")];
    expect(imgs).toHaveLength(12);
    expect(imgs.filter(img => img.hasAttribute("src"))).toHaveLength(8);
    expect(imgs.filter(img => img.classList.contains("is-loaded"))).toHaveLength(0);
    await act(async () => imgs[0].dispatchEvent(new Event("load")));
    expect(imgs.filter(img => img.hasAttribute("src"))).toHaveLength(9);
    await act(async () => root.unmount());
  });
});
