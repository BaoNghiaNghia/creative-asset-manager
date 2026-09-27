import type {
  Campaign,
  CampaignCreateRequest,
  CampaignCreated,
  CampaignExportSummary,
  Candidate,
  GenerationAttempt,
  GenerationCapability,
  BatchExportResult,
  ExportList,
  ExportRecord,
  Product,
  ProductCreateRequest,
  ProductReference,
  ProductReferenceView,
  ProductUpdateRequest,
  ReviewTask,
  ReviewTaskList,
  ReviewTaskTransition,
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

export const listCampaigns = (signal?: AbortSignal) =>
  request<Campaign[]>("/api/v1/realistic-review-ugc/campaigns", { signal });

export const createCampaign = (body: CampaignCreateRequest) =>
  request<CampaignCreated>("/api/v1/realistic-review-ugc/campaigns", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const bindCampaignProduct = (campaignId: string, productId: string | null) =>
  request<Campaign>(
    "/api/v1/realistic-review-ugc/campaigns/" + encodeURIComponent(campaignId) + "/product",
    { method: "PUT", body: JSON.stringify({ product_id: productId }) },
  );

export const listGenerationAttempts = (campaignId: string, signal?: AbortSignal) =>
  request<GenerationAttempt[]>(
    "/api/v1/realistic-review-ugc/campaigns/" + encodeURIComponent(campaignId) + "/generation-attempts",
    { signal },
  );

export const prepareGenerationAttempt = (
  campaignId: string,
  candidateId: string,
  generationVariant = 1,
  workerSkillVersion = "worker-hat-v1",
) =>
  request<{ created: boolean; attempt: GenerationAttempt }>(
    "/api/v1/realistic-review-ugc/campaigns/" + encodeURIComponent(campaignId)
      + "/candidates/" + encodeURIComponent(candidateId) + "/generation-attempts",
    {
      method: "POST",
      body: JSON.stringify({
        generation_variant: generationVariant,
        worker_skill_version: workerSkillVersion,
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


export const listCandidates = (campaignId: string, signal?: AbortSignal) =>
  request<Candidate[]>("/api/v1/realistic-review-ugc/campaigns/" + encodeURIComponent(campaignId) + "/candidates?limit=120", { signal });

export const analyzeCandidate = (campaignId: string, candidateId: string) =>
  request<{ candidate: Candidate }>(
    "/api/v1/realistic-review-ugc/campaigns/" + encodeURIComponent(campaignId) + "/candidates/" + encodeURIComponent(candidateId) + "/analyze",
    { method: "POST", body: "{}" },
  );

export const importCandidate = (campaignId: string, candidateId: string) =>
  request<{ candidate: Candidate }>(
    "/api/v1/realistic-review-ugc/campaigns/" + encodeURIComponent(campaignId) + "/candidates/" + encodeURIComponent(candidateId) + "/import",
    { method: "POST", body: "{}" },
  );
