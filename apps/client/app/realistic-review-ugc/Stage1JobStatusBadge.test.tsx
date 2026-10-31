// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { Stage1JobStatusBadge } from "./KeywordImageStage";
import type { KeywordImageRow } from "./types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const baseline: KeywordImageRow = {
  keyword_id: "example", keyword: "BEACH PLEASE", search_volume: 100,
  source_image_url: null, status: "completed", job_id: "job-1",
  skill_name: "creative-brief-v3", skill_version: null, retry_count: 0,
  saved_output_count: 6, attempt_count: 1, max_attempts: 1,
  error_code: null, error_message: null, output_url: null, updated_at: null,
};

async function mount(row: KeywordImageRow) {
  const host = document.createElement("div");
  host.className = "rrugc-keyword-gen-status-cell";
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<Stage1JobStatusBadge row={row} />));
  return {
    host,
    badge: () => host.querySelector<HTMLElement>(".rrugc-keyword-gen-badge")!,
    async cleanup() { await act(async () => root.unmount()); host.remove(); },
  };
}

afterEach(() => document.body.replaceChildren());

describe("Stage 1 job error badges", () => {
  it("shows the red Partial message only while hovering the badge, outside the table", async () => {
    const error = "Saved 4/6 genuine image edits. Unavailable concepts were not replaced with synthetic images.";
    const ui = await mount({
      ...baseline, error_code: "stage1_partial_output", error_message: error,
      saved_output_count: 4,
    });
    try {
      expect(ui.badge().textContent).toContain("Partial");
      expect(ui.host.textContent).not.toContain(error);
      expect(document.querySelector('[role="tooltip"]')).toBeNull();
      await act(async () => ui.badge().dispatchEvent(new MouseEvent("mouseover", { bubbles: true })));
      const popup = document.querySelector<HTMLElement>(".rrugc-keyword-gen-error-tooltip");
      expect(popup?.getAttribute("role")).toBe("tooltip");
      expect(popup?.textContent).toBe(error);
      expect(ui.host.contains(popup)).toBe(false);
      expect(ui.badge().getAttribute("aria-describedby")).toBe(popup?.id);
      await act(async () => ui.badge().dispatchEvent(new MouseEvent("mouseout", { bubbles: true })));
      expect(document.querySelector(".rrugc-keyword-gen-error-tooltip")).toBeNull();
      expect(ui.host.textContent).not.toContain(error);
    } finally { await ui.cleanup(); }
  });

  it("exposes Failed error details on keyboard focus and hides them on blur/Escape", async () => {
    const error = "Unexpected Stage 1 final image filename.";
    const ui = await mount({
      ...baseline, status: "failed", error_code: "invalid_final_filename", error_message: error,
      saved_output_count: 0,
    });
    try {
      expect(ui.badge().textContent).toContain("Failed");
      expect(ui.badge().getAttribute("tabindex")).toBe("0");
      expect(ui.host.textContent).not.toContain(error);
      await act(async () => ui.badge().focus());
      expect(document.querySelector('[role="tooltip"]')?.textContent).toBe(error);
      await act(async () => ui.badge().dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
      expect(document.querySelector('[role="tooltip"]')).toBeNull();
      await act(async () => { ui.badge().blur(); ui.badge().focus(); });
      expect(document.querySelector('[role="tooltip"]')?.textContent).toBe(error);
      await act(async () => ui.badge().blur());
      expect(document.querySelector('[role="tooltip"]')).toBeNull();
    } finally { await ui.cleanup(); }
  });

  it("keeps Completed clean and provides fallback message for Failed without error text", async () => {
    const ok = await mount(baseline);
    try {
      expect(ok.badge().getAttribute("tabindex")).toBeNull();
      await act(async () => ok.badge().dispatchEvent(new MouseEvent("mouseover", { bubbles: true })));
      expect(document.querySelector('[role="tooltip"]')).toBeNull();
    } finally { await ok.cleanup(); }
    const failed = await mount({ ...baseline, status: "failed", error_code: null, error_message: null });
    try {
      await act(async () => failed.badge().dispatchEvent(new MouseEvent("mouseover", { bubbles: true })));
      expect(document.querySelector('[role="tooltip"]')?.textContent).toBe("Generation failed.");
    } finally { await failed.cleanup(); }
  });
});
