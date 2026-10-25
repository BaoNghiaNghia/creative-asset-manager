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
  KeywordImagePage,
  KeywordImageRow,
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
  RrugcHealth,
  ScoutRun,
  SourcePlanPage,
  SourcePlanSyncResult,
  Stage2Job,
  Stage2JobCreated,
  Stage4CompletionFeed,
  Stage3AnalyzeResult,
  Stage3ReviewGroupList,
  Stage2Skill,
  Stage2SkillCatalog,
  Stage2SkillRegistry,
  Stage2SkillRegistryItem,
  Stage2SkillSelection,
  SupervisorResult,
} from "./types";


export type CapStackLabel = "good" | "bad" | "ai" | null;
export type CapStackImage = {
  id: string; pin_url: string; image_url: string; alt_text: string | null;
  source_query: string | null; caps_detected: number | null;
  count_status: string; count_confidence: number | null;
  manual_caps_count: number | null; manual_label: CapStackLabel; created_at: string;
  is_cap_stack?: boolean | null; ai_suspected?: boolean | null;
  ai_suspicion_confidence?: number | null; visual_summary?: string | null;
  analysis_model?: string | null; analysis_error_code?: string | null; analyzed_at?: string | null;
};
export type CapStackPage = {
  items: CapStackImage[]; total: number; page: number; page_size: number;
  stats: { discovered: number; approved: number; rejected: number; ai: number; pending: number };
  vision?: { ready: number; analyzing: number; pending: number; needs_review: number; not_cap_stack: number; out_of_range?: number; error: number; ai_suspected: number };
  scout: { enabled: boolean; running: boolean; last_result: string | null; next_scan_at: string | null };
};
const CAP_STACK_PATH = "/api/v1/realistic-review-ugc/cap-stack";
export function listCapStackImages(params: {
  page?: number; pageSize?: number; q?: string; label?: string; capsCount?: number | null; visionStatus?: string;
}, signal?: AbortSignal): Promise<CapStackPage> {
  const query = new URLSearchParams({
    page: String(params.page || 1), page_size: String(params.pageSize || 40),
    q: params.q || "", label: params.label || "all", vision_status: params.visionStatus || "all",
  });
  if (params.capsCount) query.set("caps_count", String(params.capsCount));
  return request<CapStackPage>(CAP_STACK_PATH + "?" + query.toString(), { signal });
}
export function setCapStackEnabled(enabled: boolean): Promise<{ enabled: boolean }> {
  return request(CAP_STACK_PATH + "/settings", {
    method: "POST", body: JSON.stringify({ enabled }),
  });
}
export function setCapStackImageFeedback(
  imageId: string,
  body: { label?: "good" | "bad" | "ai" | "clear"; caps_count?: number },
): Promise<CapStackImage> {
  return request(CAP_STACK_PATH + "/" + encodeURIComponent(imageId) + "/feedback", {
    method: "POST", body: JSON.stringify(body),
  });
}

export function retryCapStackVision(imageId: string): Promise<{ image: CapStackImage; vision_queued: number }> {
  return request(CAP_STACK_PATH + "/" + encodeURIComponent(imageId) + "/analyze", { method: "POST" });
}

