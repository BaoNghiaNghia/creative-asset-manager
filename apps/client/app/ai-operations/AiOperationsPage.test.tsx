// @ts-expect-error Vitest executes this test-only import in Node.
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import {
  cancelAiOperationsJob,
  fetchAiOperationsDashboard,
  filtersFromSearch,
  normalizePipelineSnapshot,
  retryAiOperationsJob,
  searchFromFilters,
  type AiOpsDashboardData,
  type AiOpsFilters,
} from "../../features/ai_operations";

import { mayViewAiOperations } from "../components/Sidebar";
import { routeForPath } from "../AppRoute";
import {
  AiOperationsContent,
  AiOperationsFilters,
  AiOperationsShell,
  eligibleProcessingAction,
  formatProcessingDuration,
  emptyDashboard,
  handleTabKeyDown,
  pageFilters,
  usagePageFilters,
  visiblePages,
  PipelineOverview,
  ProcessingJobAction,
  StatusText,
} from "./AiOperationsPage";

const noop = () => undefined;
const filters: AiOpsFilters = {
  range: 30,
  provider: "openai",
  model: "gpt-test",
  processingMode: "batch",
  metadataProfile: "catalog",
  status: "",
  page: 2,
  pageSize: 25,
  usagePage: 1,
  usagePageSize: 25,
};

