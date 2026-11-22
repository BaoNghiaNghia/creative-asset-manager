// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { VideoUGCCell } from "./VideoUGCCell";
import type { Listing } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root;
let container: HTMLDivElement;
function mount(item: Listing) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  act(() => root.render(<table><tbody><tr><VideoUGCCell item={item}/></tr></tbody></table>));
}
afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  document.querySelectorAll(".etsy-video-ugc-backdrop").forEach(el => el.remove());
});
const listing = "4456979256";
const base = { listingKey: listing, pageTitle: "Comfort Colors Print Tee", picked: true } as Listing;

describe("Etsy Spy Video UGC cell", () => {
  it("does not claim a QA-held design has a video", () => {
    mount({ ...base, videoUgc: { status: "qa_hold", count: 0, videos: [] } });
    expect(container.textContent).toContain("Chờ QA ảnh");
    expect(container.querySelector(".etsy-video-ugc-placeholder.tone-waiting")).toBeTruthy();
    expect(container.querySelector(".etsy-video-ugc-placeholder")?.getAttribute("title"))
      .toContain("QA ảnh");
    expect(container.querySelector("video")).toBeNull();
    expect(container.querySelector("button")).toBeNull();
  });
  it("shows two verified CAM video streams in one compact modal", () => {
    const videos = [
      { aspectRatio: "9:16" as const, url: "/api/v1/etsy-spy/video-ugc/" + listing + "/vertical",
        qaStatus: "pending_review" as const },
      { aspectRatio: "16:9" as const, url: "/api/v1/etsy-spy/video-ugc/" + listing + "/horizontal",
        qaStatus: "pending_review" as const },
    ];
    mount({ ...base, videoUgc: { status: "saved", count: 2, videos } });
    const button = container.querySelector<HTMLButtonElement>("button");
    expect(button?.textContent).toContain("2/2 video");
    expect(button?.getAttribute("title")).toContain("Chờ QA nội dung");
    expect(button?.getAttribute("title")).not.toContain("Đạt QA");
    expect(container.querySelector(".etsy-video-ugc-cell")?.getAttribute("data-ugc-state"))
      .toBe("saved");
    act(() => button?.click());
    const modal = document.querySelector(".etsy-video-ugc-modal");
    expect(modal).not.toBeNull();
    expect(modal?.querySelectorAll("video")).toHaveLength(2);
    expect(Array.from(modal?.querySelectorAll("video") ?? []).map(el => el.getAttribute("src")))
      .toEqual(videos.map(v => v.url));
    expect(modal?.textContent).toContain("Chờ QA nội dung");
    expect(modal?.querySelectorAll(".etsy-video-ugc-player")).toHaveLength(2);
    expect(modal?.querySelector(".etsy-video-ugc-players")?.className).not.toContain("is-single");
    expect(modal?.querySelectorAll(".etsy-video-ugc-stage")).toHaveLength(2);
    expect(modal?.querySelector(".etsy-video-ugc-qa.is-pending")?.textContent).toContain("Chờ QA nội dung");
    expect(modal?.querySelectorAll(".etsy-video-ugc-file-status")).toHaveLength(2);
    expect(modal?.querySelectorAll('a[target="_blank"][rel="noopener noreferrer"]')).toHaveLength(2);
    expect(modal?.getAttribute("aria-labelledby")).toBe(
      modal?.querySelector(".etsy-video-ugc-modal-titles h2")?.id);
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Đóng video UGC");
    expect(document.body.style.overflow).toBe("hidden");
    const tabs = Array.from(modal?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? []);
    expect(tabs.map(tab=>tab.getAttribute("aria-selected"))).toEqual(["true", "false"]);
    act(() => tabs[1]?.click());
    expect(tabs.map(tab=>tab.getAttribute("aria-selected"))).toEqual(["false", "true"]);
    expect(modal?.querySelector(".etsy-video-ugc-player.is-active video")?.getAttribute("src"))
      .toBe(videos[1].url);
    act(() => document.querySelector<HTMLButtonElement>('[aria-label="Đóng video UGC"]')?.click());
    expect(document.querySelector(".etsy-video-ugc-modal")).toBeNull();
    expect(document.body.style.overflow).not.toBe("hidden");
    expect(document.activeElement).toBe(button);
  });
  it("closes on Escape and on the backdrop, not when clicking inside the player", () => {
    mount({...base, videoUgc: {status: "saved", count: 1, videos: [
      { aspectRatio: "9:16", url: "/api/v1/etsy-spy/video-ugc/" + listing + "/vertical",
        qaStatus: "pending_review" }
    ]}});
    const button = container.querySelector<HTMLButtonElement>(".etsy-video-ugc-badge");
    act(() => button?.click());
    expect(document.querySelector(".etsy-video-ugc-modal")).not.toBeNull();
    expect(document.querySelector(".etsy-video-ugc-players.is-single")).not.toBeNull();
    expect(document.querySelector(".etsy-video-ugc-mobile-tabs")).toBeNull();
    act(() => {
      document.querySelector(".etsy-video-ugc-player")?.dispatchEvent(
        new MouseEvent("click", {bubbles: true}));
    });
    expect(document.querySelector(".etsy-video-ugc-modal")).not.toBeNull();
    act(() => window.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true})));
    expect(document.querySelector(".etsy-video-ugc-modal")).toBeNull();
    expect(document.activeElement).toBe(button);
    act(() => button?.click());
    act(() => {
      document.querySelector(".etsy-video-ugc-backdrop")?.dispatchEvent(
        new MouseEvent("click", {bubbles: true}));
    });
    expect(document.querySelector(".etsy-video-ugc-modal")).toBeNull();
  });
  it.each([
    {status: "stalled", label: "Job gián đoạn", tone: "attention"},
    {status: "queued", label: "Đang chờ", tone: "waiting"},
    {status: "not_queued", label: "Chưa tạo", tone: "idle"},
  ])("renders $status without claiming any saved video", ({status, label, tone}) => {
    mount({...base, videoUgc: {status,count:0,videos:[]}});
    expect(container.textContent).toContain(label);
    expect(container.querySelector(".etsy-video-ugc-placeholder")?.className).toContain("tone-"+tone);
    expect(container.querySelector(".etsy-video-ugc-badge")).toBeNull();
  });
  it("does not misrepresent a complete job without verified video URLs", () => {
    mount({...base,videoUgc:{status:"completed",count:2,videos:[]}});
    expect(container.textContent).toContain("Chờ đồng bộ");
    expect(container.textContent).not.toContain("2/2 video");
    expect(container.querySelector(".etsy-video-ugc-badge")).toBeNull();
  });
  it("does not play unverified external receipt URLs", () => {
    mount({ ...base, videoUgc: { status: "receipt_only", count: 0, videos: [] } });
    expect(container.textContent).toContain("Chờ lưu Drive");
    expect(container.querySelector("video")).toBeNull();
  });
});
