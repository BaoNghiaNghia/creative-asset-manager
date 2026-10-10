// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KeywordImageStage } from "./KeywordImageStage";
import { createManualKeywordImage, getKeywordAnalysisDetail, getKeywordImageJobStatus, listKeywordImages, listStage2Skills } from "./api";

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
  getKeywordAnalysisDetail: vi.fn(async (id: string) => ({
    id, keyword: "BEACH PLEASE", search_volume: 100, provider: "aebrowse_google_ads",
    competition: "HIGH", cpc_low: 1.17, cpc_high: 5.16,
    source_image_url: null, source_pin_url: null, picked: true, favorite: false,
    picked_at: null, favorite_at: null, fetched_at: "2026-10-10T01:00:00Z",
    trend: [{ period: "2026-08", volume: 100 }, { period: "2026-09", volume: 200 }],
  })),
  listKeywordImages: vi.fn(async () => ({
    total: 0, page: 1, page_size: 20, items: [],
    overview: { not_run: 0, queued: 0, running: 0, completed: 0, failed: 0 },
  })),
  createManualKeywordImage: vi.fn(async () => ({
    created: true, keyword_id: "manual-1", job_id: "job-1", status: "queued",
  })),
  listGenerationOutputVersions: vi.fn(async () => ({ stage: "stage1", job_id: "job-1", versions: Array.from({ length: 5 }, (_, index) => ({
    version: 5 - index, url: "/api/v1/image/" + (5 - index), created_at: "2026-10-10T00:00:00Z", width: 512, height: 512,
  })) })),
  getKeywordImageJobStatus: vi.fn(async () => ({
    keyword_id: "manual-1", keyword: "BEACH PLEASE", search_volume: 0,
    source_image_url: null, status: "completed", job_id: "job-1",
    skill_name: "redesign-8869-v3", skill_version: null,
    retry_count: 0, saved_output_count: 5, attempt_count: 1, max_attempts: 1,
    error_code: null, error_message: null,
    output_url: "/api/v1/realistic-review-ugc/keyword-images/jobs/job-1/output",
    updated_at: null,
  })),
}));

afterEach(() => document.body.replaceChildren());

describe("Stage 1 manual keyword generation", () => {
  it("describes six separate final designs when the new Stage 1 Skill is selected", async () => {
    vi.mocked(listStage2Skills).mockResolvedValueOnce({
      stage_defaults: { stage1: "local:gatorhats-stage1-six-designs" },
      items: [{ source: "local", skill_id: null, skill_name: "gatorhats-stage1-six-designs",
        display_name: "GatorHats Stage 1 Six Designs", ready: true, keyword_artwork_ready: true,
        default_version: null, synced_version: null, local_version: "4.0.0",
        version_options: [], sync_state: "ready" }],
    } as never);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<KeywordImageStage active skillCatalogRevision={0} onManageSkills={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(host.querySelector(".rrugc-keyword-manual-heading")?.textContent)
      .toContain("6 separate high-resolution final designs");
    expect(host.querySelector(".rrugc-keyword-gen-list-title")?.textContent)
      .toContain("6 final images per job");
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

  it("renders Output slider and Actions as independent table columns", async () => {
    vi.mocked(listKeywordImages).mockResolvedValueOnce({
      page: 1, page_size: 20, total: 1,
      overview: { not_run: 0, queued: 0, running: 0, completed: 1, failed: 0 },
      items: [{
        keyword_id: "key-1", keyword: "BEACH PLEASE", search_volume: 100,
        source_image_url: null, status: "completed", job_id: "job-1",
        skill_name: "redesign-8869-v3", skill_version: null, retry_count: 0,
        saved_output_count: 5, attempt_count: 1, max_attempts: 1,
        error_code: null, error_message: null, output_url: "/api/v1/image/5", updated_at: null,
      }],
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(
      <KeywordImageStage active skillCatalogRevision={0} onManageSkills={() => undefined} />,
    ));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    const headers = Array.from(host.querySelectorAll(".rrugc-keyword-gen-table th")).map(node => node.textContent?.trim());
    expect(headers).toEqual(["Keyword / Source", "Search volume", "Generation skill", "Job status", "Duration", "Finished at", "Output", "Actions"]);
    const cells = host.querySelectorAll(".rrugc-keyword-row td");
    expect(cells).toHaveLength(8);
    expect(cells[4].textContent).toBe("—");
    expect(cells[5].textContent).toBe("—");
    expect(cells[6].querySelector(".rrugc-keyword-output-track")).not.toBeNull();
    expect(cells[7].querySelector(".rrugc-keyword-result-actions")).not.toBeNull();
    expect(cells[7].querySelector(".rrugc-keyword-output-track")).toBeNull();
    // A real thumbnail click must restore the same gallery modal used by Versions;
    // dragging the strip remains separate and never navigates to a raw image URL.
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    const thumbnail = host.querySelectorAll<HTMLButtonElement>(".rrugc-keyword-output-image")[1];
    expect(thumbnail?.tagName).toBe("BUTTON");
    await act(async () => { thumbnail.click(); await Promise.resolve(); await Promise.resolve(); });
    expect(host.querySelector('[role="dialog"][aria-modal="true"]')).not.toBeNull();
    expect(host.querySelector(".rrugc-blueprint-cross")).not.toBeNull();
    expect(host.querySelectorAll(".rrugc-version-compare-card")).toHaveLength(0);
    expect(host.querySelector(".rrugc-version-filter")).toBeNull();
    expect(host.querySelector('[aria-label="Select output versions"]')).toBeNull();
    expect(host.querySelector<HTMLAnchorElement>(".rrugc-blueprint-original")?.getAttribute("href"))
      .toBe("/api/v1/image/4");
    await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Close output versions"]')?.click());
    expect(host.querySelector('[role="dialog"][aria-modal="true"]')).toBeNull();
    expect(host.querySelector(".rrugc-keyword-output-track")).not.toBeNull();
    await act(async () => {
      host.querySelector<HTMLButtonElement>('button[aria-label="Open keyword details for BEACH PLEASE"]')?.click();
      await Promise.resolve(); await Promise.resolve();
    });
    expect(getKeywordAnalysisDetail).toHaveBeenCalledWith("key-1", expect.anything());
    expect(host.querySelector(".rrugc-stage0-detail-modal")?.textContent).toContain("Monthly search volume");
    await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Close keyword details"]')?.click());
    await act(async () => root.unmount());
  });

});