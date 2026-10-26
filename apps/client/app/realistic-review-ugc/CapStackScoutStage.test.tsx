// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { CapStackScoutStage } from "./CapStackScoutStage";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => { vi.restoreAllMocks(); document.body.replaceChildren(); });

const sample: api.CapStackPage = {
  items: [{
    id: "one", image_url: "data:image/svg+xml,%3Csvg%3E%3C/svg%3E",
    pin_url: "https://www.pinterest.com/pin/123/",
    alt_text: "Stacked caps", source_query: "Cap Stack",
    caps_detected: null, count_status: "pending", count_confidence: null,
    manual_caps_count: null, manual_label: null, created_at: "2026-10-15T02:00:00Z",
  }],
  total: 1, page: 1, page_size: 100,
  stats: { discovered: 1, approved: 0, rejected: 0, ai: 0, pending: 1 },
  scout: { enabled: false, running: false, last_result: null, next_scan_at: null },
};

describe("Cap Stack Scout Stage 1", () => {
  it("loads a shared library with unknown counts, feedback and server enable/disable", async () => {
    vi.spyOn(api, "listCapStackImages").mockResolvedValue(sample);
    const settings = vi.spyOn(api, "setCapStackEnabled").mockResolvedValue({ enabled: true });
    const votes = vi.spyOn(api, "setCapStackImageFeedback").mockImplementation(async (_id, body) => ({
      ...sample.items[0], manual_label: body.label === "clear" ? null : body.label ?? null,
    }));
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => { root.render(<CapStackScoutStage active />); await Promise.resolve(); });
    expect(host.textContent).toContain("Cap Stack Scout");
    expect(host.textContent).toContain("? caps");
    expect(host.querySelectorAll(".rrugc-cap-stack-card")).toHaveLength(1);
    const card = host.querySelector(".rrugc-cap-stack-card")!;
    const overlay = card.querySelector(".rrugc-cap-stack-votes.is-overlay")!;
    expect(overlay.parentElement?.classList.contains("rrugc-cap-stack-media")).toBe(true);
    expect(overlay.querySelectorAll("button")).toHaveLength(3);
    expect(card.querySelector(".rrugc-cap-stack-card-body")).toBeNull();
    expect(card.querySelector(".rrugc-cap-stack-ai-status")).toBeNull();
    expect(card.querySelector(".rrugc-cap-stack-card-footer strong")?.textContent).toBe("Cap Stack");
    expect(card.querySelector(".rrugc-cap-stack-card-footer a")?.getAttribute("href")).toBe(sample.items[0].pin_url);

    await act(async () => {
      host.querySelector<HTMLButtonElement>(".rrugc-cap-stack-start")!.click();
      await Promise.resolve();
    });
    expect(settings).toHaveBeenCalledWith(true);
    const approve = host.querySelector<HTMLButtonElement>(".rrugc-cap-stack-card .rrugc-cap-stack-votes button.good")!;
    await act(async () => { approve.click(); await Promise.resolve(); });
    expect(votes).not.toHaveBeenCalled();
    expect(host.querySelector(".rrugc-cap-stack-modal")).not.toBeNull();
    expect(host.textContent).toContain("confirm 2–6 visible caps");
    const input = host.querySelector<HTMLInputElement>(".rrugc-cap-stack-modal input[type=number]")!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(input, "4");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const confirm = host.querySelector<HTMLButtonElement>(".rrugc-cap-stack-count-actions .is-approve")!;
    expect(confirm.disabled).toBe(false);
    await act(async () => { confirm.click(); await Promise.resolve(); });
    expect(votes).toHaveBeenCalledWith("one", { label: "good", caps_count: 4 });
    expect(host.querySelector(".rrugc-cap-stack-card .label-good")).not.toBeNull();
    expect(host.querySelector('[role="dialog"]')).not.toBeNull();
    await act(async () => root.unmount());
  });

  it("shows Gemini suggestions separately and allows reanalysis without changing human feedback", async () => {
    const analyzed: api.CapStackPage = {
      ...sample,
      items: [{
        ...sample.items[0], count_status: "needs_review", caps_detected: 3,
        count_confidence: 0.52, manual_caps_count: 4, manual_label: "good",
        is_cap_stack: true, ai_suspected: true,
        visual_summary: "Several trucker caps overlap; a rear cap is obscured.",
        analysis_model: "gemini-pool-qa",
      }],
      vision: {
        ready: 0, analyzing: 0, pending: 0, needs_review: 1,
        not_cap_stack: 0, error: 0, ai_suspected: 1,
      },
    };
    vi.spyOn(api, "listCapStackImages").mockResolvedValue(analyzed);
    const retry = vi.spyOn(api, "retryCapStackVision").mockResolvedValue({
      image: { ...analyzed.items[0], count_status: "pending" }, vision_queued: 1,
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => { root.render(<CapStackScoutStage active />); await Promise.resolve(); });
    expect(host.textContent).toContain("Gemini: low confidence");
    expect(host.textContent).toContain("Suspected AI: 1");
    await act(async () => { host.querySelector<HTMLButtonElement>(".rrugc-cap-stack-preview")!.click(); });
    expect(host.textContent).toContain("Visible caps: 3");
    expect(host.textContent).toContain("Gemini model: gemini-pool-qa");
    const action = Array.from(host.querySelectorAll<HTMLButtonElement>(".rrugc-cap-stack-modal button"))
      .find(button => button.textContent?.includes("Retry Gemini"));
    await act(async () => { action!.click(); await Promise.resolve(); });
    expect(retry).toHaveBeenCalledWith("one");
    expect(host.textContent).toContain("Gemini vision · pending");
    expect(host.textContent).toContain("Approved");
    await act(async () => root.unmount());
  });


  it("keeps the three quick feedback actions on top of the photo without opening details when count is ready", async () => {
    const ready = { ...sample.items[0], caps_detected: 3, count_status: "ready", is_cap_stack: true };
    vi.spyOn(api, "listCapStackImages").mockResolvedValue({ ...sample, items: [ready] });
    const feedback = vi.spyOn(api, "setCapStackImageFeedback").mockImplementation(async (_id, body) => ({
      ...ready, manual_label: body.label === "clear" ? null : body.label ?? null,
      manual_caps_count: body.caps_count ?? ready.manual_caps_count,
    }));
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => { root.render(<CapStackScoutStage active />); await Promise.resolve(); });

    const photo = host.querySelector(".rrugc-cap-stack-media")!;
    const imageButton = photo.querySelector<HTMLButtonElement>(".rrugc-cap-stack-preview")!;
    const overlay = photo.querySelector(".rrugc-cap-stack-votes.is-overlay")!;
    expect(overlay.parentElement).toBe(photo);
    expect(imageButton.contains(overlay)).toBe(false);
    const action = (value: string) => overlay.querySelector<HTMLButtonElement>("button." + value)!;
    expect(action("good").getAttribute("aria-label")).toMatch(/Approve authentic/);
    expect(action("bad").getAttribute("aria-label")).toMatch(/Reject unsuitable/);
    expect(action("ai").getAttribute("aria-label")).toMatch(/AI-generated/);

    await act(async () => { action("good").click(); await Promise.resolve(); });
    expect(feedback).toHaveBeenLastCalledWith("one", { label: "good", caps_count: 3 });
    expect(action("good").getAttribute("aria-pressed")).toBe("true");
    expect(host.querySelector(".rrugc-cap-stack-modal")).toBeNull();

    await act(async () => { action("bad").click(); await Promise.resolve(); });
    expect(feedback).toHaveBeenLastCalledWith("one", { label: "bad" });
    expect(action("good").getAttribute("aria-pressed")).toBe("false");
    expect(action("bad").getAttribute("aria-pressed")).toBe("true");

    await act(async () => { action("ai").click(); await Promise.resolve(); });
    expect(feedback).toHaveBeenLastCalledWith("one", { label: "ai" });
    expect(action("ai").getAttribute("aria-pressed")).toBe("true");

    await act(async () => { action("ai").click(); await Promise.resolve(); });
    expect(feedback).toHaveBeenLastCalledWith("one", { label: "clear" });
    expect(action("ai").getAttribute("aria-pressed")).toBe("false");

    await act(async () => { imageButton.click(); });
    expect(host.querySelector(".rrugc-cap-stack-modal")).not.toBeNull();
    await act(async () => root.unmount());
  });

  it("asks for a visible count instead of posting an unprocessable approval", async () => {
    const outOfRange: api.CapStackPage = {
      ...sample, items: [{
        ...sample.items[0], caps_detected: 7, count_status: "out_of_range",
      }],
    };
    vi.spyOn(api, "listCapStackImages").mockResolvedValue(outOfRange);
    const feedback = vi.spyOn(api, "setCapStackImageFeedback").mockImplementation(
      async (_id, body) => ({
        ...outOfRange.items[0],
        manual_label: body.label === "clear" ? null : body.label ?? null,
        manual_caps_count: body.caps_count ?? null,
      }),
    );
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => { root.render(<CapStackScoutStage active />); await Promise.resolve(); });
    await act(async () => { host.querySelector<HTMLButtonElement>(".rrugc-cap-stack-votes.is-overlay button.good")!.click(); });
    expect(feedback).not.toHaveBeenCalled();
    const modal = host.querySelector(".rrugc-cap-stack-modal")!;
    expect(modal).not.toBeNull();
    expect(modal.textContent).toContain("confirm 2–6 visible caps");
    const input = modal.querySelector<HTMLInputElement>("input[type=number]")!;
    expect(input.value).toBe("");
    const confirm = () => host.querySelector<HTMLButtonElement>(".rrugc-cap-stack-count-actions .is-approve")!;
    expect(confirm().disabled).toBe(true);

    const setInput = (value: string) => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    };
    await act(async () => { setInput("7"); });
    expect(confirm().disabled).toBe(true);
    expect(feedback).not.toHaveBeenCalled();
    await act(async () => { setInput("3"); });
    expect(confirm().disabled).toBe(false);
    await act(async () => { confirm().click(); await Promise.resolve(); });
    expect(feedback).toHaveBeenCalledTimes(1);
    expect(feedback).toHaveBeenCalledWith("one", { label: "good", caps_count: 3 });
    expect(host.querySelector(".rrugc-cap-stack-card.label-good")).not.toBeNull();
    await act(async () => root.unmount());
  });

  it("keeps reject and AI actions available while Gemini has not counted caps", async () => {
    vi.spyOn(api, "listCapStackImages").mockResolvedValue(sample);
    const feedback = vi.spyOn(api, "setCapStackImageFeedback").mockImplementation(async (_id, body) => ({
      ...sample.items[0], manual_label: body.label === "clear" ? null : body.label ?? null,
    }));
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => { root.render(<CapStackScoutStage active />); await Promise.resolve(); });
    const overlay = host.querySelector(".rrugc-cap-stack-votes.is-overlay")!;
    await act(async () => { overlay.querySelector<HTMLButtonElement>("button.bad")!.click(); await Promise.resolve(); });
    expect(feedback).toHaveBeenLastCalledWith("one", { label: "bad" });
    await act(async () => { overlay.querySelector<HTMLButtonElement>("button.ai")!.click(); await Promise.resolve(); });
    expect(feedback).toHaveBeenLastCalledWith("one", { label: "ai" });
    expect(host.querySelector(".rrugc-cap-stack-modal")).toBeNull();
    await act(async () => root.unmount());
  });

  it("uses the masonry gallery without Gemini-status or layout dropdowns", async () => {
    const list = vi.spyOn(api, "listCapStackImages").mockResolvedValue(sample);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => { root.render(<CapStackScoutStage active />); await Promise.resolve(); });

    const gallery = host.querySelector(".rrugc-cap-stack-gallery")!;
    expect(gallery.classList.contains("view-masonry")).toBe(true);
    expect(gallery.classList.contains("rrugc-stable-masonry")).toBe(true);
    expect(gallery.querySelector(".rrugc-cap-stack-votes.is-overlay")).not.toBeNull();
    expect(host.querySelector('[aria-label="Filter by Gemini status"]')).toBeNull();
    expect(host.querySelector('[aria-label="Gallery view"]')).toBeNull();
    expect(host.querySelector('[aria-label="Filter by review status"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Filter by cap count"]')).not.toBeNull();
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ visionStatus: "all" }));
    await act(async () => root.unmount());
  });

  it("reserves varied masonry skeleton ratios and uses the actual aspect after lazy image decode", async () => {
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(1200);
    const items = Array.from({ length: 14 }, (_, index) => ({
      ...sample.items[0], id: "pin-" + index, image_url: "/img/pin-" + index + ".jpg",
    }));
    vi.spyOn(api, "listCapStackImages").mockResolvedValue({
      ...sample, items, total: items.length,
      stats: { ...sample.stats, discovered: items.length, pending: items.length },
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => { root.render(<CapStackScoutStage active />); await Promise.resolve(); });

    const gallery = host.querySelector(".rrugc-cap-stack-gallery.view-masonry")!;
    const columns = [...gallery.querySelectorAll(".rrugc-stable-masonry-column")];
    expect(columns.length).toBeGreaterThan(1);
    expect(gallery.querySelectorAll(".rrugc-cap-stack-card")).toHaveLength(14);
    const before = columns.map(col => [...col.querySelectorAll("[data-cap-stack-id]")]
      .map(card => card.getAttribute("data-cap-stack-id")));
    const ratios = [...gallery.querySelectorAll<HTMLElement>(".rrugc-cap-stack-media")]
      .map(element => element.style.aspectRatio);
    expect(new Set(ratios).size).toBeGreaterThan(2);

    const media = gallery.querySelector<HTMLElement>(".rrugc-cap-stack-media")!;
    const img = media.querySelector("img")!;
    expect(media.querySelector(".rrugc-cap-stack-skeleton")).not.toBeNull();
    expect(media.dataset.imageState).toBe("loading");
    expect(img.getAttribute("loading")).toBe("lazy");
    expect(img.getAttribute("decoding")).toBe("async");
    Object.defineProperty(img, "naturalWidth", { configurable: true, value: 600 });
    Object.defineProperty(img, "naturalHeight", { configurable: true, value: 1100 });
    await act(async () => { img.dispatchEvent(new Event("load")); });
    expect(media.dataset.imageState).toBe("loaded");
    expect(media.style.aspectRatio).toBe("0.6");
    expect(img.classList.contains("is-loaded")).toBe(true);
    const after = [...gallery.querySelectorAll(".rrugc-stable-masonry-column")]
      .map(col => [...col.querySelectorAll("[data-cap-stack-id]")].map(card => card.getAttribute("data-cap-stack-id")));
    expect(after).toEqual(before);
    await act(async () => root.unmount());
  });

  it("shows a multi-height skeleton gallery while the initial API request is pending", async () => {
    let settle!: (value: api.CapStackPage) => void;
    vi.spyOn(api, "listCapStackImages").mockImplementation(() =>
      new Promise<api.CapStackPage>(resolve => { settle = resolve; }));
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => { root.render(<CapStackScoutStage active />); });
    expect(host.querySelectorAll(".rrugc-cap-stack-placeholder")).toHaveLength(12);
    const placeholders = [...host.querySelectorAll<HTMLElement>(".rrugc-cap-stack-placeholder")];
    expect(new Set(placeholders.map(p => p.style.aspectRatio)).size).toBeGreaterThan(3);
    await act(async () => { settle(sample); await Promise.resolve(); });
    expect(host.querySelector(".rrugc-cap-stack-placeholder-gallery")).toBeNull();
    expect(host.querySelectorAll(".rrugc-cap-stack-card")).toHaveLength(1);
    await act(async () => root.unmount());
  });

  it("uses the shared Stage header and exactly two compact control rows", async () => {
    const page: api.CapStackPage = {
      ...sample, total: 2354,
      stats: { discovered: 2354, approved: 7, rejected: 30, ai: 2, pending: 2315 },
      scout: { ...sample.scout, enabled: true },
      vision: { ready: 270, analyzing: 14, pending: 1034, needs_review: 0,
        not_cap_stack: 945, out_of_range: 91, ai_suspected: 81, error: 0 },
    };
    vi.spyOn(api, "listCapStackImages").mockResolvedValue(page);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => { root.render(<CapStackScoutStage active />); await Promise.resolve(); });

    const header = host.querySelector(".rrugc-cap-stack-heading.rrugc-stage-header.rrugc-card")!;
    expect(header).not.toBeNull();
    expect(header.querySelector("h2")?.textContent).toBe("Cap Stack Scout");
    expect(header.querySelector(".rrugc-cap-stack-pause")).not.toBeNull();
    const controls = host.querySelector(".rrugc-cap-stack-controls")!;
    expect(controls.children).toHaveLength(2);
    expect(controls.children[0].classList.contains("rrugc-cap-stack-overview")).toBe(true);
    expect(controls.children[1].classList.contains("rrugc-cap-stack-toolbar")).toBe(true);
    const metrics = controls.querySelectorAll(".rrugc-cap-stack-metric");
    expect(metrics).toHaveLength(5);
    expect([...metrics].map(metric => metric.textContent)).toEqual([
      "Discovered2354", "Approved7", "Rejected30", "AI2", "Pending2315",
    ]);
    expect(host.querySelector(".rrugc-cap-stack-learning")).toBeNull();
    expect(controls.querySelector(".rrugc-cap-stack-vision")).not.toBeNull();
    const details = controls.querySelector<HTMLDetailsElement>(".rrugc-cap-stack-vision")!;
    expect(details.open).toBe(false);
    expect(details.querySelector("summary")?.textContent).toContain("270");
    expect(details.querySelector("summary")?.textContent).toContain("1048");
    await act(async () => { details.querySelector("summary")!.click(); });
    expect(details.open).toBe(true);
    expect(details.textContent).toContain("Suspected AI: 81");
    expect(details.textContent).toContain("Over 6 caps: 91");
    expect(controls.querySelectorAll(".rrugc-cap-stack-toolbar select")).toHaveLength(2);
    expect(controls.querySelector<HTMLInputElement>(".rrugc-cap-stack-search input")?.getAttribute("aria-label"))
      .toBe("Search Cap Stack library");
    expect(controls.querySelector(".rrugc-cap-stack-results")?.textContent).toContain("2354 results");
    await act(async () => root.unmount());
  });

  it("defaults to 100 images per page and supports 200/500 with server-side pagination", async () => {
    const list = vi.spyOn(api, "listCapStackImages").mockImplementation(async ({ page = 1, pageSize = 100 }) => ({
      ...sample, page, page_size: pageSize, total: 520,
      items: [{ ...sample.items[0], id: `counted-${page}`, caps_detected: 3, count_status: "ready", is_cap_stack: true }],
    }));
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => { root.render(<CapStackScoutStage active />); await Promise.resolve(); });

    const pageSelect = host.querySelector<HTMLSelectElement>('[aria-label="Cap Stack images per page"]')!;
    expect(pageSelect.value).toBe("100");
    expect([...pageSelect.options].map(option => Number(option.value))).toEqual([100, 200, 500]);
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({
      page: 1, pageSize: 100, capsCount: null, visionStatus: "all",
    }));
    expect(host.textContent).toContain("Page 1 of 6");
    const nextPage = () => host.querySelectorAll<HTMLButtonElement>(".rrugc-cap-stack-page-navigation button")[1];
    const choose = async (select: HTMLSelectElement, value: string) => {
      await act(async () => {
        select.value = value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        await Promise.resolve();
      });
    };
    await act(async () => { nextPage().click(); await Promise.resolve(); });
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2, pageSize: 100 }));
    expect(host.textContent).toContain("Page 2 of 6");

    await choose(pageSelect, "200");
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, pageSize: 200 }));
    expect(host.textContent).toContain("Page 1 of 3");
    await choose(pageSelect, "500");
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, pageSize: 500 }));
    expect(host.textContent).toContain("Page 1 of 2");

    const countSelect = host.querySelector<HTMLSelectElement>('[aria-label="Filter by cap count"]')!;
    expect(countSelect.options[0].textContent).toBe("Any number");
    await choose(countSelect, "2");
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, pageSize: 500, capsCount: 2 }));
    await choose(countSelect, "");
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, pageSize: 500, capsCount: null }));

    await act(async () => root.unmount());
  });

  it("does not poll while the panel is inactive", async () => {
    const loader = vi.spyOn(api, "listCapStackImages");
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<CapStackScoutStage active={false}/>));
    expect(loader).not.toHaveBeenCalled();
    await act(async () => root.unmount());
  });
});
