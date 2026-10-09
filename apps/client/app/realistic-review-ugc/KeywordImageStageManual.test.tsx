// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KeywordImageStage } from "./KeywordImageStage";
import { createManualKeywordImage, getKeywordImageJobStatus, listStage2Skills } from "./api";

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
  it("describes both independent v4 boards when the v4 Skill is selected", async () => {
    vi.mocked(listStage2Skills).mockResolvedValueOnce({
      stage_defaults: { stage1: "local:hanh-redesign-8869-ver-4" },
      items: [{ source: "local", skill_id: null, skill_name: "hanh-redesign-8869-ver-4",
        display_name: "GatorHats v4", ready: true, keyword_artwork_ready: true,
        default_version: null, synced_version: null, local_version: "4.0.0",
        version_options: [], sync_state: "ready" }],
    } as never);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<KeywordImageStage active skillCatalogRevision={0} onManageSkills={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(host.querySelector(".rrugc-keyword-manual-heading")?.textContent)
      .toContain("10 independent concepts + 13 hat colorways");
    expect(host.querySelector(".rrugc-keyword-gen-list-title")?.textContent)
      .toContain("Regenerate");
    await act(async () => root.unmount());
  });


  it("honors an explicitly selected Stage 1 default over legacy hardcoded names", async () => {
    vi.mocked(listStage2Skills).mockResolvedValueOnce({
      stage_defaults: { stage1: "local:hanh-redesign-8869-ver-3" },
      items: [
        { source: "local", skill_id: null, skill_name: "redesign-8869-v3",
          display_name: "Legacy redesign", ready: true, keyword_artwork_ready: true,
          default_version: null, synced_version: null, local_version: null,
          version_options: [], sync_state: "ready" },
        { source: "local", skill_id: null, skill_name: "hanh-redesign-8869-ver-3",
          display_name: "Hanh redesign", ready: true, keyword_artwork_ready: true,
          default_version: null, synced_version: null, local_version: null,
          version_options: [], sync_state: "ready" },
      ],
    } as never);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<KeywordImageStage active skillCatalogRevision={0} onManageSkills={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    const select = host.querySelector<HTMLSelectElement>('select[aria-label="Keyword generation Skill"]')!;
    expect(select.value).toBe("local:hanh-redesign-8869-ver-3");
    await act(async () => root.unmount());
  });


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
    expect(host.querySelector(".rrugc-keyword-manual-final")?.textContent).toContain("View latest output");
    expect(host.querySelector(".rrugc-keyword-manual-result img")).not.toBeNull();
    await act(async () => root.unmount());
  });
});
