export type RrugcStageTab = "stage0" | "stage1" | "stage2" | "reviews-stage2" | "stage3" | "stage4" | "stage5";
export type RrugcWorkflowGroup = "products" | "reviews";

export const RRUGC_STAGE_TABS: ReadonlyArray<{
  id: RrugcStageTab;
  label: string;
  description: string;
  marker: string;
}> = [
  { id: "stage0", label: "Stage 0", description: "Analysis Keyword", marker: "0" },
  { id: "stage1", label: "Stage 1", description: "Keyword Image Generation", marker: "1" },
  { id: "stage2", label: "Stage 2", description: "Selected Stage 1 Designs → 13 Colors", marker: "2" },
  { id: "reviews-stage2", label: "Stage 2", description: "embroidery_ Files → 13 Colors", marker: "2" },
  { id: "stage3", label: "Stage 3", description: "Pinterest References", marker: "3" },
  { id: "stage4", label: "Stage 4", description: "Image Generation", marker: "4" },
  { id: "stage5", label: "Stage 5", description: "UGC Review", marker: "5" },
];

export const RRUGC_WORKFLOW_GROUPS = [
  { id: "products", title: "GatorHats Products", subtitle: "Stage 0–2", stages: ["stage0", "stage1", "stage2"] },
  { id: "reviews", title: "GatorHats Reviews", subtitle: "Stage 2–5", stages: ["reviews-stage2", "stage3", "stage4", "stage5"] },
] as const;

export function workflowGroupForStage(stage: RrugcStageTab): RrugcWorkflowGroup {
  return stage === "stage0" || stage === "stage1" || stage === "stage2" ? "products" : "reviews";
}

export function stagesForWorkflowGroup(group: RrugcWorkflowGroup) {
  const stages: readonly RrugcStageTab[] = group === "products"
    ? ["stage0", "stage1", "stage2"] : ["reviews-stage2", "stage3", "stage4", "stage5"];
  return RRUGC_STAGE_TABS.filter(item => stages.includes(item.id));
}

export function defaultStageForGroup(group: RrugcWorkflowGroup): RrugcStageTab {
  return group === "products" ? "stage0" : "reviews-stage2";
}

export function rrugcWorkflowHref(group: RrugcWorkflowGroup, stage: RrugcStageTab = defaultStageForGroup(group)): string {
  const selected = workflowGroupForStage(stage) === group ? stage : defaultStageForGroup(group);
  return "/realistic-review-ugc?workflow=" + group + "&stage=" + selected;
}

/** Old deep links with only ?stage=stageN remain supported. Group wins if both disagree. */
export function rrugcStageFromSearch(search: string): RrugcStageTab {
  const query = new URLSearchParams(search);
  const requestedGroup = query.get("workflow");
  const rawStage = query.get("stage");
  const stage = RRUGC_STAGE_TABS.find(item => item.id === rawStage)?.id;
  const group = requestedGroup === "reviews" || requestedGroup === "products" ? requestedGroup : null;
  if (group) return stage && workflowGroupForStage(stage) === group ? stage : defaultStageForGroup(group);
  return stage ?? "stage0";
}

export function currentRrugcStage(): RrugcStageTab {
  return rrugcStageFromSearch(typeof window === "undefined" ? "" : window.location.search);
}

/** Preserve unrelated query parameters, but never create history entries on every Stage click. */
export function replaceRrugcStageInUrl(stage: RrugcStageTab) {
  if (typeof window === "undefined" || window.location.pathname !== "/realistic-review-ugc") return;
  const url = new URL(window.location.href);
  url.searchParams.set("workflow", workflowGroupForStage(stage));
  url.searchParams.set("stage", stage);
  window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
}
