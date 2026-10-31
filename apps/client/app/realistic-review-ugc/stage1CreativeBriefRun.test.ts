import { beforeEach, describe, expect, it, vi } from "vitest";
import { queueStage1CreativeBriefRun } from "./stage1CreativeBriefRun";
import {
  getKeywordCreativeBrief, generateCreativeV3Stage1Images, generateKeywordCreativeBrief,
} from "./api";

vi.mock("./api", () => ({
  getKeywordCreativeBrief: vi.fn(),
  generateCreativeV3Stage1Images: vi.fn(),
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
  vi.mocked(generateKeywordCreativeBrief).mockResolvedValue({
    keyword_id: "key-one", status: "queued", queued: true,
  });
});

describe("Stage 1 Redesign and Retry use the same Creative Brief V3 path", () => {
  it("pins a completed three-concept brief and queues six ImageGen outputs", async () => {
    expect(await queueStage1CreativeBriefRun("key-one", "CREATIVE DICTATOR")).toBe("images_queued");
    expect(generateCreativeV3Stage1Images).toHaveBeenCalledOnce();
    expect(generateCreativeV3Stage1Images).toHaveBeenCalledWith("key-one", "2026-10-31T01:00:00+07:00");
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
