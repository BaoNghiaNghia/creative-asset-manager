import type {
  AiOpsDaily, AiOpsDashboardData, AiOpsFailure, AiOpsFilters, AiOpsJob,
  AiOpsConfiguration, AiOpsProvider, AiOpsProviderBreakdown, AiOpsSummary, AiOpsUsage, Page, PipelineSnapshot,
} from "./types";

type Fetcher = typeof fetch;

export class AiOperationsApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function read<T>(url: string, fetcher: Fetcher): Promise<T> {
  const response = await fetcher(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: string | { message?: string } };
    const detail = typeof payload.detail === "string" ? payload.detail : payload.detail?.message;
    throw new AiOperationsApiError(detail || `Request failed (${response.status})`, response.status);
  }
  return response.json() as Promise<T>;
}

function isoRange(days: AiOpsFilters["range"], now: Date): { from: string | null; to: string } {
  if (days === 0) return { from: null, to: now.toISOString() };
  const from = new Date(now);
  from.setUTCDate(from.getUTCDate() - days);
  return { from: from.toISOString(), to: now.toISOString() };
}

function filteredParams(filters: AiOpsFilters, range: { from: string | null; to: string }): URLSearchParams {
  const params = new URLSearchParams({ to: range.to });
  if (range.from) params.set("from", range.from);
  if (range.from === null) params.set("range", "all");
  if (filters.provider) params.set("provider", filters.provider);
  if (filters.model) params.set("model", filters.model);
  if (filters.processingMode) params.set("processing_mode", filters.processingMode);
  if (filters.metadataProfile) params.set("metadata_profile", filters.metadataProfile);
  if (filters.status) params.set("status", filters.status);
  return params;
}

export type DashboardResult = {
  data: AiOpsDashboardData;
  errors: string[];
  unauthorized: boolean;
};

export function normalizePipelineSnapshot(value: PipelineSnapshot | null | undefined): PipelineSnapshot | null | undefined {
  if (!value) return value;
  const recent = (value as unknown as { recent_assets?: unknown }).recent_assets;
  if (Array.isArray(recent)) {
    return {
      ...value,
      recent_assets: { page: 1, page_size: 25, total: recent.length, items: recent },
    };
  }
  if (!recent || typeof recent !== "object" || !Array.isArray((recent as { items?: unknown }).items)) {
    return {
      ...value,
      recent_assets: { page: 1, page_size: 25, total: 0, items: [] },
    };
  }
  return value;
}

export async function fetchAiOperationsDashboard(
  filters: AiOpsFilters,
  fetcher: Fetcher = fetch,
  now = new Date(),
): Promise<DashboardResult> {
  const range = isoRange(filters.range, now);
  const current = filteredParams(filters, range);
  const todayStart = new Date(now);
  todayStart.setUTCHours(0, 0, 0, 0);
  const monthStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const today = filteredParams(filters, { from: todayStart.toISOString(), to: now.toISOString() });
  const month = filteredParams(filters, { from: monthStart.toISOString(), to: now.toISOString() });
  const jobs = new URLSearchParams(current); jobs.set("page", String(filters.page)); jobs.set("page_size", String(filters.pageSize || 25));
  const usage = new URLSearchParams(current); usage.set("page", String(filters.usagePage || 1)); usage.set("page_size", String(filters.usagePageSize || 25));
  const base = "/api/v1/admin/ai-operations";
  const calls = [
    read<AiOpsSummary>(`${base}/summary?${current}`, fetcher),
    read<AiOpsSummary>(`${base}/summary?${today}`, fetcher),
    read<AiOpsSummary>(`${base}/summary?${month}`, fetcher),
    read<{ items: AiOpsDaily[] }>(`${base}/daily?${current}`, fetcher),
    read<{ items: AiOpsProviderBreakdown[] }>(`${base}/providers?${current}`, fetcher),
    read<{ items: AiOpsProviderBreakdown[] }>(`${base}/providers?${today}`, fetcher),
    read<{ items: AiOpsFailure[] }>(`${base}/failures?${current}`, fetcher),
    read<Page<AiOpsJob>>(`${base}/jobs?${jobs}`, fetcher),
    read<Page<AiOpsUsage>>(`${base}/usage?${usage}`, fetcher),
    read<PipelineSnapshot>(base + "/pipeline?recent_page=" + (filters.pipelinePage || 1) + "&recent_page_size=" + (filters.pipelinePageSize || 25), fetcher),
  ] as const;
  const settled = await Promise.allSettled(calls);
  const pipelineUnavailable = settled[9]?.status === "rejected"
    && settled[9].reason instanceof AiOperationsApiError
    && settled[9].reason.status === 404;
  const errors = settled.flatMap((item, index) => item.status === "rejected" && !(index === 9 && pipelineUnavailable)
    ? [String(item.reason?.message || "Request failed")] : []);
  const unauthorized = settled.some(item => item.status === "rejected" && item.reason instanceof AiOperationsApiError && [401, 403].includes(item.reason.status));
  const value = <T,>(index: number, fallback: T): T => settled[index]?.status === "fulfilled"
    ? (settled[index] as PromiseFulfilledResult<T>).value : fallback;
  return {
    unauthorized,
    errors: [...new Set(errors)],
    data: {
      summary: value<AiOpsSummary | null>(0, null),
      today: value<AiOpsSummary | null>(1, null),
      month: value<AiOpsSummary | null>(2, null),
      daily: value<{ items: AiOpsDaily[] }>(3, { items: [] }).items,
      providers: value<{ items: AiOpsProviderBreakdown[] }>(4, { items: [] }).items,
      todayProviders: value<{ items: AiOpsProviderBreakdown[] }>(5, { items: [] }).items,
      failures: value<{ items: AiOpsFailure[] }>(6, { items: [] }).items,
      jobs: value<Page<AiOpsJob>>(7, { page: filters.page, page_size: filters.pageSize || 25, total: 0, items: [] }),
      usage: value<Page<AiOpsUsage>>(8, { page: filters.usagePage || 1, page_size: filters.usagePageSize || 25, total: 0, items: [] }),
      coverage: null,
      // The pipeline snapshot was introduced after the original AI Operations API.
      // A rolling deployment may briefly serve the prior API; keep AI metrics usable.
      pipeline: pipelineUnavailable ? undefined : normalizePipelineSnapshot(value<PipelineSnapshot | null>(9, null)),
    },
  };
}

