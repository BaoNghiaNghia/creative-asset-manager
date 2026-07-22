import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { AiCapabilities } from "../../features/metadata";
import {
  AnalysisFields,
  AnalysisProgressView,
  AnalyzeMetadataDialog,
  BatchWarning,
  CapabilitiesState,
  availableModes,
  forceConfirmationMessage,
  initialAnalysisSelection,
  retainExplicitSelection,
} from "./AnalyzeMetadataDialog";
import { AnalysisHistoryCard } from "./AnalysisHistoryCard";

const both: AiCapabilities = { providers: [
  { id: "gemini", label: "Google Gemini", enabled: true, default_model: "gemini-2", supported_modes: ["single", "batch"], models: [{ id: "gemini-2", label: "Gemini 2", supports_single: true, supports_batch: true }] },
  { id: "openai", label: "OpenAI", enabled: true, default_model: "gpt-5", supported_modes: ["single", "batch"], models: [{ id: "gpt-5", label: "GPT 5", supports_single: true, supports_batch: true }, { id: "single-only", label: "Single only", supports_single: true, supports_batch: false }] },
] };
const noop = () => undefined;

describe("Analyze metadata selection", () => {
  it("defaults one asset to single and multiple assets to batch", () => {
    expect(initialAnalysisSelection(both, 1)?.processingMode).toBe("single");
    expect(initialAnalysisSelection(both, 2)?.processingMode).toBe("batch");
  });

  it("supports Gemini-only, OpenAI-only and both-provider capabilities", () => {
    expect(initialAnalysisSelection({ providers: [both.providers[0]] }, 1)?.provider).toBe("gemini");
    expect(initialAnalysisSelection({ providers: [both.providers[1]] }, 1)?.provider).toBe("openai");
    const markup = fieldsMarkup(both, { provider: "gemini", processingMode: "single", model: "gemini-2" });
    expect(markup).toContain("Google Gemini"); expect(markup).toContain("OpenAI");
  });

  it("hides unsupported modes and models", () => {
    const provider = { ...both.providers[1], supported_modes: ["single" as const] };
    expect(availableModes(provider)).toEqual(["single"]);
    const markup = fieldsMarkup({ providers: [provider] }, { provider: "openai", processingMode: "single", model: "single-only" });
    expect(markup).not.toContain('value="batch"');
    expect(markup).toContain("Single only");
  });

  it("retains an explicit valid provider and model selection", () => {
    const explicit = { provider: "openai" as const, processingMode: "single" as const, model: "single-only" };
    expect(retainExplicitSelection(both, explicit, 5)).toBe(explicit);
  });

  it("renders loading, failure and no-provider states", () => {
    expect(renderToStaticMarkup(<CapabilitiesState loading error="" capabilities={null} />)).toContain("Loading AI providers");
    expect(renderToStaticMarkup(<CapabilitiesState loading={false} error="Network down" capabilities={null} />)).toContain("Network down");
    expect(renderToStaticMarkup(<CapabilitiesState loading={false} error="" capabilities={{ providers: [] }} />)).toContain("No AI provider is enabled");
  });

  it("shows batch delay and one-item batch warnings", () => {
    const markup = renderToStaticMarkup(<BatchWarning assetCount={1} />);
    expect(markup).toContain("asynchronous"); expect(markup).toContain("one-asset batch");
  });

  it("shows accessible progress and budget-blocked count", () => {
    const markup = renderToStaticMarkup(<AnalysisProgressView progress={{ provider: "openai", model: "gpt-5", processingMode: "batch", accepted: 4, queued: 1, running: 1, completed: 1, failed: 1, budgetBlocked: 1 }} />);
    expect(markup).toContain("Budget blocked"); expect(markup).toContain('role="progressbar"'); expect(markup).toContain("OpenAI");
  });

  it("explains unauthorized actions and force confirmation semantics", () => {
    const markup = renderToStaticMarkup(<AnalyzeMetadataDialog open assetIds={["a1"]} sourceProvider="google-drive" authorized={false} onClose={noop} />);
    expect(markup).toContain("not authorized");
    expect(forceConfirmationMessage({ provider: "openai", processingMode: "single", model: "gpt-5" })).toContain("preserves all previous analysis history");
  });

  it("renders provider, model, mode, profile, usage, cost and retry error history", () => {
    const markup = renderToStaticMarkup(<AnalysisHistoryCard showCost analysis={{ ai_provider: "openai", ai_model: "gpt-5", pipeline_version: "batch-asset-v1", metadata_profile: "creative", metadata_profile_version: "3", status: "failed", attempt_count: 2, usage: { input_units: 100, output_units: 20, locally_estimated_cost_micros: 12500, currency: "USD" }, last_error_code: "rate_limited", last_error_message: "Retry later" }} />);
    for (const value of ["OpenAI", "gpt-5", "Batch", "creative", "estimated", "rate_limited", "Retry later"]) expect(markup).toContain(value);
  });
});

function fieldsMarkup(capabilities: AiCapabilities, selection: Parameters<typeof AnalysisFields>[0]["selection"]): string {
  return renderToStaticMarkup(<AnalysisFields capabilities={capabilities} selection={selection} disabled={false} profile="creative" profileVersion="" force={false} onProfile={noop} onProfileVersion={noop} onProvider={noop} onMode={noop} onModel={noop} onForce={noop} />);
}
