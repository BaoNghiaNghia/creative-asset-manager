import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { inventoryApi } from "./api";
import {
  InventoryGeminiCredentialSettings,
  clearCredentialDraft,
  credentialStatusClass,
  credentialStatusLabel,
} from "./InventoryGeminiCredentialSettings";

const secret = "AIzaSyFullCandidateMustNeverRender000000";
const credential = {
  provider: "gemini" as const,
  configured: true,
  source: "configuration" as const,
  masked_key: "••••••••7KxQ",
  label: "Gemini Account B",
  status: "connected",
  last_tested_at: "2026-08-13T10:20:00Z",
  updated_at: "2026-08-13T10:20:00Z",
  updated_by: "user-a",
};

describe("Inventory Gemini credential settings", () => {
  it("renders only safe masked metadata and states Gemini is independent from Drive", () => {
    const markup = renderToStaticMarkup(
      <InventoryGeminiCredentialSettings initialCredential={credential} />
    );
    for (const value of [
      "Inventory AI", "Google Gemini", "Connected", "••••••••7KxQ",
      "Configuration", "Gemini Account B", "Last Tested", "Last Updated",
      "Updated By", "Gemini AI credentials are independent from the Google Drive connection.",
      "Test Connection", "Replace API Key",
    ]) expect(markup).toContain(value);
    expect(markup).not.toContain(secret);
    expect(markup).not.toContain("Google Drive account");
  });

  it("starts every replacement draft empty and clears it on cancellation boundaries", () => {
    expect(clearCredentialDraft()).toEqual({ apiKey: "", label: "" });
    expect(JSON.stringify(clearCredentialDraft())).not.toContain(secret);
  });

  it("maps all normalized provider states safely for the settings UI", () => {
    expect(credentialStatusLabel("connected", true)).toBe("Connected");
    expect(credentialStatusLabel("INVALID_KEY", true)).toBe("Invalid key");
    expect(credentialStatusLabel("PERMISSION_DENIED", true)).toBe("Permission denied");
    expect(credentialStatusLabel("RATE_LIMITED", true)).toBe("Rate limited");
    expect(credentialStatusLabel("PROVIDER_UNAVAILABLE", true)).toBe("Unavailable");
    expect(credentialStatusLabel("unavailable", false)).toBe("Not configured");
    expect(credentialStatusClass("INVALID_KEY", true)).toBe("invalid");
    expect(credentialStatusClass("PERMISSION_DENIED", true)).toBe("denied");
    expect(credentialStatusClass("RATE_LIMITED", true)).toBe("limited");
  });

  it("uses only Inventory API endpoints for metadata, testing, and replacement", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => credential,
    });
    vi.stubGlobal("fetch", fetchMock);
    await inventoryApi.getAiCredential();
    await inventoryApi.testAiCredential(secret, "Gemini Account B");
    await inventoryApi.replaceAiCredential(secret, "Gemini Account B");
    expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
      "/api/inventory/configuration/ai-credential",
      "/api/inventory/configuration/ai-credential/test",
      "/api/inventory/configuration/ai-credential",
    ]);
    const allCalls = JSON.stringify(fetchMock.mock.calls);
    expect(allCalls).not.toContain("/api/ai");
    expect(allCalls).not.toContain("/api/auth/google");
    vi.unstubAllGlobals();
  });

  it("does not render a candidate key or any credential storage surface", () => {
    const markup = renderToStaticMarkup(
      <InventoryGeminiCredentialSettings initialCredential={credential} />
    );
    expect(markup).not.toContain(secret);
    expect(markup).not.toMatch(/localStorage|sessionStorage|indexedDB/);
  });
});
