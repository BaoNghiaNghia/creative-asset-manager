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
  total: 1, page: 1, page_size: 40,
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
    expect(votes).toHaveBeenCalledWith("one", { label: "good" });
    expect(host.querySelector(".rrugc-cap-stack-card .label-good")).not.toBeNull();

    await act(async () => {
      host.querySelector<HTMLButtonElement>(".rrugc-cap-stack-preview")!.click();
    });
    const input = host.querySelector<HTMLInputElement>(".rrugc-cap-stack-modal input[type=number]")!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(input, "4");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
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


  it("keeps the three quick feedback actions on top of the photo without opening details", async () => {
    vi.spyOn(api, "listCapStackImages").mockResolvedValue(sample);
    const feedback = vi.spyOn(api, "setCapStackImageFeedback").mockImplementation(async (_id, body) => ({
      ...sample.items[0], manual_label: body.label === "clear" ? null : body.label ?? null,
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
    expect(feedback).toHaveBeenLastCalledWith("one", { label: "good" });
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

  it("keeps both the compact grid and list controls usable", async () => {
    vi.spyOn(api, "listCapStackImages").mockResolvedValue(sample);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => { root.render(<CapStackScoutStage active />); await Promise.resolve(); });

    const gallery = host.querySelector(".rrugc-cap-stack-gallery")!;
    expect(gallery.classList.contains("view-grid")).toBe(true);
    const switchView = host.querySelector<HTMLSelectElement>(".rrugc-cap-stack-toolbar label:last-of-type select")!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!;
      setter.call(switchView, "list");
      switchView.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(gallery.classList.contains("view-list")).toBe(true);
    expect(gallery.querySelector(".rrugc-cap-stack-votes.is-overlay")).not.toBeNull();
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
