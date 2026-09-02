import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../features/ai_operations", () => ({
  fetchAiOperationsJobQueue: vi.fn(),
}));

import { formatJobTime, formatJobType, JobQueuePage, jobStatusLabel } from "./JobQueuePage";

describe("JobQueuePage", () => {
  it("renders the shared workspace sidebar with Job Queue active", () => {
    const markup = renderToStaticMarkup(<JobQueuePage />);

    expect(markup).toContain('class="ops-shell job-queue-shell"');
    expect(markup).toContain('class="ops-sidebar"');
    expect(markup).toContain('href="/job-queue"');
    expect(markup).toContain('aria-current="page"');
    expect(markup).toContain("Asset Explorer");
    expect(markup).toContain("AI Operations");
    expect(markup).toContain("Access Management");
  });

  it("uses the access-management visual hierarchy and complete job controls", () => {
    const markup = renderToStaticMarkup(<JobQueuePage />);
    for (const value of [
      "OPERATIONS", "Job Queue", "Workspace - Creative Assets", "Back to assets",
      "All generations", "Queued", "Running", "Completed", "Failed",
      "FIND A JOB", "All providers", "25 per page", "Square generation jobs",
    ]) expect(markup).toContain(value);
    expect(markup).toContain('class="job-queue-header"');
    expect(markup).toContain('class="job-queue-tabs"');
    expect(markup).toContain('class="job-queue-toolbar"');
    expect(markup).toContain("Generate Square 1:1 jobs");
    expect(markup).toContain("Source image or job ID");
  });

  it("formats job types, times and deferred statuses safely", () => {
    expect(formatJobType("image_generate")).toBe("Image Generate");
    expect(formatJobTime(null)).toBe("Not started");
    expect(formatJobTime("invalid")).toBe("Not available");
    expect(jobStatusLabel({ status: "pending", is_deferred: false })).toBe("Queued");
    expect(jobStatusLabel({ status: "pending", is_deferred: true })).toBe("Waiting");
  });
});
