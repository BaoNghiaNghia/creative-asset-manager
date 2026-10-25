import type { RrugcWorkflowGroup } from "./workflowRouting";

/** One Skill per Ecommerce image, plus one per Multi-hat, UGC, and colorway group.
 * Photo workers remain gated; these rows configure Skill assignments only. */
export const PRODUCT_STAGE2_SKILL_SLOTS = [
  { key: "prod_s2_ec1", title: "Skill 1", label: "Ecommerce · Image 01", detail: "Front High · 1 photo output", expectedSkill: null, active: true },
  { key: "prod_s2_ec2", title: "Skill 2", label: "Ecommerce · Image 02", detail: "Front Right 3/4 · 1 photo output", expectedSkill: null, active: true },
  { key: "prod_s2_ec3", title: "Skill 3", label: "Ecommerce · Image 03", detail: "Right Side · 1 photo output", expectedSkill: null, active: true },
  { key: "prod_s2_ec4", title: "Skill 4", label: "Ecommerce · Image 04", detail: "Back High · 1 photo output", expectedSkill: null, active: true },
  { key: "prod_s2_mh", title: "Skill 5", label: "Multi-hat", detail: "Images 05–07 · 3 photo outputs", expectedSkill: "gatorhats-8869-multihat-v2-4", active: true },
  { key: "prod_s2_ugc", title: "Skill 6", label: "UGC", detail: "Images 08–10 · 3 photo outputs", expectedSkill: "gatorhats-8869-ugc-v3", active: true },
  { key: "prod_s2_col", title: "Skill 7", label: "12 Hat Colors", detail: "12 Natural colorway outputs · existing worker", expectedSkill: null, active: true },
] as const;

export type ProductStage2SkillKey = typeof PRODUCT_STAGE2_SKILL_SLOTS[number]["key"];
export type WorkflowSkillStage = ProductStage2SkillKey | "prod_s1" | "rev_s2" | "rev_s4";

export const WORKFLOW_SKILL_SLOTS: Record<RrugcWorkflowGroup, ReadonlyArray<{
  key: WorkflowSkillStage;
  title: string;
  label: string;
  detail: string;
  expectedSkill: string | null;
  active: boolean;
}>> = {
  products: [
    { key: "prod_s1", title: "Stage 1", label: "Six Designs", detail: "Keyword image generation", expectedSkill: null, active: true },
    ...PRODUCT_STAGE2_SKILL_SLOTS,
  ],
  reviews: [
    { key: "rev_s2", title: "Stage 2", label: "13 Hat Colors", detail: "Embroidery source colorways", expectedSkill: null, active: true },
    { key: "rev_s4", title: "Stage 4", label: "UGC Images", detail: "Pinterest references → review images", expectedSkill: null, active: true },
  ],
};

/** Preserve old product group assignments read-only; do not mistake a bulk
 * Ecommerce Skill for one of the four independent single-image jobs. */
export function skillDefaultForStage(defaults: Record<string, string> | undefined, stage: WorkflowSkillStage): string {
  if (defaults && Object.hasOwn(defaults, stage)) return defaults[stage] || "";
  const legacy: Partial<Record<WorkflowSkillStage, string>> = {
    prod_s1: "stage1", prod_s2_mh: "prod_s2_2", prod_s2_ugc: "prod_s2_3",
    prod_s2_col: "prod_s2_4", rev_s2: "stage2", rev_s4: "stage4",
  };
  if (stage === "prod_s2_col" && defaults && !Object.hasOwn(defaults, "prod_s2_4"))
    return defaults.stage2 || "";
  return defaults?.[legacy[stage] || ""] || "";
}