export class RrugcApiError extends Error {
  constructor(public status: number, message: string, public code?: string) {
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
    let message = `Request failed (HTTP ${response.status}).`;
    let code: string | undefined;
    try {
      const payload = await response.json();
      const detail = payload?.detail;
      if (typeof detail === "string" && detail.trim()) message = detail;
      else if (detail && typeof detail === "object") {
        if (typeof detail.message === "string" && detail.message.trim()) message = detail.message;
        else if (Array.isArray(detail)) {
          const issues = detail.filter(item => typeof item?.msg === "string")
            .slice(0, 3).map(item => item.msg);
          if (issues.length) message = "Invalid request: " + issues.join("; ");
        }
        if (typeof detail.code === "string") code = detail.code;
      } else if (typeof payload?.message === "string" && payload.message.trim()) {
        message = payload.message;
      }
    } catch {
      // Reverse proxies and gateway timeouts can send HTML or empty bodies.
      // Do not leak their raw response to the UI.
    }
    throw new RrugcApiError(response.status, message, code);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}


export const attachKeywordSourceImage = (
  keywordId: string,
  listingUrl: string,
  imageUrl: string,
  replace = false,
) => request<{source_image_url: string; source_image_status: string}>(
  "/api/v1/realistic-review-ugc/keyword-analysis/" + encodeURIComponent(keywordId) + "/source-image",
  { method: "POST", body: JSON.stringify({
    listing_url: listingUrl, image_url: imageUrl, replace,
  }) },
);

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

export type SourcePlanSortBy = "source" | "updated" | "analyzed" | "group_size" | "status" | "progress" | "refs" | "active_jobs" | "outputs";
export type SourcePlanSortDirection = "asc" | "desc";
export type SourcePlanFolders = {
  folders: Array<{ path: string; group_count: number; image_count: number }>;
  total_groups: number;
};
export const listSourcePlanFolders = (signal?: AbortSignal) =>
  request<SourcePlanFolders>("/api/v1/realistic-review-ugc/source-plans/folders", { signal });
export type KeywordAnalysisSortBy = "keyword" | "trend" | "platform" | "agent" | "search_volume" | "three_month_change" | "yoy_change" | "competition" | "cpc" | "high_cpc" | "trademark" | "created_at" | "fetched_at";
export type KeywordAnalysisSortDirection = "asc" | "desc";
export type KeywordUsageFilter = "all" | "unused" | "used";
export type KeywordTailFilter = "all" | "short" | "mid" | "long";

export const listSourcePlans = (
  filters: {
    page?: number;
    pageSize?: number;
    query?: string;
    sortBy?: SourcePlanSortBy;
    sortDirection?: SourcePlanSortDirection;
    stage2Only?: boolean;
    sourcePrefix?: string;
    folderPath?: string | null;
    colorwaySources?: boolean;
    colorwayWorkflow?: "products" | "reviews";
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
  if (filters.sourcePrefix?.trim()) params.set("source_prefix", filters.sourcePrefix.trim());
  if (filters.folderPath !== null && filters.folderPath !== undefined) params.set("folder_path", filters.folderPath);
  if (filters.colorwaySources) {
    params.set("colorway_sources", "true");
    params.set("colorway_workflow", filters.colorwayWorkflow || "reviews");
  }
  return request<SourcePlanPage>(
    "/api/v1/realistic-review-ugc/source-plans?" + params.toString(),
    { signal },
  );
};

export const syncSourcePlans = (colorwayOnly = false) =>
  request<SourcePlanSyncResult>("/api/v1/realistic-review-ugc/source-plans/sync" + (colorwayOnly ? "?colorway_sources=true" : ""), {
    method: "POST",
  });

export type QueryIntelligenceSummary = {
  total_queries: number;
  cycles_completed: number;
  new_keywords: number;
  duplicate_pins: number;
  active_leases: number;
  lanes: Array<{ lane: string; queries: number; cycles: number }>;
  recent: Array<{
    query: string; lane: string; cycles: number;
    new_keywords: number; scanned_pins: number; duplicate_pins: number;
    last_searched_at: string | null;
  }>;
};

export const getQueryIntelligence = (signal?: AbortSignal) =>
  request<QueryIntelligenceSummary>(
    "/api/v1/realistic-review-ugc/keyword-analysis/search-intelligence", { signal },
  );



export const listKeywordImages = (
  filters: { page?: number; pageSize?: number; query?: string; status?: string; sortBy?: "keyword" | "search_volume" | "duration" | "finished_at"; sortDirection?: "asc" | "desc" } = {},
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({
    page: String(filters.page ?? 1),
    page_size: String(filters.pageSize ?? 20),
    q: filters.query ?? "",
    status: filters.status ?? "all",
    sort_by: filters.sortBy ?? "finished_at",
    sort_dir: filters.sortDirection ?? "desc",
  });
  return request<KeywordImagePage>("/api/v1/realistic-review-ugc/keyword-images?" + params.toString(), { signal });
};

export const getKeywordAnalysisDetail = (keywordId: string, signal?: AbortSignal) =>
  request<import("./types").KeywordVolume>(
    "/api/v1/realistic-review-ugc/keyword-analysis/detail/" + encodeURIComponent(keywordId),
    { signal },
  );

export const getKeywordImageJobStatus = (jobId: string, signal?: AbortSignal) =>
  request<KeywordImageRow>(
    "/api/v1/realistic-review-ugc/keyword-images/jobs/" + encodeURIComponent(jobId) + "/status",
    { signal },
  );

export const createManualKeywordImage = (keyword: string, skill: Stage2SkillSelection) =>
  request<{ created: boolean; keyword_id: string; job_id: string; status: string }>(
    "/api/v1/realistic-review-ugc/keyword-images/manual",
    { method: "POST", body: JSON.stringify({
      keyword,
      skill_source: skill.source,
      skill_id: skill.skill_id,
      skill_name: skill.skill_name,
      skill_version: skill.skill_version,
    }) },
  );

export const createKeywordImage = (
  keywordId: string,
  skill: Stage2SkillSelection,
  prompt?: string,
) => request<{ created: boolean; job_id: string; status: string }>(
  "/api/v1/realistic-review-ugc/keyword-images/" + encodeURIComponent(keywordId),
  { method: "POST", body: JSON.stringify({
    skill_source: skill.source, skill_id: skill.skill_id,
    skill_name: skill.skill_name, skill_version: skill.skill_version,
    ...(prompt?.trim() ? { prompt: prompt.trim() } : {}),
  }) },
);


export const queueAllKeywordImages = (skill: Stage2SkillSelection, signal?: AbortSignal) =>
  request<{ queued: number; remaining: number }>(
    "/api/v1/realistic-review-ugc/keyword-images/batch?limit=50",
    { method: "POST", signal, body: JSON.stringify({
      skill_source: skill.source, skill_id: skill.skill_id,
      skill_name: skill.skill_name, skill_version: skill.skill_version,
    }) },
  );

export const retryKeywordImage = (keywordId: string) =>
  request<{ job_id: string; status: string }>(
    "/api/v1/realistic-review-ugc/keyword-images/" + encodeURIComponent(keywordId) + "/retry",
    { method: "POST", body: JSON.stringify({ confirm_replace_outputs: true }) },
  );


export const resolveStage0KeywordMetrics = (keyword: string) =>
  request<{ requested: number; provider_requested: number; cached: number }>(
    "/api/v1/realistic-review-ugc/keyword-analysis/resolve",
    { method: "POST", body: JSON.stringify({ keywords: [keyword], force: false }) },
  );

export const listKeywordAnalysis = (
  filters: {
    page?: number;
    pageSize?: number;
    query?: string;
    sortBy?: KeywordAnalysisSortBy;
    sortDirection?: KeywordAnalysisSortDirection;
    usage?: KeywordUsageFilter;
    tail?: KeywordTailFilter;
    favoritesOnly?: boolean;
    suggestedOnly?: boolean;
  } = {},
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({
    page: String(filters.page ?? 1),
    page_size: String(filters.pageSize ?? 20),
    sort_by: filters.sortBy ?? "search_volume",
    sort_dir: filters.sortDirection ?? "desc",
    usage: filters.usage ?? "all",
    tail: filters.tail ?? "all",
    favorites_only: String(filters.favoritesOnly ?? false),
    suggested_only: String(filters.suggestedOnly ?? false),
  });
  if (filters.query?.trim()) params.set("query", filters.query.trim());
  return request<KeywordVolumePage>(
    "/api/v1/realistic-review-ugc/keyword-analysis?" + params.toString(),
    { signal },
  );
};

export type KeywordSearchSuggestion = {
  keyword: string;
  search_volume: number;
  favorite: boolean;
  picked: boolean;
};

export const suggestKeywordAnalysis = (query: string, signal?: AbortSignal) => {
  const params = new URLSearchParams({ q: query.trim(), limit: "8" });
  return request<KeywordSearchSuggestion[]>(
    "/api/v1/realistic-review-ugc/keyword-analysis/suggestions?" + params.toString(),
    { signal },
  );
};

export type ScoutFeedbackAction = "suggested" | "blocked" | "neutral";
export type ScoutFeedbackScope = "keyword" | "pin" | "both";

export const setKeywordScoutFeedback = (keywordId: string, action: ScoutFeedbackAction, scope: ScoutFeedbackScope) =>
  request<import("./types").KeywordVolume>(
    "/api/v1/realistic-review-ugc/keyword-analysis/" + encodeURIComponent(keywordId) + "/feedback",
    { method: "PATCH", body: JSON.stringify({ action, scope }) },
  );

export const listScoutMetrics = (signal?: AbortSignal) =>
  request<{
    items: Array<{ agent_id: string; machine_label: string; mode: "keyword" | "review"; scanned_pins: number; found_quotes: number; new_keywords: number; duplicate_pins: number; errors: number; last_activity_at: string | null }>;
    review_items: Array<{
      agent_id: string; submitted: number; new_references: number;
      duplicates: number; runs: number; failed_runs: number;
      last_activity_at: string | null;
    }>;
    overview: {
      total_keywords: number; added_24h: number; added_7d: number;
      priority_pending: number;
    };
    feedback: { suggested?: number; blocked?: number };
    period: string;
  }>("/api/v1/realistic-review-ugc/scout-metrics", { signal });

export const setKeywordAnalysisPicked = (keywordId: string, picked: boolean) =>
  request<import("./types").KeywordVolume>(
    "/api/v1/realistic-review-ugc/keyword-analysis/" + encodeURIComponent(keywordId) + "/pick",
    {
      method: "PATCH",
      body: JSON.stringify({ picked }),
    },
  );

export const setKeywordAnalysisFavorite = (keywordId: string, favorite: boolean) =>
  request<import("./types").KeywordVolume>(
    "/api/v1/realistic-review-ugc/keyword-analysis/" + encodeURIComponent(keywordId) + "/favorite",
    { method: "PATCH", body: JSON.stringify({ favorite }) },
  );

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

export const getRrugcHealth = (signal?: AbortSignal) =>
  request<RrugcHealth>("/api/v1/realistic-review-ugc/health", { signal });

export const listScoutAgents = (signal?: AbortSignal) =>
  request<ScoutAgent[]>("/api/v1/realistic-review-ugc/scout-agents", { signal });

export const createScoutAgent = (name: string) =>
  request<ScoutAgentCreated>("/api/v1/realistic-review-ugc/scout-agents", {
    method: "POST",
    body: JSON.stringify({ name }),
  });

export const resetScoutAgentPairing = (agentId: string) =>
  request<ScoutAgentCreated>(
    "/api/v1/realistic-review-ugc/scout-agents/" + encodeURIComponent(agentId) + "/reset-pairing",
    { method: "POST" },
  );

export const archiveScoutAgent = (agentId: string) =>
  request<ScoutAgent>(
    "/api/v1/realistic-review-ugc/scout-agents/" + encodeURIComponent(agentId),
    { method: "DELETE" },
  );

export const listStage4Completions = (cursor?: string, signal?: AbortSignal) =>
  request<Stage4CompletionFeed>(
    "/api/v1/realistic-review-ugc/stage4/completions"
    + (cursor !== undefined ? "?cursor=" + encodeURIComponent(cursor) : ""),
    { signal },
  );

export async function listAllStage4ActiveJobs(signal?: AbortSignal): Promise<Stage2Job[]> {
  const results: Stage2Job[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < 100; page += 1) {
    const params = new URLSearchParams({ limit: "200" });
    if (cursor) params.set("cursor", cursor);
    const batch = await request<Stage2Job[]>(
      "/api/v1/realistic-review-ugc/stage4/active-jobs?" + params.toString(),
      { signal },
    );
    results.push(...batch);
    if (batch.length < 200) return results;
    cursor = batch[batch.length - 1].id;
  }
  throw new Error("Active job list exceeded the supported paging limit.");
}

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
  cursor?: string,
  folderPath?: string | null,
) =>
  request<Stage3ReviewGroupList>(
    "/api/v1/realistic-review-ugc/stage3/review-groups?limit=500&page_size=250"
      + (cursor ? "&cursor=" + encodeURIComponent(cursor) : "")
      + (folderPath != null ? "&folder_path=" + encodeURIComponent(folderPath) : ""),
    { signal },
  );

export const hideStage5Review = (stage2JobId: string) =>
  request<{ hidden: boolean; stage2_job_id: string }>(
    "/api/v1/realistic-review-ugc/stage3/reviews/" + encodeURIComponent(stage2JobId),
    { method: "DELETE" },
  );

export type Stage5LooxExportResult = {
  rows: number;
  missingPhotos: number;
  normalizedHandles: number;
  format: "csv" | "xlsx" | "internal_csv";
};

export async function downloadStage5LooxReviewConcepts(
  format: "csv" | "xlsx" | "internal_csv" = "csv",
): Promise<Stage5LooxExportResult> {
  const response = await fetch(
    "/api/v1/realistic-review-ugc/stage3/reviews/export-loox?format=" + format,
    {
      credentials: "include",
      headers: {
        Accept: format !== "xlsx" ? "text/csv" : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      },
    },
  );
  if (!response.ok) throw new RrugcApiError(response.status, "Unable to export review concepts.");
  const blob = await response.blob();
  const fileUrl = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = fileUrl;
    const exportedAt = new Date();
    const stamp = `${exportedAt.getFullYear()}${String(exportedAt.getMonth() + 1).padStart(2, "0")}${String(exportedAt.getDate()).padStart(2, "0")}_${String(exportedAt.getHours()).padStart(2, "0")}${String(exportedAt.getMinutes()).padStart(2, "0")}${String(exportedAt.getSeconds()).padStart(2, "0")}`;
    link.download = format === "csv" ? `CAM_Stage5_Loox_Import_${stamp}.csv` : format === "internal_csv" ? `CAM_Stage5_Internal_Concepts_${stamp}.csv` : `CAM_Stage5_Loox_Preview_${stamp}.xlsx`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(fileUrl);
  }
  return {
    rows: Number(response.headers.get("X-CAM-Export-Rows") || 0),
    missingPhotos: Number(response.headers.get("X-CAM-Export-Missing-Photos") || 0),
    normalizedHandles: Number(response.headers.get("X-CAM-Export-Normalized-Handles") || 0),
    format,
  };
}

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

