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
    act(() => document.querySelector<HTMLButtonElement>('[aria-label="Đóng video UGC"]')?.click());
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
