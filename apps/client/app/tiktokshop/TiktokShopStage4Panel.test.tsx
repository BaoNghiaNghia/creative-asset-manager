// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import { TiktokShopStage4Panel } from "./TiktokShopStage4Panel";
import type { SourcePlan, Stage2Job } from "../realistic-review-ugc/types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const plan = {
  id: "plan-1", source_name: "Holiday shirt.png", source_relative_path: "Shirts/Holiday shirt.png",
  source_preview_url: "/mock.png", status: "ready", campaign_id: "campaign-1",
  source_group_images: [],
  reference_previews: Array.from({length: 6}, (_, i) => ({
    id: "ref-" + i, status: "drive_ready", rejected: i === 5,
    image_url: "/ref.png", pin_url: "https://example.com/ref", source_query: "apparel",
  })),
} as unknown as SourcePlan;
const done = {
  id: "done", source_plan_id: plan.id, status: "completed", selected_candidate_ids: ["ref-0"],
  created_at: "2026-11-24T00:00:00Z", output_remote_file_id: "remote-1",
} as Stage2Job;
const failed = {
  id: "failed", source_plan_id: plan.id, status: "failed", selected_candidate_ids: ["ref-1"],
  created_at: "2026-11-24T00:01:00Z", last_error_message: "Mock failure",
} as Stage2Job;
describe("TikTokShop Stage 4 compact table", () => {
  it("preserves generation selection cap, already-generated protection, history and actions", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const onCreate = vi.fn(async () => {});
    const onRetry = vi.fn(async () => {});
    const onRegenerate = vi.fn(async () => {});
    const onPageChange = vi.fn();
    const onPageSizeChange = vi.fn();
    const onSortByChange = vi.fn();
    try {
      await act(async () => root.render(<TiktokShopStage4Panel plans={[plan]} jobs={[done, failed]}
        loading={false} busy={false} page={1} pageSize={20} total={21}
        query="" folderPath={null} sortBy="source" sortDirection="asc"
        onPageChange={onPageChange} onPageSizeChange={onPageSizeChange} onQueryChange={vi.fn()}
        onClearFolder={vi.fn()} onSortByChange={onSortByChange} onSortDirectionChange={vi.fn()}
        onCreate={onCreate} onRetry={onRetry} onRegenerate={onRegenerate} onRefresh={vi.fn()} />));
      expect(host.querySelectorAll(".tiktok-generation-row")).toHaveLength(1);
      expect(host.querySelectorAll(".tiktok-generation-table th")).toHaveLength(4);
      const checkbox = (i:number) => host.querySelector<HTMLInputElement>('input[aria-label="Pick reference ref-' + i + '"]')!;
      const generate = host.querySelector<HTMLButtonElement>(".tiktok-generate-button")!;
      expect(checkbox(0).disabled).toBe(true);
      expect(checkbox(5)).toBeNull();
      expect(generate.disabled).toBe(true);
      await act(async () => checkbox(1).click());
      await act(async () => checkbox(2).click());
      await act(async () => checkbox(3).click());
      expect(checkbox(4).disabled).toBe(true);
      expect(generate.disabled).toBe(false);
      await act(async () => generate.click());
      expect(onCreate).toHaveBeenCalledWith(plan, ["ref-1", "ref-2", "ref-3"]);
      expect(generate.disabled).toBe(true);
      const buttons = [...host.querySelectorAll<HTMLButtonElement>(".tiktok-output-action")];
      await act(async () => buttons.find(button => button.textContent === "Retry")!.click());
      await act(async () => buttons.find(button => button.textContent === "Regenerate")!.click());
      expect(onRetry).toHaveBeenCalledWith(failed);
      expect(onRegenerate).toHaveBeenCalledWith(done);
      expect(host.querySelector(".tiktok-output-media[href]")?.getAttribute("href")).toContain("workspace=tiktokshop");
      await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Next Stage 4 page"]')!.click());
      expect(onPageChange).toHaveBeenCalledWith(2);
    } finally { await act(async () => root.unmount()); host.remove(); }
  });
});
