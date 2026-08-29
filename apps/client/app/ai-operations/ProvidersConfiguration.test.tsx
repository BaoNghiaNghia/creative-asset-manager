import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { AiOpsConfiguration, AiOpsProviderBreakdown } from "../../features/ai_operations";
import { fetchAiOperationsConfiguration, getManagedStorageOAuthStatus, updateAiBudget, updateAiProvider } from "../../features/ai_operations";
import { ConfigurationForm, ProviderCards, replaceProviderConfiguration } from "./ProvidersConfiguration";

const configuration: AiOpsConfiguration = {
  tenant_id: "tenant-a",
  scope: { tenant: "tenant-a", global_upper_bounds_read_only: true },
  permissions: { can_manage_tenant: true, can_manage_global: false, platform_admin: false },
  tenant: { ai_enabled: true, processing_paused: false, default_provider: "openai", default_model: "gpt-5", default_mode: "single", default_metadata_profile: "creative", auto_analyze_new_assets: false, daily_item_limit: 100, total_ai_concurrency: 2, retry_count: 2, timeout_seconds: 60 },
  global: { ai_auto_analyze_enabled: false, single_enabled: true, batch_enabled: true, emergency_stop: false },
  providers: [
    { id: "gemini", label: "Google Gemini", enabled: true, connection_configured: true, processing_enabled: true, paused: false, single_enabled: true, batch_enabled: true, default_model: "gemini-2", allowed_models: ["gemini-2"], active_jobs_limit: 2, single_concurrency: 1, batch_concurrency: 1, last_error: null },
    { id: "openai", label: "OpenAI", enabled: false, connection_configured: false, processing_enabled: false, paused: true, single_enabled: true, batch_enabled: false, default_model: "gpt-5", allowed_models: ["gpt-5", "gpt-5-mini"], active_jobs_limit: 2, single_concurrency: 1, batch_concurrency: 1, last_error: "rate_limited" },
  ],
  metadata_profiles: ["creative", "products"],
  metadata_prompt_template: { id: "profile-1", profile_name: "creative", profile_version: "1", prompt_template: "Describe {{ asset }}", updated_at: null, is_draft: false },
  video_prompt_template: { id: "video-profile-1", profile_name: "video-search", profile_version: "1", prompt_template: "Describe video scenes", updated_at: null, is_draft: false },
  budget: { enabled: true, daily_limit_micros: 1_000_000, monthly_limit_micros: 10_000_000, warning_threshold_percent: 80, hard_stop_threshold_percent: 100, currency: "USD" },
};
const metrics: AiOpsProviderBreakdown[] = [{ provider: "gemini", model: "gemini-2", processing_mode: "single", count: 10, completed: 9, failed: 1, success_rate: .9, average_latency_ms: 100, p95_latency_ms: 225, input_units: 10, output_units: 5, estimated_cost_micros: 50_000, provider_reported_cost_micros: 0, reconciled_cost_micros: 50_000, currency: "USD" }];
const noop = () => undefined;

