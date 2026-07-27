import { describe, expect, it } from "vitest";

import { aiOperationsExportUrl } from "../../features/ai_operations";
import type { AiOpsDaily, AiOpsFilters } from "../../features/ai_operations";
import {
  dailyProviderCostChart, dailyStatusChart, failureChart, providerVolumeChart,
} from "./presentation";

const filters: AiOpsFilters = {
  range: 30,
  provider: "openai",
  model: "gpt-image-test",
  processingMode: "batch",
  metadataProfile: "catalog",
  status: "",
  page: 3,
};

function daily(overrides: Partial<AiOpsDaily> = {}): AiOpsDaily {
  return {
    date: "2026-07-20",
    requested: 2,
    completed: 1,
    failed: 1,
    estimated_cost_micros: 30,
    provider_reported_cost_micros: 20,
    reconciled_cost_micros: 25,
    provider_estimated_cost_micros: { gemini: 10, openai: 20 },
    average_latency_ms: 150,
    p95_latency_ms: 200,
    ...overrides,
  };
}

describe("AI Operations presentation", () => {
  it("maps server daily provider aggregates without using a truncated usage page", () => {
    expect(dailyProviderCostChart([daily()])).toEqual([{
      label: "2026-07-20",
      values: { gemini: 10, openai: 20 },
    }]);
  });

  it("maps status, provider/mode volume and stable failure categories", () => {
    expect(dailyStatusChart([daily()])).toEqual([{
      label: "2026-07-20",
      values: { Completed: 1, Failed: 1 },
    }]);
    expect(providerVolumeChart([{
      provider: "openai", model: "gpt-test", processing_mode: "batch",
      count: 4, completed: 3, failed: 1, success_rate: .75,
      average_latency_ms: 100, p95_latency_ms: 180,
      input_units: 10, output_units: 5,
      estimated_cost_micros: 10, provider_reported_cost_micros: 8,
      reconciled_cost_micros: 9, currency: "USD",
    }])).toEqual([{
      label: "OpenAI · gpt-test · Batch",
      values: { Analyses: 4 },
    }]);
    expect(failureChart([
      { source: "analysis", error_code: "provider_timeout", count: 2 },
    ])).toEqual([{
      label: "provider_timeout",
      values: { Failures: 2 },
    }]);
  });
  it("builds a bounded tenant-authenticated export URL with all dashboard filters", () => {
    const url = new URL(aiOperationsExportUrl("usage", filters, new Date("2026-07-22T00:00:00Z")), "https://app.test");
    expect(url.pathname).toBe("/api/v1/admin/ai-operations/exports/usage.csv");
    expect(Object.fromEntries(url.searchParams)).toMatchObject({
      provider: "openai",
      model: "gpt-image-test",
      processing_mode: "batch",
      metadata_profile: "catalog",
      row_limit: "5000",
      from: "2026-06-22T00:00:00.000Z",
      to: "2026-07-22T00:00:00.000Z",
    });
    expect(url.search).not.toContain("page=");
  });
});
