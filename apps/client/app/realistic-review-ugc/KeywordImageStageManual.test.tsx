// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KeywordImageStage } from "./KeywordImageStage";
import { createManualKeywordImage, getKeywordImageJobStatus } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("./api", async importOriginal => ({
  ...(await importOriginal<typeof import("./api")>()),
  listStage2Skills: vi.fn(async () => ({
    stage_defaults: { stage1: "local:redesign-8869-v3" },
    items: [{ source: "local", skill_id: null, skill_name: "redesign-8869-v3",
      display_name: "Redesign 8869 V3", ready: true, keyword_artwork_ready: true,
      default_version: null, synced_version: null, local_version: null,
      version_options: [], sync_state: "ready" }],
  })),
  listKeywordImages: vi.fn(async () => ({
    total: 0, page: 1, page_size: 20, items: [],
    overview: { not_run: 0, queued: 0, running: 0, completed: 0, failed: 0 },
  })),
  createManualKeywordImage: vi.fn(async () => ({
    created: true, keyword_id: "manual-1", job_id: "job-1", status: "queued",
  })),
  getKeywordImageJobStatus: vi.fn(async () => ({
    keyword_id: "manual-1", keyword: "BEACH PLEASE", search_volume: 0,
    source_image_url: null, status: "completed", job_id: "job-1",
    skill_name: "redesign-8869-v3", skill_version: null,
    retry_count: 0, attempt_count: 1, max_attempts: 3,
    error_code: null, error_message: null,
    output_url: "/api/v1/realistic-review-ugc/keyword-images/jobs/job-1/output",
    updated_at: null,
  })),
}));

afterEach(() => document.body.replaceChildren());

describe("Stage 1 manual keyword generation", () => {
  it("queues typed keywords using Redesign V3 and shows the final saved image", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<KeywordImageStage active skillCatalogRevision={0} onManageSkills={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    const input = host.querySelector<HTMLInputElement>("#rrugc-stage1-manual-keyword")!;
    const form = host.querySelector<HTMLFormElement>("form.rrugc-keyword-manual")!;
    expect(input).not.toBeNull();
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    await act(async () => {
      setter?.call(input, "BEACH PLEASE");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(createManualKeywordImage).toHaveBeenCalledWith("BEACH PLEASE",
      expect.objectContaining({ skill_name: "redesign-8869-v3" }));
    expect(getKeywordImageJobStatus).toHaveBeenCalledWith("job-1", expect.anything());
    expect(host.querySelector(".rrugc-keyword-manual-final")?.textContent).toContain("View final image");
    expect(host.querySelector(".rrugc-keyword-manual-result img")).not.toBeNull();
    await act(async () => root.unmount());
  });
});
