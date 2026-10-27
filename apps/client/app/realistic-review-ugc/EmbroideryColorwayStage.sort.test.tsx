// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EmbroideryColorwayStage } from "./EmbroideryColorwayStage";
import type { SourcePlanPage } from "./types";
import type { SourcePlanSortBy, SourcePlanSortDirection } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => { document.body.replaceChildren(); });

const emptyPage: SourcePlanPage = {
  items: [], page: 1, page_size: 20, total: 0,
  overview: {
    embroidery_groups: 0, source_images: 0, working_groups: 0, refs_loaded: 0,
    stage2_groups: 0, stage2_source_images: 0, stage2_drive_ready_refs: 0,
    stage2_active_jobs: 0,
  },
};

describe.each(["products", "reviews"] as const)("Embroidery design Stage 2 sort (%s)", workflow => {
  it("sorts by the column header like Stage 0: ASC -> DESC -> ASC, with accessible direction", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const onSortChange = vi.fn<(key: SourcePlanSortBy, direction: SourcePlanSortDirection) => void>();
    const props = {
      workflow, data: emptyPage, active: false, loading: false, syncing: false,
      query: "", onSortChange, onSync: () => undefined,
      onQueryChange: () => undefined, onPageChange: () => undefined,
      onPageSizeChange: () => undefined,
    } as const;
    const header = () => host.querySelector<HTMLElement>(workflow === "products"
      ? ".rrugc-products-source-tree-sort[aria-sort]"
      : ".rrugc-colorway-table th[aria-sort]")!;
    const button = () => header().querySelector<HTMLButtonElement>(".rrugc-colorway-design-sort")!;
    const render = async (sortBy: SourcePlanSortBy, sortDirection: SourcePlanSortDirection) => {
      await act(async () => root.render(
        <EmbroideryColorwayStage {...props} sortBy={sortBy} sortDirection={sortDirection} />,
      ));
    };

    await render("source", "asc");
    expect(header().getAttribute("aria-sort")).toBe("ascending");
    expect(button().getAttribute("aria-label")).toBe("Sort by Embroidery design descending");
    expect(button().querySelector(".rrugc-sort-indicator")?.getAttribute("data-sort-state")).toBe("asc");
    await act(async () => button().click());
    expect(onSortChange).toHaveBeenLastCalledWith("source", "desc");

    await render("source", "desc");
    expect(header().getAttribute("aria-sort")).toBe("descending");
    expect(button().getAttribute("aria-label")).toBe("Sort by Embroidery design ascending");
    expect(button().querySelector(".rrugc-sort-indicator")?.getAttribute("data-sort-state")).toBe("desc");
    await act(async () => button().click());
    expect(onSortChange).toHaveBeenLastCalledWith("source", "asc");

    await render("updated", "desc");
    expect(header().getAttribute("aria-sort")).toBe("none");
    expect(button().querySelector(".rrugc-sort-indicator")?.getAttribute("data-sort-state")).toBe("none");
    await act(async () => button().click());
    expect(onSortChange).toHaveBeenLastCalledWith("source", "asc");
    expect(onSortChange).toHaveBeenCalledTimes(3);
    await act(async () => root.unmount());
  });
});