export function filtersFromSearch(search: string): AiOpsFilters {
  const params = new URLSearchParams(search);
  const requestedRange = Number(params.get("range"));
  const range = params.get("range") === "all" ? 0
    : requestedRange === 30 || requestedRange === 90 || requestedRange === 180 ? requestedRange : 90;
  return {
    range,
    provider: params.get("provider") || "",
    model: params.get("model") || "",
    processingMode: params.get("mode") || "",
    metadataProfile: params.get("profile") || "",
    status: params.get("status") || "",
    page: Math.max(1, Number(params.get("page")) || 1),
    pageSize: [25, 50, 100].includes(Number(params.get("page_size"))) ? Number(params.get("page_size")) as 25 | 50 | 100 : 25,
    usagePage: Math.max(1, Number(params.get("usage_page")) || 1),
    usagePageSize: [25, 50, 100].includes(Number(params.get("usage_page_size"))) ? Number(params.get("usage_page_size")) as 25 | 50 | 100 : 25,
    pipelinePage: params.has("pipeline_page") ? Math.max(1, Number(params.get("pipeline_page")) || 1) : undefined,
    pipelinePageSize: params.has("pipeline_page_size") && [25, 50, 100].includes(Number(params.get("pipeline_page_size"))) ? Number(params.get("pipeline_page_size")) as 25 | 50 | 100 : undefined,
  };
}

export function searchFromFilters(filters: AiOpsFilters, tab: string, refreshSeconds = 0): string {
  const params = new URLSearchParams();
  if (filters.range !== 90) params.set("range", filters.range === 0 ? "all" : String(filters.range));
  if (filters.provider) params.set("provider", filters.provider);
  if (filters.model) params.set("model", filters.model);
  if (filters.processingMode) params.set("mode", filters.processingMode);
  if (filters.metadataProfile) params.set("profile", filters.metadataProfile);
  if (filters.status) params.set("status", filters.status);
  if (filters.page > 1) params.set("page", String(filters.page));
  if ((filters.pageSize || 25) !== 25) params.set("page_size", String(filters.pageSize));
  if ((filters.usagePage || 1) > 1) params.set("usage_page", String(filters.usagePage));
  if ((filters.usagePageSize || 25) !== 25) params.set("usage_page_size", String(filters.usagePageSize));
  if ((filters.pipelinePage || 1) > 1) params.set("pipeline_page", String(filters.pipelinePage));
  if ((filters.pipelinePageSize || 25) !== 25) params.set("pipeline_page_size", String(filters.pipelinePageSize));
  if (tab !== "overview") params.set("tab", tab);
  if (refreshSeconds) params.set("refresh", String(refreshSeconds));
  return params.toString();
}

type MutationResult = { audit?: import("./types").AiOpsAudit; [key: string]: unknown };

