import type { RrugcWorkflowGroup } from "./workflowRouting";

/** Built-in reference-first inpaint for Ecommerce + Colors. Only the Multi-hat
 * and UGC scene-composition slots need configurable image Skills. */
export const PRODUCT_STAGE2_SKILL_SLOTS = [
  { key: "prod_s2_ec1", title: "Slot 1", label: "Front · Thread Inpaint V2", detail: "Built-in · 2 native ImageGen edits: fine thread reference → original cap inpaint · no custom Skill", expectedSkill: null, active: true },
  { key: "prod_s2_ec2", title: "Slot 2", label: "Three-quarter · Satin Inpaint", detail: "Built-in · reuse Slot 1 native thread material on the original 3/4 stock photo", expectedSkill: null, active: true },
  { key: "prod_s2_ec3", title: "Slot 3", label: "Right Side · Satin Inpaint", detail: "Built-in · perspective-limited front edge on original right-side stock photo", expectedSkill: null, active: true },
  { key: "prod_s2_ec4", title: "Slot 4", label: "Back · Original Stock", detail: "Built-in · exact original rear photograph; no invented rear embroidery", expectedSkill: null, active: true },
  { key: "prod_s2_mh", title: "Skill 5", label: "Multi-hat", detail: "Images 05–07 · 3 photo outputs", expectedSkill: "gatorhats-8869-multihat-v2-4", active: true },
  { key: "prod_s2_ugc", title: "Skill 6", label: "UGC", detail: "Images 08–10 · 3 photo outputs", expectedSkill: "gatorhats-8869-ugc-v3", active: true },
  { key: "prod_s2_col", title: "Slot 7", label: "12 Hat Colors · Satin Inpaint", detail: "Built-in · 12 original stock photos, one native inpaint per color using Slot 1 thread reference", expectedSkill: null, active: true },
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