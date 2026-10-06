import type {
  Campaign,
  CampaignCreateRequest,
  CampaignUpdateRequest,
  CampaignCreated,
  CampaignExportSummary,
  CampaignDeliverySummary,
  DeliveryDestination,
  DeliveryLifecycleReconcile,
  DeliveryMaintenanceEnqueue,
  DeliveryOperationsSummary,
  DeliveryPackage,
  DeliveryPackageList,
  Candidate,
  AiFeedbackCalibration,
  AiManualLabel,
  ReferenceManualLabel,
  ContextManualLabel,
  GenerationAttempt,
  GenerationCapability,
  GenerationSkillCatalog,
  KeywordVolumePage,
  ReferenceAsset,
  ReferenceSet,
  ReferenceSetRecommendation,
  BatchExportResult,
  ExportList,
  ExportRecord,
  Product,
  ProductCreateRequest,
  ProductReference,
  ProductReferenceView,
  ProductUpdateRequest,
  ProductVariant,
  ProductUrlImportResult,
  ReviewTask,
  ReviewTaskList,
  ReviewTaskTransition,
  ScoutAgent,
  ScoutAgentCreated,
  ScoutRun,
  SourcePlanPage,
  SourcePlanSyncResult,
  Stage2Job,
  Stage2JobCreated,
  Stage3AnalyzeResult,
  Stage3ReviewGroupList,
  Stage2Skill,
  Stage2SkillCatalog,
  Stage2SkillRegistry,
  Stage2SkillRegistryItem,
  Stage2SkillSelection,
  SupervisorResult,
} from "./types";

