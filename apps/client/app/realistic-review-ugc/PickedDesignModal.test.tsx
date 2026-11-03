// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { PickedDesignModal } from "./PickedDesignModal";
import { listGenerationOutputVersions, listSourcePlans, selectStage1ProductDesign } from "./api";
import type { SourcePlan } from "./types";

vi.mock("./api", () => ({
  listSourcePlans: vi.fn(),
  listGenerationOutputVersions: vi.fn(),
  selectStage1ProductDesign: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const plan = {
  id: "picked-one",
  source_name: "design_03.png",
  source_relative_path: "Stage 1/job-one/design_03.png",
  source_preview_url: "/api/v1/realistic-review-ugc/source-plans/picked-one/image?rev=123",
} as SourcePlan;

beforeEach(() => {
  vi.mocked(listSourcePlans).mockResolvedValue({
    items: [plan], total: 1, page: 1, page_size: 500,
  } as Awaited<ReturnType<typeof listSourcePlans>>);
  vi.mocked(listGenerationOutputVersions).mockResolvedValue({
    versions: [{ version: 3, selected_for_products_stage2: true, output_name: "design_03.png" }],
  } as Awaited<ReturnType<typeof listGenerationOutputVersions>>);
  vi.mocked(selectStage1ProductDesign).mockResolvedValue({ selected: false, source_plan_id: null });
});

afterEach(() => {
  vi.clearAllMocks();
  document.body.replaceChildren();
});

describe("Picked design modal PNG transparency + masonry", () => {
  it("uses authenticated alpha-preserving PNG thumbs, keeps Unpick functional", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const onUnpick = vi.fn();
    await act(async () => root.render(<PickedDesignModal onClose={() => undefined} onUnpick={onUnpick} />));
    const image = document.body.querySelector<HTMLImageElement>(".rrugc-picked-masonry img");
    expect(image).toBeTruthy();
    expect(image?.getAttribute("src")).toBe(
      "/api/v1/realistic-review-ugc/source-plans/picked-one/image?rev=123&thumbnail=true&size=384&artwork=stage1-source-alpha-v2",
    );
    expect(image?.getAttribute("loading")).toBe("lazy");
    expect(document.body.querySelectorAll(".rrugc-picked-masonry article")).toHaveLength(1);
    const button = document.body.querySelector<HTMLButtonElement>(".rrugc-picked-masonry article button");
    expect(button?.disabled).toBe(false);
    await act(async () => button?.click());
    expect(selectStage1ProductDesign).toHaveBeenCalledWith("job-one", 3, false);
    expect(onUnpick).toHaveBeenCalledWith("job-one", 3);
    expect(document.body.querySelectorAll(".rrugc-picked-masonry article")).toHaveLength(0);
    await act(async () => root.unmount());
  });

});
