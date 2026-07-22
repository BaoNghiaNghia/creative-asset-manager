import { describe, expect, it, vi } from "vitest";
import { fetchAiCapabilities, submitAnalysis } from "./api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("AI metadata API client", () => {
  it("loads capabilities without embedding provider availability", async () => {
    const fetcher = vi.fn(async () => jsonResponse({ providers: [{ id: "openai", enabled: true }] }));
    const result = await fetchAiCapabilities(undefined, fetcher as typeof fetch);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/admin/ai/capabilities", { signal: undefined });
    expect(result.providers[0].id).toBe("openai");
  });

  it("sends every explicit selection field to the single endpoint", async () => {
    const fetcher = vi.fn(async () => jsonResponse({ analysis_id: "a1", job_id: "j1", provider: "openai", model: "gpt-image", processing_mode: "single", status: "accepted" }, 202));
    const result = await submitAnalysis({ assetIds: ["asset-1"], sourceProvider: "google-drive", metadataProfile: "creative", metadataProfileVersion: "2", provider: "openai", processingMode: "single", model: "gpt-image", force: true }, fetcher as typeof fetch);
    const [, init] = fetcher.mock.calls[0];
    expect(result.kind).toBe("single");
    expect(JSON.parse(String(init?.body))).toEqual({ asset_id: "asset-1", source_provider: "google-drive", metadata_profile: "creative", metadata_profile_version: "2", ai_provider: "openai", processing_mode: "single", ai_model: "gpt-image", force: true });
  });

  it("uses the bulk endpoint and Idempotency-Key for multiple assets", async () => {
    const fetcher = vi.fn(async () => jsonResponse({ request_id: "r1", status: "accepted", provider: "gemini", model: "gemini-model", processing_mode: "batch", analysis_count: 2, warning: null, items: [] }, 202));
    const result = await submitAnalysis({ assetIds: ["a1", "a2"], sourceProvider: "sharepoint", metadataProfile: "creative", provider: "gemini", processingMode: "batch", model: "gemini-model", force: false }, fetcher as typeof fetch);
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("/api/v1/admin/asset-analyses/bulk");
    expect(new Headers(init?.headers).get("Idempotency-Key")).toMatch(/^analysis-ui-/);
    expect(JSON.parse(String(init?.body))).toMatchObject({ asset_ids: ["a1", "a2"], ai_provider: "gemini", processing_mode: "batch", ai_model: "gemini-model", force: false });
    expect(result.kind).toBe("bulk");
  });

  it("returns a safe server authorization error", async () => {
    const fetcher = vi.fn(async () => jsonResponse({ detail: "Authentication required" }, 401));
    await expect(fetchAiCapabilities(undefined, fetcher as typeof fetch)).rejects.toThrow("Authentication required");
  });
});
