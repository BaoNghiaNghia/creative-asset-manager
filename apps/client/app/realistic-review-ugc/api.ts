import type {
  Campaign,
  CampaignCreateRequest,
  CampaignCreated,
  Candidate,
  GenerationAttempt,
  GenerationCapability,
  Product,
  ProductCreateRequest,
  ProductReference,
  ProductReferenceView,
  ProductUpdateRequest,
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
