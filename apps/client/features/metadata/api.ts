import type {
  AiCapabilities,
  AnalysisRequestStatus,
  AnalysisSubmission,
  BulkAnalysisAccepted,
  SingleAnalysisAccepted,
  SubmitAnalysisInput,
} from "./types";

type Fetcher = typeof fetch;

async function responseError(response: Response): Promise<Error> {
  const payload = await response.json().catch(() => ({})) as {
    detail?: string | { message?: string; code?: string };
  };
  if (typeof payload.detail === "string") return Error(payload.detail);
  if (payload.detail?.message) return Error(payload.detail.message);
  return Error(`Request failed (${response.status})`);
}

export async function fetchAiCapabilities(
  signal?: AbortSignal,
  fetcher: Fetcher = fetch,
): Promise<AiCapabilities> {
  const response = await fetcher("/api/v1/admin/ai/capabilities", { signal });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<AiCapabilities>;
}

export function analysisIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `analysis-ui-${crypto.randomUUID()}`;
  }
  return `analysis-ui-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export async function submitAnalysis(
  input: SubmitAnalysisInput,
  fetcher: Fetcher = fetch,
): Promise<AnalysisSubmission> {
  const common = {
    metadata_profile: input.metadataProfile,
    metadata_profile_version: input.metadataProfileVersion || null,
    ai_provider: input.provider,
    processing_mode: input.processingMode,
    ai_model: input.model,
    force: input.force,
  };
  if (input.assetIds.length === 1) {
    const response = await fetcher("/api/v1/admin/asset-analyses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        asset_id: input.assetIds[0],
        source_provider: input.sourceProvider,
        ...common,
      }),
    });
    if (!response.ok) throw await responseError(response);
    return { kind: "single", ...await response.json() } as SingleAnalysisAccepted;
  }
  const response = await fetcher("/api/v1/admin/asset-analyses/bulk", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": analysisIdempotencyKey(),
    },
    body: JSON.stringify({ asset_ids: input.assetIds, ...common }),
  });
  if (!response.ok) throw await responseError(response);
  return { kind: "bulk", ...await response.json() } as BulkAnalysisAccepted;
}

export async function fetchAnalysisRequestStatus(
  requestId: string,
  includeProviderBatchId: boolean,
  signal?: AbortSignal,
  fetcher: Fetcher = fetch,
): Promise<AnalysisRequestStatus> {
  const query = includeProviderBatchId ? "?include_provider_batch_id=true" : "";
  const response = await fetcher(
    `/api/v1/admin/asset-analyses/requests/${encodeURIComponent(requestId)}${query}`,
    { signal },
  );
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<AnalysisRequestStatus>;
}

export async function fetchAssetAnalysisStatus(
  assetId: string,
  analysisId: string,
  signal?: AbortSignal,
  fetcher: Fetcher = fetch,
): Promise<Record<string, unknown> | null> {
  const response = await fetcher(`/api/v1/assets/${encodeURIComponent(assetId)}?analysis_limit=100`, { signal });
  if (!response.ok) throw await responseError(response);
  const payload = await response.json() as { analysis_history?: Array<Record<string, unknown>> };
  return payload.analysis_history?.find(item => item.id === analysisId) || null;
}
