// @ts-expect-error Vitest executes this test-only import in Node.
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import {
  cancelAiOperationsJob,
  fetchAiOperationsDashboard,
  filtersFromSearch,
  normalizeMediaDashboard,
  normalizePipelineSnapshot,
  retryAiOperationsJob,
  retryAiOperationsJobsByError,
  setVideoAiPaused,
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
  aiWorkerIsPaused,
  eligibleProcessingAction,
  formatErrorDetail,
  formatProcessingDuration,
  formatVideoDuration,
  emptyDashboard,
  handleTabKeyDown,
  pageFilters,
  usagePageFilters,
  visiblePages,
  PipelineOverview,
  ProcessingJobAction,
  StatusText,
} from "./AiOperationsPage";
import { InventoryDailyOverview } from "./InventoryDailyTab";

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
  local_rate_limited: 0,
  quota_deferred: 0,
  provider_cooldown_deferred: 0,
  next_local_rate_limit_retry_at: null,
  next_quota_retry_at: null,
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
      filename: "inventory-photo.avif",
      mime_type: "image/avif",
      thumbnail_url: "/api/explorer/thumbnail/drive-item?provider=google-drive",
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
      error: { code: "provider_timeout", message: "Provider timed out after 30 seconds.", retryable: false },
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

function render(tab: "overview" | "processing" | "inventory" | "cost" = "overview", overrides = {}) {
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

describe("AI Operations date range", () => {
  it("defaults to six months and preserves all-time selection", () => {
    expect(filtersFromSearch("").range).toBe(90);
    expect(filtersFromSearch("?range=all").range).toBe(0);
    expect(searchFromFilters({ ...filters, range: 0 }, "overview")).toContain("range=all");
  });

  it("renders month and all-time choices", () => {
    const markup = renderToStaticMarkup(<AiOperationsFilters filters={{ ...filters, range: 180 }} models={[]} profiles={[]} onChange={noop} />);
    expect(markup).toContain("Last 1 month");
    expect(markup).toContain("Last 3 months");
    expect(markup).toContain("Last 6 months");
    expect(markup).toContain("All time");
  });
});

describe("AI Operations media dashboard compatibility", () => {
  it("normalizes a partial media response before the Video AI view renders", () => {
    const media = normalizeMediaDashboard({
      image: {
        key: "asset_analyze", label: "Image analysis", queued: 1,
        eligible_now: 1, running: 0, completed: 2, failed: 0,
        waiting_rate_limit: 0,
      },
    } as AiOpsDashboardData["media"]);
    expect(media?.recent_video).toEqual({ page: 1, page_size: 25, total: 0, items: [] });
    expect(media?.video.queued).toBe(0);
    expect(media?.video_indexing.completed).toBe(0);
    const legacy = normalizeMediaDashboard({
      recent_video: [{ job_id: "video-job", source_asset_id: "asset", asset_id: null, filename: "clip.mp4", mime_type: "video/mp4", duration_ms: 65_000, location: "Google Drive / Campaigns", thumbnail_url: "/api/explorer/thumbnail/asset", completed_chunks: 2, total_chunks: 5, status: "completed", attempt_count: 1, max_attempts: 5, updated_at: "2026-08-21T00:00:00Z", error_code: null }],
    } as unknown as AiOpsDashboardData["media"]);
    expect(legacy?.recent_video.total).toBe(1);
    expect(legacy?.recent_video.items[0]?.filename).toBe("clip.mp4");
    const processingMarkup = renderToStaticMarkup(<AiOperationsContent
      data={{ ...data, media: legacy }}
      filters={filters}
      tab="processing"
      media="video"
      onTab={noop}
      onFilters={noop}
      onRetry={noop}
    />);
    expect(processingMarkup).toContain("Video processing jobs");
    expect(processingMarkup).toContain("clip.mp4");
    expect(processingMarkup).toContain("Google Drive / Campaigns");
    expect(processingMarkup).toContain("/api/explorer/thumbnail/asset");
    expect(processingMarkup).toContain("Segments");
    expect(processingMarkup).toContain("2/5");
    expect(processingMarkup).toContain("Video processing page numbers");
    expect(processingMarkup).toContain('aria-label="Thời lượng 1:05"');
    expect(processingMarkup).toContain("video-duration-badge");
    expect(processingMarkup).toContain('aria-label="Mở chi tiết clip.mp4"');
    expect(processingMarkup).toContain("video-processing-title-button");
    expect(processingMarkup).not.toContain("<b>clip.mp4</b>");
    expect(() => renderToStaticMarkup(<AiOperationsContent
      data={{ ...data, media }}
      filters={filters}
      tab="overview"
      media="video"
      onTab={noop}
      onFilters={noop}
      onRetry={noop}
    />)).not.toThrow();
  });


  it("renders Video AI charts from video analytics without borrowing Image cost", () => {
    const media = normalizeMediaDashboard({
      video_processed_today: 7,
      analytics: {
        daily: [{
          date: "2026-08-21", requested: 3, completed: 2, failed: 1,
          estimated_cost_micros: 0, provider_reported_cost_micros: 0,
          reconciled_cost_micros: 0, provider_estimated_cost_micros: {},
          average_latency_ms: 1200, p95_latency_ms: 1800,
        }],
        providers: [{
          provider: "gemini", model: "gemini-video", processing_mode: "single",
          count: 3, completed: 2, failed: 1, success_rate: 2 / 3,
          average_latency_ms: 1200, p95_latency_ms: 1800,
          input_units: 0, output_units: 0, estimated_cost_micros: 0,
          provider_reported_cost_micros: 0, reconciled_cost_micros: 0, currency: "USD",
        }],
        failures: [{ source: "video_analyze", error_code: "video_provider_failed", count: 1 }],
        latency: { average_ms: 1200, p95_ms: 1800 },
        cost_available: false,
      },
    } as unknown as AiOpsDashboardData["media"]);
    const markup = renderToStaticMarkup(<AiOperationsContent
      data={{ ...data, media }}
      filters={filters}
      tab="overview"
      media="video"
      onTab={noop}
      onFilters={noop}
      onRetry={noop}
    />);

    expect(markup).toContain("Daily processing");
    expect(markup).toContain('class="ops-kpis ops-kpis-video"');
    expect(markup).toContain("Processed today");
    expect(markup).toContain("Completed video analyses today (UTC)");
    expect(markup).toContain(">7<");
    const processedToday = markup.indexOf("Processed today");
    const processed = markup.indexOf("Processed", processedToday + "Processed today".length);
    expect(processedToday).toBeGreaterThan(-1);
    expect(processed).toBeGreaterThan(processedToday);
    expect(markup.slice(markup.lastIndexOf("<article", processedToday), processedToday)).toContain("ops-kpi-neutral");
    expect(markup).toContain("Daily estimated cost by provider");
    expect(markup).toContain("Provider and mode volume");
    expect(markup).toContain("Failure categories");
    expect(markup).toContain("Latency");
    expect(markup).toContain("gemini-video");
    expect(markup).toContain("video_provider_failed");
    expect(markup).toContain("Video AI cost is not recorded by the current worker.");
  });

  it("renders Image-style pagination for recent videos", () => {
    const media = normalizeMediaDashboard({
      recent_video: {
        page: 1,
        page_size: 25,
        total: 3_226,
        items: [{
          job_id: "video-job",
          source_asset_id: "asset",
          asset_id: "logical-asset",
          filename: "clip.mp4",
          mime_type: "video/mp4",
          duration_ms: 65_000,
          location: "Google Drive / Campaigns",
          thumbnail_url: "/api/explorer/thumbnail/asset",
          status: "completed",
          attempt_count: 1,
          max_attempts: 5,
          updated_at: "2026-08-21T00:00:00Z",
          error_code: null,
          steps: [
            { key: "video_analyze", label: "Video analysis", status: "completed", attempt_count: 1, max_attempts: 5, updated_at: "2026-08-21T00:00:00Z", error_code: null },
            { key: "video_search_index", label: "Video indexing", status: "completed", attempt_count: 1, max_attempts: 5, updated_at: "2026-08-21T00:01:00Z", error_code: null },
          ],
        }],
      },
    } as unknown as AiOpsDashboardData["media"]);
    const pipeline = {
      generated_at: "2026-08-21T00:00:00Z",
      latest_source_sync: null,
      overall: {
        source_items_discovered: 0, supported_assets: 0, unsupported_assets: 0,
        completed: 0, active: 0, queued: 0, failed: 0, skipped: 0,
        indexed_percentage: 0, throughput_today: 0, asset_progress: [],
      },
      stages: [], active_job: null, failure_groups: [],
      recent_assets: { page: 1, page_size: 25, total: 0, items: [] },
    } as NonNullable<AiOpsDashboardData["pipeline"]>;
    const markup = renderToStaticMarkup(<PipelineOverview
      pipeline={pipeline}
      mediaDashboard={media}
      media="video"
    />);
    expect(markup).toContain("Số mục mỗi trang");
    expect(markup).toContain("Số video mỗi trang");
    expect(markup).toContain('aria-label="Video page numbers"');
    expect(markup).toContain('aria-current="page"');
    expect(markup).toContain(">130<");
    expect(markup).toContain("...");
    expect(markup).toContain("/api/explorer/thumbnail/asset");
    expect(markup).toContain('aria-label="Thời lượng 1:05"');
    expect(markup).toContain("video-duration-badge");
    expect(markup).toContain('aria-label="Mở chi tiết clip.mp4"');
    expect(markup).toContain("video-recent-title-button");
    expect(markup).toContain("video/mp4");
    const recentMarkup = markup.slice(markup.indexOf('aria-label="Tiến độ video gần đây"'));
    expect(recentMarkup).toContain("<th>Pipeline</th>");
    expect(recentMarkup).toContain("video-pipeline-flow");
    expect(recentMarkup).toContain('data-video-step="video_analyze"');
    expect(recentMarkup).toContain('data-video-step="video_search_index"');
    expect(recentMarkup).toContain("Video analysis");
    expect(recentMarkup).toContain("Video indexing");
    expect(recentMarkup).not.toContain("video-pipeline-flow-status");
  });
});

describe("AI Operations media tab placement", () => {
  it("places Image AI and Video AI before the filters for pipeline and AI analysis", () => {
    const markup = render("overview");
    const queryBar = markup.slice(markup.indexOf("ops-query-bar"), markup.indexOf("ops-partial-error"));
    expect(queryBar.indexOf("Image AI")).toBeGreaterThanOrEqual(0);
    expect(queryBar.indexOf("Image AI")).toBeLessThan(queryBar.indexOf("Last 1 month"));
    expect(queryBar.indexOf("Video AI")).toBeLessThan(queryBar.indexOf("Last 1 month"));
    const processing = render("processing");
    expect(processing).toContain("Processing media type");
    expect(processing.indexOf("Image AI")).toBeLessThan(processing.indexOf("Last 1 month"));
  });
});

describe("Inventory Daily tab", () => {
  it("places Inventory in the Operations tabs and removes unrelated AI filters", () => {
    const markup = render("inventory");
    expect(markup).toContain("Inventory Daily");
    expect(markup).toContain("Đang tải dữ liệu Inventory hằng ngày");
    expect(markup).not.toContain("Last 1 month");
    expect(markup).not.toContain("Export data");
  });

  it("renders daily status, scheduler, snapshot and reconciliation data", () => {
    const markup = renderToStaticMarkup(<InventoryDailyOverview
      status={{
        enabled: true,
        configured: true,
        execution_mode: "legacy_daily_run",
        operational_state: "healthy",
        image_pipeline_enabled: true,
        timezone: "Asia/Ho_Chi_Minh",
        current_local_date: "2026-08-27",
        working_business_date: "2026-08-26",
        snapshot_time: "05:50",
        reconcile_time: "07:00",
        next_snapshot_at: "2026-08-27T05:50:00+07:00",
        next_reconciliation_at: "2026-08-27T07:00:00+07:00",
        working_spreadsheet_url: "https://docs.google.com/spreadsheets/d/workbook",
        last_snapshot: {
          id: "snapshot-1",
          business_date: "2026-08-26",
          status: "completed",
          snapshot_file_id: "file-1",
          snapshot_url: "https://docs.google.com/spreadsheets/d/snapshot",
          archive_folder_url: null,
          error_code: null,
          completed_at: "2026-08-26T05:51:00+07:00",
        },
        last_reconciliation: {
          id: "reconcile-1",
          business_date: "2026-08-26",
          previous_business_date: "2026-08-25",
          status: "completed",
          summary: { row_count: 206, changed_count: 4, invalid_count: 0 },
          error_code: null,
          completed_at: "2026-08-26T07:02:00+07:00",
        },
      }}
      run={{
        id: "run-1",
        business_date: "2026-08-26",
        status: "ready",
        ready: true,
        finalized: false,
        forced: false,
        blockers: [],
        report: {},
        finalized_at: null,
        finalized_by: null,
      }}
    />);
    for (const value of ["Inventory hằng ngày", "27/08/2026", "26/08/2026", "Ngày dữ liệu đang xử lý", "Sẵn sàng", "206", "4 thay đổi", "Snapshot gần nhất", "Đối soát gần nhất", "Chụp dữ liệu lúc 05:50", "Chu kỳ hiện không có vấn đề"]) {
      expect(markup).toContain(value);
    }
    expect(markup).toContain("https://docs.google.com/spreadsheets/d/workbook");
    expect(markup.match(/aria-haspopup="dialog"/g)).toHaveLength(2);
    const heading = markup.slice(markup.indexOf("ops-section-heading"), markup.indexOf("ops-kpis"));
    expect(heading).toContain("ops-inventory-quick-actions");
    for (const action of ["Mở Google Sheet đang xử lý", "Mở Daily Inventory", "Cấu hình Inventory", "Làm mới"]) {
      expect(heading).toContain(action);
    }
    expect(heading.indexOf("Cấu hình Inventory")).toBeLessThan(heading.indexOf("Làm mới"));
    expect(markup).not.toContain("ops-inventory-links");
    expect(markup).not.toContain('href="/inventory/daily"');
  });
});

describe("AI Operations dashboard", () => {
  it("restores the AI worker toggle from the persisted AI policy, not the general pipeline pause", () => {
    expect(aiWorkerIsPaused({ ai_enabled: false })).toBe(true);
    expect(aiWorkerIsPaused({ ai_enabled: true })).toBe(false);
  });

  it("uses the independent Video control endpoint", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ state: "paused" }), {
      status: 200, headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
    await setVideoAiPaused(true, "video maintenance", fetcher);
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/admin/ai-operations/controls/video/pause",
      expect.objectContaining({ method: "POST" }),
    );
  });

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

  it("preserves Video pagination and page size in URL state", () => {
    const videoFilters = { ...filters, videoPage: 3, videoPageSize: 50 as const };
    const query = searchFromFilters(videoFilters, "pipeline", 0, "video");
    expect(new URLSearchParams(query).get("video_page")).toBe("3");
    expect(new URLSearchParams(query).get("video_page_size")).toBe("50");
    expect(filtersFromSearch(query)).toEqual(videoFilters);
  });

  it("renders all KPI cards and accessible chart/table equivalents", () => {
    const markup = render();
    for (const value of [
      "Processed today", "Completed", "Failed", "Đang chạy", "Đã xếp hàng", "Success rate",
      "Estimated cost today", "Estimated cost this month", "Daily processing",
      "Daily estimated cost by provider", "Provider and mode volume",
      "Failure categories", "Latency", "View chart data table",
    ]) expect(markup).toContain(value);
    expect(markup).toContain("Đang chạy");
    expect(markup).toContain("Currently processing");
    expect(markup).toContain("Đã xếp hàng");
    expect(markup).toContain("Chờ bắt đầu");
    expect(markup).toContain('role="img"');
    expect(markup).toContain("provider_timeout");
  });

  it("explains deferred Gemini work with its next retry time", () => {
    const markup = render("overview", { data: { ...data, summary: { ...summary, deferred: 3, quota_deferred: 3, next_deferred_retry_at: "2026-07-22T10:30:00Z", next_quota_retry_at: "2026-07-22T10:30:00Z" } } });
    expect(markup).toContain("Gemini quota or provider cooldown is active");
    expect(markup).toContain("3 analyses will retry automatically");
    expect(markup).toContain("Tiếp provider retry");
  });

  it("formats only accumulated worker execution time for completed jobs", () => {
    expect(formatProcessingDuration(2_000)).toBe("2.0 s");
    expect(formatProcessingDuration(125_000)).toBe("2.1 min");
    expect(formatProcessingDuration(0)).toBe("—");
    expect(formatVideoDuration(65_000)).toBe("1:05");
    expect(formatVideoDuration(3_665_000)).toBe("1:01:05");
    expect(formatVideoDuration(0)).toBe("0:00");
    expect(formatVideoDuration(null)).toBe("—");
    expect(formatErrorDetail("provider_timeout", "Provider timed out.")).toBe("Error code: provider_timeout\nMessage: Provider timed out.");
    expect(formatErrorDetail("provider_timeout", null)).toContain("No additional error detail was recorded.");
  });

  it("opens processing asset details in AI Operations without routing to Asset Explorer", () => {
    const markup = render("processing", { data: { ...data, usage: { ...data.usage, total: 0, items: [] } } });
    for (const value of ["AI processing jobs", "OpenAI", "gpt-test", "Batch", "catalog", "1/3", "2.0 s", "provider_timeout", "Showing 26-50 of 60", "Số mục mỗi trang"]) expect(markup).toContain(value);
    expect(markup).toContain("asset-1");
    expect(markup).toContain("inventory-photo.avif");
    expect(markup).toContain("image/avif");
    expect(markup).toContain('src="/api/explorer/thumbnail/drive-item?provider=google-drive"');
    expect(markup).toContain('aria-label="View asset asset-1"');
    expect(markup).toContain("Chi tiết");
    expect(markup).toContain('aria-haspopup="dialog"');
    expect(markup).toContain("Show error details");
    expect(markup).not.toContain("/?details=1&amp;asset=asset-1");
    expect(markup).not.toContain(">analysis-1</code>");
  });

  it("keeps estimated, provider-reported and reconciled cost clearly separated", () => {
    const markup = render("cost");
    for (const value of ["Cost &amp; Usage", "AI cost and usage records", "Showing 1-1 of 1", "Số mục mỗi trang", "Estimated total", "Provider-reported total", "Reconciled total", "$1.20", "$1.10", "$1.05", "Input units", "Output units", "Export usage CSV"]) expect(markup).toContain(value);
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
    expect(styles).toContain(".ops-header-actions .ops-refresh-control{width:152px!important;max-width:152px!important");
    expect(styles).toContain("grid-template-columns:repeat(7,minmax(130px,1fr))");
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

  it("limits dashboard aggregate requests so the API pool is not saturated", async () => {
    let active = 0;
    let peak = 0;
    const fetcher = vi.fn(async () => {
      active += 1;
      peak = Math.max(peak, active);
      await new Promise(resolve => setTimeout(resolve, 0));
      active -= 1;
      return new Response(JSON.stringify(summary), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }) as unknown as typeof fetch;
    await fetchAiOperationsDashboard(filters, fetcher, new Date("2026-07-22T00:00:00Z"));
    expect(fetcher).toHaveBeenCalledTimes(11);
    expect(peak).toBeLessThanOrEqual(3);
  });

  it("requests the selected Video page size from the media dashboard API", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(summary), {
      status: 200, headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
    await fetchAiOperationsDashboard(
      { ...filters, status: "failed", videoPage: 3, videoPageSize: 50 },
      fetcher,
      new Date("2026-07-22T00:00:00Z"),
    );
    const urls = vi.mocked(fetcher).mock.calls.map(call => String(call[0]));
    const mediaUrl = urls.find(url => url.startsWith("/api/v1/admin/ai-operations/media-dashboard?"));
    expect(mediaUrl).toContain("video_page=3");
    expect(mediaUrl).toContain("video_page_size=50");
    expect(mediaUrl).toContain("from=");
    expect(mediaUrl).toContain("to=");
    expect(mediaUrl).toContain("provider=openai");
    expect(mediaUrl).toContain("model=gpt-test");
    expect(mediaUrl).toContain("processing_mode=batch");
    expect(mediaUrl).toContain("metadata_profile=catalog");
    expect(mediaUrl).toContain("status=failed");
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
    expect(renderToStaticMarkup(<PipelineOverview pipeline={result.data.pipeline} />)).toContain("Tổng quan pipeline chưa khả dụng");
  });
});


describe("AI Operations interactions", () => {
  it("renders keyboard tabs, auto-refresh choices and preserves refresh in URL state", () => {
    const markup = render("processing", { refreshSeconds: 30, lastUpdated: new Date("2026-07-22T10:00:00Z") });
    expect(markup).toContain('role="tablist"');
    expect(markup).toContain('role="tab"');
    expect(markup).toContain('role="tabpanel"');
    expect(markup).toContain('aria-selected="true"');
    expect(markup).toContain("Số mục mỗi trang");
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
    expect(changes.map(item => item.videoPage)).toEqual([1, 1, 1, 1, 1]);
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
    expect(renderToStaticMarkup(<StatusText status={waiting.status} isDeferred={waiting.is_deferred} nextAttemptAt={waiting.next_attempt_at} />)).toContain("Đang chờ for Gemini quota");
    expect(renderToStaticMarkup(<StatusText status="failed" />)).toContain("Failed");
    expect(renderToStaticMarkup(<StatusText status="pending" />)).toContain("Đã xếp hàng");
    expect(eligibleProcessingAction(waiting)).toBe("force_retry");
  });

  it("hides mutation actions without their specific permissions and exposes AI Operations by read permission", () => {
    const failed = { ...data.jobs.items[0], status: "failed" };
    expect(renderToStaticMarkup(<ProcessingJobAction job={failed} permissions={[]} onAccepted={noop} />)).toBe("");
    expect(renderToStaticMarkup(<ProcessingJobAction job={failed} permissions={["ai_jobs.retry"]} onAccepted={noop} />)).toContain("Retry failed job");
    expect(mayViewAiOperations(["ai_operations.read"])).toBe(true);
    expect(mayViewAiOperations(["assets.read"])).toBe(false);
  });

  it("renders an error-group selector and bulk retry action for authorized operators", () => {
    const markup = render("processing", { permissions: ["ai_jobs.retry"] });
    expect(markup).toContain("Failed error group");
    expect(markup).toContain("provider_timeout (2)");
    expect(markup).toContain("Retry failed group");
    expect(markup).toContain("maximum 1,000 per action");
  });

  it("renders scoped group and per-job retry actions for failed Video jobs", () => {
    const media = normalizeMediaDashboard({
      analytics: {
        failures: [{ source: "video_analyze", error_code: "video_provider_failed", count: 2 }],
      },
      recent_video: {
        page: 1, page_size: 25, total: 1,
        items: [{
          job_id: "video-failed", source_asset_id: "source-video", asset_id: null,
          filename: "failed.mp4", mime_type: "video/mp4", duration_ms: 10_000,
          location: "Google Drive / Video", thumbnail_url: null,
          completed_chunks: 0, total_chunks: 1, status: "failed",
          attempt_count: 5, max_attempts: 5, updated_at: "2026-08-29T00:00:00Z",
          error_code: "video_provider_failed",
          error_message: "Gemini video request returned HTTP 503.",
        }],
      },
    } as unknown as AiOpsDashboardData["media"]);
    const markup = render("processing", {
      data: { ...data, media }, media: "video", permissions: ["ai_jobs.retry"],
    });
    expect(markup).toContain("video_provider_failed (2)");
    expect(markup).toContain("Retry failed group");
    expect(markup).toContain("Retry failed job");
    expect(markup).toContain('aria-haspopup="dialog"');
    expect(markup).toContain('id="video_analyze-failed-error-group"');
  });

  it("posts a bounded audited bulk retry request", async () => {
    const fetcher = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => new Response(JSON.stringify({
      tenant_id: "tenant-a", error_code: "analysis_image_dimensions",
      matched: 2, retried: 2, skipped: 0, items: [],
    }), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    await retryAiOperationsJobsByError("analysis_image_dimensions", "operator requested retry", 1000, fetcher);
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/admin/ai-operations/jobs/retry-by-error",
      expect.objectContaining({ method: "POST", body: JSON.stringify({
        error_code: "analysis_image_dimensions", reason: "operator requested retry", limit: 1000,
      }) }),
    );
  });

  it("scopes Video bulk retry requests to video_analyze", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({
      tenant_id: "tenant-a", error_code: "video_provider_failed", job_type: "video_analyze",
      matched: 2, retried: 2, skipped: 0, items: [],
    }), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    await retryAiOperationsJobsByError(
      "video_provider_failed", "video provider recovered", 1000, fetcher, "video_analyze",
    );
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/admin/ai-operations/jobs/retry-by-error",
      expect.objectContaining({ body: JSON.stringify({
        error_code: "video_provider_failed", reason: "video provider recovered",
        limit: 1000, job_type: "video_analyze",
      }) }),
    );
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
    expect(source).toContain("JPEG, PNG, WebP, AVIF, HEIC, and HEIF images");
    expect(source).toContain('"avif", "heic", "heif"');
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


  it("does not claim completed stages are shown when pipeline data is empty", () => {
    const markup = renderToStaticMarkup(<PipelineOverview pipeline={{ generated_at: "2026-08-21T00:00:00Z", latest_source_sync: null, overall: { source_items_discovered: 0, supported_assets: 0, unsupported_assets: 0, completed: 0, active: 0, queued: 0, failed: 0, skipped: 0, indexed_percentage: 0, throughput_today: 0, asset_progress: [] }, stages: [], active_job: null, failure_groups: [], recent_assets: { page: 1, page_size: 25, total: 0, items: [] } }} />);
    expect(markup).toContain("Hiện chưa có dữ liệu hàng đợi hoặc giai đoạn hoàn tất trong phạm vi đang xem.");
    expect(markup).not.toContain("Các giai đoạn chỉ hoàn tất được hiển thị ở trên.");
  });

  it("renders the full pipeline flow and current active download", () => {
    const stages = [
      ["source_asset_download", "Download"],
      ["asset_store", "Store"],
      ["asset_analyze", "AI Analyze"],
      ["search_projection_build", "Search Projection"],
      ["asset_index", "Elasticsearch Index"],
    ].map(([key, label]) => ({ key, label, subtitle: "Pipeline stage", total: 3, pending: 1, eligible_now: 1, waiting: 0, processing: key === "source_asset_download" ? 1 : 0, completed: 1, failed: 0, percentage: 33.3, oldest_pending_at: null, total_logical_assets: 3, completed_assets: 1, queued_assets: 1, eligible_now_assets: 1, waiting_assets: 0, processing_assets: key === "source_asset_download" ? 1 : 0, needs_attention_assets: 0, skipped_assets: 0, not_started_assets: 0, total_attempts: 3, completed_attempts: 1, failed_attempts: 0 }));
    const markup = renderToStaticMarkup(<PipelineOverview pipeline={{
      generated_at: "2026-07-27T00:00:00Z",
      latest_source_sync: { mode: "full", status: "completed", pages_count: 2, items_seen_count: 8, jobs_created_count: 4, started_at: "2026-07-27T00:00:00Z", completed_at: "2026-07-27T00:01:00Z", duration_ms: 60_000, error_code: null },
      overall: { source_items_discovered: 8, supported_assets: 3, unsupported_assets: 5, completed: 1, active: 1, queued: 1, failed: 0, skipped: 0, indexed_percentage: 33.3, throughput_today: 1, asset_progress: [{ key: "discovered", count: 1 }, { key: "downloaded", count: 0 }, { key: "stored", count: 0 }, { key: "analyzed", count: 1 }, { key: "projection_built", count: 0 }, { key: "indexed", count: 1 }] },
      stages, active_job: { stage: "Download", job_type: "source_asset_download", status: "processing", filename: "nurse.jpg", provider: "google_drive", attempt_count: 1, max_attempts: 5, started_at: "2026-07-27T00:00:00Z", elapsed_ms: 1_000, message: "Downloading from Google Drive" },
      failure_groups: [], skipped_breakdown: [{ category: "folders_non_images", count: 5 }], recent_assets: { page: 2, page_size: 25, total: 60, items: [{ asset_id: "asset-1", filename: "nurse.jpg", mime_type: "image/jpeg", thumbnail_url: "/api/explorer/thumbnail/drive-item?provider=google-drive", state: "search_pending", stage_statuses: { download: "completed", store: "completed", analyze: "completed", projection: "completed", index: "pending" }, updated_at: "2026-07-27T00:00:00Z", error_code: null }] },
    }} />);
    expect(markup).toContain("scan-status-icon completed");
    expect(markup).toContain("Lập chỉ mục tìm kiếm");
    expect(markup).toContain("Downloading from Google Drive");
    expect(markup).toContain("Phân bổ hàng đợi theo giai đoạn");
    expect(markup).toContain("Chờ bắt đầu");
    expect(markup).toContain("pipeline-diagnostics-content");
    expect(markup).toContain("Lịch sử kỹ thuật, không dùng để tính tiến độ tài sản");
    expect(markup).toContain("Đã lên lịch thử lại");
    expect(markup).toContain("pipeline-progress-summary");
    expect(markup).toContain("Sẵn sàng tìm kiếm");
    expect(markup).toContain("Đang xử lý");
    expect(markup).toContain("Đang chờ xử lý");
    expect(markup).toContain('aria-label="Latest scan and current processing"');
    expect(markup).toContain("8 m");
    expect(markup).not.toContain("<dd>Pipeline item</dd>");
    expect(markup).toContain("Hiển thị 26-50 trên tổng số 60 tài sản logic");
    expect(markup).toContain("Pipeline asset pagination");
    expect(markup).toContain("pipeline-asset-thumbnail");
    expect(markup).toContain("/api/explorer/thumbnail/drive-item?provider=google-drive");
    expect(markup).toContain("image/jpeg");
    expect(markup).toContain('class="pipeline-asset-link"');
    expect(markup).toContain("xem chi tiết ngay trong AI Operations");
    expect(markup).not.toContain("/?details=1&amp;asset=asset-1");
    expect(markup).toContain("Sẵn sàng");
    expect(markup).toContain("Các giai đoạn đang hoạt động");
    expect(markup).toContain("Cần xử lý");
    expect(markup).toContain("Dữ liệu bị loại trừ");
    expect(markup).toContain("Loại trừ vĩnh viễn; không cần thử lại");
    expect(markup).not.toContain("Folders and non-images are excluded");
  });

  it("explains unresolved pipeline failures without hiding their technical codes", () => {
    const stage = { key: "asset_analyze", label: "AI Analyze", subtitle: "Generate metadata", total: 1, pending: 1, eligible_now: 0, waiting: 1, processing: 0, completed: 0, failed: 1, percentage: 0, oldest_pending_at: null, total_logical_assets: 1, completed_assets: 0, queued_assets: 0, eligible_now_assets: 0, waiting_assets: 1, processing_assets: 0, needs_attention_assets: 1, skipped_assets: 0, not_started_assets: 0, total_attempts: 1, completed_attempts: 0, failed_attempts: 1 };
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
