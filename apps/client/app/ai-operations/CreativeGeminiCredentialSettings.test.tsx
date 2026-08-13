import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { getCreativeGeminiCredential, replaceCreativeGeminiCredential, testCreativeGeminiCredential } from "../../features/ai_operations";
import { CreativeGeminiCredentialSettings } from "./CreativeGeminiCredentialSettings";

const secret = "creative-full-key-must-never-render";
describe("Creative Gemini credential settings", () => {
  it("calls only Creative AI Operations endpoints", async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ provider:"gemini", configured:true, source:"configuration", masked_key:"••••••••1234", label:"Creative project", status:"connected", last_tested_at:null, updated_at:null, updated_by:null }) });
    await getCreativeGeminiCredential(fetcher); await testCreativeGeminiCredential(secret, "Creative project", fetcher); await replaceCreativeGeminiCredential(secret, "Creative project", fetcher);
    expect(fetcher.mock.calls.map((call) => call[0])).toEqual(["/api/v1/admin/ai-operations/configuration/credentials/gemini", "/api/v1/admin/ai-operations/configuration/credentials/gemini/test", "/api/v1/admin/ai-operations/configuration/credentials/gemini"]);
    expect(JSON.stringify(fetcher.mock.calls)).not.toContain("/api/inventory");
    expect(JSON.stringify(fetcher.mock.calls)).not.toContain("/api/auth/google");
  });
  it("renders separate Creative scope without rendering a candidate key", () => {
    const markup = renderToStaticMarkup(<CreativeGeminiCredentialSettings canManage={false} />);
    expect(markup).toContain("Creative AI"); expect(markup).toContain("Loading Gemini credential configuration");
    expect(markup).not.toContain(secret); expect(markup).not.toContain("Inventory document pipeline");
  });
});
