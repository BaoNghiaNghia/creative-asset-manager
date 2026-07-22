import type {
  AiOpsDaily, AiOpsFailure, AiOpsProviderBreakdown, AiOpsUsage,
} from "../../features/ai_operations";

export type ChartDatum = { label: string; values: Record<string, number> };

export function dailyStatusChart(items: AiOpsDaily[]): ChartDatum[] {
  return items.map(item => ({
    label: item.date,
    values: { Completed: item.completed, Failed: item.failed },
  }));
}

export function dailyProviderCostChart(items: AiOpsUsage[]): ChartDatum[] {
  const rows = new Map<string, Record<string, number>>();
  for (const item of items) {
    const date = item.occurred_at.slice(0, 10);
    const value = rows.get(date) || {};
    value[item.provider] = (value[item.provider] || 0) + (item.estimated_cost_micros || 0);
    rows.set(date, value);
  }
  return [...rows].sort(([a], [b]) => a.localeCompare(b)).map(([label, values]) => ({ label, values }));
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

function csvCell(value: unknown): string {
  let text = value == null ? "" : String(value);
  if (/^[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

export function usageCsv(items: AiOpsUsage[]): string {
  const columns = [
    "date", "provider", "model", "mode", "input_units", "output_units",
    "estimated_cost_micros", "reconciled_cost_micros", "currency",
  ];
  const rows = items.map(item => [
    item.occurred_at, item.provider, item.model, item.processing_mode,
    item.input_units, item.output_units, item.estimated_cost_micros,
    item.provider_reported_cost_micros, item.currency,
  ]);
  return [columns, ...rows].map(row => row.map(csvCell).join(",")).join("\r\n");
}

export function downloadCsv(csv: string, filename: string): void {
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