export class RrugcApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const multipart = typeof FormData !== "undefined" && init?.body instanceof FormData;
  const response = await fetch(url, {
    credentials: "include",
    ...init,
    headers: {
      ...(multipart ? {} : { "Content-Type": "application/json" }),
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    let message = "Request failed.";
    try {
      const payload = await response.json();
      message = payload?.detail?.message || payload?.detail || message;
    } catch {
      // Keep bounded fallback; no response body is required.
    }
    throw new RrugcApiError(response.status, String(message));
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}


export const listProducts = (signal?: AbortSignal) =>
  request<Product[]>("/api/v1/realistic-review-ugc/products", { signal });

export const createProduct = (body: ProductCreateRequest) =>
  request<Product>("/api/v1/realistic-review-ugc/products", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const updateProduct = (productId: string, body: ProductUpdateRequest) =>
  request<Product>("/api/v1/realistic-review-ugc/products/" + encodeURIComponent(productId), {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const importProductUrls = (urls: string[], importPrimaryImage = true) =>
  request<ProductUrlImportResult>("/api/v1/realistic-review-ugc/products/import-urls", {
    method: "POST",
    body: JSON.stringify({
      urls,
      import_primary_image: importPrimaryImage,
    }),
  });

export const updateProductVariant = (
  productId: string,
  variantId: string,
  enabled: boolean,
) =>
  request<ProductVariant>(
    "/api/v1/realistic-review-ugc/products/"
      + encodeURIComponent(productId)
      + "/variants/"
      + encodeURIComponent(variantId),
    {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    },
  );

export const archiveProduct = (productId: string) =>
  request<Product>("/api/v1/realistic-review-ugc/products/" + encodeURIComponent(productId), {
    method: "DELETE",
  });

export const listProductReferences = (productId: string, signal?: AbortSignal) =>
  request<ProductReference[]>(
    "/api/v1/realistic-review-ugc/products/" + encodeURIComponent(productId) + "/references",
    { signal },
  );

export const uploadProductReference = (
  productId: string,
  viewType: ProductReferenceView,
  file: File,
) => {
  const body = new FormData();
  body.set("view_type", viewType);
  body.set("file", file);
  return request<ProductReference>(
    "/api/v1/realistic-review-ugc/products/" + encodeURIComponent(productId) + "/references",
    { method: "POST", body },
  );
};

export const archiveProductReference = (productId: string, referenceId: string) =>
  request<ProductReference>(
    "/api/v1/realistic-review-ugc/products/" + encodeURIComponent(productId) + "/references/" + encodeURIComponent(referenceId),
    { method: "DELETE" },
  );

export type SourcePlanSortBy = "source" | "updated" | "analyzed" | "group_size" | "status";
export type SourcePlanSortDirection = "asc" | "desc";

export const listSourcePlans = (
  filters: {
    page?: number;
    pageSize?: number;
    query?: string;
    sortBy?: SourcePlanSortBy;
    sortDirection?: SourcePlanSortDirection;
    stage2Only?: boolean;
  } = {},
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({
    page: String(filters.page ?? 1),
    page_size: String(filters.pageSize ?? 20),
    sort_by: filters.sortBy ?? "source",
    sort_dir: filters.sortDirection ?? "asc",
  });
  if (filters.query?.trim()) params.set("q", filters.query.trim());
  if (filters.stage2Only) params.set("stage2_only", "true");
  return request<SourcePlanPage>(
    "/api/v1/realistic-review-ugc/source-plans?" + params.toString(),
    { signal },
  );
};

export const syncSourcePlans = () =>
  request<SourcePlanSyncResult>("/api/v1/realistic-review-ugc/source-plans/sync", {
    method: "POST",
  });

export const listKeywordAnalysis = (
  filters: { page?: number; pageSize?: number; query?: string } = {},
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({
    page: String(filters.page ?? 1),
    page_size: String(filters.pageSize ?? 20),
  });
  if (filters.query?.trim()) params.set("query", filters.query.trim());
  return request<KeywordVolumePage>(
    "/api/v1/realistic-review-ugc/keyword-analysis?" + params.toString(),
    { signal },
  );
};

export const listCampaigns = (signal?: AbortSignal) =>
  request<Campaign[]>("/api/v1/realistic-review-ugc/campaigns", { signal });

export const getCampaign = (campaignId: string, signal?: AbortSignal) =>
  request<Campaign>(
    "/api/v1/realistic-review-ugc/campaigns/" + encodeURIComponent(campaignId),
    { signal },
  );

export const createCampaign = (body: CampaignCreateRequest) =>
  request<CampaignCreated>("/api/v1/realistic-review-ugc/campaigns", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const updateCampaign = (campaignId: string, body: CampaignUpdateRequest) =>
  request<Campaign>(
    "/api/v1/realistic-review-ugc/campaigns/" + encodeURIComponent(campaignId),
    {
      method: "PATCH",
      body: JSON.stringify(body),
    },
  );

export const deleteCampaign = (campaignId: string) =>
  request<void>(
    "/api/v1/realistic-review-ugc/campaigns/" + encodeURIComponent(campaignId),
    { method: "DELETE" },
  );

export const configureCampaignScoutAutomation = (
  campaignId: string,
  autoScout: boolean,
  scanIntervalSeconds: number,
) =>
  request<Campaign>(
    "/api/v1/realistic-review-ugc/campaigns/"
      + encodeURIComponent(campaignId) + "/scout-automation",
    {
      method: "PUT",
      body: JSON.stringify({
        auto_scout: autoScout,
        scan_interval_seconds: scanIntervalSeconds,
      }),
    },
  );

export const listScoutAgents = (signal?: AbortSignal) =>
  request<ScoutAgent[]>("/api/v1/realistic-review-ugc/scout-agents", { signal });

export const createScoutAgent = (name: string) =>
  request<ScoutAgentCreated>("/api/v1/realistic-review-ugc/scout-agents", {
    method: "POST",
    body: JSON.stringify({ name }),
  });

export const archiveScoutAgent = (agentId: string) =>
  request<ScoutAgent>(
    "/api/v1/realistic-review-ugc/scout-agents/" + encodeURIComponent(agentId),
    { method: "DELETE" },
  );

export const listStage2Jobs = (
  sourcePlanIds: string[] = [],
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({ limit: "1000" });
  for (const sourcePlanId of Array.from(new Set(sourcePlanIds))) {
    if (sourcePlanId) params.append("source_plan_id", sourcePlanId);
  }
  return request<Stage2Job[]>(
    "/api/v1/realistic-review-ugc/stage2-jobs?" + params.toString(),
    { signal },
  );
};

export const listStage3ReviewGroups = (
  signal?: AbortSignal,
) =>
  request<Stage3ReviewGroupList>(
    "/api/v1/realistic-review-ugc/stage3/review-groups",
    { signal },
  );

export const analyzeStage3ReviewGroups = (
  folderId?: string,
  force = false,
) =>
  request<Stage3AnalyzeResult>(
    "/api/v1/realistic-review-ugc/stage3/review-groups/analyze",
    {
      method: "POST",
      body: JSON.stringify({
        folder_id: folderId || null,
        force,
      }),
    },
  );

export const listStage2Skills = (refresh = false, signal?: AbortSignal) =>
  request<Stage2SkillCatalog>(
    "/api/v1/realistic-review-ugc/stage2-skills?refresh=" + String(refresh),
    { signal },
  );

export const syncStage2Skill = (skillId: string, version?: string | null) =>
  request<Stage2Skill>(
    "/api/v1/realistic-review-ugc/stage2-skills/"
      + encodeURIComponent(skillId)
      + "/sync",
    {
      method: "POST",
      body: JSON.stringify({
        ...(version ? { version } : {}),
      }),
    },
  );

export const listStage2SkillRegistry = (refresh = false, signal?: AbortSignal) =>
  request<Stage2SkillRegistry>(
    "/api/v1/realistic-review-ugc/stage2-skills/registry?refresh=" + String(refresh),
    { signal },
  );

export const createStage2Skill = (file: File) => {
  const body = new FormData();
  body.set("file", file);
  return request<Stage2SkillRegistryItem>(
    "/api/v1/realistic-review-ugc/stage2-skills/registry",
    { method: "POST", body },
  );
};

export const createStage2SkillVersion = (
  registryId: string,
  file: File,
  makeDefault = false,
) => {
  const body = new FormData();
  body.set("file", file);
  return request<Stage2SkillRegistryItem>(
    "/api/v1/realistic-review-ugc/stage2-skills/registry/"
      + encodeURIComponent(registryId)
      + "/versions?make_default="
      + String(makeDefault),
    { method: "POST", body },
  );
};

export const setStage2SkillEnabled = (registryId: string, enabled: boolean) =>
  request<Stage2SkillRegistryItem>(
    "/api/v1/realistic-review-ugc/stage2-skills/registry/"
      + encodeURIComponent(registryId)
      + "/enabled",
    {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    },
  );

export const setStage2SkillDefaultVersion = (registryId: string, version: string) =>
  request<Stage2SkillRegistryItem>(
    "/api/v1/realistic-review-ugc/stage2-skills/registry/"
      + encodeURIComponent(registryId)
      + "/default",
    {
      method: "POST",
      body: JSON.stringify({ version }),
    },
  );

export const syncStage2SkillRegistry = (registryId: string, version?: string | null) =>
  request<Stage2SkillRegistryItem>(
    "/api/v1/realistic-review-ugc/stage2-skills/registry/"
      + encodeURIComponent(registryId)
      + "/sync",
    {
      method: "POST",
      body: JSON.stringify({ ...(version ? { version } : {}) }),
    },
  );

export const deleteStage2SkillVersion = (registryId: string, version: string) =>
  request<Stage2SkillRegistryItem>(
    "/api/v1/realistic-review-ugc/stage2-skills/registry/"
      + encodeURIComponent(registryId)
      + "/versions/"
      + encodeURIComponent(version),
    { method: "DELETE" },
  );

export const deleteStage2Skill = (registryId: string) =>
  request<{ deleted: boolean; registry_id: string }>(
    "/api/v1/realistic-review-ugc/stage2-skills/registry/"
      + encodeURIComponent(registryId),
    { method: "DELETE" },
  );

export const createStage2Job = (
  sourcePlanId: string,
  selectedCandidateIds: string[],
  skill: Stage2SkillSelection,
  prompt?: string,
) =>
  request<Stage2JobCreated>(
    "/api/v1/realistic-review-ugc/source-plans/"
      + encodeURIComponent(sourcePlanId)
      + "/stage2-jobs",
    {
      method: "POST",
      body: JSON.stringify({
        selected_candidate_ids: selectedCandidateIds,
        skill_source: skill.source,
        ...(skill.skill_id ? { skill_id: skill.skill_id } : {}),
        skill_name: skill.skill_name,
        ...(skill.skill_version ? { skill_version: skill.skill_version } : {}),
        ...(prompt?.trim() ? { prompt: prompt.trim() } : {}),
      }),
    },
  );

export const cancelStage2Jobs = (sourcePlanId: string) =>
  request<{ cancelled: number; job_ids: string[] }>(
    "/api/v1/realistic-review-ugc/source-plans/"
      + encodeURIComponent(sourcePlanId)
      + "/stage2-jobs/cancel",
    { method: "POST" },
  );

export const stage2JobOutputUrl = (jobId: string) =>
  "/api/v1/realistic-review-ugc/stage2-jobs/"
  + encodeURIComponent(jobId)
  + "/output";

export const stage2JobOutputThumbnailUrl = (jobId: string, size = 192) =>
  stage2JobOutputUrl(jobId)
  + "?thumbnail=true&size="
  + Math.min(1024, Math.max(128, Math.round(size)));

export const listScoutRuns = (
  campaignId?: string,
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({ limit: "50" });
  if (campaignId) params.set("campaign_id", campaignId);
  return request<ScoutRun[]>(
    "/api/v1/realistic-review-ugc/scout-runs?" + params.toString(),
    { signal },
  );
};

export const bindCampaignProduct = (
  campaignId: string,
  productId: string | null,
  variantIds?: string[],
) =>
  request<Campaign>(
    "/api/v1/realistic-review-ugc/campaigns/" + encodeURIComponent(campaignId) + "/product",
    {
      method: "PUT",
      body: JSON.stringify({
        product_id: productId,
        ...(variantIds ? { variant_ids: variantIds } : {}),
      }),
    },
  );

export const analyzeCampaignProductVisualContext = (campaignId: string) =>
  request<Campaign>(
    "/api/v1/realistic-review-ugc/campaigns/"
      + encodeURIComponent(campaignId)
      + "/product-context/visual-analysis",
    { method: "POST" },
  );

export const listGenerationAttempts = (campaignId: string, signal?: AbortSignal) =>
  request<GenerationAttempt[]>(
    "/api/v1/realistic-review-ugc/campaigns/" + encodeURIComponent(campaignId) + "/generation-attempts",
    { signal },
  );

export const listGenerationSkills = (campaignId: string, signal?: AbortSignal) =>
  request<GenerationSkillCatalog>(
    "/api/v1/realistic-review-ugc/generation-skills?campaign_id="
      + encodeURIComponent(campaignId),
    { signal },
  );

export const listReferenceAssets = (signal?: AbortSignal) =>
  request<ReferenceAsset[]>(
    "/api/v1/realistic-review-ugc/reference-assets?status=ready&limit=500",
    { signal },
  );

export const getReferenceSetRecommendation = (
  campaignId: string,
  skillName: string,
  signal?: AbortSignal,
) =>
  request<ReferenceSetRecommendation>(
    "/api/v1/realistic-review-ugc/reference-sets/recommendation?campaign_id="
      + encodeURIComponent(campaignId)
      + "&skill_name="
      + encodeURIComponent(skillName),
    { signal },
  );

export const listReferenceSets = (campaignId: string, signal?: AbortSignal) =>
  request<ReferenceSet[]>(
    "/api/v1/realistic-review-ugc/reference-sets?campaign_id="
      + encodeURIComponent(campaignId)
      + "&include_global=true&status=active",
    { signal },
  );

export const createReferenceSetFromSkill = (
  campaignId: string,
  skillName: string,
  name: string,
  items: Array<{ role: string; reference_asset_id: string }>,
) =>
  request<ReferenceSet>("/api/v1/realistic-review-ugc/reference-sets/from-skill", {
    method: "POST",
    body: JSON.stringify({
      campaign_id: campaignId,
      skill_name: skillName,
      name,
      items,
    }),
  });

export const prepareGenerationAttempt = (
  campaignId: string,
  candidateId: string,
  generationVariant = 1,
  workerSkillVersion?: string,
  referenceSetId?: string,
) =>
  request<{ created: boolean; attempt: GenerationAttempt }>(
    "/api/v1/realistic-review-ugc/campaigns/" + encodeURIComponent(campaignId)
      + "/candidates/" + encodeURIComponent(candidateId) + "/generation-attempts",
    {
      method: "POST",
      body: JSON.stringify({
        generation_variant: generationVariant,
        ...(workerSkillVersion ? { worker_skill_version: workerSkillVersion } : {}),
        ...(referenceSetId ? { reference_set_id: referenceSetId } : {}),
      }),
    },
  );

export const getGenerationCapability = (signal?: AbortSignal) =>
  request<GenerationCapability>(
    "/api/v1/realistic-review-ugc/generation-capability",
    { signal },
  );

export const executeGenerationAttempt = (attemptId: string) =>
  request<GenerationAttempt>(
    "/api/v1/realistic-review-ugc/generation-attempts/"
      + encodeURIComponent(attemptId) + "/execute",
    { method: "POST", body: "{}" },
  );

export const generationAttemptOutputUrl = (attemptId: string) =>
  "/api/v1/realistic-review-ugc/generation-attempts/"
    + encodeURIComponent(attemptId) + "/output";

export const listSupervisorResults = (campaignId: string, signal?: AbortSignal) =>
  request<SupervisorResult[]>(
    "/api/v1/realistic-review-ugc/campaigns/"
      + encodeURIComponent(campaignId) + "/supervisor-results",
    { signal },
  );

export const prepareSupervisorCorrection = (resultId: string) =>
  request<{ created: boolean; attempt: GenerationAttempt }>(
    "/api/v1/realistic-review-ugc/supervisor-results/"
      + encodeURIComponent(resultId) + "/prepare-correction",
    { method: "POST", body: "{}" },
  );

export type ReviewTaskFilters = {
  status?: ReviewTask["status"];
  priority?: ReviewTask["priority"];
  campaign_id?: string;
  limit?: number;
  offset?: number;
};

export const listReviewTasks = (
  filters: ReviewTaskFilters = {},
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.priority) params.set("priority", filters.priority);
  if (filters.campaign_id) params.set("campaign_id", filters.campaign_id);
  params.set("limit", String(filters.limit ?? 50));
  params.set("offset", String(filters.offset ?? 0));
  return request<ReviewTaskList>(
    "/api/v1/realistic-review-ugc/review-tasks?" + params.toString(),
    { signal },
  );
};

export const getReviewTask = (taskId: string, signal?: AbortSignal) =>
  request<ReviewTask>(
    "/api/v1/realistic-review-ugc/review-tasks/" + encodeURIComponent(taskId),
    { signal },
  );

const transitionReviewTask = (
  taskId: string,
  action: "approve" | "reject",
  reviewNote?: string,
) =>
  request<ReviewTaskTransition>(
    "/api/v1/realistic-review-ugc/review-tasks/"
      + encodeURIComponent(taskId) + "/" + action,
    {
      method: "POST",
      body: JSON.stringify({ review_note: reviewNote?.trim() || null }),
    },
  );

export const approveReviewTask = (taskId: string, reviewNote?: string) =>
  transitionReviewTask(taskId, "approve", reviewNote);

export const rejectReviewTask = (taskId: string, reviewNote?: string) =>
  transitionReviewTask(taskId, "reject", reviewNote);

export const reconcileReviewTasks = (limit = 100) =>
  request<{ scanned: number; created: number }>(
    "/api/v1/realistic-review-ugc/review-tasks/reconcile?limit=" + limit,
    { method: "POST", body: "{}" },
  );

export const listExports = (
  campaignId?: string,
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({ limit: "100", offset: "0" });
  if (campaignId) params.set("campaign_id", campaignId);
  return request<ExportList>(
    "/api/v1/realistic-review-ugc/exports?" + params.toString(),
    { signal },
  );
};

export const exportGenerationAttempt = (attemptId: string) =>
  request<ExportRecord>(
    "/api/v1/realistic-review-ugc/generation-attempts/"
      + encodeURIComponent(attemptId) + "/export",
    { method: "POST", body: "{}" },
  );

export const exportCampaignOutputs = (campaignId: string, limit = 100) =>
  request<BatchExportResult>(
    "/api/v1/realistic-review-ugc/campaigns/"
      + encodeURIComponent(campaignId) + "/exports?limit=" + limit,
    { method: "POST", body: "{}" },
  );

export const getCampaignExportSummary = (
  campaignId: string,
  signal?: AbortSignal,
) =>
  request<CampaignExportSummary>(
    "/api/v1/realistic-review-ugc/campaigns/"
      + encodeURIComponent(campaignId) + "/export-summary",
    { signal },
  );

export const listDeliveryDestinations = (signal?: AbortSignal) =>
  request<DeliveryDestination[]>(
    "/api/v1/realistic-review-ugc/delivery-destinations",
    { signal },
  );

export const createDeliveryDestination = (input: {
  name: string;
  target_ref: string;
  retention_days: number;
}) =>
  request<DeliveryDestination>(
    "/api/v1/realistic-review-ugc/delivery-destinations",
    {
      method: "POST",
      body: JSON.stringify({
        ...input,
        kind: "google_drive_folder",
      }),
    },
  );

export const archiveDeliveryDestination = (destinationId: string) =>
  request<DeliveryDestination>(
    "/api/v1/realistic-review-ugc/delivery-destinations/"
      + encodeURIComponent(destinationId),
    { method: "DELETE" },
  );

export const updateCampaignLifecyclePolicy = (
  campaignId: string,
  autoCompleteOnDelivery: boolean,
  completionDestinationId: string | null,
) =>
  request<Campaign>(
    "/api/v1/realistic-review-ugc/campaigns/"
      + encodeURIComponent(campaignId) + "/lifecycle-policy",
    {
      method: "PUT",
      body: JSON.stringify({
        auto_complete_on_delivery: autoCompleteOnDelivery,
        completion_destination_id: completionDestinationId,
      }),
    },
  );

export const listDeliveryPackages = (
  campaignId?: string,
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({ limit: "100", offset: "0" });
  if (campaignId) params.set("campaign_id", campaignId);
  return request<DeliveryPackageList>(
    "/api/v1/realistic-review-ugc/delivery-packages?" + params.toString(),
    { signal },
  );
};

export const deliverCampaign = (
  campaignId: string,
  destinationId: string,
) =>
  request<DeliveryPackage>(
    "/api/v1/realistic-review-ugc/campaigns/"
      + encodeURIComponent(campaignId) + "/deliveries/"
      + encodeURIComponent(destinationId),
    { method: "POST", body: "{}" },
  );

export const getCampaignDeliverySummary = (
  campaignId: string,
  signal?: AbortSignal,
) =>
  request<CampaignDeliverySummary>(
    "/api/v1/realistic-review-ugc/campaigns/"
      + encodeURIComponent(campaignId) + "/delivery-summary",
    { signal },
  );

export const reconcileDeliveryLifecycle = (limit = 200) =>
  request<DeliveryLifecycleReconcile>(
    "/api/v1/realistic-review-ugc/delivery-lifecycle/reconcile?limit=" + limit,
    { method: "POST", body: "{}" },
  );

export const getDeliveryOperationsSummary = (
  signal?: AbortSignal,
) =>
  request<DeliveryOperationsSummary>(
    "/api/v1/realistic-review-ugc/delivery-operations/summary",
    { signal },
  );

export const runDeliveryMaintenance = () =>
  request<DeliveryMaintenanceEnqueue>(
    "/api/v1/realistic-review-ugc/delivery-operations/maintenance",
    { method: "POST", body: "{}" },
  );


export const listCandidates = (campaignId: string, signal?: AbortSignal) =>
  request<Candidate[]>("/api/v1/realistic-review-ugc/campaigns/" + encodeURIComponent(campaignId) + "/candidates?limit=500", { signal });

export const analyzeCandidate = (campaignId: string, candidateId: string) =>
  request<{ candidate: Candidate }>(
    "/api/v1/realistic-review-ugc/campaigns/" + encodeURIComponent(campaignId) + "/candidates/" + encodeURIComponent(candidateId) + "/analyze",
    { method: "POST", body: "{}" },
  );

export const markCandidateAiFeedback = (
  campaignId: string,
  candidateId: string,
  label: AiManualLabel,
  note?: string,
) =>
  request<{ candidate: Candidate; calibration: AiFeedbackCalibration }>(
    "/api/v1/realistic-review-ugc/campaigns/"
      + encodeURIComponent(campaignId)
      + "/candidates/"
      + encodeURIComponent(candidateId)
      + "/ai-feedback",
    {
      method: "POST",
      body: JSON.stringify({ label, note: note || null }),
    },
  );

export const markCandidateReferenceFeedback = (
  campaignId: string,
  candidateId: string,
  label: ReferenceManualLabel | "clear",
  note?: string,
) =>
  request<{ candidate: Candidate; learning: { active: boolean; good_count: number; bad_count: number } }>(
    "/api/v1/realistic-review-ugc/campaigns/"
      + encodeURIComponent(campaignId)
      + "/candidates/"
      + encodeURIComponent(candidateId)
      + "/reference-feedback",
    {
      method: "POST",
      body: JSON.stringify({ label, note: note || null }),
    },
  );

export const markCandidateContextFeedback = (
  campaignId: string,
  candidateId: string,
  label: ContextManualLabel | "clear",
  note?: string,
) =>
  request<{ candidate: Candidate }>(
    "/api/v1/realistic-review-ugc/campaigns/"
      + encodeURIComponent(campaignId)
      + "/candidates/"
      + encodeURIComponent(candidateId)
      + "/context-feedback",
    {
      method: "POST",
      body: JSON.stringify({ label, note: note || null }),
    },
  );

export const importCandidate = (campaignId: string, candidateId: string) =>
  request<{ candidate: Candidate }>(
    "/api/v1/realistic-review-ugc/campaigns/" + encodeURIComponent(campaignId) + "/candidates/" + encodeURIComponent(candidateId) + "/import",
    { method: "POST", body: "{}" },
  );
