// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { VideoUGCCell } from "./VideoUGCCell";
import type { Listing } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root;
let container: HTMLDivElement;
function mount(item: Listing, canDelete=false, onRegenerated?: () => void) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  act(() => root.render(<table><tbody><tr><VideoUGCCell item={item} canDelete={canDelete} onRegenerated={onRegenerated}/></tr></tbody></table>));
}
afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  document.querySelectorAll(".etsy-video-ugc-backdrop").forEach(el => el.remove());
  vi.useRealTimers();
  vi.restoreAllMocks();
});
const listing = "4456979256";
const videos = [
  { aspectRatio: "9:16" as const,
    url: "/api/v1/etsy-spy/video-ugc/" + listing + "/vertical",
    durationSeconds: 12, qaStatus: "pending_review" as const },
  { aspectRatio: "16:9" as const,
    url: "/api/v1/etsy-spy/video-ugc/" + listing + "/horizontal",
    durationSeconds: 15, qaStatus: "pending_review" as const },
];
const base = { listingKey: listing, pageTitle: "Comfort Colors Print Tee", picked: true } as Listing;
const withVideos = { ...base, videoUgc: { status: "saved", count: 2, videos } };
const open = () => {
  const trigger = container.querySelector<HTMLButtonElement>(".etsy-video-ugc-badge");
  act(() => trigger?.click());
  return trigger;
};
const activeSource = () => document.querySelector<HTMLVideoElement>(".etsy-video-ugc-player-stage video");
const modal = () => document.querySelector<HTMLElement>(".etsy-video-ugc-modal");

describe("Etsy Spy Video UGC compact thumbnails", () => {
  it("renders a lazy verified poster with center Play and count badge, without text clutter", () => {
    mount({...base, videoUgc:{status:"saved", count:2, videos:[
      {...videos[0], thumbnailUrl:"/api/v1/etsy-spy/video-ugc/4456979256/vertical/poster/320"},
      videos[1],
    ]}});
    const card = container.querySelector<HTMLButtonElement>(".etsy-video-ugc-badge");
    expect(card).toBeTruthy();
    expect(card?.querySelector("img")?.getAttribute("loading")).toBe("lazy");
    expect(card?.querySelector(".etsy-video-ugc-thumb-play")).toBeTruthy();
    expect(card?.querySelector(".etsy-video-ugc-thumb-count")?.textContent).toBe("2");
    expect(card?.textContent).not.toContain("video");
    expect(container.querySelector("video")).toBeNull();
  });
});

describe("Etsy Spy Video UGC poster fallback", () => {
  it("tries the other verified video poster before a clearly marked product fallback", () => {
    mount({...base, thumbnailUrl: "/product-thumb.webp",
      videoUgc:{status:"saved", count:2, videos:videos.map((file,index)=>({
        ...file,thumbnailUrl:"/poster/"+index+"/320",
      }))}});
    const cover = () => container.querySelector(".etsy-video-ugc-cover");
    const image = () => cover()?.querySelector<HTMLImageElement>(".etsy-video-ugc-cover-image");
    const fail = () => act(() => image()?.dispatchEvent(new Event("error")));
    expect(image()?.getAttribute("src")).toBe("/poster/0/320");
    expect(cover()?.querySelector(".etsy-video-ugc-cover-blur")).toBeTruthy();
    fail();
    expect(image()?.getAttribute("src")).toBe("/poster/1/320");
    fail();
    expect(image()?.getAttribute("src")).toBe("/product-thumb.webp");
    expect(cover()?.getAttribute("data-cover-source")).toBe("product");
    expect(cover()?.querySelector(".etsy-video-ugc-cover-fallback")?.textContent).toBe("Ảnh SP");
    expect(container.querySelector(".etsy-video-ugc-thumb-count")?.textContent).toBe("2");
    expect(container.querySelector(".etsy-video-ugc-thumb-play")).toBeTruthy();
    expect(container.querySelector("video")).toBeNull();
    fail();
    expect(cover()?.getAttribute("data-cover-source")).toBe("none");
    expect(image()).toBeFalsy();
  });
  it("does not load MP4 when using a listing image fallback", () => {
    mount({...withVideos,listingImages:[{garmentCode:"C1717",count:1,
      mainThumbnailUrl:"/listing-hero.webp"}]});
    expect(container.querySelector(".etsy-video-ugc-cover-image")?.getAttribute("src")).toBe("/listing-hero.webp");
    expect(container.querySelector(".etsy-video-ugc-cover")?.getAttribute("data-cover-source")).toBe("product");
    expect(container.querySelector("video")).toBeNull();
    open();
    expect(activeSource()?.getAttribute("src")).toBe(videos[0].url);
  });
});