export type HatStockGroup = {
  color: string;
  views: Record<string, string[]>;
  missing: string[];
  duplicates: string[];
  ready: boolean;
};
export type HatStockPreview = {
  sha256: string;
  file_count: number;
  ready_count: number;
  groups: HatStockGroup[];
  unmatched: Array<{ file: string; error: string }>;
  applied?: string[];
  applied_count?: number;
};
export type HatStockCatalog = {
  can_sync: boolean;
  colors: Record<string, { revision: string; views: Partial<Record<"front" | "side" | "back" | "three-quarter", string>> }>;
};
const HAT_STOCK_PATH = "/api/v1/realistic-review-ugc/products/hat-stock";
export const getHatStockCatalog = (signal?: AbortSignal) =>
  request<HatStockCatalog>(HAT_STOCK_PATH + "/catalog", { signal });
export const previewHatStockZip = (file: File) => {
  const form = new FormData();
  form.append("file", file);
  return request<HatStockPreview>(HAT_STOCK_PATH + "/preview", { method: "POST", body: form });
};
export const applyHatStockZip = (file: File, sha256: string) => {
  const form = new FormData();
  form.append("file", file);
  form.append("preview_sha256", sha256);
  return request<HatStockPreview>(HAT_STOCK_PATH + "/apply", { method: "POST", body: form });
};