async function mutate<T extends MutationResult>(url: string, method: "POST" | "PATCH" | "PUT", body: object, fetcher: Fetcher = fetch): Promise<T> {
  const response = await fetcher(url, {
    method,
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: string | { message?: string } };
    const detail = typeof payload.detail === "string" ? payload.detail : payload.detail?.message;
    throw new AiOperationsApiError(detail || `Request failed (${response.status})`, response.status);
  }
  return response.json() as Promise<T>;
}

export const fetchAiOperationsConfiguration = (fetcher: Fetcher = fetch) =>
  read<AiOpsConfiguration>("/api/v1/admin/ai-operations/configuration", fetcher);

export const updateAiOperationsConfiguration = (body: object, fetcher: Fetcher = fetch) =>
  mutate("/api/v1/admin/ai-operations/configuration", "PATCH", body, fetcher);

export const updateAiDefaults = (body: object, fetcher: Fetcher = fetch) =>
  mutate("/api/v1/admin/ai-operations/controls/defaults", "PATCH", body, fetcher);

export const updateAiProvider = (provider: AiOpsProvider, body: object, fetcher: Fetcher = fetch) =>
  mutate(`/api/v1/admin/ai-operations/providers/${provider}`, "PATCH", body, fetcher);

export const setAiProviderPaused = (provider: AiOpsProvider, paused: boolean, reason: string, fetcher: Fetcher = fetch) =>
  mutate(`/api/v1/admin/ai-operations/providers/${provider}/${paused ? "pause" : "resume"}`, "POST", { reason }, fetcher);

export const setTenantAiPaused = (paused: boolean, reason: string, fetcher: Fetcher = fetch) =>
  mutate(`/api/v1/admin/ai-operations/controls/${paused ? "pause" : "resume"}`, "POST", { reason }, fetcher);

export const updateAiBudget = (body: object, fetcher: Fetcher = fetch) =>
  mutate("/api/v1/admin/ai-operations/budget", "PATCH", body, fetcher);

export const setGlobalAiEmergencyStop = (stopped: boolean, reason: string, fetcher: Fetcher = fetch) =>
  mutate("/api/v1/admin/ai-governance/runtime-controls/global", "PUT", { stopped, reason }, fetcher);
export type AiJobMutationResult = {
  tenant_id: string;
  outcome: "retry_requested" | "force_retry_requested" | "already_requested" | "queued_cancelled" | "running_cancel_requested" | "provider_batch_cancel_requested";
  job: Record<string, unknown>;
};

export const retryAiOperationsJob = (jobId: string, reason: string, force = false, fetcher: Fetcher = fetch) =>
  mutate<AiJobMutationResult>(
    `/api/v1/admin/ai-operations/jobs/${encodeURIComponent(jobId)}/retry`, "POST", { reason, force }, fetcher,
  );

export type AiBulkRetryResult = {
  tenant_id: string;
  error_code: string;
  matched: number;
  retried: number;
  skipped: number;
  items?: Array<{ job_id: string; outcome: string }>;
  audit?: import("./types").AiOpsAudit;
};

export const retryAiOperationsJobsByError = (
  errorCode: string, reason: string, limit = 1000, fetcher: Fetcher = fetch,
) => mutate<AiBulkRetryResult>(
  "/api/v1/admin/ai-operations/jobs/retry-by-error", "POST",
  { error_code: errorCode, reason, limit }, fetcher,
);

export const cancelAiOperationsJob = (jobId: string, reason: string, fetcher: Fetcher = fetch) =>
  mutate<AiJobMutationResult>(
    `/api/v1/admin/ai-operations/jobs/${encodeURIComponent(jobId)}/cancel`, "POST", { reason }, fetcher,
  );

export type AiOperationsExportType = "daily" | "usage" | "failures" | "jobs";

export function aiOperationsExportUrl(
  exportType: AiOperationsExportType,
  filters: AiOpsFilters,
  now = new Date(),
): string {
  const params = filteredParams(filters, isoRange(filters.range, now));
  params.set("row_limit", "5000");
  return `/api/v1/admin/ai-operations/exports/${exportType}.csv?${params}`;
}
export const runSearchCoverageAudit = (body: { verify_elasticsearch?: boolean; limit?: number }, fetcher: Fetcher = fetch) =>
  mutate("/api/v1/admin/ai-operations/coverage/audit", "POST", body, fetcher);

export const repairSearchCoverage = (body: { confirmed: true; limit: number; verify_elasticsearch?: boolean; repair_projections: boolean; repair_indexes: boolean }, fetcher: Fetcher = fetch) =>
  mutate("/api/v1/admin/ai-operations/coverage/repair", "POST", body, fetcher);
