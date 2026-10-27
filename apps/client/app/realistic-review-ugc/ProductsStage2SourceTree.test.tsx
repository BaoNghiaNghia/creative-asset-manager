// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EmbroideryColorwayStage } from "./EmbroideryColorwayStage";
import { ProductsStage2SourceTree, stage2DesignKeyword } from "./ProductsStage2SourceTree";
import type { SourcePlan, SourcePlanPage } from "./types";
import type { ProductDesignProgress } from "./ProductsStage2Progress";

vi.mock("./ProductsPhotoWorkflow", async importOriginal => {
  const actual = await importOriginal<typeof import("./ProductsPhotoWorkflow")>();
  return {
    ...actual,
    ProductsPhotoWorkflow: ({ sourcePlanId }: { sourcePlanId?: string }) =>
      <div data-testid="product-workflow" data-plan-id={sourcePlanId}>7 skill workspace</div>,
  };
});

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => { document.body.replaceChildren(); localStorage.clear(); });

const plans = [
  {
    id: "design-one",
    source_name: "design_02.png",
    source_relative_path: "Stage 1/job-one/design_02.png",
    source_preview_url: "/api/v1/realistic-review-ugc/source-plans/design-one/image?v=1",
    source_revision: "ver-one",
    source_keyword: "COLD BE MEANER",
    updated_at: "2026-10-27T02:00:00Z",
    status: "ready",
    visual_context: null,
  },
  {
    id: "design-two",
    source_name: "design_04.png",
    source_relative_path: "Stage 1/job-two/design_04.png",
    source_preview_url: "/api/v1/realistic-review-ugc/source-plans/design-two/image?v=2",
    source_revision: "ver-two",
    source_keyword: "Y'ALL NEED JESUS",
    updated_at: "2026-10-27T02:01:00Z",
    status: "ready",
    visual_context: null,
  },
] as SourcePlan[];

const progressByPlan: Record<string, ProductDesignProgress> = {
  "design-one": { state: "complete", completed: 22, total: 22, running: 0, failed: 0 },
  "design-two": { state: "running", completed: 4, total: 22, running: 1, failed: 0 },
};

const page: SourcePlanPage = {
  items: plans, total: 2, page: 1, page_size: 20,
  overview: {
    embroidery_groups: 2, source_images: 2, working_groups: 0, refs_loaded: 0,
    stage2_groups: 2, stage2_source_images: 2, stage2_drive_ready_refs: 0, stage2_active_jobs: 0,
  },
};

describe("Products Stage 2 thumbnail + keyword source tree", () => {
  it("labels source thumbnails by stored Stage 1 keyword, never by generic PNG name", () => {
    expect(stage2DesignKeyword(plans[0])).toBe("COLD BE MEANER");
    expect(stage2DesignKeyword({ ...plans[0], source_keyword: null,
      visual_context: { embroidery_text: ["  LOVE YOUR NEIGHBOR  "] } }))
      .toBe("LOVE YOUR NEIGHBOR");
    expect(stage2DesignKeyword({ ...plans[0], source_keyword: null })).toBe("design 02");
  });

  it("shows a row for each source with a private 128px thumbnail and an action", async () => {
    const host = document.createElement("div"); document.body.append(host);
    const root = createRoot(host);
    const onSelect = vi.fn();
    await act(async () => root.render(
      <ProductsStage2SourceTree plans={plans} total={2} loading={false}
        progressByPlan={progressByPlan}
        selectedPlanId="design-one" onSelect={onSelect}
        sortBy="source" sortDirection="asc" onSortChange={() => undefined} />,
    ));
    const items = host.querySelectorAll<HTMLButtonElement>(".rrugc-products-source-tree-item");
    expect(items).toHaveLength(2);
    expect(items[0].getAttribute("aria-current")).toBe("true");
    expect(items[0].textContent).toContain("COLD BE MEANER");
    expect(items[1].textContent).toContain("Y'ALL NEED JESUS");
    expect(items[1].getAttribute("title")).toContain("design_04.png");
    expect(items[0].textContent).toContain("Complete");
    expect(items[0].textContent).toContain("22/22");
    expect(items[1].textContent).toContain("Running");
    expect(items[1].textContent).toContain("4/22");
    expect(host.textContent).toContain("All 2 designs");
    expect(host.textContent).toContain("1 complete");
    expect(host.querySelector(".rrugc-products-source-tree-item.is-selected")).toBe(items[0]);
    await act(async () => items[1].click());
    expect(onSelect).toHaveBeenCalledWith("design-two");
    const image = items[0].querySelector("img");
    expect(image).not.toBeNull();
    // The lazy loader may not have loaded src yet; verify the thumb reference
    // is never handed off as a full original.
    expect(image?.getAttribute("src") ?? "").not.toMatch(/artwork=stage1-thread-geometry/);
    await act(async () => root.unmount());
  });

  it("selects one design and mounts only its 7-skill detail on the right", async () => {
    const host = document.createElement("div"); document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(
      <EmbroideryColorwayStage workflow="products" active={false} loading={false}
        data={page} query="" syncing={false} onSync={() => undefined}
        onQueryChange={() => undefined} onPageChange={() => undefined}
        onPageSizeChange={() => undefined} />,
    ));
    expect(host.querySelectorAll('[data-testid="product-workflow"]')).toHaveLength(1);
    expect(host.querySelector('[data-testid="product-workflow"]')?.getAttribute("data-plan-id"))
      .toBe("design-one");
    const second = host.querySelectorAll<HTMLButtonElement>(".rrugc-products-source-tree-item")[1];
    await act(async () => second.click());
    expect(host.querySelector('[data-testid="product-workflow"]')?.getAttribute("data-plan-id"))
      .toBe("design-two");
    expect(host.querySelector(".rrugc-products-selected-head")).toBeNull();
    expect(host.querySelectorAll('[data-testid="product-workflow"]')).toHaveLength(1);
    await act(async () => root.unmount());
  });
});