export type ColorwayJob = {
  id: string;
  source_plan_id: string;
  color_key: string;
  color_name: string;
  status: "queued" | "running" | "completed" | "failed";
  retry_count: number;
  attempt_count: number;
  error_code: string | null;
  output_url: string | null;
  started_at?: string | null; completed_at?: string | null;
};

export type ProductsStage2PhotoStep = {
  key: "ecommerce" | "multihat" | "ugc";
  skill_name: string;
  title: string;
  output_count: number;
  shot_labels: string[];
  ready: boolean;
  bundle_installed: boolean;
  status: "needs_install" | "needs_pipeline_worker" | "ready";
  missing_assets: string[];
  required_inputs: string;
};
export type EcommerceJob = {
  id: string; source_plan_id: string; stage: string; color_key?: string;
  status: "queued" | "running" | "completed" | "failed";
  retry_count: number; attempt_count: number; error_code: string | null;
  output_url: string | null; skill_name: string;
  scene_reference?: { candidate_id: string; image_url: string } | null;
  scene_references?: Array<{ image_id: string; image_url: string; pin_url: string; caps_count: 3 | 4 }>;
  scene_colorways?: Array<{ key: string; label: string }>;
  started_at?: string | null; completed_at?: string | null;
};
export type EcommerceJobLogs = {
  job_id: string; stage: string; status: string; skill: string;
  error_code: string | null;
  attempts: Array<{ id: string; status: string; attempt_count: number;
    error_code: string | null; error_message: string | null;
    created_at: string; completed_at: string | null }>;
};
export type EcommerceOutputVersion = {
  version: number; created_at: string; output_url: string;
  width: number | null; height: number | null;
};
export type UgcScoutItem = { id: string; image_url: string; pin_url: string | null; score: number | null; status: string; manual_label?: "good" | null };
export type UgcScoutGallery = { source_plan_id: string; campaign_id: string | null; status: string; error_code?: string | null; next_attempt_at?: string | null; target: number; count: number; items: UgcScoutItem[] };
export const getUgcScoutGallery = (sourcePlanId: string, signal?: AbortSignal) =>
  request<UgcScoutGallery>("/api/v1/realistic-review-ugc/products-stage2/ugc-scout/" + encodeURIComponent(sourcePlanId), { signal });
