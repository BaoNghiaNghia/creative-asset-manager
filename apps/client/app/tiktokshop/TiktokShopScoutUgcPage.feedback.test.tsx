// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../realistic-review-ugc/api";
import type { SourcePlan, SourcePlanReferencePreview } from "../realistic-review-ugc/types";
import { TiktokShopScoutUgcPage, TIKTOKSHOP_SOURCE_ROOT } from "./TiktokShopScoutUgcPage";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ref: SourcePlanReferencePreview = {
  id: "pinterest-ref-1",
  pin_url: "https://www.pinterest.com/pin/123456/",
  image_url: "data:image/svg+xml,%3Csvg%3E%3C/svg%3E",
  status: "drive_ready", picked: false, rejected: false,
  manual_label: null, source_query: "shirt front photo", width: 600,
  height: 800, created_at: "2026-11-02T09:00:00Z",
};
const source: SourcePlan = {
  id: "tiktok-source-1", root_folder_id: TIKTOKSHOP_SOURCE_ROOT,
  source_file_id: "drive-source-1",
  source_parent_folder_id: "folder-apparel",
  source_relative_path: "Shirts/shirt.jpg", source_name: "shirt.jpg",
  source_mime_type: "image/jpeg", source_size_bytes: 150000,
  source_width: 600, source_height: 800,
  source_modified_at: null, source_web_url: null,
  source_preview_url: "data:image/svg+xml,%3Csvg%3E%3C/svg%3E",
  source_revision: "revision-1", analysis_revision: 1,
  embroidery_signature: "sleeve-design", embroidery_group_size: 1,
  target_count: 50, status: "ready",
  visual_context: { status: "ready", summary: "Sleeve embroidery" },
  campaign_id: "tiktok-campaign-1", campaign_name: "shirt",
  campaign_status: "running", scout_status: "ready",
  auto_scout: true, search_queries: ["shirt front photo"],
  progress_count: 1, pipeline_count: 1, candidate_count: 1,
  approved_count: 1, pending_ai_count: 0, drive_ready_count: 1,
  scan_next_at: null, scan_last_completed_at: null,
  reference_previews: [ref], last_error_code: null,
  analyzed_at: "2026-11-02T08:00:00Z",
  created_at: "2026-11-02T08:00:00Z",
  updated_at: "2026-11-02T08:00:00Z",
};
const overview = {
  embroidery_groups: 1, source_images: 1, working_groups: 1,
  refs_loaded: 1, stage2_groups: 0, stage2_source_images: 0,
  stage2_drive_ready_refs: 1, stage2_active_jobs: 0,
};

afterEach(() => {
  vi.restoreAllMocks();
  document.body.replaceChildren();
  document.body.style.overflow = "";
});

describe("TikTokShop Stage 3 Reference feedback", () => {
  async function mount() {
    let serverLabel: "good" | "bad" | "ai" | null = null;
    const list = vi.spyOn(api, "listSourcePlans").mockImplementation(async () => {
      const reference = {
        ...ref, picked: serverLabel === "good",
        rejected: serverLabel === "bad" || serverLabel === "ai",
        manual_label: serverLabel,
      };
      return { items: [{ ...source, reference_previews: [reference] }],
        total: 1, page: 1, page_size: 20, overview };
    });
    vi.spyOn(api, "listSourcePlanFolders").mockResolvedValue(null as never);
    const update = vi.spyOn(api, "markCandidateReferenceFeedback").mockImplementation(
      async (_campaign, _candidate, label) => {
        serverLabel = label === "clear" ? null : label;
        return { candidate: {} as never, learning: {
          active: false, good_count: 0, bad_count: 0,
        } };
      },
    );
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(<TiktokShopScoutUgcPage />);
      await Promise.resolve();
    });
    const open = host.querySelector<HTMLButtonElement>(
      '[aria-label^="Open all references for"]',
    );
    expect(open).not.toBeNull();
    await act(async () => open!.click());
    const buttons = () => host.querySelectorAll<HTMLButtonElement>(
      ".rrugc-source-review-votes button",
    );
    expect(buttons()).toHaveLength(3);
    return { host, root, list, update, buttons };
  }

  it("saves Good, undo, Reject and AI labels in TikTokShop workspace", async () => {
    const { host, root, list, update, buttons } = await mount();
    await act(async () => { buttons()[0].click(); await Promise.resolve(); });
    expect(update).toHaveBeenLastCalledWith(
      source.campaign_id, ref.id, "good", expect.stringContaining("TikTokShop"), "tiktokshop",
    );
    expect(buttons()[0].getAttribute("aria-pressed")).toBe("true");
    expect(list).toHaveBeenCalledWith(
      expect.objectContaining({ workspace: "tiktokshop" }), expect.anything(),
    );

    await act(async () => { buttons()[0].click(); await Promise.resolve(); });
    expect(update).toHaveBeenLastCalledWith(
      source.campaign_id, ref.id, "clear", expect.any(String), "tiktokshop",
    );
    expect(buttons()[0].getAttribute("aria-pressed")).toBe("false");

    await act(async () => { buttons()[1].click(); await Promise.resolve(); });
    expect(update).toHaveBeenLastCalledWith(
      source.campaign_id, ref.id, "bad", expect.any(String), "tiktokshop",
    );
    expect(host.textContent).toContain("Rejected (1)");
    await act(async () => {
      host.querySelector<HTMLButtonElement>(".rrugc-source-review-filters button:nth-child(2)")!.click();
    });
    expect(buttons()).toHaveLength(3);
    await act(async () => { buttons()[2].click(); await Promise.resolve(); });
    expect(update).toHaveBeenLastCalledWith(
      source.campaign_id, ref.id, "ai", expect.any(String), "tiktokshop",
    );
    expect(buttons()[2].getAttribute("aria-pressed")).toBe("true");
    await act(async () => root.unmount());
  });

  it("opens the rejected-only recovery action and restores the reference in Stage 3", async () => {
    const { host, root, update, buttons } = await mount();
    await act(async () => { buttons()[1].click(); await Promise.resolve(); });
    await act(async () => {
      host.querySelector<HTMLButtonElement>('[aria-label="Close reference review"]')!.click();
    });
    expect(host.textContent).toContain("No active references");
    const action = host.querySelector<HTMLButtonElement>("#tiktok-panel-stage3 .rrugc-source-ref-review-rejected");
    expect(action?.textContent).toBe("Review rejected");
    expect(host.querySelector('[role="dialog"]')).toBeNull();

    await act(async () => action!.click());
    expect(host.querySelector('[role="dialog"]')).not.toBeNull();
    expect(host.textContent).toContain("Rejected (1)");

    await act(async () => {
      host.querySelector<HTMLButtonElement>('[aria-label="Restore rejected reference 1"]')!.click();
      await Promise.resolve();
    });
    expect(update).toHaveBeenLastCalledWith(
      source.campaign_id, ref.id, "clear", expect.any(String), "tiktokshop",
    );
    expect(host.querySelector(".rrugc-source-ref-review-rejected")).toBeNull();
    await act(async () => root.unmount());
  });

  it("restores the image and shows an error when saving feedback fails", async () => {
    const { host, root, update, buttons } = await mount();
    update.mockRejectedValueOnce(new Error("Access denied"));
    await act(async () => { buttons()[0].click(); await Promise.resolve(); });
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("Access denied");
    expect(buttons()[0].getAttribute("aria-pressed")).toBe("false");
    await act(async () => root.unmount());
  });
});
