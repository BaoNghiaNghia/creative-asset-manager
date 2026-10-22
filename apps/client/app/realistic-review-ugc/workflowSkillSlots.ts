import type { RrugcWorkflowGroup } from "./workflowRouting";

/** Independent Stage 2 assignments; the three additional slots do not
 * create new photo outputs or activate workers until their workflow is wired. */
export const PRODUCT_STAGE2_SKILL_SLOTS = [
  { key: "prod_s2_1", title: "Skill 1", label: "Ecommerce", detail: "4 photo outputs", expectedSkill: "gatorhats-8869-ecommerce-v2-4", active: true },
  { key: "prod_s2_2", title: "Skill 2", label: "Multi-hat", detail: "3 photo outputs", expectedSkill: "gatorhats-8869-multihat-v2-4", active: true },
  { key: "prod_s2_3", title: "Skill 3", label: "UGC", detail: "3 photo outputs", expectedSkill: "gatorhats-8869-ugc-v3", active: true },
  { key: "prod_s2_4", title: "Skill 4", label: "13 Hat Colors", detail: "13 colorway outputs", expectedSkill: null, active: true },
  { key: "prod_s2_5", title: "Skill 5", label: "Additional Skill A", detail: "Reserved · no worker connected", expectedSkill: null, active: false },
  { key: "prod_s2_6", title: "Skill 6", label: "Additional Skill B", detail: "Reserved · no worker connected", expectedSkill: null, active: false },
  { key: "prod_s2_7", title: "Skill 7", label: "Additional Skill C", detail: "Reserved · no worker connected", expectedSkill: null, active: false },
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

/** Fallback for older read-only clients; new writes only use workflow keys. */
export function skillDefaultForStage(defaults: Record<string, string> | undefined, stage: WorkflowSkillStage): string {
  if (defaults && Object.hasOwn(defaults, stage)) return defaults[stage] || "";
  const legacy: Partial<Record<WorkflowSkillStage, string>> = {
    prod_s1: "stage1", prod_s2_4: "stage2", rev_s2: "stage2", rev_s4: "stage4",
  };
  return defaults?.[legacy[stage] || ""] || "";
}