describe("Etsy Spy Video UGC single-player modal", () => {
  it("shows QA-hold as status only, without a video request", () => {
    mount({...base, videoUgc: {status: "qa_hold", count: 0, videos: []}});
    expect(container.textContent).toContain("Chờ QA ảnh");
    expect(container.querySelector(".etsy-video-ugc-placeholder.tone-waiting")).toBeTruthy();
    expect(container.querySelector("video, button")).toBeNull();
  });
  it("requests only one verified MP4 and exposes a real two-version selector", () => {
    mount(withVideos);
    const trigger = open();
    expect(modal()).toBeTruthy();
    expect(modal()?.getAttribute("role")).toBe("dialog");
    expect(modal()?.classList.contains("is-vertical")).toBe(true);
    expect(modal()?.querySelector(".etsy-video-ugc-studio.is-vertical")).toBeTruthy();
    expect(modal()?.querySelector(".etsy-video-ugc-player-stage.is-vertical")).toBeTruthy();
    expect(modal()?.getAttribute("aria-labelledby"))
      .toBe(modal()?.querySelector("h2")?.id);
    expect(modal()?.querySelector(".etsy-video-ugc-poster-action")).toBeNull();
    expect(modal()?.querySelectorAll("video")).toHaveLength(1);
    expect(activeSource()?.getAttribute("src")).toBe(videos[0].url);
    expect(activeSource()?.getAttribute("preload")).toBe("auto");
    expect(activeSource()?.autoplay).toBe(true);
    expect(activeSource()?.muted).toBe(true);
    expect(activeSource()?.playsInline).toBe(true);
    expect(activeSource()?.controls).toBe(true);
    expect(modal()?.querySelectorAll('[role="tab"]')).toHaveLength(2);
    expect(Array.from(modal()?.querySelectorAll('[role="tab"]') ?? [])
      .map(tab => tab.getAttribute("aria-selected"))).toEqual(["true", "false"]);
    expect(modal()?.querySelectorAll(".etsy-video-ugc-version-preview img")).toHaveLength(0);
    expect(modal()?.querySelectorAll('a[target="_blank"]')).toHaveLength(1);
    expect(modal()?.querySelector(".etsy-video-ugc-primary-head, .etsy-video-ugc-qa")).toBeNull();
    expect(modal()?.textContent).not.toContain("Chờ QA nội dung");
    expect(modal()?.querySelector(".etsy-video-ugc-selected-info")).toBeNull();
    expect(document.body.style.overflow).toBe("hidden");
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Đóng video UGC");
    act(() => modal()?.querySelector<HTMLButtonElement>(".etsy-video-ugc-close")?.click());
    expect(modal()).toBeNull();
    expect(activeSource()).toBeNull();
    expect(document.body.style.overflow).not.toBe("hidden");
    expect(document.activeElement).toBe(trigger);
  });
  it("switches from vertical to horizontal without mounting two players", () => {
    mount(withVideos);
    open();
    const tabs = Array.from(document.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
    act(() => tabs[1].click());
    expect(activeSource()?.getAttribute("src")).toBe(videos[1].url);
    expect(activeSource()?.autoplay).toBe(true);
    expect(activeSource()?.muted).toBe(true);
    expect(document.querySelectorAll(".etsy-video-ugc-player-stage video")).toHaveLength(1);
    expect(document.querySelector(".etsy-video-ugc-player-stage.is-horizontal")).toBeTruthy();
    expect(modal()?.classList.contains("is-horizontal")).toBe(true);
    expect(modal()?.classList.contains("is-vertical")).toBe(false);
    expect(modal()?.querySelector(".etsy-video-ugc-studio.is-horizontal")).toBeTruthy();
    expect(tabs.map(tab => tab.getAttribute("aria-selected"))).toEqual(["false", "true"]);
    expect(modal()?.querySelector('a[target="_blank"]')?.getAttribute("href"))
      .toBe(videos[1].url);
    act(() => tabs[0].click());
    expect(modal()?.classList.contains("is-vertical")).toBe(true);
    expect(modal()?.querySelector(".etsy-video-ugc-player-stage.is-vertical")).toBeTruthy();
    expect(activeSource()?.getAttribute("src")).toBe(videos[0].url);
  });
  it("supports keyboard navigation across both video variants", () => {
    mount(withVideos);
    open();
    const tabs = Array.from(document.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
    act(() => tabs[0].dispatchEvent(new KeyboardEvent("keydown", {
      key:"ArrowRight", bubbles:true, cancelable:true,
    })));
    expect(activeSource()?.getAttribute("src")).toBe(videos[1].url);
    expect(modal()?.querySelector(".etsy-video-ugc-player-stage.is-horizontal")).toBeTruthy();
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement?.getAttribute("role")).toBe("tab");
    act(() => tabs[1].dispatchEvent(new KeyboardEvent("keydown", {
      key:"Home", bubbles:true, cancelable:true,
    })));
    expect(activeSource()?.getAttribute("src")).toBe(videos[0].url);
    expect(modal()?.querySelector(".etsy-video-ugc-player-stage.is-vertical")).toBeTruthy();
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
  });
  it("captures a preview only from an actually loaded video frame", () => {
    const originalGetContext = HTMLCanvasElement.prototype.getContext;
    const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      configurable: true, value: vi.fn(() => ({drawImage: vi.fn()})),
    });
    Object.defineProperty(HTMLCanvasElement.prototype, "toDataURL", {
      configurable: true, value: vi.fn(() => "data:image/jpeg;base64,REAL_FRAME"),
    });
    try {
      mount(withVideos);
      open();
      const player = activeSource()!;
      Object.defineProperty(player, "videoWidth", {configurable:true, value:1080});
      Object.defineProperty(player, "videoHeight", {configurable:true, value:1920});
      act(() => player.dispatchEvent(new Event("loadeddata", {bubbles: true})));
      expect(modal()?.querySelectorAll(".etsy-video-ugc-version-preview img")).toHaveLength(1);
      expect(modal()?.querySelector<HTMLImageElement>(".etsy-video-ugc-version-preview img")
        ?.getAttribute("src")).toBe("data:image/jpeg;base64,REAL_FRAME");
      expect(modal()?.querySelector(".etsy-video-ugc-load-status")).toBeNull();
      expect(modal()?.querySelectorAll("video")).toHaveLength(1);
    } finally {
      Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
        configurable:true, value:originalGetContext,
      });
      Object.defineProperty(HTMLCanvasElement.prototype, "toDataURL", {
        configurable:true, value:originalToDataURL,
      });
    }
  });
  it("exposes a retry action after the active MP4 encounters an error", () => {
    mount(withVideos);
    open();
    const first = activeSource();
    act(() => first?.dispatchEvent(new Event("error")));
    expect(document.querySelector('.etsy-video-ugc-error[role="alert"]')).toBeTruthy();
    expect(document.querySelector(".etsy-video-ugc-error")?.textContent).toContain("Thử lại");
    act(() => document.querySelector<HTMLButtonElement>(".etsy-video-ugc-error button")?.click());
    expect(document.querySelector(".etsy-video-ugc-error")).toBeNull();
    expect(activeSource()).not.toBe(first);
    expect(activeSource()?.autoplay).toBe(true);
    expect(activeSource()?.getAttribute("src")).toBe(videos[0].url);
  });
  it("replaces a stuck video spinner with retry after 15 seconds", () => {
    vi.useFakeTimers();
    mount(withVideos);
    open();
    expect(document.querySelector(".etsy-video-ugc-load-status")?.textContent)
      .toContain("Đang chuẩn bị video");
    act(() => vi.advanceTimersByTime(15050));
    expect(document.querySelector(".etsy-video-ugc-load-status")).toBeNull();
    expect(document.querySelector(".etsy-video-ugc-error")?.textContent)
      .toContain("Không thể tải video");
    act(() => document.querySelector<HTMLButtonElement>(".etsy-video-ugc-error button")?.click());
    expect(document.querySelector(".etsy-video-ugc-error")).toBeNull();
    expect(document.querySelector(".etsy-video-ugc-load-status")).toBeTruthy();
  });
  it("waits for playable media instead of treating metadata as a loaded frame", () => {
    mount(withVideos);
    open();
    expect(document.querySelector(".etsy-video-ugc-load-status")).toBeTruthy();
    act(() => activeSource()?.dispatchEvent(new Event("loadedmetadata")));
    expect(document.querySelector(".etsy-video-ugc-load-status")?.textContent)
      .toContain("Đang tải dữ liệu video");
    act(() => activeSource()?.dispatchEvent(new Event("canplay")));
    expect(document.querySelector(".etsy-video-ugc-load-status")).toBeNull();
    expect(document.querySelector(".etsy-video-ugc-player-stage")?.getAttribute("data-player-phase"))
      .toBe("ready");
  });
  it("supports a single verified video without a nonfunctional second selector", () => {
    mount({...base, videoUgc: {status:"partial", count:1, videos:[videos[0]]}});
    open();
    expect(modal()?.querySelectorAll("video")).toHaveLength(1);
    expect(modal()?.querySelectorAll('[role="tab"]')).toHaveLength(0);
    expect(modal()?.querySelector(".etsy-video-ugc-studio.is-single.is-vertical")).toBeTruthy();
    expect(modal()?.classList.contains("is-single")).toBe(true);
    expect(activeSource()?.getAttribute("src")).toBe(videos[0].url);
  });
  it("uses cached WebP thumbnails and loads only the active MP4 when the modal opens", () => {
    const fromCache = videos.map((file, index) => ({
      ...file,
      posterUrl: "/api/v1/etsy-spy/video-ugc/" + listing + "/" +
        (index === 0 ? "vertical" : "horizontal") + "/poster/640",
      thumbnailUrl: "/api/v1/etsy-spy/video-ugc/" + listing + "/" +
        (index === 0 ? "vertical" : "horizontal") + "/poster/320",
    }));
    mount({...base,videoUgc:{status:"saved", count:2, videos:fromCache}});
    expect(document.querySelectorAll("video")).toHaveLength(0);
    open();
    expect(document.querySelectorAll("video")).toHaveLength(1);
    expect(document.querySelectorAll(".etsy-video-ugc-version-preview img")).toHaveLength(2);
    expect(document.querySelector(".etsy-video-ugc-version-preview.is-vertical img")).toBeTruthy();
    expect(document.querySelector(".etsy-video-ugc-version-preview.is-horizontal img")).toBeTruthy();
    expect(activeSource()?.getAttribute("aria-label")).toBe("Phát Video dọc");
    expect(activeSource()?.getAttribute("src")).toBe(fromCache[0].url);
    expect(activeSource()?.getAttribute("poster")).toBe(fromCache[0].posterUrl);
    expect(document.querySelectorAll("video")).toHaveLength(1);
  });
  it("closes by Escape and by backdrop but not by player click", () => {
    mount(withVideos);
    const trigger = open();
    act(() => modal()?.querySelector(".etsy-video-ugc-primary")?.dispatchEvent(
      new MouseEvent("click", {bubbles:true})));
    expect(modal()).toBeTruthy();
    act(() => window.dispatchEvent(new KeyboardEvent("keydown", {
      key:"Escape", bubbles:true,
    })));
    expect(modal()).toBeNull();
    expect(document.activeElement).toBe(trigger);
    act(() => trigger?.click());
    act(() => document.querySelector(".etsy-video-ugc-backdrop")?.dispatchEvent(
      new MouseEvent("click", {bubbles:true})));
    expect(modal()).toBeNull();
  });
  it.each([
    {status:"stalled", label:"Job gián đoạn", tone:"attention"},
    {status:"queued", label:"Đang chờ", tone:"waiting"},
    {status:"not_queued", label:"Chưa tạo", tone:"idle"},
  ])("does not claim a saved MP4 for $status", ({status,label,tone}) => {
    mount({...base,videoUgc:{status,count:0,videos:[]}});
    expect(container.textContent).toContain(label);
    expect(container.querySelector(".etsy-video-ugc-placeholder")?.className)
      .toContain("tone-"+tone);
    expect(container.querySelector("video, .etsy-video-ugc-badge")).toBeNull();
  });
  it("never plays a receipt-only or incomplete record without a verified URL", () => {
    mount({...base,videoUgc:{status:"receipt_only",count:2,videos:[]}});
    expect(container.textContent).toContain("Chờ lưu Drive");
    expect(container.querySelector("video, .etsy-video-ugc-badge")).toBeNull();
  });
});
describe("Operator video deletion and Muse regeneration", () => {
  const verified = videos.map((video,index) => ({...video,sha256:(index ? "b" : "a").repeat(64)}));
  const item = {...withVideos,videoUgc:{status:"saved",count:2,videos:verified}};
  const action = () => modal()?.querySelector<HTMLButtonElement>(".etsy-video-ugc-delete");
  const submit = () => modal()?.querySelector<HTMLButtonElement>(".etsy-video-ugc-delete-submit");
  it("does not offer deletion to read-only users", () => {
    mount(item);
    open();
    expect(action()).toBeNull();
  });
  it("requires explicit confirmation and queues only the selected video", async () => {
    const changed = vi.fn();
    mount(item, true, changed);
    const request = vi.spyOn(globalThis,"fetch").mockResolvedValue(new Response(JSON.stringify({
      ok:true,queued:true,commandId:"job-new",deletedAspectRatios:["9:16"],
      videoUgc:{status:"partial",count:1,videos:[verified[1]],regeneratingAspectRatios:["9:16"]},
    }),{status:202}));
    open();
    act(() => action()?.click());
    expect(request).not.toHaveBeenCalled();
    expect(modal()?.querySelectorAll('input[type="checkbox"]:checked')).toHaveLength(1);
    await act(async () => {submit()?.click();});
    expect(request).toHaveBeenCalledTimes(1);
    const [url,init] = request.mock.calls[0];
    expect(url).toBe("/api/v1/etsy-spy/video-ugc/"+listing+"/regenerate");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({"9:16":"a".repeat(64)});
    expect(changed).toHaveBeenCalledOnce();
    expect(container.querySelector(".etsy-video-ugc-thumb-count")?.textContent).toBe("1");
    expect(modal()?.textContent).toContain("Muse đang chờ tạo lại");
  });
  it("supports selecting both and reports backend failure without hiding either", async () => {
    mount(item,true);
    const request = vi.spyOn(globalThis,"fetch").mockResolvedValue(new Response(JSON.stringify({detail:"Drive unavailable"}),{status:503}));
    open();
    act(() => action()?.click());
    const boxes = Array.from(modal()!.querySelectorAll<HTMLInputElement>('input[type="checkbox"]'));
    act(() => boxes[1].click());
    expect(submit()?.textContent).toContain("(2)");
    await act(async () => {submit()?.click();});
    expect(JSON.parse(String(request.mock.calls[0][1]?.body))).toEqual({"9:16":"a".repeat(64),"16:9":"b".repeat(64)});
    expect(modal()?.querySelector('[role="alert"]')?.textContent).toContain("Drive unavailable");
    expect(container.querySelector(".etsy-video-ugc-thumb-count")?.textContent).toBe("2");
  });
});
