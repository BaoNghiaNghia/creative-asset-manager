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
  getHatStockCatalog: vi.fn(async () => ({ can_sync: false, colors: {
    "white-black": { revision: "fixture-1", views: {
      front: "/official/white-black/front.jpg", side: "/official/white-black/side.jpg",
    } },
  } })),
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

describe("Stage 1 Products without the manual keyword artwork bar", () => {
  it("hides the entire manual keyword creator, retaining Generate all unused and the keyword jobs table", async () => {
    vi.mocked(listKeywordImages).mockResolvedValueOnce({
      page: 1, page_size: 20, total: 1,
      overview: { not_run: 1, queued: 0, running: 0, completed: 0, failed: 0 },
      items: [{
        keyword_id: "key-1", keyword: "BEACH PLEASE", search_volume: 100,
        source_image_url: null, status: "not_run", job_id: null,
        skill_name: null, skill_version: null, retry_count: 0,
        saved_output_count: 0, attempt_count: 0, max_attempts: 0,
        error_code: null, error_message: null, output_url: null, updated_at: null,
      }],
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(
      <KeywordImageStage active skillCatalogRevision={0} onManageSkills={() => undefined} />,
    ));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(host.querySelector(".rrugc-keyword-manual")).toBeNull();
    expect(host.querySelector("#rrugc-stage1-manual-keyword")).toBeNull();
    expect(host.textContent).not.toContain("Create artwork from a keyword");
    expect(host.textContent).not.toContain("Stage 1 requires the Six Designs Skill");
    expect(host.textContent).not.toContain("Enter keyword or quote");
    expect(host.querySelector(".rrugc-keyword-gen-actions")?.textContent).toContain("Generate all unused");
    expect(host.querySelector(".rrugc-keyword-gen-stats")).not.toBeNull();
    expect(host.querySelector(".rrugc-keyword-gen-table")).not.toBeNull();
    expect(host.textContent).toContain("BEACH PLEASE");
    expect(createManualKeywordImage).not.toHaveBeenCalled();
    expect(getKeywordImageJobStatus).not.toHaveBeenCalled();
    await act(async () => root.unmount());
  });

  it("keeps the main Stage 1 skill selection when Six Designs is installed", async () => {
    vi.mocked(listStage2Skills).mockResolvedValueOnce({
      stage_defaults: { stage1: "local:gatorhats-stage1-six-designs" },
      items: [{ source: "local", skill_id: null, skill_name: "gatorhats-stage1-six-designs",
        display_name: "GatorHats Stage 1 Six Designs", ready: true, keyword_artwork_ready: true,
        default_version: "4.0.0", synced_version: "4.0.0", local_version: "4.0.0",
        version_options: ["4.0.0"], sync_state: "ready" }],
    } as never);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(
      <KeywordImageStage active skillCatalogRevision={0} onManageSkills={() => undefined} />,
    ));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(host.querySelector(".rrugc-keyword-manual")).toBeNull();
    expect(host.querySelector(".rrugc-keyword-gen-primary")).not.toBeNull();
    expect(host.querySelector(".rrugc-keyword-gen-table")).not.toBeNull();
    await act(async () => root.unmount());
  });

  it("renders recent Finished at as Vietnamese time ago with full date on hover and preserves absolute dates older than 24 hours", async () => {
    const recent = new Date(Date.now() - 24 * 60_000 - 15_000).toISOString();
    const older = new Date(Date.now() - 26 * 60 * 60_000).toISOString();
    const completed = (id: string, finished_at: string) => ({
      keyword_id: id, keyword: id, search_volume: 100,
      source_image_url: null, status: "completed" as const, job_id: "job-" + id,
      skill_name: "redesign-8869-v3", skill_version: null, retry_count: 0,
      saved_output_count: 0, attempt_count: 1, max_attempts: 1,
      error_code: null, error_message: null, output_url: null, updated_at: null,
      finished_at,
    });
    vi.mocked(listKeywordImages).mockResolvedValueOnce({
      page: 1, page_size: 20, total: 2,
      overview: { not_run: 0, queued: 0, running: 0, completed: 2, failed: 0 },
      items: [completed("RECENT", recent), completed("OLDER", older)],
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(
      <KeywordImageStage active skillCatalogRevision={0} onManageSkills={() => undefined} />,
    ));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    const times = Array.from(host.querySelectorAll<HTMLTimeElement>(".rrugc-keyword-gen-finished time"));
    expect(times).toHaveLength(2);
    expect(times[0].textContent).toBe("24 phút trước");
    expect(times[0].dateTime).toBe(recent);
    expect(times[0].title).toBe(new Date(recent).toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    }));
    expect(times[1].textContent).toBe(times[1].title);
    expect(times[1].textContent).not.toContain("trước");
    await act(async () => root.unmount());
  });

  it("shows the Stage 0 picked timestamp and sorts by Picked date, without assuming a date for legacy records", async () => {
    const response = {
      page: 1, page_size: 20, total: 2,
      overview: { not_run: 2, queued: 0, running: 0, completed: 0, failed: 0 },
      items: ["SELECTED", "LEGACY"].map((keyword, index) => ({
        keyword_id: keyword, keyword, search_volume: 100,
        picked_at: index === 0 ? "2026-10-08T08:15:00Z" : null,
        source_image_url: null, status: "not_run" as const, job_id: null,
        skill_name: null, skill_version: null, retry_count: 0,
        saved_output_count: 0, attempt_count: 0, max_attempts: 0,
        error_code: null, error_message: null, output_url: null, updated_at: null,
      })),
    };
    vi.mocked(listKeywordImages).mockResolvedValueOnce(response).mockResolvedValueOnce(response).mockResolvedValueOnce(response);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(
      <KeywordImageStage active skillCatalogRevision={0} onManageSkills={() => undefined} />,
    ));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    const times = host.querySelectorAll<HTMLTimeElement>(".rrugc-keyword-gen-picked time");
    expect(times).toHaveLength(1);
    expect(times[0].dateTime).toBe("2026-10-08T08:15:00Z");
    expect(times[0].textContent).toContain("2026");
    expect(host.querySelectorAll(".rrugc-keyword-gen-picked")[1]?.textContent).toBe("—");
    await act(async () => {
      host.querySelector<HTMLButtonElement>('button[aria-label="Sort by Picked date descending"]')?.click();
      await Promise.resolve();
    });
    expect(listKeywordImages).toHaveBeenLastCalledWith(
      expect.objectContaining({ sortBy: "picked_at", sortDirection: "desc", page: 1 }),
      expect.anything(),
    );
    await act(async () => {
      host.querySelector<HTMLButtonElement>('button[aria-label="Sort by Picked date ascending"]')?.click();
      await Promise.resolve();
    });
    expect(listKeywordImages).toHaveBeenLastCalledWith(
      expect.objectContaining({ sortBy: "picked_at", sortDirection: "asc", page: 1 }),
      expect.anything(),
    );
    await act(async () => root.unmount());
  });

  it("falls back to the existing Stage 0 detail API when the deployed Stage 1 API omits picked_at", async () => {
    vi.mocked(getKeywordAnalysisDetail).mockResolvedValueOnce({
      id: "legacy-1", keyword: "LEGACY", search_volume: 100,
      provider: "aebrowse_google_ads", picked: true,
      picked_at: "2026-10-08T08:15:00Z", favorite: false, favorite_at: null,
      source_image_url: null, source_pin_url: null, fetched_at: "2026-10-10T01:00:00Z",
    } as never);
    vi.mocked(listKeywordImages).mockResolvedValueOnce({
      total: 1, page: 1, page_size: 20,
      overview: { not_run: 1, queued: 0, running: 0, completed: 0, failed: 0 },
      items: [{
        keyword_id: "legacy-1", keyword: "LEGACY", search_volume: 100,
        source_image_url: null, status: "not_run", job_id: null,
        skill_name: null, skill_version: null, retry_count: 0,
        saved_output_count: 0, attempt_count: 0, max_attempts: 0,
        error_code: null, error_message: null, output_url: null, updated_at: null,
      }],
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(
      <KeywordImageStage active skillCatalogRevision={0} onManageSkills={() => undefined} />,
    ));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
    expect(getKeywordAnalysisDetail).toHaveBeenCalledWith("legacy-1", expect.anything());
    expect(host.querySelector<HTMLTimeElement>(".rrugc-keyword-gen-picked time")?.dateTime)
      .toBe("2026-10-08T08:15:00Z");
    expect(host.querySelector(".rrugc-keyword-gen-table thead")?.textContent).toContain("Picked date");
    expect(host.querySelector('button[aria-label="Sort by Picked date descending"]')).toBeNull();
    await act(async () => root.unmount());
  });

  it("renders Output slider and Actions as independent table columns", async () => {
    vi.mocked(listKeywordImages).mockResolvedValueOnce({
      page: 1, page_size: 20, total: 1,
      overview: { not_run: 0, queued: 0, running: 0, completed: 1, failed: 0 },
      items: [{
        keyword_id: "key-1", keyword: "BEACH PLEASE", search_volume: 100,
        picked_at: null, source_image_url: null, status: "completed", job_id: "job-1",
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
    expect(headers).toEqual(["Keyword / Source↕", "Trend", "Search volume↕", "Generation skill", "Job status", "Picked date↕", "Duration↕", "Finished at↓", "Output", "Actions"]);
    const cells = host.querySelectorAll(".rrugc-keyword-row td");
    expect(cells).toHaveLength(10);
    expect(cells[1].querySelector(".rrugc-stage0-trend")).not.toBeNull();
    expect(cells[5].textContent).toBe("—");
    expect(cells[6].textContent).toBe("—");
    expect(cells[7].textContent).toBe("—");
    expect(cells[8].querySelector(".rrugc-keyword-output-track")).not.toBeNull();
    expect(cells[9].querySelector(".rrugc-keyword-result-actions")).not.toBeNull();
    expect(cells[9].querySelector(".rrugc-keyword-output-track")).toBeNull();
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
    // The full-resolution design is still loaded, but the redundant footer
    // with its raw-image link is gone to maximize cap-preview space.
    expect(host.querySelector(".rrugc-blueprint-original")).toBeNull();
    expect(host.querySelector(".rrugc-blueprint-selected-design img")).not.toBeNull();
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
    expect(host.querySelector('th[aria-sort="descending"]')?.textContent).toContain("Finished at");
    await act(async () => {
      host.querySelector<HTMLButtonElement>('button[aria-label="Sort by Search volume descending"]')?.click();
      await Promise.resolve(); await Promise.resolve();
    });
    expect(listKeywordImages).toHaveBeenLastCalledWith(
      expect.objectContaining({ sortBy: "search_volume", sortDirection: "desc", page: 1 }),
      expect.anything(),
    );
    await act(async () => {
      host.querySelector<HTMLButtonElement>('button[aria-label="Sort by Search volume ascending"]')?.click();
      await Promise.resolve(); await Promise.resolve();
    });
    expect(listKeywordImages).toHaveBeenLastCalledWith(
      expect.objectContaining({ sortBy: "search_volume", sortDirection: "asc", page: 1 }),
      expect.anything(),
    );
    await act(async () => root.unmount());
  });

});