export const startUgcScout = (sourcePlanId: string) =>
  request<{ status: string; campaign_id: string }>("/api/v1/realistic-review-ugc/products-stage2/ugc-scout/start", {
    method: "POST", body: JSON.stringify({ source_plan_id: sourcePlanId }),
  });

const ECOMMERCE_PATH = "/api/v1/realistic-review-ugc/products-stage2/ecommerce";
export const listEcommerceJobs = (sourcePlanIds: string[], signal?: AbortSignal) => {
  const params = new URLSearchParams();
  for (const id of sourcePlanIds) params.append("source_plan_id", id);
  return request<EcommerceJob[]>(ECOMMERCE_PATH + "/jobs?" + params.toString(), { signal });
};
export type EcommerceRunAllStep = {
  stage: string;
  slot: number;
  name: string;
  status: "running" | "completed" | "blocked" | "pending";
  completed_outputs: number;
  total_outputs: number;
  reason?: string | null;
};
export type EcommerceRunAllResult = {
  run_id: string;
  source_plan_id: string;
  source_revision: string;
  color_key: string;
  current_slot: number;
  status: "running" | "completed" | "blocked";
  error_code: string | null;
  error_message: string | null;
  steps: EcommerceRunAllStep[];
};
export const runAllEcommerceJobs = (sourcePlanId: string, colorKey = "natural-black",
                                    colorwaySkill?: Stage2SkillSelection | null) =>
  request<EcommerceRunAllResult>(ECOMMERCE_PATH + "/run-all", {
    method: "POST", body: JSON.stringify({
      source_plan_id: sourcePlanId, color_key: colorKey, colorway_skill: colorwaySkill || null,
    }),
  });
