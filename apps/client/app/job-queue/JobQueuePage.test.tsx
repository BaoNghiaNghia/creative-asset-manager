import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../features/ai_operations", () => ({
  fetchAiOperationsJobQueue: vi.fn(),
}));

import { formatJobDuration, formatJobTime, formatJobType, GenerationResultModal, JobProviderIcon, JobQueuePage, jobStatusLabel } from "./JobQueuePage";
import type { AiOpsJob } from "../../features/ai_operations";

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
      "FIND A JOB", "All providers", "25 per page", "Square generation jobs", "Duration",
    ]) expect(markup).toContain(value);
    expect(markup).toContain('class="job-queue-header"');
    expect(markup).toContain('class="job-queue-tabs"');
    expect(markup).toContain('class="job-queue-toolbar"');
    expect(markup).toContain("Generate Square 1:1 jobs");
    expect(markup).toContain("Source image or job ID");
  });

  it("renders the completed generation comparison with original and generated images", () => {
    const job: AiOpsJob = {
      id: "job-1",
      job_type: "image_generate",
      entity_type: "asset",
      entity_id: "asset-1",
      asset_id: "asset-1",
      filename: "source.jpg",
      source_thumbnail_url: "/api/assets/asset-1/thumbnail",
      generated_image_url: "/api/assets/generated-1/content",
      provider: "cloudflare_sd",
      ai_model: "@cf/runwayml/stable-diffusion-v1-5-inpainting",
      status: "completed",
      priority: 0,
      attempt_count: 1,
      max_attempts: 8,
      processing_duration_ms: 1000,
      next_attempt_at: null,
      is_deferred: false,
      waiting_reason: null,
      claimed_at: null,
      lease_expires_at: null,
      created_at: "2026-09-02T10:00:00Z",
      updated_at: "2026-09-02T10:00:01Z",
      completed_at: "2026-09-02T10:00:01Z",
      error: null,
    };
    const markup = renderToStaticMarkup(<GenerationResultModal job={job} onClose={() => undefined} />);

    expect(markup).toContain('role="dialog"');
    expect(markup).toContain('aria-modal="true"');
    expect(markup).toContain("Generation result");
    expect(markup).toContain("Original");
    expect(markup).toContain("Generated");
    expect(markup).toContain('src="/api/assets/asset-1/thumbnail"');
    expect(markup).toContain('src="/api/assets/generated-1/content"');
    expect(markup).toContain("Open full size");
  });

  it("renders branded provider icons and formats accumulated active duration", () => {
    const cloudflare = renderToStaticMarkup(<JobProviderIcon provider="cloudflare_sd" />);
    const gemini = renderToStaticMarkup(<JobProviderIcon provider="gemini" />);
    const firefly = renderToStaticMarkup(<JobProviderIcon provider="adobe_firefly" />);

    expect(cloudflare).toContain('class="job-provider-icon cloudflare"');
    expect(gemini).toContain("gemini-sparkle");
    expect(firefly).toContain("/brands/adobe-firefly.svg");
    expect(formatJobDuration({ status: "completed", claimed_at: null, processing_duration_ms: 1_500 })).toBe("1.5 s");
    expect(formatJobDuration({
      status: "processing",
      claimed_at: "2026-09-02T10:00:00.000Z",
      processing_duration_ms: 1_000,
    }, new Date("2026-09-02T10:00:02.000Z").valueOf())).toBe("3.0 s");
    expect(formatJobDuration({ status: "pending", claimed_at: null, processing_duration_ms: 0 })).toBe("—");
  });

  it("formats job types, times and deferred statuses safely", () => {
    expect(formatJobType("image_generate")).toBe("Image Generate");
    expect(formatJobTime(null)).toBe("Not started");
    expect(formatJobTime("invalid")).toBe("Not available");
    expect(jobStatusLabel({ status: "pending", is_deferred: false })).toBe("Queued");
    expect(jobStatusLabel({ status: "pending", is_deferred: true })).toBe("Waiting");
    expect(jobStatusLabel({ status: "pending", is_deferred: true, waiting_reason: "gemini_image_quota_deferred" })).toBe("Waiting for Gemini quota");
  });
});
