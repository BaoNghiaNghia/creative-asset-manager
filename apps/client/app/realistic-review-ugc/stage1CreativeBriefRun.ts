import {
  generateCreativeV3Stage1Images, generateKeywordCreativeBrief, getKeywordCreativeBrief,
} from "./api";

/**
 * One explicit Stage 1 Redesign/Retry action. Never silently fall back to the
 * old keyword-only Skill when the tenant has Creative Brief ImageGen enabled.
 *
 * The already deployed server pins the completed brief's timestamp and
 * fingerprint to a six-slot processing job and preserves prior Output History.
 */
export async function queueStage1CreativeBriefRun(keywordId: string, keyword: string): Promise<
  "images_queued" | "brief_queued" | "brief_in_progress"
> {
  const detail = await getKeywordCreativeBrief(keywordId);
  if (detail.keyword !== keyword || (detail.brief && detail.brief.keyword_exact !== keyword)) {
    throw new Error("Keyword changed. Reload Stage 1 before starting ImageGen.");
  }
  if (detail.status === "completed") {
    if (!detail.brief || !detail.updated_at || detail.brief.concepts.length !== 3) {
      throw new Error("Creative Brief needs a valid three-concept revision. Re-analyze before generating.");
    }
    await generateCreativeV3Stage1Images(keywordId, detail.updated_at);
    return "images_queued";
  }
  if (detail.status === "queued" || detail.status === "running") {
    return "brief_in_progress";
  }
  // Missing, stale or failed analysis: queue text-only Codex, not legacy image
  // generation. The operator can use the already existing Creative Brief modal
  // to follow its status and start the six-image run when complete.
  await generateKeywordCreativeBrief(keywordId);
  return "brief_queued";
}
