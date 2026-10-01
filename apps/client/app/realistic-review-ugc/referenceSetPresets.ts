import type { Campaign, GenerationSkill, ReferenceAsset } from "./types";

export type ReferenceRoleSlot = {
  role: string;
  required: boolean;
};

export function referenceRoleSlots(skill: GenerationSkill | null): ReferenceRoleSlot[] {
  if (!skill) return [];
  const seen = new Set<string>();
  const slots: ReferenceRoleSlot[] = [];
  for (const role of skill.required_reference_roles) {
    if (!seen.has(role)) {
      seen.add(role);
      slots.push({ role, required: true });
    }
  }
  for (const role of skill.optional_reference_roles) {
    if (!seen.has(role)) {
      seen.add(role);
      slots.push({ role, required: false });
    }
  }
  return slots;
}

function preferredReferenceTypes(role: string): string[] {
  const token = role.toLowerCase();
  if (token.includes("artwork") || token.includes("logo")) {
    return ["artwork", "detail", "product"];
  }
  if (token.includes("detail")) return ["detail", "product", "artwork"];
  if (
    token.includes("product")
    || token.includes("front")
    || token.includes("side")
    || token.includes("back")
  ) {
    return ["product", "detail", "artwork"];
  }
  if (token.includes("scene")) return ["scene", "person", "other"];
  if (token.includes("person")) return ["person", "scene", "other"];
  return [];
}

export function referenceAssetsForRole(
  assets: ReferenceAsset[],
  role: string,
  campaignId: string,
): ReferenceAsset[] {
  const preferred = preferredReferenceTypes(role);
  const typeRank = new Map(preferred.map((type, index) => [type, index]));
  return [...assets].sort((left, right) => {
    const leftType = typeRank.get(left.reference_type) ?? preferred.length + 1;
    const rightType = typeRank.get(right.reference_type) ?? preferred.length + 1;
    if (leftType !== rightType) return leftType - rightType;

    const leftCampaign = left.source_campaign_id === campaignId ? 0 : 1;
    const rightCampaign = right.source_campaign_id === campaignId ? 0 : 1;
    if (leftCampaign !== rightCampaign) return leftCampaign - rightCampaign;

    const leftQuality = left.quality_score ?? -1;
    const rightQuality = right.quality_score ?? -1;
    if (leftQuality !== rightQuality) return rightQuality - leftQuality;

    return right.updated_at.localeCompare(left.updated_at);
  });
}

export function defaultReferenceSetName(
  campaign: Campaign,
  skill: GenerationSkill | null,
): string {
  const subject = campaign.product_sku || campaign.name || "Campaign";
  return skill ? subject + " · " + skill.display_name : subject + " · Reference set";
}

export function missingRequiredPresetRoles(
  skill: GenerationSkill | null,
  roleAssetIds: Record<string, string>,
): string[] {
  if (!skill) return [];
  return skill.required_reference_roles.filter(role => !roleAssetIds[role]);
}
