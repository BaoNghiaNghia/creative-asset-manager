// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UgcScoutReviewGallery } from "./UgcScoutReviewGallery";
import type { UgcScoutItem } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => { document.body.replaceChildren(); document.body.style.overflow = ""; });

const refs: UgcScoutItem[] = [
  { id: "ref-a", image_url: "https://i.pinimg.com/originals/a.jpg",
    pin_url: "https://www.pinterest.com/pin/111/", score: 0.8, status: "approved" },
  { id: "ref-b", image_url: "https://i.pinimg.com/originals/b.jpg",
    pin_url: "https://www.pinterest.com/pin/222/", score: 0.92, status: "approved" },
  { id: "ref-c", image_url: "https://i.pinimg.com/originals/c.jpg",
    pin_url: "https://www.pinterest.com/pin/333/", score: 0.7, status: "approved" },
];

describe("Slot 6 Review Scout feedback gallery", () => {
  it("marks pinned UGC shots ON matching image cards instead of a detached preview strip", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const onReview = vi.fn(async () => undefined);
    await act(async () => root.render(<UgcScoutReviewGallery items={refs} pinned={{
      "ref-b": { slot: "UGC 2", status: "completed" },
      "ref-c": { slot: "UGC 3", status: "running" },
    }} onReview={onReview} />));
    const tiles = host.querySelectorAll<HTMLButtonElement>(".rrugc-ugc-review-tile");
    expect(tiles).toHaveLength(3);
    expect(tiles[0].querySelector("img")?.getAttribute("src")).toBe("https://i.pinimg.com/236x/a.jpg");
    expect(tiles[0].querySelector(".rrugc-ugc-review-picked")).toBeNull();
    expect(tiles[1].querySelector(".rrugc-ugc-review-picked")?.textContent).toContain("UGC 2 · Done");
    expect(tiles[2].querySelector(".rrugc-ugc-review-picked")?.textContent).toContain("UGC 3 · running");
    await act(async () => tiles[1].click());
    expect(document.querySelector('[role="dialog"][aria-label="Review lifestyle reference 2"]')).not.toBeNull();
    expect(document.querySelector(".rrugc-ugc-review-dialog img")?.getAttribute("src")).toBe(refs[1].image_url);
    expect(document.querySelector(".rrugc-ugc-review-modal-picked")?.textContent).toContain("UGC 2");
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Close reference review"]')!.click());
    expect(document.querySelector(".rrugc-ugc-review-dialog")).toBeNull();
    await act(async () => root.unmount());
  });

  it("supports ✓, ✕, AI and keyboard navigation with persistence callback", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    let current = [...refs];
    const saved: Array<{ id: string; label: string }> = [];
    const onReview = vi.fn(async (item: UgcScoutItem, label: string) => {
      saved.push({ id: item.id, label });
      current = label === "bad" || label === "ai"
        ? current.filter(x => x.id !== item.id)
        : current.map(x => x.id === item.id ? { ...x, manual_label: "good" as const } : x);
    });
    const render = async () => act(async () => root.render(<UgcScoutReviewGallery
      items={current} pinned={{}} onReview={onReview} />));
    await render();
    await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Review lifestyle image 1"]')!.click());
    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })));
    expect(document.querySelector(".rrugc-ugc-review-dialog img")?.getAttribute("src")).toBe(refs[1].image_url);
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Pick good lifestyle reference"]')!.click());
    await render();
    expect(saved.at(-1)).toEqual({ id: "ref-b", label: "good" });
    expect(host.querySelectorAll(".rrugc-ugc-review-preferred")).toHaveLength(1);
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Mark lifestyle reference as AI-generated"]')!.click());
    await render();
    expect(saved.at(-1)).toEqual({ id: "ref-b", label: "ai" });
    expect(host.querySelectorAll(".rrugc-ugc-review-tile")).toHaveLength(2);
    expect(host.textContent).not.toContain("AI-generated");
    expect(document.querySelector(".rrugc-ugc-review-dialog img")?.getAttribute("src")).toBe(refs[2].image_url);
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Reject lifestyle reference"]')!.click());
    await render();
    expect(saved.at(-1)).toEqual({ id: "ref-c", label: "bad" });
    expect(host.querySelectorAll(".rrugc-ugc-review-tile")).toHaveLength(1);
    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(document.querySelector(".rrugc-ugc-review-dialog")).toBeNull();
    await act(async () => root.unmount());
  });

  it("keeps the modal open and reports a failed feedback write without losing the image", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<UgcScoutReviewGallery items={[refs[0]]}
      pinned={{}} onReview={async () => { throw Error("Request failed (HTTP 500)."); }} />));
    await act(async () => host.querySelector("button")!.click());
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Pick good lifestyle reference"]')!.click());
    expect(document.querySelector('[role="alert"]')?.textContent).toContain("HTTP 500");
    expect(document.querySelector(".rrugc-ugc-review-dialog")).not.toBeNull();
    await act(async () => root.unmount());
  });
});