const summary = {
  requested: 12,
  queued: 2,
  running: 1,
  completed: 8,
  failed: 2,
  cancelled: 0,
  budget_blocked: 1,
  deferred: 0,
  next_deferred_retry_at: null,
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
      asset_id: "asset-1",
      provider: "openai",
      status: "completed",
      priority: 10,
      attempt_count: 1,
      max_attempts: 3,
      processing_duration_ms: 2_000,
      next_attempt_at: "2026-07-21T10:00:00Z",
      is_deferred: false,
      waiting_reason: null,
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
    expect(markup).toContain("Running");
    expect(markup).toContain("Currently processing");
    expect(markup).toContain("Queued");
    expect(markup).toContain("Waiting to start");
    expect(markup).toContain('role="img"');
    expect(markup).toContain("provider_timeout");
  });

  it("explains deferred Gemini work with its next retry time", () => {
    const markup = render("overview", { data: { ...data, summary: { ...summary, deferred: 3, next_deferred_retry_at: "2026-07-22T10:30:00Z" } } });
    expect(markup).toContain("Gemini quota is temporarily busy");
    expect(markup).toContain("3 analyses will retry automatically");
    expect(markup).toContain("Next retry");
  });

  it("formats only accumulated worker execution time for completed jobs", () => {
    expect(formatProcessingDuration(2_000)).toBe("2.0 s");
    expect(formatProcessingDuration(125_000)).toBe("2.1 min");
    expect(formatProcessingDuration(0)).toBe("—");
  });

  it("renders processing details, stable errors, pagination and the real asset link", () => {
    const markup = render("processing", { data: { ...data, usage: { ...data.usage, total: 0, items: [] } } });
    for (const value of ["AI processing jobs", "OpenAI", "gpt-test", "Batch", "catalog", "1/3", "2.0 s", "provider_timeout", "Showing 26-50 of 60", "Items per page"]) expect(markup).toContain(value);
    expect(markup).toContain("asset-1");
    expect(markup).toContain("asset=asset-1");
    expect(markup).not.toContain(">analysis-1</code>");
  });

  it("keeps estimated, provider-reported and reconciled cost clearly separated", () => {
    const markup = render("cost");
    for (const value of ["Cost &amp; Usage", "AI cost and usage records", "Showing 1-1 of 1", "Items per page", "Estimated total", "Provider-reported total", "Reconciled total", "$1.20", "$1.10", "$1.05", "Input units", "Output units", "Export usage CSV"]) expect(markup).toContain(value);
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
    expect(styles).toContain("@media(max-width:1500px)");
    expect(styles).toContain("grid-template-columns:minmax(150px,.82fr)");
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

  it("maps each dashboard response to its matching field", async () => {
    const responses = [
      summary,
      { ...summary, completed: 3, cost: { ...summary.cost, estimated_cost_micros: 250_000 } },
      { ...summary, completed: 8, cost: { ...summary.cost, estimated_cost_micros: 900_000 } },
      { items: data.daily },
      { items: data.providers },
      { items: [] },
      { items: data.failures },
      data.jobs,
      data.usage,
      null,
    ];
    let index = 0;
    const fetcher = vi.fn(async () => new Response(JSON.stringify(responses[index++]), {
      status: 200, headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
    const result = await fetchAiOperationsDashboard(filters, fetcher, new Date("2026-07-22T00:00:00Z"));
    expect(result.data.summary?.cost.estimated_cost_micros).toBe(1_200_000);
    expect(result.data.today?.cost.estimated_cost_micros).toBe(250_000);
    expect(result.data.month?.cost.estimated_cost_micros).toBe(900_000);
    expect(result.data.daily).toEqual(data.daily);
    expect(result.data.jobs).toEqual(data.jobs);
    expect(result.data.usage).toEqual(data.usage);
  });

  it("normalizes a pre-pagination pipeline response without crashing", () => {
    const result = normalizePipelineSnapshot({
      generated_at: "2026-07-27T00:00:00Z",
      latest_source_sync: null,
      overall: { source_items_discovered: 0, supported_assets: 0, unsupported_assets: 0, completed: 0, active: 0, queued: 0, failed: 0, skipped: 0, indexed_percentage: null, throughput_today: 0, asset_progress: [] },
      stages: [], active_job: null, failure_groups: [],
      recent_assets: [{ asset_id: "asset-1", filename: "legacy.jpg", state: "indexed", stage_statuses: {}, updated_at: "2026-07-27T00:00:00Z", error_code: null }],
    } as unknown as AiOpsDashboardData["pipeline"]);
    expect(result?.recent_assets.items).toHaveLength(1);
    expect(result?.recent_assets.total).toBe(1);
  });

  it("keeps AI Operations usable while an older API returns 404 for pipeline", async () => {
    const responses = [summary, summary, summary, { items: [] }, { items: [] }, { items: [] }, { items: [] }, data.jobs, data.usage];
    let index = 0;
    const fetcher = vi.fn(async () => {
      if (index++ === 9) return new Response(JSON.stringify({ detail: "Not Found" }), { status: 404, headers: { "Content-Type": "application/json" } });
      return new Response(JSON.stringify(responses[index - 1]), { status: 200, headers: { "Content-Type": "application/json" } });
    }) as unknown as typeof fetch;
    const result = await fetchAiOperationsDashboard(filters, fetcher, new Date("2026-07-22T00:00:00Z"));
    expect(result.errors).toEqual([]);
    expect(result.data.pipeline).toBeUndefined();
    expect(renderToStaticMarkup(<PipelineOverview pipeline={result.data.pipeline} />)).toContain("API is updated and restarted");
  });
});


describe("AI Operations interactions", () => {
  it("renders keyboard tabs, auto-refresh choices and preserves refresh in URL state", () => {
    const markup = render("processing", { refreshSeconds: 30, lastUpdated: new Date("2026-07-22T10:00:00Z") });
    expect(markup).toContain('role="tablist"');
    expect(markup).toContain('role="tab"');
    expect(markup).toContain('role="tabpanel"');
    expect(markup).toContain('aria-selected="true"');
    expect(markup).toContain("Items per page");
    for (const value of ["Off", "15s", "30s", "60s"]) expect(markup).toContain(value);
    expect(markup).toContain("Last updated");
    expect(markup).toContain("10:00:00");
    const query = searchFromFilters(filters, "processing", 30);
    expect(new URLSearchParams(query).get("refresh")).toBe("30");
    expect(filtersFromSearch(query)).toEqual(filters);
  });

  it("updates date/provider/model/mode/profile filters and resets pagination", () => {
    const changes: AiOpsFilters[] = [];
    const tree = AiOperationsFilters({ filters, models: ["gpt-test"], profiles: ["catalog"], onChange: value => changes.push(value) }) as any;
    const fields = tree.props.children as any[];
    fields[0].props.children[1].props.onChange({ target: { value: "90" } });
    fields[1].props.children[1].props.onChange({ target: { value: "gemini" } });
    fields[2].props.children[1].props.onChange({ target: { value: "gemini-test" } });
    fields[3].props.children[1].props.onChange({ target: { value: "single" } });
    fields[4].props.children[1].props.onChange({ target: { value: "creative" } });
    expect(changes.map(item => item.page)).toEqual([1, 1, 1, 1, 1]);
    expect(changes[0].range).toBe(90);
    expect(changes[1].provider).toBe("gemini");
    expect(changes[2].model).toBe("gemini-test");
    expect(changes[3].processingMode).toBe("single");
    expect(changes[4].metadataProfile).toBe("creative");
  });

  it("supports arrow-key tab navigation and bounded pagination", () => {
    const selected: string[] = [];
    const focus = vi.fn();
    const event = {
      key: "ArrowRight",
      preventDefault: vi.fn(),
      currentTarget: { querySelectorAll: () => [{ focus }, { focus }, { focus }, { focus }, { focus }] },
    } as any;
    handleTabKeyDown(event, "overview", value => selected.push(value));
    expect(selected).toEqual(["processing"]);
    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(focus).toHaveBeenCalledOnce();
    expect(pageFilters(filters, 3).page).toBe(3);
    expect(pageFilters(filters, 0).page).toBe(1);
    expect(usagePageFilters(filters, 3).usagePage).toBe(3);
    expect(visiblePages(2, 18)).toEqual([1, 2, 3, 4, 5, "ellipsis", 18]);
    expect(visiblePages(10, 18)).toEqual([1, "ellipsis", 9, 10, 11, "ellipsis", 18]);
  });

  it("keeps budget-blocked separate and uses server terminal success rate", () => {
    const markup = render();
    expect(markup).toContain("Budget blocked");
    expect(markup).toContain(">1</strong>");
    expect(markup).toContain("80.0%");
  });

  it("maps only backend-eligible processing actions", () => {
    const job = data.jobs.items[0];
    expect(eligibleProcessingAction({ ...job, status: "failed" })).toBe("retry");
    expect(eligibleProcessingAction({ ...job, status: "pending" })).toBe("cancel");
    expect(eligibleProcessingAction({ ...job, status: "retry" })).toBe("cancel");
    expect(eligibleProcessingAction({ ...job, status: "processing" })).toBe("cancel");
    expect(eligibleProcessingAction({ ...job, status: "completed" })).toBeNull();
    expect(eligibleProcessingAction({ ...job, status: "failed", error: { code: "operation_cancelled", retryable: false } })).toBeNull();
  });

  it("renders deferred Gemini jobs as waiting and keeps normal pending jobs queued", () => {
    const waiting = { ...data.jobs.items[0], status: "pending", is_deferred: true, waiting_reason: "gemini_quota_deferred", next_attempt_at: "2099-01-01T10:00:00Z" };
    expect(renderToStaticMarkup(<StatusText status={waiting.status} isDeferred={waiting.is_deferred} nextAttemptAt={waiting.next_attempt_at} />)).toContain("Waiting for Gemini quota");
    expect(renderToStaticMarkup(<StatusText status="failed" />)).toContain("Failed");
    expect(renderToStaticMarkup(<StatusText status="pending" />)).toContain("Queued");
    expect(eligibleProcessingAction(waiting)).toBe("force_retry");
  });

  it("hides mutation actions without their specific permissions and exposes AI Operations by read permission", () => {
    const failed = { ...data.jobs.items[0], status: "failed" };
    expect(renderToStaticMarkup(<ProcessingJobAction job={failed} permissions={[]} onAccepted={noop} />)).toBe("");
    expect(renderToStaticMarkup(<ProcessingJobAction job={failed} permissions={["ai_jobs.retry"]} onAccepted={noop} />)).toContain("Retry failed job");
    expect(mayViewAiOperations(["ai_operations.read"])).toBe(true);
    expect(mayViewAiOperations(["assets.read"])).toBe(false);
  });

  it("sends audited retry and cancellation reasons to the supported endpoints", async () => {
    const fetcher = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => new Response(JSON.stringify({
      tenant_id: "tenant-a", outcome: "retry_requested", job: {},
    }), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    await retryAiOperationsJob("job/a", "transient provider failure", false, fetcher);
    await cancelAiOperationsJob("job/a", "operator requested cancellation", fetcher);
    expect(String((fetcher as any).mock.calls[0][0])).toContain("/jobs/job%2Fa/retry");
    expect(String((fetcher as any).mock.calls[0][1]?.body)).toContain("transient provider failure");
    expect(String((fetcher as any).mock.calls[1][0])).toContain("/jobs/job%2Fa/cancel");
    expect(String((fetcher as any).mock.calls[1][1]?.body)).toContain("operator requested cancellation");
  });

  it("wires visibility-aware intervals and cleanup abort without exposing sensitive fields", () => {
    const source = readFileSync(new URL("./AiOperationsPage.tsx", import.meta.url), "utf8");
    expect(source).toContain("document.visibilityState");
    expect(source).toContain("requests.current.abort()");
    expect(source).toContain("window.clearInterval(timer)");
    expect(source).not.toContain("raw_job_payload");
    expect(source).not.toContain("signed_url");
    expect(source).not.toContain("provider_api_key");
  });
});

describe("Search Coverage card", () => {
  it("renders tenant coverage and administrator-only repair controls", () => {
    const markup = renderToStaticMarkup(<AiOperationsContent
      data={{ ...data, coverage: {
        completed_analysis_assets: 12, current_projection_assets: 10, v3_indexed_documents: 8,
        projection_missing: 1, projection_stale: 1, indexing_backlog: 0, search_failed: 0,
        database_indexed_document_missing: 2, coverage_percent: 66.7,
        last_audited_at: "2026-07-26T00:00:00Z", elasticsearch_verification_included: true,
        repair_jobs: { queued: 1, running: 0, completed: 3, failed: 0 },
      } }}
      filters={filters} tab="overview" onTab={noop} onFilters={noop} onRetry={noop}
      permissions={["search.rebuild"]}
    />);
    expect(markup).toContain("Search Coverage");
    expect(markup).toContain("Run coverage audit");
    expect(markup).toContain("Repair missing search data");
    expect(markup).toContain("Database and Elasticsearch disagree");
  });
});


  it("renders the full pipeline flow and current active download", () => {
    const stages = [
      ["source_asset_download", "Download"],
      ["asset_store", "Store"],
      ["asset_analyze", "AI Analyze"],
      ["search_projection_build", "Search Projection"],
      ["asset_index", "Elasticsearch Index"],
    ].map(([key, label]) => ({ key, label, subtitle: "Pipeline stage", total: 3, pending: 1, eligible_now: 1, waiting: 0, processing: key === "source_asset_download" ? 1 : 0, completed: 1, failed: 0, percentage: 33.3, oldest_pending_at: null }));
    const markup = renderToStaticMarkup(<PipelineOverview pipeline={{
      generated_at: "2026-07-27T00:00:00Z",
      latest_source_sync: { mode: "full", status: "completed", pages_count: 2, items_seen_count: 8, jobs_created_count: 4, started_at: "2026-07-27T00:00:00Z", completed_at: "2026-07-27T00:01:00Z", duration_ms: 60_000, error_code: null },
      overall: { source_items_discovered: 8, supported_assets: 3, unsupported_assets: 5, completed: 1, active: 1, queued: 1, failed: 0, skipped: 0, indexed_percentage: 33.3, throughput_today: 1, asset_progress: [{ key: "discovered", count: 1 }, { key: "downloaded", count: 0 }, { key: "stored", count: 0 }, { key: "analyzed", count: 1 }, { key: "projection_built", count: 0 }, { key: "indexed", count: 1 }] },
      stages, active_job: { stage: "Download", job_type: "source_asset_download", status: "processing", filename: "nurse.jpg", provider: "google_drive", attempt_count: 1, max_attempts: 5, started_at: "2026-07-27T00:00:00Z", elapsed_ms: 1_000, message: "Downloading from Google Drive" },
      failure_groups: [], recent_assets: { page: 2, page_size: 25, total: 60, items: [{ asset_id: "asset-1", filename: "nurse.jpg", state: "search_pending", stage_statuses: { download: "completed", store: "completed", analyze: "completed", projection: "completed", index: "pending" }, updated_at: "2026-07-27T00:00:00Z", error_code: null }] },
    }} />);
    expect(markup).toContain("GOOGLE DRIVE SCAN");
    expect(markup).toContain("Elasticsearch Index");
    expect(markup).toContain("Downloading from Google Drive");
    expect(markup).toContain("Queue by stage");
    expect(markup).toContain("Furthest verified stage");
    expect(markup).toContain("Search ready");
    expect(markup).toContain("In progress");
    expect(markup).toContain("Queued work");
    expect(markup).toContain("Showing 26-50 of 60");
    expect(markup).toContain("Pipeline asset pagination");
    expect(markup).toContain("Eligible now");
    expect(markup).toContain("Active pipeline stages");
    expect(markup).toContain("Needs attention");
    expect(markup).not.toContain("Folders and non-images are excluded");
  });

  it("explains unresolved pipeline failures without hiding their technical codes", () => {
    const stage = { key: "asset_analyze", label: "AI Analyze", subtitle: "Generate metadata", total: 1, pending: 1, eligible_now: 0, waiting: 1, processing: 0, completed: 0, failed: 1, percentage: 0, oldest_pending_at: null };
    const markup = renderToStaticMarkup(<PipelineOverview pipeline={{
      generated_at: "2026-07-27T00:00:00Z", latest_source_sync: null,
      overall: { source_items_discovered: 1, supported_assets: 1, unsupported_assets: 0, completed: 0, active: 0, queued: 1, failed: 1, skipped: 0, indexed_percentage: 0, throughput_today: 0, asset_progress: [] },
      stages: [stage], active_job: null,
      failure_groups: [{ stage: "AI Analyze", error_code: "analysis_image_dimensions", message: "Image dimensions are invalid", count: 3, latest_at: "2026-07-27T00:00:00Z" }],
      recent_assets: { page: 1, page_size: 25, total: 0, items: [] },
    }} />);
    expect(markup).toContain("The image could not be prepared safely for analysis.");
    expect(markup).toContain("Image dimensions are not supported");
    expect(markup).toContain("analysis_image_dimensions");
  });
