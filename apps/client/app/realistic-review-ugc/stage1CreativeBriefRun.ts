import {
  generateCreativeV3Stage1Images, regenerateCreativeV3Stage1Images,
  retryCreativeV3Stage1Images, generateKeywordCreativeBrief, getKeywordCreativeBrief,
} from "./api";

export type Stage1ImageAction = "generate" | "regenerate" | "retry";

/**
 * Explicit Stage 1 actions:
 * - Generate: first 6-image V3 batch.
 * - Regenerate: NEW 6-image V3 batch with a new processing ID.
 * - Retry: resume the SAME failed V3 processing ID (checkpoints and uploads).
 * Never fall back to Google Fonts, concept-short, or any legacy Skill.
 */
export async function queueStage1CreativeBriefRun(
  keywordId: string, keyword: string, action: Stage1ImageAction = "generate",
  expectedProcessingId?: string | null,
): Promise<"images_queued" | "brief_queued" | "brief_in_progress"> {
  const detail = await getKeywordCreativeBrief(keywordId);
  if (detail.keyword !== keyword || (detail.brief && detail.brief.keyword_exact !== keyword)) {
    throw new Error("Keyword changed. Reload Stage 1 before starting ImageGen.");
  }
  if (detail.status === "completed") {
    if (!detail.brief || !detail.updated_at || detail.brief.concepts.length !== 3) {
      throw new Error("Creative Brief needs a valid three-concept revision. Re-analyze before generating.");
    }
    if (action === "retry") {
      if (!expectedProcessingId) {
        throw new Error("Cannot retry without the failed V3 processing attempt. Refresh Stage 1.");
      }
      await retryCreativeV3Stage1Images(keywordId, detail.updated_at, expectedProcessingId);
    } else if (action === "regenerate") {
      await regenerateCreativeV3Stage1Images(keywordId, detail.updated_at);
    } else {
      await generateCreativeV3Stage1Images(keywordId, detail.updated_at);
    }
    return "images_queued";
  }
  if (action === "retry") {
    throw new Error("The original Creative Brief is no longer ready. Review the brief and Regenerate instead.");
  }
  if (detail.status === "queued" || detail.status === "running") {
    return "brief_in_progress";
  }
  // Only Generate / Regenerate may queue a missing text-only analysis.
  // Analysis does not silently schedule image calls.
  await generateKeywordCreativeBrief(keywordId);
  return "brief_queued";
}
