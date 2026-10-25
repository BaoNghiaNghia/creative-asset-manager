// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { EmbroideryColorwayStage } from "./EmbroideryColorwayStage";
import type { SourcePlan, SourcePlanPage } from "./types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => document.body.replaceChildren());

const plan = {
  id: "design-default-hat",
  source_name: "embroidery-example.png",
  source_relative_path: "designs/embroidery-example.png",
  source_preview_url: "/images/embroidery-example.png",
  source_revision: "test-v1",
  updated_at: "2026-10-25T10:00:00Z",
  status: "ready",
} as SourcePlan;
const data: SourcePlanPage = {
  items: [plan], page: 1, page_size: 20, total: 1,
  overview: {
    embroidery_groups: 1, source_images: 1, working_groups: 1, refs_loaded: 0,
    stage2_groups: 1, stage2_source_images: 1, stage2_drive_ready_refs: 0, stage2_active_jobs: 0,
  },
};

describe("Products Embroidery Design default hat color", () => {
  it("preselects natural charcoal before the stock catalog loads", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => {
        root.render(
          <EmbroideryColorwayStage
            data={data} workflow="products" active={false}
            loading={false} syncing={false} query=""
            onSync={() => undefined} onQueryChange={() => undefined}
            onPageChange={() => undefined} onPageSizeChange={() => undefined}
          />,
        );
      });
      const select = host.querySelector<HTMLSelectElement>('select[aria-label="Ecommerce stock color"]');
      expect(select).not.toBeNull();
      expect(select?.value).toBe("natural-charcoal");
      expect([...select!.options].map(option => option.value)).toContain("natural-charcoal");
      expect(select?.selectedOptions[0]?.textContent).toBe("natural charcoal");
    } finally {
      await act(async () => root.unmount());
    }
  });
});
