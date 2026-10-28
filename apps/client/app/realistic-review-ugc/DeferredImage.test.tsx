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

  it("offers a one-image retry and recovers when media storage reconnects", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<div role="dialog">
      <DeferredImage src="/api/v1/preview/recover" alt="Recovered output" />
    </div>));
    const img = host.querySelector("img")!;
    await act(async () => img.dispatchEvent(new Event("error")));
    const retry = host.querySelector<HTMLButtonElement>(".rrugc-deferred-error.is-action");
    expect(retry?.textContent).toContain("Retry");
    await act(async () => document.dispatchEvent(new Event("rrugc-media-storage-restored")));
    expect(host.querySelector(".rrugc-deferred-error")).toBeNull();
    expect(img.getAttribute("src")).toBe("/api/v1/preview/recover");
    await act(async () => img.dispatchEvent(new Event("load")));
    expect(img.classList.contains("is-loaded")).toBe(true);
    await act(async () => root.unmount());
  });

  it("eagerly grants inline SVG fixtures without waiting for intersection", async () => {
    vi.stubGlobal("IntersectionObserver", class {
      constructor() {}
      observe() {}
      disconnect() {}
      unobserve() {}
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const source = "data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%3E%3C/svg%3E";
    await act(async () => root.render(<DeferredImage src={source} alt="Fixture" />));
    expect(host.querySelector("img")?.getAttribute("src")).toBe(source);
    await act(async () => root.unmount());
  });

  it("never nests a retry button inside an interactive image preview", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<button type="button" aria-label="Open output">
      <DeferredImage src="/api/v1/preview/nested" alt="Output" embeddedInButton />
    </button>));
    const img = host.querySelector("img")!;
    await act(async () => img.dispatchEvent(new Event("error")));
    expect(host.querySelectorAll("button")).toHaveLength(1);
    expect(host.querySelector(".rrugc-deferred-error")?.tagName).toBe("SPAN");
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
