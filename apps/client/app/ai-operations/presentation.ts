import type {
  AiOpsDaily, AiOpsFailure, AiOpsProviderBreakdown,
} from "../../features/ai_operations";

export type ChartDatum = { label: string; values: Record<string, number> };

export function dailyStatusChart(items: AiOpsDaily[]): ChartDatum[] {
  return items.map(item => ({
    label: item.date,
    values: { Completed: item.completed, Failed: item.failed },
  }));
}

export function dailyProviderCostChart(items: AiOpsDaily[]): ChartDatum[] {
  return items.map(item => ({
    label: item.date,
    values: item.provider_estimated_cost_micros || {},
  }));
}

export function providerVolumeChart(items: AiOpsProviderBreakdown[]): ChartDatum[] {
  return items.map(item => ({
    label: `${providerLabel(item.provider)} · ${modeLabel(item.processing_mode)}`,
    values: { Analyses: item.count },
  }));
}

export function failureChart(items: AiOpsFailure[]): ChartDatum[] {
  return items.map(item => ({ label: item.error_code, values: { Failures: item.count } }));
}

export function providerLabel(value: string | null | undefined): string {
  if (value === "gemini") return "Google Gemini";
  if (value === "openai") return "OpenAI";
  return value || "—";
}

export function modeLabel(value: string | null | undefined): string {
  return value ? value[0].toUpperCase() + value.slice(1) : "—";
}

export function formatCost(micros: number | null | undefined, currency = "USD"): string {
  if (micros == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: 4,
  }).format(micros / 1_000_000);
}

export function formatDuration(start: string | null, end: string | null): string {
  if (!start || !end) return "—";
  const milliseconds = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "—";
  if (milliseconds < 1000) return `${milliseconds} ms`;
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(1)} s`;
  return `${(milliseconds / 60_000).toFixed(1)} min`;
}