export const getEcommerceRunAllStatus = (sourcePlanId: string, colorKey = "natural-black", signal?: AbortSignal) =>
  request<EcommerceRunAllResult | null>(ECOMMERCE_PATH + "/run-all/status?source_plan_id=" +
    encodeURIComponent(sourcePlanId) + "&color_key=" + encodeURIComponent(colorKey), { signal });
export type ColorwayQueueResult = {
  queued: number;
  existing: number;
  retried?: number;
  error?: string;
};

export const runSceneJob = (sourcePlanId: string, slot: "prod_s2_mh" | "prod_s2_ugc", colorKey = "natural-black") =>
  request<EcommerceRunAllResult>(ECOMMERCE_PATH + "/run-scene", {
    method: "POST", body: JSON.stringify({ source_plan_id: sourcePlanId, slot, color_key: colorKey }),
  });

export const runEcommerceJob = (sourcePlanId: string, stage: string, colorKey = "natural-black") =>
  request<{ job_id: string; status: string }>(ECOMMERCE_PATH + "/run", {
    method: "POST", body: JSON.stringify({ source_plan_id: sourcePlanId, stage, color_key: colorKey }),
  });
export const retryEcommerceJob = (jobId: string) =>
  request<{ job_id: string; status: string }>(
    ECOMMERCE_PATH + "/jobs/" + encodeURIComponent(jobId) + "/retry",
    { method: "POST" },
  );
export const regenerateEcommerceJob = (jobId: string) =>
  request<{ job_id: string; status: string }>(
    ECOMMERCE_PATH + "/jobs/" + encodeURIComponent(jobId) + "/regenerate",
    { method: "POST" },
  );
export const getEcommerceJobLogs = (jobId: string, signal?: AbortSignal) =>
  request<EcommerceJobLogs>(
    ECOMMERCE_PATH + "/jobs/" + encodeURIComponent(jobId) + "/logs",
    { signal },
  );
export const getEcommerceOutputVersions = (jobId: string, signal?: AbortSignal) =>
  request<EcommerceOutputVersion[]>(
    ECOMMERCE_PATH + "/jobs/" + encodeURIComponent(jobId) + "/versions",
    { signal },
  );

export type EcommerceSlotInputReadiness = {
  stage: string; view: string; required_roles: string[]; missing_roles: string[];
  ready: boolean; status: "missing_inputs" | "missing_skill" | "worker_pending" | "ready";
};
export type EcommerceInputReadiness = {
  source_plan_id: string; source_revision: string; color_key: string; slots: EcommerceSlotInputReadiness[];
};
export const getEcommerceInputReadiness = (sourcePlanId: string, signal?: AbortSignal, colorKey = "natural-black") =>
  request<EcommerceInputReadiness>(
    "/api/v1/realistic-review-ugc/products-stage2/ecommerce/input-readiness?source_plan_id=" +
    encodeURIComponent(sourcePlanId) + "&color_key=" + encodeURIComponent(colorKey), { signal },
  );

export type ProductsStage2PhotoReadiness = {
  workflow: string;
  sequential: boolean;
  ready: boolean;
  total_outputs_per_design: number;
  steps: ProductsStage2PhotoStep[];
};
export const getProductsStage2PhotoReadiness = (signal?: AbortSignal) =>
  request<ProductsStage2PhotoReadiness>(
    "/api/v1/realistic-review-ugc/products-stage2/photo-pipeline/readiness",
    { signal },
  );

export type ColorwayReadiness = {
  ready: boolean;
  error_code: string | null;
  stock_ready_count: number;
  stock_total_count: number;
};

export const getColorwayReadiness = (signal?: AbortSignal, workflow: "products" | "reviews" = "reviews") =>
  request<ColorwayReadiness>("/api/v1/realistic-review-ugc/colorways/readiness" +
    (workflow === "products" ? "?workflow=products" : ""), { signal });

export const listColorwayJobs = async (sourceIds: string[], signal?: AbortSignal): Promise<ColorwayJob[]> => {
  const ids = Array.from(new Set(sourceIds));
  const chunks: Promise<ColorwayJob[]>[] = [];
  for (let offset = 0; offset < ids.length; offset += 50) {
    const params = new URLSearchParams();
    ids.slice(offset, offset + 50).forEach(id => params.append("source_plan_id", id));
    chunks.push(request<ColorwayJob[]>("/api/v1/realistic-review-ugc/colorways?" + params, { signal }));
  }
  return (await Promise.all(chunks)).flat();
};

export const queueColorwayBatch = (
  sourcePlanIds: string[], skill: Stage2SkillSelection, workflow: "products" | "reviews" = "reviews",
) => request<{ queued: number; existing: number }>("/api/v1/realistic-review-ugc/colorways/batch", {
  method: "POST",
  body: JSON.stringify({ source_plan_ids: sourcePlanIds, workflow, skill_source: skill.source, skill_id: skill.skill_id, skill_name: skill.skill_name, skill_version: skill.skill_version }),
});

