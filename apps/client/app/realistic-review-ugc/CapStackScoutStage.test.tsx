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
