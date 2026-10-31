import { beforeEach, describe, expect, it, vi } from "vitest";
import { queueStage1CreativeBriefRun } from "./stage1CreativeBriefRun";
import {
  getKeywordCreativeBrief, generateCreativeV3Stage1Images, regenerateCreativeV3Stage1Images, retryCreativeV3Stage1Images, generateKeywordCreativeBrief,
} from "./api";

vi.mock("./api", () => ({
  getKeywordCreativeBrief: vi.fn(),
  generateCreativeV3Stage1Images: vi.fn(),
  regenerateCreativeV3Stage1Images: vi.fn(),
  retryCreativeV3Stage1Images: vi.fn(),
  generateKeywordCreativeBrief: vi.fn(),
}));

const detail = (status: "completed" | "not_analyzed" | "stale" | "failed" | "queued" | "running") => ({
  keyword_id: "key-one",
  keyword: "CREATIVE DICTATOR",
  status,
  updated_at: "2026-10-31T01:00:00+07:00",
  prompt_version: "meaning-first-v1",
  error_code: null,
  brief: status === "completed" ? {
    keyword_exact: "CREATIVE DICTATOR", strategy: "", meaning_emotion: "",
    target_audience: "", purchase_motivation: "", creative_hook: "",
    concepts: [{}, {}, {}],
  } : null,
});

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getKeywordCreativeBrief).mockResolvedValue(detail("completed") as never);
  vi.mocked(generateCreativeV3Stage1Images).mockResolvedValue({
    keyword_id: "key-one", job_id: "job-new", status: "queued",
    queued: true, expected_output_count: 6,
  });
  vi.mocked(regenerateCreativeV3Stage1Images).mockResolvedValue({
    keyword_id: "key-one", job_id: "job-new", processing_job_id: "new-p",
    queued: true, action: "regenerate",
  });
  vi.mocked(retryCreativeV3Stage1Images).mockResolvedValue({
    keyword_id: "key-one", job_id: "job-new", processing_job_id: "old-p",
    queued: true, action: "retry",
  });
  vi.mocked(generateKeywordCreativeBrief).mockResolvedValue({
    keyword_id: "key-one", status: "queued", queued: true,
  });
});

describe("Stage 1 Generate, Regenerate and Retry dispatch different V3 endpoints", () => {
  it("pins a completed three-concept brief and queues six ImageGen outputs", async () => {
    expect(await queueStage1CreativeBriefRun("key-one", "CREATIVE DICTATOR")).toBe("images_queued");
    expect(generateCreativeV3Stage1Images).toHaveBeenCalledOnce();
    expect(generateCreativeV3Stage1Images).toHaveBeenCalledWith("key-one", "2026-10-31T01:00:00+07:00");
    expect(generateKeywordCreativeBrief).not.toHaveBeenCalled();
  });

  it("Regenerate creates a fresh V3 processing attempt without calling Generate or Retry", async () => {
    expect(await queueStage1CreativeBriefRun("key-one", "CREATIVE DICTATOR", "regenerate")).toBe("images_queued");
    expect(regenerateCreativeV3Stage1Images).toHaveBeenCalledWith(
      "key-one", "2026-10-31T01:00:00+07:00",
    );
    expect(retryCreativeV3Stage1Images).not.toHaveBeenCalled();
    expect(generateCreativeV3Stage1Images).not.toHaveBeenCalled();
  });

  it("Retry reuses the exact failed V3 processing ID, never a new generation endpoint", async () => {
    const id="123e4567-e89b-12d3-a456-426614174000";
    expect(await queueStage1CreativeBriefRun("key-one", "CREATIVE DICTATOR", "retry", id)).toBe("images_queued");
    expect(retryCreativeV3Stage1Images).toHaveBeenCalledWith(
      "key-one", "2026-10-31T01:00:00+07:00", id,
    );
    expect(regenerateCreativeV3Stage1Images).not.toHaveBeenCalled();
    expect(generateCreativeV3Stage1Images).not.toHaveBeenCalled();
  });

  it("fails closed when a failed V3 processing ID is missing or the brief needs reanalysis", async () => {
    await expect(queueStage1CreativeBriefRun("key-one", "CREATIVE DICTATOR", "retry"))
      .rejects.toThrow("failed V3 processing attempt");
    vi.mocked(getKeywordCreativeBrief).mockResolvedValue(detail("stale") as never);
    await expect(queueStage1CreativeBriefRun("key-one", "CREATIVE DICTATOR", "retry", "old-id"))
      .rejects.toThrow("Regenerate");
    expect(regenerateCreativeV3Stage1Images).not.toHaveBeenCalled();
    expect(retryCreativeV3Stage1Images).not.toHaveBeenCalled();
    expect(generateKeywordCreativeBrief).not.toHaveBeenCalled();
  });

  it.each(["not_analyzed", "stale", "failed"] as const)("queues missing or invalid %s analysis, never legacy generation", async status => {
    vi.mocked(getKeywordCreativeBrief).mockResolvedValue(detail(status) as never);
    expect(await queueStage1CreativeBriefRun("key-one", "CREATIVE DICTATOR")).toBe("brief_queued");
    expect(generateKeywordCreativeBrief).toHaveBeenCalledWith("key-one");
    expect(generateCreativeV3Stage1Images).not.toHaveBeenCalled();
  });

  it.each(["queued", "running"] as const)("does not double-queue %s analysis", async status => {
    vi.mocked(getKeywordCreativeBrief).mockResolvedValue(detail(status) as never);
    expect(await queueStage1CreativeBriefRun("key-one", "CREATIVE DICTATOR")).toBe("brief_in_progress");
    expect(generateKeywordCreativeBrief).not.toHaveBeenCalled();
    expect(generateCreativeV3Stage1Images).not.toHaveBeenCalled();
  });

  it("fails closed on changed exact quote or invalid brief", async () => {
    vi.mocked(getKeywordCreativeBrief).mockResolvedValue({
      ...detail("completed"), keyword: "CHANGED",
    } as never);
    await expect(queueStage1CreativeBriefRun("key-one", "CREATIVE DICTATOR")).rejects.toThrow("Keyword changed");
    vi.mocked(getKeywordCreativeBrief).mockResolvedValue({
      ...detail("completed"), brief: { ...detail("completed").brief, concepts: [{}] },
    } as never);
    await expect(queueStage1CreativeBriefRun("key-one", "CREATIVE DICTATOR")).rejects.toThrow("three-concept");
    expect(generateCreativeV3Stage1Images).not.toHaveBeenCalled();
  });

  it("never retries the legacy Skill when V3 rejects an active job", async () => {
    vi.mocked(generateCreativeV3Stage1Images).mockRejectedValue(new Error("A Stage 1 run is already active"));
    await expect(queueStage1CreativeBriefRun("key-one", "CREATIVE DICTATOR")).rejects.toThrow("already active");
    expect(generateKeywordCreativeBrief).not.toHaveBeenCalled();
  });
});