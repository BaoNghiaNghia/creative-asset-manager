// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../realistic-review-ugc/api";
import type { SourcePlan } from "../realistic-review-ugc/types";
import { TiktokShopScoutUgcPage, TIKTOKSHOP_SOURCE_ROOT } from "./TiktokShopScoutUgcPage";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function makePlan(name: string, progress: number): SourcePlan {
  return {
    id: name, root_folder_id: TIKTOKSHOP_SOURCE_ROOT,
    source_file_id: "drive-" + name, source_parent_folder_id: "folder-apparel",
    source_relative_path: "Shirts/" + name, source_name: name,
    source_mime_type: "image/jpeg", source_size_bytes: 150000,
    source_width: 600, source_height: 800,
    source_modified_at: null, source_web_url: null,
    source_preview_url: "data:image/svg+xml,%3Csvg%3E%3C/svg%3E",
    source_revision: name, analysis_revision: 1,
    embroidery_signature: name, embroidery_group_size: 1,
    target_count: 50, status: "ready",
    visual_context: { status: "ready", summary: "Shirt embroidery" },
    campaign_id: "campaign-" + name, campaign_name: name,
    campaign_status: "running", scout_status: "ready",
    auto_scout: true, search_queries: ["shirt reference"],
    progress_count: progress, pipeline_count: 0, candidate_count: progress,
    approved_count: progress, pending_ai_count: 0, drive_ready_count: progress,
    scan_next_at: null, scan_last_completed_at: null,
    reference_previews: [], last_error_code: null,
    analyzed_at: "2026-11-02T08:00:00Z",
    created_at: "2026-11-02T08:00:00Z",
    updated_at: "2026-11-02T08:00:00Z",
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  document.body.replaceChildren();
  document.body.style.overflow = "";
});

describe("TikTokShop Scout column sorting", () => {
  it("requests server-side Scout progress in both directions and renders the returned order", async () => {
    const low = makePlan("low.jpg", 2);
    const high = makePlan("high.jpg", 28);
    const list = vi.spyOn(api, "listSourcePlans").mockImplementation(async filters => {
      const descending = filters?.sortBy === "progress" && filters?.sortDirection === "desc";
      return {
        items: descending ? [high, low] : [low, high],
        total: 2, page: filters?.page || 1, page_size: filters?.pageSize || 20,
        overview: {
          embroidery_groups: 2, source_images: 2, working_groups: 2,
          refs_loaded: 30, stage2_groups: 0, stage2_source_images: 0,
          stage2_drive_ready_refs: 0, stage2_active_jobs: 0,
        },
      };
    });
    vi.spyOn(api, "listSourcePlanFolders").mockResolvedValue(null as never);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(<TiktokShopScoutUgcPage />);
      await Promise.resolve();
    });

    expect(list).toHaveBeenLastCalledWith(
      expect.objectContaining({ workspace: "tiktokshop", sortBy: "source", sortDirection: "asc" }),
      expect.anything(),
    );

    const table = () => host.querySelector<HTMLTableElement>(".rrugc-stage3-source-table")!;
    const button = () => host.querySelector<HTMLButtonElement>(".rrugc-stage3-scout-sort")!;
    const firstFile = () => table().querySelector(".rrugc-source-file-grouped > span > strong")?.textContent;

    await act(async () => { button().click(); await Promise.resolve(); });
    expect(list).toHaveBeenLastCalledWith(
      expect.objectContaining({ workspace: "tiktokshop", page: 1, sortBy: "progress", sortDirection: "desc" }),
      expect.anything(),
    );
    expect(table().dataset.sortColumn).toBe("scout");
    expect(table().querySelectorAll("thead th")[2].getAttribute("aria-sort")).toBe("descending");
    expect(firstFile()).toBe("high.jpg");

    await act(async () => { button().click(); await Promise.resolve(); });
    expect(list).toHaveBeenLastCalledWith(
      expect.objectContaining({ workspace: "tiktokshop", page: 1, sortBy: "progress", sortDirection: "asc" }),
      expect.anything(),
    );
    expect(table().querySelectorAll("thead th")[2].getAttribute("aria-sort")).toBe("ascending");
    expect(firstFile()).toBe("low.jpg");

    await act(async () => root.unmount());
  });
});
