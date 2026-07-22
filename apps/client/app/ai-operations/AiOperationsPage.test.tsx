// @ts-expect-error Vitest executes this test-only import in Node.
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import {
  fetchAiOperationsDashboard,
  filtersFromSearch,
  searchFromFilters,
  type AiOpsDashboardData,
  type AiOpsFilters,
} from "../../features/ai_operations";

import { routeForPath } from "../AppRoute";
import {
  AiOperationsContent,
  AiOperationsFilters,
  AiOperationsShell,
  emptyDashboard,
} from "./AiOperationsPage";

const noop = () => undefined;
const filters: AiOpsFilters = {
  range: 30,
  provider: "openai",
  model: "gpt-test",
  processingMode: "batch",
  metadataProfile: "catalog",
  page: 2,
};

const summary = {
  requested: 12,
  queued: 2,
  running: 1,
  completed: 8,
  failed: 2,
  cancelled: 0,
  budget_blocked: 1,
  success_rate: 0.8,
  input_units: 1_000,
  output_units: 200,
  cost: {
    estimated_cost_micros: 1_200_000,
    provider_reported_cost_micros: 1_100_000,
    reconciled_cost_micros: 1_050_000,
    currency: "USD",
  },
  latency: { average_ms: 200, p50_ms: 150, p95_ms: 450 },
  average_cost_per_completed_asset_micros: 131_250,
};

const data: AiOpsDashboardData = {
  summary,
  today: {
    ...summary,
    completed: 3,
    failed: 1,
    cost: { ...summary.cost, estimated_cost_micros: 250_000 },
  },
  month: {
    ...summary,
    cost: { ...summary.cost, estimated_cost_micros: 900_000 },
  },
  daily: [{
    date: "2026-07-21",
    requested: 6,
    completed: 4,
    failed: 1,
    estimated_cost_micros: 300_000,
    provider_reported_cost_micros: 250_000,
    reconciled_cost_micros: 275_000,
    provider_estimated_cost_micros: { gemini: 100_000, openai: 200_000 },
    average_latency_ms: 200,
    p95_latency_ms: 450,
  }],
  providers: [{
    provider: "openai",
    model: "gpt-test",
    processing_mode: "batch",
    count: 6,
    completed: 5,
    failed: 1,
    success_rate: 5 / 6,
    average_latency_ms: 200,
    p95_latency_ms: 450,
    input_units: 1_000,
    output_units: 200,
    estimated_cost_micros: 300_000,
    provider_reported_cost_micros: 250_000,
    reconciled_cost_micros: 275_000,
    currency: "USD",
  }],
  todayProviders: [],
  failures: [{ source: "analysis", error_code: "provider_timeout", count: 2 }],
  jobs: {
    page: 2,
    page_size: 25,
    total: 60,
    items: [{
      id: "job-1",
      job_type: "asset_analyze",
      entity_type: "asset_ai_analysis",
      entity_id: "analysis-1",
      provider: "openai",
      status: "completed",
      priority: 10,
      attempt_count: 1,
      max_attempts: 3,
      next_attempt_at: "2026-07-21T10:00:00Z",
      claimed_at: "2026-07-21T10:00:00Z",
      lease_expires_at: null,
      created_at: "2026-07-21T09:59:00Z",
      updated_at: "2026-07-21T10:00:02Z",
      completed_at: "2026-07-21T10:00:02Z",
      error: { code: "provider_timeout", retryable: false },
    }],
  },
  usage: {
    page: 1,
    page_size: 100,
    total: 1,
    items: [{
      id: "usage-1",
      asset_id: "asset-1",
      analysis_id: "analysis-1",
      job_id: "job-1",
      provider: "openai",
      model: "gpt-test",
      processing_mode: "batch",
      metadata_profile: "catalog",
      metadata_profile_version: "1",
      input_units: 1_000,
      output_units: 200,
      media_units: 1,
      estimated_cost_micros: 300_000,
      provider_reported_cost_micros: 250_000,
      currency: "USD",
      latency_ms: 2_000,
      outcome: "completed",
      retry_count: 0,
      occurred_at: "2026-07-21T10:00:02Z",
    }],
  },
};

function render(tab: "overview" | "processing" | "cost" = "overview", overrides = {}) {
  return renderToStaticMarkup(<AiOperationsContent
    data={data}
    filters={filters}
    tab={tab}
    onTab={noop}
    onFilters={noop}
    onRetry={noop}
    {...overrides}
  />);
}

