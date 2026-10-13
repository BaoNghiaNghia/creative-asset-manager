// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Stage2OutputReviewModal } from "./Stage2JobTable";
import type { SourcePlan, Stage2Job } from "./types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => { document.body.replaceChildren(); document.body.style.overflow = ""; });

const plan = { id: "hat-plan", source_name: "Happy Ever After Hat" } as SourcePlan;
function job(id: string, status: Stage2Job["status"] = "completed", root: string | null = null): Stage2Job {
  return {
    id, source_plan_id: plan.id, status, regenerated_from_job_id: root,
    output_web_url: "https://drive.example/" + id,
    created_at: "2026-10-13T04:30:00Z",
    completed_at: status === "completed" ? "2026-10-13T04:31:00Z" : null,
  } as Stage2Job;
}

describe("Stage 4 generated-output regenerate confirmation", () => {
  it("requires explicit confirmation, queues once and leaves original image visible", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const original = job("old-image");
    const newJob = job("new-image", "queued", original.id);
    const regenerate = vi.fn(async () => newJob);
    const close = vi.fn();
    await act(async () => root.render(<Stage2OutputReviewModal
      plan={plan} jobs={[original]} onRegenerateJob={regenerate} onClose={close} />));
    const regenerateButton = () => host.querySelector<HTMLButtonElement>('button[aria-label="Regenerate output 1"]')!;
    expect(regenerateButton()).not.toBeNull();
    await act(async () => regenerateButton().click());
    expect(regenerate).not.toHaveBeenCalled();
    expect(host.querySelector('[aria-labelledby="rrugc-stage4-regenerate-title"]')).not.toBeNull();
    await act(async () => host.querySelector<HTMLButtonElement>(".rrugc-stage4-regenerate-confirm button:not(.is-accept)")!.click());
    expect(regenerate).not.toHaveBeenCalled();
    await act(async () => regenerateButton().click());
    await act(async () => {
      host.querySelector<HTMLButtonElement>(".rrugc-stage4-regenerate-confirm .is-accept")!.click();
      await Promise.resolve();
    });
    expect(regenerate).toHaveBeenCalledTimes(1);
    expect(regenerate).toHaveBeenCalledWith(original);
    expect(close).not.toHaveBeenCalled();
    expect(host.textContent).toContain("Regeneration queued");
    expect(host.textContent).toContain("1 generated output");
    await act(async () => root.render(<Stage2OutputReviewModal
      plan={plan} jobs={[original]} historyJobs={[original, newJob]}
      onRegenerateJob={regenerate} onClose={close} />));
    expect(regenerateButton().disabled).toBe(true);
    await act(async () => root.unmount());
  });

  it("keeps confirmation open and original output unchanged when API rejects", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const original = job("old-image");
    const regenerate = vi.fn(async () => { throw new Error("Original Skill version is unavailable"); });
    await act(async () => root.render(<Stage2OutputReviewModal
      plan={plan} jobs={[original]} onRegenerateJob={regenerate} onClose={() => undefined} />));
    await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Regenerate output 1"]')!.click());
    await act(async () => {
      host.querySelector<HTMLButtonElement>(".rrugc-stage4-regenerate-confirm .is-accept")!.click();
      await Promise.resolve();
    });
    expect(host.querySelector(".rrugc-stage4-regenerate-confirm")).not.toBeNull();
    expect(host.textContent).toContain("Original Skill version is unavailable");
    expect(host.textContent).toContain("1 generated output");
    await act(async () => root.unmount());
  });
});