describe("AI Operations provider and configuration tabs", () => {
  it("renders provider cards, connection state, modes, health, cost and last error without secrets", () => {
    const markup = renderToStaticMarkup(<ProviderCards configuration={configuration} metrics={metrics} onChanged={noop} onReload={noop} />);
    for (const value of ["Google Gemini", "OpenAI", "Connection configured", "Connection not configured", "Requests today", "90.0%", "Highest grouped p95 latency", "225 ms", "rate_limited", "Pause provider", "Resume provider"]) expect(markup).toContain(value);
    expect(markup.toLowerCase()).not.toContain("api_key"); expect(markup).not.toContain("sk-");
  });

  it("renders tenant configuration, model allowlist, budgets and read-only global controls", () => {
    const markup = renderToStaticMarkup(<ConfigurationForm configuration={configuration} onChanged={noop} onReload={noop} />);
    for (const value of ["Thiết lập mặc định", "gpt-5-mini", "Default metadata profile", "Prompt template", "Describe {{ asset }}", "Describe video scenes", "Image AI", "Video AI", "Save image prompt template", "Save video prompt template", "Expand", "Daily item limit", "Retry count", "Timeout", "Daily budget", "Monthly budget", "Warning threshold", "Hard-stop threshold", "Chỉ Platform administrator mới có thể thay đổi cấu hình toàn cục"]) expect(markup).toContain(value);
    expect(markup).not.toContain("Emergency stop all AI");
  });

  it("renders an editable default Video prompt when the API has no active video profile", () => {
    const legacyConfiguration = { ...configuration, video_prompt_template: undefined };
    const markup = renderToStaticMarkup(<ConfigurationForm configuration={legacyConfiguration} onChanged={noop} onReload={noop} />);
    expect(markup).toContain("video-default");
    expect(markup).toContain("Analyze this video for semantic search and retrieval.");
    expect(markup).toContain("embroidery_type:&lt;type&gt;");
    expect(markup).toContain("Save video prompt template");
    expect(markup).not.toContain("Chưa có video metadata profile");
  });

  it("keeps core Configuration cards in the responsive layout and moves Gemini credentials to Providers", () => {
    const markup = renderToStaticMarkup(<ConfigurationForm configuration={configuration} onChanged={noop} onReload={noop} />);
    expect(markup).toContain("ops-config-card ops-config-defaults");
    expect(markup).toContain("ops-config-card ops-config-budget");
    expect(markup).toContain("ops-global-settings ops-config-global");
    expect(markup).not.toContain("Google Gemini credential settings");
    expect(markup).not.toContain("Inventory AI");
  });

  it("renders both independent Gemini credential settings inside the Google Gemini provider card", () => {
    const markup = renderToStaticMarkup(<ProviderCards
      configuration={configuration}
      metrics={metrics}
      onChanged={noop}
      onReload={noop}
      inventoryPermissions={["inventory.read", "inventory.credentials.manage"]}
    />);
    expect(markup).toContain("ops-provider-gemini-credentials");
    expect(markup).toContain("Creative AI");
    expect(markup).toContain("Inventory AI");
    expect(markup).toContain("Loading Gemini credential configuration");
    expect(markup).not.toContain("INVENTORY_AUTOMATION_ENABLED");
  });

  it("keeps Inventory Gemini visible in Providers while automation is disabled without exposing actions without credential permission", () => {
    const disabledInventory = { ...configuration, tenant: { ...configuration.tenant, ai_enabled: false } };
    const markup = renderToStaticMarkup(<ProviderCards
      configuration={disabledInventory}
      metrics={metrics}
      onChanged={noop}
      onReload={noop}
      inventoryPermissions={["inventory.read"]}
    />);
    expect(markup).toContain("Inventory AI");
    expect(markup).toContain("Loading Gemini credential configuration");
    expect(markup).not.toContain("INVENTORY_AUTOMATION_ENABLED");
  });

  it("shows Managed Storage reconnect only to platform administrators", () => {
    const elevated = { ...configuration, permissions: { ...configuration.permissions, platform_admin: true } };
    const elevatedMarkup = renderToStaticMarkup(<ProviderCards configuration={elevated} metrics={metrics} onChanged={noop} onReload={noop} />);
    expect(elevatedMarkup).toContain("Google Drive Managed Storage");
    expect(elevatedMarkup).toContain("Loading Managed Storage credential");
    const tenantMarkup = renderToStaticMarkup(<ProviderCards configuration={configuration} metrics={metrics} onChanged={noop} onReload={noop} />);
    expect(tenantMarkup).not.toContain("Google Drive Managed Storage");
  });

  it("loads Managed Storage OAuth status from the admin-only endpoint", async () => {
    const payload = { root_folder_configured: true, connected: true, source: "database" as const, account_email: "m***@example.com", updated_at: null, reconnect_required: false };
    const fetcher = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    await expect(getManagedStorageOAuthStatus(fetcher)).resolves.toEqual(payload);
    expect(fetcher).toHaveBeenCalledWith("/api/auth/google/managed-storage/status", expect.any(Object));
  });

  it("shows platform-only global emergency action to platform administrators", () => {
    const elevated = { ...configuration, permissions: { can_manage_tenant: true, can_manage_global: true, platform_admin: true } };
    expect(renderToStaticMarkup(<ConfigurationForm configuration={elevated} onChanged={noop} onReload={noop} />)).toContain("Emergency stop all AI");
  });

  it("separates provider configuration from emergency pause permission", () => {
    const configureOnly = { ...configuration, permissions: { can_manage_tenant: false, can_manage_global: false, platform_admin: false, can_configure_provider: true, can_emergency_stop: false } };
    const configureMarkup = renderToStaticMarkup(<ProviderCards configuration={configureOnly} metrics={metrics} onChanged={noop} onReload={noop} />);
    expect(configureMarkup).toMatch(/<fieldset class=/);
    expect(configureMarkup).toMatch(/type=.button. disabled=/);
    const emergencyOnly = { ...configuration, permissions: { can_manage_tenant: false, can_manage_global: false, platform_admin: false, can_configure_provider: false, can_emergency_stop: true } };
    const emergencyMarkup = renderToStaticMarkup(<ProviderCards configuration={emergencyOnly} metrics={metrics} onChanged={noop} onReload={noop} />);
    expect(emergencyMarkup).toMatch(/<fieldset disabled=.*class=/);
    expect(emergencyMarkup).not.toMatch(/type=.button. disabled=/);
  });

  it("applies optimistic provider changes immutably and preserves rollback snapshot", () => {
    const before = configuration;
    const optimistic = replaceProviderConfiguration(before, "openai", { processing_enabled: true });
    expect(optimistic.providers[1].processing_enabled).toBe(true);
    expect(before.providers[1].processing_enabled).toBe(false);
    const rolledBack = before;
    expect(rolledBack.providers[1].processing_enabled).toBe(false);
  });

  it("loads public configuration and excludes provider secrets", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(configuration), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    const result = await fetchAiOperationsConfiguration(fetcher);
    expect(result.providers[0].connection_configured).toBe(true);
    expect(JSON.stringify(result)).not.toContain("api_key");
  });

  it("sends validated provider and budget mutations and surfaces API errors", async () => {
    const ok = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => new Response(JSON.stringify({ audit: { actor: "a", action: "updated", reason: "capacity", timestamp: "2026-01-01T00:00:00Z" }, body: init?.body }), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    await updateAiProvider("openai", { single_enabled: false, reason: "capacity" }, ok);
    await updateAiBudget({ daily_limit_micros: 100, reason: "budget" }, ok);
    expect(ok).toHaveBeenCalledTimes(2);
    expect(String((ok as any).mock.calls[0][1].body)).toContain("single_enabled");
    const failed = vi.fn(async () => new Response(JSON.stringify({ detail: { message: "Policy rejected" } }), { status: 422, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    await expect(updateAiProvider("openai", { batch_enabled: true, reason: "test" }, failed)).rejects.toThrow("Policy rejected");
  });
});