export const retryColorway = (jobId: string) =>
  request<{ job_id: string; status: string }>(
    "/api/v1/realistic-review-ugc/colorways/" + encodeURIComponent(jobId) + "/retry",
    { method: "POST" },
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

export const restoreArchivedStage1Skill = (registryId: string) =>
  request<Stage2SkillRegistryItem>(
    "/api/v1/realistic-review-ugc/stage2-skills/registry/" + encodeURIComponent(registryId) + "/restore-stage1",
    { method: "POST" },
  );

export const updateStage2SkillNote = (registryId: string, note: string) =>
  request<Stage2SkillRegistryItem>(
    "/api/v1/realistic-review-ugc/stage2-skills/registry/" + encodeURIComponent(registryId) + "/note",
    { method: "PATCH", body: JSON.stringify({ note }) },
  );

export const updateStageSkillDefault = (stage: import("./workflowSkillSlots").WorkflowSkillStage, registryId: string | null) =>
  request<{ stage_defaults: Record<string, string> }>(
    "/api/v1/realistic-review-ugc/stage2-skills/defaults/" + stage,
    { method: "PATCH", body: JSON.stringify({ registry_id: registryId }) },
  );

export type GenerationOutputVersion = { selected_for_products_stage2?: boolean; version: number; created_at: string; width: number | null;
  height: number | null; url: string; output_role?: "design_concepts" | "colorways" | null; output_name?: string | null;
  font_info?: { font_name: string; font_style: string; layout_name: string; font_source: string; font_url: string; font_license: string; font_status: string } | null };
export const listGenerationOutputVersions = (stage: "stage1" | "stage2" | "stage4", jobId: string, signal?: AbortSignal) =>
  request<{ job_id: string; stage: string; versions: GenerationOutputVersion[] }>(
    "/api/v1/realistic-review-ugc/generation-jobs/" + stage + "/" + encodeURIComponent(jobId) + "/outputs",
    { signal },
  );
export const selectStage1ProductDesign = (jobId: string, version: number, selected: boolean) =>
  request<{ selected: boolean; source_plan_id: string | null }>(
    "/api/v1/realistic-review-ugc/keyword-images/jobs/" + encodeURIComponent(jobId)
    + "/versions/" + version + "/products-stage2?selected=" + selected,
    { method: "POST" },
  );

export const regenerateKeywordImage = (keywordId: string, skill?: Stage2SkillSelection | null) =>
  request<{ job_id: string; status: string }>(
    "/api/v1/realistic-review-ugc/keyword-images/" + encodeURIComponent(keywordId) + "/regenerate",
    { method: "POST", body: JSON.stringify({ ...(skill || {}), confirm_replace_outputs: true }) },
  );
export const regenerateColorwayJob = (jobId: string) =>
  request<{ job_id: string; status: string }>(
    "/api/v1/realistic-review-ugc/colorways/" + encodeURIComponent(jobId) + "/regenerate",
    { method: "POST" },
  );

export type SkillExecutionProgress = {
  state: string; started_at: string; last_activity_at: string; elapsed_seconds: number;
  event_count: number; last_event: string; stdout_bytes: number; stderr_bytes: number;
  events: Array<{ time: string; event: string; item: string }>;
};
export type SkillJobLog = { stage: string; job_id: string; status: string;
  skill: { name: string; source: string; version: string | null };
  attempts: Array<{ id: string; status: string; attempt_count: number; max_attempts: number; duration_ms: number;
    error_code: string | null; error_message: string; created_at: string; updated_at: string; completed_at: string | null;
    execution?: SkillExecutionProgress | null }>;
};
export const getSkillJobLog = (stage: "stage1" | "stage2" | "stage4", jobId: string, signal?: AbortSignal) =>
  request<SkillJobLog>("/api/v1/realistic-review-ugc/generation-jobs/" + stage + "/" + encodeURIComponent(jobId) + "/logs", { signal });

/** XHR is used only for uploads requiring real byte progress; credentials and
 * error contract match request(). 100% transfer is not installation success. */
function uploadSkillWithProgress<T>(url: string, body: FormData, onProgress: (percent: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.withCredentials = true;
    xhr.responseType = "json";
    xhr.upload.addEventListener("progress", event => {
      if (event.lengthComputable && event.total > 0) {
        onProgress(Math.min(100, Math.floor(event.loaded / event.total * 100)));
      }
    });
    xhr.addEventListener("error", () => reject(new Error("Upload connection failed. Check your network and retry.")));
    xhr.addEventListener("abort", () => reject(new Error("Skill upload was interrupted.")));
    xhr.addEventListener("load", () => {
      const payload = xhr.response;
      if (xhr.status < 200 || xhr.status >= 300) {
        const detail = payload?.detail;
        reject(new RrugcApiError(xhr.status, String(detail?.message || detail || "Skill installation failed."),
          typeof detail?.code === "string" ? detail.code : undefined));
      } else {
        onProgress(100);
        resolve(payload as T);
      }
    });
    xhr.send(body);
  });
}

