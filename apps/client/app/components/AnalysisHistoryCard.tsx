import { providerLabel } from "./AnalyzeMetadataDialog";

export function AnalysisHistoryCard({ analysis, showCost }: { analysis: Record<string, any>; showCost: boolean }) {
  const usage = analysis.usage && typeof analysis.usage === "object" ? analysis.usage as Record<string, unknown> : null;
  const reported = typeof usage?.provider_reported_cost_micros === "number" ? usage.provider_reported_cost_micros : null;
  const estimated = typeof usage?.locally_estimated_cost_micros === "number" ? usage.locally_estimated_cost_micros : null;
  const currency = typeof usage?.currency === "string" ? usage.currency : "USD";
  const mode = String(analysis.pipeline_version || "").startsWith("batch") ? "Batch" : "Single";

  return <article className="analysis-history-card">
    <header><div><b>{providerLabel(String(analysis.ai_provider || ""))}</b><span>{String(analysis.ai_model || "Model unavailable")}</span></div><em className={String(analysis.status)}>{String(analysis.status)}</em></header>
    <dl>
      <div><dt>Mode</dt><dd>{mode}</dd></div>
      <div><dt>Profile</dt><dd>{String(analysis.metadata_profile || "Unknown")} / {String(analysis.metadata_profile_version || "-")}</dd></div>
      <div><dt>Attempts</dt><dd>{String(analysis.attempt_count ?? 0)}</dd></div>
      {showCost && <div><dt>Usage</dt><dd>{usage ? `${String(usage.input_units ?? 0)} in / ${String(usage.output_units ?? 0)} out` : "Unavailable"}</dd></div>}
      {showCost && <div><dt>Cost</dt><dd>{reported !== null ? `${formatMicros(reported)} ${currency} actual` : estimated !== null ? `${formatMicros(estimated)} ${currency} estimated` : "Unavailable"}</dd></div>}
    </dl>
    {(analysis.last_error_code || analysis.last_error_message) && <div className="analysis-history-error" role="note"><b>{String(analysis.last_error_code || "Analysis failed")}</b><span>{String(analysis.last_error_message || "Retry may be available.")}</span></div>}
  </article>;
}

function formatMicros(value: number): string {
  return (value / 1_000_000).toFixed(4);
}