describe("AI Operations dashboard", () => {
  it("routes normally and renders navigation without opening a new tab", () => {
    expect(routeForPath("/ai-operations")).toBe("ai-operations");
    expect(routeForPath("/ai-operations/processing")).toBe("ai-operations");
    expect(routeForPath("/")).toBe("explorer");
    const markup = renderToStaticMarkup(<AiOperationsShell><p>Dashboard</p></AiOperationsShell>);
    expect(markup).toContain('href="/ai-operations"');
    expect(markup).toContain("AI Operations");
    expect(markup).not.toContain('target="_blank"');
  });

  it("preserves all filters and active tab in URL state", () => {
    const query = searchFromFilters(filters, "processing");
    expect(filtersFromSearch(query)).toEqual(filters);
    expect(new URLSearchParams(query).get("tab")).toBe("processing");
    const markup = renderToStaticMarkup(<AiOperationsFilters filters={filters} models={["gpt-test"]} profiles={["catalog"]} onChange={noop} />);
    for (const label of ["Date range", "Provider", "Model", "Processing mode", "Metadata profile"]) expect(markup).toContain(label);
  });

  it("renders all KPI cards and accessible chart/table equivalents", () => {
    const markup = render();
    for (const value of [
      "Processed today", "Completed", "Failed", "Running", "Queued", "Success rate",
      "Estimated cost today", "Estimated cost this month", "Daily processing",
      "Daily estimated cost by provider", "Provider and mode volume",
      "Failure categories", "Latency", "View chart data table",
    ]) expect(markup).toContain(value);
    expect(markup).toContain('role="img"');
    expect(markup).toContain("provider_timeout");
  });

  it("renders processing details, stable errors, pagination and the real asset link", () => {
    const markup = render("processing");
    for (const value of ["AI processing jobs", "OpenAI", "gpt-test", "Batch", "catalog", "1/3", "2.0 s", "provider_timeout", "Page 2 of 3"]) expect(markup).toContain(value);
    expect(markup).toContain("asset-1");
    expect(markup).toContain("asset=asset-1");
    expect(markup).not.toContain(">analysis-1</code>");
  });

  it("keeps estimated, provider-reported and reconciled cost clearly separated", () => {
    const markup = render("cost");
    for (const value of ["Cost &amp; Usage", "Estimated total", "Provider-reported total", "Reconciled total", "$1.20", "$1.10", "$1.05", "Input units", "Output units", "Export usage CSV"]) expect(markup).toContain(value);
    expect(markup).toContain("/api/v1/admin/ai-operations/exports/usage.csv");
  });

  it("renders loading, partial failure, empty and unauthorized states", () => {
    expect(render("overview", { loading: true })).toContain('aria-busy="true"');
    const partial = render("overview", { errors: ["Daily metrics unavailable"] });
    expect(partial).toContain('role="alert"');
    expect(partial).toContain("Daily metrics unavailable");
    expect(partial).toContain("Retry");
    expect(renderToStaticMarkup(<AiOperationsContent data={emptyDashboard()} filters={{ ...filters, page: 1 }} tab="overview" onTab={noop} onFilters={noop} onRetry={noop} />)).toContain("No AI activity in this period");
    const denied = render("overview", { unauthorized: true });
    expect(denied).toContain("AI Operations access required");
    expect(denied).not.toContain("Export usage CSV");
  });

  it("has responsive breakpoints, horizontal table scrolling and no rendered secrets", () => {
    const styles = readFileSync(new URL("../../styles/ai-operations.css", import.meta.url), "utf8");
    expect(styles).toContain("@media(max-width:1050px)");
    expect(styles).toContain("@media(max-width:720px)");
    expect(styles).toContain("@media(max-width:460px)");
    expect(styles).toContain(".ops-table-scroll{max-width:100%;overflow:auto");
    const markup = render("processing").toLowerCase();
    for (const secret of ["api_key", "signed_url", "provider_request_id", "sk-"]) expect(markup).not.toContain(secret);
  });

  it("treats a forbidden dashboard response as unauthorized", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ detail: "Forbidden" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
    const result = await fetchAiOperationsDashboard(
      filters, fetcher, new Date("2026-07-22T00:00:00Z"),
    );
    expect(result.unauthorized).toBe(true);
    expect(result.errors).toEqual(["Forbidden"]);
  });
});
