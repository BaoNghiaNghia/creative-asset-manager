// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { Listing } from "./api";
import { EtsySpyQuickInspect } from "./EtsySpyWorkspaceV2";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const base = {
  listingKey: "4456979256", pageTitle: "Dinosaurs Didn't Read Comfort Colors Shirt",
  picked: true, later: false, views24h: null, inCarts: 20, bought24h: null,
  url: "https://www.etsy.com/listing/4456979256", badge: "Bestseller",
  thumbnailUrl: "https://i.etsystatic.com/etsy-original.jpg",
  listingImages: [{garmentCode: "C1717",count: 13,mainThumbnailUrl: "/api/v1/etsy-spy/images/main"}],
  designs: [{thumbnail_url: "https://i.etsystatic.com/design.jpg"}],
  videoUgc: {status:"saved", count:2,videos: []},
  keywordSms: ["Dinosaur Shirt", "Reading Teacher Gift"],
  tiktokListing: {search_terms_preview: ["dinosaur reading shirt","dinosaurs didnt read"]},
} as unknown as Listing;
let root: Root;
let host: HTMLDivElement;
function mount(listing: Listing = base, {canManage = true, busy = false}: {canManage?:boolean;busy?:boolean} = {}) {
  host = document.createElement("div");
  host.className = "etsy-spy-page";
  document.body.append(host);
  root = createRoot(host);
  const onClose = vi.fn();
  const onPicked = vi.fn();
  const onLater = vi.fn();
  act(() => root.render(
    <EtsySpyQuickInspect listing={listing} canManage={canManage} busy={busy}
      onClose={onClose} onPicked={onPicked} onLater={onLater}/>));
  return {onClose,onPicked,onLater};
}
afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  vi.restoreAllMocks();
});
describe("Etsy Spy Quick Inspect compact workspace", () => {
  it("keeps the action dock outside the scroll area and accurately renders product data", () => {
    mount();
    const panel = host.querySelector(".etsy-v2-inspector");
    const scroll = panel?.querySelector(".etsy-v2-inspector-scroll");
    const footer = panel?.querySelector(".etsy-v2-inspector-footer");
    expect(scroll).toBeTruthy();
    expect(footer).toBeTruthy();
    expect(footer?.parentElement).toBe(panel);
    expect(scroll?.contains(footer ?? null)).toBe(false);
    expect(panel?.querySelector(".etsy-v2-inspector-title")?.textContent).toContain("Dinosaurs Didn't Read");
    expect(panel?.querySelector(".etsy-v2-inspector-header small")?.textContent).toContain("4456979256");
    expect(panel?.querySelector(".etsy-v2-inspector-badge.is-bestseller")?.textContent).toBe("Bestseller");
    expect(panel?.querySelector(".etsy-v2-inspector-metrics")?.textContent).toContain("20+");
    expect(panel?.querySelector(".etsy-v2-inspector-medias")?.textContent).toContain("13");
    expect(panel?.querySelector(".etsy-v2-inspector-medias")?.textContent).toContain("2");
    expect(panel?.querySelectorAll(".etsy-v2-inspector-keywords .etsy-v2-inspector-section")).toHaveLength(2);
  });
  it("lets a viewer select original and design previews without mutating image URLs", () => {
    mount();
    const buttons = Array.from(host.querySelectorAll<HTMLButtonElement>(".etsy-v2-inspector-thumbs button"));
    expect(buttons).toHaveLength(3);
    const hero = host.querySelector<HTMLImageElement>(".etsy-v2-inspector-hero img");
    expect(hero?.getAttribute("src")).toBe("/api/v1/etsy-spy/images/main");
    expect(buttons.map(b=>b.getAttribute("aria-pressed"))).toEqual(["true","false","false"]);
    act(() => buttons[1].click());
    expect(hero?.getAttribute("src")).toBe("https://i.etsystatic.com/etsy-original.jpg");
    expect(buttons.map(b=>b.getAttribute("aria-pressed"))).toEqual(["false","true","false"]);
    act(() => buttons[2].click());
    expect(hero?.getAttribute("src")).toBe("https://i.etsystatic.com/design.jpg");
    expect(host.querySelector(".etsy-v2-inspector-hero-label")?.textContent).toBe("Design");
  });
  it("preserves Pick/Later permissions, source link and close interactions", () => {
    const {onClose,onPicked,onLater}=mount();
    const actions=host.querySelector(".etsy-v2-inspector-actions");
    const link=actions?.querySelector("a");
    expect(link?.getAttribute("href")).toContain("etsy.com/listing/");
    expect(link?.getAttribute("rel")).toBe("noopener noreferrer");
    const pick=Array.from(actions?.querySelectorAll("button")??[]).find(b=>b.textContent?.includes("Bỏ Pick"));
    const later=Array.from(actions?.querySelectorAll("button")??[]).find(b=>b.textContent?.trim()==="Later");
    expect(pick?.disabled).toBe(false);
    expect(later?.disabled).toBe(true);
    act(()=>pick?.click());
    expect(onPicked).toHaveBeenCalledWith(base);
    expect(onLater).not.toHaveBeenCalled();
    act(()=>host.querySelector<HTMLButtonElement>('.etsy-v2-inspector-header button')?.click());
    expect(onClose).toHaveBeenCalledOnce();
  });
  it("disables management while busy or without permission; keeps copy and Etsy link",()=>{
    mount(base,{canManage:false,busy:true});
    const buttons=Array.from(host.querySelectorAll<HTMLButtonElement>(".etsy-v2-inspector-actions button"));
    expect(buttons).toHaveLength(3);
    expect(buttons[0].disabled).toBe(false);
    expect(buttons[1].disabled).toBe(true);
    expect(buttons[2].disabled).toBe(true);
    expect(host.querySelector(".etsy-v2-inspector-actions a")).not.toBeNull();
  });
});
