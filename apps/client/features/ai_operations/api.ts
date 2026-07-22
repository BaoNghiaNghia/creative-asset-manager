import type {
  AiOpsDaily, AiOpsDashboardData, AiOpsFailure, AiOpsFilters, AiOpsJob,
  AiOpsConfiguration, AiOpsProvider, AiOpsProviderBreakdown, AiOpsSummary, AiOpsUsage, Page,
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

function isoRange(days: number, now: Date): { from: string; to: string } {
  const from = new Date(now);
  from.setUTCDate(from.getUTCDate() - days);
  return { from: from.toISOString(), to: now.toISOString() };
}

function filteredParams(filters: AiOpsFilters, range: { from: string; to: string }): URLSearchParams {
  const params = new URLSearchParams({ from: range.from, to: range.to });
  if (filters.provider) params.set("provider", filters.provider);
  if (filters.model) params.set("model", filters.model);
  if (filters.processingMode) params.set("processing_mode", filters.processingMode);
  if (filters.metadataProfile) params.set("metadata_profile", filters.metadataProfile);
  return params;
}

export type DashboardResult = {
  data: AiOpsDashboardData;
  errors: string[];
  unauthorized: boolean;
};

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
  const jobs = new URLSearchParams(current); jobs.set("page", String(filters.page)); jobs.set("page_size", "25");
  const usage = new URLSearchParams(current); usage.set("page", "1"); usage.set("page_size", "100");
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
  ] as const;
  const settled = await Promise.allSettled(calls);
  const errors = settled.flatMap(item => item.status === "rejected" ? [String(item.reason?.message || "Request failed")] : []);
  const unauthorized = settled.some(item => item.status === "rejected" && item.reason instanceof AiOperationsApiError && [401, 403].includes(item.reason.status));
  const value = <T,>(index: number, fallback: T): T => settled[index].status === "fulfilled"
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
      jobs: value<Page<AiOpsJob>>(7, { page: filters.page, page_size: 25, total: 0, items: [] }),
      usage: value<Page<AiOpsUsage>>(8, { page: 1, page_size: 100, total: 0, items: [] }),
    },
  };
}

export function filtersFromSearch(search: string): AiOpsFilters {
  const params = new URLSearchParams(search);
  const requestedRange = Number(params.get("range"));
  const range = requestedRange === 30 || requestedRange === 90 ? requestedRange : 7;
  return {
    range,
    provider: params.get("provider") || "",
    model: params.get("model") || "",
    processingMode: params.get("mode") || "",
    metadataProfile: params.get("profile") || "",
    page: Math.max(1, Number(params.get("page")) || 1),
  };
}

export function searchFromFilters(filters: AiOpsFilters, tab: string): string {
  const params = new URLSearchParams();
  if (filters.range !== 7) params.set("range", String(filters.range));
  if (filters.provider) params.set("provider", filters.provider);
  if (filters.model) params.set("model", filters.model);
  if (filters.processingMode) params.set("mode", filters.processingMode);
  if (filters.metadataProfile) params.set("profile", filters.metadataProfile);
  if (filters.page > 1) params.set("page", String(filters.page));
  if (tab !== "overview") params.set("tab", tab);
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