// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Stage2ReferenceReviewModal } from "./Stage2JobTable";
import { GenerationOutputVersionsDialog } from "./GenerationOutputVersionsDialog";
import type { SourcePlanReferencePreview } from "./types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("./api", async importOriginal => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, listGenerationOutputVersions: vi.fn(async () => ({
    versions: [1, 2, 3].map(version => ({
      version, url: "https://example.test/output-" + version + ".png",
      created_at: "2026-10-09T05:00:00Z", width: 1024, height: 1024,
    })),
  })) };
});

afterEach(() => { document.body.style.overflow = ""; document.body.replaceChildren(); });

describe("Stage 4 bulk reference selection", () => {
  it("offers icon-only Select all and Deselect while leaving already generated refs locked", async () => {
    const refs = [1, 2, 3].map(id => ({
      id: String(id), image_url: "https://example.test/reference-" + id + ".jpg",
      pin_url: "https://www.pinterest.com/pin/" + id,
      status: "drive_ready", rejected: false,
    })) as SourcePlanReferencePreview[];
    const onBulkSelection = vi.fn();
    const rootHost = document.createElement("div");
    document.body.append(rootHost);
    const root = createRoot(rootHost);
    const render = (selected: string[], busy: boolean) =>
      <Stage2ReferenceReviewModal planId="plan-1" planName="Test cap" references={refs}
        selected={selected} generated={new Set(["2"])} busy={busy}
        onToggle={() => undefined} onBulkSelection={onBulkSelection} onClose={() => undefined} />;
    await act(async () => root.render(render(["1"], false)));
    const all = document.querySelector<HTMLButtonElement>('button[aria-label="Select all available references"]')!;
    const none = document.querySelector<HTMLButtonElement>('button[aria-label="Deselect all references"]')!;
    expect(all.disabled).toBe(false);
    expect(none.disabled).toBe(false);
    expect(document.querySelectorAll('[data-ref-action="select-all"]')).toHaveLength(1);
    expect(document.querySelector('button[aria-label="Reference 2 already generated"]')?.hasAttribute("disabled")).toBe(true);
    await act(async () => all.click());
    expect(onBulkSelection).toHaveBeenLastCalledWith("plan-1", refs, new Set(["2"]), "all");
    await act(async () => none.click());
    expect(onBulkSelection).toHaveBeenLastCalledWith("plan-1", refs, new Set(["2"]), "none");
    await act(async () => root.render(render(["1"], true)));
    expect(document.querySelector<HTMLButtonElement>('button[aria-label="Select all available references"]')?.disabled).toBe(true);
    await act(async () => root.unmount());
  });
});

describe("Version history comparison", () => {
  it("compares versions horizontally with individual, all and none controls", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const regenerate = vi.fn();
    await act(async () => { root.render(<GenerationOutputVersionsDialog stage="stage4" jobId="job-1"
      title="Cap art" onClose={() => undefined} onRegenerate={regenerate} />); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(document.querySelectorAll(".rrugc-version-compare-card")).toHaveLength(3);
    expect(document.querySelector('[aria-label="Side-by-side version comparison"]')).not.toBeNull();
    const deselect = document.querySelector<HTMLButtonElement>('button[aria-label="Deselect all versions"]')!;
    const selectAll = document.querySelector<HTMLButtonElement>('button[aria-label="Select all versions"]')!;
    await act(async () => deselect.click());
    expect(document.querySelectorAll(".rrugc-version-compare-card")).toHaveLength(0);
    expect(document.body.textContent).toContain("Select versions above to compare");
    await act(async () => (document.querySelector('button[aria-label="Compare version 2"]') as HTMLButtonElement).click());
    expect(document.querySelectorAll(".rrugc-version-compare-card")).toHaveLength(1);
    expect(document.querySelector<HTMLAnchorElement>('.rrugc-version-compare-card .rrugc-version-image-link')?.href).toContain("output-2.png");
    await act(async () => selectAll.click());
    expect(document.querySelectorAll(".rrugc-version-compare-card")).toHaveLength(3);
    await act(async () => (document.querySelector(".rrugc-version-create") as HTMLButtonElement).click());
    expect(regenerate).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
  });
});