export const createStage2Skill = (file: File, replaceExisting = false, onProgress?: (percent: number) => void) => {
  const body = new FormData();
  body.set("file", file);
  const url = "/api/v1/realistic-review-ugc/stage2-skills/registry?replace_existing=" + String(replaceExisting);
  return onProgress
    ? uploadSkillWithProgress<Stage2SkillRegistryItem>(url, body, onProgress)
    : request<Stage2SkillRegistryItem>(url, { method: "POST", body });
};

export const uploadLocalKeywordSkillForStage1 = (registryId: string, file: File) => {
  const body = new FormData();
  body.set("file", file);
  return request<Stage2SkillRegistryItem>(
    "/api/v1/realistic-review-ugc/stage2-skills/registry/"
    + encodeURIComponent(registryId) + "/upload-stage1",
    { method: "POST", body },
  );
};

export const createStage2SkillVersion = (
  registryId: string,
  file: File,
  makeDefault = true,
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

export type CodexAccountConfig = {
  account_id: "account-1" | "account-2";
  display_name: string;
  enabled: boolean;
  max_concurrent: number;
  stages: string[];
  auth_status: "connected" | "not_connected" | "auth_required" | "cli_unavailable" | "check_failed";
  is_executing: boolean;
  active_jobs: number;
  updated_at: string;
};
export type CodexAccountsStatus = {
  routing_mode: "single_active" | "multi_account" | "stage2_pool";
  routing_note: string;
  can_manage: boolean;
  accounts: CodexAccountConfig[];
};
export const getCodexAccounts = (signal?: AbortSignal) =>
  request<CodexAccountsStatus>("/api/v1/realistic-review-ugc/codex-accounts", { signal });
export const updateCodexAccount = (id: CodexAccountConfig["account_id"], config: Pick<CodexAccountConfig, "display_name" | "enabled" | "max_concurrent" | "stages">) =>
  request<CodexAccountsStatus>("/api/v1/realistic-review-ugc/codex-accounts/" + encodeURIComponent(id), {
    method: "PUT",
    body: JSON.stringify(config),
  });

export type Stage4QueueControl = { paused: boolean; queued: number; running: number };

export const getStage4QueueControl = (signal?: AbortSignal) =>
  request<Stage4QueueControl>("/api/v1/realistic-review-ugc/stage4/queue-control", { signal });

export const setStage4QueuePaused = (paused: boolean) =>
  request<Stage4QueueControl>("/api/v1/realistic-review-ugc/stage4/queue-control", {
    method: "POST", body: JSON.stringify({ paused }),
  });

export const regenerateStage4Job = (jobId: string) =>
  request<Stage2Job>("/api/v1/realistic-review-ugc/stage4/jobs/" + encodeURIComponent(jobId) + "/regenerate", { method: "POST" });

export const retryStage4Job = (jobId: string) =>
  request<Stage2Job>("/api/v1/realistic-review-ugc/stage4/jobs/" + encodeURIComponent(jobId) + "/retry", { method: "POST" });

export const cancelStage2Jobs = (sourcePlanId: string, jobIds?: string[]) =>
  request<{ cancelled: number; job_ids: string[] }>(
    "/api/v1/realistic-review-ugc/source-plans/"
      + encodeURIComponent(sourcePlanId)
      + "/stage2-jobs/cancel",
    { method: "POST", ...(jobIds?.length ? { body: JSON.stringify({ job_ids: jobIds }) } : {}) },
  );

export type RrugcMediaStorageStatus = {
  status: "connected" | "reconnect_required";
  root_folder_configured: boolean;
  connected: boolean;
  reconnect_required: boolean;
  retry_after_seconds: number;
};
export const getRrugcMediaStorageStatus = (signal?: AbortSignal) =>
  request<RrugcMediaStorageStatus>(
    "/api/v1/realistic-review-ugc/media-storage/status", { signal },
  );

export const stage2JobOutputUrl = (jobId: string) =>
  "/api/v1/realistic-review-ugc/stage2-jobs/"
  + encodeURIComponent(jobId)
  + "/output";

export const stage2JobOutputThumbnailUrl = (jobId: string, size = 192, revision?: string | null) =>
  stage2JobOutputUrl(jobId)
  + "?thumbnail=true&size="
  + Math.min(1024, Math.max(128, Math.round(size)))
  + (revision ? "&v=" + encodeURIComponent(revision) : "");

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