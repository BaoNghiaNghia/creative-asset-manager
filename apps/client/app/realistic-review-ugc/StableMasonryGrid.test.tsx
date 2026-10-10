// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StableMasonryGrid, stableMasonryRatio } from "./StableMasonryGrid";
import { DeferredImage } from "./DeferredImage";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
beforeEach(() => { vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(960); });
afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });

describe("Stable masonry in Stage 0–5 dialogs", () => {
  const refs = Array.from({ length: 51 }, (_, index) => ({
    id: "ref-" + index, width: index % 3 === 0 ? 700 : 500,
    height: index % 3 === 0 ? 800 : 900,
  }));
  const ratio = (item: typeof refs[number]) => stableMasonryRatio(item.width, item.height);
  const render = (item: typeof refs[number]) => <article data-ref-id={item.id}>
    <div style={{ aspectRatio: ratio(item) }}>
      <DeferredImage src={"/review/" + item.id + ".jpg"} alt={item.id} />
    </div>
  </article>;

  it("keeps card assignment fixed for 51 images while loads and statuses change", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<StableMasonryGrid items={refs} getKey={r => r.id}
      getRatio={ratio} renderItem={render} />));
    const gallery = host.querySelector(".rrugc-stable-masonry")!;
    const columns = [...gallery.querySelectorAll(".rrugc-stable-masonry-column")];
    expect(columns).toHaveLength(4);
    const before = columns.map(column =>
      [...column.querySelectorAll("[data-ref-id]")].map(node => node.getAttribute("data-ref-id")));
    expect(before.flat()).toHaveLength(51);

    const pictures = [...host.querySelectorAll("img")];
    await act(async () => {
      pictures[0].dispatchEvent(new Event("load"));
      pictures[1].dispatchEvent(new Event("error"));
    });
    expect(host.querySelectorAll(".rrugc-deferred-error")).toHaveLength(1);
    const after = [...gallery.querySelectorAll(".rrugc-stable-masonry-column")].map(column =>
      [...column.querySelectorAll("[data-ref-id]")].map(node => node.getAttribute("data-ref-id")));
    expect(after).toEqual(before);
    await act(async () => root.unmount());
  });

  it("keeps earlier card placement when additional references append", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const show = (items: typeof refs) => <StableMasonryGrid items={items}
      getKey={r => r.id} getRatio={ratio} renderItem={render} />;
    await act(async () => root.render(show(refs.slice(0, 25))));
    const columnFor = (id: string) => [...host.querySelectorAll(".rrugc-stable-masonry-column")]
      .findIndex(col => !!col.querySelector('[data-ref-id="' + id + '"]'));
    const indices = refs.slice(0, 25).map(r => columnFor(r.id));
    await act(async () => root.render(show(refs)));
    expect(refs.slice(0, 25).map(r => columnFor(r.id))).toEqual(indices);
    await act(async () => root.unmount());
  });

  it("bounds aspect ratios and preserves a stable fallback for unknown images", () => {
    expect(stableMasonryRatio(900, 300)).toBe(1.65);
    expect(stableMasonryRatio(300, 1200)).toBe(0.6);
    expect(stableMasonryRatio(null, null)).toBe(0.8);
  });
});
